#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
שלב טכני משלים לסורק גראהם.

הקלט הוא results.csv שמיוצר בסריקה הערכית הלילית. מתוכו נלקחות רק המניות
שעברו את מלוא הקריטריונים (במצב המגן, במצב היוזם, או בשניהם), ועליהן מחושבת
שכבה טכנית שמבוססת על הספר של קונסטנס בראון,
"Technical Analysis for the Trading Professional" (McGraw-Hill, 1999):

  * RSI-14 עם כללי הטווח מפרק 1 — התעלה שבה ה-RSI נע משתנה לפי המגמה,
    ולכן 30/70 קבועים מוחלפים בגבולות שתלויים במשטר השוק של המניה.
  * Positive ו-Negative Reversals מפרק 8, כולל יעד המחיר שמחושב מהם
    (הנוסחאות מופיעות בנספח D של הספר).
  * האוסילטור הנגזר מפרק 14 — RSI מוחלק שלוש פעמים ומוצג כהיסטוגרמה.

בנוסף מחושבת שכבת ניהול סיכון סטנדרטית: ATR באחוזים, ADX, ומיקום מול
ממוצע נע של 200 יום.

זהו כלי סינון כמותי בלבד. הוא אינו ייעוץ השקעות.
"""

import argparse
import math
import os
import sys
import time
from datetime import date, datetime

import numpy as np
import pandas as pd
import yfinance as yf


# ---------------------------------------------------------------------------
# כללי הטווח של בראון (פרק 1)
# ---------------------------------------------------------------------------
# בשוק שורי ה-RSI נע בין תמיכה של 40-50 להתנגדות של 80-90.
# בשוק דובי הוא נע בין תמיכה של 20-30 להתנגדות של 55-65.
BULL_BANDS = {"support_lo": 40.0, "support_hi": 50.0,
              "resist_lo": 80.0, "resist_hi": 90.0}
BEAR_BANDS = {"support_lo": 20.0, "support_hi": 30.0,
              "resist_lo": 55.0, "resist_hi": 65.0}

# בראון מקבלת ירידה עד 38-39 בזמן מעבר משוק דובי לשורי בלי לפסול את המעבר.
TRANSITION_FLOOR = 38.0
EARNINGS_BLACKOUT_DAYS = 5   # אין כניסה כשהדוח מעבר לפינה

RSI_PERIOD = 14          # בראון משתמשת ב-14 בכל גרפי המניות שבספר
PIVOT_WINDOW = 5         # כמה נרות מכל צד מגדירים שיא או שפל מקומי ב-RSI
LOOKBACK_DAYS = 420      # בערך שנה וחצי של מסחר, מספיק לממוצע 200 יום
# שש שנים של נרות יומיים נמשכות כדי שיהיה מדגם סביר של תבניות היפוך קודמות
# שממנו אפשר לאמוד כמה זמן לוקח ליעד להיסגר.
HISTORY_PERIOD = "6y"

# תבנית היפוך שהתרחשה לפני יותר מחצי שנה כבר איבדה את ערך התזמון שלה,
# ושני צירים שרחוקים זה מזה יותר משנה מייצרים יעד מנופח.
MAX_SIGNAL_AGE = 120
MAX_PIVOT_SPAN = 250
# יעד שמרמז על תנועה של יותר מ-60 אחוז מהמחיר הנוכחי כמעט תמיד נובע
# מצמד צירים לא קשור, ולא מתבנית אמיתית.
MAX_TARGET_MOVE = 0.60


def _clean(value):
    """
    ערך מתא ריק ב-CSV חוזר מ-pandas כ-NaN, ו-str(NaN) הוא "nan" — מחרוזת
    שנראית מלאה. כל ערך שמגיע מהקובץ עובר דרך כאן לפני שנבדק.
    """
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() in ("nan", "none", "<na>") else value


def _plausible_target(target: float, price: float) -> bool:
    if not price or target <= 0:
        return False
    return abs(target / price - 1.0) <= MAX_TARGET_MOVE


# ---------------------------------------------------------------------------
# אינדיקטורים
# ---------------------------------------------------------------------------
def wilder_rma(series: pd.Series, period: int) -> pd.Series:
    """
    ההחלקה של ויילדר — הבסיס ל-RSI, ל-ATR ול-ADX.

    הזרע הוא ממוצע פשוט של התקופה הראשונה, כפי שוויילדר הגדיר, ולא הערך
    הבודד הראשון. זה מה שכל תוכנות הגרפים מציגות, וכך המספרים כאן זהים
    למה שרואים בגרף.
    """
    v = series.to_numpy(dtype=float)
    out = np.full(len(v), np.nan)
    valid = ~np.isnan(v)

    start, run = None, 0
    for i in range(len(v)):
        run = run + 1 if valid[i] else 0
        if run == period:
            start = i
            break
    if start is None:
        return pd.Series(out, index=series.index)

    out[start] = v[start - period + 1:start + 1].mean()
    for i in range(start + 1, len(v)):
        x = v[i] if valid[i] else 0.0
        out[i] = (out[i - 1] * (period - 1) + x) / period
    return pd.Series(out, index=series.index)


def rsi(close: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = wilder_rma(gain, period)
    avg_loss = wilder_rma(loss, period)
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - (100.0 / (1.0 + rs))
    # כשאין ירידות כלל ה-RSI הוא 100. כשאין עליות כלל היחס הוא אפס וה-RSI
    # יוצא אפס מעצמו, ולכן אין צורך בטיפול נפרד.
    out = out.where(avg_loss != 0.0, 100.0)
    return out


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    return pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)


def atr(high, low, close, period: int = 14) -> pd.Series:
    return wilder_rma(true_range(high, low, close), period)


def adx(high, low, close, period: int = 14) -> pd.Series:
    up = high.diff()
    down = -low.diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=high.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=high.index)

    tr_n = wilder_rma(true_range(high, low, close), period)
    plus_di = 100.0 * wilder_rma(plus_dm, period) / tr_n.replace(0.0, np.nan)
    minus_di = 100.0 * wilder_rma(minus_dm, period) / tr_n.replace(0.0, np.nan)

    denom = (plus_di + minus_di).replace(0.0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / denom
    return wilder_rma(dx, period)


def derivative_oscillator(close: pd.Series) -> pd.Series:
    """
    האוסילטור הנגזר, פרק 14 בספר.

    ארבעת השלבים שבראון מפרטת:
      1. ממוצע נע אקספוננציאלי של 5 על RSI-14
      2. ממוצע נע אקספוננציאלי של 3 על התוצאה
      3. ממוצע נע פשוט של 9 על התוצאה של שלב 2
      4. ההפרש בין שלב 2 לשלב 3, מוצג כהיסטוגרמה

    בראון מזהירה שהנוסחה נבנתה למדדי מניות ולמניות, ושאין להשתמש בה
    על אג"ח. הסורק הזה רץ רק על מניות, ולכן היא במקומה.
    """
    r = rsi(close, RSI_PERIOD)
    ema1 = r.ewm(span=5, adjust=False).mean()
    ema2 = ema1.ewm(span=3, adjust=False).mean()
    sma = ema2.rolling(9).mean()
    return ema2 - sma


# ---------------------------------------------------------------------------
# זיהוי משטר השוק
# ---------------------------------------------------------------------------
def detect_regime(close: pd.Series, r: pd.Series):
    """
    בראון קובעת את התעלה של ה-RSI לפי המגמה, אבל לא נותנת מבחן מכני לזיהוי
    המגמה. כאן המגמה נקבעת בשתי דרכים שמצטלבות:

      * מבחן מחיר: המיקום מול ממוצע נע של 200 יום והכיוון של הממוצע עצמו.
      * מבחן התנהגות ה-RSI עצמו לאורך 120 הנרות האחרונים, לפי ההיגיון של
        פרק 1 — ב-RSI של שוק שורי השפלים לא שוברים את אזור ה-40 והשיאים
        מגיעים ל-80, ובשוק דובי השיאים נעצרים באזור 65 והשפלים יורדים ל-30.

    כששני המבחנים מסכימים המשטר ודאי. כשהם חלוקים מוחזר "מעבר", וזה בדיוק
    המצב שבראון מתארת כשלב שבין המשטרים.
    """
    sma200 = close.rolling(200).mean()
    price_test = "unknown"
    if len(close) >= 220 and not math.isnan(sma200.iloc[-1]):
        above = close.iloc[-1] > sma200.iloc[-1]
        rising = sma200.iloc[-1] > sma200.iloc[-21]
        if above and rising:
            price_test = "bull"
        elif (not above) and (not rising):
            price_test = "bear"
        else:
            price_test = "mixed"

    window = r.dropna().iloc[-120:]
    rsi_test = "unknown"
    if len(window) >= 60:
        hi, lo = float(window.max()), float(window.min())
        bull_like = hi >= 75.0 and lo >= 35.0
        bear_like = hi <= 68.0 and lo <= 32.0
        if bull_like and not bear_like:
            rsi_test = "bull"
        elif bear_like and not bull_like:
            rsi_test = "bear"
        else:
            rsi_test = "mixed"

    if price_test == rsi_test and price_test in ("bull", "bear"):
        regime, confidence = price_test, "ודאי"
    elif price_test in ("bull", "bear") and rsi_test in ("mixed", "unknown"):
        regime, confidence = price_test, "חלקי"
    elif rsi_test in ("bull", "bear") and price_test in ("mixed", "unknown"):
        regime, confidence = rsi_test, "חלקי"
    elif price_test in ("bull", "bear") and rsi_test in ("bull", "bear"):
        regime, confidence = "transition", "סותר"
    else:
        regime, confidence = "transition", "לא ברור"

    return regime, confidence, price_test, rsi_test


def rsi_zone(value: float, regime: str):
    """מיקום ה-RSI בתוך התעלה של המשטר, במקום מול 30/70 קבועים."""
    if value is None or math.isnan(value):
        return "", None
    bands = BULL_BANDS if regime == "bull" else BEAR_BANDS
    if regime == "transition":
        bands = BEAR_BANDS

    if value < bands["support_lo"]:
        return "מתחת לתמיכה", bands
    if value <= bands["support_hi"]:
        return "על התמיכה", bands
    if value < bands["resist_lo"]:
        return "אמצע התעלה", bands
    if value <= bands["resist_hi"]:
        return "על ההתנגדות", bands
    return "מעל ההתנגדות", bands


def detect_regime_shift(r: pd.Series, lookback: int = 180) -> str:
    """
    רצף היפוך המגמה מפרק 1: ה-RSI נכשל באזור 55-65, נסוג אבל מחזיק את
    אזור 40-50 (בראון מקבלת 38-39), ואז הגל הבא פורץ מעל 68 בדרך ל-75-85.
    """
    w = r.dropna().iloc[-lookback:]
    if len(w) < 60:
        return ""
    vals = w.to_numpy()

    # השיא האחרון שנעצר באזור ההתנגדות הדובית
    fail_idx = None
    for i in range(len(vals) - 20, 10, -1):
        if BEAR_BANDS["resist_lo"] <= vals[i] <= BEAR_BANDS["resist_hi"]:
            if vals[i] == max(vals[max(0, i - 10):i + 11]):
                fail_idx = i
                break
    if fail_idx is None:
        return ""

    after = vals[fail_idx + 1:]
    if len(after) < 10:
        return ""
    trough = float(after.min())
    if trough < TRANSITION_FLOOR:
        return ""          # נשברה התמיכה, המעבר לא התקיים
    if float(after[-10:].max()) > 68.0:
        return "מעבר לשוק שורי"
    return "מחזיק את התמיכה, טרם פרץ"


# ---------------------------------------------------------------------------
# Positive / Negative Reversals (פרק 8, נוסחאות בנספח D)
# ---------------------------------------------------------------------------
def find_pivots(series: pd.Series, k: int = PIVOT_WINDOW):
    """מחזיר שתי רשימות של מיקומים: שפלים מקומיים ושיאים מקומיים."""
    v = series.to_numpy()
    lows, highs = [], []
    for i in range(k, len(v) - k):
        window = v[i - k:i + k + 1]
        if np.isnan(window).any():
            continue
        if v[i] == window.min() and (window.min() < window.max()):
            lows.append(i)
        elif v[i] == window.max() and (window.min() < window.max()):
            highs.append(i)
    return lows, highs


def positive_reversal(close: pd.Series, r: pd.Series,
                      max_bars_ago: int = MAX_SIGNAL_AGE,
                      max_span: int = MAX_PIVOT_SPAN):
    """
    שפל נמוך יותר ב-RSI מול שפל גבוה יותר במחיר — איתות שורי.

    היעד לפי נספח D: (מחיר ב-X פחות מחיר ב-W) ועוד המחיר ב-Y,
    כאשר W הוא השפל הראשון ב-RSI, Y הוא השיא ב-RSI שאחריו,
    ו-X הוא השפל הבא.

    התבנית נחשבת רק אם היא טרייה מספיק ואם שני השפלים קרובים זה לזה בזמן.
    צמד נקודות רחוק מדי מייצר יעד מנופח שאין לו משמעות מעשית.
    """
    lows, highs = find_pivots(r)
    if len(lows) < 2:
        return None
    c = close.to_numpy()
    rv = r.to_numpy()
    last = len(c) - 1

    for xi in reversed(lows):
        if last - xi > max_bars_ago:
            break                  # כל מה שנשאר ישן עוד יותר
        for wi in reversed([p for p in lows if p < xi and xi - p <= max_span]):
            if rv[xi] >= rv[wi]:
                continue           # ה-RSI חייב לעשות שפל נמוך יותר
            if c[xi] <= c[wi]:
                continue           # והמחיר חייב לעשות שפל גבוה יותר
            between = [p for p in highs if wi < p < xi]
            if not between:
                continue
            yi = max(between, key=lambda p: rv[p])
            target = (c[xi] - c[wi]) + c[yi]
            if not _plausible_target(target, c[last]):
                continue
            return {
                "bars_ago": int(last - xi),
                "w_price": float(c[wi]), "w_rsi": float(rv[wi]),
                "x_price": float(c[xi]), "x_rsi": float(rv[xi]),
                "y_price": float(c[yi]),
                "target": float(target),
            }
    return None


def negative_reversal(close: pd.Series, r: pd.Series,
                      max_bars_ago: int = MAX_SIGNAL_AGE,
                      max_span: int = MAX_PIVOT_SPAN):
    """
    שיא גבוה יותר ב-RSI מול שיא נמוך יותר במחיר — איתות דובי.

    היעד לפי נספח D: המחיר ב-C פחות (המחיר ב-A פחות המחיר ב-B),
    כאשר A הוא השיא הראשון ב-RSI, C הוא השפל ב-RSI שאחריו,
    ו-B הוא השיא הבא.
    """
    lows, highs = find_pivots(r)
    if len(highs) < 2:
        return None
    c = close.to_numpy()
    rv = r.to_numpy()
    last = len(c) - 1

    for bi in reversed(highs):
        if last - bi > max_bars_ago:
            break
        for ai in reversed([p for p in highs if p < bi and bi - p <= max_span]):
            if rv[bi] <= rv[ai]:
                continue
            if c[bi] >= c[ai]:
                continue
            between = [p for p in lows if ai < p < bi]
            if not between:
                continue
            ci = min(between, key=lambda p: rv[p])
            target = c[ci] - (c[ai] - c[bi])
            if not _plausible_target(target, c[last]):
                continue
            return {
                "bars_ago": int(last - bi),
                "a_price": float(c[ai]), "b_price": float(c[bi]),
                "c_price": float(c[ci]),
                "target": float(target),
            }
    return None


# ---------------------------------------------------------------------------
# סיכום לכל מניה
# ---------------------------------------------------------------------------
def reversal_history(close: pd.Series, r: pd.Series, horizon_cap: int = 250):
    """
    כמה זמן לוקח ל-Positive Reversal של המניה הזו להגיע ליעד שלו.

    הפונקציה סורקת את כל ההיסטוריה הזמינה, מוצאת כל תבנית שורית, ובודקת אם
    המחיר אכן הגיע ליעד שהנוסחה של בראון חישבה ותוך כמה ימי מסחר. ההנחה היא
    שהעסקה נפתחת רק כשהציר מאושר, כלומר חמישה נרות אחרי השפל עצמו, כי לפני כן
    אי אפשר לדעת שזה היה שפל. תבניות צעירות מדי מכדי להיסגר או להיכשל
    אינן נספרות, כדי לא לנפח את אחוז ההצלחה.

    זו סטטיסטיקה לאחור על המניה הבודדת, לא תחזית.
    """
    lows, highs = find_pivots(r)
    if len(lows) < 2:
        return {"n": 0, "hits": 0, "median_bars": None}

    c = close.to_numpy()
    rv = r.to_numpy()
    n = len(c)
    outcomes = []

    for xi in lows:
        match = None
        for wi in reversed([p for p in lows if p < xi and xi - p <= MAX_PIVOT_SPAN]):
            if rv[xi] >= rv[wi] or c[xi] <= c[wi]:
                continue
            between = [p for p in highs if wi < p < xi]
            if not between:
                continue
            yi = max(between, key=lambda p: rv[p])
            target = (c[xi] - c[wi]) + c[yi]
            if not _plausible_target(target, c[xi]):
                continue
            match = target
            break
        if match is None:
            continue

        entry_idx = xi + PIVOT_WINDOW          # הציר מאושר רק כאן
        if entry_idx >= n or match <= c[entry_idx]:
            continue

        forward = c[entry_idx:min(n, entry_idx + horizon_cap + 1)]
        reached = np.where(forward >= match)[0]
        if len(reached):
            outcomes.append(int(reached[0]))
        elif (n - entry_idx) > horizon_cap:
            outcomes.append(None)              # נכשל בתוך חלון הזמן
        # אחרת התבנית עדיין פתוחה, ולא סופרים אותה כלל

    hits = [b for b in outcomes if b is not None]
    return {
        "n": len(outcomes),
        "hits": len(hits),
        "median_bars": int(np.median(hits)) if hits else None,
    }


def structural_stop(close: pd.Series, low: pd.Series, a: pd.Series,
                    pos: dict | None) -> float | None:
    """
    הרמה שבה התבנית מתבטלת. בראון חוזרת ומדגישה שהסטופ נכנס באותו רגע
    שבו נכנסת הפקודה, ולכן הוא נגזר מהמבנה של התבנית ולא מאחוז שרירותי:
    השפל הנמוך ביותר מאז הציר שיצר את האיתות, פחות חצי ATR כמרווח רעש.
    """
    bars = 20
    if pos and isinstance(pos.get("bars_ago"), int):
        bars = max(10, min(60, pos["bars_ago"] + PIVOT_WINDOW))
    window = low.iloc[-bars:]
    if window.empty:
        return None
    atr_now = float(a.iloc[-1]) if not math.isnan(a.iloc[-1]) else 0.0
    return float(window.min()) - 0.5 * atr_now


def summarize(ticker: str, hist: pd.DataFrame) -> dict:
    close = hist["Close"].astype(float)
    high = hist["High"].astype(float)
    low = hist["Low"].astype(float)

    r = rsi(close)
    do = derivative_oscillator(close)
    a = atr(high, low, close)
    adx_series = adx(high, low, close)
    sma200 = close.rolling(200).mean()
    sma50 = close.rolling(50).mean()

    price = float(close.iloc[-1])
    rsi_now = float(r.iloc[-1]) if not math.isnan(r.iloc[-1]) else float("nan")

    regime, confidence, price_test, rsi_test = detect_regime(close, r)
    zone, bands = rsi_zone(rsi_now, regime)

    do_now = float(do.iloc[-1]) if len(do.dropna()) else float("nan")
    do_prev = float(do.iloc[-4]) if len(do.dropna()) > 4 else float("nan")
    do_dir = ""
    if not math.isnan(do_now) and not math.isnan(do_prev):
        do_dir = "עולה" if do_now > do_prev else "יורדת"

    do_cross = ""
    dov = do.dropna()
    if len(dov) > 2:
        signs = np.sign(dov.to_numpy())
        last = signs[-1]
        bars = 0
        for s in reversed(signs[:-1]):
            if s == last:
                bars += 1
            else:
                break
        do_cross = f"{bars + 1}"

    pos = positive_reversal(close, r)
    neg = negative_reversal(close, r)

    atr_pct = float(a.iloc[-1]) / price * 100.0 if price else float("nan")
    adx_now = float(adx_series.iloc[-1]) if not math.isnan(adx_series.iloc[-1]) else float("nan")
    sma200_now = float(sma200.iloc[-1]) if not math.isnan(sma200.iloc[-1]) else float("nan")
    dist200 = (price / sma200_now - 1.0) * 100.0 if sma200_now and not math.isnan(sma200_now) else float("nan")
    sma50_now = float(sma50.iloc[-1]) if not math.isnan(sma50.iloc[-1]) else float("nan")

    # ----- אופק זמן, סטופ ויחס סיכון לתשואה -----
    hist_stats = reversal_history(close, r)
    stop = structural_stop(close, low, a, pos)
    stop_pct = ((stop / price - 1.0) * 100.0) if (stop and 0 < stop < price) else None

    rr = None
    if pos and stop and 0 < stop < price:
        reward = pos["target"] - price
        risk = price - stop
        if reward > 0 and risk > 0:
            rr = reward / risk

    median_bars = hist_stats["median_bars"]
    horizon_weeks = round(median_bars / 5.0, 1) if median_bars else ""

    row = {
        "ticker": ticker,
        "price": round(price, 2),
        "rsi": round(rsi_now, 1) if not math.isnan(rsi_now) else "",
        "regime": {"bull": "שורי", "bear": "דובי", "transition": "מעבר"}.get(regime, ""),
        "regime_key": regime,
        "regime_confidence": confidence,
        "rsi_zone": zone,
        "band_support": f"{bands['support_lo']:.0f}-{bands['support_hi']:.0f}" if bands else "",
        "band_resistance": f"{bands['resist_lo']:.0f}-{bands['resist_hi']:.0f}" if bands else "",
        "regime_shift": detect_regime_shift(r),
        "deriv_osc": round(do_now, 3) if not math.isnan(do_now) else "",
        "deriv_dir": do_dir,
        "deriv_bars_since_cross": do_cross,
        "pos_rev_bars_ago": pos["bars_ago"] if pos else "",
        "pos_rev_target": round(pos["target"], 2) if pos else "",
        "pos_rev_upside_pct": round((pos["target"] / price - 1.0) * 100.0, 1) if pos else "",
        "neg_rev_bars_ago": neg["bars_ago"] if neg else "",
        "neg_rev_target": round(neg["target"], 2) if neg else "",
        "neg_rev_downside_pct": round((neg["target"] / price - 1.0) * 100.0, 1) if neg else "",
        "atr_pct": round(atr_pct, 2) if not math.isnan(atr_pct) else "",
        "adx": round(adx_now, 1) if not math.isnan(adx_now) else "",
        "sma200": round(sma200_now, 2) if not math.isnan(sma200_now) else "",
        "dist_sma200_pct": round(dist200, 1) if not math.isnan(dist200) else "",
        "above_sma50": "" if math.isnan(sma50_now) else ("כן" if price > sma50_now else "לא"),
        "direction": "לונג",
        "stop_price": round(stop, 2) if stop and stop > 0 else "",
        "stop_pct": round(stop_pct, 1) if stop_pct is not None else "",
        "risk_reward": round(rr, 2) if rr else "",
        "hist_patterns": hist_stats["n"],
        "hist_hits": hist_stats["hits"],
        "hist_hit_rate": round(100.0 * hist_stats["hits"] / hist_stats["n"], 0)
                         if hist_stats["n"] else "",
        "hist_median_bars": median_bars if median_bars else "",
        "horizon_weeks": horizon_weeks,
    }
    row["signal"], row["signal_rank"], row["signal_kind"] = classify_signal(row)
    return row


def classify_signal(row: dict):
    """
    סיווג מצומצם: משטר, מיקום ב-RSI, ומומנטום. זהו.

    הגרסה הקודמת שקללה גם יעדי מחיר, אוסילטור נגזר וסטופ. הם הוסרו מפני
    שהם שייכים לאופק של שבועות, והאופק כאן הוא שנתיים - גראהם עצמו הגדיר
    כלל מכירה של יעד או שנתיים, המוקדם מביניהם. שכבה טכנית באופק כזה
    אמורה לענות על שאלה אחת בלבד: האם המגמה נגדי כרגע, או לא.

    המומנטום הוא של 12 חודשים בהשמטת החודש האחרון, ומגיע משלב האיכות.
    """
    regime = row.get("regime_key")
    zone = row.get("rsi_zone", "")

    try:
        rsi_now = float(row.get("rsi"))
    except (TypeError, ValueError):
        rsi_now = None
    try:
        mom = float(_clean(row.get("momentum_12_1")))
    except (TypeError, ValueError):
        mom = None

    red = str(_clean(row.get("red_flag", ""))).strip()
    if red:
        return f"נפסל במבחן {red}", 6, "פסילה"

    # בראון מקבלת ירידה עד 38-39 כמבחן של אזור התמיכה השורי. מתחת לזה
    # התעלה נשברה.
    broke = (regime == "bull" and rsi_now is not None and rsi_now < TRANSITION_FLOOR)
    at_support = (regime == "bull" and not broke
                  and zone in ("על התמיכה", "מתחת לתמיכה"))
    mom_positive = mom is not None and mom > 0

    if broke:
        signal = ("שבר את תעלת השורי", 5, "המתנה")
    elif at_support and mom_positive:
        signal = ("אזור כניסה", 1, "כניסה")
    elif at_support:
        signal = ("בתמיכה, מומנטום שלילי", 3, "המתנה")
    elif regime == "bull" and mom_positive:
        signal = ("מגמה תקינה", 2, "החזקה")
    elif regime == "bull":
        signal = ("שורי אך מומנטום שלילי", 4, "החזקה")
    elif regime == "transition":
        signal = ("מעבר, לא ברור", 7, "המתנה")
    else:
        signal = ("מגמה נגדית", 8, "המתנה")

    return _block_entry(signal, row)


def _block_entry(signal, row: dict):
    """שני חסמים שמורידים איתות כניסה להמתנה, בלי לשנות דבר אחר.

    נזילות: אפשר שהתמונה מושלמת, אבל אם המניה נסחרת בכמה מאות אלפי דולרים
    ביום, פער הקנייה-מכירה יבלע חלק מהתשואה והיציאה תהיה קשה בדיוק ביום
    שבו תרצה לצאת.

    דוחות: כניסה יומיים לפני פרסום דוח אינה החלטה טכנית אלא הימור על
    תוצאה שאיש אינו יודע. המתנה של כמה ימים לא עולה כמעט כלום כשהאופק
    הוא שנתיים.
    """
    label, order, kind = signal
    if kind != "כניסה":
        return signal

    if str(_clean(row.get("liquidity_flag", ""))).strip():
        return "כניסה, אך דליל למסחר", 3, "המתנה"

    days = _days_to_earnings(row.get("next_earnings"))
    if days is not None and 0 <= days <= EARNINGS_BLACKOUT_DAYS:
        return f"כניסה אחרי הדוח ({days} ימים)", 3, "המתנה"

    return signal


def _days_to_earnings(value):
    text = str(_clean(value) or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            when = datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
        return (when - date.today()).days
    return None


# ---------------------------------------------------------------------------
# בחירת המניות מהסריקה הערכית
# ---------------------------------------------------------------------------
def select_tickers(df: pd.DataFrame, mode: str) -> pd.DataFrame:
    # קלט שהגיע מריצת בדיקה של שלב האיכות אינו נושא את עמודות גראהם. במקרה
    # כזה אין מה לסנן, וכל השורות עוברות הלאה.
    needed = {"error", "score", "max_score", "score_ent", "max_score_ent"}
    if not needed.issubset(df.columns):
        out = df.copy()
        out["graham_mode"] = out.get("graham_mode", "")
        return out

    df = df[df["error"].isna() | (df["error"].astype(str).str.strip() == "")].copy()

    for col in ("score", "max_score", "score_ent", "max_score_ent"):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    passes_def = df["score"] >= df["max_score"]
    passes_ent = df["score_ent"] >= df["max_score_ent"]

    if mode == "defensive":
        keep = passes_def
    elif mode == "enterprising":
        keep = passes_ent
    else:
        keep = passes_def | passes_ent

    out = df[keep.fillna(False)].copy()
    out["graham_mode"] = np.where(
        passes_def[keep.fillna(False)] & passes_ent[keep.fillna(False)], "מגן ויוזם",
        np.where(passes_def[keep.fillna(False)], "מגן", "יוזם"))
    return out


def main():
    ap = argparse.ArgumentParser(description="שלב טכני משלים לסורק גראהם")
    ap.add_argument("--in", dest="infile", default="quality_results.csv",
                    help="קובץ התוצאות של שלב האיכות, או של הסריקה הערכית")
    ap.add_argument("--out", default="tech_results.csv")
    ap.add_argument("--mode", choices=["both", "defensive", "enterprising"], default="both",
                    help="אילו עוברי גראהם לקחת")
    ap.add_argument("--tickers", default="",
                    help="רשימת טיקרים מופרדת בפסיקים, לעקיפת הקלט לצורך בדיקה")
    ap.add_argument("--sleep", type=float, default=0.2)
    args = ap.parse_args()

    if args.tickers.strip():
        base = pd.DataFrame({
            "ticker": [t.strip().upper() for t in args.tickers.split(",") if t.strip()],
        })
        base["name"] = base["ticker"]
        base["sector"] = ""
        base["graham_mode"] = "בדיקה"
        base["score"] = ""
        base["max_score"] = ""
        base["fscore"] = ""
        base["margin_of_safety"] = ""
        base["graham_number"] = ""
    else:
        if not os.path.exists(args.infile):
            print(f"לא נמצא קובץ הקלט: {args.infile}", file=sys.stderr)
            return 1
        raw = pd.read_csv(args.infile)
        base = select_tickers(raw, args.mode)
        print(f"נבחרו {len(base)} מניות שעברו את גראהם מתוך {len(raw)} בקובץ.")

    if base.empty:
        print("אין מניות לעיבוד.")
        pd.DataFrame().to_csv(args.out, index=False)
        return 0

    rows = []
    total = len(base)
    for i, (_, src) in enumerate(base.iterrows(), start=1):
        ticker = str(src["ticker"]).strip().upper()
        try:
            hist = yf.Ticker(ticker).history(period=HISTORY_PERIOD, interval="1d",
                                             auto_adjust=False)
            if hist is None or hist.empty or len(hist) < 220:
                print(f"[{i}/{total}] {ticker}: אין מספיק היסטוריה")
                continue
            row = summarize(ticker, hist)
        except Exception as exc:  # noqa: BLE001
            print(f"[{i}/{total}] {ticker}: שגיאה — {exc}")
            continue

        row["name"] = _clean(src.get("name", ""))
        row["sector"] = _clean(src.get("sector", ""))
        row["graham_mode"] = _clean(src.get("graham_mode", ""))
        row["graham_score"] = _clean(src.get("score", ""))
        row["graham_max"] = _clean(src.get("max_score", ""))
        row["fscore"] = _clean(src.get("fscore", ""))
        row["margin_of_safety"] = _clean(src.get("margin_of_safety", ""))
        row["graham_number"] = _clean(src.get("graham_number", ""))

        # שדות שלב האיכות, אם הקלט הגיע ממנו
        for field in ("quality_score", "beneish_m", "beneish_flag", "altman_z",
                      "altman_flag", "ebit_ev", "gross_profitability",
                      "momentum_12_1", "net_payout_yield", "red_flag",
                      "accruals", "accruals_flag", "liquidity_flag",
                      "dollar_volume", "next_earnings", "rank_bucket",
                      "rank_basis", "data_source"):
            row[field] = _clean(src.get(field, ""))

        # הסיווג נקבע מחדש אחרי המיזוג, כדי שדגל אדום יוכל לפסול איתות כניסה
        row["signal"], row["signal_rank"], row["signal_kind"] = classify_signal(row)
        rows.append(row)
        print(f"[{i}/{total}] {ticker}: {row['signal']} | RSI {row['rsi']} "
              f"({row['regime']}, {row['rsi_zone']})")
        time.sleep(args.sleep)

    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values(["signal_rank", "ticker"]).reset_index(drop=True)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    out.to_csv(args.out, index=False)
    print(f"\nנכתבו {len(out)} שורות אל {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
