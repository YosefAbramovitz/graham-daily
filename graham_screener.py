#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
graham_screener.py
===================

"Screener-agent" (סורק מניות) שבודק מניות מול 7 הקריטריונים של בנג'מין גראהם
למשקיע המגן (Defensive Investor), מתוך הספר "המשקיע הנבון" (פרק 14), וגם גרסה
מקוצרת של 5 הקריטריונים למשקיע היוזם (Enterprising Investor, פרק 15).

חשוב להבין לפני שמריצים:
--------------------------
1. זהו כלי סינון טכני-כמותי בלבד, שמיישם קריטריונים שגראהם כתב לפני עשרות
   שנים. הוא לא ייעוץ השקעות, לא המלצה לקנות או למכור דבר, והתוצאות תלויות
   לגמרי באיכות ובעדכניות הנתונים שמגיעים מהמקור החיצוני (Yahoo Finance
   דרך הספרייה yfinance). בדקו כל מניה בעצמכם/עם יועץ מוסמך לפני החלטה.
2. הסף המקורי של גראהם ל"גודל מספיק" (100 מיליון דולר מכירות שנתיות, נכון
   ל-1970) שווה היום למשהו כמו מיליארד דולר ומעלה בגלל אינפלציה של יותר מ-50
   שנה. בברירת המחדל של הסקריפט השתמשתי בסף מותאם-אינפלציה (ר' MIN_REVENUE
   למטה) - אפשר וכדאי לשנות אותו לפי הטעם שלכם.
3. נתונים חינמיים (yfinance/Yahoo) בדרך כלל נותנים 4-5 שנות דוחות שנתיים,
   לא 10. הסקריפט משתמש בכל מה שיש וזה מסומן ברור בפלט (n_years_used),
   כך שאתם יודעים על כמה שנים בפועל נבדקה כל קריטריון.
4. כדי להריץ את זה צריך אינטרנט רגיל (לא עובד בסביבות עם חסימת רשת, כמו
   sandbox מבודד). מריצים על המחשב האישי, בקולאב (Google Colab), או בכל
   שרת עם גישה רגילה לאינטרנט.

התקנה:
------
    pip install yfinance pandas requests lxml

הרצה לדוגמה:
-------------
    # סריקת רשימת טיקרים ספציפית:
    python3 graham_screener.py --tickers AAPL,KO,JNJ,PG,XOM,IBM,MMM

    # סריקת כל מדד ה-S&P 500 (נמשך כמה דקות, יש הגבלת קצב מובנית):
    python3 graham_screener.py --universe sp500

    # סינון לפי הקריטריונים של המשקיע היוזם (פרק 15) במקום המגן (פרק 14):
    python3 graham_screener.py --tickers AAPL,KO,JNJ --mode enterprising

    # שינוי סף "גודל מספיק" (בדולרים, ברירת מחדל 1,500,000,000):
    python3 graham_screener.py --universe sp500 --min-revenue 500000000

הפלט:
-----
    קובץ CSV (ברירת מחדל: graham_results.csv) עם שורה לכל מניה, עמודת
    PASS/FAIL לכל קריטריון, וציון כולל (כמה קריטריונים עברה מתוך 7 או 5),
    ממוין מהגבוה לנמוך. גם הדפסה למסך של רשימת המניות שעברו הכי הרבה
    קריטריונים.
