"""
מסנני איכות לגראהם נגד "מלכודות ערך" - בדיקה לאחור (אוקטובר 2026).

הרקע: בשנה 10.2025-10.2026 המסך הניב -3.5% מול SPY +17.4% ו-VTV (מניות ערך) +19%.
ההפסד בא מכמה מהמניות הזולות ביותר שהמשיכו לקרוס (LULU, NFLX, CALM, BBWI, LKQ). כלומר
הבעיה לא הייתה סגנון הערך, אלא מניות שזולות כי העסק נחלש.

כל גרסה מוסיפה מסנן אחד (או צירוף אחד) על המסך הקיים, ובוחרת את 15 הזולות לפי EBIT/EV
מבין מה שנשאר - בדיוק כמו backtest.py (איזון רבעוני, משקל שווה, אותם מחירים ודוחות).
הספים נקבעו מראש לפי הספרות, לא לפי התוצאה:
  * F-score של פיוטרוסקי (9 בדיקות שנתיות) 5 ומעלה / 7 ומעלה
  * הכנסות עלו מול השנה הקודמת / רווח תפעולי עלה מול השנה הקודמת
  * מומנטום 12-1 חיובי (המחיר לפני חודש מעל המחיר לפני שנה)
  * רווח תפעולי עלה וגם מומנטום חיובי

מדווח: כל התקופה, שני חצאים (כדי לראות אם יתרון מחזיק), והשנה האחרונה בנפרד.
הטיית שורדים: היקום הוא S&P 1500 של היום. התוצאה היא תקרה.

    python quality_sim.py [--start 2017] [--end 2026] [--month 10] [--day 7]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import backtest as bt
import sec_facts as sf

HERE = Path(__file__).parent
MAX_NAMES = 15


def load_env() -> None:
    """המפתחות מ-.env לסביבה (לתיקון פיצולים מאלפקה). לא מודפס כלום."""
    try:
        for line in (HERE / ".env").read_text(encoding="utf-8").splitlines():
            k, sep, v = line.partition("=")
            if sep and k.strip() and not k.strip().startswith("#"):
                os.environ.setdefault(k.strip(), v.strip())
    except OSError:
        pass


def _h(hist: dict, field: str, i: int) -> Optional[float]:
    v = hist.get(field) or []
    return float(v[i]) if len(v) > i and isinstance(v[i], (int, float)) else None


def _ratio(a, b):
    return a / b if a is not None and b not in (None, 0) else None


def fscore(hist: dict) -> int:
    """פיוטרוסקי, מהשנה האחרונה מול הקודמת. נתון חסר = הבדיקה לא עוברת."""
    ni0, ni1 = _h(hist, "net_income", 0), _h(hist, "net_income", 1)
    a0, a1, a2 = _h(hist, "assets", 0), _h(hist, "assets", 1), _h(hist, "assets", 2)
    cfo = _h(hist, "cfo", 0)
    roa0, roa1 = _ratio(ni0, a1 or a0), _ratio(ni1, a2 or a1)
    debt = lambda i: _h(hist, "debt_long", i) if _h(hist, "debt_long", i) is not None \
        else _h(hist, "debt_total", i)
    lev0, lev1 = _ratio(debt(0) or 0.0, a0), _ratio(debt(1) or 0.0, a1)
    cr0 = _ratio(_h(hist, "assets_current", 0), _h(hist, "liabilities_current", 0))
    cr1 = _ratio(_h(hist, "assets_current", 1), _h(hist, "liabilities_current", 1))
    sh0, sh1 = _h(hist, "shares_outstanding", 0), _h(hist, "shares_outstanding", 1)
    rev0, rev1 = _h(hist, "revenue", 0), _h(hist, "revenue", 1)

    def gross(i):
        g = _h(hist, "gross_profit", i)
        if g is None and _h(hist, "revenue", i) is not None and _h(hist, "cogs", i) is not None:
            g = _h(hist, "revenue", i) - _h(hist, "cogs", i)
        return g
    gm0, gm1 = _ratio(gross(0), rev0), _ratio(gross(1), rev1)
    at0, at1 = _ratio(rev0, a1 or a0), _ratio(rev1, a2 or a1)
    tests = [
        roa0 is not None and roa0 > 0,
        cfo is not None and cfo > 0,
        roa0 is not None and roa1 is not None and roa0 > roa1,
        cfo is not None and ni0 is not None and cfo > ni0,
        lev0 is not None and lev1 is not None and lev0 <= lev1,
        cr0 is not None and cr1 is not None and cr0 > cr1,
        sh0 is not None and sh1 is not None and sh0 <= sh1 * 1.01,
        gm0 is not None and gm1 is not None and gm0 > gm1,
        at0 is not None and at1 is not None and at0 > at1,
    ]
    return sum(bool(t) for t in tests)


def growth(hist: dict, field: str) -> Optional[float]:
    a, b = _h(hist, field, 0), _h(hist, field, 1)
    return a / b - 1 if a is not None and b and b > 0 else None


VARIANTS = {
    "בסיס (המסך היום)": lambda q: True,
    "F-score 5 ומעלה": lambda q: q["f"] >= 5,
    "F-score 7 ומעלה": lambda q: q["f"] >= 7,
    "הכנסות עלו": lambda q: q["rev_g"] is not None and q["rev_g"] >= 0,
    "רווח תפעולי עלה": lambda q: q["ebit_g"] is not None and q["ebit_g"] >= 0,
    "מומנטום 12-1 חיובי": lambda q: q["mom"] is not None and q["mom"] > 0,
    "רווח תפעולי עלה + מומנטום חיובי": lambda q: (q["ebit_g"] is not None and q["ebit_g"] >= 0
                                                and q["mom"] is not None and q["mom"] > 0),
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", choices=["sp500", "sp1500"], default="sp1500")
    ap.add_argument("--start", type=int, default=2017)
    ap.add_argument("--end", type=int, default=date.today().year)
    ap.add_argument("--month", type=int, default=10)
    ap.add_argument("--day", type=int, default=7)
    ap.add_argument("--cost-bps", type=float, default=bt.DEFAULT_COST_BPS)
    ap.add_argument("--out", default="quality_sim_results.json")
    a = ap.parse_args()
    load_env()

    from graham_screener import get_sp500_tickers, get_sp1500_tickers
    tickers = get_sp1500_tickers() if a.universe == "sp1500" else get_sp500_tickers()
    dates = bt.rebalance_dates(a.start, a.end, a.month, a.day, "quarterly")
    dates = [d for d in dates if d <= date.today()]
    print(f"{len(tickers)} מניות, {len(dates) - 1} תקופות רבעוניות {dates[0]} עד {dates[-1]}",
          flush=True)
    close = bt.load_prices(tickers, dates[0] - timedelta(days=400), dates[-1] + timedelta(days=5))
    funds = ("SPY", "RSP", "VTV")
    fclose = bt.load_prices(list(funds), dates[0] - timedelta(days=10), dates[-1] + timedelta(days=5))
    split_map: Dict[str, list] = {}
    import alpaca_prices
    if alpaca_prices.available():
        split_map = alpaca_prices.splits(tickers, dates[0] - timedelta(days=800), date.today())
    print(f"פיצולים: {sum(len(v) for v in split_map.values())} ב-{len(split_map)} מניות", flush=True)
    cik_map = sf.ticker_to_cik()
    facts = {t: c for t in tickers
             if (cik := cik_map.get(t.upper())) and (c := sf.company_facts(cik))}
    print(f"דוחות: {len(facts)} מתוך {len(tickers)}", flush=True)

    periods = {v: [] for v in VARIANTS}
    cost = 2 * a.cost_bps / 10_000.0
    for buy, sell in zip(dates, dates[1:]):
        rows, uni = [], []
        for tk, compact in facts.items():
            p0, p1 = bt.price_on(close, tk, buy), bt.price_on(close, tk, sell)
            if not p0 or not p1:
                continue
            ret = p1 / p0 - 1
            uni.append(ret)
            m = bt.metrics_at(compact, buy, p0, split_map.get(tk))
            if not m or not bt.passes_screen(m)[0]:
                continue
            hist = sf.as_of(compact, buy, years=3)["_history"]
            pm12 = bt.price_on(close, tk, buy - timedelta(days=365))
            pm1 = bt.price_on(close, tk, buy - timedelta(days=30))
            rows.append({"t": tk, "ret": ret, "ev": m["ebit_ev"] or 0.0, "f": fscore(hist),
                         "rev_g": growth(hist, "revenue"), "ebit_g": growth(hist, "ebit"),
                         "mom": pm1 / pm12 - 1 if pm1 and pm12 else None})
        rows.sort(key=lambda r: -r["ev"])
        bench = sum(uni) / len(uni) if uni else 0.0
        fr = {f: (lambda x0, x1: round(x1 / x0 - 1, 4) if x0 and x1 else None)(
            bt.price_on(fclose, f, buy), bt.price_on(fclose, f, sell)) for f in funds}
        line = []
        for name, keep in VARIANTS.items():
            held = [r for r in rows if keep(r)][:MAX_NAMES]
            port = (sum(r["ret"] for r in held) / len(held) - cost) if held else 0.0
            periods[name].append({"buy": buy.isoformat(), "sell": sell.isoformat(),
                                  "n_held": len(held), "portfolio": round(port, 4),
                                  "benchmark": round(bench, 4), "excess": round(port - bench, 4),
                                  "funds": fr, "names": [r["t"] for r in held]})
            line.append(f"{port * 100:+.1f}%({len(held)})")
        print(f"{buy} -> {sell}: SPY {(fr['SPY'] or 0) * 100:+.1f}% | " + " ".join(line), flush=True)

    out = {}
    n = len(dates) - 1
    half = n // 2
    for name, ps in periods.items():
        def cagr(seq, key="portfolio"):
            return round(bt._annualised(seq, key), 4) if seq else None
        spy = lambda seq: round(bt._annualised(
            [dict(p, spy=p["funds"]["SPY"] or 0.0) for p in seq], "spy"), 4)
        out[name] = {
            "all": cagr(ps), "first_half": cagr(ps[:half]), "second_half": cagr(ps[half:]),
            "last_year": cagr(ps[-4:]), "spy_all": spy(ps), "spy_first": spy(ps[:half]),
            "spy_second": spy(ps[half:]), "spy_last_year": spy(ps[-4:]),
            "avg_names": round(sum(p["n_held"] for p in ps) / len(ps), 1),
            "beat_spy": f"{sum(1 for p in ps if p['funds']['SPY'] is not None and p['portfolio'] > p['funds']['SPY'])}/{len(ps)}",
            "periods": ps,
        }
    print("\n" + "=" * 70)
    print(f"{'גרסה':<34}{'הכול':>8}{'חצי 1':>8}{'חצי 2':>8}{'שנה אחרונה':>11}{'מניות':>7}{'מכה SPY':>9}")
    for name, r in out.items():
        print(f"{name:<34}{r['all']*100:>+7.1f}%{r['first_half']*100:>+7.1f}%"
              f"{r['second_half']*100:>+7.1f}%{r['last_year']*100:>+10.1f}%{r['avg_names']:>7}{r['beat_spy']:>9}")
    r = next(iter(out.values()))
    print(f"{'SPY':<34}{r['spy_all']*100:>+7.1f}%{r['spy_first']*100:>+7.1f}%"
          f"{r['spy_second']*100:>+7.1f}%{r['spy_last_year']*100:>+10.1f}%")
    print(f"\nחצי 1: {dates[0]} עד {dates[half]}, חצי 2: {dates[half]} עד {dates[-1]}. "
          "תשואות שנתיות, אחרי עלות. היקום הוא S&P 1500 של היום (הטיית שורדים).")
    Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"נשמר ל-{a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
