"""
בדיקת קווי המגמה (trendlines.py) על כלל הסווינג: האם מיקום המחיר ביחס לקו,
שיפוע הקו ומספר הנגיעות מנבאים את תוצאת העסקה, והאם פריצת קו מגמה יורד קצר
היא נקודת כניסה טובה יותר מקנייה למחרת. בחירה על 2016-2020, בדיקה על 2021-2025.

    python trendline_sim.py [variants_cache]
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import swing
import trendlines as tl
from swing_sim import SPLIT, Data, Exit, all_trades, portfolio, pstats, summary, trade

CACHE = Path(sys.argv[1] if len(sys.argv) > 1 else "variants_cache")


def main():
    d = Data(CACHE)
    b = pd.read_pickle(CACHE / "bars.pkl.gz")
    f = lambda k: b[k].reindex(columns=d.cols).astype("float64")
    C, H, L, V = f("close"), f("high"), f("low"), f("volume")
    sig = swing.signals(C, H, L, V).to_numpy()
    d.sig = {"rule": sig}
    ex = Exit(swing.STOP_ATR, None, swing.HOLD_DAYS)
    t = all_trades(d, "rule", ex, True)
    t["oos"] = d.idx[t.s] >= SPLIT

    rows = []
    for s, k in zip(t.s, t.k):
        ft = tl.features(d.H[:, k], d.L[:, k], d.C[:, k], d.atr[:, k], s)
        rows.append(ft or {})
    F = pd.DataFrame(rows, index=t.index)
    t = t.join(F)
    print(f"עסקאות: {len(t)}, עם קו: {t.slope_pct.notna().sum()}")

    def show(col, bins=None, q=5):
        g = t[t[col].notna()]
        cut = pd.cut(g[col], bins) if bins is not None else \
            pd.qcut(g[col].rank(method="first"), q, labels=False)
        tab = g.groupby([cut, "oos"], observed=True).R.agg(["count", "mean"]).unstack().round(3)
        print(f"\n== {col}\n{tab}")

    show("broken", bins=[-0.5, 0.5, 1.5]) if False else None
    g = t[t.broken.notna()]
    print("\n== broken (סגירה מתחת לקו העולה)")
    print(g.groupby([g.broken.astype(bool), "oos"]).R.agg(["count", "mean"]).unstack().round(3))
    show("dist_atr", bins=[-99, -1, 0, 1, 2, 3, 99])
    show("slope_pct")
    show("touches", bins=[0, 2, 3, 5, 999])

    # טריגר כניסה: פריצת קו מגמה יורד קצר (משיא התיקון) בסגירה, עד 5 ימים
    N = len(d.idx)
    out = []
    busy = np.full(len(d.cols), -1)
    spyk = d.cols.index("SPY")
    for s in range(200, N - 2):
        if not d.spy_ok[s]:
            continue
        for k in np.flatnonzero(sig[s]):
            if k == spyk or busy[k] >= s:
                continue
            hs = d.H[max(0, s - 15):s + 1, k]
            if np.all(np.isnan(hs)):
                continue
            p = max(0, s - 15) + int(np.nanargmax(hs))
            e = None
            for j in range(s + 1, min(s + 6, N - 1)):
                sl = tl.resistance_line(d.H[:, k], p, j - 1)
                if sl is None:
                    continue
                if d.C[j, k] > tl.line_at(d.H[p, k], sl, p, j):
                    e = j
                    break
            if e is None:
                continue
            r = trade(d, k, e, ex)          # מחשב כניסה בפתיחה של e+1
            if r is None:
                continue
            busy[k] = r[0]
            out.append((e, k, r[0], r[1], r[2], r[3]))
    tb = pd.DataFrame(out, columns=["s", "k", "x", "ret", "R", "riskpct"])
    print("\n== כניסה בפריצת קו מגמה יורד קצר (מול קנייה למחרת)")
    for lab, tt in (("למחרת (שלנו)", t), ("פריצת קו", tb)):
        i = summary(tt[d.idx[tt.s] < SPLIT], d)
        o = summary(tt[d.idx[tt.s] >= SPLIT], d)
        e_ = portfolio(d, tt[["s", "k", "x", "ret", "R", "riskpct"]], rank="mom")
        a = pstats(e_[e_.index < SPLIT]); bb = pstats(e_[e_.index >= SPLIT])
        print(f"{lab:14} IS n{i['n']} {i['exp_R']:+.3f}R win {i['win']:.0%} | OOS n{o['n']} "
              f"{o['exp_R']:+.3f}R t {o['t']:.1f} win {o['win']:.0%} | תיק IS {a[0]:+.1%} OOS {bb[0]:+.1%} DD {bb[1]:.1%}")
    t.to_pickle(CACHE / "trend_trades.pkl")


if __name__ == "__main__":
    main()
