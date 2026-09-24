"""
שכבת נתונים מדוחות רשות ניירות ערך האמריקאית (SEC EDGAR, XBRL).

למה זה קיים
-----------
yfinance מחזיר את הדוחות **כפי שהם נראים היום**: אחרי תיקונים, אחרי הצגה מחדש,
ובלי לספר מתי כל מספר התפרסם. זה מספיק לסריקה של היום, אבל הורס כל בדיקה לאחור:
אם נחשב קריטריון ל-1 בינואר על מספר שפורסם רק במרץ, "קנינו" עם מידע שלא היה קיים.

ל-SEC יש לכל נתון שדה ``filed`` — תאריך ההגשה בפועל. המודול הזה שומר אותו,
ומאפשר לשאול "מה היה ידוע על החברה הזאת בתאריך X" בלי לרמות.

שני מסלולים
-----------
* ``companyfacts`` — כל הנתונים של חברה אחת, כולל תאריכי הגשה. מדויק, אבל כבד:
  קובץ של חברה גדולה יכול להיות עשרות מגה. מתאים לבדיקה לאחור ולבדיקת מניה בודדת.
* ``sec_frames`` (מודול נפרד) — מושך מושג חשבונאי אחד לכל היקום בבקשה אחת.
  מתאים לסריקה היומית, שבה ממילא הכל "ידוע היום" ותאריך ההגשה לא רלוונטי.
"""

from __future__ import annotations

import gzip
import json
import os
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import requests

# ה-SEC דורשת שם ודוא"ל אמיתיים בכותרת, ומגבילה לעשר בקשות בשנייה.
USER_AGENT = os.environ.get("SEC_USER_AGENT", "graham-daily screener contact@example.com")
REQUEST_GAP = 0.15  # שניות בין בקשות, כלומר כשש בשנייה — מתחת לתקרה בבטחה

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

CACHE_DIR = Path(os.environ.get("SEC_CACHE_DIR", ".sec_cache"))

_last_request = 0.0


def _throttle() -> None:
    global _last_request
    gap = time.monotonic() - _last_request
    if gap < REQUEST_GAP:
        time.sleep(REQUEST_GAP - gap)
    _last_request = time.monotonic()


def _get(url: str, timeout: int = 30) -> Optional[dict]:
    _throttle()
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT,
                                       "Accept-Encoding": "gzip, deflate"},
                         timeout=timeout)
    except requests.RequestException:
        return None
    if r.status_code != 200:
        return None
    try:
        return r.json()
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# מיפוי סימול למספר CIK
# ---------------------------------------------------------------------------

_cik_cache: Optional[Dict[str, int]] = None


def ticker_to_cik(refresh: bool = False) -> Dict[str, int]:
    """סימול באותיות גדולות -> מספר CIK. נשמר בדיסק כי הוא כמעט לא משתנה."""
    global _cik_cache
    if _cik_cache is not None and not refresh:
        return _cik_cache

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / "cik_map.json"
    if path.exists() and not refresh:
        try:
            _cik_cache = {k: int(v) for k, v in json.loads(path.read_text()).items()}
            return _cik_cache
        except (ValueError, OSError):
            pass

    raw = _get(TICKERS_URL)
    if not raw:
        _cik_cache = {}
        return _cik_cache

    mapping: Dict[str, int] = {}
    for entry in raw.values():
        sym = str(entry.get("ticker", "")).strip().upper()
        cik = entry.get("cik_str")
        if sym and cik is not None:
            mapping[sym] = int(cik)

    # yfinance כותב BRK-B, ה-SEC כותבת BRK-B גם כן, אבל יש מקורות עם BRK.B.
    for sym in list(mapping):
        if "-" in sym:
            mapping.setdefault(sym.replace("-", "."), mapping[sym])

    try:
        path.write_text(json.dumps(mapping))
    except OSError:
        pass
    _cik_cache = mapping
    return mapping


# ---------------------------------------------------------------------------
# המושגים החשבונאיים שאנחנו צריכים
# ---------------------------------------------------------------------------
# לכל שדה רשימת תגיות us-gaap לפי סדר עדיפות. חברות מדווחות באותו דבר בתגיות
# שונות, ולכן צריך כמה חלופות כמעט לכל שורה.

