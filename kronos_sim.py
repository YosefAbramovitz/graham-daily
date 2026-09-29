"""
Kronos (github.com/shiyu-coder/Kronos) כמסנן על אותות הסווינג.

Kronos הוא מודל שאומן על נרות OHLCV מ-45 בורסות וצופה את הנרות הבאים. השאלה
כאן: ביום האות של כלל הסווינג (RSI14 מתחת ל-35 במגמה עולה), האם העסקאות שהמודל
צופה להן עלייה ב-15 הימים הבאים עושות יותר מהשאר?

זליגה: המודל ראה בזמן האימון את ההיסטוריה של רוב המניות. כל בדיקה על תאריך
שלפני סוף האימון שלו מודדת זיכרון ולא תחזית. לכן בודקים רק אותות מ-SINCE והלאה
(ברירת מחדל ספטמבר 2025, אחרי פרסום המודל). החלון קצר, אז גם השוואה לסינון
אקראי באותו גודל.

המודל רואה רק את הנרות עד סגירת יום האות (כולל), ולוחות הזמנים העתידיים הם ימי
עסקים ולא לוח המסחר האמיתי - בלי הצצה.

התקנה (פעם אחת, על הלפטופ):
    git clone https://github.com/shiyu-coder/Kronos variants_cache/Kronos
    py -m pip install torch einops huggingface_hub safetensors tqdm

    python kronos_sim.py [variants_cache] [--since 2025-09-01] [--model NeoQuasar/Kronos-small]

התחזיות נשמרות ב-variants_cache/kronos_preds.csv, והרצה חוזרת ממשיכה מאיפה שעצרה.
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import swing
from books_sim import portfolio
from ideas_sim import HOLD, trades
from swing_sim import Data, pstats

LOOKBACK = 400      # נרות יומיים לפני יום האות (המודל מוגבל ל-512)
HORIZON = HOLD      # 15 ימי מסחר, כמו היציאה לפי זמן
SAMPLES = 5         # מסלולים לכל תחזית; Kronos ממצע אותם
BATCH = 32
SEED = 123
RANDOM_RUNS = 1000


def load_predictor(repo: Path, model_name: str, tok_name: str):
    sys.path.insert(0, str(repo))
    import torch
    from model import Kronos, KronosPredictor, KronosTokenizer
    torch.set_num_threads(max(1, min(8, (torch.get_num_threads() or 4) - 2)))
    tok = KronosTokenizer.from_pretrained(tok_name)
    mdl = Kronos.from_pretrained(model_name)
    tok.eval(); mdl.eval()
    return KronosPredictor(mdl, tok, device="cpu", max_context=512)


def window(d: Data, V: np.ndarray, s: int, k: int):
    """נרות [s-LOOKBACK+1, s] של מניה k, או None אם חסר משהו."""
    a = s - LOOKBACK + 1
    if a < 0:
        return None
    x = pd.DataFrame({"open": d.O[a:s + 1, k], "high": d.H[a:s + 1, k], "low": d.L[a:s + 1, k],
                      "close": d.C[a:s + 1, k], "volume": V[a:s + 1, k]})
    if x.isna().values.any():
        return None
    x["amount"] = x.volume * x.close
    xt = pd.Series(d.idx[a:s + 1])
    yt = pd.Series(pd.bdate_range(d.idx[s] + pd.Timedelta(days=1), periods=HORIZON))
    return x, xt, yt


def forecast(d: Data, V: np.ndarray, t: pd.DataFrame, predictor, cache: Path, tag: str) -> pd.DataFrame:
    """מוסיף ל-t את fc5 ו-fc15: תשואה צפויה מסגירת יום האות ל-5 ול-15 ימים."""
    key = lambda s, k: (str(d.idx[s].date()), d.cols[k])
    done = {}
    if cache.exists():
        c = pd.read_csv(cache)
        c = c[c.tag == tag]
        done = {(r.date, r.ticker): (r.fc5, r.fc15) for r in c.itertuples()}
    todo = [(s, k) for s, k in zip(t.s, t.k) if key(s, k) not in done]
    print(f"תחזיות: {len(t) - len(todo)} שמורות, {len(todo)} לחישוב", flush=True)
    for i in range(0, len(todo), BATCH):
        chunk = []
        for s, k in todo[i:i + BATCH]:
            w = window(d, V, s, k)
            if w is None:
                done[key(s, k)] = (np.nan, np.nan)
            else:
                chunk.append((s, k, w))
        rows = []
        if chunk:
            import torch
            torch.manual_seed(SEED + i)
            np.random.seed(SEED + i)
            preds = predictor.predict_batch([w[0] for *_, w in chunk], [w[1] for *_, w in chunk],
                                            [w[2] for *_, w in chunk], pred_len=HORIZON, T=1.0,
                                            top_p=0.9, sample_count=SAMPLES, verbose=False)
            for (s, k, _), p in zip(chunk, preds):
                c0 = d.C[s, k]
                f5, f15 = p.close.iloc[4] / c0 - 1, p.close.iloc[-1] / c0 - 1
                done[key(s, k)] = (f5, f15)
                rows.append({"tag": tag, "date": key(s, k)[0], "ticker": key(s, k)[1], "fc5": f5, "fc15": f15})
        if rows:
            pd.DataFrame(rows).to_csv(cache, mode="a", header=not cache.exists(), index=False)
        print(f"  {min(i + BATCH, len(todo))}/{len(todo)}", flush=True)
    t = t.copy()
    t["fc5"] = [done[key(s, k)][0] for s, k in zip(t.s, t.k)]
    t["fc15"] = [done[key(s, k)][1] for s, k in zip(t.s, t.k)]
    return t


def spearman(a, b):
    a, b = pd.Series(a), pd.Series(b)
    m = a.notna() & b.notna()
    n = int(m.sum())
    if n < 4:
        return np.nan, np.nan, n
    r = a[m].rank().corr(b[m].rank())
    tt = r * math.sqrt((n - 2) / max(1e-12, 1 - r * r))
    return r, tt, n


def group(x: pd.DataFrame) -> dict:
    n = len(x)
    return {"n": n, "R": x.R.mean(), "t": x.R.mean() / x.R.std() * math.sqrt(n) if n > 2 else np.nan,
            "win%": (x.ret > 0).mean() * 100, "ret%": x.ret.mean() * 100}


def book(d: Data, t: pd.DataFrame, score: np.ndarray) -> dict:
    if t.empty:
        return {"n": 0, "cagr%": np.nan, "dd%": np.nan, "sharpe": np.nan, "total%": np.nan}
    e = portfolio(d, t, score)
    cagr, dd, sh = pstats(e)
    return {"n": len(t), "cagr%": cagr * 100, "dd%": dd * 100, "sharpe": sh,
            "total%": (e.iloc[-1] / e.iloc[0] - 1) * 100}


def report(d: Data, t: pd.DataFrame, mom: np.ndarray, past5: np.ndarray) -> dict:
    t = t[t.fc15.notna()].reset_index(drop=True)
    out = {"n": len(t)}
    print(f"\n== {len(t)} עסקאות עם תחזית, {d.idx[t.s].min().date()} עד {d.idx[t.s].max().date()}")
    if len(t) < 10:
        print("מעט מדי עסקאות למסקנה.")
        return out

    print("\n== מתאם דירוג (Spearman) בין התחזית לתוצאה")
    rows = []
    for lab, a, b in (("fc15 מול R", t.fc15, t.R), ("fc5 מול R", t.fc5, t.R),
                      ("fc15 מול תשואה", t.fc15, t.ret),
                      ("fc15 מול מומנטום 126 יום", t.fc15, [mom[s, k] for s, k in zip(t.s, t.k)]),
                      ("fc15 מול תשואת 5 ימים אחרונים", t.fc15, [past5[s, k] for s, k in zip(t.s, t.k)])):
        r, tt, n = spearman(a, b)
        rows.append({"בדיקה": lab, "rho": round(r, 3), "t": round(tt, 2), "n": n})
    print(pd.DataFrame(rows).to_string(index=False))
    out["rho_R"] = rows[0]["rho"]; out["rho_t"] = rows[0]["t"]

    print("\n== R לעסקה לפי כיוון התחזית ל-15 יום")
    rows = [{"קבוצה": "הכול", **group(t)},
            {"קבוצה": "Kronos צופה עלייה", **group(t[t.fc15 > 0])},
            {"קבוצה": "Kronos צופה ירידה", **group(t[t.fc15 <= 0])}]
    q = pd.qcut(t.fc15.rank(method="first"), 3, labels=["שליש תחתון", "שליש אמצעי", "שליש עליון"])
    rows += [{"קבוצה": str(g), **group(t[q == g])} for g in q.cat.categories]
    g = pd.DataFrame(rows)
    print(g.round(3).to_string(index=False))
    up, dn = t[t.fc15 > 0], t[t.fc15 <= 0]
    out.update(up_n=len(up), up_R=up.R.mean(), dn_n=len(dn), dn_R=dn.R.mean(), all_R=t.R.mean())

    # סינון אקראי באותו גודל: כמה פעמים מקרה עושה טוב כמו Kronos
    if 0 < len(up) < len(t):
        rng = np.random.default_rng(SEED)
        rnd = np.array([t.R.to_numpy()[rng.choice(len(t), len(up), replace=False)].mean()
                        for _ in range(RANDOM_RUNS)])
        p = (rnd >= up.R.mean()).mean()
        out["p_random"] = p
        print(f"\nסינון אקראי של {len(up)} מתוך {len(t)} עסקאות, {RANDOM_RUNS} פעמים: "
              f"R ממוצע {rnd.mean():.3f}, ב-{p * 100:.1f}% מהפעמים טוב לפחות כמו Kronos ({up.R.mean():.3f})")

    print("\n== תיק (0.5% סיכון, 15 מקומות) בחלון הבדיקה")
    fc = np.full(d.C.shape, np.nan)
    fc[t.s.to_numpy(), t.k.to_numpy()] = t.fc15.to_numpy()
    rows = [{"תיק": "הכלל כמו היום (דירוג מומנטום)", **book(d, t, mom)},
            {"תיק": "רק עסקאות ש-Kronos צופה להן עלייה", **book(d, up, mom)},
            {"תיק": "כל העסקאות, דירוג לפי Kronos", **book(d, t, fc)}]
    print(pd.DataFrame(rows).round(2).to_string(index=False))
    out["books"] = rows
    return out


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    pd.set_option("display.width", 250)
    ap = argparse.ArgumentParser()
    ap.add_argument("cache", nargs="?", default="variants_cache")
    ap.add_argument("--since", default="2025-09-01", help="רק אותות מהתאריך הזה (אחרי סוף האימון של המודל)")
    ap.add_argument("--model", default="NeoQuasar/Kronos-small")
    ap.add_argument("--tokenizer", default="NeoQuasar/Kronos-Tokenizer-base")
    ap.add_argument("--repo", default=None, help="תיקיית הקוד של Kronos (ברירת מחדל: <cache>/Kronos)")
    a = ap.parse_args(argv)
    cache = Path(a.cache)

    d = Data(cache)
    b = pd.read_pickle(cache / "bars.pkl.gz")
    f = lambda k: b[k].reindex(index=d.idx, columns=d.cols).astype("float64")
    C, H, L, V = f("close"), f("high"), f("low"), f("volume")
    mom = swing.indicators(C, H, L, V)["mom"].to_numpy()
    past5 = (C / C.shift(5) - 1).to_numpy()
    print(f"נתונים: {len(d.cols)} מניות, {d.idx[0].date()} עד {d.idx[-1].date()}")

    t = trades(d, swing.signals(C, H, L, V).to_numpy())
    since = pd.Timestamp(a.since)
    # רק אותות אחרי סוף האימון, ורק עסקאות שהספיקו 15 ימי מסחר (או נעצרו)
    t = t[(d.idx[t.s] >= since) & ((t.e + HORIZON - 1 < len(d.idx)) | (t.x < len(d.idx) - 1))]
    t = t.reset_index(drop=True)
    print(f"אותות סווינג מ-{since.date()}: {len(t)}")
    if t.empty:
        print("אין עסקאות בחלון. צריך לבנות מחדש את המטמון (variants_build.py) עם נתונים עדכניים.")
        return 1

    predictor = load_predictor(Path(a.repo) if a.repo else cache / "Kronos", a.model, a.tokenizer)
    tag = f"{a.model}|{LOOKBACK}|{SAMPLES}"
    t = forecast(d, V.to_numpy(), t, predictor, cache / "kronos_preds.csv", tag)
    report(d, t, mom, past5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
