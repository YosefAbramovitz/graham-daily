"""
בדיקת רעיונות Greenblatt על שיטת גראהם (ספט' 2026). נתונים: greenblatt_build.py.

  0. הטיית הפיצולים: כמה מהבדיקות הקודמות נבעו משווי שוק שגוי של מניות שהתפצלו אחר כך.
  1. עשירונים (כל S&P 1500 בלי פיננסים ותשתיות): תשואת 12 חודשים קדימה לפי EBIT/EV, לפי ROC,
     ולפי הנוסחה (סכום הדירוגים). האם יורד בצורה מסודרת מהעשירון הזול לפחות זול.
  2. התיק של הכלל (variants_sim: כניסה בסוף חודש, +50% או סוף השנה השנייה, בלי סטופ) על
     המועמדים המתוקנים: 30/20/15 מקומות, דירוג EBIT/EV מול הנוסחה, בלי פיננסים ותשתיות,
     והחזקה של שנה (Greenblatt) במקום הכלל של גראהם.

בחירה על 2017-2020, שיפוט על 2021-2025. הקבוצה היא רשימת S&P 1500 של היום (הטיית שורדים).

    python greenblatt_sim.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

import variants_sim as vs

CACHE = Path("variants_cache")
SPLIT = pd.Timestamp("2021-01-01")
EXCL = {"Financials", "Utilities"}


def load():
    m = pd.read_csv(CACHE / "greenblatt" / "metrics.csv.gz", parse_dates=["date"])
    return m


def cands_from(m, key="ey", passed="pass", order="ey", excl=False):
    """מועמדים לכל סוף חודש, בסדר העדפה. order: ey או magic (סכום דירוגים בתוך העוברים)."""
    out = {}
    q = m[m[passed]].copy()
    if excl:
        q = q[~q.sector.isin(EXCL)]
    for d, g in q.groupby("date"):
        g = g.dropna(subset=[key])
        if order == "magic":
            g = g.assign(roc=g.roc.fillna(-np.inf))
            score = g[key].rank(ascending=False) + g.roc.rank(ascending=False)
            g = g.assign(s=score).sort_values(["s", key], ascending=[True, False])
        else:
            g = g.sort_values(key, ascending=False)
        out[pd.Timestamp(d)] = [{"t": t, "ebit_ev": float(e)} for t, e in zip(g.t, g[key])]
    return out


def period_stats(eq):
    a, b = eq[eq.index < SPLIT], eq[eq.index >= SPLIT]
    ci, di, si = vs.stats(a)
    co, do, so = vs.stats(b)
    return dict(IS_cagr=ci * 100, IS_dd=di * 100, IS_sh=si, OOS_cagr=co * 100, OOS_dd=do * 100, OOS_sh=so)


def deciles(m, d, key, label, stat="mean"):
    """ממוצע תשואת 12 חודשים קדימה לכל עשירון (1 = הכי טוב לפי המדד)."""
    C = pd.DataFrame(d.Cf, index=d.idx, columns=d.cols)
    rows = []
    for dt, g in m.groupby("date"):
        s = d.day.get(pd.Timestamp(dt))
        if s is None or s + 252 >= len(d.idx):
            continue
        g = g[~g.sector.isin(EXCL)].dropna(subset=[key])
        g = g[g.t.isin(C.columns)]
        if len(g) < 100:
            continue
        fwd = C.iloc[s + 252][g.t].to_numpy() / C.iloc[s][g.t].to_numpy() - 1
        g = g.assign(fwd=fwd).dropna(subset=["fwd"])
        g["dec"] = pd.qcut(g[key].rank(ascending=False, method="first"), 10, labels=False) + 1
        # נתונים שבורים (מניה שיצאה מפשיטת רגל ונתפרה לסימול ישן: +60,000%) - חיתוך ל-[-95%, +200%]
        g["fwd"] = g.fwd.clip(-0.95, 2.0)
        r = g.groupby("dec").fwd.mean() if stat == "mean" else g.groupby("dec").fwd.median()
        r["all"] = g.fwd.mean() if stat == "mean" else g.fwd.median()
        r["date"] = pd.Timestamp(dt)
        rows.append(r)
    R = pd.DataFrame(rows)
    out = {}
    for nm, sub in (("IS", R[R.date < SPLIT]), ("OOS", R[R.date >= SPLIT])):
        mean = sub.drop(columns="date").mean() * 100
        out[nm] = mean
    df = pd.DataFrame(out).T.round(1)
    print(f"\n== עשירונים לפי {label} ({'ממוצע חתוך' if stat == 'mean' else 'חציון'} תשואת 12 ח' קדימה, %; 1 = הכי טוב)")
    print(df.to_string())
    return df


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    pd.set_option("display.width", 250)
    d = vs.Data(CACHE)
    m = load()
    m["magic"] = np.nan

    # 0. הטיית הפיצולים
    fut_split = m.mcap_old < m.mcap / 1.4
    print(f"שורות {len(m)}; מניות-חודשים עם פיצול עתידי (שווי שוק ישן קטן פי 1.4+): "
          f"{fut_split.mean():.1%}")
    po, pn = m[m.pass_old], m[m["pass"]]
    print(f"עוברים: ישן {len(po)}, מתוקן {len(pn)}, משותפים "
          f"{len(po.merge(pn, on=['date', 't']))}")
    top_old = po.sort_values("ey_old", ascending=False).groupby("date").head(15)
    print(f"ב-15 הזולות לפי החישוב הישן: {(top_old.mcap_old < top_old.mcap / 1.4).mean():.0%} "
          f"הן מניות שהתפצלו אחר כך")
    old_json = {pd.Timestamp(k): v for k, v in
                __import__("json").loads((CACHE / "candidates.json").read_text(encoding="utf-8")).items()}
    same = np.mean([len({x["t"] for x in old_json.get(pd.Timestamp(dt), [])} ^ set(g.t)) == 0
                    for dt, g in po.groupby("date")])
    print(f"שחזור candidates.json מהחישוב הישן: {same:.0%} מהחודשים זהים")

    # 1. עשירונים
    m["ey_r"] = m.groupby("date").ey.rank(ascending=False)
    m["roc_r"] = m.groupby("date").roc.rank(ascending=False)
    m["magic"] = -(m.ey_r + m.roc_r)          # גבוה = טוב
    dec_ey = deciles(m, d, "ey", "EBIT/EV")
    dec_roc = deciles(m, d, "roc", "ROC")
    dec_mg = deciles(m[m.roc.notna()], d, "magic", "הנוסחה (EBIT/EV + ROC)")
    dec_old = deciles(m.assign(ey=m.ey_old), d, "ey", "EBIT/EV בחישוב הישן (עם הטיית הפיצולים)")
    deciles(m, d, "ey", "EBIT/EV", stat="median")
    deciles(m[m.roc.notna()], d, "magic", "הנוסחה", stat="median")

    # ברמת העסקה, בלי מגבלת מקומות: 15 הזולות בכל חודש מול שאר העוברים (כלל היציאה של גראהם)
    for nm, cands in (("ישן", cands_from(m, "ey_old", "pass_old")), ("מתוקן", cands_from(m)),
                      ("מתוקן, לפי הנוסחה", cands_from(m, order="magic"))):
        d.cands = cands
        rank = {(d.day[ts], d.col[c["t"]]): i for ts, cs in cands.items() if ts in d.day
                for i, c in enumerate(cs) if c["t"] in d.col}
        T = pd.DataFrame(vs.all_trades(d, vs.Rules()))
        T["top"] = [rank.get((s_, k), 99) < 15 for s_, k in zip(T.s, T.k)]
        T["is"] = d.idx[T.s] < SPLIT
        g = T.groupby(["is", "top"]).ret.agg(["mean", "size"])
        print(f"\nעסקאות ({nm}): 15 הראשונות מול השאר, תשואה ממוצעת לעסקה")
        print((g.assign(mean=g["mean"] * 100)).round(1).to_string())

    # 2. תיקים
    tests = [(f"מתוקן, {sl} מקומות, {ex}, {od}", cands_from(m, order=od), sl, None if ex == "graham" else ex)
             for sl in (10, 15, 20, 30, 45) for ex in ("graham", "1y", "1y50", "2y") for od in ("ey", "magic")]
    tests += [
        ("ישן (הטיית פיצולים), 30 מקומות", cands_from(m, "ey_old", "pass_old"), 30, None),
        ("ישן, 15 מקומות", cands_from(m, "ey_old", "pass_old"), 15, None),
        ("מתוקן, 30 מקומות", cands_from(m), 30, None),
        ("מתוקן, 20 מקומות", cands_from(m), 20, None),
        ("מתוקן, 15 מקומות (הכלל היום)", cands_from(m), 15, None),
        ("מתוקן, 15, דירוג לפי הנוסחה", cands_from(m, order="magic"), 15, None),
        ("מתוקן, 30, דירוג לפי הנוסחה", cands_from(m, order="magic"), 30, None),
        ("מתוקן, 15, בלי פיננסים ותשתיות", cands_from(m, excl=True), 15, None),
        ("מתוקן, 15, החזקה שנה בלי יעד", cands_from(m), 15, "1y"),
        ("מתוקן, 15, שנה עם יעד +50%", cands_from(m), 15, "1y50"),
    ]
    rows = []
    orig_dl, orig_target = vs.deadline_idx, vs.TARGET
    for name, cands, slots, hold in tests:
        d.cands = cands
        vs.deadline_idx, vs.TARGET = orig_dl, orig_target
        if hold:
            def dl(dd, i):
                j = int(dd.idx.searchsorted(dd.idx[i] + pd.Timedelta(days=365), side="right")) - 1
                return min(j, len(dd.idx) - 1)
            vs.deadline_idx = dl
            if hold in ("1y", "2y"):
                vs.TARGET = 1e9
            if hold == "2y":
                vs.deadline_idx = orig_dl
        eq, trades = vs.run_portfolio(d, vs.Variant(name, slots=slots))
        r = period_stats(eq)
        rets = [t["ret"] for t in trades]
        rows.append(dict(test=name, trades=len(trades), avg_trade=np.mean(rets) * 100, **r))
        print(name, flush=True)
    vs.deadline_idx, vs.TARGET = orig_dl, orig_target
    spy = pd.Series(d.Cf[:, d.col["SPY"]], index=d.idx)
    spy = spy[spy.index >= min(d.cands)]
    rows.append(dict(test="SPY", trades=0, avg_trade=np.nan, **period_stats(spy)))
    R = pd.DataFrame(rows).round(2)
    print("\n== תיקים (2017-2020 / 2021-2025)")
    print(R.to_string(index=False))
    R.to_csv(CACHE / "greenblatt" / "portfolios.csv", index=False, encoding="utf-8-sig")
    pd.concat({"ey": dec_ey, "roc": dec_roc, "magic": dec_mg, "ey_old": dec_old}).to_csv(
        CACHE / "greenblatt" / "deciles.csv", encoding="utf-8-sig")


if __name__ == "__main__":
    main()