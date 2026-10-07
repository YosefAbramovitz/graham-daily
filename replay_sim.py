"""
הדמיה לאחור "מה היה אילו": שני חשבונות (סווינג וגראהם) מקבלים 10,000 ש"ח ביום ההתחלה,
ועוברים יום מסחר אחרי יום עד היום, לפי אותם כללים שהמסך עובד לפיהם (plans.py, swing.py).

סווינג (swing.py):
  * אותות בסגירה של יום X (מגמה עולה, נזילות, RSI(14) מתחת ל-35, SPY מעל ממוצע 200)
    נקנים בפתיחה של יום המסחר הבא, לפי דירוג המומנטום, עד 15 פוזיציות.
  * סטופ 4 ATR (ה-ATR של יום האות) מתחת לכניסה. פתיחה מתחת לסטופ = יציאה בפתיחה,
    שפל מתחת לסטופ = יציאה בסטופ. אחרת יציאה בסגירה של יום המסחר ה-20 (כולל יום הכניסה).
  * גודל: סיכון לפי ציון האיכות (0/0.25/0.5/1% מההון לציון 0/1/2/3, ציון 0 לא נקנה)
    חלקי המרחק לסטופ, עד 20% מההון לפוזיציה ועד המזומן.
  * עמלה+מרווח 0.1% לכל צד, כמו ב-swing_sim.py.

גראהם (plans.plan_graham):
  * הרשימה היא tech_results.csv כפי שנשמר ב-git באותו בוקר (השלב הטכני רץ לפני פתיחת
    המסחר). כל המניות שלא נפסלו, הזולות קודם (value_rank), עד 15 פוזיציות, כל אחת
    1/15 מההון, קנייה בפתיחה.
  * יציאה ביעד +50% (פתיחה מעל היעד = בפתיחה, גבוה מעל היעד = ביעד), או שנה אחרי הקנייה
    (app.HOLD_DAYS) בפתיחה. בלי סטופ.

שברי מניות מותרים בשני החשבונות: ב-10,000 ש"ח (כ-2,700$) מניות שלמות היו מעוותות את
הגודל שהכלל קובע. הכסף מתורגם לדולרים בשער של יום ההתחלה, והשווי מוצג בשקלים בשער של
כל יום (כלומר כולל השפעת הדולר).

אזהרות: רשימת המניות לסווינג היא S&P 1500 של היום (הטיית שורדים), והנרות הם מותאמים
לפיצולים ודיבידנדים. זו הדמיה, לא עסקאות שבוצעו, ולא המלצה.

פונקציות טהורות בלבד - הנרות, הרשימות והשער מגיעים מ-app.py. test_replay_sim.py בודק.
"""
from __future__ import annotations

import csv
import io
import math
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import swing

CASH_ILS = 10_000.0
DEFAULT_START = "2026-09-25"
COST = 0.0010                 # עמלה+מרווח לכל צד, כמו swing_sim.py
MAX_GAP = 0.20                # כמו plans.MAX_GAP: פתיחה רחוקה מסגירת האות = תקלת נתונים
GRAHAM_SLOTS = 15
GRAHAM_TARGET = 0.50
GRAHAM_HOLD_DAYS = 365        # כמו app.HOLD_DAYS
MARKET_OPEN_UTC = (13, 30)    # רשימה שנשמרה לפני זה זמינה לקנייה באותו יום


def _num(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


def _value(pos: dict, px: Optional[float]) -> float:
    return pos["shares"] * (px if px else pos["last"])


def _stats(points: List[list], start_value: float) -> dict:
    peak, mdd = 0.0, 0.0
    for _, v in points:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1) if peak else mdd
    end = points[-1][1] if points else start_value
    return {"equity": round(end, 2), "ret": end / start_value - 1 if start_value else 0.0,
            "maxdd": mdd}


def _result(points, trades, pos, cash, start_value, extra=None) -> dict:
    out = {"points": points, "trades": trades, "cash": round(cash, 2),
           "start_value": start_value,
           "positions": [{"ticker": t, "entry_day": p["day"], "entry": round(p["entry"], 4),
                          "shares": round(p["shares"], 6), "last": round(p["last"], 4),
                          "value": round(p["shares"] * p["last"], 2),
                          "ret": p["last"] / p["entry"] - 1,
                          **({"stop": round(p["stop"], 2), "exit_day": p["exit_day"]}
                             if "stop" in p else {"target": round(p["target"], 2),
                                                  "exit_day": p["exit_day"]})}
                         for t, p in sorted(pos.items())],
           "buys": sum(1 for t in trades if t["side"] == "buy"),
           "sells": sum(1 for t in trades if t["side"] == "sell")}
    out.update(_stats(points, start_value))
    out.update(extra or {})
    return out


def _sell(trades, day, t, p, px, reason) -> float:
    """רושם מכירה ומחזיר את התמורה (אחרי עלות)."""
    got = p["shares"] * px * (1 - COST)
    cost = p["shares"] * p["entry"] * (1 + COST)
    trades.append({"day": day, "side": "sell", "ticker": t, "shares": round(p["shares"], 6),
                   "price": round(px, 4), "value": round(got, 2), "reason": reason,
                   "pnl": round(got - cost, 2), "ret": got / cost - 1 if cost else 0.0,
                   "entry_day": p["day"]})
    return got


