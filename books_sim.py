"""
שני רעיונות מהספרים על כלל הסווינג:

  1. ירידה בפתיחה ביום הכניסה (Chan, Algorithmic Trading פרק 4 "Buy-on-Gap";
     Larry Williams "Oops"): חלוקת העסקאות לפי הפער בין הפתיחה ביום הכניסה לסגירה
     של יום האות, ביחידות ATR, ולפי פתיחה מתחת לשפל של יום האות.
  2. כלל 6% החודשי (Elder, Come Into My Trading Room): כשהתיק ירד X% מהשווי בסוף
     החודש הקודם - בלי כניסות חדשות עד סוף החודש.

הבחירה על 2016-2020 בלבד, הבדיקה על 2021-2025.

    python books_sim.py [variants_cache]
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import swing
from ideas_sim import RISK, CAP, MAXPOS, stats, trades
from swing_sim import SPLIT, Data, pstats

CACHE = Path(sys.argv[1] if len(sys.argv) > 1 else "variants_cache")


def portfolio(d, t, score, month_stop=None):
    t = t.copy()
    t["score"] = [score[s, k] for s, k in zip(t.s, t.k)]
    t["score"] = t["score"].fillna(-np.inf)
    by_day = {e: g.sort_values("score", ascending=False) for e, g in t.groupby("e")}
    cash, pos, eq = 1.0, [], []
    start = int(t.e.min())
    month_ref, blocked, cur_month, last_val = 1.0, False, None, 1.0
    for j in range(start, len(d.idx)):
        m = (d.idx[j].year, d.idx[j].month)
        if m != cur_month:                     # חודש חדש: הבסיס = שווי בסגירה האחרונה
            cur_month, month_ref, blocked = m, last_val, False
        keep = []
        for p in pos:
            if p["x"] == j:
                cash += p["amt"] * (1 + p["ret"])
            else:
                keep.append(p)
        pos = keep
        # הכניסות היום בפתיחה: לפי השווי של אתמול בסגירה (בלי להציץ בסגירה של היום)
        if month_stop is not None and last_val < month_ref * (1 - month_stop):
            blocked = True
        g = by_day.get(j)
        if g is not None and not blocked:
            held = {p["k"] for p in pos}
            for r in g.itertuples():
                if len(pos) >= MAXPOS:
                    break
                if r.k in held:
                    continue
                amt = min(last_val * RISK / r.riskpct, last_val * CAP, cash)
                if amt <= last_val * 0.01:
                    break
                cash -= amt
                pos.append({"k": r.k, "x": int(r.x), "ret": r.ret, "amt": amt, "px0": r.px0})
                held.add(r.k)
        val = cash + sum(p["amt"] * d.C[j, p["k"]] / p["px0"] if d.C[j, p["k"]] == d.C[j, p["k"]]
                         else p["amt"] for p in pos)
        eq.append(val)
        last_val = val
    return pd.Series(eq, index=d.idx[start:])


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    pd.set_option("display.width", 250)
    d = Data(CACHE)
    b = pd.read_pickle(CACHE / "bars.pkl.gz")
    f = lambda k: b[k].reindex(index=d.idx, columns=d.cols).astype("float64")
    C, H, L, V = f("close"), f("high"), f("low"), f("volume")
    base = swing.signals(C, H, L, V).to_numpy()
    mom = swing.indicators(C, H, L, V)["mom"].to_numpy()
    t = trades(d, base)
    t["gap_atr"] = [(d.O[e, k] - d.C[s, k]) / d.atr[s, k] for s, k, e in zip(t.s, t.k, t.e)]
    t["below_low"] = [d.O[e, k] < d.L[s, k] for s, k, e in zip(t.s, t.k, t.e)]
    t["oos"] = d.idx[t.s] >= SPLIT

    # 1. לפי הפער בפתיחה
    bins = [-np.inf, -1.0, -0.5, -0.2, 0.0, 0.2, 0.5, np.inf]
    t["gap_bin"] = pd.cut(t.gap_atr, bins)
    tab = t.groupby(["gap_bin", "oos"], observed=True).agg(
        n=("R", "size"), R=("R", "mean"), win=("ret", lambda x: (x > 0).mean())).unstack()
    print("== פער בפתיחה ביום הכניסה (ATR), R לעסקה [False=2016-20, True=2021-25]")
    print(tab.round(3).to_string())
    tab2 = t.groupby(["below_low", "oos"]).agg(n=("R", "size"), R=("R", "mean")).unstack()
    print("\n== פתיחה מתחת לשפל של יום האות")
    print(tab2.round(3).to_string())

    rows = [{"test": "בסיס", **stats(d, t, portfolio(d, t, mom))}]
    for lab, m in (("רק פער של 0.5- ATR ומטה", t.gap_atr <= -0.5),
                   ("בלי פער של 0.5- ATR ומטה", t.gap_atr > -0.5),
                   ("בלי פער של 1- ATR ומטה", t.gap_atr > -1.0),
                   ("רק פתיחה מתחת לשפל", t.below_low),
                   ("בלי פתיחה מתחת לשפל", ~t.below_low),
                   ("בלי פער עלייה של 0.5+ ATR", t.gap_atr < 0.5)):
        sub = t[m]
        rows.append({"test": "1: " + lab, **stats(d, sub, portfolio(d, sub, mom))})
    # 2. עצירה חודשית
    for x in (0.04, 0.06, 0.08, 0.10):
        rows.append({"test": f"2: עצירה אחרי ירידה של {int(x*100)}% בחודש",
                     **stats(d, t, portfolio(d, t, mom, month_stop=x))})
    # כמה חודשים נעצרו (6%)
    R = pd.DataFrame(rows)
    fmt = R.copy()
    for c in fmt.columns:
        if c.endswith(("cagr", "dd", "win")):
            fmt[c] = (fmt[c] * 100).round(1)
        elif c.endswith(("_R", "_t", "sharpe")):
            fmt[c] = fmt[c].round(3 if c.endswith("_R") else 2)
    print()
    print(fmt.to_string(index=False))
    R.to_csv(CACHE / "books_table.csv", index=False, encoding="utf-8-sig")
    # שנה-שנה לכלל 6% מול הבסיס
    e0 = portfolio(d, t, mom)
    e6 = portfolio(d, t, mom, month_stop=0.06)
    yr = pd.DataFrame({"בסיס": e0.resample("YE").last().pct_change(),
                       "6%": e6.resample("YE").last().pct_change()})
    print("\n== תשואה שנתית\n", (yr * 100).round(1).to_string())


if __name__ == "__main__":
    main()