"""

import argparse
import sys
import time
import warnings
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

warnings.filterwarnings("ignore")

# ---------------------------------------------------------------------------
# ברירות מחדל של הקריטריונים (ניתנות לשינוי דרך שורת הפקודה או בקוד)
# ---------------------------------------------------------------------------

MIN_REVENUE_INDUSTRIAL = 1_500_000_000   # מותאם-אינפלציה ל-$100M של גראהם ב-1970
MIN_ASSETS_UTILITY = 750_000_000         # מותאם-אינפלציה ל-$50M של גראהם ב-1970
MIN_CURRENT_RATIO = 2.0                  # יחס שוטף מינימלי (חברות תעשייה)
MAX_DIVIDEND_GAP_YEARS_DEFENSIVE = 20    # שנות דיבידנד רצוף נדרשות (משקיע מגן)
MAX_DIVIDEND_GAP_YEARS_ENTERPRISING = 0  # מספיק דיבידנד כלשהו כרגע (משקיע יוזם)
MIN_EPS_GROWTH_DEFENSIVE = 1 / 3         # צמיחת רווח למניה נדרשת על פני התקופה
MAX_PE_DEFENSIVE = 15.0
MAX_PB_DEFENSIVE = 1.5
MAX_PE_TIMES_PB = 22.5                   # כלל האצבע המשולב של גראהם
MAX_PRICE_TO_NET_TANGIBLE_ENTERPRISING = 1.20  # 120% מהנכסים המוחשיים

# --- ספי המסך המודרני ---
# שלושת הקריטריונים המאזניים של גראהם (מכפיל הון, כלל 22.5, ומספר גראהם
# שנגזר מהם) הוחלפו במדדים שאינם מניחים שהמאזן משקף את העסק. הרקע:
# חלק גדול מהערך התאגידי היום יושב בתוכנה, במחקר ובמותג, שאינם רשומים
# כנכסים. ראו Mauboussin, "Intangibles and Modern Value Investing" (2025).
MIN_EBIT_EV = 0.10            # רווח תפעולי של 10% משווי הפעילות, כלומר EV/EBIT עד 10
MIN_GROSS_PROFITABILITY = 0.20  # רווח גולמי של 20% מסך הנכסים (Novy-Marx 2013)
MIN_NET_PAYOUT_YIELD = 0.02   # החזר הון של 2%, מחליף את מבחן 20 שנות הדיבידנד
MAX_NET_DEBT_TO_EBITDA = 3.0  # מחליף את היחס השוטף כמבחן איתנות

# --- מסנני היגיינה ---
MIN_DOLLAR_VOLUME = 2_000_000    # מחזור יומי ממוצע בדולרים
EARNINGS_BLACKOUT_DAYS = 5       # אין כניסה כשהדוח מעבר לפינה
MAX_ACCRUALS = 0.10              # צבירות מעל 10% מהנכסים: איכות רווח ירודה

# רשימת גיבוי אם אי אפשר למשוך את רשימת ה-S&P 500 מוויקיפדיה (למשל: אין רשת)
FALLBACK_LARGE_CAPS = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "KO", "PEP", "JNJ", "PG", "XOM", "CVX",
    "IBM", "MMM", "CAT", "HD", "MCD", "WMT", "JPM", "V", "MA", "UNH",
    "DIS", "VZ", "T", "CSCO", "INTC", "MRK", "PFE", "ABT", "COST", "NKE",
]


@dataclass
class ScreenResult:
    ticker: str
    name: str = ""
    sector: str = ""
    error: Optional[str] = None
    n_years_used: int = 0
    price: Optional[float] = None
    revenue: Optional[float] = None
    total_assets: Optional[float] = None
    current_ratio: Optional[float] = None
    total_debt: Optional[float] = None
    net_current_assets: Optional[float] = None
    dividend_years_streak: Optional[int] = None
    eps_growth: Optional[float] = None
    pe: Optional[float] = None
    pb: Optional[float] = None
    checks: dict = field(default_factory=dict)  # criterion_name -> bool
    score: int = 0
    max_score: int = 0
    company_type: str = "industrial"            # industrial / utility / financial
    na_criteria: list = field(default_factory=list)  # קריטריונים שלא חלים על סוג החברה
    fscore: Optional[int] = None                # Piotroski F-Score (0-9)
    fscore_checks: dict = field(default_factory=dict)
    eps_ttm: Optional[float] = None
    book_value_per_share: Optional[float] = None
    graham_number: Optional[float] = None       # אומדן שווי: שורש(22.5 * רווח למניה * הון למניה)
    margin_of_safety: Optional[float] = None    # הפער באחוזים בין המחיר לאומדן השווי
    checks_ent: dict = field(default_factory=dict)   # קריטריוני המשקיע היוזם (פרק 15)
    score_ent: int = 0
    max_score_ent: int = 0

    # --- המסך המודרני: מדדים שאינם תלויים במאזן ---
    enterprise_value: Optional[float] = None
    ebit: Optional[float] = None
    ebit_ev: Optional[float] = None              # תשואת הרווח התפעולי על שווי הפעילות
    gross_profitability: Optional[float] = None  # רווח גולמי חלקי סך הנכסים (Novy-Marx)
    net_payout_yield: Optional[float] = None     # דיבידנד ורכישות עצמיות, חלקי שווי השוק
    net_debt_to_ebitda: Optional[float] = None
    measurable: bool = True                      # האם המסך בכלל חל על החברה

    # --- איכות הנתונים ---
    data_source: str = "yahoo"                   # yahoo / sec / mixed
    sec_fields: int = 0                          # כמה שדות הגיעו מדוחות ה-SEC
    accruals: Optional[float] = None             # צבירות מנורמלות (Sloan 1996)
    dollar_volume: Optional[float] = None        # מחזור יומי ממוצע בדולרים
    next_earnings: Optional[str] = None          # תאריך הדוח הקרוב, אם ידוע
    share_class_of: Optional[str] = None         # הסימול הראשי, כשיש כמה סדרות

    def as_row(self) -> dict:
        row = {
            "ticker": self.ticker,
            "name": self.name,
            "sector": self.sector,
            "error": self.error,
            "n_years_used": self.n_years_used,
            "price": self.price,
            "revenue": self.revenue,
            "total_assets": self.total_assets,
            "current_ratio": self.current_ratio,
            "total_debt": self.total_debt,
            "dividend_years_streak": self.dividend_years_streak,
            "eps_growth_over_period": self.eps_growth,
            "P/E": self.pe,
            "P/B": self.pb,
            "score": self.score,
            "max_score": self.max_score,
            "company_type": self.company_type,
            "na_criteria": ";".join(self.na_criteria),
            "fscore": self.fscore,
            "eps_ttm": self.eps_ttm,
            "book_value_per_share": self.book_value_per_share,
            "graham_number": self.graham_number,
            "margin_of_safety": self.margin_of_safety,
        }
        row["score_ent"] = self.score_ent
        row["max_score_ent"] = self.max_score_ent
        row["enterprise_value"] = self.enterprise_value
        row["ebit"] = self.ebit
        row["ebit_ev"] = self.ebit_ev
        row["gross_profitability"] = self.gross_profitability
        row["net_payout_yield"] = self.net_payout_yield
        row["net_debt_to_ebitda"] = self.net_debt_to_ebitda
        row["measurable"] = self.measurable
        row["data_source"] = self.data_source
        row["sec_fields"] = self.sec_fields
        row["accruals"] = self.accruals
        row["dollar_volume"] = self.dollar_volume
        row["next_earnings"] = self.next_earnings
        row["share_class_of"] = self.share_class_of
        row.update({f"crit_{k}": v for k, v in self.checks.items()})
        row.update({f"ent_{k}": v for k, v in self.checks_ent.items()})
        row.update({f"f_{k}": v for k, v in self.fscore_checks.items()})
        return row


SP500_CSV_URL = (
    "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/"
    "main/data/constituents.csv"
)


BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")

WIKI_LISTS = {
    "sp400": "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies",
    "sp600": "https://en.wikipedia.org/wiki/List_of_S%26P_600_companies",
    "sp500": "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
}


def _clean_symbols(series) -> list:
    """מנרמל טיקרים לפורמט שמקור הנתונים מבין (BRK.B הופך ל-BRK-B)."""
    out = []
    for s in series.astype(str):
        s = s.strip().upper().replace(".", "-")
        if s and s != "NAN" and len(s) <= 8:
            out.append(s)
    return out


def _wikipedia_symbols(url: str) -> list:
    """מושך טבלת מרכיבים מוויקיפדיה. חובה להזדהות כדפדפן, אחרת מתקבל 403."""
    import io
    import requests

    resp = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=30)
    resp.raise_for_status()
    for table in pd.read_html(io.StringIO(resp.text)):
        for col in ("Symbol", "Ticker symbol", "Ticker"):
            if col in table.columns:
                syms = _clean_symbols(table[col])
                if len(syms) > 50:
                    return syms
    raise ValueError("לא נמצאה טבלת מרכיבים בדף")


def get_sp500_tickers() -> list:
    """מושך את רשימת מרכיבי ה-S&P 500. מנסה קודם CSV יציב מגיטהאב, אחר כך ויקיפדיה."""
    try:
        df = pd.read_csv(SP500_CSV_URL)
        tickers = _clean_symbols(df["Symbol"])
        print(f"[info] נמשכו {len(tickers)} טיקרים מה-S&P 500 (מקור: datasets/s-and-p-500-companies).")
        return tickers
    except Exception as e:
        print(f"[warn] נכשלה משיכה מגיטהאב ({e}). מנסה ויקיפדיה...")

    try:
        tickers = _wikipedia_symbols(WIKI_LISTS["sp500"])
        print(f"[info] נמשכו {len(tickers)} טיקרים מה-S&P 500 (ויקיפדיה).")
        return tickers
    except Exception as e:
        print(f"[warn] נכשלה משיכת רשימת S&P 500 ({e}). משתמש ברשימת גיבוי מצומצמת.")
        return FALLBACK_LARGE_CAPS


def get_sp1500_tickers() -> list:
    """
    רשימת ה-S&P Composite 1500: 500 הגדולות, 400 הבינוניות ו-600 הקטנות.

    שני המדדים הנוספים דורשים רווחיות לצורך הכללה, ולכן הם מסננים מראש חלק
    ניכר מהחברות הבעייתיות - בשונה ממדדים רחבים כמו ראסל 2000. אם אחת
    הרשימות אינה זמינה, הסריקה ממשיכה עם מה שכן נמשך.
    """
    tickers = list(get_sp500_tickers())
    for key, label in (("sp400", "S&P MidCap 400"), ("sp600", "S&P SmallCap 600")):
        try:
            extra = _wikipedia_symbols(WIKI_LISTS[key])
            print(f"[info] נמשכו {len(extra)} טיקרים מה-{label}.")
            tickers.extend(extra)
        except Exception as e:
            print(f"[warn] נכשלה משיכת {label} ({e}). ממשיך בלעדיו.")

    seen, uniq = set(), []
    for t in tickers:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    print(f"[info] סך הכול {len(uniq)} טיקרים ייחודיים ביקום הסריקה.")
    return uniq


def _consecutive_dividend_years(dividends: pd.Series) -> int:
    """סופר כמה שנים רצופות (עד השנה הנוכחית, כולל) שולם דיבידנד לפחות פעם אחת."""
    if dividends is None or dividends.empty:
        return 0
    years_with_div = set(dividends.index.year)
    from datetime import datetime
    current_year = datetime.now().year
    streak = 0
    year = current_year
    # אם עדיין לא שולם דיבידנד השנה, מתחילים לספור מהשנה שעברה
    if year not in years_with_div:
        year -= 1
    while year in years_with_div:
        streak += 1
        year -= 1
    return streak


FINANCIAL_SECTORS = ("Financial", "Real Estate")


def classify_company(sector: str) -> str:
    """
    מסווג את החברה לסוג שקובע אילו קריטריונים של גראהם רלוונטיים לה.

    גראהם ניסח את שבעת הקריטריונים עבור חברות תעשייתיות, עם כללים מותאמים
    לחברות תשתית. למאזן של בנק, חברת ביטוח או קרן ריט אין חלוקה משמעותית
    בין נכסים שוטפים להתחייבויות שוטפות, ולכן מבחן היחס השוטף פשוט לא חל
    עליהן - לא "נכשל", אלא לא רלוונטי.
    """
    s = sector or ""
    if "Utilit" in s:
        return "utility"
    if any(k in s for k in FINANCIAL_SECTORS):
        return "financial"
    return "industrial"


def _series_val(df, names, idx=0):
    """שולף ערך משורה בדוח לפי רשימת שמות אפשריים (yfinance משנה שמות בין גרסאות)."""
    if df is None or getattr(df, "empty", True):
        return None
    for n in names:
        if n in df.index:
            try:
                v = df.loc[n].iloc[idx]
            except (IndexError, KeyError):
                continue
            if pd.notna(v):
                return float(v)
    return None


def piotroski_fscore(balance, income, cashflow) -> tuple:
    """
    מחשב את ציון פיוטרוסקי (F-Score): תשעה מבחנים בינאריים שבודקים אם
    המצב הפיננסי של החברה השתפר או הידרדר בשנה האחרונה.

    מחזיר (score, checks) - כאשר checks הוא מילון של שם מבחן -> True/False/None.
    None פירושו שלא היו מספיק נתונים כדי להכריע, והמבחן נספר ככישלון
    (כמו בגישה השמרנית המקורית).
    """
    c = {}

    def g(df, names, idx=0):
        return _series_val(df, names, idx)

    # --- נתוני בסיס לשתי השנים האחרונות ---
    ni_0 = g(income, ["Net Income", "Net Income Common Stockholders"], 0)
    ni_1 = g(income, ["Net Income", "Net Income Common Stockholders"], 1)
    ta_0 = g(balance, ["Total Assets"], 0)
    ta_1 = g(balance, ["Total Assets"], 1)
    cfo_0 = g(cashflow, ["Operating Cash Flow", "Total Cash From Operating Activities"], 0)
    ltd_0 = g(balance, ["Long Term Debt", "Long Term Debt And Capital Lease Obligation"], 0)
    ltd_1 = g(balance, ["Long Term Debt", "Long Term Debt And Capital Lease Obligation"], 1)
    ca_0 = g(balance, ["Total Current Assets", "Current Assets"], 0)
    ca_1 = g(balance, ["Total Current Assets", "Current Assets"], 1)
    cl_0 = g(balance, ["Total Current Liabilities", "Current Liabilities"], 0)
    cl_1 = g(balance, ["Total Current Liabilities", "Current Liabilities"], 1)
    sh_0 = g(balance, ["Ordinary Shares Number", "Share Issued", "Common Stock"], 0)
    sh_1 = g(balance, ["Ordinary Shares Number", "Share Issued", "Common Stock"], 1)
    rev_0 = g(income, ["Total Revenue", "Operating Revenue"], 0)
    rev_1 = g(income, ["Total Revenue", "Operating Revenue"], 1)
    gp_0 = g(income, ["Gross Profit"], 0)
    gp_1 = g(income, ["Gross Profit"], 1)

    roa_0 = (ni_0 / ta_0) if (ni_0 is not None and ta_0) else None
    roa_1 = (ni_1 / ta_1) if (ni_1 is not None and ta_1) else None

    # --- רווחיות (4 מבחנים) ---
    c["1_roa_positive"] = (roa_0 > 0) if roa_0 is not None else None
    c["2_cfo_positive"] = (cfo_0 > 0) if cfo_0 is not None else None
    c["3_roa_improving"] = (roa_0 > roa_1) if (roa_0 is not None and roa_1 is not None) else None
    c["4_cfo_above_income"] = (cfo_0 > ni_0) if (cfo_0 is not None and ni_0 is not None) else None

    # --- מינוף, נזילות ומקורות מימון (3 מבחנים) ---
    lev_0 = (ltd_0 / ta_0) if (ltd_0 is not None and ta_0) else None
    lev_1 = (ltd_1 / ta_1) if (ltd_1 is not None and ta_1) else None
    c["5_leverage_down"] = (lev_0 <= lev_1) if (lev_0 is not None and lev_1 is not None) else None

    cr_0 = (ca_0 / cl_0) if (ca_0 is not None and cl_0) else None
    cr_1 = (ca_1 / cl_1) if (ca_1 is not None and cl_1) else None
    c["6_current_ratio_up"] = (cr_0 > cr_1) if (cr_0 is not None and cr_1 is not None) else None

    c["7_no_new_shares"] = (sh_0 <= sh_1) if (sh_0 is not None and sh_1 is not None) else None

    # --- יעילות תפעולית (2 מבחנים) ---
    gm_0 = (gp_0 / rev_0) if (gp_0 is not None and rev_0) else None
    gm_1 = (gp_1 / rev_1) if (gp_1 is not None and rev_1) else None
    c["8_gross_margin_up"] = (gm_0 > gm_1) if (gm_0 is not None and gm_1 is not None) else None

    at_0 = (rev_0 / ta_0) if (rev_0 is not None and ta_0) else None
    at_1 = (rev_1 / ta_1) if (rev_1 is not None and ta_1) else None
    c["9_asset_turnover_up"] = (at_0 > at_1) if (at_0 is not None and at_1 is not None) else None

    score = sum(1 for v in c.values() if v is True)
    return score, c


def graham_number(eps: Optional[float], bvps: Optional[float]) -> Optional[float]:
    """
    "מספר גראהם" - אומדן השווי המרבי שגראהם היה מוכן לשלם עבור מניה.

    הנוסחה נגזרת ישירות משני הקריטריונים של פרק 14: מכפיל רווח של עד 15
    ומכפיל הון של עד 1.5. מכפלתם היא 22.5, ומכאן:

        שווי = שורש ריבועי של (22.5 × רווח למניה × הון עצמי למניה)

    הנוסחה מוגדרת רק כששני הנתונים חיוביים. לחברה עם הון עצמי שלילי
    (למשל בעקבות רכישות עצמיות מסיביות) או עם הפסד - אין לה משמעות.
    """
    if eps is None or bvps is None:
        return None
    if eps <= 0 or bvps <= 0:
        return None
    return (22.5 * eps * bvps) ** 0.5


def _sane_ratio(v, lo, hi):
    """מסנן מכפילים מופרכים שמגיעים מדי פעם ממקור הנתונים."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if lo <= v <= hi else None


