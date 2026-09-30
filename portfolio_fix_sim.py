"""
תיקון באג בסימולציית התיק (ספט' 2026): עסקה שנעצרה ביום הכניסה עצמו (x == יום הכניסה)
נכנסה לתיק אחרי שלב היציאות ולא יצאה לעולם - "זומבי" שתפס מקום ומזומן עד סוף הבדיקה.
בבסיס נכנסו לתיק רק 178 עסקאות ב-9 שנים. כל תוצאות התיק (לא התוחלת לעסקה) חושבו מחדש.

  1. הבסיס אחרי התיקון, ורשת גודל פוזיציה (סיכון לעסקה, תקרה, מספר פוזיציות) - בחירה על 2016-20.
  2. בדיקות קודמות ברמת התיק: ירדה לבד מהענף, מסנן שוק 12 ח', הרכב S&P 500 היסטורי.

    python portfolio_fix_sim.py
"""
from __future__ import annotations

import itertools
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import swing
from swing_sim import SPLIT, Data, Exit, all_trades, portfolio, pstats

CACHE = Path("variants_cache")


def run(d, t, **kw):
    e = portfolio(d, t, rank="mom", **kw)
    ci, ddi, shi = pstats(e[e.index < SPLIT])
    co, ddo, sho = pstats(e[e.index >= SPLIT])
    return dict(IS_cagr=ci, IS_dd=ddi, IS_sh=shi, OOS_cagr=co, OOS_dd=ddo, OOS_sh=sho)


def load(cache):
    d = Data(cache)
    b = pd.read_pickle(cache / "bars.pkl.gz")
    f = lambda k: b[k].reindex(index=d.idx, columns=d.cols).astype("float64")
    C, H, L, V = f("close"), f("high"), f("low"), f("volume")
    return d, C, H, L, V


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    pd.set_option("display.width", 250)
    d, C, H, L, V = load(CACHE)
    d.sig = {"rule": swing.signals(C, H, L, V).to_numpy()}
    ex = Exit(swing.STOP_ATR, None, swing.HOLD_DAYS)
    t = all_trades(d, "rule", ex, True)
    print(f"עסקאות: {len(t)}, מהן נעצרו ביום הכניסה: {(t.x == t.s + 1).sum()}", flush=True)
    spy = pd.Series(d.C[:, d.spy_col], index=d.idx)
    rows = [{"test": "SPY", **dict(zip(["IS_cagr", "IS_dd", "IS_sh"], pstats(spy[spy.index < SPLIT]))),
             **dict(zip(["OOS_cagr", "OOS_dd", "OOS_sh"], pstats(spy[spy.index >= SPLIT])))}]
    grid = []
    for risk, cap, mp in itertools.product([0.0025, 0.005, 0.0075, 0.01, 0.015], [0.1, 0.2], [5, 10, 15, 20]):
        r = run(d, t, risk=risk, max_pos=mp, cap=cap)
        grid.append({"risk": risk, "cap": cap, "max_pos": mp, **r})
    G = pd.DataFrame(grid)
    G.to_csv(CACHE / "sizing_grid_fixed.csv", index=False)
    cur = G[(G.risk == 0.005) & (G.cap == 0.2) & (G.max_pos == 15)].iloc[0]
    best = G.sort_values("IS_sh", ascending=False).iloc[0]
    print("\n== רשת גודל (מיון לפי שארפ 2016-20), 10 הראשונות")
    show = G.sort_values("IS_sh", ascending=False).head(10).copy()
    for c in ("IS_cagr", "IS_dd", "OOS_cagr", "OOS_dd"):
        show[c] = (show[c] * 100).round(1)
    print(show.round(2).to_string(index=False))
    rows.append({"test": "הכלל של היום (0.5%, 20%, 15)", **cur.to_dict()})
    rows.append({"test": f"הטוב ב-2016-20: {best.risk*100:.2f}%, {best.cap*100:.0f}%, {int(best.max_pos)}", **best.to_dict()})

    # ירדה לבד מהענף: דילוג
    sec = json.loads((CACHE / "sectors.json").read_text(encoding="utf-8"))
    sc = swing.sector_change(C, sec).to_numpy()
    alone = np.array([sc[s, k] >= 0 if sc[s, k] == sc[s, k] else False for s, k in zip(t.s, t.k)])
    rows.append({"test": "בלי אותות 'לבד' (הענף עלה)", **run(d, t[~alone], risk=0.005, max_pos=15, cap=0.2)})
    # מסנן 12 ח'
    g12 = (spy / spy.shift(252) - 1 > 0).to_numpy()
    saved = d.spy_ok
    d.spy_ok = g12
    t12 = all_trades(d, "rule", ex, True)
    d.spy_ok = saved
    rows.append({"test": "מסנן שוק: תשואת 12 ח' של SPY", **run(d, t12, risk=0.005, max_pos=15, cap=0.2)})
    d.spy_ok = np.ones(len(d.idx), bool)
    tn = all_trades(d, "rule", ex, True)
    d.spy_ok = saved
    rows.append({"test": "בלי מסנן שוק", **run(d, tn, risk=0.005, max_pos=15, cap=0.2)})

    # S&P 500 היסטורי מול רשימת היום
    P = CACHE / "pit"
    if (P / "bars.pkl.gz").exists():
        dp, Cp, Hp, Lp, Vp = load(P)
        comp = pd.read_csv(P / "components.csv", parse_dates=["date"]).sort_values("date")
        mem = pd.DataFrame(False, index=dp.idx, columns=dp.cols)
        rr = list(comp.itertuples())
        for i, r in enumerate(rr):
            end = rr[i + 1].date if i + 1 < len(rr) else pd.Timestamp("2100-01-01")
            m = (dp.idx >= r.date) & (dp.idx < end)
            if m.any():
                mem.loc[m, [c for c in r.tickers.split(",") if c in mem.columns]] = True
        today = np.array([c in set(comp.iloc[-1].tickers.split(",")) for c in dp.cols])
        s = swing.signals(Cp, Hp, Lp, Vp).to_numpy()
        for lab, sg in (("S&P 500 היסטורי (בלי הטיית שורדים)", s & mem.to_numpy()),
                        ("S&P 500 רשימת היום", s & today[None, :])):
            dp.sig = {"rule": sg}
            tp = all_trades(dp, "rule", ex, True)
            rows.append({"test": lab, "IS_R": tp.R[dp.idx[tp.s] < SPLIT].mean(),
                         "OOS_R": tp.R[dp.idx[tp.s] >= SPLIT].mean(), **run(dp, tp, risk=0.005, max_pos=15, cap=0.2)})
    R = pd.DataFrame(rows)
    R.to_csv(CACHE / "portfolio_fixed.csv", index=False, encoding="utf-8-sig")
    out = R[[c for c in ["test", "IS_R", "OOS_R", "IS_cagr", "IS_dd", "IS_sh", "OOS_cagr", "OOS_dd", "OOS_sh"] if c in R]].copy()
    for c in ("IS_cagr", "IS_dd", "OOS_cagr", "OOS_dd"):
        out[c] = (out[c] * 100).round(1)
    print("\n== תיק אחרי התיקון")
    print(out.round(3).to_string(index=False))
    # שנה-שנה לכלל של היום
    e = portfolio(d, t, risk=0.005, max_pos=15, cap=0.2, rank="mom")
    yr = e.resample("YE").last().pct_change() * 100
    sy = spy.resample("YE").last().pct_change() * 100
    print("\n== שנה-שנה (תיק / SPY)\n", pd.DataFrame({"תיק": yr.round(1), "SPY": sy.round(1)}).to_string())


if __name__ == "__main__":
    main()