CONCEPTS: Dict[str, List[str]] = {
    # דוח רווח והפסד (שורות זרימה)
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax",
                "RevenueFromContractWithCustomerIncludingAssessedTax",
                "Revenues", "SalesRevenueNet", "SalesRevenueGoodsNet"],
    "cogs": ["CostOfGoodsAndServicesSold", "CostOfRevenue", "CostOfGoodsSold"],
    "gross_profit": ["GrossProfit"],
    "ebit": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss",
                   "ProfitLoss",
                   "NetIncomeLossAvailableToCommonStockholdersBasic"],
    "sga": ["SellingGeneralAndAdministrativeExpense",
            "GeneralAndAdministrativeExpense"],
    "depreciation": ["DepreciationDepletionAndAmortization",
                     "DepreciationAmortizationAndAccretionNet",
                     "DepreciationAndAmortization", "Depreciation"],
    "interest_expense": ["InterestExpense", "InterestExpenseDebt",
                         "InterestIncomeExpenseNet"],
    "tax_expense": ["IncomeTaxExpenseBenefit"],
    "eps_diluted": ["EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted"],
    "eps_basic": ["EarningsPerShareBasic"],

    # מאזן (שורות מצב)
    "assets": ["Assets"],
    "assets_current": ["AssetsCurrent"],
    "liabilities": ["Liabilities"],
    "liabilities_current": ["LiabilitiesCurrent"],
    "equity": ["StockholdersEquity",
               "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue",
             "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"],
    "short_term_investments": ["ShortTermInvestments",
                               "AvailableForSaleSecuritiesDebtSecuritiesCurrent",
                               "MarketableSecuritiesCurrent"],
    "receivables": ["AccountsReceivableNetCurrent", "ReceivablesNetCurrent"],
    "ppe_net": ["PropertyPlantAndEquipmentNet"],
    "retained_earnings": ["RetainedEarningsAccumulatedDeficit"],
    "debt_long": ["LongTermDebtNoncurrent", "LongTermDebt",
                  "LongTermDebtAndCapitalLeaseObligations"],
    "debt_short": ["LongTermDebtCurrent", "DebtCurrent", "ShortTermBorrowings",
                   "OtherShortTermBorrowings"],
    "shares_outstanding": ["CommonStockSharesOutstanding",
                           "CommonStockSharesIssued"],

    # תזרים מזומנים (שורות זרימה)
    "cfo": ["NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment",
              "PaymentsToAcquireProductiveAssets"],
    "dividends_paid": ["PaymentsOfDividends", "PaymentsOfDividendsCommonStock",
                       "PaymentsOfDistributionsToAffiliates"],
    "buybacks": ["PaymentsForRepurchaseOfCommonStock",
                 "PaymentsForRepurchaseOfEquity"],
    "stock_issued": ["ProceedsFromIssuanceOfCommonStock",
                     "ProceedsFromIssuanceOfSharesUnderIncentiveAndShareBasedCompensationPlans"],
}

# שורות מצב: נקודה בזמן, אין להן ``start``. שורות זרימה: תקופה.
INSTANT_FIELDS = {
    "assets", "assets_current", "liabilities", "liabilities_current", "equity",
    "cash", "short_term_investments", "receivables", "ppe_net",
    "retained_earnings", "debt_long", "debt_short", "shares_outstanding",
}

UNITS = {"eps_diluted": "USD/shares", "eps_basic": "USD/shares",
         "shares_outstanding": "shares"}

ANNUAL_MIN_DAYS = 340
ANNUAL_MAX_DAYS = 400


@dataclass
class Fact:
    """נתון בודד, עם התאריך שבו באמת פורסם."""
    end: date
    val: float
    filed: date
    form: str
    fy: Optional[int] = None
    start: Optional[date] = None

    @property
    def days(self) -> Optional[int]:
        if self.start is None:
            return None
        return (self.end - self.start).days


