"""
נרות 5 דקות לחלונות של אותות הסווינג בעבר (לבדיקת תזמון תוך-יומי).

קלט: variants_cache/intraday_jobs.json - {יום אות: {start, end, syms}}.
פלט: variants_cache/intraday/part_NNN.pkl.gz - {(יום אות, סימול): DataFrame}.
ממשיך מאיפה שעצר.

    python intraday_fetch.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pandas as pd

import alpaca_prices as ap

OUT = Path("variants_cache/intraday")
PART = 100


def fetch(syms, start, end):
    """(ok, {sym: [bars]}) עם דפדוף. sip קודם, iex כגיבוי."""
    for feed in ("sip", "iex"):
        params = {"symbols": ",".join(s.replace("-", ".") for s in syms), "timeframe": "5Min",
                  "start": f"{start}T13:00:00Z", "end": f"{end}T21:00:00Z",
                  "adjustment": "all", "feed": feed, "limit": 10000, "sort": "asc"}
        got, ok = {}, True
        for _ in range(50):
            for attempt in range(4):
                try:
                    r = ap.requests.get(ap.BARS_URL, headers=ap._headers(), params=params, timeout=45)
                except ap.requests.RequestException:
                    time.sleep(3); continue
                if r.status_code == 429 or r.status_code >= 500:
                    time.sleep(3 * (attempt + 1)); continue
                break
            else:
                ok = False; break
            if r.status_code != 200:
                ok = False; break
            p = r.json()
            for s, rows in (p.get("bars") or {}).items():
                got.setdefault(s.replace(".", "-"), []).extend(rows)
            if not p.get("next_page_token"):
                break
            params["page_token"] = p["next_page_token"]
        if ok:
            return True, got
    return False, {}


def frame(rows):
    df = pd.DataFrame(rows)
    df["t"] = pd.to_datetime(df["t"], utc=True)
    return df.set_index("t")[["o", "h", "l", "c", "v"]].astype("float32")


def main() -> int:
    from app import load_env
    load_env()
    jobs = json.loads(Path("variants_cache/intraday_jobs.json").read_text())
    keys = sorted(jobs)
    OUT.mkdir(parents=True, exist_ok=True)
    t0, missing = time.time(), 0
    # python intraday_fetch.py [n m]: עובד n מתוך m (כמה תהליכים במקביל)
    n, m = (int(sys.argv[1]), int(sys.argv[2])) if len(sys.argv) > 2 else (0, 1)
    for pi in range(0, len(keys), PART):
        path = OUT / f"part_{pi // PART:03d}.pkl.gz"
        if path.exists() or (pi // PART) % m != n:
            continue
        data = {}
        for day in keys[pi:pi + PART]:
            j = jobs[day]
            ok, got = fetch(j["syms"], j["start"], j["end"])
            if not ok:          # סימול לא מוכר מפיל בקשה: אחד-אחד
                got = {}
                for s in j["syms"]:
                    ok1, g1 = fetch([s], j["start"], j["end"])
                    got.update(g1)
            for s in j["syms"]:
                if got.get(s):
                    data[(day, s)] = frame(got[s])
                else:
                    missing += 1
        pd.to_pickle(data, path)
        print(f"{min(pi + PART, len(keys))}/{len(keys)} ימים, {time.time()-t0:.0f}s, חסרים {missing}",
              flush=True)
    print("סיום", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