# ---------------------------------------------------------------------------
# סווינג
# ---------------------------------------------------------------------------

def swing_replay(O, H, L, C, V, start: str, cash: float, end: Optional[str] = None,
                 spy: str = "SPY") -> dict:
    """O/H/L/C/V: טבלאות pandas, שורה ליום ועמודה לסימול, עם היסטוריה לפני start
    (ממוצע 200 צריך כ-10 חודשים). כסף נכנס ביום המסחר הראשון מ-start."""
    import pandas as pd
    ind = swing.indicators(C, H, L, V)
    sig = (ind["liquid"] & ind["uptrend"] & (ind["rsi"] < swing.RSI_MAX)).fillna(False)
    q = swing.quality(ind)
    mk = swing.market_ok(C[spy].ffill()) if spy in C.columns else None
    idx = list(C.index)
    s0 = pd.Timestamp(start)
    s1 = pd.Timestamp(end) if end else idx[-1]
    days = [i for i, d in enumerate(idx) if s0 <= d <= s1]
    start_value = cash
    pos: Dict[str, dict] = {}
    trades, points = [], []
    names = [c for c in C.columns if c != spy]
    Ov, Hv, Lv, Cv = (x.to_numpy() for x in (O, H, L, C))
    col = {c: k for k, c in enumerate(C.columns)}
    sigv, qv, atrv, momv = (sig.to_numpy(), q.to_numpy(), ind["atr"].to_numpy(),
                            ind["mom"].to_numpy())
    prev_eq = cash

    for i in days:
        day = idx[i].date().isoformat()
        busy = set(pos)               # מה שהוחזק בבוקר לא נקנה שוב באותו יום (כמו plans.busy)
        # 1. פתיחה: סטופ שנפתח מתחתיו (רק לפוזיציות מימים קודמים)
        for t in list(pos):
            p, k = pos[t], col[t]
            o = Ov[i, k]
            if o == o and o > 0 and o <= p["stop"]:
                cash += _sell(trades, day, t, p, o, "פתיחה מתחת לסטופ")
                del pos[t]
        # 2. כניסות: אותות מסגירת אתמול, לפי המומנטום, לפי ההון בסגירת אתמול
        s = i - 1
        market = s >= 0 and (mk is None or bool(mk.iloc[s]))
        if s >= 0 and market:
            cand = [c for c in names if sigv[s, col[c]] and c not in busy]
            cand.sort(key=lambda c: -(momv[s, col[c]] if momv[s, col[c]] == momv[s, col[c]] else -9))
            for c in cand:
                if len(pos) >= swing.MAX_POSITIONS:
                    break
                k = col[c]
                o, a, cl = Ov[i, k], atrv[s, k], Cv[s, k]
                quality = int(qv[s, k])
                if not (o == o and o > 0 and a == a and a > 0 and cl > 0):
                    continue
                if abs(o / cl - 1) > MAX_GAP:
                    continue
                stop = o - swing.STOP_ATR * a
                risk = prev_eq * swing.QUALITY_RISK.get(quality, 0.0)
                if risk <= 0 or stop <= 0:
                    continue
                amt = min(risk / (o - stop) * o, prev_eq * swing.MAX_POSITION_PCT,
                          cash / (1 + COST))
                if amt < 1:
                    continue
                cash -= amt * (1 + COST)
                pos[c] = {"shares": amt / o, "entry": o, "stop": stop, "day": day, "last": o,
                          "quality": quality, "i": i,
                          "exit_day": swing.exit_date(idx[i].date()).isoformat()}
                trades.append({"day": day, "side": "buy", "ticker": c, "shares": round(amt / o, 6),
                               "price": round(o, 4), "value": round(amt, 2), "stop": round(stop, 2),
                               "quality": quality, "reason": f"RSI {ind['rsi'].iat[s, k]:.0f}, ציון {quality}"})
        # 3. במהלך היום: שפל מתחת לסטופ. בסגירה: יום המסחר ה-20
        for t in list(pos):
            p, k = pos[t], col[t]
            lo, c = Lv[i, k], Cv[i, k]
            if lo == lo and lo <= p["stop"]:
                cash += _sell(trades, day, t, p, p["stop"], "סטופ")
                del pos[t]
                continue
            if c == c and c > 0:
                p["last"] = c
            if i - p["i"] + 1 >= swing.HOLD_DAYS:
                cash += _sell(trades, day, t, p, p["last"], f"יום {swing.HOLD_DAYS}")
                del pos[t]
        prev_eq = cash + sum(p["shares"] * p["last"] for p in pos.values())
        points.append([day, round(prev_eq, 2)])
    return _result(points, trades, pos, cash, start_value)


# ---------------------------------------------------------------------------
# גראהם
# ---------------------------------------------------------------------------

