"""
כניסה בפקודת limit במקום market בפתיחה (Katz, The Encyclopedia of Trading Strategies:
"הדבר החשוב ביותר לשיפור מערכת").

לכל אות סווינג עם 15 ימים מלאים בנרות 5 דקות: פקודת limit ביום הכניסה בלבד, במחיר
סגירת יום האות פחות k ATR. פתיחה מתחת למחיר = מילוי בפתיחה; אחרת מילוי במחיר ה-limit
בנר הראשון שנגע בו; בלי מילוי עד סוף היום - אין עסקה. סטופ 1.5 ATR מתחת למחיר
המילוי, יציאה בסגירה של היום ה-15 (יום הכניסה = 1). עלות 10bps לצד.

המדד הקובע: R לאות (עסקה שלא בוצעה = 0), כי הוא מה שנשאר בתיק. הבחירה על 2016-2020.

    python limit_sim.py [variants_cache]
"""
from __future__ import annotations

import glob
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from swing_sim import COST, SPLIT, Data
from timing_sim import sessions

CACHE = Path(sys.argv[1] if len(sys.argv) > 1 else "variants_cache")
STOP_ATR, HOLD = 1.5, 15
KS = [None, 0.0, 0.25, 0.5, 0.75, 1.0]          # None = market בפתיחה (הכלל)


def run(B, first_day_len, fill_i, px, atr, same_bar):
    """R ותשואה מעסקה שנכנסה בנר fill_i במחיר px, עד סגירת היום ה-15."""
    stop = px - STOP_ATR * atr
    o, l, c = B[:, 0], B[:, 2], B[:, 3]
    start = fill_i if same_bar else fill_i + 1
    out = None
    for i in range(start, len(B)):
        if i > fill_i and o[i] <= stop:
            out = o[i]; break
        if l[i] <= stop:
            out = stop; break
    if out is None:
        out = c[-1]
    ret = out * (1 - COST) / (px * (1 + COST)) - 1
    return ret, ret * px / (STOP_ATR * atr)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    pd.set_option("display.width", 250)
    d = Data(CACHE)
    col = {c: i for i, c in enumerate(d.cols)}
    day_ix = {ts.date(): i for i, ts in enumerate(d.idx)}
    rows = []
    for p in sorted(glob.glob(str(CACHE / "intraday" / "part_*.pkl.gz"))):
        part = pd.read_pickle(p)
        for (sday, sym), df in part.items():
            s, k = day_ix.get(pd.Timestamp(sday).date()), col.get(sym)
            if s is None or k is None or df.empty:
                continue
            atr, c0 = d.atr[s, k], d.C[s, k]
            if not (atr > 0 and c0 > 0):
                continue
            days = [x for x in sessions(df) if x[0] > pd.Timestamp(sday).date()][:HOLD]
            if len(days) < HOLD or len(days[0][2]) < 70:
                continue
            B = np.vstack([x[2] for x in days])
            n0 = len(days[0][2])
            o, l = B[:, 0], B[:, 2]
            for kk in KS:
                if kk is None:
                    r = run(B, n0, 0, o[0], atr, True)
                    rows.append((sday, sym, "market בפתיחה", True) + r)
                    continue
                lim = c0 - kk * atr
                if o[0] <= lim:
                    fi, px, same = 0, o[0], True
                else:
                    hit = np.nonzero(l[:n0] <= lim)[0]
                    if not len(hit):
                        rows.append((sday, sym, f"limit {kk} ATR", False, 0.0, 0.0))
                        continue
                    fi, px, same = int(hit[0]), lim, False   # הסטופ נבדק מהנר הבא (לא ידוע הסדר בתוך הנר)
                r = run(B, n0, fi, px, atr, same)
                rows.append((sday, sym, f"limit {kk} ATR", True) + r)
        del part
        print(p, len(rows), flush=True)
    R = pd.DataFrame(rows, columns=["day", "sym", "v", "filled", "ret", "R"])
    R["oos"] = pd.to_datetime(R.day) >= SPLIT
    R.to_pickle(CACHE / "limit_results.pkl")
    base = R[R.v == "market בפתיחה"].set_index(["day", "sym"]).R
    out = []
    for v, g in R.groupby("v", sort=False):
        g = g.set_index(["day", "sym"])
        row = {"גרסה": v}
        for lab, m in (("16-20", ~g.oos), ("21-25", g.oos)):
            x = g[m]
            f = x[x.filled]
            miss = x[~x.filled]
            row[lab + " מילוי%"] = round(x.filled.mean() * 100, 1)
            row[lab + " R לעסקה"] = round(f.R.mean(), 3)
            row[lab + " R לאות"] = round(x.R.mean(), 3)
            row[lab + " R שפוספס"] = round(base.reindex(miss.index).mean(), 3) if len(miss) else None
        out.append(row)
    T = pd.DataFrame(out)
    print(T.to_string(index=False))
    T.to_csv(CACHE / "limit_table.csv", index=False, encoding="utf-8-sig")


if __name__ == "__main__":
    main()