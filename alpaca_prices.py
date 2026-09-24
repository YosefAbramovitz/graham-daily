"""
מחירים מ-Alpaca, כשיש מפתח.

למה
---
yfinance הוא מגרד של אתר, לא ממשק. הוא נופל מדי פעם, מגביל קצב בלי להודיע,
ולפעמים מחזיר עמודות חסרות באמצע הורדה של מאות מניות. לבדיקה לאחור, שמושכת
עשר שנים על מאות סימולים, זה מקור הכשל העיקרי.

ל-Alpaca יש ממשק אמיתי: נרות יומיים מתואמים לפיצולים ולדיבידנדים, עד אלף
סימולים בבקשה, עם עימוד מסודר. כשיש מפתח, משתמשים בו. כשאין — נופלים חזרה
ל-yfinance, והכל ממשיך לעבוד בדיוק כמו קודם.

מפתחות
------
נקראים ממשתני סביבה בלבד::

    ALPACA_API_KEY_ID
    ALPACA_API_SECRET_KEY

במאגר הם מוגדרים כסודות (Settings -> Secrets and variables -> Actions).
המפתח לא נכתב בקוד ולא נשמר בקובץ.

מגבלה שכדאי להכיר: בחשבון החינמי הנתונים ההיסטוריים מתחילים בסביבות 2016,
ולכן בדיקה לאחור שמתחילה לפני כן תקבל שורות חסרות בשנים הראשונות.
"""

from __future__ import annotations

import os
import time
from datetime import date
from typing import Dict, Iterable, List, Optional

import pandas as pd
import requests

BARS_URL = "https://data.alpaca.markets/v2/stocks/bars"
BATCH = 100          # כמה סימולים בבקשה אחת
PAGE_LIMIT = 10_000  # שורות לעמוד
FIRST_YEAR = 2016    # תחילת הנתונים בחשבון החינמי


def credentials() -> Optional[tuple]:
    key = os.environ.get("ALPACA_API_KEY_ID", "").strip()
    secret = os.environ.get("ALPACA_API_SECRET_KEY", "").strip()
    return (key, secret) if key and secret else None


def available() -> bool:
    return credentials() is not None


def _headers() -> Dict[str, str]:
    key, secret = credentials()
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret,
            "Accept": "application/json"}


def _fetch_batch(symbols: List[str], start: date, end: date,
                 feed: str) -> Dict[str, List[dict]]:
    """נרות יומיים לקבוצת סימולים, עם עימוד עד הסוף."""
    out: Dict[str, List[dict]] = {}
    page_token = None
    while True:
        params = {
            "symbols": ",".join(symbols),
            "timeframe": "1Day",
            "start": start.isoformat(),
            "end": end.isoformat(),
            "adjustment": "all",      # פיצולים ודיבידנדים
            "limit": PAGE_LIMIT,
            "feed": feed,
            "sort": "asc",
        }
        if page_token:
            params["page_token"] = page_token
        try:
            r = requests.get(BARS_URL, headers=_headers(), params=params, timeout=45)
        except requests.RequestException:
            return out
        if r.status_code == 429:          # חריגת קצב: להמתין ולנסות שוב
            time.sleep(2.0)
            continue
        if r.status_code != 200:
            return out
        try:
            payload = r.json()
        except ValueError:
            return out
        for sym, bars in (payload.get("bars") or {}).items():
            out.setdefault(sym, []).extend(bars)
        page_token = payload.get("next_page_token")
        if not page_token:
            return out


def daily_closes(symbols: Iterable[str], start: date, end: date,
                 feed: str = "sip", quiet: bool = True) -> pd.DataFrame:
    """סגירות יומיות מתואמות. שורה לכל יום, עמודה לכל סימול.

    אותו מבנה בדיוק שמחזיר ``yfinance.download``, כדי שאפשר יהיה להחליף
    ביניהם בלי לשנות שום דבר אחר.
    """
    if not available():
        return pd.DataFrame()

    syms = sorted({str(s).strip().upper() for s in symbols if str(s).strip()})
    if not syms:
        return pd.DataFrame()

    collected: Dict[str, List[dict]] = {}
    for i in range(0, len(syms), BATCH):
        chunk = syms[i:i + BATCH]
        got = _fetch_batch(chunk, start, end, feed)
        if not got and feed == "sip":
            # חשבון חינמי בלי גישה ל-SIP: לנסות שוב עם IEX
            got = _fetch_batch(chunk, start, end, "iex")
        collected.update(got)
        if not quiet:
            print(f"  Alpaca: {min(i + BATCH, len(syms))}/{len(syms)} סימולים",
                  flush=True)

    if not collected:
        return pd.DataFrame()

    frames = {}
    for sym, bars in collected.items():
        if not bars:
            continue
        idx = pd.to_datetime([b["t"] for b in bars], utc=True).tz_convert(None).normalize()
        frames[sym] = pd.Series([float(b["c"]) for b in bars], index=idx)

    if not frames:
        return pd.DataFrame()
    df = pd.DataFrame(frames).sort_index()
    return df[~df.index.duplicated(keep="last")]


def last_prices(symbols: Iterable[str]) -> Dict[str, float]:
    """המחיר האחרון לכל סימול. לשימוש בכלל המכירה."""
    today = date.today()
    start = date(today.year - 1, today.month, 1)
    close = daily_closes(symbols, start, today)
    if close.empty:
        return {}
    out = {}
    for sym in close.columns:
        series = close[sym].dropna()
        if not series.empty:
            out[sym] = float(series.iloc[-1])
    return out


__all__ = ["available", "credentials", "daily_closes", "last_prices"]
