"""
קווי מגמה כנוסחה, בלי שרטוט ביד.

קו מגמה עולה (תמיכה) הוא הישר הנמוך ביותר שנשען על השפל של המגמה ושאף שפל
אחריו לא יורד מתחתיו - המעטפת הקמורה התחתונה של השפלים. לשפל העוגן A (הנמוך
ביותר בחלון) ולכל יום j אחריו השיפוע הוא (L[j] - L[A]) / (j - A); הקו הוא זה
עם השיפוע הקטן ביותר:

    slope = min_j (L[j] - L[A]) / (j - A)       line(t) = L[A] + slope * (t - A)

כך הקו נוגע בשני שפלים לפחות (A ו-j*), וכל השפלים שבין A להיום נמצאים עליו או
מעליו - בדיוק "קו שמחבר את השפלים" מהקורס (שיעור 7), אבל חד-משמעי.

קו מגמה יורד (התנגדות) הוא המראה: המעטפת העליונה של השיאים משיא עוגן,
    slope = max_j (H[j] - H[P]) / (j - P).

במה הקורס משתמש בקו, ומה מחושב כאן:
  * עוצמת המגמה = שיפוע הקו, באחוזים ליום מהמחיר (קו תלול = מגמה חזקה).
  * מספר הנגיעות = כמה שפלים במרחק עד 0.25 ATR מהקו (יותר נגיעות = קו משמעותי).
  * מרחק המחיר מהקו ביחידות ATR; שבירה בשער סגירה = סגירה מתחת לקו.
  * פריצת קו מגמה יורד קצר בתוך התיקון = טריגר כניסה.

המימוש לא מציץ קדימה: הקו ביום s נבנה רק מנתונים עד s-gap.
"""

from __future__ import annotations

import numpy as np


def support_line(low: np.ndarray, s: int, lookback: int = 120, gap: int = 3,
                 min_span: int = 5):
    """קו תמיכה עולה ביום s. מחזיר (A, slope) או None.

    העוגן: השפל הנמוך ביותר ב-[s-lookback, s-gap-min_span]. הקו נבנה מהשפלים
    עד s-gap בלבד, כדי שהירידה של הימים האחרונים תוכל "לשבור" אותו.
    """
    lo = max(0, s - lookback)
    hi = s - gap
    if hi - lo < 2 * min_span:
        return None
    seg = low[lo:hi - min_span + 1]
    if np.all(np.isnan(seg)):
        return None
    a = lo + int(np.nanargmin(seg))
    js = np.arange(a + min_span, hi + 1)
    if len(js) == 0:
        return None
    ys = low[js]
    ok = ~np.isnan(ys)
    if not ok.any():
        return None
    slopes = (ys[ok] - low[a]) / (js[ok] - a)
    return a, float(slopes.min())


def resistance_line(high: np.ndarray, p: int, s: int, min_span: int = 2):
    """קו התנגדות יורד משיא העוגן p עד יום s (כולל). מחזיר slope או None."""
    js = np.arange(p + min_span, s + 1)
    if len(js) == 0:
        return None
    ys = high[js]
    ok = ~np.isnan(ys)
    if not ok.any():
        return None
    return float(((ys[ok] - high[p]) / (js[ok] - p)).max())


def line_at(y0: float, slope: float, x0: int, t: int) -> float:
    return y0 + slope * (t - x0)


def touches(low: np.ndarray, a: int, slope: float, end: int, tol: float) -> int:
    """כמה שפלים (ימים) נגעו בקו: במרחק עד tol מעליו, מהעוגן עד end."""
    t = np.arange(a, end + 1)
    d = low[a:end + 1] - (low[a] + slope * (t - a))
    return int(np.nansum(d <= tol))


def features(high, low, close, atr, s: int, lookback: int = 120, gap: int = 3):
    """מאפייני קו המגמה העולה ביום s, למניה אחת (מערכים חד-ממדיים)."""
    r = support_line(low, s, lookback, gap)
    if r is None or not (atr[s] > 0) or not (close[s] > 0):
        return None
    a, slope = r
    lvl = line_at(low[a], slope, a, s)
    return {
        "slope_pct": slope / close[s] * 100,            # עוצמה: % ליום
        "dist_atr": (close[s] - lvl) / atr[s],          # מרחק מהקו
        "broken": close[s] < lvl,                       # שבירה בסגירה
        "touches": touches(low, a, slope, s - gap, 0.25 * atr[s]),
        "age": s - a,
    }
