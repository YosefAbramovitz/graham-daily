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
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import requests

# ה-SEC דורשת שם ודוא"ל אמיתיים בכותרת, ומגבילה לעשר בקשות בשנייה.
USER_AGENT = os.environ.get("SEC_USER_AGENT", "graham-daily screener contact@example.com")
REQUEST_GAP = 0.15  # שניות בין בקשות, כלומר כשש בשנייה — מתחת לתקרה בבטחה

# המיפוי מסימול ל-CIK יושב על www.sec.gov, מארח אחר מזה שמגיש את הנתונים,
# והוא נוטה לחסום בקשות בלי כותרת יצירת קשר תקינה. מנסים כמה כתובות.
TICKERS_URLS = [
    "https://www.sec.gov/files/company_tickers.json",
    "https://www.sec.gov/files/company_tickers_exchange.json",
    "https://data.sec.gov/files/company_tickers.json",
]
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

# עותק שנשמר במאגר. כשהוא קיים הוא מנצח, מפני שהמיפוי כמעט לא משתנה ואין
# טעם לתלות סריקה יומית בנקודת כשל שכבר הפילה אותנו פעם.
BUNDLED_MAP = "cik_map.json"

CACHE_DIR = Path(os.environ.get("SEC_CACHE_DIR", ".sec_cache"))

_last_request = 0.0


def _throttle() -> None:
    global _last_request
    gap = time.monotonic() - _last_request
    if gap < REQUEST_GAP:
        time.sleep(REQUEST_GAP - gap)
    _last_request = time.monotonic()


last_status = None   # קוד התגובה האחרון, לצורך הודעות שגיאה מובנות


def _get(url: str, timeout: int = 30) -> Optional[dict]:
    global last_status
    _throttle()
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT,
                                       "Accept-Encoding": "gzip, deflate"},
                         timeout=timeout)
    except requests.RequestException as exc:
        last_status = type(exc).__name__
        return None
    last_status = r.status_code
    if r.status_code != 200:
        return None
    try:
        return r.json()
    except ValueError:
        last_status = "לא JSON"
        return None


# ---------------------------------------------------------------------------
# מיפוי סימול למספר CIK
# ---------------------------------------------------------------------------

_cik_cache: Optional[Dict[str, int]] = None


def _parse_ticker_payload(raw) -> Dict[str, int]:
    """שתי הכתובות מחזירות מבנים שונים. מנרמל את שניהם."""
    mapping: Dict[str, int] = {}
    if isinstance(raw, dict) and "fields" in raw and "data" in raw:
        # company_tickers_exchange.json: כותרות ואז שורות
        fields = [str(f).lower() for f in raw["fields"]]
        try:
            i_cik, i_tk = fields.index("cik"), fields.index("ticker")
        except ValueError:
            return mapping
        for row in raw["data"]:
            try:
                sym = str(row[i_tk]).strip().upper()
                if sym:
                    mapping[sym] = int(row[i_cik])
            except (IndexError, TypeError, ValueError):
                continue
        return mapping

    if isinstance(raw, dict):
        for entry in raw.values():
            if not isinstance(entry, dict):
                continue
            sym = str(entry.get("ticker", "")).strip().upper()
            cik = entry.get("cik_str", entry.get("cik"))
            if sym and cik is not None:
                try:
                    mapping[sym] = int(cik)
                except (TypeError, ValueError):
                    continue
    return mapping


