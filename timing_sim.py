"""
באיזו שעה לקנות ולמכור בכלל הסווינג? (הכלל מהקורס: לא לקנות בתחילת יום המסחר ובסופו)

לכל אות סווינג מהעבר (נרות 5 דקות מ-intraday_fetch.py) משווים שעת כניסה ביום
שאחרי האות ושעת יציאה ביום המסחר ה-15. סטופ 1.5 ATR יומי מתחת למחיר הכניסה
(נבדק מנר הכניסה ואילך; פתיחה מתחת לסטופ = יציאה במחיר הפתיחה). עלות 10bps לצד.

  כניסה: במחיר הפתיחה של נר ה-5 דקות שמתחיל בשעה (שעון ניו יורק); "סגירה" = סגירת היום.
  יציאה: באותו אופן ביום ה-15.

כל הגרסאות רצות על אותן עסקאות בדיוק (רק אותות שיש להם 15 ימים מלאים בנתונים),
וההשוואה לבסיס (פתיחה / סגירה) היא לכל עסקה בנפרד (הפרש זוגי), עם t.
הבחירה על 2016-2020 בלבד; 2021-2025 רק לבדיקה.

    python timing_sim.py [variants_cache]
"""
from __future__ import annotations

import glob
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from swing_sim import COST, SPLIT, Data

CACHE = Path(sys.argv[1] if len(sys.argv) > 1 else "variants_cache")
STOP_ATR, HOLD = 1.5, 15
CLOSE = 960
ENTRIES = {"09:30": 570, "09:35": 575, "09:45": 585, "10:00": 600, "10:30": 630,
           "11:00": 660, "12:00": 720, "14:00": 840, "15:00": 900, "15:30": 930,
           "15:50": 950, "סגירה": CLOSE}
EXITS = {"09:30": 570, "10:00": 600, "12:00": 720, "15:00": 900, "15:30": 930,
         "15:45": 945, "15:50": 950, "סגירה": CLOSE}


def sessions(df: pd.DataFrame):
    t = df.index.tz_convert("America/New_York")
    mins = np.asarray(t.hour * 60 + t.minute)
    keep = (mins >= 570) & (mins < 960)
    df, t, mins = df[keep], t[keep], mins[keep]
    out = []
    for day, ix in pd.Series(np.arange(len(df))).groupby(np.asarray(t.date)):
        ix = ix.to_numpy()
        out.append((day, mins[ix], df[["o", "h", "l", "c"]].to_numpy("float64")[ix]))
    return out


def price_at(m, b, t, off):
    """(מיקום גלובלי, מחיר): פתיחת הנר הראשון שמתחיל ב-t או אחריו; CLOSE = סגירת הנר האחרון."""
    if t == CLOSE:
        return off + len(b) - 1, b[-1, 3], True
    j = np.searchsorted(m, t)
    if j >= len(b):
        return None
    return off + j, b[j, 0], False


def main():
    d = Data(CACHE)
    col = {c: i for i, c in enumerate(d.cols)}
    day_ix = {ts.date(): i for i, ts in enumerate(d.idx)}
    rows, skipped = [], {"short": 0, "partial": 0}
    for p in sorted(glob.glob(str(CACHE / "intraday" / "part_*.pkl.gz"))):
        part = pd.read_pickle(p)
        for (sday, sym), df in part.items():
            s, k = day_ix.get(pd.Timestamp(sday).date()), col.get(sym)
            if s is None or k is None or df.empty:
                continue
            atr = d.atr[s, k]
            if not atr > 0:
                continue
            days = [x for x in sessions(df) if x[0] > pd.Timestamp(sday).date()][:HOLD]
            if len(days) < HOLD:
                skipped["short"] += 1
                continue
            if len(days[0][1]) < 70 or len(days[-1][1]) < 70:     # חצי יום / נתונים חסרים
                skipped["partial"] += 1
                continue
            offs = np.cumsum([0] + [len(x[2]) for x in days])
            B = np.vstack([x[2] for x in days])
            o, l = B[:, 0], B[:, 2]
            ex = {}
            for xn, xt in EXITS.items():
                r = price_at(days[-1][1], days[-1][2], xt, offs[-2])
                if r is None:
                    break
                ex[xn] = r
            if len(ex) < len(EXITS):
                skipped["partial"] += 1
                continue
            for en, et in ENTRIES.items():
                r = price_at(days[0][1], days[0][2], et, 0)
                if r is None:
                    continue
                ei, px, at_close = r
                stop = px - STOP_ATR * atr
                first = ei + 1 if at_close else ei
                # הנר הראשון מאז הכניסה שנגע בסטופ
                hit = np.nonzero(l[first:] <= stop)[0]
                hi = first + hit[0] if len(hit) else None
                for xn, (xi, xp, x_close) in ex.items():
                    last = xi if x_close else xi - 1      # יציאה בפתיחת נר xi = לפניו
                    if hi is not None and hi <= last:
                        out = min(o[hi], stop) if hi > ei or at_close else stop
                        if hi == ei and not at_close:
                            out = stop
                    else:
                        out = xp
                    ret = out * (1 - COST) / (px * (1 + COST)) - 1
                    rows.append((sday, sym, en, xn, ret, ret * px / (STOP_ATR * atr)))
        del part
        print(p, len(rows), skipped, flush=True)
    R = pd.DataFrame(rows, columns=["day", "sym", "entry", "exit", "ret", "R"])
    R["oos"] = pd.to_datetime(R.day) >= SPLIT
    R["year"] = pd.to_datetime(R.day).dt.year
    # רק עסקאות שיש להן את כל הגרסאות
    full = R.groupby(["day", "sym"]).size()
    keep = full[full == len(ENTRIES) * len(EXITS)].index
    R = R.set_index(["day", "sym"]).loc[keep].reset_index()
    R.to_pickle(CACHE / "timing_results.pkl")
    base = R[(R.entry == "09:30") & (R.exit == "סגירה")].set_index(["day", "sym"]).R
    out = []
    for (en, xn), g in R.groupby(["entry", "exit"]):
        g = g.set_index(["day", "sym"])
        diff = g.R - base.reindex(g.index)
        row = {"entry": en, "exit": xn}
        for lab, m in (("IS", ~g.oos), ("OOS", g.oos)):
            dd = diff[m]
            row[f"{lab}_R"] = g.R[m].mean()
            row[f"{lab}_win"] = (g.ret[m] > 0).mean()
            row[f"{lab}_dR"] = dd.mean()
            row[f"{lab}_t"] = dd.mean() / (dd.std() / np.sqrt(len(dd))) if dd.std() > 0 else 0.0
            row[f"{lab}_n"] = int(m.sum())
        yr = diff.groupby(g.year).mean()
        row["years_better"] = f"{int((yr > 0).sum())}/{len(yr)}"
        out.append(row)
    T = pd.DataFrame(out)
    pd.set_option("display.width", 250)
    print("\nבסיס = כניסה 09:30, יציאה בסגירה. dR = הפרש ממוצע לעסקה מול הבסיס (זוגי).")
    print(T.round(3).sort_values("IS_R", ascending=False).to_string(index=False))
    T.to_csv(CACHE / "timing_table.csv", index=False, encoding="utf-8-sig")


if __name__ == "__main__":
    main()