# ---------------------------------------------------------------------------
# שכבת נתוני ה-SEC
# ---------------------------------------------------------------------------
# מילון שממולא פעם אחת בתחילת הריצה: סימול -> שורת נתונים מדוחות ה-SEC.
# כשהוא ריק, הסורק עובד בדיוק כמו קודם על נתוני yahoo בלבד.

SEC_DATA: dict = {}


def load_sec_data(tickers, quiet: bool = False) -> dict:
    """מושך תמונת מצב מדוחות ה-SEC לכל היקום, בבקשה אחת לכל מושג חשבונאי."""
    global SEC_DATA
    try:
        import sec_frames
    except ImportError:
        return {}
    try:
        if not quiet:
            print("מושך נתונים מדוחות ה-SEC...", flush=True)
        df = sec_frames.snapshot_for(tickers, progress=not quiet)
    except Exception as exc:  # noqa: BLE001
        if not quiet:
            print(f"  שכבת ה-SEC לא זמינה ({type(exc).__name__}), ממשיכים עם yahoo", flush=True)
        return {}
    SEC_DATA = {
        sym: {k: v for k, v in row.items() if v == v and v is not None}
        for sym, row in df.to_dict(orient="index").items()
    }
    if not quiet:
        if SEC_DATA:
            print(f"  התקבלו נתונים ל-{len(SEC_DATA)} מתוך {len(tickers)} מניות\n", flush=True)
        else:
            print("  שכבת ה-SEC לא החזירה דבר. הסריקה תרוץ על yahoo בלבד,\n"
                  "  והבדיקה לאחור לא תהיה נקייה מהצצה קדימה.\n", flush=True)
    return SEC_DATA