def ticker_to_cik(refresh: bool = False, quiet: bool = True) -> Dict[str, int]:
    """סימול באותיות גדולות -> מספר CIK.

    בלי המיפוי הזה כל שכבת ה-SEC חסרת ערך: אפשר למשוך את כל הנתונים של כל
    החברות ואז לא לדעת איזו שורה שייכת לאיזו מניה. זה כבר קרה פעם אחת -
    סריקה שלמה משכה 6,840 חברות ואז שמרה 1504 שורות של yahoo - ולכן כאן
    מנסים כמה מקורות, ומדווחים בקול כשכולם נכשלים.
    """
    global _cik_cache
    if _cik_cache is not None and not refresh:
        return _cik_cache

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = CACHE_DIR / "cik_map.json"

    # 1. מטמון של הריצה הזאת
    if cache.exists() and not refresh:
        try:
            _cik_cache = {k: int(v) for k, v in json.loads(cache.read_text()).items()}
            if _cik_cache:
                return _cik_cache
        except (ValueError, OSError):
            pass

    # 2. עותק שנשמר במאגר
    bundled = Path(BUNDLED_MAP)
    if bundled.exists() and not refresh:
        try:
            _cik_cache = {k: int(v) for k, v in json.loads(bundled.read_text()).items()}
            if _cik_cache:
                if not quiet:
                    print(f"  מיפוי CIK מהעותק שבמאגר: {len(_cik_cache)} סימולים", flush=True)
                return _cik_cache
        except (ValueError, OSError):
            pass

    # 3. מהרשת, לפי סדר הכתובות
    mapping: Dict[str, int] = {}
    statuses = []
    for url in TICKERS_URLS:
        raw = _get(url)
        statuses.append(last_status)
        if raw:
            mapping = _parse_ticker_payload(raw)
            if mapping:
                if not quiet:
                    print(f"  מיפוי CIK מ-{url}: {len(mapping)} סימולים", flush=True)
                break
        if not quiet:
            print(f"  {url} -> {last_status}", flush=True)

    if not mapping:
        if not quiet:
            print("  לא התקבל מיפוי CIK משום מקור.", flush=True)
            if any(st in (403, 429) for st in statuses):
                print("  ה-SEC דחתה את הבקשות. כמעט תמיד זו כותרת יצירת הקשר:\n"
                      "  יש להגדיר SEC_USER_AGENT בפורמט 'graham-daily your@email.com'.",
                      flush=True)
            elif "@" not in USER_AGENT:
                print("  ל-User-Agent אין כתובת דוא\"ל, וה-SEC דורשת אחת.\n"
                      "  יש להגדיר SEC_USER_AGENT בפורמט 'graham-daily your@email.com'.",
                      flush=True)
            else:
                print(f"  המארח אינו נגיש מכאן ({statuses[0]}). זה תקין בסביבה\n"
                      "  חסומה, אבל בשרתי GitHub זה אמור לעבוד.", flush=True)
        _cik_cache = {}
        return _cik_cache

    # סימולים עם מקף נכתבים לפעמים עם נקודה במקורות אחרים
    for sym in list(mapping):
        if "-" in sym:
            mapping.setdefault(sym.replace("-", "."), mapping[sym])

    for target in (cache, bundled):
        try:
            target.write_text(json.dumps(mapping))
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
    # החוב מפוצל לארבעה שדות, כי חברות מדווחות אותו בצורות שאינן מתחברות
    # בחיבור פשוט: LongTermDebt כבר כולל את החלק השוטף, DebtCurrent כבר כולל
    # את ההלוואות לזמן קצר. הצירוף הנכון נעשה ב-total_debt.
    "debt_long": ["LongTermDebtNoncurrent",                 # ארוך, בלי החלק השוטף
                  "LongTermDebtAndCapitalLeaseObligations"],
    "debt_total": ["LongTermDebt"],                        # ארוך כולל החלק השוטף
    "debt_short": ["LongTermDebtCurrent",                  # החלק השוטף של הארוך
                   "LongTermDebtAndCapitalLeaseObligationsCurrent"],
    "debt_current": ["DebtCurrent"],                       # כל החוב השוטף
    "short_borrowings": ["ShortTermBorrowings", "CommercialPaper",
                         "OtherShortTermBorrowings"],
    "shares_outstanding": ["CommonStockSharesOutstanding",
                           "CommonStockSharesIssued"],

    # תזרים מזומנים (שורות זרימה)
    "cfo": ["NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment",
              "PaymentsToAcquireProductiveAssets"],
    # PaymentsOfDistributionsToAffiliates הוסר: זו חלוקה לבעלי זכויות מיעוט,
    # לא דיבידנד לבעלי המניות. דיבידנד לרגילות קודם (JNJ ו-PFE משתמשות
    # ב-PaymentsOfOrdinaryDividends), הכולל כגיבוי.
    "dividends_paid": ["PaymentsOfDividendsCommonStock", "PaymentsOfOrdinaryDividends",
                       "PaymentsOfDividends"],
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
    "debt_total", "debt_current", "short_borrowings",
}


