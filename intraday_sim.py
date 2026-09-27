"""
האם תזמון תוך-יומי (נרות 5 דקות) לפי קווי מגמה משפר את כלל הסווינג?

לכל אות סווינג מהעבר (intraday_fetch.py הביא את נרות ה-5 דקות) משווים:

כניסה (ביום שאחרי האות; אם אין טריגר - גם ביום שאחריו, ואחר כך מוותרים):
  open   - קנייה בפתיחה (הכלל של היום, מחושב על נרות 5 דקות)
  tl     - פריצת קו מגמה יורד תוך-יומי: המעטפת העליונה של השיאים משיא היום עד
           הנר הקודם; קנייה בסגירת הנר הראשון שנסגר מעל הקו
  tl_eod - כמו tl, ואם לא הייתה פריצה עד סוף היום הראשון - קנייה בסגירה
  orb    - סגירה מעל הגבוה של 30 הדקות הראשונות

יציאה:
  base   - סטופ 1.5 ATR יומי, יציאה בסגירה של יום המסחר ה-15
  tl_1r  - כמו base, ובנוסף: אחרי רווח של 1R, יציאה כשנר 5 דקות נסגר מתחת לקו
           המגמה העולה התוך-יומי (המעטפת התחתונה של השפלים מאז הכניסה)
  tl_any - יציאה בשבירת הקו התוך-יומי בכל שלב (מהיום השני)

הבחירה על 2016-2020, הבדיקה על 2021-2025.

    python intraday_sim.py [variants_cache]
"""
from __future__ import annotations

import glob
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from swing_sim import COST, SPLIT, Data

CACHE = Path(sys.argv[1] if len(sys.argv) > 1 else "variants_cache")
STOP_ATR, HOLD = 1.5, 15


def sessions(df: pd.DataFrame):
    """רשימת (תאריך, מערך OHLC) לשעות המסחר הרגילות בלבד."""
    t = df.index.tz_convert("America/New_York")
    mins = t.hour * 60 + t.minute
    df = df[(mins >= 570) & (mins < 960)]
    t = t[(mins >= 570) & (mins < 960)]
    out = []
    for day, g in df.groupby(t.date):
        out.append((day, g[["o", "h", "l", "c"]].to_numpy(dtype="float64")))
    return out


def upper_hull_level(h: np.ndarray, p: int, i: int) -> float | None:
    """רמת קו ההתנגדות היורד (משיא p) בנר i, מהשיאים p+1..i-1."""
    if i - p < 3:
        return None
    js = np.arange(p + 1, i)
    slope = np.max((h[p + 1:i] - h[p]) / (js - p))
    if slope >= 0:
        return None                  # אין קו יורד
    return h[p] + slope * (i - p)


def lower_hull_level(l: np.ndarray, a: int, i: int) -> float | None:
    """רמת קו התמיכה העולה (מהשפל a) בנר i, מהשפלים a+1..i-2."""
    if i - a < 6:
        return None
    js = np.arange(a + 1, i - 1)
    slope = np.min((l[a + 1:i - 1] - l[a]) / (js - a))
    if slope <= 0:
        return None
    return l[a] + slope * (i - a)


def entry(days, mode):
    """(מספר יום, מספר נר, מחיר) או None."""
    for di in range(min(2, len(days))):
        b = days[di][1]
        if len(b) < 8:
            continue
        o, h, l, c = b[:, 0], b[:, 1], b[:, 2], b[:, 3]
        if mode == "open":
            return di, 0, o[0]
        if mode == "orb":
            hi = h[:6].max()
            for i in range(6, len(b)):
                if c[i] > hi:
                    return di, i, c[i]
            continue
        # tl / tl_eod
        for i in range(4, len(b)):
            p = int(np.argmax(h[:i]))
            lvl = upper_hull_level(h, p, i)
            if lvl is not None and c[i] > lvl:
                return di, i, c[i]
        if mode == "tl_eod" and di == 0:
            return 0, len(b) - 1, c[-1]
    return None


def run(days, di, bi, px, atr, exit_mode):
    """מחזיר (תשואה, R, מספר ימים) לעסקה."""
    stop = px - STOP_ATR * atr
    risk = px - stop
    last_day = min(len(days) - 1, HOLD - 1)          # היום ה-15 מהיום הראשון
    # רצף הנרות מהכניסה ואילך, עם מספר היום של כל נר
    bars, dayno = [], []
    for k in range(di, last_day + 1):
        b = days[k][1]
        s = bi + 1 if k == di else 0
        bars.append(b[s:]); dayno += [k] * len(b[s:])
    if not bars or sum(len(x) for x in bars) == 0:
        return (days[last_day][1][-1, 3] if False else None)
    B = np.vstack(bars); dayno = np.array(dayno)
    o, h, l, c = B[:, 0], B[:, 1], B[:, 2], B[:, 3]
    armed = exit_mode == "tl_any"
    anchor = 0
    out = None
    for i in range(len(B)):
        if o[i] <= stop:
            out = o[i]; break
        if l[i] <= stop:
            out = stop; break
        if exit_mode == "base":
            continue
        if not armed and exit_mode == "tl_1r" and h[i] >= px + risk:
            armed, anchor = True, i
        if armed and dayno[i] > di:
            a = anchor + int(np.argmin(l[anchor:i + 1])) if i > anchor else anchor
            lvl = lower_hull_level(l, a, i)
            if lvl is not None and c[i] < lvl:
                out = c[i]; break
    if out is None:
        out, i = c[-1], len(B) - 1
    ret = out * (1 - COST) / (px * (1 + COST)) - 1
    return ret, ret * px / risk, int(dayno[min(i, len(B) - 1)]) - di + 1


def main():
    d = Data(CACHE)
    col = {c: i for i, c in enumerate(d.cols)}
    day_ix = {ts.date(): i for i, ts in enumerate(d.idx)}
    data = {}
    for p in sorted(glob.glob(str(CACHE / "intraday" / "part_*.pkl.gz"))):
        data.update(pd.read_pickle(p))
    print(f"חלונות עם נתונים: {len(data)}", flush=True)
    rows = []
    for (sday, sym), df in data.items():
        s = day_ix.get(pd.Timestamp(sday).date())
        k = col.get(sym)
        if s is None or k is None or df.empty:
            continue
        atr = d.atr[s, k]
        if not atr > 0:
            continue
        days = [x for x in sessions(df) if x[0] > pd.Timestamp(sday).date()]
        if len(days) < 2:
            continue
        for em in ("open", "tl", "tl_eod", "orb"):
            e = entry(days, em)
            if e is None:
                continue
            for xm in ("base", "tl_1r", "tl_any"):
                if em != "open" and xm != "base":
                    continue
                r = run(days, *e, atr, xm)
                if r is None:
                    continue
                rows.append((sday, sym, em, xm, *r))
    R = pd.DataFrame(rows, columns=["day", "sym", "entry", "exit", "ret", "R", "days"])
    R["oos"] = pd.to_datetime(R.day) >= SPLIT
    tab = R.groupby(["entry", "exit", "oos"]).agg(n=("R", "size"), R=("R", "mean"),
                                                  win=("ret", lambda x: (x > 0).mean()),
                                                  ret=("ret", "mean"), days=("days", "mean"))
    print(tab.round(3).to_string())
    R.to_pickle(CACHE / "intraday_results.pkl")


if __name__ == "__main__":
    main()
