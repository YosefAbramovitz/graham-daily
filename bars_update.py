"""
עדכון יומי של קבצי ההיסטוריה (variants_cache/bars.pkl.gz ו-variants_cache/pit/bars.pkl.gz).

הקבצים נבנו פעם אחת (variants_build.py, survivor_build.py) עד סוף 2025. כאן מוסיפים להם
את ימי המסחר שעברו מאז, כדי שבדיקות לאחור יכללו גם את הימים האחרונים.

המחירים מ-Alpaca מתואמים לפיצולים ולדיבידנדים (adjustment=all). אחרי פיצול או דיבידנד
כל ההיסטוריה של המניה זזה, ולכן לא מספיק להדביק ימים חדשים: מורידים גם כמה ימים
שכבר שמורים, ומניה שהמחיר שלה ביום חופף השתנה מורדת מחדש מההתחלה.

    python bars_update.py [--cache variants_cache]
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import pandas as pd

import alpaca_prices as ap

FIELDS = {"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"}
OVERLAP_DAYS = 10        # ימים קלנדריים שמורידים מחדש לפני היום האחרון בקובץ
MAX_DRIFT = 0.001        # שינוי במחיר שמור מעל זה = פיצול או דיבידנד, להוריד מחדש


def fetch(symbols: Iterable[str], start: date, end: date) -> Dict[str, pd.DataFrame]:
    """נרות יומיים כטבלאות רחבות (שורה ליום, עמודה לסימול), כמו בקבצים."""
    got: Dict[str, List[dict]] = {}
    syms = sorted(set(symbols))
    for i in range(0, len(syms), ap.BATCH):
        chunk = syms[i:i + ap.BATCH]
        part = ap._fetch_batch(chunk, start, end, "sip") or ap._fetch_batch(chunk, start, end, "iex")
        got.update(part)
    frames: Dict[str, dict] = {f: {} for f in FIELDS.values()}
    for sym, bs in got.items():
        if not bs:
            continue
        idx = pd.to_datetime([str(b["t"])[:10] for b in bs])
        for k, f in FIELDS.items():
            frames[f][sym] = pd.Series([float(b[k]) for b in bs], index=idx)
    out = {}
    for f, d in frames.items():
        df = pd.DataFrame(d).sort_index()
        out[f] = df[~df.index.duplicated(keep="last")].astype("float32")
    return out


def drifted(old: pd.DataFrame, new: pd.DataFrame) -> List[str]:
    """מניות שהסגירה השמורה שלהן ביום חופף שונה ממה ש-Alpaca מחזירה היום."""
    days = old.index.intersection(new.index)
    out = []
    for sym in new.columns.intersection(old.columns):
        a, b = old.loc[days, sym].dropna(), new.loc[days, sym].dropna()
        both = a.index.intersection(b.index)
        if len(both) and abs(float(b[both[-1]]) / float(a[both[-1]]) - 1) > MAX_DRIFT:
            out.append(sym)
    return out


def merge(data: Dict[str, pd.DataFrame], new: Dict[str, pd.DataFrame],
          full: Dict[str, pd.DataFrame], last: pd.Timestamp) -> Dict[str, pd.DataFrame]:
    """ימים חדשים אחרי last מתווספים; מניות שהורדו מחדש מקבלות את ההיסטוריה החדשה כולה."""
    out = {}
    for f, old in data.items():
        add = new.get(f, pd.DataFrame())
        add = add[add.index > last].reindex(columns=old.columns)
        df = pd.concat([old, add]) if len(add) else old.copy()
        re = full.get(f)
        if re is not None and re.shape[1]:
            cols = [c for c in re.columns if c in df.columns]
            df = df.reindex(df.index.union(re.index))
            df.loc[:, cols] = re[cols].reindex(df.index).to_numpy()
        out[f] = df.sort_index().astype("float32")
    return out


def update(path: Path, today: Optional[date] = None, fetcher=fetch) -> str:
    """מעדכן קובץ אחד במקום. מחזיר שורת סיכום."""
    data = pd.read_pickle(path)
    close = data["close"]
    first, last = close.index.min(), close.index.max()
    today = today or date.today()
    if last.date() >= today:
        return f"{path}: כבר מעודכן עד {last.date()}"
    syms = list(close.columns)
    new = fetcher(syms, (last - pd.Timedelta(days=OVERLAP_DAYS)).date(), today)
    if not new.get("close", pd.DataFrame()).shape[0]:
        return f"{path}: לא התקבלו נתונים מ-Alpaca"
    redo = drifted(close, new["close"])
    full = fetcher(redo, first.date(), today) if redo else {}
    out = merge(data, new, full, last)
    tmp = path.with_suffix(".tmp")
    pd.to_pickle(out, tmp, compression="gzip")
    os.replace(tmp, path)
    end = out["close"].index.max().date()
    added = int((out["close"].index > last).sum())
    return (f"{path}: נוספו {added} ימים, עד {end}; "
            f"{len(redo)} מניות הורדו מחדש אחרי פיצול או דיבידנד")


def update_all(cache: Path) -> List[str]:
    lines = []
    for p in (cache / "bars.pkl.gz", cache / "pit" / "bars.pkl.gz"):
        if p.exists():
            lines.append(update(p))
    return lines


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap_ = argparse.ArgumentParser()
    ap_.add_argument("--cache", default="variants_cache")
    a = ap_.parse_args()
    from app import load_env           # מפתחות Alpaca מ-.env, בלי להדפיס אותם
    load_env()
    if not ap.available():
        print("אין מפתחות Alpaca")
        return 1
    t0 = time.time()
    for line in update_all(Path(a.cache)):
        print(line, flush=True)
    print(f"({time.time() - t0:.0f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