def total_debt(get) -> Optional[float]:
    """סך החוב הפיננסי, בלי לספור פעמיים.

    ``get`` מקבל שם שדה ומחזיר מספר או None. הכללים:
      * החלק השוטף: DebtCurrent אם קיים (הוא כבר כולל הכול), אחרת החלק השוטף
        של החוב הארוך ועוד הלוואות לזמן קצר ונייר ערך מסחרי.
      * החלק הארוך: הלא-שוטף אם דווח. אם דווח רק LongTermDebt, הוא כבר כולל
        את החלק השוטף, ולכן מוסיפים לו רק את מה שאינו חוב ארוך.
    """
    def g(k):
        v = get(k)
        return float(v) if isinstance(v, (int, float)) and v == v else None

    noncurrent, total = g("debt_long"), g("debt_total")
    cur_ltd, cur_all, borrow = g("debt_short"), g("debt_current"), g("short_borrowings")

    if cur_all is not None:
        current = cur_all
    elif cur_ltd is not None or borrow is not None:
        current = (cur_ltd or 0.0) + (borrow or 0.0)
    else:
        current = None

    if noncurrent is not None:
        return noncurrent + (current or 0.0)
    if total is not None:
        extra = (cur_all - (cur_ltd or 0.0)) if cur_all is not None else (borrow or 0.0)
        return total + max(extra, 0.0)
    return current

UNITS = {"eps_diluted": "USD/shares", "eps_basic": "USD/shares",
         "shares_outstanding": "shares"}

ANNUAL_MIN_DAYS = 340
STALE_DAYS = 400      # שדה שהתקופה האחרונה שלו ישנה מזה ביחס למאזן - לא נוכחי
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
    tag: Optional[str] = None

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
            # לא עוצרים בתגית הראשונה. חברות מחליפות תגית כשהתקן משתנה - ההכנסות
            # עברו ל-RevenueFromContractWithCustomer... עם ASC 606 בדוחות של 2018 -
            # והתגית החדשה מכסה רק את השנים שאחרי המעבר. עצירה בה מחקה את כל
            # ההיסטוריה שלפני, ובבדיקה לאחור אף חברה לא עברה את מבחן הגודל לפני 2019.
            # הבחירה בין תגיות נעשית לכל תקופה בנפרד, ב-_pick_per_period.
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
                          start=_parse_day(r.get("start")),
                          tag=r.get("tag")))
    return facts


def _tag_rank(field_name: str, tag: Optional[str]) -> int:
    tags = CONCEPTS.get(field_name, [])
    return tags.index(tag) if tag in tags else len(tags)


def _pick_per_period(facts: List[Fact], field_name: str) -> Dict[date, Fact]:
    """דיווח אחד לכל תקופה.

    קודם התגית המועדפת מבין אלה שכבר הוגשו, ובתוך אותה תגית - ההגשה
    הראשונה, כי זה מה שהמשקיע ראה אז. הסינון לפי תאריך הגשה קורה לפני כן,
    ולכן תגית שהופיעה רק אחר כך אינה יכולה לדרוס את מה שהיה ידוע בזמנו.
    """
    by_period: Dict[date, Fact] = {}
    for f in facts:
        seen = by_period.get(f.end)
        if seen is None or ((_tag_rank(field_name, f.tag), f.filed)
                            < (_tag_rank(field_name, seen.tag), seen.filed)):
            by_period[f.end] = f
    return by_period


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

    by_period = _pick_per_period(facts, field_name)

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
    by_period = _pick_per_period(facts, field_name)
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

    # שדה שהחברה הפסיקה לדווח לפני שנים (למשל תגית שהוחלפה) לא נחשב נוכחי.
    # מודדים מול תאריך המאזן האחרון שדווח.
    ref = annual_series(compact, "assets", when)
    stale_before = (ref[0].end - timedelta(days=STALE_DAYS)) if ref else None

    for field_name in CONCEPTS:
        series = annual_series(compact, field_name, when)
        if not series:
            continue
        if stale_before is not None and series[0].end < stale_before:
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