def _sum(row: dict, *fields) -> Optional[float]:
    """סכום של שדות, ובלבד שלפחות אחד מהם קיים. חסר נספר כאפס."""
    vals = [row.get(f) for f in fields]
    present = [v for v in vals if v is not None]
    if not present:
        return None
    return float(sum(present))


def apply_sec(res: ScreenResult, market_cap: Optional[float]) -> None:
    """דורס את נתוני yahoo בנתוני ה-SEC, בכל שדה שקיים שם.

    ה-SEC היא המקור הרשמי: אלה המספרים שהחברה הגישה, בתגיות תקניות, בלי
    שכבת הפרשנות של ספק נתונים חינמי. איפה שאין — נשאר מה שהיה.
    """
    row = SEC_DATA.get(res.ticker.strip().upper())
    if not row:
        return

    used = 0

    def take(field):
        nonlocal used
        val = row.get(field)
        if val is None:
            return None
        used += 1
        return float(val)

    revenue = take("revenue")
    assets = take("assets")
    ebit = take("ebit")
    gross = take("gross_profit")
    cogs = take("cogs")
    cash = take("cash")
    sti = take("short_term_investments")
    depreciation = take("depreciation")
    cfo = take("cfo")
    net_income = take("net_income")
    equity = take("equity")
    ca = take("assets_current")
    cl = take("liabilities_current")
    eps = take("eps_diluted") or take("eps_basic")
    shares = take("shares_outstanding")

    debt = _sum(row, "debt_long", "debt_short")
    liquid = (cash or 0.0) + (sti or 0.0)

    if revenue is not None:
        res.revenue = revenue
    if assets is not None:
        res.total_assets = assets
    if ebit is not None:
        res.ebit = ebit
    if debt is not None:
        res.total_debt = debt
    if ca is not None and cl:
        res.current_ratio = ca / cl
        res.net_current_assets = ca - cl
    if eps is not None:
        res.eps_ttm = eps
    if equity is not None and shares:
        res.book_value_per_share = equity / shares

    # שווי פעילות: שווי שוק ועוד חוב נטו. עדיף על השדה של yahoo, שלעתים
    # קרובות חסר או מעודכן לפי מאזן ישן.
    if market_cap and debt is not None:
        res.enterprise_value = market_cap + debt - liquid
    if res.ebit and res.enterprise_value and res.enterprise_value > 0:
        res.ebit_ev = res.ebit / res.enterprise_value

    # רווח גולמי: מדווח אם יש, אחרת הכנסות פחות עלות המכר
    if gross is None and revenue is not None and cogs is not None:
        gross = revenue - cogs
    if gross is not None and res.total_assets:
        res.gross_profitability = gross / res.total_assets

    # החזר הון נטו. בתגיות ה-SEC כל השלושה מדווחים כמספרים חיוביים.
    payout = _sum(row, "dividends_paid", "buybacks")
    if payout is not None and market_cap:
        issued = row.get("stock_issued") or 0.0
        res.net_payout_yield = (payout - float(issued)) / market_cap

    # חוב נטו חלקי רווח תפעולי בתוספת פחת
    if ebit is not None and depreciation is not None and debt is not None:
        ebitda = ebit + depreciation
        if ebitda > 0:
            res.net_debt_to_ebitda = (debt - liquid) / ebitda

    # צבירות לפי Hribar & Collins (2002): הפער בין הרווח החשבונאי לתזרים
    # התפעולי, חלקי סך הנכסים. גרסת תזרים המזומנים מדויקת יותר מהגרסה
    # המאזנית המקורית של סלואן, מפני שהיא חסינה לרכישות ולמכירת פעילות.
    if net_income is not None and cfo is not None and res.total_assets:
        res.accruals = (net_income - cfo) / res.total_assets

    res.sec_fields = used
    res.data_source = "sec" if used >= 8 else "mixed"


