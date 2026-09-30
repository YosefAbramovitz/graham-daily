"""
נתונים לבדיקת רעיונות Greenblatt (The Little Book That Beats the Market), ספט' 2026.

לכל מניה ב-S&P 1500 ולכל סוף חודש 2017-2025: תשואת רווח תפעולי (EBIT/EV), תשואה על ההון
(ROC = EBIT / (הון חוזר תפעולי + רכוש קבוע נטו)), האם עברה את המסך, סקטור.

תיקון חשוב: הנרות מאלפקה מותאמים לפיצולים (adjustment=all), אבל מספר המניות ב-SEC הוא כפי
שדווח. מניה שהתפצלה אחר כך (ISRG, LRCX, NVDA...) קיבלה בבדיקות הקודמות שווי שוק קטן פי יחס
הפיצול, ולכן נראתה זולה מאוד - הטיית הצצה קדימה לטובת מניות שעלו. כאן שווי השוק מחושב
ממחיר לא מותאם (raw) באותו יום, ואם היה פיצול בין תאריך דיווח מספר המניות ליום הבדיקה,
מספר המניות מתוקן לפיו. העמודות *_old הן החישוב הישן, להשוואה.

    python greenblatt_build.py            # נרות raw מאלפקה + מדדים (כחצי שעה, 8 תהליכים)
    python greenblatt_build.py --skip-raw # רק המדדים
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import sys
import time
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

import sec_facts as sf
from backtest import passes_screen

CACHE = Path("variants_cache")
OUT = CACHE / "greenblatt"
STABILITY_YEARS = 5


def fetch_raw(syms, start: date, end: date) -> pd.DataFrame:
    """סגירות לא מותאמות (adjustment=raw). קבוצה שנכשלה (סימול לא מוכר) מתפצלת."""
    import app
    app.load_env()
    closes = {}
    to_api = {s: s.replace("-", ".") for s in syms}
    back = {v: k for k, v in to_api.items()}

    def one(chunk):
        got, token = {}, None
        for _ in range(200):
            params = {"symbols": ",".join(chunk), "timeframe": "1Day", "limit": 10000,
                      "start": start.isoformat(), "end": end.isoformat(),
                      "adjustment": "raw", "feed": "sip", "sort": "asc"}
            if token:
                params["page_token"] = token
            for attempt in range(4):
                ok, data = app.api("GET", f"{app.DATA_BASE}/v2/stocks/bars", params=params)
                err = str(data.get("error", "")) if not ok else ""
                if ok or not any(x in err for x in ("429", "השיבה 5", "אין חיבור")):
                    break
                time.sleep(2 * (attempt + 1))
            if not ok:
                return False, got
            for s, rows in (data.get("bars") or {}).items():
                got.setdefault(s, []).extend(rows)
            token = data.get("next_page_token")
            if not token:
                return True, got
        return True, got

    with app.using(app.accounts_available()[0]):
        todo = [[to_api[s] for s in syms[i:i + 100]] for i in range(0, len(syms), 100)]
        while todo:
            chunk = todo.pop()
            ok, got = one(chunk)
            if ok:
                for s, rows in got.items():
                    closes[back.get(s, s)] = pd.Series(
                        [float(r["c"]) for r in rows], index=pd.to_datetime([str(r["t"])[:10] for r in rows]))
            elif len(chunk) > 1:
                todo += [chunk[:len(chunk) // 2], chunk[len(chunk) // 2:]]
    df = pd.DataFrame(closes).sort_index()
    print(f"raw: {df.shape[1]} מתוך {len(syms)} סימולים", flush=True)
    return df


def metrics_from_snap(snap: dict, price: float):
    """כמו backtest.metrics_at, מתמונת מצב קיימת (as_of יקר - פעם אחת לכל חברה-תאריך)."""
    def val(name):
        v = snap.get(name)
        return float(v) if isinstance(v, (int, float)) else None

    revenue, assets, ebit, shares = val("revenue"), val("assets"), val("ebit"), val("shares_outstanding")
    if not price or not shares or not assets:
        return None
    mcap = price * shares
    debt = sf.total_debt(val) or 0.0
    liquid = (val("cash") or 0.0) + (val("short_term_investments") or 0.0)
    ev = mcap + debt - liquid
    gross = val("gross_profit")
    if gross is None and revenue is not None and val("cogs") is not None:
        gross = revenue - val("cogs")
    dep = val("depreciation")
    ebitda = (ebit + dep) if (ebit is not None and dep is not None) else None
    payout = (val("dividends_paid") or 0.0) + (val("buybacks") or 0.0)
    issued = val("stock_issued") or 0.0
    earnings = snap.get("_history", {}).get("net_income", [])
    m = {
        "market_cap": mcap, "ev": ev, "revenue": revenue, "ebit": ebit,
        "ebit_ev": (ebit / ev) if (ebit is not None and ev and ev > 0) else None,
        "gross_profitability": (gross / assets) if (gross is not None and assets) else None,
        "net_payout_yield": ((payout - issued) / mcap) if mcap else None,
        "net_debt_to_ebitda": ((debt - liquid) / ebitda) if (ebitda and ebitda > 0) else None,
        "earnings_stable": (len(earnings) >= STABILITY_YEARS
                            and all(e > 0 for e in earnings[:STABILITY_YEARS])),
    }
    # Greenblatt: הון מושקע = הון חוזר תפעולי (בלי מזומן ובלי חוב שוטף) + רכוש קבוע נטו
    ca, cl, ppe = val("assets_current"), val("liabilities_current"), val("ppe_net")
    cur_debt = val("debt_current")
    if cur_debt is None:
        cur_debt = (val("debt_short") or 0.0) + (val("short_borrowings") or 0.0)
    roc = None
    if ebit is not None and ppe is not None and ca is not None and cl is not None:
        nwc = (ca - liquid) - (cl - cur_debt)
        cap = max(nwc, 0.0) + ppe
        if cap > 0:
            roc = ebit / cap
    m["roc"] = roc
    return m


def _chunk(job):
    tickers, raw, adj, dates = job          # raw/adj: {ticker: pd.Series}
    cik = sf.ticker_to_cik()
    out = []
    for tk in tickers:
        c = cik.get(tk.upper())
        comp = sf.company_facts(c) if c else None
        if not comp or tk not in adj:
            continue
        a = adj[tk]
        r = raw.get(tk)
        for d in dates:
            ts = pd.Timestamp(d)
            pa = a.get(ts)
            if pa is None or pa != pa:
                continue
            snap = sf.as_of(comp, d, years=STABILITY_YEARS + 1)
            old = metrics_from_snap(snap, float(pa))
            if old is None:
                continue
            new, fix = old, 1.0
            if r is not None:
                pr = r.get(ts)
                if pr is not None and pr == pr and pr > 0:
                    # פיצול בין תאריך מספר המניות ליום הבדיקה: R = raw / adj יורד בפיצול
                    fix = 1.0
                    pe = (snap.get("_period_ends", {}).get("shares_outstanding") or [None])[0]
                    if pe:
                        ra = (r / a).dropna()
                        rb = ra[ra.index <= pd.Timestamp(pe)]
                        rd = ra[ra.index <= ts]
                        if len(rb) and len(rd) and rd.iloc[-1] > 0:
                            q = rb.iloc[-1] / rd.iloc[-1]
                            if q > 1.4 or q < 0.7:
                                fix = q
                    new = metrics_from_snap(snap, float(pr) * fix)
            ok_new = bool(new and passes_screen(new)[0])
            ok_old = bool(passes_screen(old)[0])
            out.append((d.isoformat(), tk, new["ebit_ev"], old["ebit_ev"], new["roc"],
                        new["market_cap"], old["market_cap"], ok_new, ok_old, round(fix, 3)))
    return out


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-raw", action="store_true")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    bars = pd.read_pickle(CACHE / "bars.pkl.gz")
    adj = bars["close"]
    syms = [c for c in adj.columns if c != "SPY"]
    rp = OUT / "raw_close.pkl"
    if a.skip_raw and rp.exists():
        raw = pd.read_pickle(rp)
    else:
        raw = fetch_raw(syms, date(2016, 1, 1), date(2025, 12, 31))
        pd.to_pickle(raw, rp)
    print(f"raw מוכן ({time.time()-t0:.0f}s)", flush=True)
    idx = adj.index[adj.index >= "2017-01-01"]
    s = pd.Series(idx, index=idx)
    dates = [x.date() for x in s.groupby([idx.year, idx.month]).max()]
    size = 25
    jobs = []
    for i in range(0, len(syms), size):
        g = syms[i:i + size]
        jobs.append((g, {t: raw[t].dropna() for t in g if t in raw.columns},
                     {t: adj[t].dropna() for t in g}, dates))
    rows = []
    with mp.Pool(max(1, min(8, (os.cpu_count() or 2) - 4))) as pool:
        for n, r in enumerate(pool.imap_unordered(_chunk, jobs), 1):
            rows += r
            if n % 5 == 0:
                print(f"  {n}/{len(jobs)} ({time.time()-t0:.0f}s)", flush=True)
    df = pd.DataFrame(rows, columns=["date", "t", "ey", "ey_old", "roc", "mcap", "mcap_old",
                                     "pass", "pass_old", "split_fix"])
    sec = json.loads((CACHE / "sectors.json").read_text(encoding="utf-8"))
    df["sector"] = df.t.map(sec)
    df.to_csv(OUT / "metrics.csv.gz", index=False)
    print(f"שורות: {len(df)}, עוברים (חדש/ישן): {df['pass'].sum()}/{df.pass_old.sum()} "
          f"({time.time()-t0:.0f}s)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())