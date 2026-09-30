"""
שחזור של backtest.py (איזון רבעוני, עד 30 הזולות מהעוברים, משקל שווה, מול כל היקום במשקל
שווה) על המדדים של greenblatt_build.py - פעם בחישוב הישן (הטיית פיצולים) ופעם מתוקן.
סוף יוני 2017 עד סוף 2025, 34 רבעונים, עלות 0.1% לכל צד כמו ב-backtest.py.

    python split_bias_check.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

CACHE = Path("variants_cache")
COST = 2 * 10 / 10_000


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    m = pd.read_csv(CACHE / "greenblatt" / "metrics.csv.gz", parse_dates=["date"])
    C = pd.read_pickle(CACHE / "bars.pkl.gz")["close"].ffill()
    qe = sorted(d for d in m.date.unique() if pd.Timestamp(d).month in (3, 6, 9, 12)
                and pd.Timestamp(d) >= pd.Timestamp("2017-06-01"))
    rows = []
    for a, b in zip(qe[:-1], qe[1:]):
        g = m[m.date == a]
        g = g[g.t.isin(C.columns)]
        r = (C.loc[b, g.t].to_numpy() / C.loc[a, g.t].to_numpy()) - 1
        g = g.assign(r=r).dropna(subset=["r"])
        g["r"] = g.r.clip(-0.95, 3.0)
        out = {"buy": a, "universe": g.r.mean()}
        for lab, ey, ps in (("old", "ey_old", "pass_old"), ("new", "ey", "pass")):
            p = g[g[ps]].sort_values(ey, ascending=False).head(30)
            out[lab] = (p.r.mean() - COST) if len(p) else 0.0
            out[lab + "_n"] = len(p)
        rows.append(out)
    R = pd.DataFrame(rows)
    yrs = (pd.Timestamp(qe[-1]) - pd.Timestamp(qe[0])).days / 365.25
    ann = lambda s: (np.prod(1 + s)) ** (1 / yrs) - 1
    for lab in ("old", "new"):
        ex = R[lab] - R.universe
        t = ex.mean() / ex.std() * math.sqrt(len(ex))
        print(f"{lab}: תיק {ann(R[lab])*100:+.1f}% בשנה, יקום {ann(R.universe)*100:+.1f}%, "
              f"עודף {ann(R[lab])*100 - ann(R.universe)*100:+.1f}%, ניצח {int((ex > 0).sum())}/{len(ex)}, "
              f"t={t:.2f}, מניות בממוצע {R[lab + '_n'].mean():.1f}")
    R.to_csv(CACHE / "greenblatt" / "split_bias_check.csv", index=False)


if __name__ == "__main__":
    main()