def screen_ticker(ticker: str, mode: str = "defensive",
                   min_revenue: float = MIN_REVENUE_INDUSTRIAL,
                   min_assets_utility: float = MIN_ASSETS_UTILITY) -> ScreenResult:
    import yfinance as yf

    res = ScreenResult(ticker=ticker)
    try:
        t = yf.Ticker(ticker)
        info = t.info or {}
        res.name = info.get("shortName") or info.get("longName") or ticker
        res.sector = info.get("sector") or ""
        res.company_type = classify_company(res.sector)
        is_utility = res.company_type == "utility"
        is_financial = res.company_type == "financial"

        res.price = info.get("currentPrice") or info.get("regularMarketPrice")
        res.revenue = info.get("totalRevenue")
        res.total_assets = None  # ממולא מהמאזן אם זמין
        res.current_ratio = info.get("currentRatio")
        res.total_debt = info.get("totalDebt")
        res.pe = info.get("trailingPE")
        res.pb = info.get("priceToBook")

        # --- מאזן: נכסים שוטפים, התחייבויות שוטפות, סך נכסים ---
        bs = None
        try:
            bs = t.balance_sheet  # annual, בד"כ 4 שנים אחרונות
            if bs is not None and not bs.empty:
                def _row(names):
                    for n in names:
                        if n in bs.index:
                            return bs.loc[n]
                    return None

                curr_assets = _row(["Total Current Assets", "Current Assets"])
                curr_liab = _row(["Total Current Liabilities", "Current Liabilities"])
                total_assets_row = _row(["Total Assets"])
                if total_assets_row is not None:
                    res.total_assets = float(total_assets_row.iloc[0])
                if curr_assets is not None and curr_liab is not None:
                    ca = float(curr_assets.iloc[0])
                    cl = float(curr_liab.iloc[0])
                    if cl:
                        res.current_ratio = res.current_ratio or (ca / cl)
                    res.net_current_assets = ca - cl
        except Exception:
            pass

        # --- רווח למניה לאורך שנים (יציבות + צמיחה) ---
        eps_by_year = {}
        fin = None
        try:
            fin = t.income_stmt  # annual income statement
            shares = info.get("sharesOutstanding")
            if fin is not None and not fin.empty:
                net_income_row = None
                for n in ["Net Income", "Net Income Common Stockholders"]:
                    if n in fin.index:
                        net_income_row = fin.loc[n]
                        break
                if net_income_row is not None:
                    for col, val in net_income_row.items():
                        year = col.year if hasattr(col, "year") else str(col)[:4]
                        if pd.notna(val):
                            eps_by_year[year] = float(val) / shares if shares else float(val)
        except Exception:
            pass
        res.n_years_used = len(eps_by_year)

        # --- דיבידנדים ---
        try:
            divs = t.dividends
            res.dividend_years_streak = _consecutive_dividend_years(divs)
        except Exception:
            res.dividend_years_streak = None

        # --- מספר גראהם ומרווח הביטחון (פרק 20) ---
        # מעדיפים לגזור את הרווח וההון למניה מהמחיר ומהמכפילים, ולא לקחת את
        # השדות הגולמיים. הסיבה: בחברות עם שתי סדרות מניות (למשל BRK-A מול
        # BRK-B) מקור הנתונים מחזיר לפעמים רווח למניה של סדרה אחת לצד הון
        # למניה של השנייה, ומכאן יוצא אומדן שווי מופרך. המכפילים, לעומת זאת,
        # מחושבים תמיד מול אותו מחיר ולכן עקביים בתוך עצמם.
        pe_ok = _sane_ratio(res.pe, 0.5, 1000)
        pb_ok = _sane_ratio(res.pb, 0.05, 200)
        if pe_ok and res.price:
            res.eps_ttm = res.price / pe_ok
        else:
            res.eps_ttm = info.get("trailingEps")
        if pb_ok and res.price:
            res.book_value_per_share = res.price / pb_ok
        else:
            res.book_value_per_share = info.get("bookValue")
        res.graham_number = graham_number(res.eps_ttm, res.book_value_per_share)

        # שסתום ביטחון על הנתונים עצמם. ראינו מקרה אמיתי שבו מקור הנתונים
        # דיווח מכפיל הון של 0.001 עבור ברקשייר האת'וויי, ומכאן יצא אומדן שווי
        # של 21,614 דולר למניה שנסחרת ב-502 - "מרווח ביטחון" של 98% שהוא
        # שגיאת נתונים ולא מציאה. אומדן שגבוה פי חמישה מהמחיר בקרב חברות
        # גדולות ובינוניות הוא כמעט תמיד תקלה, ועדיף להציג ריק מאשר מספר שקרי.
        if res.graham_number and res.price and res.graham_number > 5 * res.price:
            res.graham_number = None

        if res.graham_number and res.price:
            res.margin_of_safety = (res.graham_number - res.price) / res.graham_number

        # --- ציון פיוטרוסקי (F-Score): שיפור או הידרדרות בשנה האחרונה ---
        cf = None
        try:
            cf = t.cashflow
            res.fscore, res.fscore_checks = piotroski_fscore(bs, fin, cf)
        except Exception:
            res.fscore, res.fscore_checks = None, {}

        # --- נתוני המסך המודרני ---
        res.enterprise_value = info.get("enterpriseValue")
        market_cap = info.get("marketCap")

        def _fin_row(names, idx=0):
            return _series_val(fin, names, idx)

        res.ebit = _fin_row(["EBIT", "Operating Income", "Total Operating Income As Reported"])
        if res.ebit and res.enterprise_value and res.enterprise_value > 0:
            res.ebit_ev = res.ebit / res.enterprise_value

        gp = _fin_row(["Gross Profit"])
        if gp is None:
            rev_row = _fin_row(["Total Revenue", "Operating Revenue"])
            cogs_row = _fin_row(["Cost Of Revenue", "Cost Of Goods Sold"])
            gp = (rev_row - cogs_row) if (rev_row is not None and cogs_row is not None) else None
        if gp is not None and res.total_assets:
            res.gross_profitability = gp / res.total_assets

        # החזר הון נטו: דיבידנד ורכישות עצמיות פחות הנפקות. מחליף את מבחן
        # 20 שנות הדיבידנד, שהתיישן מפני שרכישות עצמיות החליפו את הדיבידנד
        # כדרך העיקרית שבה חברות אמריקאיות מחזירות מזומן לבעלי המניות.
        try:
            cf_div = _series_val(cf, ["Cash Dividends Paid", "Common Stock Dividend Paid"]) or 0.0
            cf_buy = _series_val(cf, ["Repurchase Of Capital Stock", "Repurchase Of Common Stock"]) or 0.0
            cf_iss = _series_val(cf, ["Issuance Of Capital Stock", "Common Stock Issuance"]) or 0.0
            if market_cap:
                returned = abs(cf_div) + abs(min(cf_buy, 0.0)) - max(cf_iss, 0.0)
                res.net_payout_yield = returned / market_cap
        except Exception:
            pass

        ebitda = info.get("ebitda")
        cash = _series_val(bs, ["Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"])
        if ebitda and ebitda > 0 and res.total_debt is not None:
            res.net_debt_to_ebitda = (res.total_debt - (cash or 0.0)) / ebitda

        # --- החלפת נתוני yahoo בנתוני ה-SEC, איפה שיש ---
        try:
            apply_sec(res, market_cap)
        except Exception:
            pass

        # --- נזילות: מחזור יומי ממוצע בדולרים ---
        # מניה שנסחרת דליל היא גם זו שהנתונים עליה הכי גרועים, וגם זו שבה
        # פער הקנייה-מכירה אוכל את התשואה. רצפה כאן חוסכת בעיות בהמשך.
        try:
            vol = info.get("averageDailyVolume3Month") or info.get("averageVolume")
            if vol and res.price:
                res.dollar_volume = float(vol) * float(res.price)
        except Exception:
            pass

        # --- מועד הדוח הקרוב ---
        try:
            cal = getattr(t, "calendar", None)
            if isinstance(cal, dict):
                dates = cal.get("Earnings Date") or []
                if dates:
                    res.next_earnings = str(dates[0])[:10]
            elif cal is not None and hasattr(cal, "loc") and "Earnings Date" in getattr(cal, "index", []):
                res.next_earnings = str(cal.loc["Earnings Date"].iloc[0])[:10]
        except Exception:
            pass

        # ================= המסך המודרני (מחליף את פרק 14) =================
        # שלושת המדדים המאזניים של גראהם הוחלפו. הפילוסופיה נשארה: קודם
        # בטיחות, אחר כך זול. רק כלי המדידה השתנו, מפני שמכפיל ההון מניח
        # שהמאזן משקף את העסק - הנחה שנשברת בחברות שהערך שלהן בתוכנה,
        # במחקר ובמותג.
        #
        # חברה שלא ניתן לחשב עבורה את המדדים האלה אינה מקבלת פטור אלא
        # נכשלת, ומסומנת כלא-ניתנת-למדידה. זו בחירה מודעת: המסך הזה מודד
        # עסקים תפעוליים, ובנקים וחברות ביטוח אינם כאלה. עדיף להצהיר על כך
        # מאשר לדרג אותם על סמך חלק מהמבחנים ולהעמיד פנים שזו אותה אמת מידה.
        if True:
            checks = {}

            # 1. גודל מספיק - ללא שינוי
            if is_utility or is_financial:
                checks["1_adequate_size"] = bool(res.total_assets and res.total_assets >= min_assets_utility)
            else:
                checks["1_adequate_size"] = bool(res.revenue and res.revenue >= min_revenue)

            # 2. יציבות רווחים - ללא שינוי. אין הפסד באף שנה שיש עליה נתונים.
            if eps_by_year:
                checks["2_earnings_stability"] = all(v > 0 for v in eps_by_year.values())
            else:
                checks["2_earnings_stability"] = False

            # 3. איתנות פיננסית - חוב נטו מול EBITDA, במקום היחס השוטף.
            # היחס השוטף נזרק מפני שהוא מבוסס על חלוקת המאזן לשוטף ולא-שוטף,
            # חלוקה שאין לה משמעות בחלק גדול מהחברות היום.
            checks["3_financial_strength"] = bool(
                res.net_debt_to_ebitda is not None
                and res.net_debt_to_ebitda <= MAX_NET_DEBT_TO_EBITDA
            )

            # 4. החזר הון לבעלי המניות - מחליף את 20 שנות הדיבידנד.
            checks["4_returns_capital"] = bool(
                res.net_payout_yield is not None
                and res.net_payout_yield >= MIN_NET_PAYOUT_YIELD
            )

            # 5. זול - רווח תפעולי מול שווי הפעילות. מחליף גם את מכפיל הרווח
            # וגם את מכפיל ההון. גריי וקרלייל מצאו את היחס הזה כחזק שבמדדי
            # הזול, והוא מודד את כל העסק כולל החוב, לא רק את ההון העצמי.
            checks["5_cheap_ebit_ev"] = bool(res.ebit_ev is not None and res.ebit_ev >= MIN_EBIT_EV)

            # 6. איכות - רווח גולמי חלקי סך הנכסים (Novy-Marx 2013). כוח ניבוי
            # דומה למכפיל ההון, וכמעט בלתי מתואם איתו.
            checks["6_gross_profitability"] = bool(
                res.gross_profitability is not None
                and res.gross_profitability >= MIN_GROSS_PROFITABILITY
            )

            res.checks = checks
            res.max_score = len(checks)
            res.score = sum(1 for v in checks.values() if v is True)

            # שני המבחנים שדורשים מבנה של חברה תפעולית. בלעדיהם אין למסך
            # הזה מה לומר על החברה, וזה מסומן במפורש ולא מוסתר.
            res.measurable = res.ebit_ev is not None and res.gross_profitability is not None

        # =================== קריטריונים: משקיע יוזם (פרק 15, מקוצר) ===================
        if True:
            checks = {}
            cond_ratio = bool(res.current_ratio and res.current_ratio >= 1.5)
            cond_debt = True
            if res.total_debt is not None and res.net_current_assets is not None and res.net_current_assets:
                cond_debt = res.total_debt <= 1.10 * res.net_current_assets
            checks["1_financial_condition"] = cond_ratio and cond_debt

            if eps_by_year:
                checks["2_earnings_stability_5y"] = all(v > 0 for v in eps_by_year.values())
            else:
                checks["2_earnings_stability_5y"] = False

            checks["3_pays_dividend_now"] = bool(res.dividend_years_streak and res.dividend_years_streak >= 1)

            if len(eps_by_year) >= 2:
                years_sorted = sorted(eps_by_year.keys())
                checks["4_earnings_higher_than_past"] = eps_by_year[years_sorted[-1]] > eps_by_year[years_sorted[0]]
            else:
                checks["4_earnings_higher_than_past"] = False

            cond_price = bool(res.pb and res.pb <= MAX_PRICE_TO_NET_TANGIBLE_ENTERPRISING)
            checks["5_price_under_120pct_net_assets"] = cond_price

            res.checks_ent = checks
            res.max_score_ent = len(checks)
            res.score_ent = sum(1 for v in checks.values() if v)

    except Exception as e:
        res.error = str(e)

    return res


