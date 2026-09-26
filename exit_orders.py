"""
פקודות הכניסה והיציאה לאלפקה, והבדיקה שהן עדיין חיות.

שתי החלטות שיושבות כאן
----------------------
1. **אין סטופ כברירת מחדל.** אצל גראהם ירידת מחיר בלי שינוי בשווי היא סיבה
   לקנות, לא למכור. לכן הכניסה נשלחת כ-OTO: קנייה ויעד מכירה של +50%. סטופ
   נוסף רק כשמבקשים במפורש, ואז הכניסה היא bracket.

2. **פקודות GTC אינן נצחיות.** אלפקה מבטלת כל פקודת GTC תשעים יום אחרי
   שנוצרה (שדה ``expires_at`` בפקודה). אחזקה לפי גראהם נמשכת עד שנתיים,
   כלומר פקודת היציאה תפוג כמה פעמים לפני המועד. ``exit_state`` מזהה פוזיציה
   שאין לה פקודת יציאה פעילה, או שזו עומדת לפוג, כדי שאפשר יהיה לחדש אותה.

3. **שברי מניות רק בפקודת יום.** אלפקה מקבלת שבר מניה רק ב-time_in_force=day,
   ובלי OTO, bracket או OCO. לכן קנייה לפי סכום מתפצלת: המניות השלמות נכנסות
   לפקודה הרגילה עם יעד GTC, והשבר שנשאר נקנה בפקודת יום נפרדת, בלי פקודת
   יציאה. גם פקודת יציאה מחודשת מוכרת רק את המניות השלמות.

המודול לא שולח דבר לרשת; הוא רק בונה גופי בקשה ומפרש תשובות. כך אפשר לבדוק
אותו בלי מפתחות, והלוגיקה זהה בסקריפט ובמסך.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Iterable, List, Optional, Tuple

GTC_LIFETIME_DAYS = 90
EXPIRY_WARN_DAYS = 14       # כמה ימים לפני הפקיעה להתחיל להתריע
MIN_FRACTION_USD = 1.0      # אלפקה לא מקבלת פקודת שבר על פחות מדולר

EXPIRY_NOTE = (
    "  שים לב: אלפקה מבטלת פקודות GTC תשעים יום אחרי שנוצרו. פקודת היציאה תפוג\n"
    "  לפני מועד היציאה של גראהם. 'check' מתריע כשזה מתקרב, ו-'renew' מחדש אותה."
)


def _day(text) -> Optional[date]:
    if not text:
        return None
    raw = str(text).strip()
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(raw[:10])
        except ValueError:
            return None


# ---------------------------------------------------------------------------
# גופי בקשה
# ---------------------------------------------------------------------------

def entry_order_body(symbol: str, qty, entry: float, target: float,
                     stop: Optional[float] = None) -> dict:
    """קנייה ב-limit עם יעד מכירה. עם סטופ - bracket, בלעדיו - OTO."""
    body = {
        "symbol": symbol, "qty": str(qty), "side": "buy", "type": "limit",
        "limit_price": f"{entry:.2f}", "time_in_force": "gtc",
        "order_class": "bracket" if stop is not None else "oto",
        "take_profit": {"limit_price": f"{target:.2f}"},
    }
    if stop is not None:
        body["stop_loss"] = {"stop_price": f"{stop:.2f}"}
    return body


def exit_order_body(symbol: str, qty, target: float,
                    stop: Optional[float] = None) -> dict:
    """פקודת יציאה לפוזיציה קיימת. עם סטופ - OCO, בלעדיו - limit פשוט."""
    body = {"symbol": symbol, "qty": str(qty), "side": "sell", "type": "limit",
            "time_in_force": "gtc"}
    if stop is None:
        body["limit_price"] = f"{target:.2f}"
    else:
        body["order_class"] = "oco"
        body["take_profit"] = {"limit_price": f"{target:.2f}"}
        body["stop_loss"] = {"stop_price": f"{stop:.2f}"}
    return body


def split_amount(amount: float, price: float) -> Tuple[int, float]:
    """סכום בדולרים -> (מניות שלמות, שבר). שבר ששווה פחות מדולר נזרק."""
    if not amount or not price or amount <= 0 or price <= 0:
        return 0, 0.0
    shares = amount / price
    whole = int(math.floor(shares + 1e-9))
    frac = round(shares - whole, 6)
    if frac * price < MIN_FRACTION_USD:
        frac = 0.0
    return whole, frac


def whole_shares(qty) -> int:
    """החלק השלם של כמות, גם כשאלפקה מחזירה אותה כמחרוזת עם שבר."""
    try:
        return int(math.floor(float(qty) + 1e-9))
    except (TypeError, ValueError):
        return 0


def fraction_order_body(symbol: str, qty: float, entry: float) -> dict:
    """קניית שבר מניה: limit, יום בלבד, בלי יעד - אלפקה לא מאפשרת יותר מזה."""
    return {"symbol": symbol, "qty": f"{qty:.6f}".rstrip("0").rstrip("."),
            "side": "buy", "type": "limit", "limit_price": f"{entry:.2f}",
            "time_in_force": "day"}


# ---------------------------------------------------------------------------
# מצב פקודות היציאה
# ---------------------------------------------------------------------------

def flatten_orders(orders: Iterable[dict]) -> List[dict]:
    """פקודות מרובות-רגליים מגיעות לפעמים עם ``legs`` מקוננות. משטיח הכול."""
    out: List[dict] = []
    for o in orders or []:
        if not isinstance(o, dict):
            continue
        out.append(o)
        out.extend(flatten_orders(o.get("legs") or []))
    return out


def exit_orders_for(orders: Optional[Iterable[dict]], symbol: str) -> List[dict]:
    sym = symbol.strip().upper()
    return [o for o in flatten_orders(orders or [])
            if str(o.get("symbol", "")).upper() == sym and o.get("side") == "sell"]


def exit_state(orders: Optional[Iterable[dict]], symbol: str,
               today: Optional[date] = None) -> dict:
    """מצב פקודות היציאה הפתוחות של סימול אחד.

    ``state`` הוא אחד מ:
      * ``unknown``  - לא הצלחנו למשוך את רשימת הפקודות
      * ``none``     - אין שום פקודת מכירה פתוחה; היעד (והסטופ, אם היה) פגו
      * ``expiring`` - יש, אבל היא פוקעת בתוך EXPIRY_WARN_DAYS ימים
      * ``ok``       - יש, והיא חיה עוד זמן
    """
    if orders is None:
        return {"state": "unknown", "count": 0, "expires": None, "days_left": None}
    today = today or date.today()
    mine = exit_orders_for(orders, symbol)
    if not mine:
        return {"state": "none", "count": 0, "expires": None, "days_left": None}
    days = [d for d in (_day(o.get("expires_at")) for o in mine) if d]
    if not days:
        return {"state": "ok", "count": len(mine), "expires": None, "days_left": None}
    first = min(days)
    left = (first - today).days
    return {"state": "expiring" if left <= EXPIRY_WARN_DAYS else "ok",
            "count": len(mine), "expires": first.isoformat(), "days_left": left}


def describe_exit(state: dict) -> str:
    s = state.get("state")
    if s == "none":
        return "אין פקודת יציאה פעילה - לחדש"
    if s == "expiring":
        return f"פקודת היציאה פוקעת בעוד {state['days_left']} ימים - לחדש"
    if s == "ok":
        if state.get("days_left") is not None:
            return f"פקודת יציאה פעילה, פוקעת בעוד {state['days_left']} ימים"
        return "פקודת יציאה פעילה"
    return "לא ידוע אם יש פקודת יציאה"


def needs_renewal(state: dict) -> bool:
    return state.get("state") in ("none", "expiring")


__all__ = ["EXPIRY_NOTE", "EXPIRY_WARN_DAYS", "GTC_LIFETIME_DAYS", "describe_exit",
           "entry_order_body", "exit_order_body", "exit_orders_for", "exit_state",
           "flatten_orders", "needs_renewal"]
