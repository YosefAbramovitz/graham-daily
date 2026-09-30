"""
Kronos (github.com/shiyu-coder/Kronos, מודל יסוד לנרות) - בדיקה קדימה בלבד (ספט' 2026).

תאריך סוף נתוני האימון של Kronos לא פורסם (המאמר: אוגוסט 2025), ולכן בדיקה על 2016-2025
חסרת ערך - ייתכן שהמודל ראה את הנרות. כאן רק אותות מ-1.10.2025 ואילך.
דורש torch (CPU; במחשב הזה 2.6.0 - גרסאות חדשות נופלות על ספריית VC++ ישנה) ו-einops,
huggingface_hub, safetensors, ואת הקוד של Kronos ב-variants_cache/kronos_repo.

    python kronos_sim.py fetch      # נרות S&P 1500 מ-2024 עד היום (אלפקה)
    python kronos_sim.py predict    # תחזית 20 יום לכל אות סווינג, נשמר ברצף (אפשר להמשיך)
    python kronos_sim.py sample     # תחזיות למניות אקראיות בימים אקראיים (IC כללי)
    python kronos_sim.py eval
"""
from __future__ import annotations

import os
import sys
import time
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

import swing

CACHE = Path("variants_cache")
OUT = CACHE / "kronos"
FROM = pd.Timestamp("2025-10-01")
LOOKBACK, PRED = 250, 20   # הקשר קצר ודגימה כפולה: על CPU זה כ-2 שניות לתחזית


def fetch():
    b = pd.read_pickle(CACHE / "bars.pkl.gz")
    syms = sorted(set(b["close"].columns) | {"SPY"})
    import app
    app.load_env()
    with app.using("swing"):
        bars = app._swing_bars(syms, date(2024, 1, 1), date.today())
    fields = {"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"}
    frames_ = {f: {} for f in fields.values()}
    for sym, bs in bars.items():
        if not bs:
            continue
        idx = pd.to_datetime([str(x["t"])[:10] for x in bs])
        for k, f in fields.items():
            frames_[f][sym] = pd.Series([float(x[k]) for x in bs], index=idx)
    out = {}
    for f, dd in frames_.items():
        df = pd.DataFrame(dd).sort_index()
        out[f] = df[~df.index.duplicated(keep="last")].astype("float64")
    OUT.mkdir(parents=True, exist_ok=True)
    pd.to_pickle(out, OUT / "bars.pkl.gz")
    print(len(out["close"].columns), out["close"].index[0].date(), out["close"].index[-1].date())


def frames():
    b = pd.read_pickle(OUT / "bars.pkl.gz")
    cut = pd.Timestamp(date.today())          # נר של היום עוד לא נסגר
    return tuple(b[k][b[k].index < cut] for k in ("open", "high", "low", "close", "volume"))