def mark_share_classes(df: pd.DataFrame) -> pd.DataFrame:
    """מסמן סדרות מניות משניות של אותו מנפיק.

    GOOG ו-GOOGL הן אותה חברה, ואם שתיהן עוברות את המסך הרשימה מציגה אותה
    הזדמנות פעמיים ומי שיקנה את שתיהן יחשוב שפיזר. מספר ה-CIK של ה-SEC מזהה
    מנפיק, ולכן הוא הדרך הנקייה לזהות את זה. הסדרה הנזילה ביותר נחשבת
    הראשית, והשאר מסומנות — לא נמחקות, כדי שאפשר יהיה לראות מה קרה.
    """
    if df.empty or "ticker" not in df.columns:
        return df
    try:
        from sec_facts import ticker_to_cik
        mapping = ticker_to_cik()
    except Exception:  # noqa: BLE001
        return df
    if not mapping:
        return df

    df = df.copy()
    if "share_class_of" not in df.columns:
        df["share_class_of"] = None

    groups: dict = {}
    for idx, tk in df["ticker"].items():
        cik = mapping.get(str(tk).strip().upper())
        if cik is not None:
            groups.setdefault(int(cik), []).append(idx)

    for members in groups.values():
        if len(members) < 2:
            continue

        def liquidity(i):
            v = df.at[i, "dollar_volume"] if "dollar_volume" in df.columns else None
            return float(v) if v == v and v is not None else -1.0

        primary = max(members, key=lambda i: (liquidity(i), -len(str(df.at[i, "ticker"]))))
        for i in members:
            if i != primary:
                df.at[i, "share_class_of"] = df.at[primary, "ticker"]
    return df


