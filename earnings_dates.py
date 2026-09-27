"""
תאריכי הדוחות הרבעוניים בעבר, מה-SEC: כל 8-K עם סעיף 2.02 ("Results of
Operations") הוא הודעת תוצאות. משמש לבדיקה אם כניסות סווינג לפני דוח גרועות
יותר (swing_sim.py), ובמסך - לסמן אות שיש לו דוח בתוך תקופת ההחזקה.

    python earnings_dates.py [--since 2015-06-01] [--out variants_cache/earnings.json]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path
from typing import List

import sec_facts as sf

SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
OLDER = "https://data.sec.gov/submissions/{name}"


def _dates(block: dict, since: str) -> List[str]:
    out = []
    forms = block.get("form") or []
    items = block.get("items") or []
    filed = block.get("filingDate") or []
    for f, it, d in zip(forms, items, filed):
        if f in ("8-K", "8-K/A") and "2.02" in str(it) and d >= since:
            out.append(d)
    return out


def earnings_dates(cik: int, since: str = "2015-06-01") -> List[str]:
    """תאריכי הודעות התוצאות של חברה, מהחדש לישן בלי כפילויות."""
    data = sf._get(SUBMISSIONS.format(cik=cik))
    if not data:
        return []
    recent = (data.get("filings") or {}).get("recent") or {}
    out = _dates(recent, since)
    oldest = min(recent.get("filingDate") or [since])
    # החברות הגדולות מגישות הרבה; ההיסטוריה הישנה בקבצים נוספים
    for f in (data.get("filings") or {}).get("files") or []:
        if oldest <= since:
            break
        if (f.get("filingTo") or "") < since:
            continue
        more = sf._get(OLDER.format(name=f["name"]))
        if more:
            out += _dates(more, since)
            oldest = min(oldest, f.get("filingFrom") or oldest)
    return sorted(set(out))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2015-06-01")
    ap.add_argument("--out", default="variants_cache/earnings.json")
    a = ap.parse_args()
    from graham_screener import get_sp1500_tickers
    tickers = sorted(set(get_sp1500_tickers()))
    cik = sf.ticker_to_cik()
    out, t0, empty = {}, time.time(), 0
    path = Path(a.out)
    if path.exists():
        out = json.loads(path.read_text(encoding="utf-8"))
    for i, tk in enumerate(tickers, 1):
        if tk in out:
            continue
        c = cik.get(tk.upper())
        if c is None:
            continue
        ds = earnings_dates(c, a.since)
        out[tk] = ds
        empty += not ds
        if i % 50 == 0:
            path.write_text(json.dumps(out), encoding="utf-8")
            print(f"{i}/{len(tickers)} ({time.time()-t0:.0f}s), בלי תאריכים: {empty}, "
                  f"סטטוס אחרון {sf.last_status}", flush=True)
    path.write_text(json.dumps(out), encoding="utf-8")
    print(f"סיום: {len(out)} חברות, {sum(len(v) for v in out.values())} תאריכים, "
          f"{sum(1 for v in out.values() if not v)} בלי תאריכים", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
