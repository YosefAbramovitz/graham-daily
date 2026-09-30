"""
רעיונות מהספרים של Chan ו-Antonacci על כלל הסווינג:

  1. מובהקות מול כניסה אקראית (Chan פרק 1, Lo-Mamaysky-Wang): אותן יציאות, כניסות
     אקראיות (א) בכל מניה נזילה במגמה עולה, (ב) בכל מניה נזילה. 200 הגרלות.
  2. כניסה בשתי מנות (Chan פרק 3): חצי בפתיחה, חצי אם הסגירה יורדת 0.75 ATR מתחת
     לכניסה ב-5 הימים הראשונים; מול הכול בבת אחת.
  3. מסנן השוק (Antonacci): SPY מעל ממוצע 200 (שלנו) מול תשואת 12 ח' חיובית,
     שניהם, אחד מהם, בלי מסנן.
  4. תנודתיות השוק (Chan פרק 8, מדדי סיכון): R לפי תנודתיות 20 יום של SPY.
  5. שגיאות נתונים (Chan פרק 3): עסקאות עם קפיצה יומית של 40%+ בתקופה.

    python books2_sim.py [variants_cache]
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import swing
from books_sim import portfolio
from ideas_sim import COST, STOP, HOLD, stats, trade, trades
from swing_sim import SPLIT, Data

CACHE = Path(sys.argv[1] if len(sys.argv) > 1 else "variants_cache")


def tstat(x):
    x = np.asarray(x, float)
    x = x[~np.isnan(x)]
    return x.mean() / x.std() * math.sqrt(len(x)) if len(x) > 2 else np.nan


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    pd.set_option("display.width", 250)
    rng = np.random.default_rng(7)
    d = Data(CACHE)
    b = pd.read_pickle(CACHE / "bars.pkl.gz")
    f = lambda k: b[k].reindex(index=d.idx, columns=d.cols).astype("float64")
    C, H, L, V = f("close"), f("high"), f("low"), f("volume")
    ind = swing.indicators(C, H, L, V)
    base = swing.signals(C, H, L, V).to_numpy()
    trend = (ind["liquid"] & ind["uptrend"]).fillna(False).to_numpy()
    liquid = ind["liquid"].fillna(False).to_numpy()
    mom = ind["mom"].to_numpy()
    t = trades(d, base)
    oos = d.idx[t.s] >= SPLIT
    real = {"IS": t.R[~oos].mean(), "OOS": t.R[oos].mean()}
    print(f"בסיס: R {real['IS']:.3f} / {real['OOS']:.3f}, עסקאות {(~oos).sum()} / {oos.sum()}", flush=True)

    # 1. כניסות אקראיות
    print("\n== 1. כניסה אקראית (200 הגרלות, אותן יציאות, מסנן SPY)")
    ok_days = np.flatnonzero(d.spy_ok[:len(d.idx) - HOLD - 1] & (np.arange(len(d.idx) - HOLD - 1) >= 200))
    for lab, pool in (("מניה נזילה במגמה עולה", trend), ("כל מניה נזילה", liquid)):
        cand = [(s, k) for s in ok_days for k in np.flatnonzero(pool[s]) if k != d.spy_col]
        cand = np.array(cand)
        cs = d.idx[cand[:, 0]] >= SPLIT
        res = {"IS": [], "OOS": []}
        for _ in range(200):
            for per, m, n in (("IS", ~cs, (~oos).sum()), ("OOS", cs, oos.sum())):
                pick = cand[m][rng.choice(m.sum(), size=n, replace=False)]
                rr = [trade(d, k, s) for s, k in pick]
                res[per].append(np.nanmean([x[4] for x in rr if x is not None]))
        for per in ("IS", "OOS"):
            arr = np.array(res[per])
            p = (arr >= real[per]).mean()
            print(f"  {lab} {per}: אקראי {arr.mean():+.3f}R (טווח 95%: {np.percentile(arr,2.5):+.3f}..{np.percentile(arr,97.5):+.3f}), "
                  f"שלנו {real[per]:+.3f}R, p={p:.3f}", flush=True)

    # 2. כניסה בשתי מנות
    print("\n== 2. כניסה בשתי מנות (תשואה לאות, כאחוז מההון המלא לעסקה)")
    rows = []
    for r in t.itertuples():
        s, k, e = r.s, r.k, r.e
        a = d.atr[s, k]
        px = d.O[e, k]
        stop = px - STOP * a
        last = min(e + HOLD - 1, len(d.idx) - 1)
        full = r.ret
        # מנה שנייה: בפתיחה שאחרי סגירה <= כניסה - 0.75 ATR, רק בימים 1-5, אם עוד לא נעצר
        ret_b, got = 0.0, False
        for j in range(e, min(e + 5, last)):
            if r.x <= j:
                break
            if d.C[j, k] <= px - 0.75 * a and j + 1 <= r.x:
                pb = d.O[j + 1, k]
                if pb > stop and not math.isnan(pb):
                    # יוצאת יחד עם הראשונה (סטופ או זמן) במחיר היציאה שלה
                    out = px * (1 + r.ret) * (1 + COST) / (1 - COST)
                    ret_b = out * (1 - COST) / (pb * (1 + COST)) - 1
                    got = True
                break
        rows.append((s, full, 0.5 * full + (0.5 * ret_b if got else 0.0), got))
    A = pd.DataFrame(rows, columns=["s", "full", "scaled", "got"])
    A["oos"] = d.idx[A.s] >= SPLIT
    for per, m in (("IS", ~A.oos), ("OOS", A.oos)):
        x = A[m]
        print(f"  {per}: הכול בבת אחת {x.full.mean()*100:+.3f}% לאות | שתי מנות {x.scaled.mean()*100:+.3f}% "
              f"(מנה שנייה ב-{x.got.mean()*100:.0f}%; תשואת המנה השנייה כשנכנסה: "
              f"{(2*(x.scaled-0.5*x.full))[x.got].mean()*100:+.3f}%)")

    # 3. מסנן השוק
    print("\n== 3. מסנן השוק")
    spy = C["SPY"]
    g200 = (spy > spy.rolling(200, min_periods=200).mean()).to_numpy()
    g12 = (spy / spy.shift(252) - 1 > 0).to_numpy()
    out = []
    saved = d.spy_ok.copy()
    for lab, gate in (("SPY מעל ממוצע 200 (שלנו)", g200), ("תשואת 12 ח' של SPY חיובית", g12),
                      ("שניהם", g200 & g12), ("אחד מהם", g200 | g12),
                      ("בלי מסנן", np.ones(len(d.idx), bool))):
        d.spy_ok = gate
        tt = trades(d, base)
        out.append({"test": lab, **stats(d, tt, portfolio(d, tt, mom))})
    d.spy_ok = saved
    R = pd.DataFrame(out)
    for c in ("IS_cagr", "IS_dd", "OOS_cagr", "OOS_dd"):
        R[c] = (R[c] * 100).round(1)
    print(R[["test", "IS_n", "IS_R", "OOS_n", "OOS_R", "IS_cagr", "IS_dd", "OOS_cagr", "OOS_dd", "OOS_sharpe"]].round(3).to_string(index=False))

    # 4. תנודתיות השוק
    print("\n== 4. R לפי תנודתיות 20 יום של SPY (שנתית) ביום האות")
    vol = (spy.pct_change().rolling(20).std() * math.sqrt(252)).to_numpy()
    t["vol"] = vol[t.s]
    t["oos"] = oos
    t["vb"] = pd.cut(t.vol, [0, 0.12, 0.18, 0.25, 0.35, 5])
    print(t.groupby(["vb", "oos"], observed=True).R.agg(["count", "mean"]).unstack().round(3).to_string())

    # 5. שגיאות נתונים
    print("\n== 5. קפיצה יומית של 40%+ בין יום האות ליציאה")
    ret1 = (C / C.shift(1) - 1).abs().to_numpy()
    jump = [np.nanmax(ret1[s:x + 1, k]) >= 0.4 for s, k, x in zip(t.s, t.k, t.x)]
    t["jump"] = jump
    print(t.groupby(["jump", "oos"]).R.agg(["count", "mean"]).unstack().round(3).to_string())


if __name__ == "__main__":
    main()