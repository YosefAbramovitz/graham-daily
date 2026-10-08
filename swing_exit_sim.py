"""
יציאות לסווינג, בדיקה לאחור (אוקטובר 2026).

הרקע: בשנה 10.2025-10.2026 הסווינג הניב -2.6% מול SPY +17.4%. היציאות ביום ה-20 הרוויחו
בממוצע +2.9%, אבל 25 עסקאות שנעצרו בסטופ הפסידו כ-14% כל אחת ומחקו את הרווח.
stop_fix_sim.py כבר בחר סטופ x ימי החזקה. כאן נבדקים סוגי יציאה אחרים על אותו כלל
כניסה ואותו גודל פוזיציה (גודל לפי ציון האיכות ומרחק של 4 ATR, כמו היום), כך שרק
היציאה משתנה.

הגרסאות נקבעו מראש. הבחירה היא לפי שארפ ב-2016-2020, והשיפוט הוא לפי 2021-2025 ולפי
הרכב S&P 500 ההיסטורי (pit, בלי הטיית שורדים). 2025 מוצגת גם לבד.

    python swing_exit_sim.py [--cache variants_cache] [--out swing_exit_results.json]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

import swing
from swing_sim import COST, SPLIT, Data, pstats
from stop_fix_sim import bars, membership

Y2025 = pd.Timestamp("2025-01-01")
INF = float("inf")


@dataclass(frozen=True)
class X:
    name: str
    stop_atr: Optional[float] = swing.STOP_ATR   # None = בלי סטופ
    days: int = swing.HOLD_DAYS
    target_atr: Optional[float] = None            # יעד מעל הכניסה, במכפלות ATR
    trail_atr: Optional[float] = None             # סטופ נגרר מתחת לסגירה הגבוהה מאז הכניסה
    breakeven_atr: Optional[float] = None         # סגירה מעל כניסה+X ATR מעלה את הסטופ לכניסה
    sma5_exit: bool = False                       # סגירה מעל ממוצע 5 = יציאה בסגירה
    rsi_exit: Optional[float] = None              # RSI14 בסגירה מעל X = יציאה בסגירה
    early_day: Optional[int] = None               # ביום ה-N, סגירה מתחת לכניסה = יציאה


VARIANTS = [
    X("היום: סטופ 4 ATR, 20 יום"),
    X("בלי סטופ, 20 יום", stop_atr=None),
    X("סטופ 2 ATR, 20 יום", stop_atr=2.0),
    X("סטופ 4 ATR, 10 ימים", days=10),
    X("יעד 2 ATR", target_atr=2.0),
    X("יעד 3 ATR", target_atr=3.0),
    X("סטופ נגרר 3 ATR", trail_atr=3.0),
    X("סטופ נגרר 4 ATR", trail_atr=4.0),
    X("סטופ לכניסה אחרי +2 ATR", breakeven_atr=2.0),
    X("יציאה מעל ממוצע 5", sma5_exit=True),
    X("יציאה ב-RSI מעל 50", rsi_exit=50.0),
    X("ביום 5 בהפסד: יציאה", early_day=5),
]


def trade(d: Data, k: int, s: int, x: X):
    """אות בסגירת s, כניסה בפתיחת s+1. מחזיר (יום יציאה, תשואה, אחוז מרחק הגודל)."""
    e = s + 1
    if e >= len(d.idx):
        return None
    entry, a = d.O[e, k], d.atr[s, k]
    if not (entry > 0) or not (a > 0):
        return None
    stop = entry - x.stop_atr * a if x.stop_atr else -INF
    target = entry + x.target_atr * a if x.target_atr else INF
    last = min(e + x.days - 1, len(d.idx) - 1)
    px, j, best = None, e, entry
    for j in range(e, last + 1):
        o, h, l, c = d.O[j, k], d.H[j, k], d.L[j, k], d.C[j, k]
        if math.isnan(c):
            continue
        if j > e and not math.isnan(o):
            if o <= stop or o >= target:
                px = o
                break
        if l <= stop:                      # סטופ קודם (שמרני)
            px = stop
            break
        if h >= target:
            px = target
            break
        if (x.sma5_exit and c > d.sma5[j, k]) or (x.rsi_exit and d.r14[j, k] > x.rsi_exit) \
                or (x.early_day and j - e + 1 == x.early_day and c < entry):
            px = c
            break
        best = max(best, c)
        if x.trail_atr:
            stop = max(stop, best - x.trail_atr * a)
        if x.breakeven_atr and c >= entry + x.breakeven_atr * a:
            stop = max(stop, entry)
    if px is None:
        while math.isnan(d.C[j, k]) and j > e:
            j -= 1
        px = d.C[j, k]
    ret = px * (1 - COST) / (entry * (1 + COST)) - 1
    return j, ret, swing.STOP_ATR * a / entry


def trades(d: Data, x: X, q: np.ndarray) -> pd.DataFrame:
    out, busy = [], np.full(len(d.cols), -1)
    for s in range(200, len(d.idx) - 1):
        if not d.spy_ok[s]:
            continue
        for k in np.flatnonzero(d.sig["rule"][s]):
            if k == d.spy_col or busy[k] >= s:
                continue
            r = trade(d, k, s, x)
            if r is None:
                continue
            busy[k] = r[0]
            out.append((s, k, r[0], r[1], r[2], q[s, k]))
    return pd.DataFrame(out, columns=["s", "k", "x", "ret", "riskpct", "quality"])


def portfolio(d: Data, t: pd.DataFrame):
    """כמו התיק החי: סיכון לפי ציון האיכות, עד 15 פוזיציות, עד 20% לפוזיציה, לפי מומנטום."""
    t = t.copy()
    t["score"] = [d.mom[s, k] for s, k in zip(t.s, t.k)]
    by_day = {s: g.sort_values("score", ascending=False) for s, g in t.groupby("s")}
    cash, pos, eq, inv = 1.0, [], [], []
    start = int(t.s.min())
    for j in range(start, len(d.idx)):
        keep = []
        for p in pos:
            if p["x"] <= j:
                cash += p["amt"] * (1 + p["ret"])
            else:
                keep.append(p)
        pos = keep
        held_val = sum(p["amt"] * d.C[j, p["k"]] / p["px0"] if d.C[j, p["k"]] == d.C[j, p["k"]]
                       else p["amt"] for p in pos)
        val = cash + held_val
        eq.append(val)
        inv.append(held_val / val if val else 0.0)
        g = by_day.get(j - 1)
        if g is None:
            continue
        held = {p["k"] for p in pos}
        for r in g.itertuples():
            if len(pos) >= swing.MAX_POSITIONS:
                break
            risk = swing.QUALITY_RISK.get(int(r.quality), 0.0)
            if r.k in held or risk <= 0:
                continue
            amt = min(val * risk / r.riskpct, val * swing.MAX_POSITION_PCT, cash)
            if amt <= val * 0.01:
                break
            cash -= amt
            pos.append({"k": r.k, "x": int(r.x), "ret": r.ret, "amt": amt, "px0": d.O[j, r.k]})
            held.add(r.k)
    idx = d.idx[start:]
    return pd.Series(eq, index=idx), pd.Series(inv, index=idx)


def run(d: Data, q: np.ndarray, label: str) -> list:
    out = []
    for x in VARIANTS:
        t = trades(d, x, q)
        e, inv = portfolio(d, t)
        row = {"universe": label, "variant": x.name, "trades": len(t),
               "win": round(float((t.ret > 0).mean()), 3), "avg_ret": round(float(t.ret.mean()), 4)}
        for lab, m in (("IS", e.index < SPLIT), ("OOS", e.index >= SPLIT), ("2025", e.index >= Y2025)):
            c, dd, sh = pstats(e[m])
            row.update({f"{lab}_cagr": round(c, 4), f"{lab}_dd": round(dd, 4),
                        f"{lab}_sh": round(sh, 2)})
        row["OOS_invested"] = round(float(inv[inv.index >= SPLIT].mean()), 2)
        out.append(row)
        print(f"{label:5} {x.name:28} IS {row['IS_cagr']*100:+6.1f}% sh {row['IS_sh']:.2f} | "
              f"OOS {row['OOS_cagr']*100:+6.1f}% dd {row['OOS_dd']*100:6.1f}% sh {row['OOS_sh']:.2f} | "
              f"2025 {row['2025_cagr']*100:+6.1f}% | מושקע {row['OOS_invested']*100:.0f}%", flush=True)
    return out


def load(path: Path, pit: bool):
    d = Data(path)
    C, H, L, V = bars(d, path)
    sig = swing.signals(C, H, L, V).to_numpy()
    if pit:
        sig = sig & membership(d, path)
    d.sig = {"rule": sig}
    q = swing.quality(swing.indicators(C, H, L, V)).to_numpy()
    return d, np.nan_to_num(q).astype(int)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="variants_cache")
    ap.add_argument("--out", default="swing_exit_results.json")
    a = ap.parse_args()
    cache = Path(a.cache)
    rows = []
    d, q = load(cache, False)
    rows += run(d, q, "main")
    spy = pd.Series(d.C[:, d.spy_col], index=d.idx)
    bench = {lab: [round(v, 4) for v in pstats(spy[m])] for lab, m in
             (("IS", spy.index < SPLIT), ("OOS", spy.index >= SPLIT), ("2025", spy.index >= Y2025))}
    print("SPY (cagr, dd, sharpe):", bench, flush=True)
    if (cache / "pit" / "bars.pkl.gz").exists():
        p, qp = load(cache / "pit", True)
        rows += run(p, qp, "pit")
    best = max((r for r in rows if r["universe"] == "main"), key=lambda r: r["IS_sh"])
    print(f"\nנבחרה לפי שארפ 2016-2020: {best['variant']}")
    Path(a.out).write_text(json.dumps({"variants": [asdict(x) for x in VARIANTS], "rows": rows,
                                       "spy": bench, "chosen_by_IS_sharpe": best["variant"]},
                                      ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"נשמר ל-{a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
