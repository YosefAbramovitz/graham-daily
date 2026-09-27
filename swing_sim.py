"""
בדיקה לאחור של סטאפים לטווח קצר (סווינג: ימים עד שלושה שבועות).

משתמש בנרות שאסף variants_build.py (S&P 1500, 2016-2025). כל סטאפ מוגדר רק
לפי נתונים שידועים בסגירת יום האות; הכניסה למחרת בפתיחה.

כדי לא "לכייל עד שזה נראה טוב": כל הפרמטרים נבחרים על 2016-2020 בלבד, והמספר
שקובע הוא הביצוע על 2021-2025, שלא שימש לבחירה.

    python swing_sim.py [--cache variants_cache]
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

COST = 0.0010            # עמלה+מרווח לכל צד, מניות נזילות
SPLIT = pd.Timestamp("2021-01-01")
MIN_PRICE = 10.0
MIN_DOLLAR_VOL = 20e6    # מחזור דולרי ממוצע 20 יום


# ---------------------------------------------------------------------------
# נתונים ואינדיקטורים
# ---------------------------------------------------------------------------

def rsi(close: pd.DataFrame, n: int) -> pd.DataFrame:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


class Data:
    def __init__(self, cache: Path):
        b = pd.read_pickle(cache / "bars.pkl.gz")
        self.idx = b["close"].index
        cols = [c for c in b["close"].columns]
        self.cols = cols
        f = lambda k: b[k].reindex(columns=cols).astype("float64")
        O, H, L, C, V = f("open"), f("high"), f("low"), f("close"), f("volume")
        self.O, self.H, self.L, self.C = O.to_numpy(), H.to_numpy(), L.to_numpy(), C.to_numpy()
        sma = lambda x, n: x.rolling(n, min_periods=n).mean()
        s20, s50, s200 = sma(C, 20), sma(C, 50), sma(C, 200)
        tr = pd.concat([H - L, (H - C.shift()).abs(), (L - C.shift()).abs()]).groupby(level=0).max()
        tr = tr.reindex(self.idx)[cols]
        self.atr = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean().to_numpy()
        v50 = sma(V, 50)
        dv20 = sma(C * V, 20)
        liquid = (C >= MIN_PRICE) & (dv20 >= MIN_DOLLAR_VOL)
        uptrend = (C > s200) & (s50 > s200)
        spy = C["SPY"]
        self.spy_ok = (spy > sma(spy, 200)).to_numpy()
        self.r2 = rsi(C, 2).to_numpy()
        self.r14 = rsi(C, 14).to_numpy()
        self.sma5 = sma(C, 5).to_numpy()
        self.relvol = (V / v50).to_numpy()
        self.mom = (C / C.shift(126) - 1).to_numpy()

        hh20 = H.shift(1).rolling(20).max()
        hh50 = H.shift(1).rolling(50).max()
        base = liquid & uptrend
        self.sig = {
            # פריצה: סגירה מעל שיא 20/50 יום עם מחזור גבוה
            "פריצה 20 יום + מחזור": base & (C > hh20) & (V > 1.5 * v50),
            "פריצה 50 יום + מחזור": base & (C > hh50) & (V > 1.5 * v50),
            # תיקון לממוצע 20 במגמה עולה, מחזור יורד, ויום היפוך (סגירה מעל שיא אתמול)
            "תיקון לממוצע 20 + היפוך": base & (L.shift(1) <= s20.shift(1) * 1.01)
                & (C.shift(1) >= s50.shift(1)) & (C > H.shift(1))
                & (V.shift(1) < v50.shift(1)),
            "תיקון לממוצע 50 + היפוך": base & (L.shift(1) <= s50.shift(1) * 1.01)
                & (C.shift(1) >= s200.shift(1)) & (C > H.shift(1)),
            # מכירת יתר קצרה במגמה עולה (RSI 2, קונורס)
            "RSI2 מתחת 10 במגמה": base & (pd.DataFrame(self.r2, self.idx, cols) < 10),
            "RSI14 מתחת 35 במגמה": base & (pd.DataFrame(self.r14, self.idx, cols) < 35),
        }
        self.sig = {k: v.fillna(False).to_numpy() for k, v in self.sig.items()}
        self.spy_col = cols.index("SPY")


# ---------------------------------------------------------------------------
# עסקה
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Exit:
    stop_atr: float = 2.0         # סטופ במכפלות ATR מתחת לכניסה
    target_r: float | None = 2.0  # יעד במכפלות הסיכון (R); None = אין
    max_days: int = 15            # יציאה בסגירה אחרי כך וכך ימי מסחר
    sma5_exit: bool = False       # יציאה בסגירה מעל ממוצע 5 (לסטאפי מכירת יתר)

    def label(self):
        t = f"יעד {self.target_r}R" if self.target_r else "בלי יעד"
        s = " / יציאה מעל ממוצע 5" if self.sma5_exit else ""
        return f"סטופ {self.stop_atr} ATR, {t}, עד {self.max_days} ימים{s}"


def trade(d: Data, k: int, s: int, ex: Exit):
    """אות בסגירת יום s, כניסה בפתיחת s+1. מחזיר (יום יציאה, תשואה, R)."""
    e = s + 1
    if e >= len(d.idx):
        return None
    entry = d.O[e, k]
    a = d.atr[s, k]
    if not (entry > 0) or not (a > 0):
        return None
    stop = entry - ex.stop_atr * a
    risk = entry - stop
    target = entry + ex.target_r * risk if ex.target_r else None
    last = min(e + ex.max_days - 1, len(d.idx) - 1)
    px, j = None, e
    for j in range(e, last + 1):
        o, h, l, c = d.O[j, k], d.H[j, k], d.L[j, k], d.C[j, k]
        if math.isnan(c):
            continue
        if j > e and not math.isnan(o):
            if o <= stop:
                px = o; break
            if target and o >= target:
                px = o; break
        if l <= stop:                      # סטופ קודם (שמרני)
            px = stop; break
        if target and h >= target:
            px = target; break
        if ex.sma5_exit and c > d.sma5[j, k]:
            px = c; break
    if px is None:
        while math.isnan(d.C[j, k]) and j > e:
            j -= 1
        px = d.C[j, k]
    ret = px * (1 - COST) / (entry * (1 + COST)) - 1
    return j, ret, ret * entry / risk, risk / entry


def all_trades(d: Data, name: str, ex: Exit, market: bool):
    sig = d.sig[name]
    out = []
    busy = np.full(len(d.cols), -1)
    for s in range(200, len(d.idx) - 1):
        if market and not d.spy_ok[s]:
            continue
        for k in np.flatnonzero(sig[s]):
            if k == d.spy_col or busy[k] >= s:
                continue
            r = trade(d, k, s, ex)
            if r is None:
                continue
            busy[k] = r[0]
            out.append((s, k, r[0], r[1], r[2], r[3]))
    return pd.DataFrame(out, columns=["s", "k", "x", "ret", "R", "riskpct"])


def summary(t: pd.DataFrame, d: Data):
    if t.empty:
        return {}
    days = t.x - t.s
    tstat = t.R.mean() / t.R.std() * math.sqrt(len(t)) if len(t) > 2 else float("nan")
    return {"n": len(t), "win": float((t.ret > 0).mean()), "avg_ret": float(t.ret.mean()),
            "exp_R": float(t.R.mean()), "t": float(tstat), "days": float(days.mean())}


# ---------------------------------------------------------------------------
# תיק: סיכון קבוע לעסקה
# ---------------------------------------------------------------------------

def portfolio(d: Data, t: pd.DataFrame, risk=0.01, max_pos=10, cap=0.20, rank="relvol"):
    """1% מההון בסיכון לעסקה, עד 10 פוזיציות, עד 20% מההון בפוזיציה."""
    if t.empty:
        return None
    t = t.copy()
    key = {"relvol": d.relvol, "mom": d.mom}[rank]
    t["score"] = [key[s, k] for s, k in zip(t.s, t.k)]
    by_day = {s: g.sort_values("score", ascending=False) for s, g in t.groupby("s")}
    cash, pos, eq = 1.0, [], []
    start = int(t.s.min())
    for j in range(start, len(d.idx)):
        # יציאות: הערך מחושב לפי תשואת העסקה הסופית ביום היציאה
        keep = []
        for p in pos:
            if p["x"] == j:
                cash += p["amt"] * (1 + p["ret"])
            else:
                keep.append(p)
        pos = keep
        val = cash + sum(p["amt"] * d.C[j, p["k"]] / p["px0"] if d.C[j, p["k"]] == d.C[j, p["k"]]
                         else p["amt"] for p in pos)
        eq.append(val)
        # אותות מאתמול נכנסים היום בפתיחה (הכניסה עצמה ב-trade)
        g = by_day.get(j - 1)
        if g is None:
            continue
        held = {p["k"] for p in pos}
        for r in g.itertuples():
            if len(pos) >= max_pos:
                break
            if r.k in held:
                continue
            amt = min(val * risk / r.riskpct, val * cap, cash)
            if amt <= val * 0.01:
                break
            px0 = d.O[j, r.k]
            cash -= amt
            pos.append({"k": r.k, "x": int(r.x), "ret": r.ret, "amt": amt, "px0": px0})
            held.add(r.k)
    e = pd.Series(eq, index=d.idx[start:])
    return e


def pstats(e: pd.Series):
    yrs = (e.index[-1] - e.index[0]).days / 365.25
    cagr = (e.iloc[-1] / e.iloc[0]) ** (1 / yrs) - 1
    dd = (e / e.cummax() - 1).min()
    r = e.pct_change().dropna()
    sh = r.mean() / r.std() * math.sqrt(252) if r.std() > 0 else 0
    return cagr, dd, sh


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="variants_cache")
    ap.add_argument("--out", default="swing_results.json")
    ap.add_argument("--extra", action="store_true", help="רק ניתוח איכות ודוחות לכלל שנבחר")
    a = ap.parse_args()
    d = Data(Path(a.cache))
    if a.extra:
        extra_report(d, Path(a.cache))
        return 0
    ins = lambda t: t[d.idx[t.s] < SPLIT]
    oos = lambda t: t[d.idx[t.s] >= SPLIT]

    exits = [Exit(sa, tr, md) for sa, tr, md in
             itertools.product([1.5, 2.0, 3.0], [1.5, 2.0, 3.0, None], [10, 15])]
    exits += [Exit(sa, None, md, True) for sa in (2.0, 3.0) for md in (10, 15)]

    res = {}
    print(f"{'סטאפ':26} {'שוק':4} {'יציאה (נבחרה ב-2016-20)':46} "
          f"{'IS n':>6} {'IS R':>6} {'OOS n':>6} {'OOS R':>6} {'OOS t':>6} {'OOS הצלחה':>9} {'ימים':>5}")
    for name in d.sig:
        for market in (False, True):
            best = None
            for ex in exits:
                t = all_trades(d, name, ex, market)
                si = summary(ins(t), d)
                if si and si["n"] >= 100 and (best is None or si["exp_R"] > best[1]["exp_R"]):
                    best = (ex, si, t)
            if best is None:
                continue
            ex, si, t = best
            so = summary(oos(t), d)
            res[f"{name}|{int(market)}"] = {"exit": ex.__dict__, "is": si, "oos": so}
            print(f"{name:26} {'כן' if market else 'לא':4} {ex.label():46} {si['n']:6d} "
                  f"{si['exp_R']:+6.2f} {so.get('n',0):6d} {so.get('exp_R',float('nan')):+6.2f} "
                  f"{so.get('t',float('nan')):6.2f} {so.get('win',0)*100:8.0f}% {so.get('days',0):5.1f}")
            res[f"{name}|{int(market)}"]["_t"] = t

    # תיק לכל סטאפ עם היציאה שנבחרה, מחולק לשתי התקופות
    spy = pd.Series(d.C[:, d.spy_col], index=d.idx)
    print(f"\n{'תיק (1% סיכון, עד 10 פוזיציות)':40} {'IS שנתי':>8} {'OOS שנתי':>9} {'OOS ירידה':>9} {'OOS שארפ':>8}")
    for key, r in res.items():
        t = r.pop("_t")
        rank = "mom" if "תיקון" in key or "RSI" in key else "relvol"
        e = portfolio(d, t, rank=rank)
        if e is None:
            continue
        ci, _, _ = pstats(e[e.index < SPLIT])
        co, ddo, sho = pstats(e[e.index >= SPLIT])
        r["portfolio"] = {"is_cagr": ci, "oos_cagr": co, "oos_dd": ddo, "oos_sharpe": sho}
        print(f"{key:40} {ci*100:+7.1f}% {co*100:+8.1f}% {ddo*100:8.1f}% {sho:8.2f}")
    for lab, part in (("IS", spy[spy.index < SPLIT]), ("OOS", spy[spy.index >= SPLIT])):
        c, dd, sh = pstats(part)
        print(f"SPY {lab}: {c*100:+.1f}% בשנה, ירידה {dd*100:.1f}%, שארפ {sh:.2f}")
        res[f"SPY|{lab}"] = {"cagr": c, "dd": dd, "sharpe": sh}
    Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1, default=float),
                           encoding="utf-8")
    return 0



# ---------------------------------------------------------------------------
# ניתוחים נוספים: ציון איכות ודוחות (python swing_sim.py --extra)
# ---------------------------------------------------------------------------

def extra_report(d: Data, cache: Path) -> dict:
    """תוחלת לעסקה של הכלל הנבחר, לפי ציון איכות ולפי קרבה לדוח רבעוני."""
    import swing
    b = pd.read_pickle(cache / "bars.pkl.gz")
    f = lambda k: b[k].reindex(columns=d.cols).astype("float64")
    C, H, L, V = f("close"), f("high"), f("low"), f("volume")
    ind = swing.indicators(C, H, L, V)
    d.sig = {"rule": swing.signals(C, H, L, V).to_numpy()}
    t = all_trades(d, "rule", Exit(swing.STOP_ATR, None, swing.HOLD_DAYS), True)
    q = swing.quality(ind).to_numpy()
    t["quality"] = [q[s, k] for s, k in zip(t.s, t.k)]
    t["oos"] = d.idx[t.s] >= SPLIT
    out = {"quality": t.groupby(["quality", "oos"]).R.agg(["count", "mean"]).round(3)}

    ef = cache / "earnings.json"
    if ef.exists():
        earn = json.loads(ef.read_text(encoding="utf-8"))
        pos = {tk: np.searchsorted(d.idx.values, pd.to_datetime(v).values) for tk, v in earn.items() if v}
        def flag(s, k, lo, hi):
            p = pos.get(d.cols[k])
            if p is None:
                return None
            return bool(((p >= s + lo) & (p <= s + hi)).any())
        # בתוך תקופת ההחזקה המתוכננת: מיום הכניסה (s+1) עד היום ה-15
        t["earn_in_hold"] = [flag(s, k, 1, swing.HOLD_DAYS) for s, k in zip(t.s, t.k)]
        # דוח ב-5 הימים שלפני האות (הירידה היא תגובה לדוח)
        t["earn_before"] = [flag(s, k, -5, 0) for s, k in zip(t.s, t.k)]
        for col in ("earn_in_hold", "earn_before"):
            g = t[t[col].notna()]
            out[col] = g.groupby([col, "oos"]).R.agg(["count", "mean"]).round(3)
            a, b_ = g[g[col] == True].R, g[g[col] == False].R  # noqa: E712
            out[col + "_t"] = round(float((a.mean() - b_.mean()) /
                                          math.sqrt(a.var() / len(a) + b_.var() / len(b_))), 2)
        g = t[t.earn_in_hold.notna()]
        for lab, sub in (("בלי דוח בתקופה", g[g.earn_in_hold == False]),  # noqa: E712
                         ("הכול", g)):
            e = portfolio(d, sub, rank="mom")
            out["portfolio " + lab] = [round(x, 3) for x in
                                        pstats(e[e.index < SPLIT]) + pstats(e[e.index >= SPLIT])]
    for k, v in out.items():
        print(f"\n== {k}\n{v}")
    return out


if __name__ == "__main__":
    sys.exit(main())
