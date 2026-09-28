"""
בדיקת סף הגודל של גראהם (מחזור מכירות): האם חברות עם מכירות בין 500 מיליון
ל-1.5 מיליארד מוסיפות תשואה או רק סיכון.

אותו מסך בדיוק כמו variants_build.py, עם סף מכירות של 500 מיליון, ולכל מועמדת
נשמר גם המחזור שלה. כך אפשר לבנות משני קבצי מועמדים - הרגיל (1.5 מיליארד ומעלה)
והמורחב - על אותם נתונים, ולהריץ את variants_sim.py על כל אחד.

    python size_build.py            # צריך variants_cache/bars.pkl.gz ו-.sec_cache
    -> variants_cache/candidates_rev.json   {תאריך: [{t, ebit_ev, rev}]}
"""

from __future__ import annotations

import json
import multiprocessing as mp
import os
import sys
import time
from datetime import date

import pandas as pd

import sec_facts as sf
from backtest import metrics_at, passes_screen
from graham_screener import get_sp1500_tickers
from variants_build import OUT, month_ends

MIN_REVENUE = 500_000_000


def _chunk(job):
    tickers, prices = job
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
            m = metrics_at(comp, d, px)
            if m and passes_screen(m, {"min_revenue": MIN_REVENUE})[0]:
                out.append((d.isoformat(), tk, round(m["ebit_ev"] or 0.0, 4), m.get("revenue")))
    return out


def main() -> int:
    t0 = time.time()
    close = pd.read_pickle(OUT / "bars.pkl.gz")["close"]
    tickers = get_sp1500_tickers()
    cik = sf.ticker_to_cik(quiet=True)
    names = sorted(t for t in tickers if cik.get(t.upper()) is not None and t in close.columns)
    dates = month_ends(date(2017, 1, 1), date(2025, 12, 31), close)
    prices = {d: {tk: float(v) for tk, v in close.loc[pd.Timestamp(d)].items() if not pd.isna(v)}
              for d in dates}
    size = 30
    jobs = [(names[i:i + size], {d: {t: r[t] for t in names[i:i + size] if t in r}
                                 for d, r in prices.items()})
            for i in range(0, len(names), size)]
    cands = {d.isoformat(): [] for d in dates}
    with mp.Pool(max(1, min(8, (os.cpu_count() or 2) - 4))) as pool:
        for n, rows in enumerate(pool.imap_unordered(_chunk, jobs), 1):
            for d, tk, e, rev in rows:
                cands[d].append({"t": tk, "ebit_ev": e, "rev": rev})
            print(f"  קבוצה {n}/{len(jobs)} ({time.time()-t0:.0f}s)", flush=True)
    for v in cands.values():
        v.sort(key=lambda r: r["ebit_ev"], reverse=True)
    (OUT / "candidates_rev.json").write_text(json.dumps(cands), encoding="utf-8")
    print(f"סיום אחרי {time.time()-t0:.0f} שניות", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
