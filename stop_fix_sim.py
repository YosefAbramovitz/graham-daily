"""
בחירת סטופ מחדש ברמת התיק (ספט' 2026). הסטופ 1.5 ATR נבחר לפי תוחלת לעסקה ב-2016-20, והבדיקות
ברמת התיק נעשו עם הבאג (עסקה שנעצרה ביום הכניסה לא יצאה). אחרי התיקון, ב-ideas_sim סטופ 3 ATR
באותו סיכון הניב תיק טוב בהרבה בשתי התקופות. כאן בדיקה מסודרת: סטופ x ימי החזקה x סיכון,
בחירה לפי שארפ 2016-20, שיפוט 2021-25, וגם על הרכב S&P 500 ההיסטורי (בלי הטיית שורדים).

    python stop_fix_sim.py
"""
from __future__ import annotations

import itertools
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import swing
from swing_sim import SPLIT, Data, Exit, all_trades, portfolio, pstats

CACHE = Path("variants_cache")


def bars(d, path):
    b = pd.read_pickle(path / "bars.pkl.gz")
    f = lambda k: b[k].reindex(index=d.idx, columns=d.cols).astype("float64")
    return f("close"), f("high"), f("low"), f("volume")


def membership(d, path):
    comp = pd.read_csv(path / "components.csv", parse_dates=["date"]).sort_values("date")
    mem = pd.DataFrame(False, index=d.idx, columns=d.cols)
    rows = list(comp.itertuples())
    for i, r in enumerate(rows):
        end = rows[i + 1].date if i + 1 < len(rows) else pd.Timestamp("2100-01-01")
        m = (d.idx >= r.date) & (d.idx < end)
        if m.any():
            mem.loc[m, [c for c in r.tickers.split(",") if c in mem.columns]] = True
    return mem.to_numpy()


def grid(d, label):
    out = []
    for sa, md, risk in itertools.product([1.5, 2.0, 2.5, 3.0, 4.0, 5.0], [10, 15, 20], [0.005, 0.01]):
        t = all_trades(d, "rule", Exit(sa, None, md), True)
        e = portfolio(d, t, risk=risk, max_pos=15, cap=0.20, rank="mom")
        ci, ddi, shi = pstats(e[e.index < SPLIT])
        co, ddo, sho = pstats(e[e.index >= SPLIT])
        ins = d.idx[t.s] < SPLIT
        out.append(dict(universe=label, stop=sa, days=md, risk=risk, IS_R=t.R[ins].mean(), OOS_R=t.R[~ins].mean(),
                        IS_cagr=ci, IS_dd=ddi, IS_sh=shi, OOS_cagr=co, OOS_dd=ddo, OOS_sh=sho))
        print(label, sa, md, risk, round(ci * 100, 1), round(co * 100, 1), flush=True)
    return out


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    pd.set_option("display.width", 250)
    d = Data(CACHE)
    C, H, L, V = bars(d, CACHE)
    d.sig = {"rule": swing.signals(C, H, L, V).to_numpy()}
    rows = grid(d, "main")
    P = CACHE / "pit"
    p = Data(P)
    C, H, L, V = bars(p, P)
    p.sig = {"rule": swing.signals(C, H, L, V).to_numpy() & membership(p, P)}
    rows += grid(p, "pit")
    G = pd.DataFrame(rows)
    G.to_csv(CACHE / "stop_grid_fixed.csv", index=False)
    for c in ("IS_cagr", "IS_dd", "OOS_cagr", "OOS_dd"):
        G[c] = (G[c] * 100).round(1)
    for u in ("main", "pit"):
        g = G[G.universe == u].sort_values("IS_sh", ascending=False)
        print(f"\n== {u}: 12 הטובים לפי שארפ 2016-20")
        print(g.head(12).round(3).to_string(index=False))
        print("today's rule:")
        print(g[(g.stop == 1.5) & (g.days == 15) & (g.risk == 0.005)].round(3).to_string(index=False))


if __name__ == "__main__":
    main()
