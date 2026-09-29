"""
נרות יפניים ביום האות, כמידע על כלל הסווינג (הגדרות לפי Nison, נקבעו לפני הבדיקה):

  פטיש      - צל תחתון לפחות פי 2 מהגוף, וסגירה בשליש העליון של טווח היום
  בולען שורי - נר עולה שגופו בולע את גוף הנר היורד של אתמול
  דוג'י      - גוף קטן מ-10% מטווח היום
  סגירה ליד השפל - סגירה בשליש התחתון של טווח היום

הבחירה על 2016-2020 בלבד, הבדיקה על 2021-2025.

    python candles_sim.py [variants_cache]
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import swing
from books_sim import portfolio
from ideas_sim import stats, trades
from swing_sim import SPLIT, Data

CACHE = Path(sys.argv[1] if len(sys.argv) > 1 else "variants_cache")


def shapes(d, s, k):
    o, h, l, c = d.O[s, k], d.H[s, k], d.L[s, k], d.C[s, k]
    po, pc = d.O[s - 1, k], d.C[s - 1, k]
    rng = h - l
    if not (rng > 0) or any(math.isnan(x) for x in (o, h, l, c, po, pc)):
        return None
    body = abs(c - o)
    pos = (c - l) / rng
    return {
        "פטיש": (min(o, c) - l) >= 2 * body and pos >= 2 / 3,
        "בולען שורי": c > o and pc < po and o <= pc and c >= po,
        "דוג'י": body <= 0.1 * rng,
        "סגירה ליד השפל": pos <= 1 / 3,
    }


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    pd.set_option("display.width", 250)
    d = Data(CACHE)
    b = pd.read_pickle(CACHE / "bars.pkl.gz")
    f = lambda k: b[k].reindex(index=d.idx, columns=d.cols).astype("float64")
    C, H, L, V = f("close"), f("high"), f("low"), f("volume")
    mom = swing.indicators(C, H, L, V)["mom"].to_numpy()
    t = trades(d, swing.signals(C, H, L, V).to_numpy())
    sh = [shapes(d, s, k) for s, k in zip(t.s, t.k)]
    t = t[[x is not None for x in sh]].reset_index(drop=True)
    sh = pd.DataFrame([x for x in sh if x is not None])
    t = pd.concat([t, sh], axis=1)
    t["oos"] = d.idx[t.s] >= SPLIT
    names = list(sh.columns)
    t["אף צורה"] = ~t[names].any(axis=1)

    print("== R לעסקה לפי צורת הנר ביום האות")
    rows = []
    for nm in names + ["אף צורה"]:
        for flag in (True, False):
            g = t[t[nm] == flag]
            r = {"צורה": nm, "יש": flag}
            for lab, m in (("16-20", ~g.oos), ("21-25", g.oos)):
                x = g[m]
                r[lab + " n"] = len(x)
                r[lab + " R"] = round(x.R.mean(), 3)
                r[lab + " t"] = round(x.R.mean() / x.R.std() * math.sqrt(len(x)), 2) if len(x) > 2 else None
                r[lab + " win"] = round((x.ret > 0).mean() * 100, 1)
            rows.append(r)
    print(pd.DataFrame(rows).to_string(index=False))

    out = [{"test": "בסיס", **stats(d, t, portfolio(d, t, mom))}]
    for nm in names:
        for lab, m in (("רק " + nm, t[nm]), ("בלי " + nm, ~t[nm])):
            sub = t[m]
            out.append({"test": lab, **stats(d, sub, portfolio(d, sub, mom))})
    R = pd.DataFrame(out)
    R.to_csv(CACHE / "candles_table.csv", index=False, encoding="utf-8-sig")
    fmt = R[["test", "IS_n", "IS_R", "OOS_n", "OOS_R", "IS_cagr", "IS_dd", "OOS_cagr", "OOS_dd", "OOS_sharpe"]].copy()
    for c in ("IS_cagr", "IS_dd", "OOS_cagr", "OOS_dd"):
        fmt[c] = (fmt[c] * 100).round(1)
    for c in ("IS_R", "OOS_R"):
        fmt[c] = fmt[c].round(3)
    fmt["OOS_sharpe"] = fmt["OOS_sharpe"].round(2)
    print("\n== תיק (0.5% סיכון, 15 מקומות)")
    print(fmt.to_string(index=False))


if __name__ == "__main__":
    main()