def _parse_day(text) -> Optional[date]:
    if not text:
        return None
    try:
        return datetime.strptime(str(text)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def extract(raw: dict) -> Dict[str, List[dict]]:
    """מצמצם את קובץ ה-companyfacts הענק לשדות שאנחנו באמת צריכים.

    מחזיר מיפוי שם-שדה -> רשימת נתונים, כל אחד עם תאריך ההגשה שלו. התוצאה
    קטנה מספיק כדי להישמר בדיסק לכל היקום.
    """
    facts = (raw or {}).get("facts", {})
    gaap = facts.get("us-gaap", {})
    dei = facts.get("dei", {})

    out: Dict[str, List[dict]] = {}
    for field_name, tags in CONCEPTS.items():
        unit_name = UNITS.get(field_name, "USD")
        rows: List[dict] = []
        for tag in tags:
            node = gaap.get(tag) or dei.get(tag)
            if not node:
                continue
            units = node.get("units", {})
            series = units.get(unit_name)
            if series is None and unit_name == "shares":
                series = units.get("shares")
            if not series:
                continue
            for point in series:
                end = _parse_day(point.get("end"))
                filed = _parse_day(point.get("filed"))
                val = point.get("val")
                if end is None or filed is None or val is None:
                    continue
                rows.append({
                    "end": end.isoformat(),
                    "val": float(val),
                    "filed": filed.isoformat(),
                    "form": str(point.get("form") or ""),
                    "fy": point.get("fy"),
                    "start": (point.get("start") or None),
                    "tag": tag,
                })
            if rows:
                # התגית הראשונה שהחזירה משהו מנצחת; לא מערבבים תגיות באותו שדה.
                break
        if rows:
            out[field_name] = rows

    # מספר המניות במחזור מגיע לרוב מ-dei ולא מ-us-gaap.
    if "shares_outstanding" not in out:
        node = dei.get("EntityCommonStockSharesOutstanding")
        if node:
            rows = []
            for point in node.get("units", {}).get("shares", []):
                end = _parse_day(point.get("end"))
                filed = _parse_day(point.get("filed"))
                val = point.get("val")
                if end and filed and val is not None:
                    rows.append({"end": end.isoformat(), "val": float(val),
                                 "filed": filed.isoformat(),
                                 "form": str(point.get("form") or ""),
                                 "fy": point.get("fy"), "start": None,
                                 "tag": "EntityCommonStockSharesOutstanding"})
            if rows:
                out["shares_outstanding"] = rows

    return out


def _to_facts(rows: Iterable[dict]) -> List[Fact]:
    facts = []
    for r in rows:
        end = _parse_day(r.get("end"))
        filed = _parse_day(r.get("filed"))
        if end is None or filed is None:
            continue
        facts.append(Fact(end=end, val=float(r["val"]), filed=filed,
                          form=str(r.get("form") or ""), fy=r.get("fy"),
                          start=_parse_day(r.get("start"))))
    return facts


def annual_series(compact: Dict[str, List[dict]], field_name: str,
                  as_of_date: Optional[date] = None) -> List[Fact]:
    """סדרה שנתית של שדה, מהחדש לישן, רק ממה שהוגש עד ``as_of_date``.

    לשורות זרימה נלקחות רק תקופות באורך שנה בערך. לשורות מצב נלקח כל דיווח,
    וממוינות לפי תאריך הסיום. כשאותה תקופה דווחה יותר מפעם אחת — למשל בדוח
    המקורי ואחר כך בהצגה מחדש — נבחר הדיווח **הראשון** שהיה זמין, כי זה מה
    שהמשקיע באמת ראה אז.
    """
    rows = compact.get(field_name)
    if not rows:
        return []

    facts = _to_facts(rows)
    if as_of_date is not None:
        facts = [f for f in facts if f.filed <= as_of_date]
    if not facts:
        return []

    if field_name not in INSTANT_FIELDS:
        facts = [f for f in facts
                 if f.days is not None and ANNUAL_MIN_DAYS <= f.days <= ANNUAL_MAX_DAYS]
        if not facts:
            return []

    by_period: Dict[date, Fact] = {}
    for f in facts:
        seen = by_period.get(f.end)
        if seen is None or f.filed < seen.filed:
            by_period[f.end] = f

    return sorted(by_period.values(), key=lambda f: f.end, reverse=True)


def quarterly_series(compact: Dict[str, List[dict]], field_name: str,
                     as_of_date: Optional[date] = None) -> List[Fact]:
    """אותו דבר לרבעונים, לצורך חישוב שנים-עשר החודשים האחרונים."""
    rows = compact.get(field_name)
    if not rows or field_name in INSTANT_FIELDS:
        return []
    facts = _to_facts(rows)
    if as_of_date is not None:
        facts = [f for f in facts if f.filed <= as_of_date]
    facts = [f for f in facts if f.days is not None and 60 <= f.days <= 120]
    by_period: Dict[date, Fact] = {}
    for f in facts:
        seen = by_period.get(f.end)
        if seen is None or f.filed < seen.filed:
            by_period[f.end] = f
    return sorted(by_period.values(), key=lambda f: f.end, reverse=True)


def ttm(compact: Dict[str, List[dict]], field_name: str,
        as_of_date: Optional[date] = None) -> Optional[float]:
    """סכום ארבעת הרבעונים האחרונים, או השנה האחרונה אם אין רבעונים."""
    qs = quarterly_series(compact, field_name, as_of_date)
    if len(qs) >= 4:
        window = qs[:4]
        # לוודא שהרבעונים באמת עוקבים ומכסים שנה בערך
        span = (window[0].end - window[3].end).days
        if 240 <= span <= 300:
            return sum(f.val for f in window)
    annual = annual_series(compact, field_name, as_of_date)
    return annual[0].val if annual else None


def as_of(compact: Dict[str, List[dict]], when: Optional[date] = None,
          years: int = 1) -> Dict[str, object]:
    """תמונת מצב של החברה כפי שהיא נראתה בתאריך נתון.

    מחזיר לכל שדה את הערך העדכני ביותר שהיה ידוע אז, ובנוסף ``_history``
    עם עד ``years`` שנים אחורה, לשימוש במבחנים שצריכים השוואה בין שנים
    (בניש, צבירות, יציבות רווחים).
    """
    when = when or date.today()
    snapshot: Dict[str, object] = {"as_of": when.isoformat()}
    history: Dict[str, List[float]] = {}
    period_ends: Dict[str, List[str]] = {}

    for field_name in CONCEPTS:
        series = annual_series(compact, field_name, when)
        if not series:
            continue
        snapshot[field_name] = series[0].val
        history[field_name] = [f.val for f in series[:years]]
        period_ends[field_name] = [f.end.isoformat() for f in series[:years]]

    snapshot["_history"] = history
    snapshot["_period_ends"] = period_ends
    latest = [annual_series(compact, f, when) for f in ("assets", "revenue", "net_income")]
    filed_dates = [s[0].filed for s in latest if s]
    snapshot["_last_filed"] = max(filed_dates).isoformat() if filed_dates else None
    return snapshot


# ---------------------------------------------------------------------------
# שליפה עם מטמון
# ---------------------------------------------------------------------------

def _cache_path(cik: int) -> Path:
    return CACHE_DIR / "facts" / f"CIK{cik:010d}.json.gz"


def load_cached(cik: int) -> Optional[Dict[str, List[dict]]]:
    path = _cache_path(cik)
    if not path.exists():
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def save_cached(cik: int, compact: Dict[str, List[dict]]) -> None:
    path = _cache_path(cik)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with gzip.open(path, "wt", encoding="utf-8") as fh:
            json.dump(compact, fh)
    except OSError:
        pass


def company_facts(cik: int, use_cache: bool = True) -> Optional[Dict[str, List[dict]]]:
    """מוריד ומצמצם את הדוחות של חברה אחת. מחזיר את המבנה הקומפקטי."""
    if use_cache:
        cached = load_cached(cik)
        if cached is not None:
            return cached
    raw = _get(FACTS_URL.format(cik=cik))
    if not raw:
        return None
    compact = extract(raw)
    if use_cache and compact:
        save_cached(cik, compact)
    return compact


def facts_for(ticker: str, use_cache: bool = True) -> Optional[Dict[str, List[dict]]]:
    cik = ticker_to_cik().get(ticker.strip().upper())
    if cik is None:
        return None
    return company_facts(cik, use_cache=use_cache)


__all__ = ["CONCEPTS", "Fact", "annual_series", "as_of", "company_facts",
           "extract", "facts_for", "quarterly_series", "ticker_to_cik", "ttm"]
