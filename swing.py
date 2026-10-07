"""
סווינג (טווח קצר): הכלל היחיד שעבר את הבדיקה לאחור, והחישובים שלו.

הכלל (swing_sim.py, S&P 1500, נבחר על 2016-2020 ונבדק על 2021-2025):
  * מגמה עולה: סגירה מעל ממוצע 200, וממוצע 50 מעל ממוצע 200.
  * נזילות: מחיר 10$ לפחות, מחזור דולרי ממוצע 20 יום 20 מיליון לפחות.
  * אות: RSI(14) מתחת ל-35 - ירידה קצרה בתוך מגמה עולה.
  * שוק: SPY מעל ממוצע 200. בלי זה אין כניסות חדשות.
  * כניסה למחרת. סטופ 4 ATR מתחת לכניסה, בלי יעד רווח, ויציאה בסגירה של
    יום המסחר ה-20 (כולל יום הכניסה) אם הסטופ לא נפגע קודם.
    (עד ספט' 2026: סטופ 1.5 ATR ו-15 ימים. הוחלף אחרי תיקון באג בסימולציית התיק - ראו
    stop_fix_sim.py והערת הגודל למטה.)
  * גודל: סיכון קבוע (0.5% מההון) חלקי המרחק לסטופ, עד 20% מההון לפוזיציה, עד 15 פוזיציות.

מה שנבדק ונפל: פריצות 20/50 יום עם מחזור (תוחלת כמעט אפס), תיקון לממוצע
20/50 עם היפוך (טוב ב-2016-2020, שלילי אחר כך), RSI(2) (חיובי לעסקה אבל
תיק חלש), יעדי רווח ב-R (גרועים מיציאה לפי זמן).

החישובים כאן זהים לאלה שב-swing_sim.py, כדי שמה שהמסך מציג יהיה בדיוק מה
שנבדק. test_swing.py בודק את זה.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Dict, List, Optional

import numpy as np

try:
    import pandas as pd
except ImportError:  # pragma: no cover
    # במחשב שבו Windows חוסם את pandas (Smart App Control) מסך המסחר עדיין
    # צריך לעלות: הגודל, התאריכים וההזמנות לא צריכים את pandas. רק הסריקה
    # היומית נכשלת, ועד אז משתמשים באותות השמורים.
    pd = None

RSI_N = 14
RSI_MAX = 35.0
STOP_ATR = 4.0
HOLD_DAYS = 20
# גודל: סיכון 0.5% לעסקה, עד 20% לפוזיציה, עד 15 פוזיציות.
# תיקון ספט' 2026 (portfolio_fix_sim.py): בסימולציית התיק היה באג - עסקה שנעצרה ביום הכניסה
# לא יצאה לעולם. אחרי התיקון התיק לפי הכלל: +3.9% / +3.0% בשנה (2016-20 / 2021-25), ירידה
# מקסימלית ‎-36%, מול SPY +15.3% / +14.7% וירידה ‎-25%. רשת של 40 גדלים (סיכון 0.25%-1.5%,
# תקרה 10%/20%, 5-20 פוזיציות) לא מצאה גודל שמשנה את התמונה (הטוב ב-2016-20 הניב +3.0%
# ב-2021-25), ולכן הגודל לא שונה. העסקאות שנכנסו לתיק הניבו +0.06R/+0.03R, פחות מכלל
# האותות (+0.10R).
# היציאה נבחרה מחדש ברמת התיק (stop_fix_sim.py: סטופ 1.5-5 ATR, 10/15/20 ימים, סיכון
# 0.5%/1%, לפי שארפ 2016-20): סטופ 4 ATR ו-20 ימים ב-0.5% סיכון. תיק +11.1% / +6.4% בשנה,
# ירידה מקסימלית ‎-19% / ‎-15%, שארפ 1.16 / 0.72 (מול 0.34 / 0.26 בכלל הקודם). על הרכב S&P 500
# ההיסטורי אותה בחירה (4 ATR, 20 ימים) הייתה הטובה ב-2016-20. עדיין פחות מ-SPY ב-2021-25
# (+14.7%, שארפ 0.89) - היתרון הוא ירידה קטנה יותר, לא תשואה.
RISK_PCT = 0.005
# סיכון לפי ציון האיכות (size_quality_sim.py, אוקטובר 2026, 15 מקומות, תקרה 20%):
# 0/0.25/0.5/1% לציון 0/1/2/3, מול 0.5% קבוע. 2016-20: +11.7% / ‎-20% / שארפ 1.06 (מול
# +11.1% / ‎-19% / 1.16). 2021-25: +11.2% / ‎-17% / 0.92 (מול +6.4% / ‎-15% / 0.72).
# יותר תשואה וירידה עמוקה יותר. הטיה מתונה (0.25/0.25/0.5/0.75) הניבה +8.6% ב-2021-25.
# ציון 0 = בלי כניסה. RISK_PCT נשאר הבסיס (ציון 2), וגם ברירת המחדל כשאין ציון.
QUALITY_RISK = {0: 0.0, 1: 0.0025, 2: 0.005, 3: 0.01}
MAX_POSITION_PCT = 0.20
MAX_POSITIONS = 15
MIN_PRICE = 10.0
MIN_DOLLAR_VOL = 20e6
HISTORY_DAYS = 420          # ימים קלנדריים: מספיק לממוצע 200 ולחימום ה-RSI
ORDER_PREFIX = "swing-"     # client_order_id של קניות סווינג, כדי לזהות אותן אחר כך

# ציון איכות 0-3 לכל אות (הספים נקבעו על 2016-2020 בלבד, swing_sim.py --quality):
#   A. מגמה חזקה: המחיר לפחות 3.6% מעל ממוצע 200
#   B. תיקון עמוק: המחיר לפחות 4.7% מתחת לממוצע 50
#   C. מחזור נמוך בירידה: ממוצע מחזור 5 ימים עד פי 1.26 מממוצע 50 יום
# תוחלת לעסקה לפי ציון (2016-20 / 2021-25): 0-1: -0.07R/-0.04R, 2: +0.14R/+0.08R,
# 3: +0.30R/+0.26R. בתיק (1% סיכון, 10 מקומות) העדפת ציון 3 לא שיפרה את התשואה
# (+12.6% מול +15.4% ב-2021-25), ולכן זה מידע לבחירה ולא כלל שמסנן.
# (המספרים למעלה ביציאה הקודמת. ביציאה 4 ATR / 20 ימים: 0: +0.06R/+0.01R, 1: -0.03R/+0.07R,
# 2: +0.09R/+0.09R, 3: +0.19R/+0.13R - הפער בין הציונים קטן יותר.)
Q_DIST200_MIN = 0.036
Q_DIST50_MAX = -0.047
Q_VOLRATIO_MAX = 1.26

# מה הבדיקה לאחור הראתה, להצגה במסך (swing_results.json)
BACKTEST = {
    "period": "2021-2025", "trades": 4618, "win": 0.54, "exp_r": 0.10,
    "avg_ret": 0.012, "days": 18.8, "cagr": 0.112, "maxdd": -0.171,
    "is_cagr": 0.117, "spy_is_cagr": 0.153,
    # תוחלת לעסקה על הרכב S&P 500 היסטורי (בלי הטיית שורדים, survivor_sim.py)
    "pit_exp_r": 0.08,
    "spy_cagr": 0.147, "spy_maxdd": -0.245,
    # תוחלת לעסקה ב-2021-25 לפי ציון האיכות
    "quality_r": {"0": 0.01, "1": 0.07, "2": 0.09, "3": 0.13},
    # תוחלת לעסקה ב-2021-25: המניה ירדה לבד (הענף עלה ב-10 ימים) מול ירדה עם הענף
    "alone_r": {"alone": 0.03, "with": 0.12},
}

# ענף (ideas_sim.py, ספטמבר 2026): אות על מניה שירדה בזמן שהענף שלה (GICS, משקל
# שווה) עלה ב-10 הימים האחרונים הניב כמעט אפס: +0.003R / +0.030R (2016-20 / 2021-25),
# מול +0.114R / +0.127R כשהענף ירד. כ-30% מהאותות. תיק שמסנן לפיו הניב פחות
# ב-2016-20 (+9.9% מול +14.3%), ולכן זה מידע, לא כלל שמסנן.
SECTOR_DAYS = 10


# ---------------------------------------------------------------------------
# אינדיקטורים
# ---------------------------------------------------------------------------

def rsi(close: pd.DataFrame, n: int = RSI_N) -> pd.DataFrame:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def atr(high: pd.DataFrame, low: pd.DataFrame, close: pd.DataFrame, n: int = 14) -> pd.DataFrame:
    prev = close.shift()
    tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()]).groupby(level=0).max()
    tr = tr.reindex(close.index)[close.columns]
    return tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def indicators(C, H, L, V) -> Dict[str, pd.DataFrame]:
    sma = lambda x, n: x.rolling(n, min_periods=n).mean()
    s50, s200 = sma(C, 50), sma(C, 200)
    return {
        "dist200": C / s200 - 1, "dist50": C / s50 - 1,
        "volratio": sma(V, 5) / sma(V, 50),
        "sma50": s50, "sma200": s200, "rsi": rsi(C), "atr": atr(H, L, C),
        "dv20": sma(C * V, 20), "mom": C / C.shift(126) - 1,
        "uptrend": (C > s200) & (s50 > s200),
        "liquid": (C >= MIN_PRICE) & (sma(C * V, 20) >= MIN_DOLLAR_VOL),
    }


def signals(C, H, L, V) -> pd.DataFrame:
    """טבלת אותות לכל יום ולכל מניה (True = אות בסגירה של אותו יום)."""
    ind = indicators(C, H, L, V)
    return (ind["liquid"] & ind["uptrend"] & (ind["rsi"] < RSI_MAX)).fillna(False)


def quality(ind: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    """ציון 0-3: מגמה חזקה + תיקון עמוק + מחזור נמוך."""
    a = (ind["dist200"] >= Q_DIST200_MIN).astype(int)
    b = (ind["dist50"] <= Q_DIST50_MAX).astype(int)
    c = (ind["volratio"] <= Q_VOLRATIO_MAX).astype(int)
    return a + b + c


def sector_change(C: pd.DataFrame, sectors: Dict[str, str], days: int = SECTOR_DAYS,
                  skip=("SPY",)) -> pd.DataFrame:
    """לכל מניה: תשואת הענף שלה (ממוצע משקל שווה של המניות בטבלה) ב-days ימים. NaN בלי ענף."""
    ret = C.pct_change(fill_method=None)
    names = pd.Series({c: sectors.get(c) for c in C.columns
                       if c not in skip and sectors.get(c)}, dtype=object)
    out = pd.DataFrame(np.nan, index=C.index, columns=C.columns)
    for _, members in names.groupby(names).groups.items():
        m = list(members)
        level = (1 + ret[m].mean(axis=1).fillna(0)).cumprod()
        ch = level / level.shift(days) - 1
        for c in m:
            out[c] = ch
    return out


def market_ok(spy: pd.Series) -> pd.Series:
    return spy > spy.rolling(200, min_periods=200).mean()


def scan(C, H, L, V, spy_symbol: str = "SPY", sectors: Optional[Dict[str, str]] = None) -> dict:
    """האותות ליום האחרון בטבלאות. C/H/L/V: שורה ליום, עמודה לסימול."""
    if C.empty:
        return {"as_of": None, "market_ok": None, "rows": []}
    ind = indicators(C, H, L, V)
    sig = (ind["liquid"] & ind["uptrend"] & (ind["rsi"] < RSI_MAX)).fillna(False)
    last = C.index[-1]
    mk = None
    if spy_symbol in C.columns:
        m = market_ok(C[spy_symbol].ffill())
        mk = bool(m.iloc[-1]) if not pd.isna(m.iloc[-1]) else None
    q = quality(ind)
    sc = sector_change(C, sectors, skip=(spy_symbol,)) if sectors else None
    rows = []
    for t in C.columns:
        if t == spy_symbol or not bool(sig.at[last, t]):
            continue
        c, a = float(C.at[last, t]), float(ind["atr"].at[last, t])
        if not (c > 0 and a > 0):
            continue
        s10 = None if sc is None or pd.isna(sc.at[last, t]) else float(sc.at[last, t])
        rows.append({
            "ticker": t, "close": round(c, 2), "rsi": round(float(ind["rsi"].at[last, t]), 1),
            "atr": round(a, 4), "atr_pct": round(a / c * 100, 2),
            "stop_dist": round(STOP_ATR * a, 4),
            "mom": round(float(ind["mom"].at[last, t]), 4) if not pd.isna(ind["mom"].at[last, t]) else None,
            "dv20": round(float(ind["dv20"].at[last, t])),
            "sma50": round(float(ind["sma50"].at[last, t]), 2),
            "quality": int(q.at[last, t]),
            "q_trend": bool(ind["dist200"].at[last, t] >= Q_DIST200_MIN),
            "q_pullback": bool(ind["dist50"].at[last, t] <= Q_DIST50_MAX),
            "q_volume": bool(ind["volratio"].at[last, t] <= Q_VOLRATIO_MAX),
            "dist200": round(float(ind["dist200"].at[last, t]), 4),
            "dist50": round(float(ind["dist50"].at[last, t]), 4),
            "volratio": round(float(ind["volratio"].at[last, t]), 2),
            "sector": (sectors or {}).get(t),
            "sector10": round(s10, 4) if s10 is not None else None,
            "alone": (s10 >= 0) if s10 is not None else None,
        })
    # בבדיקה לאחור, כשהיו יותר אותות ממקומות, נבחרו החזקות במומנטום חצי שנה
    rows.sort(key=lambda r: -(r["mom"] if r["mom"] is not None else -9))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return {"as_of": last.date().isoformat(), "market_ok": mk, "rows": rows}


# ---------------------------------------------------------------------------
# לוח המסחר של ניו יורק ומועד היציאה
# ---------------------------------------------------------------------------

def _observed(d: date) -> date:
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def _nth(year, month, weekday, n):
    d = date(year, month, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def _last(year, month, weekday):
    d = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def nyse_holidays(year: int) -> set:
    from dateutil.easter import easter
    h = {
        _nth(year, 1, 0, 3), _nth(year, 2, 0, 3), easter(year) - timedelta(days=2),
        _last(year, 5, 0), _observed(date(year, 7, 4)), _nth(year, 9, 0, 1),
        _nth(year, 11, 3, 4), _observed(date(year, 12, 25)),
    }
    ny = date(year, 1, 1)
    if ny.weekday() != 5:            # שבת: לא מוזז לשישי של השנה הקודמת
        h.add(_observed(ny))
    if year >= 2022:
        h.add(_observed(date(year, 6, 19)))
    return h


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in nyse_holidays(d.year)


def add_trading_days(start: date, n: int) -> date:
    """יום המסחר ה-n כשיום ההתחלה (אם הוא יום מסחר) הוא הראשון."""
    d = start
    while not is_trading_day(d):
        d += timedelta(days=1)
    count = 1
    while count < n:
        d += timedelta(days=1)
        if is_trading_day(d):
            count += 1
    return d


def exit_date(entry: date) -> date:
    return add_trading_days(entry, HOLD_DAYS)


def trading_days_between(a: date, b: date) -> int:
    """כמה ימי מסחר מ-a עד b כולל שניהם (0 אם b לפני a)."""
    if b < a:
        return 0
    n, d = 0, a
    while d <= b:
        if is_trading_day(d):
            n += 1
        d += timedelta(days=1)
    return n


# ---------------------------------------------------------------------------
# גודל ופקודה
# ---------------------------------------------------------------------------

def risk_mult(quality) -> float:
    """פי כמה מהסיכון הבסיסי (RISK_PCT) לפי ציון האיכות. בלי ציון: פי 1."""
    try:
        return QUALITY_RISK[int(quality)] / RISK_PCT
    except (TypeError, ValueError, KeyError):
        return 1.0


def size(equity: float, entry: float, stop: float, risk_usd: Optional[float] = None,
         cash: Optional[float] = None, quality=None) -> dict:
    """כמה מניות: סיכון לפי ציון האיכות חלקי המרחק לסטופ, עם תקרה של 20% מההון.

    risk_usd הוא הסיכון הבסיסי (ציון 2); הוא מוכפל לפי הציון כמו RISK_PCT.
    """
    if not (equity and entry > 0 and stop < entry):
        return {"qty": 0, "risk": 0.0, "value": 0.0, "capped": False}
    base = risk_usd if risk_usd and risk_usd > 0 else equity * RISK_PCT
    risk = base * risk_mult(quality)
    qty = math.floor(risk / (entry - stop))
    cap = math.floor(equity * MAX_POSITION_PCT / entry)
    capped = qty > cap
    qty = min(qty, cap)
    if cash is not None and cash >= 0:
        qty = min(qty, math.floor(cash / entry))
    qty = max(qty, 0)
    return {"qty": qty, "risk": round(qty * (entry - stop), 2),
            "value": round(qty * entry, 2), "capped": capped}


def order_body(symbol: str, qty: int, entry: float, stop: float, client_id: str) -> dict:
    """קנייה ב-limit עם סטופ צמוד (OTO). GTC כדי שהסטופ יישאר חי אחרי הכניסה."""
    return {
        "symbol": symbol, "qty": str(int(qty)), "side": "buy", "type": "limit",
        "limit_price": f"{entry:.2f}", "time_in_force": "gtc", "order_class": "oto",
        "stop_loss": {"stop_price": f"{stop:.2f}"}, "client_order_id": client_id,
    }


def swing_entries(orders: Optional[List[dict]]) -> Dict[str, date]:
    """סימול -> תאריך הכניסה, לפוזיציות שנפתחו מקניית סווינג ועוד לא נמכרו.

    הפקודות מהחדשה לישנה. מכירה שבוצעה אחרי הקנייה סוגרת אותה.
    """
    out: Dict[str, date] = {}
    sold: set = set()
    for o in orders or []:
        sym = (o.get("symbol") or "").upper()
        if not sym or not o.get("filled_at") or sym in out or sym in sold:
            continue
        if o.get("side") == "sell":
            sold.add(sym)
            continue
        if str(o.get("client_order_id") or "").startswith(ORDER_PREFIX):
            try:
                out[sym] = date.fromisoformat(str(o["filled_at"])[:10])
            except ValueError:
                pass
        else:
            sold.add(sym)          # הקנייה האחרונה לא הייתה סווינג
    return out