def main():
    parser = argparse.ArgumentParser(description="סורק מניות לפי הקריטריונים של בנג'מין גראהם")
    parser.add_argument("--tickers", type=str, default=None,
                         help="רשימת טיקרים מופרדת בפסיקים, למשל: AAPL,KO,JNJ")
    parser.add_argument("--list-only", action="store_true",
                         help="רק מושך את רשימת הטיקרים ומדפיס כמה נמצאו, בלי לסרוק (לבדיקת מקורות הנתונים)")
    parser.add_argument("--universe", type=str, choices=["sp500", "sp1500"], default=None,
                         help="סרוק אוסף מובנה במקום רשימה ידנית (כרגע נתמך: sp500)")
    parser.add_argument("--mode", type=str, choices=["defensive", "enterprising"], default="defensive",
                         help="defensive = פרק 14 (7 קריטריונים), enterprising = פרק 15 (5 קריטריונים)")
    parser.add_argument("--min-revenue", type=float, default=MIN_REVENUE_INDUSTRIAL,
                         help="סף מכירות שנתיות לחברת תעשייה (קריטריון 1, מגן בלבד)")
    parser.add_argument("--out", type=str, default="graham_results.csv", help="שם קובץ הפלט")
    parser.add_argument("--sleep", type=float, default=0.5, help="השהיה בשניות בין בקשות (מניעת חסימת קצב)")
    parser.add_argument("--no-sec", action="store_true",
                         help="לא למשוך נתונים מדוחות ה-SEC, לעבוד על yahoo בלבד")
    args = parser.parse_args()

    if args.tickers:
        tickers = [x.strip().upper() for x in args.tickers.split(",") if x.strip()]
    elif args.universe == "sp1500":
        tickers = get_sp1500_tickers()
    elif args.universe == "sp500":
        tickers = get_sp500_tickers()
    else:
        print("[info] לא צוינו טיקרים/יקום - משתמש ברשימת ברירת המחדל של חברות גדולות ומוכרות.")
        tickers = FALLBACK_LARGE_CAPS

    if args.list_only:
        print(f"[list-only] {len(tickers)} טיקרים. עשרת הראשונים: {', '.join(tickers[:10])}")
        return 0

    if not args.no_sec:
        load_sec_data(tickers)

    print(f"[info] סורק {len(tickers)} טיקרים במצב '{args.mode}'...")
    results = []
    for i, tk in enumerate(tickers, 1):
        print(f"  ({i}/{len(tickers)}) {tk} ...", end=" ", flush=True)
        r = screen_ticker(tk, mode=args.mode, min_revenue=args.min_revenue)
        if r.error:
            print(f"שגיאה: {r.error}")
        else:
            fs = f", F={r.fscore}/9" if r.fscore is not None else ""
            ms = f", MoS={r.margin_of_safety*100:.0f}%" if r.margin_of_safety is not None else ""
            print(f"ציון {r.score}/{r.max_score}{fs}{ms}")
        results.append(r.as_row())
        time.sleep(args.sleep)

    df = pd.DataFrame(results)
    df = mark_share_classes(df)
    if "score" in df.columns:
        df = df.sort_values(["score", "ticker"], ascending=[False, True])
    df.to_csv(args.out, index=False, encoding="utf-8-sig")
    print(f"\n[done] הפלט המלא נשמר ל-{args.out}")

    if "data_source" in df.columns:
        from_sec = int((df["data_source"] == "sec").sum())
        mixed = int((df["data_source"] == "mixed").sum())
        print(f"[info] {from_sec} מתוך {len(df)} מניות נסרקו על נתוני SEC מלאים"
              f" ({mixed} חלקית)")
        if from_sec + mixed == 0 and not args.no_sec:
            print("[warn] אף מניה לא קיבלה נתוני SEC. בדוק שהסוד SEC_USER_AGENT\n"
                  "       מוגדר במאגר בפורמט 'graham-daily your@email.com'.")
    dupes = df["share_class_of"].notna().sum() if "share_class_of" in df.columns else 0
    if dupes:
        print(f"[info] {dupes} סדרות מניות משניות סומנו ולא ייספרו פעמיים")

    if "score" in df.columns and "max_score" in df.columns and not df.empty:
        top = df[df["error"].isna()].head(15)
        print("\nהמניות המובילות (הכי הרבה קריטריונים שעברו):")
        cols_to_show = ["ticker", "name", "company_type", "score", "max_score", "fscore",
                         "price", "graham_number", "margin_of_safety", "P/E", "P/B"]
        cols_to_show = [c for c in cols_to_show if c in top.columns]
        print(top[cols_to_show].to_string(index=False))


if __name__ == "__main__":
    sys.exit(main())