# חברה שעברה ארגון מחדש יכולה לקבל CIK חדש, ומיפוי הסימולים של ה-SEC מצביע
# תמיד על הישות העדכנית. ההיסטוריה נשארת תחת ה-CIK הקודם, ובלעדיה החברה
# נראית בבדיקה לאחור כאילו לא הגישה דבר. כאן ממזגים את השניים.
PREDECESSOR_CIKS: Dict[int, List[int]] = {
    2115436: [34088],   # ExxonMobil Holdings Corp <- Exxon Mobil Corp
}


def _cache_path(cik: int) -> Path:
    # v3: שדות החוב פוצלו ונוספו ישויות קודמות; מטמון ישן חסר אותם.
    return CACHE_DIR / "facts_v3" / f"CIK{cik:010d}.json.gz"


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
    for old in PREDECESSOR_CIKS.get(int(cik), []):
        old_raw = _get(FACTS_URL.format(cik=old))
        for field_name, rows in extract(old_raw or {}).items():
            compact.setdefault(field_name, []).extend(rows)
    if use_cache and compact:
        save_cached(cik, compact)
    return compact


def facts_for(ticker: str, use_cache: bool = True) -> Optional[Dict[str, List[dict]]]:
    cik = ticker_to_cik().get(ticker.strip().upper())
    if cik is None:
        return None
    return company_facts(cik, use_cache=use_cache)


__all__ = ["CONCEPTS", "Fact", "annual_series", "as_of", "company_facts",
           "extract", "facts_for", "quarterly_series", "ticker_to_cik", "total_debt",
           "ttm"]


def main() -> int:
    """כלי אבחון קטן. ``--check`` אומר אם שכבת ה-SEC בכלל עובדת מכאן,
    ו-``--refresh-map`` שומר את מיפוי ה-CIK לקובץ שנשמר במאגר, כדי שהסריקה
    היומית לא תהיה תלויה בנקודת הכשל הזאת."""
    import argparse
    ap = argparse.ArgumentParser(description="בדיקה ותחזוקה של שכבת ה-SEC")
    ap.add_argument("--check", action="store_true", help="בדוק שהמיפוי והנתונים נגישים")
    ap.add_argument("--refresh-map", action="store_true", help="משוך ושמור את מיפוי ה-CIK")
    ap.add_argument("--ticker", default="AAPL", help="סימול לבדיקה")
    args = ap.parse_args()

    print(f"User-Agent: {USER_AGENT}")
    if "@" not in USER_AGENT:
        print("אזהרה: אין כתובת דוא\"ל ב-User-Agent. ה-SEC חוסמת בקשות כאלה.")

    mapping = ticker_to_cik(refresh=args.refresh_map, quiet=False)
    print(f"מיפוי CIK: {len(mapping)} סימולים")
    if not mapping:
        return 1

    if args.refresh_map:
        print(f"נשמר ל-{BUNDLED_MAP}")

    if args.check:
        sym = args.ticker.strip().upper()
        cik = mapping.get(sym)
        print(f"{sym} -> CIK {cik}")
        if cik is None:
            return 1
        compact = company_facts(cik, use_cache=False)
        if not compact:
            print(f"לא התקבלו דוחות ({last_status})")
            return 1
        snap = as_of(compact)
        got = [k for k in ("revenue", "assets", "ebit", "net_income", "cfo")
               if snap.get(k) is not None]
        print(f"שדות שהתקבלו: {len(compact)} | מתוכם מרכזיים: {', '.join(got)}")
        print(f"הדוח האחרון הוגש: {snap.get('_last_filed')}")
    return 0


if __name__ == "__main__":
    import sys as _sys
    _sys.exit(main())