def predictor():
    sys.path.insert(0, str(CACHE / "kronos_repo"))
    import torch
    torch.set_num_threads(max(1, (os.cpu_count() or 2) // 2))   # שהמחשב יישאר זמין
    from model import Kronos, KronosPredictor, KronosTokenizer
    tok = KronosTokenizer.from_pretrained("NeoQuasar/Kronos-Tokenizer-base")
    mdl = Kronos.from_pretrained("NeoQuasar/Kronos-small")
    return KronosPredictor(mdl, tok, device="cpu", max_context=512)


def run(jobs, path, batch=16, samples=2, limit=None):
    """jobs: (יום אות, סימול). כל שורה: תחזית הסגירה ליום 1/5/20 ביחס לסגירת יום האות."""
    O, H, L, C, V = frames()
    idx = C.index
    done = set()
    if path.exists():
        prev = pd.read_csv(path, parse_dates=["day"])
        done = set(zip(prev.day, prev.sym))
    jobs = [j for j in jobs if j not in done][:limit]
    print("jobs left", len(jobs), flush=True)
    if not jobs:
        return
    p = predictor()
    for i in range(0, len(jobs), batch):
        t0 = time.time()
        chunk = jobs[i:i + batch]
        dfs, xs, ys, keep = [], [], [], []
        for day, sym in chunk:
            s = idx.get_loc(day)
            if s + 1 < LOOKBACK:
                continue
            sl = slice(s + 1 - LOOKBACK, s + 1)
            x = pd.DataFrame({"open": O[sym].iloc[sl].values, "high": H[sym].iloc[sl].values,
                              "low": L[sym].iloc[sl].values, "close": C[sym].iloc[sl].values,
                              "volume": V[sym].iloc[sl].values})
            if x.isna().any().any():
                continue
            dfs.append(x)
            xs.append(pd.Series(idx[sl]))
            ys.append(pd.Series(pd.bdate_range(day + pd.Timedelta(days=1), periods=PRED)))
            keep.append((day, sym))
        if not dfs:
            continue
        preds = p.predict_batch(dfs, xs, ys, pred_len=PRED, T=1.0, top_p=0.9, sample_count=samples, verbose=False)
        rows = []
        for (day, sym), x, pr in zip(keep, dfs, preds):
            c0 = x.close.iloc[-1]
            rows.append(dict(day=day, sym=sym, p1=pr.close.iloc[0] / c0 - 1, p5=pr.close.iloc[4] / c0 - 1,
                             p20=pr.close.iloc[19] / c0 - 1))
        pd.DataFrame(rows).to_csv(path, mode="a", header=not path.exists(), index=False)
        print(f"{i + len(chunk)}/{len(jobs)} {time.time() - t0:.1f}s", flush=True)


def signal_jobs():
    O, H, L, C, V = frames()
    sig = swing.signals(C, H, L, V)
    ok = swing.market_ok(C["SPY"])
    last = C.index[-PRED - 1]
    jobs = []
    for day in C.index[(C.index >= FROM) & (C.index <= last)]:
        if not ok.loc[day]:
            continue
        for sym in sig.columns[sig.loc[day].fillna(False).to_numpy()]:
            if sym != "SPY":
                jobs.append((day, sym))
    return jobs


def sample_jobs(n_days=30, n_syms=12, seed=0):
    O, H, L, C, V = frames()
    rng = np.random.default_rng(seed)
    days = C.index[(C.index >= FROM) & (C.index <= C.index[-PRED - 1])]
    liquid = (C * V).rolling(20).mean() >= 20e6
    jobs = []
    for day in sorted(rng.choice(days, size=min(n_days, len(days)), replace=False)):
        day = pd.Timestamp(day)
        cand = [s for s in C.columns[liquid.loc[day].fillna(False).to_numpy()] if s != "SPY"]
        for sym in rng.choice(cand, size=min(n_syms, len(cand)), replace=False):
            jobs.append((day, str(sym)))
    return jobs


def realized(df):
    O, H, L, C, V = frames()
    idx = C.index
    for k in (1, 5, 20):
        r, x = [], []
        for day, sym in zip(df.day, df.sym):
            s = idx.get_loc(day)
            if s + k >= len(idx):
                r.append(np.nan); x.append(np.nan); continue
            rr = C[sym].iloc[s + k] / O[sym].iloc[s + 1] - 1
            rs = C["SPY"].iloc[s + k] / O["SPY"].iloc[s + 1] - 1
            r.append(rr); x.append(rr - rs)
        df[f"r{k}"] = r
        df[f"x{k}"] = x
    return df


def report(name, df):
    df = realized(df.dropna(subset=["p20"]).copy())
    print(f"\n== {name}: {len(df)} תחזיות, {df.day.nunique()} ימים, {df.day.min().date()} עד {df.day.max().date()}")
    for k in (1, 5, 20):
        ics = df.groupby("day").apply(lambda g: g[f"p{k}"].rank().corr(g[f"x{k}"].rank()) if len(g) >= 5 else np.nan,
                                      include_groups=False).dropna()
        t = ics.mean() / ics.std() * np.sqrt(len(ics)) if len(ics) > 2 else np.nan
        q = df.groupby("day")[f"p{k}"].transform(lambda s: s.rank(pct=True))
        top, bot = df[q > 0.5][f"x{k}"].mean(), df[q <= 0.5][f"x{k}"].mean()
        pos, neg = df[df[f"p{k}"] > 0][f"x{k}"].mean(), df[df[f"p{k}"] <= 0][f"x{k}"].mean()
        npos = (df[f"p{k}"] > 0).mean()
        print(f"יום {k:2d}: IC יומי {ics.mean():+.3f} (t={t:+.2f}, {len(ics)} ימים) | "
              f"חצי עליון {top*100:+.2f}% מול תחתון {bot*100:+.2f}% (מעבר ל-SPY) | "
              f"תחזית חיובית ({npos:.0%}) {pos*100:+.2f}% מול שלילית {neg*100:+.2f}%")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "eval"
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else None
    if cmd == "fetch":
        fetch()
    elif cmd == "predict":
        run(signal_jobs(), OUT / "pred_signals.csv", limit=limit)
    elif cmd == "sample":
        run(sample_jobs(), OUT / "pred_sample.csv", limit=limit)
    else:
        for nm, f in (("אותות סווינג", "pred_signals.csv"), ("מניות אקראיות", "pred_sample.csv")):
            if (OUT / f).exists():
                report(nm, pd.read_csv(OUT / f, parse_dates=["day"]))


if __name__ == "__main__":
    main()
