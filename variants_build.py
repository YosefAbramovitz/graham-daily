"""
שלב א' של בדיקת השינויים מהקורס: איסוף הנתונים פעם אחת.

מוריד נרות יומיים מלאים (פתיחה, גבוה, נמוך, סגירה, מחזור) ליקום S&P 1500
ול-SPY, ממפה כל מניה לסקטור, ומחשב בכל סוף חודש אילו מניות עברו את המסך
המודרני, רק עם דוחות שכבר הוגשו באותו יום (כמו backtest.py).

התוצאה נשמרת בתיקייה variants_cache/, ושלב ב' (variants_sim.py) מריץ עליה את
כל הגרסאות בלי לגעת ברשת. ככה אפשר לבדוק עשרות גרסאות בשניות, ואותם נתונים
בדיוק משמשים את הבסיס ואת כל שינוי.

    python variants_build.py --start 2016-01-01 --end 2025-12-31
"""

from __future__ import annotations

import argparse
import io
import json
import multiprocessing as mp
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

import alpaca_prices as ap
import sec_facts as sf
from backtest import metrics_at, passes_screen
from graham_screener import BROWSER_UA, WIKI_LISTS, get_sp1500_tickers

OUT = Path("variants_cache")
FIELDS = {"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"}


def sectors() -> dict:
    """סימול -> סקטור GICS, משלוש טבלאות ויקיפדיה."""
    out = {}
    for key in ("sp500", "sp400", "sp600"):
        try:
            r = requests.get(WIKI_LISTS[key], headers={"User-Agent": BROWSER_UA}, timeout=30)
            for t in pd.read_html(io.StringIO(r.text)):
                sym = next((c for c in ("Symbol", "Ticker symbol", "Ticker") if c in t.columns), None)
                sec = next((c for c in t.columns if "GICS" in str(c) and "Sector" in str(c)), None)
                if sym and sec and len(t) > 50:
                    for s, g in zip(t[sym], t[sec]):
                        out[str(s).strip().upper().replace(".", "-")] = str(g).strip()
                    break
        except Exception as e:  # noqa: BLE001
            print(f"[warn] סקטורים {key}: {e}", flush=True)
    return out


def bars(symbols, start: date, end: date) -> dict:
    """נרות מלאים, מילון של טבלאות רחבות (שורה ליום, עמודה לסימול)."""
    collected = {}
    syms = sorted(set(symbols))
    for i in range(0, len(syms), ap.BATCH):
        chunk = syms[i:i + ap.BATCH]
        got = ap._fetch_batch(chunk, start, end, "sip")
        if not got:
            got = ap._fetch_batch(chunk, start, end, "iex")
        collected.update(got)
        print(f"  נרות: {min(i + ap.BATCH, len(syms))}/{len(syms)}", flush=True)
    frames = {f: {} for f in FIELDS.values()}
    for sym, bs in collected.items():
        if not bs:
            continue
        idx = pd.to_datetime([b["t"] for b in bs], utc=True).tz_convert(None).normalize()
        for k, f in FIELDS.items():
            frames[f][sym] = pd.Series([float(b[k]) for b in bs], index=idx)
    out = {}
    for f, d in frames.items():
        df = pd.DataFrame(d).sort_index()
        out[f] = df[~df.index.duplicated(keep="last")].astype("float32")
    return out


def _screen_chunk(job):
    """קבוצת מניות על כל התאריכים. כל עובד טוען רק את הדוחות של הקבוצה שלו,
    אחרת כל תהליך מחזיק את כל הדוחות בזיכרון ונגמר הזיכרון."""
    tickers, prices, spl = job      # prices: {date: {ticker: price}}, spl: {ticker: [(יום, יחס)]}
    cik = sf.ticker_to_cik()
    out = []
    for tk in tickers:
        comp = sf.company_facts(cik[tk.upper()])
        if not comp:
            continue
        for d, row in prices.items():
            px = row.get(tk)
            if px is None:
                continue
            m = metrics_at(comp, d, px, spl.get(tk))
            if m and passes_screen(m)[0]:
                out.append((d.isoformat(), tk, round(m["ebit_ev"] or 0.0, 4)))
    return out


def month_ends(start: date, end: date, close: pd.DataFrame) -> list:
    """יום המסחר האחרון בכל חודש."""
    idx = close.index[(close.index.date >= start) & (close.index.date <= end)]
    s = pd.Series(idx, index=idx)
    return [d.date() for d in s.groupby([idx.year, idx.month]).max()]


