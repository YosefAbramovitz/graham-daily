"""
תוכניות קנייה לכל חשבון, לחיצה אחת ביום עם אישור - בלי רשת, כדי שאפשר לבדוק.

  * סווינג: אותות הסגירה של אתמול, לפי הדירוג, 0.5% סיכון לעסקה (swing.size),
    עד 15 פוזיציות ועד המזומן. קנייה market עם סטופ צמוד ב-swing.STOP_ATR (4 ATR).
  * גראהם: המניות מהמסך שלא נפסלו, הזולות קודם (value_rank), עד 15 פוזיציות
    במשקל שווה (1/15 מהתיק), עם יעד מכירה +50%.
  * S&P 500: כבר לא חשבון באלפקה - הדמיה מקומית ב-spy_sim.py.
"""

from __future__ import annotations

import math
from typing import Dict, Iterable, List

import swing

MAX_GAP = 0.20          # מחיר רחוק מהסגירה של האות ביותר מזה = תקלת נתונים
GRAHAM_SLOTS = 15
GRAHAM_TARGET = 0.50


def plan_swing(rows: List[dict], equity: float, cash: float, prices: Dict[str, float],
               busy: Iterable[str], open_swing: int) -> List[dict]:
    busy = {s.upper() for s in busy}
    slots = max(0, swing.MAX_POSITIONS - open_swing)
    out, left = [], max(0.0, cash)
    for r in sorted(rows, key=lambda x: x.get("rank", 9999)):
        if len(out) >= slots:
            break
        sym = r["ticker"].upper()
        px = prices.get(sym)
        if sym in busy or not px or px <= 0 or abs(px / r["close"] - 1) > MAX_GAP:
            continue
        stop = round(px - r["stop_dist"], 2)
        if not 0 < stop < px:
            continue
        sz = swing.size(equity, px, stop, cash=left)
        if sz["qty"] < 1:
            continue
        left -= sz["qty"] * px
        out.append({"ticker": sym, "qty": sz["qty"], "price": round(px, 2), "stop": stop,
                    "risk": sz["risk"], "value": sz["value"], "quality": r.get("quality"),
                    "pct": round(sz["value"] / equity * 100, 1) if equity else None})
    return out


def swing_order(p: dict, client_id: str) -> dict:
    """market עם סטופ צמוד (OTO, GTC)."""
    return {"symbol": p["ticker"], "qty": str(int(p["qty"])), "side": "buy", "type": "market",
            "time_in_force": "gtc", "order_class": "oto",
            "stop_loss": {"stop_price": f"{p['stop']:.2f}"}, "client_order_id": client_id}


def plan_graham(rows: List[dict], equity: float, cash: float, prices: Dict[str, float],
                busy: Iterable[str], held: int, slots: int = GRAHAM_SLOTS) -> List[dict]:
    busy = {s.upper() for s in busy}
    free = max(0, slots - held)
    per = equity / slots if slots else 0.0
    cand = [r for r in rows if r.get("signal_kind") != "פסילה"]
    cand.sort(key=lambda r: (r.get("value_rank") is None, r.get("value_rank") or 0))
    out, left = [], max(0.0, cash)
    for r in cand:
        if len(out) >= free:
            break
        sym = r["ticker"].upper()
        px = prices.get(sym)
        if sym in busy or not px or px <= 0:
            continue
        qty = int(math.floor(min(per, left) / px))
        if qty < 1:
            continue
        limit = round(px * 1.005, 2)
        left -= qty * limit
        out.append({"ticker": sym, "qty": qty, "price": limit,
                    "target": round(limit * (1 + GRAHAM_TARGET), 2),
                    "value": round(qty * limit, 2), "value_rank": r.get("value_rank"),
                    "signal": r.get("signal"),
                    "pct": round(qty * limit / equity * 100, 1) if equity else None})
    return out

