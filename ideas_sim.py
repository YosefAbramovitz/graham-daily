"""
ארבעה רעיונות מהספרות על כלל הסווינג (RSI14<35 במגמה, SPY מעל ממוצע 200,
סטופ 1.5 ATR, 15 ימים, 0.5% סיכון, עד 15 פוזיציות, עד 20% לפוזיציה):

  A. ירידה ביחס לענף (Da ועמיתים; "Short-term reversal ... if properly measured"):
     לסנן לפי תשואה יחסית לממוצע הענף (GICS, משקל שווה), או RSI על היחס מניה/ענף.
  B. כניסה בסגירה של יום האות במקום בפתיחה למחרת (Lou, Polk, Skouras: ההיפוך
     מרוויח בלילה).
  C. דירוג כשיש יותר אותות ממקומות: מומנטום 6 ח' (היום), קרבה לשיא 52 שבועות,
     מומנטום "חלק" (Frog in the Pan), RSI הכי נמוך, אקראי.
  D. בלי סטופ / סטופ רחוק (Kaminski-Lo), עם אותו גודל פוזיציה כמו היום.

הבחירה על 2016-2020 בלבד, הבדיקה על 2021-2025.

    python ideas_sim.py [variants_cache]
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import swing
from swing_sim import COST, SPLIT, Data, pstats

CACHE = Path(sys.argv[1] if len(sys.argv) > 1 else "variants_cache")
STOP, HOLD = swing.STOP_ATR, swing.HOLD_DAYS
RISK, CAP, MAXPOS = swing.RISK_PCT, swing.MAX_POSITION_PCT, swing.MAX_POSITIONS


def trade(d, k, s, at_close=False, stop_atr=STOP, size_atr=STOP):
    """(יום כניסה, מחיר כניסה, יום יציאה, תשואה, R, סיכון כאחוז) - יציאה בסגירת היום ה-15."""
    e = s if at_close else s + 1
    if e >= len(d.idx):
        return None
    px = d.C[s, k] if at_close else d.O[e, k]
    a = d.atr[s, k]
    if not (px > 0) or not (a > 0):
        return None
    stop = px - stop_atr * a if stop_atr else -np.inf
    last = min(e + HOLD - 1, len(d.idx) - 1)
    out, j = None, e
    first = e + 1 if at_close else e
    for j in range(first, last + 1):
        o, l, c = d.O[j, k], d.L[j, k], d.C[j, k]
        if math.isnan(c):
            continue
        if j > e and not math.isnan(o) and o <= stop:
            out = o; break
        if l <= stop:
            out = stop; break
    if out is None:
        while math.isnan(d.C[j, k]) and j > e:
            j -= 1
        out = d.C[j, k]
    ret = out * (1 - COST) / (px * (1 + COST)) - 1
    unit = size_atr * a
    return e, px, j, ret, ret * px / unit, unit / px


def trades(d, sig, **kw):
    rows, busy = [], np.full(len(d.cols), -1)
    for s in range(200, len(d.idx) - 1):
        if not d.spy_ok[s]:
            continue
        for k in np.flatnonzero(sig[s]):
            if k == d.spy_col or busy[k] >= s:
                continue
            r = trade(d, k, s, **kw)
            if r is None:
                continue
            busy[k] = r[2]
            rows.append((s, k) + r)
    return pd.DataFrame(rows, columns=["s", "k", "e", "px0", "x", "ret", "R", "riskpct"])


def portfolio(d, t, score, rng=None):
    """0.5% סיכון לעסקה, עד 15 פוזיציות, עד 20% לפוזיציה, רק מזומן."""
    t = t.copy()
    t["score"] = rng.random(len(t)) if rng is not None else [score[s, k] for s, k in zip(t.s, t.k)]
    t["score"] = t["score"].fillna(-np.inf)
    by_day = {e: g.sort_values("score", ascending=False) for e, g in t.groupby("e")}
    cash, pos, eq = 1.0, [], []
    start = int(t.e.min())
    for j in range(start, len(d.idx)):
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
        g = by_day.get(j)
        if g is None:
            continue
        held = {p["k"] for p in pos}
        for r in g.itertuples():
            if len(pos) >= MAXPOS:
                break
            if r.k in held:
                continue
            amt = min(val * RISK / r.riskpct, val * CAP, cash)
            if amt <= val * 0.01:
                break
            cash -= amt
            pos.append({"k": r.k, "x": int(r.x), "ret": r.ret, "amt": amt, "px0": r.px0})
            held.add(r.k)
    return pd.Series(eq, index=d.idx[start:])


def stats(d, t, e):
    out = {}
    for lab, m in (("IS", d.idx[t.s] < SPLIT), ("OOS", d.idx[t.s] >= SPLIT)):
        x = t[m]
        out[lab + "_n"] = len(x)
        out[lab + "_R"] = x.R.mean()
        out[lab + "_t"] = x.R.mean() / x.R.std() * math.sqrt(len(x)) if len(x) > 2 else np.nan
        out[lab + "_win"] = (x.ret > 0).mean()
    ci, ddi, _ = pstats(e[e.index < SPLIT])
    co, ddo, sho = pstats(e[e.index >= SPLIT])
    out.update(IS_cagr=ci, IS_dd=ddi, OOS_cagr=co, OOS_dd=ddo, OOS_sharpe=sho)
    return out


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    d = Data(CACHE)
    b = pd.read_pickle(CACHE / "bars.pkl.gz")
    f = lambda k: b[k].reindex(index=d.idx, columns=d.cols).astype("float64")
    C, H, L, V = f("close"), f("high"), f("low"), f("volume")
    ind = swing.indicators(C, H, L, V)
    trend = (ind["liquid"] & ind["uptrend"]).fillna(False)
    base = swing.signals(C, H, L, V).to_numpy()
    mom = ind["mom"].to_numpy()

    # ענפים: מדד משקל שווה לכל ענף GICS
    sec = json.loads((CACHE / "sectors.json").read_text(encoding="utf-8"))
    ret = C.pct_change(fill_method=None)
    names = pd.Series({c: sec.get(c) for c in d.cols if c != "SPY" and sec.get(c)})
    sidx = {}
    for nm, members in names.groupby(names).groups.items():
        r = ret[list(members)].mean(axis=1).fillna(0)
        sidx[nm] = (1 + r).cumprod()
    I = pd.DataFrame({c: sidx[names[c]] for c in names.index}).reindex(columns=d.cols)
    rel = {n: (C / C.shift(n) - 1) - (I / I.shift(n) - 1) for n in (5, 10, 20)}
    sec10 = I / I.shift(10) - 1
    relrsi = swing.rsi(C / I)
    B = lambda m: (base & m.fillna(False).to_numpy())

    sigs = {
        "בסיס (היום)": base,
        "A: ירדה יותר מהענף ב-5 ימים": B(rel[5] < 0),
        "A: ירדה יותר מהענף ב-10 ימים": B(rel[10] < 0),
        "A: ירדה יותר מהענף ב-20 ימים": B(rel[20] < 0),
        "A: פיגור של 3%+ מהענף ב-10 ימים": B(rel[10] < -0.03),
        "A: פיגור של 5%+ מהענף ב-10 ימים": B(rel[10] < -0.05),
        "A: הענף עלה ב-10 ימים (ירדה לבד)": B(sec10 >= 0),
        "A: הענף ירד ב-10 ימים (ירדה עם הענף)": B(sec10 < 0),
        "A: RSI יחסי לענף מתחת 35 (במקום RSI)": (trend & (relrsi < 35)).fillna(False).to_numpy(),
        "A: RSI יחסי מתחת 35 וגם RSI מתחת 35": B(relrsi < 35),
    }
    rows = []
    T = {}
    for name, sg in sigs.items():
        t = trades(d, sg)
        T[name] = t
        rows.append({"test": name, **stats(d, t, portfolio(d, t, mom))})
        print(name, len(t), flush=True)

    t = trades(d, base, at_close=True)
    rows.append({"test": "B: כניסה בסגירת יום האות", **stats(d, t, portfolio(d, t, mom))})
    t = trades(d, base, at_close=True)
    print("B", flush=True)

    tb = T["בסיס (היום)"]
    hi52 = (C / H.rolling(252, min_periods=200).max()).to_numpy()
    up = (ret > 0).astype(float).where(ret.notna())
    dn = (ret < 0).astype(float).where(ret.notna())
    fip = (np.sign(ind["mom"]) * (up.rolling(126).mean() - dn.rolling(126).mean())).to_numpy()
    rsi_low = (-ind["rsi"]).to_numpy()
    for lab, sc in (("C: דירוג קרבה לשיא 52 שבועות", hi52), ("C: דירוג מומנטום חלק (FIP)", fip),
                    ("C: דירוג RSI הכי נמוך", rsi_low)):
        rows.append({"test": lab, **stats(d, tb, portfolio(d, tb, sc))})
    rs = [stats(d, tb, portfolio(d, tb, None, rng=np.random.default_rng(i))) for i in range(5)]
    rows.append({"test": "C: דירוג אקראי (ממוצע 5)", **pd.DataFrame(rs).mean().to_dict()})
    print("C", flush=True)

    for lab, sa in (("D: בלי סטופ (אותו גודל)", None), ("D: סטופ 3 ATR (אותו גודל)", 3.0)):
        t = trades(d, base, stop_atr=sa)
        rows.append({"test": lab, **stats(d, t, portfolio(d, t, mom))})
    print("D", flush=True)

    R = pd.DataFrame(rows)
    R.to_csv(CACHE / "ideas_table.csv", index=False, encoding="utf-8-sig")
    pd.set_option("display.width", 260)
    fmt = R.copy()
    for c in fmt.columns:
        if c.endswith(("cagr", "dd", "win")):
            fmt[c] = (fmt[c] * 100).round(1)
        elif c.endswith(("_R", "_t", "sharpe")):
            fmt[c] = fmt[c].round(3 if c.endswith("_R") else 2)
    print(fmt.to_string(index=False))


if __name__ == "__main__":
    main()