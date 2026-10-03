"""
הדמיה מקומית של השקעה ב-S&P 500 (SPY), במקום חשבון paper נפרד באלפקה (בוטל, ספט' 2026).

קונים SPY בכל הסכום (כולל שבר מניה) בסגירה של יום ההתחלה ומחזיקים. הנרות מאלפקה מותאמים
לפיצולים ולדיבידנדים (adjustment=all), כך שהתשואה כוללת דיבידנדים כאילו הושקעו מחדש. יום
ההתחלה והסכום נשמרים ב-spy_sim.json (לא ב-git), כדי שההשוואה לא תזוז.

פונקציות טהורות בלבד - הנרות והמחיר החי מגיעים מ-app.py. test_spy_sim.py בודק.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional, Tuple

CASH = 10_000.0

# קרנות הסל שמוצגות בהדמיה, באותו סכום ומאותו יום. SPY ראשונה ומזהה השורה שלה
# נשאר spy_sim, כי הסיכום היומי בטלגרם מסתמך עליה.
FUNDS = [
    ("SPY", "S&P 500"),
    ("VT", "כל העולם"),
    ("PPA", "ביטחוניות"),
    ("QQQ", "נאסד\"ק 100"),
]


def row_id(sym: str) -> str:
    return "spy_sim" if sym == "SPY" else f"sim_{sym.lower()}"


def load_settings(path: Path) -> dict:
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(path: Path, d: dict) -> None:
    Path(path).write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


def simulate(closes: List[Tuple[str, float]], start: str, cash: float = CASH,
             live: Optional[float] = None, today: Optional[str] = None) -> dict:
    """closes: [(יום ISO, סגירה)] בסדר עולה. live: מחיר עכשיו (לא חובה).
    today: היום (ISO) לחישוב השינוי היומי מול הסגירה הקודמת."""
    pts = [(d, float(c)) for d, c in closes if d >= start and c and c > 0]
    if not pts:
        return {"points": [], "equity": cash, "start_value": cash, "ret": 0.0, "maxdd": 0.0,
                "shares": 0.0, "since": None, "day_change": 0.0, "price": live}
    shares = cash / pts[0][1]
    points = [[d, round(shares * c, 2)] for d, c in pts]
    price = live if live and live > 0 else pts[-1][1]
    equity = shares * price
    peak, mdd = 0.0, 0.0
    for v in [p[1] for p in points] + [equity]:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    today = today or pts[-1][0]
    before = [p[1] for p in points if p[0] < today]
    prev = before[-1] if before else cash
    return {"points": points, "equity": round(equity, 2), "start_value": cash,
            "ret": equity / cash - 1, "maxdd": mdd, "shares": shares, "since": pts[0][0],
            "day_change": round(equity - prev, 2), "price": price}