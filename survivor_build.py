"""
הטיית שורדים: הרכב S&P 500 היסטורי (github.com/fja05680/sp500, רשימת החברות בכל
תאריך) ונרות גם למניות שיצאו מהמדד, כדי לבדוק את כלל הסווינג על המניות שהיו במדד
בכל יום - ולא על רשימת היום.

    python survivor_build.py        -> variants_cache/pit/ (bars.pkl.gz, components.csv)
"""
from __future__ import annotations

import io
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import requests

OUT = Path("variants_cache/pit")
URL = ("https://raw.githubusercontent.com/fja05680/sp500/master/"
       "S%26P%20500%20Historical%20Components%20%26%20Changes%20(Updated).csv")
START, END = date(2015, 1, 1), date(2025, 12, 31)


def components() -> pd.DataFrame:
    c = pd.read_csv(io.StringIO(requests.get(URL, timeout=60).text))
    c["date"] = pd.to_datetime(c["date"])
    c["tickers"] = c["tickers"].map(lambda s: ",".join(sorted({t.strip().upper().replace(".", "-")
                                                                for t in s.split(",") if t.strip()})))
    return c


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    OUT.mkdir(parents=True, exist_ok=True)
    c = components()
    c.to_csv(OUT / "components.csv", index=False)
    s = c[(c.date >= pd.Timestamp(START) - pd.Timedelta(days=400)) & (c.date <= pd.Timestamp(END))]
    syms = set()
    for x in s.tickers:
        syms.update(x.split(","))
    syms = sorted(syms | {"SPY"})
    print(f"סימולים שהיו במדד 2014-2025: {len(syms) - 1}", flush=True)
    import app
    app.load_env()
    with app.using("swing"):
        bars = app._swing_bars(syms, START, END)
    fields = {"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"}
    frames = {f: {} for f in fields.values()}
    for sym, bs in bars.items():
        if not bs:
            continue
        idx = pd.to_datetime([str(b["t"])[:10] for b in bs])
        for k, f in fields.items():
            frames[f][sym] = pd.Series([float(b[k]) for b in bs], index=idx)
    out = {}
    for f, dd in frames.items():
        df = pd.DataFrame(dd).sort_index()
        out[f] = df[~df.index.duplicated(keep="last")].astype("float32")
    pd.to_pickle(out, OUT / "bars.pkl.gz")
    last = set(c.iloc[-1].tickers.split(","))
    got = set(out["close"].columns)
    gone = [x for x in syms if x not in last and x != "SPY"]
    print(f"נרות: {len(got)} מתוך {len(syms)}; יצאו מהמדד: {len(gone)}, מהם עם נרות: {sum(x in got for x in gone)}")
    miss = sorted(set(syms) - got)
    (OUT / "missing.json").write_text(json.dumps(miss), encoding="utf-8")
    print("חסרים:", len(miss), miss[:60])


if __name__ == "__main__":
    main()