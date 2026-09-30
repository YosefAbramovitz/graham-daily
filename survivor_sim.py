"""
הטיית שורדים (Chan, Algorithmic Trading פרק 1: מסוכנת במיוחד ללונג שקונה ירידות).

כלל הסווינג על S&P 500 בלבד, שלוש גרסאות על אותם נרות (survivor_build.py):
  היסטורי   - אות רק אם המניה הייתה במדד ביום האות (כולל מניות שיצאו מאז)
  רשימת היום - רק מניות שבמדד היום (כמו כל הבדיקות שלנו עד עכשיו)
  שתיהן     - היו במדד אז וגם היום

    python survivor_sim.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

import swing
from books_sim import portfolio
from ideas_sim import stats, trades
from swing_sim import SPLIT, Data

PIT = Path("variants_cache/pit")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    pd.set_option("display.width", 250)
    d = Data(PIT)
    b = pd.read_pickle(PIT / "bars.pkl.gz")
    f = lambda k: b[k].reindex(index=d.idx, columns=d.cols).astype("float64")
    C, H, L, V = f("close"), f("high"), f("low"), f("volume")
    sig = swing.signals(C, H, L, V)
    mom = swing.indicators(C, H, L, V)["mom"].to_numpy()
    comp = pd.read_csv(PIT / "components.csv", parse_dates=["date"]).sort_values("date")
    mem = pd.DataFrame(False, index=d.idx, columns=d.cols)
    rows = list(comp.itertuples())
    for i, r in enumerate(rows):
        end = rows[i + 1].date if i + 1 < len(rows) else pd.Timestamp("2100-01-01")
        m = (d.idx >= r.date) & (d.idx < end)
        if not m.any():
            continue
        cols = [c for c in r.tickers.split(",") if c in mem.columns]
        mem.loc[m, cols] = True
    today = set(comp.iloc[-1].tickers.split(","))
    in_today = pd.Series([c in today for c in d.cols], index=d.cols)
    s = sig.to_numpy()
    versions = {
        "היסטורי (הרכב המדד בכל יום)": s & mem.to_numpy(),
        "רשימת היום (הטיית שורדים)": s & in_today.to_numpy()[None, :],
        "היו אז וגם היום": s & mem.to_numpy() & in_today.to_numpy()[None, :],
    }
    rows_out = []
    for lab, sg in versions.items():
        t = trades(d, sg)
        gone = t[~in_today.to_numpy()[t.k]]
        st = stats(d, t, portfolio(d, t, mom))
        st.update(test=lab, n_gone=len(gone), R_gone=gone.R.mean() if len(gone) else np.nan)
        rows_out.append(st)
        print(lab, len(t), flush=True)
    R = pd.DataFrame(rows_out)
    R.to_csv("variants_cache/survivor_table.csv", index=False, encoding="utf-8-sig")
    cols = ["test", "IS_n", "IS_R", "OOS_n", "OOS_R", "IS_cagr", "IS_dd", "OOS_cagr", "OOS_dd", "OOS_sharpe", "n_gone", "R_gone"]
    out = R[cols].copy()
    for c in ("IS_cagr", "IS_dd", "OOS_cagr", "OOS_dd"):
        out[c] = (out[c] * 100).round(1)
    print(out.round(3).to_string(index=False))


if __name__ == "__main__":
    main()