def main() -> int:
    global OUT
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2016-01-01", help="תחילת הנרות")
    p.add_argument("--first-entry", default="2017-01-01", help="החודש הראשון לכניסות")
    p.add_argument("--end", default="2025-12-31")
    p.add_argument("--skip-bars", action="store_true", help="להשתמש בנרות שכבר נשמרו")
    p.add_argument("--bars-only", action="store_true", help="רק נרות, בלי דוחות ובלי מסך")
    p.add_argument("--out", default=str(OUT), help="תיקיית המטמון")
    a = p.parse_args()
    OUT = Path(a.out)
    from app import load_env           # מפתחות Alpaca מ-.env, בלי להדפיס אותם
    load_env()
    start, end = date.fromisoformat(a.start), date.fromisoformat(a.end)
    OUT.mkdir(exist_ok=True)
    t0 = time.time()

    tickers = get_sp1500_tickers()
    print(f"יקום: {len(tickers)}", flush=True)
    sec_map = sectors()
    print(f"סקטורים: {len(sec_map)}", flush=True)
    (OUT / "sectors.json").write_text(json.dumps(sec_map), encoding="utf-8")

    bars_path = OUT / "bars.pkl.gz"
    if a.skip_bars and bars_path.exists():
        data = pd.read_pickle(bars_path)
        # השלמת סימולים שנפלו (בקשה שנכשלה באמצע מפילה קבוצה שלמה)
        miss = [t for t in tickers + ["SPY"] if t not in data["close"].columns]
        if miss:
            print(f"משלים {len(miss)} סימולים חסרים", flush=True)
            ap.BATCH = 20
            more = bars(miss, start, end)
            if more["close"].shape[1]:
                for f in data:
                    data[f] = data[f].join(more[f], how="outer").sort_index()
                pd.to_pickle(data, bars_path)
    else:
        data = bars(tickers + ["SPY"], start, end)
        pd.to_pickle(data, bars_path)
    close = data["close"]
    print(f"נרות: {close.shape[1]} סימולים, {close.index.min().date()}..{close.index.max().date()}"
          f" ({time.time()-t0:.0f}s)", flush=True)
    if a.bars_only:
        return 0

    cik = sf.ticker_to_cik(quiet=False)
    facts = {}
    for i, tk in enumerate(tickers, 1):
        c = cik.get(tk.upper())
        if c is None or tk not in close.columns:
            continue
        comp = sf.company_facts(c)
        if comp:
            facts[tk] = comp
        if i % 100 == 0:
            print(f"  דוחות: {i}/{len(tickers)} ({len(facts)}) {time.time()-t0:.0f}s", flush=True)
    print(f"דוחות: {len(facts)}", flush=True)

    dates = month_ends(date.fromisoformat(a.first_entry), end, close)
    names = sorted(facts)
    facts.clear()                     # העובדים טוענים לבד; לשחרר זיכרון
    prices = {d: {tk: float(v) for tk, v in close.loc[pd.Timestamp(d)].items() if not pd.isna(v)}
              for d in dates}
    split_map = ap.splits(names, start - timedelta(days=800), date.today())
    print(f"פיצולים: {sum(len(v) for v in split_map.values())} ב-{len(split_map)} מניות", flush=True)
    size = 30
    jobs = [(names[i:i + size], {d: {t: r[t] for t in names[i:i + size] if t in r}
                                 for d, r in prices.items()},
             {t: split_map[t] for t in names[i:i + size] if t in split_map})
            for i in range(0, len(names), size)]
    cands = {d.isoformat(): [] for d in dates}
    with mp.Pool(max(1, min(8, (os.cpu_count() or 2) - 4))) as pool:
        for n, rows in enumerate(pool.imap_unordered(_screen_chunk, jobs), 1):
            for d, tk, e in rows:
                cands[d].append({"t": tk, "ebit_ev": e})
            print(f"  מסך: קבוצה {n}/{len(jobs)} ({time.time()-t0:.0f}s)", flush=True)
    for v in cands.values():
        v.sort(key=lambda r: r["ebit_ev"], reverse=True)
    (OUT / "candidates.json").write_text(json.dumps(cands), encoding="utf-8")
    print("עוברים לפי חודש: " + ", ".join(f"{d[:7]}:{len(v)}" for d, v in cands.items()), flush=True)

    print(f"סיום אחרי {time.time()-t0:.0f} שניות", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