def parse_list(text: str) -> List[dict]:
    """שורות tech_results.csv: סימול, סוג האות ודירוג הזול (כמו app._parse_watch)."""
    rows = []
    for r in csv.DictReader(io.StringIO(text.lstrip("﻿"))):
        t = (r.get("ticker") or "").strip().upper()
        if t:
            rows.append({"ticker": t, "signal_kind": (r.get("signal_kind") or "").strip(),
                         "signal": (r.get("signal") or "").strip(),
                         "value_rank": _num(r.get("value_rank"))})
    return rows


def list_for(lists: List[Tuple[datetime, List[dict]]], day: date) -> Optional[List[dict]]:
    """הרשימה האחרונה שנשמרה לפני פתיחת המסחר של day. lists: [(זמן UTC, שורות)]."""
    cut = datetime(day.year, day.month, day.day, *MARKET_OPEN_UTC, tzinfo=timezone.utc)
    best = None
    for ts, rows in sorted(lists, key=lambda x: x[0]):
        if ts < cut:
            best = rows
    return best


def graham_replay(O, H, C, lists: List[Tuple[datetime, List[dict]]], start: str, cash: float,
                  end: Optional[str] = None, slots: int = GRAHAM_SLOTS) -> dict:
    import pandas as pd
    idx = list(C.index)
    s0 = pd.Timestamp(start)
    s1 = pd.Timestamp(end) if end else idx[-1]
    start_value = cash
    pos: Dict[str, dict] = {}
    trades, points = [], []
    prev_eq = cash
    first_list = None
    for d in idx:
        if not (s0 <= d <= s1):
            continue
        day = d.date().isoformat()
        px = lambda tab, t: _num(tab.at[d, t]) if t in tab.columns else None
        busy = set(pos)               # מה שהוחזק בבוקר לא נקנה שוב באותו יום (כמו plans.busy)
        # 1. פתיחה: יעד שנפתח מעליו, או שנה מהקנייה
        for t in list(pos):
            p = pos[t]
            o = px(O, t)
            if o and o >= p["target"]:
                cash += _sell(trades, day, t, p, o, "יעד +50% (בפתיחה)")
                del pos[t]
            elif o and day >= p["exit_day"]:
                cash += _sell(trades, day, t, p, o, "שנה מהקנייה")
                del pos[t]
        # 2. כניסות לפי הרשימה של הבוקר
        rows = list_for(lists, d.date())
        if rows is not None and first_list is None:
            first_list = day
        cand = [r for r in rows or [] if r["signal_kind"] != "פסילה"]
        cand.sort(key=lambda r: (r["value_rank"] is None, r["value_rank"] or 0))
        per = prev_eq / slots
        for r in cand:
            if len(pos) >= slots:
                break
            t = r["ticker"]
            o = px(O, t)
            if t in busy or t in pos or not o or o <= 0:
                continue
            amt = min(per, cash / (1 + COST))
            if amt < 1:
                break
            cash -= amt * (1 + COST)
            target = o * (1 + GRAHAM_TARGET)
            pos[t] = {"shares": amt / o, "entry": o, "target": target, "day": day, "last": o,
                      "exit_day": (d.date() + timedelta(days=GRAHAM_HOLD_DAYS)).isoformat()}
            trades.append({"day": day, "side": "buy", "ticker": t, "shares": round(amt / o, 6),
                           "price": round(o, 4), "value": round(amt, 2), "target": round(target, 2),
                           "reason": (r["signal"] or r["signal_kind"]) + (
                               f", דירוג זול {int(r['value_rank'])}" if r["value_rank"] else "")})
        # 3. במהלך היום: פקודת היעד מתבצעת. בסגירה: שווי
        for t in list(pos):
            p = pos[t]
            h = px(H, t)
            if h and h >= p["target"]:
                cash += _sell(trades, day, t, p, p["target"], "יעד +50%")
                del pos[t]
        for t, p in pos.items():
            c = px(C, t)
            if c and c > 0:
                p["last"] = c
        prev_eq = cash + sum(p["shares"] * p["last"] for p in pos.values())
        points.append([day, round(prev_eq, 2)])
    return _result(points, trades, pos, cash, start_value, {"first_list": first_list})


# ---------------------------------------------------------------------------
# שקלים
# ---------------------------------------------------------------------------

def fx_on(fx: Dict[str, float], day: str) -> Optional[float]:
    """שער הדולר ביום day, או ביום האחרון שלפניו שיש בו שער."""
    keys = [k for k in fx if k <= day]
    if keys:
        return fx[max(keys)]
    return fx[min(fx)] if fx else None


def in_ils(res: dict, fx: Dict[str, float], start_ils: float = CASH_ILS) -> dict:
    """מוסיף שווי בשקלים לכל נקודה ולסיכום, לפי השער של כל יום."""
    pts = [[d, round(v * fx_on(fx, d), 2)] for d, v in res["points"]]
    res = dict(res, points_ils=pts, start_ils=start_ils)
    if pts:
        st = _stats(pts, start_ils)
        res.update(equity_ils=st["equity"], ret_ils=st["ret"], maxdd_ils=st["maxdd"])
    return res
