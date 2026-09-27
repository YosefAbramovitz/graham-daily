"""
שלב ב' של בדיקת השינויים מהקורס: הרצת הבסיס וכל גרסה על אותם נתונים.

הבסיס הוא הכלל שלנו היום: בכל סוף חודש, המניות שעברו את המסך (לפי תשואת
הרווח התפעולי, הזולה קודם) נקנות למחרת בפתיחה אם יש מקום פנוי בתיק; יציאה
ב-+50% (פקודת לימיט) או בסוף השנה הקלנדרית השנייה אחרי הקנייה. בלי סטופ.

כל גרסה משנה דבר אחד בלבד, ומדווחות שתי מדידות:
  1. תיק: תשואה שנתית, ירידה מקסימלית, שארפ - כמו שהיה קורה בפועל.
  2. עסקאות: לכל מסנן כניסה, האם העסקאות שהמסנן היה מדלג עליהן באמת היו
     גרועות מאלה שהשאיר (הפרש ממוצעים ו-t). זו המדידה הנקייה, כי היא לא
     תלויה במזל של איזה מקום התפנה מתי.
ושתי חצאי תקופה בנפרד, כדי לראות אם יתרון מחזיק או שהוא מקרה של שנה אחת.

    python variants_sim.py [--cache variants_cache] [--slots 30]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

COST = 0.0020          # עלות מסחר לכל צד
TARGET = 0.50
HOLD_YEARS = 2


# ---------------------------------------------------------------------------
# נתונים ואינדיקטורים
# ---------------------------------------------------------------------------

class Data:
    def __init__(self, cache: Path):
        bars = pd.read_pickle(cache / "bars.pkl.gz")
        close = bars["close"]
        self.idx = close.index
        self.cols = list(close.columns)
        self.col = {c: i for i, c in enumerate(self.cols)}
        a = lambda k: bars[k].reindex(columns=self.cols).to_numpy(dtype="float64")
        self.O, self.H, self.L, self.C, self.V = a("open"), a("high"), a("low"), a("close"), a("volume")
        cf = close.ffill()
        self.Cf = cf.to_numpy(dtype="float64")
        self.sectors = json.loads((cache / "sectors.json").read_text(encoding="utf-8"))
        self.cands = {pd.Timestamp(k): v for k, v in
                      json.loads((cache / "candidates.json").read_text(encoding="utf-8")).items()}
        self.day = {d: i for i, d in enumerate(self.idx)}

        # אינדיקטורים, כולם ידועים בסגירת היום שבו מחושבים
        spy = close["SPY"].ffill()
        self.spy_above200 = (spy > spy.rolling(200).mean()).to_numpy()
        self.spy_above50w = (spy > spy.rolling(50).mean()).to_numpy()
        vol = bars["volume"].reindex(columns=self.cols)
        v20, v60 = vol.rolling(20, min_periods=15).mean(), vol.rolling(60, min_periods=45).mean()
        self.rv = (v20 / v60).to_numpy()
        up = (cf.diff() > 0)
        upv = (vol.where(up, 0)).rolling(50, min_periods=40).sum()
        dnv = (vol.where(~up, 0)).rolling(50, min_periods=40).sum()
        self.updown = (upv / dnv.replace(0, np.nan)).to_numpy()
        low = bars["low"].reindex(columns=self.cols)
        self.ext10 = (cf / low.rolling(10, min_periods=8).min() - 1).to_numpy()
        self.ext_sma20 = (cf / cf.rolling(20).mean() - 1).to_numpy()
        hi = bars["high"].reindex(columns=self.cols)
        tr = pd.concat([hi - low, (hi - cf.shift()).abs(), (low - cf.shift()).abs()]).groupby(level=0).max()
        tr = tr.reindex(self.idx)[self.cols]
        self.atrp = (tr.rolling(14).mean() / cf).to_numpy()
        # מומנטום סקטור: ממוצע תשואת חצי שנה של המניות בסקטור, מדורג בכל יום
        r126 = cf / cf.shift(126) - 1
        sec_of = pd.Series({c: self.sectors.get(c, "?") for c in self.cols})
        sec_mom = r126.T.groupby(sec_of).mean().T           # יום x סקטור
        self.sec_rank = sec_mom.rank(axis=1, pct=True)        # 1 = החזק ביותר
        self.sec_of = sec_of


# ---------------------------------------------------------------------------
# עסקה בודדת
# ---------------------------------------------------------------------------

@dataclass
class Rules:
    stop: float | None = None        # סטופ קבוע מתחת לכניסה (0.20 = -20%)
    atr_stop: float | None = None    # סטופ במכפלות ATR
    trail: float | None = None       # סטופ נגרר מהשיא (0.25)
    partial: float | None = None     # מימוש חצי ברווח הזה (0.25)
    partial_be: bool = False         # אחרי מימוש חלקי: סטופ לנקודת הכניסה


def deadline_idx(d: Data, i: int) -> int:
    dl = pd.Timestamp(date(d.idx[i].year + HOLD_YEARS, 12, 31))
    j = int(d.idx.searchsorted(dl, side="right")) - 1
    return min(j, len(d.idx) - 1)


def run_trade(d: Data, k: int, i: int, rules: Rules):
    """קנייה בפתיחה של יום i. מחזיר רשימת יציאות [(יום, חלק, מחיר)]."""
    entry = d.O[i, k]
    if not entry or math.isnan(entry):
        entry = d.C[i, k]
    if not entry or math.isnan(entry):
        return None, []
    target = entry * (1 + TARGET)
    stop = None
    if rules.stop:
        stop = entry * (1 - rules.stop)
    if rules.atr_stop and i > 0 and not math.isnan(d.atrp[i - 1, k]):
        stop = entry * (1 - rules.atr_stop * d.atrp[i - 1, k])
    last = deadline_idx(d, i)
    frac, peak, out = 1.0, entry, []
    part_done = False
    for j in range(i, last + 1):
        o, h, l, c = d.O[j, k], d.H[j, k], d.L[j, k], d.C[j, k]
        if math.isnan(c):
            continue
        o = c if math.isnan(o) else o
        h = c if math.isnan(h) else h
        l = c if math.isnan(l) else l
        st = stop
        if rules.trail and j > i:
            ts = peak * (1 - rules.trail)
            st = ts if st is None else max(st, ts)
        # סטופ נבדק לפני יעד (שמרני כשבאותו יום נגעו בשניהם)
        if st is not None and l <= st and j > i:
            out.append((j, frac, min(o, st)))
            return entry, out
        if rules.partial and not part_done and h >= entry * (1 + rules.partial):
            px = max(o, entry * (1 + rules.partial)) if j > i else entry * (1 + rules.partial)
            out.append((j, frac / 2, px))
            frac /= 2
            part_done = True
            if rules.partial_be:
                stop = entry if stop is None else max(stop, entry)
        if h >= target:
            px = max(o, target) if j > i else target
            out.append((j, frac, px))
            return entry, out
        peak = max(peak, c)
    # מועד היציאה, או סוף הנתונים
    j = last
    while j > i and math.isnan(d.C[j, k]):
        j -= 1
    out.append((j, frac, d.C[j, k]))
    return entry, out


def trade_return(entry, legs):
    return sum(f * (px * (1 - COST) / (entry * (1 + COST)) - 1) for _, f, px in legs)


# ---------------------------------------------------------------------------
# מסנני כניסה: מחזירים True אם מותר להיכנס, לפי נתוני יום האות (i-1)
# ---------------------------------------------------------------------------

def f_market200(d, k, s): return bool(d.spy_above200[s])
def f_market50(d, k, s): return bool(d.spy_above50w[s])
def f_rv(d, k, s): v = d.rv[s, k]; return not (v == v) or v >= 1.0
def f_updown(d, k, s): v = d.updown[s, k]; return not (v == v) or v >= 1.0
def f_ext10(d, k, s): v = d.ext10[s, k]; return not (v == v) or v <= 0.15
def f_ext20(d, k, s): v = d.ext_sma20[s, k]; return not (v == v) or v <= 0.10
def f_secmom(d, k, s):
    try:
        r = d.sec_rank.iat[s, d.sec_rank.columns.get_loc(d.sec_of.iat[k])]
    except KeyError:
        return True
    return not (r == r) or r >= 0.5


FILTERS = {
    "שוק: SPY מעל ממוצע 200": f_market200,
    "שוק: SPY מעל ממוצע 50 (שבועי)": f_market50,
    "מחזור: 20 יום מעל 60 יום": f_rv,
    "מחזור: עליות מול ירידות 50 יום": f_updown,
    "מתוחה: +15% מהשפל של 10 ימים": f_ext10,
    "מתוחה: 10% מעל ממוצע 20": f_ext20,
    "סקטור: חצי עליון במומנטום 6 ח'": f_secmom,
}


# ---------------------------------------------------------------------------
# תיק
# ---------------------------------------------------------------------------

@dataclass
class Variant:
    name: str
    filt: object = None
    rules: Rules = field(default_factory=Rules)
    slots: int = 30
    sector_cap: float | None = None   # חלק מקסימלי מהמקומות לסקטור
    inv_vol: bool = False             # גודל לפי סיכון: הפוך לתנודתיות


def signal_days(d: Data):
    """(יום אות, יום כניסה, מועמדים) לכל סוף חודש."""
    out = []
    for ts, cands in sorted(d.cands.items()):
        s = d.day.get(ts)
        if s is None or s + 1 >= len(d.idx):
            continue
        out.append((s, s + 1, cands))
    return out


def run_portfolio(d: Data, v: Variant, start_cash=100_000.0):
    sig = {e: (s, c) for s, e, c in signal_days(d)}
    cash, pos, equity, trades = start_cash, [], [], []
    first = min(sig) if sig else 0
    med_atr = np.nanmedian(d.atrp[first:])
    for j in range(first, len(d.idx)):
        # יציאות שחלות היום
        keep = []
        for p in pos:
            while p["legs"] and p["legs"][0][0] == j:
                _, f, px = p["legs"].pop(0)
                cash += p["sh"] * f * px * (1 - COST)
            (keep if p["legs"] else trades).append(p)
        pos = keep
        # כניסות
        if j in sig:
            s, cands = sig[j]
            held = {p["k"] for p in pos}
            val = cash + sum(p["sh"] * p["rem"] * d.Cf[j - 1, p["k"]] for p in pos)
            for c in cands:
                if len(pos) >= v.slots:
                    break
                k = d.col.get(c["t"])
                if k is None or k in held:
                    continue
                if v.filt and not v.filt(d, k, s):
                    continue
                if v.sector_cap:
                    sec = d.sec_of.iat[k]
                    if sum(1 for p in pos if d.sec_of.iat[p["k"]] == sec) >= max(1, int(v.sector_cap * v.slots)):
                        continue
                entry, legs = run_trade(d, k, j, v.rules)
                if not legs:
                    continue
                size = val / v.slots
                if v.inv_vol:
                    a = d.atrp[s, k]
                    if a == a and a > 0:
                        size *= min(2.0, max(0.5, med_atr / a))
                size = min(size, cash)
                if size < 1:
                    break
                sh = size / (entry * (1 + COST))
                cash -= size
                # שבירת יציאות לפי חלקים שנשארו
                rem, lg = 1.0, []
                for (jj, f, px) in legs:
                    lg.append((jj, f, px))
                p = {"k": k, "sh": sh, "legs": lg, "rem": 1.0, "entry": entry, "day": j,
                     "ret": trade_return(entry, legs)}
                pos.append(p)
                held.add(k)
        # עדכון יתרה (חלק שנשאר לפי חלקים שכבר מומשו)
        for p in pos:
            p["rem"] = sum(f for _, f, _ in p["legs"])
        equity.append(cash + sum(p["sh"] * p["rem"] * d.Cf[j, p["k"]] for p in pos))
    eq = pd.Series(equity, index=d.idx[first:])
    return eq, trades + pos


def stats(eq: pd.Series):
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1
    dd = (eq / eq.cummax() - 1).min()
    r = eq.pct_change().dropna()
    sharpe = r.mean() / r.std() * math.sqrt(252) if r.std() > 0 else 0
    return cagr, dd, sharpe


def halves(eq: pd.Series):
    mid = eq.index[len(eq) // 2]
    return stats(eq[:mid])[0], stats(eq[mid:])[0]


# ---------------------------------------------------------------------------
# בדיקת מסנן ברמת העסקה, בלי מגבלת מקומות
# ---------------------------------------------------------------------------

def all_trades(d: Data, rules: Rules):
    """כל כניסה אפשרית: מועמד שלא מוחזק (וירטואלית) באותו רגע."""
    busy_until = {}
    out = []
    for s, e, cands in signal_days(d):
        for c in cands:
            k = d.col.get(c["t"])
            if k is None or busy_until.get(k, -1) >= e:
                continue
            entry, legs = run_trade(d, k, e, rules)
            if not legs:
                continue
            busy_until[k] = legs[-1][0]
            out.append({"k": k, "s": s, "e": e, "ret": trade_return(entry, legs),
                        "days": legs[-1][0] - e})
    return out


def welch(a, b):
    a, b = np.asarray(a), np.asarray(b)
    if len(a) < 5 or len(b) < 5:
        return float("nan")
    return (a.mean() - b.mean()) / math.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="variants_cache")
    ap.add_argument("--slots", type=int, default=30)
    ap.add_argument("--out", default="variants_results.json")
    a = ap.parse_args()
    d = Data(Path(a.cache))
    res = {"trade_filters": {}, "portfolio": {}}

    # 1. מסננים ברמת העסקה
    base_tr = all_trades(d, Rules())
    rets = np.array([t["ret"] for t in base_tr])
    print(f"עסקאות אפשריות: {len(base_tr)}, ממוצע {rets.mean()*100:+.1f}%, "
          f"הצלחה {np.mean(rets>0)*100:.0f}%\n")
    print(f"{'מסנן':34} {'נשארו':>6} {'דולגו':>6} {'ממוצע נשארו':>11} {'ממוצע דולגו':>11} {'t':>6}")
    for name, f in FILTERS.items():
        keep = [t["ret"] for t in base_tr if f(d, t["k"], t["s"])]
        skip = [t["ret"] for t in base_tr if not f(d, t["k"], t["s"])]
        t = welch(keep, skip)
        res["trade_filters"][name] = {"kept": len(keep), "skipped": len(skip),
                                      "kept_mean": float(np.mean(keep)) if keep else None,
                                      "skipped_mean": float(np.mean(skip)) if skip else None,
                                      "t": t}
        print(f"{name:34} {len(keep):6d} {len(skip):6d} {np.mean(keep)*100:+10.1f}% "
              f"{(np.mean(skip)*100 if skip else float('nan')):+10.1f}% {t:6.2f}")

    # יציאות ברמת העסקה (אותן כניסות, כלל יציאה אחר)
    print()
    exits = {
        "יציאה: סטופ -20%": Rules(stop=0.20),
        "יציאה: סטופ 3 ATR": Rules(atr_stop=3.0),
        "יציאה: סטופ נגרר 25%": Rules(trail=0.25),
        "יציאה: חצי ב-+25%": Rules(partial=0.25),
        "יציאה: חצי ב-+25% וסטופ לכניסה": Rules(partial=0.25, partial_be=True),
    }
    for name, r in exits.items():
        tr = all_trades(d, r)
        x = np.array([t["ret"] for t in tr])
        dd = np.array([t["days"] for t in tr])
        print(f"{name:34} עסקאות {len(tr)}, ממוצע {x.mean()*100:+.1f}% "
              f"(בסיס {rets.mean()*100:+.1f}%), ימים ממוצע {dd.mean():.0f}")
        res["trade_filters"][name] = {"n": len(tr), "mean": float(x.mean()),
                                      "days": float(dd.mean())}

    # 2. תיק
    variants = [Variant("בסיס", slots=a.slots)]
    variants += [Variant(n, filt=f, slots=a.slots) for n, f in FILTERS.items()]
    variants += [Variant(n, rules=r, slots=a.slots) for n, r in exits.items()]
    variants += [
        Variant("מגבלת סקטור 25%", sector_cap=0.25, slots=a.slots),
        Variant("גודל לפי סיכון (ATR)", inv_vol=True, slots=a.slots),
        Variant("15 פוזיציות", slots=15),
        Variant("45 פוזיציות", slots=45),
    ]
    spy = pd.Series(d.Cf[:, d.col["SPY"]], index=d.idx)
    print(f"\n{'גרסה':34} {'שנתי':>7} {'חצי1':>7} {'חצי2':>7} {'ירידה':>7} {'שארפ':>5} {'עסקאות':>6}")
    for v in variants:
        eq, trs = run_portfolio(d, v)
        c, dd, sh = stats(eq)
        h1, h2 = halves(eq)
        res["portfolio"][v.name] = {"cagr": c, "h1": h1, "h2": h2, "maxdd": dd,
                                    "sharpe": sh, "trades": len(trs)}
        print(f"{v.name:34} {c*100:+6.1f}% {h1*100:+6.1f}% {h2*100:+6.1f}% "
              f"{dd*100:6.1f}% {sh:5.2f} {len(trs):6d}")
    sp = spy[eq.index[0]:]
    c, dd, sh = stats(sp)
    h1, h2 = halves(sp)
    res["portfolio"]["SPY"] = {"cagr": c, "h1": h1, "h2": h2, "maxdd": dd, "sharpe": sh}
    print(f"{'SPY (השוואה)':34} {c*100:+6.1f}% {h1*100:+6.1f}% {h2*100:+6.1f}% {dd*100:6.1f}% {sh:5.2f}")
    Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1, default=float),
                           encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
