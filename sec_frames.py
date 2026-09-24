"""
שליפת נתוני SEC לכל היקום בבת אחת, דרך ה-frames API.

ההבדל מ-``sec_facts``: שם מורידים חברה אחת בכל פעם, עם תאריכי ההגשה. כאן
מורידים **מושג חשבונאי אחד לכל החברות** בבקשה אחת. לסריקה יומית של 1500 מניות
זה ההבדל בין כמה עשרות ג'יגה-בייט לכמה מאות קילו-בייט.

מה מוותרים: ה-frames אינו מחזיר את תאריך ההגשה. לסריקה של היום זה לא משנה —
כל מה שנמצא שם ממילא כבר פורסם. לבדיקה לאחור זה כן משנה, ולכן הבדיקה לאחור
משתמשת ב-``sec_facts``.

מגבלה שכדאי להכיר: ה-frames מיישר נתונים לרבעונים קלנדריים. חברה ששנת הכספים
שלה מסתיימת בפברואר לא תמיד תופיע במסגרת הקלנדרית, ולכן המודול מנסה כמה
תקופות אחורה ולוקח לכל חברה את העדכנית ביותר שנמצאה.
"""

from __future__ import annotations

from datetime import date
from typing import Dict, Iterable, List, Optional

import pandas as pd

from sec_facts import CONCEPTS, INSTANT_FIELDS, UNITS, _get, ticker_to_cik

FRAME_URL = ("https://data.sec.gov/api/xbrl/frames/us-gaap/{tag}/{unit}/"
             "{period}.json")

# כמה תקופות אחורה לנסות לפני שמוותרים על חברה
ANNUAL_LOOKBACK = 3
INSTANT_LOOKBACK = 6


def _unit_path(field: str) -> str:
    unit = UNITS.get(field, "USD")
    return unit.replace("/", "-per-")


def annual_periods(today: Optional[date] = None, count: int = ANNUAL_LOOKBACK) -> List[str]:
    """תקופות שנתיות לנסות, מהחדשה לישנה.

    דוח שנתי מתפרסם חודשיים עד שלושה אחרי סוף השנה, ולכן בתחילת שנה חדשה
    השנה שהסתיימה עוד לא זמינה והמסגרת האחרונה היא זו שלפניה.
    """
    today = today or date.today()
    newest = today.year - 1 if today.month >= 4 else today.year - 2
    return [f"CY{newest - i}" for i in range(count)]


def instant_periods(today: Optional[date] = None, count: int = INSTANT_LOOKBACK) -> List[str]:
    """רבעונים לנסות עבור שורות מאזן, מהחדש לישן."""
    today = today or date.today()
    # הדוח הרבעוני מתפרסם כחודש וחצי אחרי סוף הרבעון
    quarter = (today.month - 1) // 3 + 1
    year = today.year
    quarter -= 1
    if quarter == 0:
        quarter, year = 4, year - 1
    out = []
    for _ in range(count):
        out.append(f"CY{year}Q{quarter}I")
        quarter -= 1
        if quarter == 0:
            quarter, year = 4, year - 1
    return out


def frame(tag: str, unit: str, period: str) -> Dict[int, float]:
    """מושג אחד, תקופה אחת, כל החברות. מפתח: CIK."""
    raw = _get(FRAME_URL.format(tag=tag, unit=unit, period=period))
    if not raw:
        return {}
    out: Dict[int, float] = {}
    for point in raw.get("data", []):
        cik = point.get("cik")
        val = point.get("val")
        if cik is None or val is None:
            continue
        try:
            out[int(cik)] = float(val)
        except (TypeError, ValueError):
            continue
    return out


def field_snapshot(field: str, periods: Iterable[str]) -> Dict[int, float]:
    """ערך אחד לכל חברה עבור שדה, מהתקופה העדכנית ביותר שבה היא מופיעה.

    עוברים על התגיות לפי סדר העדיפות ועל התקופות מהחדשה לישנה, וממלאים רק
    חברות שעוד אין להן ערך. כך חברה שמדווחת בתגית נדירה או בשנת כספים חריגה
    עדיין נתפסת, בלי לדרוס נתון עדכני יותר.
    """
    unit = _unit_path(field)
    merged: Dict[int, float] = {}
    for period in periods:
        for tag in CONCEPTS.get(field, []):
            data = frame(tag, unit, period)
            if not data:
                continue
            for cik, val in data.items():
                merged.setdefault(cik, val)
    return merged


def universe_snapshot(fields: Optional[Iterable[str]] = None,
                      today: Optional[date] = None,
                      progress: bool = True) -> pd.DataFrame:
    """טבלה של כל היקום: שורה לכל CIK, עמודה לכל שדה."""
    fields = list(fields or CONCEPTS.keys())
    ann = annual_periods(today)
    inst = instant_periods(today)

    columns: Dict[str, Dict[int, float]] = {}
    for i, field in enumerate(fields, 1):
        periods = inst if field in INSTANT_FIELDS else ann
        columns[field] = field_snapshot(field, periods)
        if progress:
            got = len(columns[field])
            print(f"  [{i}/{len(fields)}] {field}: {got} חברות", flush=True)

    df = pd.DataFrame(columns)
    df.index.name = "cik"
    return df


def with_tickers(df: pd.DataFrame) -> pd.DataFrame:
    """מוסיף עמודת סימול לטבלה שממופתחת ב-CIK."""
    mapping = ticker_to_cik()
    by_cik: Dict[int, str] = {}
    for sym, cik in mapping.items():
        # כשיש כמה סדרות מניות לאותו CIK, נשמר הסימול הקצר ביותר
        current = by_cik.get(cik)
        if current is None or len(sym) < len(current):
            by_cik[cik] = sym
    out = df.copy()
    out.insert(0, "ticker", [by_cik.get(int(c)) for c in out.index])
    return out


def snapshot_for(tickers: Iterable[str], today: Optional[date] = None,
                 progress: bool = True) -> pd.DataFrame:
    """תמונת מצב מסוננת לרשימת סימולים, ממופתחת בסימול."""
    mapping = ticker_to_cik()
    wanted = {}
    for sym in tickers:
        cik = mapping.get(sym.strip().upper())
        if cik is not None:
            wanted[int(cik)] = sym.strip().upper()

    full = universe_snapshot(today=today, progress=progress)
    sub = full[full.index.isin(wanted)].copy()
    sub.insert(0, "ticker", [wanted[int(c)] for c in sub.index])
    return sub.set_index("ticker")


__all__ = ["annual_periods", "field_snapshot", "frame", "instant_periods",
           "snapshot_for", "universe_snapshot", "with_tickers"]
