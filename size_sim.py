"""
תוצאות בדיקת סף הגודל (size_build.py): חברות עם מכירות 500 מיליון עד 1.5
מיליארד מול 1.5 מיליארד ומעלה, על אותם נתונים ואותם כללי קנייה ויציאה של
variants_sim.py (כניסה אחרי סוף חודש, יציאה ב-+50% או בסוף השנה השנייה).

    python size_sim.py [--cache variants_cache] [--slots 15]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import variants_sim as vs

BIG = 1_500_000_000
SPLIT = pd.Timestamp("2021-01-01")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="variants_cache")
    ap.add_argument("--slots", type=int, default=15)
    a = ap.parse_args()
    cache = Path(a.cache)
    d = vs.Data(cache)
    raw = json.loads((cache / "candidates_rev.json").read_text(encoding="utf-8"))
    full = {pd.Timestamp(k): v for k, v in raw.items()}
    rev = {(pd.Timestamp(k), c["t"]): c.get("rev") for k, v in raw.items() for c in v}
    sets = {
        "1.5 מיליארד ומעלה (היום)": {k: [c for c in v if (c.get("rev") or 0) >= BIG] for k, v in full.items()},
        "500 מיליון ומעלה": full,
        "רק 500 מיליון - 1.5 מיליארד": {k: [c for c in v if (c.get("rev") or 0) < BIG] for k, v in full.items()},
    }
    old = d.cands
    same = sum(len(v) for v in old.values()) == sum(len(v) for v in sets["1.5 מיליארד ומעלה (היום)"].values())
    print(f"בדיקת עקביות מול candidates.json: {'זהה' if same else 'שונה'} "
          f"({sum(len(v) for v in old.values())} מול {sum(len(v) for v in sets['1.5 מיליארד ומעלה (היום)'].values())})")
    print("מועמדות בממוצע לחודש: " + ", ".join(
        f"{n}: {np.mean([len(v) for v in s.values()]):.1f}" for n, s in sets.items()))

    # 1. עסקאות: כל הכניסות האפשריות מהמסך המורחב, לפי קבוצת גודל
    d.cands = full
    tr = vs.all_trades(d, vs.Rules())
    rows = []
    for t in tr:
        ts = d.idx[t["s"]]
        r = rev.get((ts, d.cols[t["k"]]))
        rows.append({"ret": t["ret"], "big": (r or 0) >= BIG, "day": ts, "days": t["days"]})
    df = pd.DataFrame(rows)
    print(f"\nעסקאות: {len(df)}")
    print(f"{'קבוצה':28} {'עסקאות':>7} {'ממוצע':>7} {'הצלחה':>6} {'2017-20':>8} {'2021-25':>8}")
    for name, g in (("1.5 מיליארד ומעלה", df[df.big]), ("500 מיליון - 1.5 מיליארד", df[~df.big])):
        h1, h2 = g[g.day < SPLIT].ret, g[g.day >= SPLIT].ret
        print(f"{name:28} {len(g):7d} {g.ret.mean()*100:+6.1f}% {(g.ret>0).mean()*100:5.0f}% "
              f"{h1.mean()*100:+7.1f}% {h2.mean()*100:+7.1f}%")
    t = vs.welch(list(df[~df.big].ret), list(df[df.big].ret))
    print(f"הפרש (קטנות פחות גדולות): t = {t:.2f}")
    print("\nממוצע לעסקה לפי שנת כניסה (גדולות / קטנות):")
    for y, g in df.groupby(df.day.dt.year):
        b, s = g[g.big].ret, g[~g.big].ret
        print(f"  {y}: {b.mean()*100:+6.1f}% ({len(b)}) / "
              + (f"{s.mean()*100:+6.1f}% ({len(s)})" if len(s) else "-"))

    # 2. תיק
    spy = pd.Series(d.Cf[:, d.col["SPY"]], index=d.idx)
    print(f"\n{'תיק ' + str(a.slots) + ' מקומות':30} {'שנתי':>7} {'2017-20':>8} {'2021-25':>8} {'ירידה':>7} {'שארפ':>5} {'עסקאות':>6}")
    for slots in sorted({a.slots, 30}):
        for n, s in sets.items():
            d.cands = s
            eq, trs = vs.run_portfolio(d, vs.Variant(n, slots=slots))
            c, dd, sh = vs.stats(eq)
            c1 = vs.stats(eq[:SPLIT])[0]
            c2 = vs.stats(eq[SPLIT:])[0]
            print(f"{n + f' ({slots})':30} {c*100:+6.1f}% {c1*100:+7.1f}% {c2*100:+7.1f}% "
                  f"{dd*100:6.1f}% {sh:5.2f} {len(trs):6d}")
    sp = spy[eq.index[0]:]
    c, dd, sh = vs.stats(sp)
    print(f"{'SPY':30} {c*100:+6.1f}% {vs.stats(sp[:SPLIT])[0]*100:+7.1f}% "
          f"{vs.stats(sp[SPLIT:])[0]*100:+7.1f}% {dd*100:6.1f}% {sh:5.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
