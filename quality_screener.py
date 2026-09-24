#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
שלב האיכות — בין הסינון הערכי לשלב הטכני.

הרעיון לקוח מ-Quantitative Value של Wesley Gray ו-Tobias Carlisle (Wiley, 2012),
שמסדרים סינון כמותי בצנרת קבועה: קודם זורקים החוצה חברות מסוכנות, ורק אחר כך
מדרגים את מי שנשאר. הסורק הערכי שלנו עשה עד היום רק את החלק השני.

השלב הזה עושה שני דברים על המניות שעברו את גראהם:

  פסילה
    * מדד בניש לזיהוי מניפולציה בדוחות (המודל בן שמונת המשתנים, Beneish 1999)
    * מדד אלטמן לסכנת חדלות פירעון (Altman 1968), על חברות שאינן פיננסיות

  דירוג
    * EBIT חלקי שווי פעילות — מדד הזול שנמצא החזק ביותר אצל גריי וקרלייל
    * רווחיות גולמית חלקי סך הנכסים — המדד של Novy-Marx (2013)
    * מומנטום של 12 חודשים פחות החודש האחרון
    * תשואת החזר הון נטו — דיבידנדים ורכישות עצמיות פחות הנפקות

זהו כלי סינון כמותי בלבד. הוא אינו ייעוץ השקעות.
"""

import argparse
import math
import os
import sys
import time

import numpy as np
import pandas as pd
import yfinance as yf


# ספי מדד בניש למודל בן שמונת המשתנים
BENEISH_MANIPULATOR = -1.78     # מעל זה: חשד ממשי
BENEISH_GREY = -2.22            # בין השניים: אזור אפור

# ספי מדד אלטמן במודל הקלאסי
ALTMAN_DISTRESS = 1.81
ALTMAN_SAFE = 2.99

RANK_FIELDS = ["ebit_ev", "gross_profitability", "momentum_12_1", "net_payout_yield"]


# ---------------------------------------------------------------------------
# שליפה סלחנית משורות הדוחות של yfinance
# ---------------------------------------------------------------------------
def pick(df: pd.DataFrame, names, col: int = 0):
    """
    מחזיר את הערך הראשון שנמצא מבין שמות שורה אפשריים, או None.

    yfinance משנה את שמות השורות בין גרסאות ובין חברות, ולכן כל שדה נשלף
    לפי רשימת חלופות ולא לפי שם יחיד.
    """
    if df is None or df.empty or col >= df.shape[1]:
        return None
    for name in names:
        if name in df.index:
            try:
                v = df.loc[name].iloc[col]
            except Exception:  # noqa: BLE001
                continue
            if v is None:
                continue
            try:
                v = float(v)
            except (TypeError, ValueError):
                continue
            if not math.isnan(v):
                return v
    return None


TOTAL_ASSETS = ["Total Assets"]
CURRENT_ASSETS = ["Current Assets", "Total Current Assets"]
CURRENT_LIAB = ["Current Liabilities", "Total Current Liabilities"]
TOTAL_LIAB = ["Total Liabilities Net Minority Interest", "Total Liabilities"]
RECEIVABLES = ["Accounts Receivable", "Receivables", "Gross Accounts Receivable"]
NET_PPE = ["Net PPE", "Net Property Plant And Equipment", "Properties"]
RETAINED = ["Retained Earnings"]
LT_DEBT = ["Long Term Debt", "Long Term Debt And Capital Lease Obligation"]
EQUITY = ["Stockholders Equity", "Total Stockholder Equity", "Common Stock Equity"]

REVENUE = ["Total Revenue", "Operating Revenue"]
COGS = ["Cost Of Revenue", "Cost Of Goods Sold"]
GROSS_PROFIT = ["Gross Profit"]
EBIT_ROWS = ["EBIT", "Operating Income", "Total Operating Income As Reported"]
NET_INCOME = ["Net Income Continuous Operations", "Net Income From Continuing Operation Net Minority Interest",
              "Net Income", "Net Income Common Stockholders"]
SGA = ["Selling General And Administration", "Selling General Administrative",
       "General And Administrative Expense"]
DEPREC = ["Reconciled Depreciation", "Depreciation And Amortization",
          "Depreciation Amortization Depletion", "Depreciation"]

CFO = ["Operating Cash Flow", "Total Cash From Operating Activities",
       "Cash Flow From Continuing Operating Activities"]
DIVIDENDS = ["Cash Dividends Paid", "Common Stock Dividend Paid", "Dividends Paid"]
BUYBACK = ["Repurchase Of Capital Stock", "Repurchase Of Common Stock"]
ISSUANCE = ["Issuance Of Capital Stock", "Common Stock Issuance", "Net Common Stock Issuance"]


def _ratio(a, b):
    if a is None or b is None or b == 0:
        return None
    return a / b


# ---------------------------------------------------------------------------
# מדד בניש (Beneish 1999, מודל שמונת המשתנים)
# ---------------------------------------------------------------------------
def beneish_m_score(bs, inc, cf):
    """
    מחזיר (ציון, מילון המשתנים) או (None, סיבה).

    כל שמונת המדדים הם יחס בין השנה הנוכחית לקודמת, ולכן דרושות שתי שנים
    מלאות של דוחות. חברה שאין לה שתיהן אינה מקבלת ציון, ולא ציון "טוב"
    כברירת מחדל.
    """
    if bs is None or inc is None or cf is None:
        return None, "אין דוחות"
    if bs.shape[1] < 2 or inc.shape[1] < 2 or cf.shape[1] < 2:
        return None, "פחות משתי שנות דוחות"

    def g(df, names, col):
        return pick(df, names, col)

    sales_t, sales_p = g(inc, REVENUE, 0), g(inc, REVENUE, 1)
    ta_t, ta_p = g(bs, TOTAL_ASSETS, 0), g(bs, TOTAL_ASSETS, 1)
    rec_t, rec_p = g(bs, RECEIVABLES, 0), g(bs, RECEIVABLES, 1)
    ca_t, ca_p = g(bs, CURRENT_ASSETS, 0), g(bs, CURRENT_ASSETS, 1)
    ppe_t, ppe_p = g(bs, NET_PPE, 0), g(bs, NET_PPE, 1)
    cogs_t, cogs_p = g(inc, COGS, 0), g(inc, COGS, 1)
    sga_t, sga_p = g(inc, SGA, 0), g(inc, SGA, 1)
    dep_t, dep_p = g(inc, DEPREC, 0), g(inc, DEPREC, 1)
    cl_t, cl_p = g(bs, CURRENT_LIAB, 0), g(bs, CURRENT_LIAB, 1)
    ltd_t, ltd_p = g(bs, LT_DEBT, 0) or 0.0, g(bs, LT_DEBT, 1) or 0.0
    ni_t = g(inc, NET_INCOME, 0)
    cfo_t = g(cf, CFO, 0)

    required = [sales_t, sales_p, ta_t, ta_p, rec_t, rec_p, ca_t, ca_p,
                cogs_t, cogs_p, cl_t, cl_p, ni_t, cfo_t]
    if any(v is None for v in required) or sales_t <= 0 or sales_p <= 0:
        return None, "חסרים שדות בדוחות"

    ppe_t = ppe_t or 0.0
    ppe_p = ppe_p or 0.0

    dsri = _ratio(_ratio(rec_t, sales_t), _ratio(rec_p, sales_p))
    gmi = _ratio((sales_p - cogs_p) / sales_p, (sales_t - cogs_t) / sales_t)
    aqi = _ratio(1.0 - (ca_t + ppe_t) / ta_t, 1.0 - (ca_p + ppe_p) / ta_p)
    sgi = _ratio(sales_t, sales_p)

    # DEPI דורש פחת. בלעדיו מניחים 1, כלומר קצב פחת ללא שינוי, שהוא הערך הנייטרלי.
    if dep_t and dep_p and (dep_t + ppe_t) and (dep_p + ppe_p):
        depi = _ratio(dep_p / (dep_p + ppe_p), dep_t / (dep_t + ppe_t))
    else:
        depi = 1.0

    sgai = _ratio(_ratio(sga_t, sales_t), _ratio(sga_p, sales_p)) if (sga_t and sga_p) else 1.0
    lvgi = _ratio((cl_t + ltd_t) / ta_t, (cl_p + ltd_p) / ta_p)
    tata = (ni_t - cfo_t) / ta_t

    parts = {"DSRI": dsri, "GMI": gmi, "AQI": aqi, "SGI": sgi,
             "DEPI": depi, "SGAI": sgai, "LVGI": lvgi, "TATA": tata}
    if any(v is None or math.isnan(v) or math.isinf(v) for v in parts.values()):
        return None, "חישוב לא תקין"

    # יחסים קיצוניים נובעים כמעט תמיד משינוי חשבונאי או מדוח חלקי, לא ממניפולציה.
    for k in ("DSRI", "GMI", "AQI", "SGI", "DEPI", "SGAI", "LVGI"):
        parts[k] = max(0.1, min(10.0, parts[k]))

    m = (-4.84
         + 0.920 * parts["DSRI"] + 0.528 * parts["GMI"] + 0.404 * parts["AQI"]
         + 0.892 * parts["SGI"] + 0.115 * parts["DEPI"] - 0.172 * parts["SGAI"]
         + 4.679 * parts["TATA"] - 0.327 * parts["LVGI"])
    return m, parts


# ---------------------------------------------------------------------------
# מדד אלטמן (Altman 1968, המודל הקלאסי)
# ---------------------------------------------------------------------------
def altman_z_score(bs, inc, market_cap):
    """
    המודל פותח לחברות תעשייה ואינו תקף לבנקים ולחברות ביטוח, שמבנה המאזן
    שלהן שונה לחלוטין. הבורר למטה מוודא שהוא לא מופעל עליהן.
    """
    if bs is None or inc is None or not market_cap:
        return None
    ta = pick(bs, TOTAL_ASSETS)
    if not ta:
        return None
    ca, cl = pick(bs, CURRENT_ASSETS), pick(bs, CURRENT_LIAB)
    re = pick(bs, RETAINED)
    ebit = pick(inc, EBIT_ROWS)
    sales = pick(inc, REVENUE)
    tl = pick(bs, TOTAL_LIAB)
    if None in (ca, cl, ebit, sales, tl) or tl == 0:
        return None
    re = re if re is not None else 0.0

    x1 = (ca - cl) / ta
    x2 = re / ta
    x3 = ebit / ta
    x4 = market_cap / tl
    x5 = sales / ta
    return 1.2 * x1 + 1.4 * x2 + 3.3 * x3 + 0.6 * x4 + 1.0 * x5


# ---------------------------------------------------------------------------
# מדדי הדירוג
# ---------------------------------------------------------------------------
def gross_profitability(bs, inc):
    """הרווח הגולמי חלקי סך הנכסים, המדד של נובי-מרקס."""
    ta = pick(bs, TOTAL_ASSETS)
    gp = pick(inc, GROSS_PROFIT)
    if gp is None:
        rev, cogs = pick(inc, REVENUE), pick(inc, COGS)
        gp = (rev - cogs) if (rev is not None and cogs is not None) else None
    return _ratio(gp, ta)


def momentum_12_1(prices: pd.Series):
    """
    תשואת שנים עשר חודשים בהשמטת החודש האחרון. החודש האחרון מושמט מפני
    שבטווח הקצר פועל היפוך ולא המשכיות, ולכן הכללתו מחלישה את הסיגנל.
    """
    if prices is None or len(prices) < 260:
        return None
    recent = float(prices.iloc[-22])
    old = float(prices.iloc[-252])
    if old <= 0:
        return None
    return recent / old - 1.0


def net_payout_yield(cf, market_cap):
    """דיבידנדים ורכישות עצמיות פחות הנפקות, חלקי שווי השוק."""
    if cf is None or not market_cap:
        return None
    div = pick(cf, DIVIDENDS) or 0.0
    buy = pick(cf, BUYBACK) or 0.0
    iss = pick(cf, ISSUANCE) or 0.0
    # בדוח התזרים דיבידנדים ורכישות מופיעים כמספרים שליליים, והנפקה כחיובי.
    returned = abs(div) + abs(min(buy, 0.0)) - max(iss, 0.0)
    return returned / market_cap


# ---------------------------------------------------------------------------
# מניה בודדת
# ---------------------------------------------------------------------------
def _rank_metrics(out: dict, tk, bs, inc, cf, market_cap, ev) -> dict:
    """מדדי הדירוג בלבד. משותף לחברות תפעוליות ולפיננסיות."""
    ebit = pick(inc, EBIT_ROWS)
    out["ebit_ev"] = round(ebit / ev, 4) if (ebit and ev and ev > 0) else ""

    gp = gross_profitability(bs, inc)
    out["gross_profitability"] = round(gp, 4) if gp is not None else ""

    try:
        hist = tk.history(period="15mo", interval="1d", auto_adjust=False)
        mom = momentum_12_1(hist["Close"]) if hist is not None and not hist.empty else None
    except Exception:  # noqa: BLE001
        mom = None
    out["momentum_12_1"] = round(mom, 4) if mom is not None else ""

    npy = net_payout_yield(cf, market_cap)
    out["net_payout_yield"] = round(npy, 4) if npy is not None else ""

    # צבירות, בגרסת תזרים המזומנים של Hribar & Collins (2002): הרווח פחות
    # התזרים התפעולי, חלקי סך הנכסים. משמש רק כגיבוי כשאין נתון מה-SEC.
    ni = pick(inc, NET_INCOME)
    ocf = pick(cf, CFO)
    ta = pick(bs, TOTAL_ASSETS)
    out["accruals_yf"] = (round((ni - ocf) / ta, 4)
                          if (ni is not None and ocf is not None and ta) else "")
    return out


def _finish_financial(out, tk, inc, cf, market_cap, ev, bs):
    out = _rank_metrics(out, tk, bs, inc, cf, market_cap, ev)
    out["red_flag"] = ""      # אין מבחן פסילה שחל על חברה פיננסית
    return out


def analyse(ticker: str, company_type: str) -> dict:
    tk = yf.Ticker(ticker)
    bs = getattr(tk, "balance_sheet", None)
    inc = getattr(tk, "income_stmt", None)
    cf = getattr(tk, "cashflow", None)

    try:
        info = tk.info or {}
    except Exception:  # noqa: BLE001
        info = {}
    market_cap = info.get("marketCap")
    ev = info.get("enterpriseValue")

    # כשהקלט הוא ריצת בדיקה בלי עמודת סוג החברה, הסוג נגזר מהסקטור.
    if not company_type:
        sector = str(info.get("sector") or "")
        company_type = "financial" if sector in ("Financial Services", "Real Estate") else "industrial"

    out = {"ticker": ticker, "company_type": company_type}

    # שני המודלים בנויים על מבנה דוחות של חברה תפעולית: מרווח גולמי, חייבים
    # מול מכירות, ונכסים שוטפים מול התחייבויות שוטפות. לבנקים ולחברות ביטוח
    # אין מהם כמעט דבר, ולכן הציון אינו מחושב להם — לא מפני שחסרים נתונים
    # אלא מפני שהמודל אינו חל עליהן.
    if company_type == "financial":
        out["beneish_m"] = ""
        out["beneish_flag"] = "לא רלוונטי"
        out["altman_z"] = ""
        out["altman_flag"] = "לא רלוונטי"
        return _finish_financial(out, tk, inc, cf, market_cap, ev, bs)

    m, parts = beneish_m_score(bs, inc, cf)
    out["beneish_m"] = round(m, 2) if m is not None else ""
    if m is None:
        out["beneish_flag"] = "אין נתונים"
    elif m > BENEISH_MANIPULATOR:
        out["beneish_flag"] = "חשד למניפולציה"
    elif m > BENEISH_GREY:
        out["beneish_flag"] = "אזור אפור"
    else:
        out["beneish_flag"] = "תקין"

    z = altman_z_score(bs, inc, market_cap)
    out["altman_z"] = round(z, 2) if z is not None else ""
    if z is None:
        out["altman_flag"] = "אין נתונים"
    elif z < ALTMAN_DISTRESS:
        out["altman_flag"] = "סיכון חדלות פירעון"
    elif z < ALTMAN_SAFE:
        out["altman_flag"] = "אזור אפור"
    else:
        out["altman_flag"] = "תקין"

    out = _rank_metrics(out, tk, bs, inc, cf, market_cap, ev)

    flags = []
    if out["beneish_flag"] == "חשד למניפולציה":
        flags.append("בניש")
    if out["altman_flag"] == "סיכון חדלות פירעון":
        flags.append("אלטמן")
    out["red_flag"] = " ו".join(flags)
    return out


MIN_SECTOR_SIZE = 5       # מתחת לזה, דירוג בתוך הענף חסר משמעות
MIN_DOLLAR_VOLUME = 2_000_000
MAX_ACCRUALS = 0.10


def _sector_buckets(df: pd.DataFrame) -> pd.Series:
    """לאיזה ענף כל שורה שייכת לצורך הדירוג. ענפים קטנים מדי מאוגדים יחד."""
    if "sector" in df.columns:
        sector = df["sector"].fillna("").astype(str).str.strip()
    else:
        sector = pd.Series([""] * len(df), index=df.index)
    counts = sector.value_counts()
    small = sector.map(counts).fillna(0) < MIN_SECTOR_SIZE
    return sector.mask(small | (sector == ""), "כלל השוק")


def add_composite(df: pd.DataFrame, by_sector: bool = True) -> pd.DataFrame:
    """
    ציון מסכם: דירוג אחוזוני של כל אחד מארבעת המדדים, וממוצע של הדירוגים.

    הדירוג נעשה **בתוך הענף** ולא על פני כל הרשימה. הסיבה פשוטה: תשואת
    רווח תפעולי של בנק ושל חברת תוכנה לא מדברות באותה שפה. כשמדרגים את
    כולם יחד, הרשימה מתמלאת בענף אחד שבמקרה זול כרגע, והמשקיע חושב שבחר
    מניות כשבעצם בחר ענף.

    ענף שיש בו פחות מחמש מניות מאוגד ל"כלל השוק", כי דירוג אחוזוני בתוך
    שתי מניות אומר רק "אחת מהן ראשונה".
    """
    df = df.copy()
    bucket = _sector_buckets(df) if by_sector else pd.Series(["כלל השוק"] * len(df),
                                                             index=df.index)
    parts = []
    used_sector = False
    for col in RANK_FIELDS:
        if col not in df.columns:
            continue
        vals = pd.to_numeric(df[col], errors="coerce")
        if vals.notna().sum() < 2:
            continue
        universe = vals.rank(pct=True) * 100.0
        if by_sector:
            per_sector = vals.groupby(bucket).rank(pct=True) * 100.0
            # ענף שבו למדד הזה יש פחות משלושה ערכים חוזר לדירוג כלל-שוקי
            enough = vals.notna().groupby(bucket).transform("sum") >= 3
            ranked = per_sector.where(enough, universe)
            used_sector = used_sector or bool(enough.any())
        else:
            ranked = universe
        parts.append(ranked)

    df["rank_bucket"] = bucket
    df["rank_basis"] = "ענף" if used_sector else "כלל השוק"
    if not parts:
        df["quality_score"] = ""
        return df
    score = pd.concat(parts, axis=1).mean(axis=1, skipna=True)
    df["quality_score"] = score.round(0).astype("Int64").astype(str).replace("<NA>", "")
    return df


def finalise_flags(df: pd.DataFrame) -> pd.DataFrame:
    """מוסיף את שתי הבדיקות שצריכות את הטבלה כולה ולא מניה בודדת.

    צבירות: הפער בין הרווח החשבונאי לתזרים התפעולי. סלואן (1996) הראה
    שחברות שהרווח שלהן מגיע מצבירות ולא ממזומן נוטות לאכזב בשנה שאחריה.
    זו בדיקה שמשלימה את בניש בדיוק במקום שבו הוא חלש: לא מניפולציה, אלא
    חשבונאות אגרסיבית שהיא לגמרי חוקית.

    נזילות: מניה שנסחרת דליל היא גם זו שהנתונים עליה הכי גרועים, וגם זו
    שבה פער הקנייה-מכירה אוכל את התשואה.
    """
    df = df.copy()

    # ערך מהדוחות הרשמיים קודם, ואם אין — מה שחושב מ-yahoo
    primary = pd.to_numeric(df.get("accruals"), errors="coerce") if "accruals" in df.columns else None
    fallback = pd.to_numeric(df.get("accruals_yf"), errors="coerce") if "accruals_yf" in df.columns else None
    if primary is None and fallback is None:
        acc = pd.Series([float("nan")] * len(df), index=df.index)
    elif primary is None:
        acc = fallback
    elif fallback is None:
        acc = primary
    else:
        acc = primary.fillna(fallback)
    df["accruals"] = acc.round(4)

    is_financial = df.get("company_type", pd.Series([""] * len(df), index=df.index)) == "financial"
    bad_accruals = acc.notna() & (acc > MAX_ACCRUALS) & ~is_financial
    df["accruals_flag"] = ""
    df.loc[bad_accruals, "accruals_flag"] = "צבירות גבוהות"
    df.loc[is_financial, "accruals_flag"] = "לא רלוונטי"

    # מצטרף לדגל האדום הקיים, כדי ששלב התזמון יראה פסילה אחת מאוחדת
    existing = df.get("red_flag", pd.Series([""] * len(df), index=df.index)).fillna("").astype(str)
    df["red_flag"] = [
        "; ".join(p for p in (e.strip(), "צבירות גבוהות" if b else "") if p)
        for e, b in zip(existing, bad_accruals)
    ]

    vol = pd.to_numeric(df.get("dollar_volume"), errors="coerce") if "dollar_volume" in df.columns else None
    if vol is None:
        df["liquidity_flag"] = ""
    else:
        thin = vol.notna() & (vol < MIN_DOLLAR_VOLUME)
        df["liquidity_flag"] = ""
        df.loc[thin, "liquidity_flag"] = "נזילות נמוכה"

    return df


def main():
    ap = argparse.ArgumentParser(description="שלב האיכות של סורק גראהם")
    ap.add_argument("--in", dest="infile", default="results.csv")
    ap.add_argument("--out", default="quality_results.csv")
    ap.add_argument("--mode", choices=["both", "defensive", "enterprising"], default="both")
    ap.add_argument("--tickers", default="", help="רשימת טיקרים לבדיקה מהירה")
    ap.add_argument("--sleep", type=float, default=0.25)
    args = ap.parse_args()

    if args.tickers.strip():
        base = pd.DataFrame({"ticker": [t.strip().upper()
                                        for t in args.tickers.split(",") if t.strip()]})
        base["company_type"] = "industrial"
    else:
        if not os.path.exists(args.infile):
            print(f"לא נמצא קובץ הקלט: {args.infile}", file=sys.stderr)
            return 1
        raw = pd.read_csv(args.infile)
        ok = raw["error"].isna() | (raw["error"].astype(str).str.strip() == "")
        for c in ("score", "max_score", "score_ent", "max_score_ent"):
            raw[c] = pd.to_numeric(raw[c], errors="coerce")
        pd_ = raw["score"] >= raw["max_score"]
        pe_ = raw["score_ent"] >= raw["max_score_ent"]
        keep = (pd_ if args.mode == "defensive"
                else pe_ if args.mode == "enterprising" else (pd_ | pe_))
        # סדרת מניות משנית של מנפיק שכבר ברשימה לא נספרת פעמיים
        if "share_class_of" in raw.columns:
            primary = raw["share_class_of"].isna() | (
                raw["share_class_of"].astype(str).str.strip().isin(["", "nan", "None"]))
        else:
            primary = pd.Series(True, index=raw.index)
        base = raw[ok & keep.fillna(False) & primary].copy()
        dropped = int((ok & keep.fillna(False) & ~primary).sum())
        print(f"נבחרו {len(base)} מניות שעברו את גראהם מתוך {len(raw)}.")
        if dropped:
            print(f"{dropped} סדרות מניות משניות הושמטו.")

    if base.empty:
        print("אין מניות לעיבוד.")
        pd.DataFrame().to_csv(args.out, index=False)
        return 0

    rows = []
    total = len(base)
    for i, (_, src) in enumerate(base.iterrows(), start=1):
        ticker = str(src["ticker"]).strip().upper()
        ctype = str(src.get("company_type", "") or "")
        try:
            res = analyse(ticker, ctype)
        except Exception as exc:  # noqa: BLE001
            print(f"[{i}/{total}] {ticker}: שגיאה — {exc}")
            res = {"ticker": ticker, "beneish_m": "", "beneish_flag": "אין נתונים",
                   "altman_z": "", "altman_flag": "אין נתונים", "ebit_ev": "",
                   "gross_profitability": "", "momentum_12_1": "",
                   "net_payout_yield": "", "red_flag": ""}
        merged = {**src.to_dict(), **res}
        rows.append(merged)
        print(f"[{i}/{total}] {ticker}: בניש {res['beneish_m']} ({res['beneish_flag']}) | "
              f"אלטמן {res['altman_z']} ({res['altman_flag']}) | "
              f"EBIT/EV {res['ebit_ev']} | מומנטום {res['momentum_12_1']}")
        time.sleep(args.sleep)

    out = finalise_flags(add_composite(pd.DataFrame(rows)))
    flagged = (out["red_flag"].astype(str).str.strip() != "").sum()
    thin = (out["liquidity_flag"].astype(str).str.strip() != "").sum()
    print(f"\n{flagged} מניות קיבלו דגל אדום מתוך {len(out)}.")
    if thin:
        print(f"{thin} מניות סומנו כדלילות מדי למסחר.")
    if "rank_basis" in out.columns and not out.empty:
        buckets = out["rank_bucket"].nunique()
        print(f"הדירוג נעשה בתוך {buckets} קבוצות ענפיות.")
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    out.to_csv(args.out, index=False)
    print(f"נכתבו {len(out)} שורות אל {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
