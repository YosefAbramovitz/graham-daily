"""
מסך המסחר. רץ על המחשב שלך, נפתח בדפדפן.

למה כך ולא כפתור בדף הציבורי
----------------------------
כדי לשלוח פקודה צריך את המפתח. בדף ציבורי הוא היה גלוי לכל אדם ברשת.
כאן השרת רץ אצלך: הדפדפן מדבר עם 127.0.0.1, השרת המקומי מדבר עם אלפקה,
והמפתח לא עוזב את המחשב. השרת מאזין ל-localhost בלבד, כך שגם מחשבים
אחרים ברשת הביתית לא רואים אותו.

הרצה
----
    pip install flask requests
    python app.py

ואז לפתוח את http://127.0.0.1:5000 בדפדפן.

גישה מהטלפון, דרך Tailscale
---------------------------
    python app.py --tailscale

השרת מאזין אז גם מחוץ למחשב, אבל מקבל רק שני סוגי כתובות: המחשב עצמו,
וכתובות Tailscale (100.64.0.0/10). כל השאר מקבלים 403, גם אם הם באותה
רשת Wi-Fi. מעבר לזה נדרש טוקן: הוא נוצר בהפעלה הראשונה, נשמר ב-.env בשם
APP_TOKEN, והשרת מדפיס כתובת שמכילה אותו. פותחים אותה פעם אחת בטלפון,
והטוקן נשמר שם בעוגייה ל-30 יום. מהמחשב עצמו לא נדרש טוקן.

להחלפת הטוקן: למחוק את השורה APP_TOKEN מ-.env ולהפעיל מחדש.

מפתחות: קובץ ``.env`` בתיקייה הזאת, שתי שורות::

    ALPACA_API_KEY_ID=PK...
    ALPACA_API_SECRET_KEY=...

מה המסך עושה
------------
* מציג את המניות שעברו את המסך, עם המחיר הנוכחי
* מחשב כניסה, יעד, סטופ ומועד יציאה, ושולח פקודת bracket אחרי אישור
* עוקב אחרי הפוזיציות הפתוחות ומרענן בשעות המסחר

המעקב הוא תצוגה, לא מנוע. הסטופ עצמו יושב אצל אלפקה ומתבצע שם גם כשהמסך
סגור והמחשב כבוי - זו בדיוק הסיבה שלא בניתי לולאה שמוכרת מהמחשב.
"""

from __future__ import annotations

import csv
import hmac
import io
import ipaddress
import json
import os
import secrets
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests
from flask import Flask, jsonify, redirect, request, send_from_directory

from exit_orders import (entry_order_body, exit_order_body, exit_orders_for,
                         exit_state, flatten_orders)

HERE = Path(__file__).parent
PAPER_BASE = "https://paper-api.alpaca.markets"
LIVE_BASE = "https://api.alpaca.markets"
DATA_BASE = "https://data.alpaca.markets"

PROFIT_TARGET = 0.50
ATR_STOP_MULT = 2.0
HOLD_YEARS = 2
FALLBACK_STOP_PCT = 0.15
# כמה מעל המחיר בשוק מותר להציע בקנייה, לפני שהשרת מסרב (טעות הקלדה, מחיר ישן)
MAX_ENTRY_ABOVE_MARKET = 0.03

TECH_CSV = ("https://raw.githubusercontent.com/YosefAbramovitz/graham-daily/"
            "main/tech_results.csv")

app = Flask(__name__, static_folder=None)
LIVE = False          # נדרס מ---live בשורת ההפעלה
REMOTE = False        # נדרס מ---tailscale בשורת ההפעלה
TOKEN = ""
COOKIE = "gd_token"

# טווח הכתובות ש-Tailscale מחלק למכשירים (IPv4 ו-IPv6).
TAILNET = (ipaddress.ip_network("100.64.0.0/10"),
           ipaddress.ip_network("fd7a:115c:a1e0::/48"))


# ---------------------------------------------------------------------------
# גישה מרחוק
# ---------------------------------------------------------------------------

def _addr(value: str):
    try:
        ip = ipaddress.ip_address((value or "").split("%")[0])
    except ValueError:
        return None
    if getattr(ip, "ipv4_mapped", None):
        ip = ip.ipv4_mapped
    return ip


def is_local(value: str) -> bool:
    ip = _addr(value)
    return bool(ip and ip.is_loopback)


def is_tailnet(value: str) -> bool:
    ip = _addr(value)
    return bool(ip and any(ip in net for net in TAILNET))


def ensure_token() -> str:
    """הטוקן מ-.env, או טוקן חדש שנשמר שם כדי שיישאר קבוע בין הפעלות."""
    load_env()
    tok = os.environ.get("APP_TOKEN", "").strip()
    if tok:
        return tok
    tok = secrets.token_urlsafe(24)
    path = HERE / ".env"
    prev = path.read_text(encoding="utf-8") if path.exists() else ""
    sep = "" if (not prev or prev.endswith("\n")) else "\n"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(f"{sep}APP_TOKEN={tok}\n")
    os.environ["APP_TOKEN"] = tok
    return tok


def tailscale_ip() -> Optional[str]:
    # בווינדוס ההתקנה לא תמיד נכנסת ל-PATH, ולכן גם הנתיב הקבוע.
    for exe in ("tailscale", r"C:\Program Files\Tailscale\tailscale.exe"):
        try:
            out = subprocess.run([exe, "ip", "-4"], capture_output=True,
                                 text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            continue
        ip = (out.stdout or "").strip().splitlines()
        if ip:
            return ip[0].strip()
    return None


@app.before_request
def guard():
    """במצב מרוחק: רק המחשב עצמו או Tailscale, ומ-Tailscale רק עם טוקן."""
    if not REMOTE:
        return None
    who = request.remote_addr or ""
    if is_local(who):
        return None
    if not is_tailnet(who):
        return ("גישה רק מהמחשב עצמו או דרך Tailscale.", 403,
                {"Content-Type": "text/plain; charset=utf-8"})
    given = request.args.get("t")
    if given is not None:
        if hmac.compare_digest(given, TOKEN):
            resp = redirect(request.path)
            resp.set_cookie(COOKIE, TOKEN, max_age=30 * 24 * 3600,
                            httponly=True, samesite="Strict")
            return resp
        return ("טוקן שגוי.", 401, {"Content-Type": "text/plain; charset=utf-8"})
    if hmac.compare_digest(request.cookies.get(COOKIE, ""), TOKEN):
        return None
    return ("נדרש טוקן. פתח את הכתובת המלאה שהשרת הדפיס בהפעלה.", 401,
            {"Content-Type": "text/plain; charset=utf-8"})


# ---------------------------------------------------------------------------
# מפתחות ובקשות
# ---------------------------------------------------------------------------

def load_env() -> None:
    path = HERE / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def creds() -> Optional[tuple]:
    load_env()
    k = os.environ.get("ALPACA_API_KEY_ID", "").strip()
    s = os.environ.get("ALPACA_API_SECRET_KEY", "").strip()
    return (k, s) if k and s else None


def headers() -> dict:
    c = creds()
    if not c:
        return {}
    return {"APCA-API-KEY-ID": c[0], "APCA-API-SECRET-KEY": c[1],
            "Content-Type": "application/json"}


def base() -> str:
    return LIVE_BASE if LIVE else PAPER_BASE


def api(method: str, url: str, **kw):
    """קריאה לאלפקה. מחזיר (ok, payload_or_error)."""
    if not creds():
        return False, {"error": "חסרים מפתחות. צור קובץ .env בתיקייה הזאת."}
    try:
        r = requests.request(method, url, headers=headers(), timeout=25, **kw)
    except requests.RequestException as exc:
        return False, {"error": f"אין חיבור לאלפקה: {type(exc).__name__}"}
    if r.status_code not in (200, 201, 204):
        try:
            msg = r.json().get("message", r.text)
        except ValueError:
            msg = r.text
        return False, {"error": f"אלפקה השיבה {r.status_code}: {msg}"}
    try:
        return True, r.json()
    except ValueError:
        return True, {}


# ---------------------------------------------------------------------------
# חישוב הרמות - זהה לדף ולסקריפט
# ---------------------------------------------------------------------------

def deadline_for(d: date) -> date:
    return date(d.year + HOLD_YEARS, 12, 31)


def levels(entry: float, atr_pct: Optional[float]) -> dict:
    tp = round(entry * (1 + PROFIT_TARGET), 2)
    if atr_pct and atr_pct > 0:
        dist = entry * (atr_pct / 100) * ATR_STOP_MULT
        how = f"{ATR_STOP_MULT}×ATR ({atr_pct:.1f}%)"
        used = True
    else:
        dist = entry * FALLBACK_STOP_PCT
        how = f"{FALLBACK_STOP_PCT*100:.0f}% (אין ATR)"
        used = False
    sl = max(round(entry - dist, 2), 0.01)
    return {"target": tp, "stop": sl, "how": how, "used_atr": used,
            "rr": round((tp - entry) / (entry - sl), 2) if entry > sl else None,
            "deadline": deadline_for(date.today()).isoformat()}


_watch: Optional[list] = None


def watchlist() -> list:
    """המניות מהשלב הטכני, עם ה-ATR שלהן."""
    global _watch
    if _watch is not None:
        return _watch
    rows = []
    try:
        r = requests.get(TECH_CSV, timeout=20)
        if r.status_code == 200:
            for row in csv.DictReader(io.StringIO(r.text)):
                def f(k):
                    v = (row.get(k) or "").strip()
                    try:
                        return float(v)
                    except ValueError:
                        return None
                rows.append({
                    "ticker": (row.get("ticker") or "").strip().upper(),
                    "name": (row.get("name") or "").strip(),
                    "sector": (row.get("sector") or "").strip(),
                    "signal": (row.get("signal") or "").strip(),
                    "signal_kind": (row.get("signal_kind") or "").strip(),
                    "price": f("price"), "atr_pct": f("atr_pct"),
                    "rsi": f("rsi"), "quality_score": f("quality_score"),
                })
    except requests.RequestException:
        pass
    _watch = [r for r in rows if r["ticker"]]
    return _watch


def entry_dates() -> dict:
    out = {}
    path = HERE / "positions.csv"
    if not path.exists():
        return out
    try:
        for row in csv.DictReader(path.open(encoding="utf-8-sig")):
            tk = (row.get("ticker") or "").strip().upper()
            d = (row.get("entry_date") or "").strip()[:10]
            if tk and d:
                try:
                    out[tk] = datetime.strptime(d, "%Y-%m-%d").date()
                except ValueError:
                    pass
    except OSError:
        pass
    return out


def last_trade(sym: str) -> Optional[float]:
    ok, data = api("GET", f"{DATA_BASE}/v2/stocks/{sym}/trades/latest")
    try:
        return float(data["trade"]["p"]) if ok else None
    except (KeyError, TypeError, ValueError):
        return None


def _ts(ts) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _day(ts) -> Optional[date]:
    d = _ts(ts)
    return d.date() if d else None


def leg_levels(order: dict) -> tuple:
    """היעד והסטופ מתוך הרגליים של פקודת כניסה (OTO או bracket)."""
    legs = order.get("legs") or []
    target = next((float(l["limit_price"]) for l in legs
                   if l.get("type") == "limit" and l.get("limit_price")), None)
    stop = next((float(l["stop_price"]) for l in legs if l.get("stop_price")), None)
    return target, stop


def closed_orders() -> Optional[list]:
    """פקודות שנסגרו (בוצעו, בוטלו, פקעו), מהחדשה לישנה. None אם המשיכה נכשלה."""
    ok, data = api("GET", f"{base()}/v2/orders",
                   params={"status": "closed", "limit": 500, "direction": "desc",
                           "nested": "true"})
    return flatten_orders(data) if ok and isinstance(data, list) else None


def fill_dates(orders: Optional[list]) -> dict:
    """מתי נפתחה כל פוזיציה, לפי הביצועים בפועל אצל אלפקה.

    הולכים מהביצוע האחרון אחורה: כל קנייה שבוצעה נאספת, ומכירה שבוצעה עוצרת
    את החיפוש לאותו סימול, כי היא סגרה את הפוזיציה הקודמת. הקנייה המוקדמת
    ביותר שנאספה היא תאריך הכניסה.
    """
    out: dict = {}
    stopped: set = set()
    for o in orders or []:
        sym = (o.get("symbol") or "").upper()
        if not sym or sym in stopped or not o.get("filled_at"):
            continue
        d = _day(o["filled_at"])
        if not d:
            continue
        if o.get("side") == "sell":
            if sym in out:
                stopped.add(sym)
            continue
        out[sym] = d
    return out


_hist_cache: dict = {}
HIST_TTL = 600


def price_history(sym: str, start: date) -> list:
    """סגירות יומיות מ-start עד היום. אלפקה קודם, Yahoo כגיבוי. נשמר עשר דקות."""
    key = (sym, start.isoformat())
    hit = _hist_cache.get(key)
    if hit and time.time() - hit[0] < HIST_TTL:
        return hit[1]
    pts: list = []
    if creds():
        ok, data = api("GET", f"{DATA_BASE}/v2/stocks/{sym}/bars",
                       params={"timeframe": "1Day", "start": start.isoformat(),
                               "limit": 1000, "feed": "iex", "adjustment": "all",
                               "sort": "asc"})
        if ok:
            for b in data.get("bars") or []:
                d = _day(b.get("t"))
                if d and b.get("c") is not None:
                    pts.append([d.isoformat(), round(float(b["c"]), 4)])
    if len(pts) < 2:
        try:
            r = requests.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}",
                params={"period1": int(datetime(start.year, start.month, start.day).timestamp()),
                        "period2": int(time.time()), "interval": "1d"},
                headers={"User-Agent": "Mozilla/5.0 (compatible; graham-daily/1.0)"},
                timeout=10)
            r.raise_for_status()
            res = (r.json().get("chart") or {}).get("result") or []
            stamps = res[0].get("timestamp") or []
            closes = ((res[0].get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
            yp = [[datetime.fromtimestamp(s, timezone.utc).date().isoformat(), round(float(c), 4)]
                  for s, c in zip(stamps, closes) if c is not None]
            if len(yp) > len(pts):
                pts = yp
        except (requests.RequestException, ValueError, IndexError, KeyError, TypeError):
            pass
    _hist_cache[key] = (time.time(), pts)
    return pts


# ---------------------------------------------------------------------------
# נקודות הקצה
# ---------------------------------------------------------------------------

@app.get("/")
def index():
    return send_from_directory(HERE, "app_ui.html")


@app.get("/api/status")
def status():
    has = creds() is not None
    out = {"keys": has, "live": LIVE, "market_open": None, "next_open": None}
    if has:
        ok, clock = api("GET", f"{base()}/v2/clock")
        if ok:
            out["market_open"] = clock.get("is_open")
            out["next_open"] = clock.get("next_open")
        ok2, acct = api("GET", f"{base()}/v2/account")
        if ok2:
            out["equity"] = acct.get("equity")
            out["buying_power"] = acct.get("buying_power")
            out["account"] = acct.get("account_number")
    return jsonify(out)


@app.get("/api/watchlist")
def api_watchlist():
    rows = watchlist()
    syms = [r["ticker"] for r in rows]
    quotes = {}
    # בקשה אחת למחיר האחרון של כל הרשימה. בלי גרפים: 90 יום של נרות לכל מניה,
    # ופנייה נפרדת ל-Yahoo לכל מניה שחסרו לה נתונים, הם מה שהאט את המסך.
    if syms and creds():
        ok, data = api("GET", f"{DATA_BASE}/v2/stocks/trades/latest",
                       params={"symbols": ",".join(syms)})
        if ok:
            for sym, t in (data.get("trades") or {}).items():
                quotes[sym] = t.get("p")
    for r in rows:
        r["last"] = quotes.get(r["ticker"]) or r["price"]
        if r["last"]:
            r["levels"] = levels(float(r["last"]), r["atr_pct"])
    return jsonify({"rows": rows})


@app.get("/api/positions")
def api_positions():
    ok, data = api("GET", f"{base()}/v2/positions")
    if not ok:
        return jsonify(data), 502
    opened = entry_dates()
    filled = fill_dates(closed_orders())
    today = date.today()
    ok_o, orders = api("GET", f"{base()}/v2/orders", params={"status": "open", "limit": 500})
    orders = flatten_orders(orders) if ok_o and isinstance(orders, list) else None
    out = []
    for p in data:
        sym = p["symbol"]
        gain = float(p.get("unrealized_plpc") or 0)
        # positions.csv קובע כשיש בו שורה; אחרת תאריך הביצוע אצל אלפקה.
        ent = opened.get(sym) or filled.get(sym)
        due = deadline_for(ent) if ent else None
        action, why = "החזקה", []
        if gain >= PROFIT_TARGET:
            action = "מכירה"; why.append(f"הרווח {gain*100:.0f}% מעל יעד ה-50%")
        if due and today > due:
            action = "מכירה"; why.append(f"חלף המועד {due.isoformat()}")
        elif due:
            left = (due - today).days
            why.append(f"נותרו {left} ימים")
            if action == "החזקה" and left <= 90:
                action = "מתקרב למועד"
        elif not ent:
            why.append("אין תאריך כניסה")
        if ent and sym not in opened:
            why.append("תאריך הכניסה מאלפקה")
        if action == "החזקה" and gain >= 0.40:
            action = "מתקרב ליעד"
        out.append({
            "ticker": sym, "qty": p.get("qty"),
            "entry": float(p.get("avg_entry_price") or 0),
            "last": float(p.get("current_price") or 0),
            "gain": round(gain, 4),
            "pl": float(p.get("unrealized_pl") or 0),
            "entry_date": ent.isoformat() if ent else None,
            "deadline": due.isoformat() if due else None,
            "action": action, "why": "; ".join(why),
            **{f"exit_{k}": v for k, v in exit_state(orders, sym, today).items()},
        })
    return jsonify({"rows": out})


@app.get("/api/orders")
def api_orders():
    """פקודות מ-30 הימים האחרונים, כולל שבוצעו ושבוטלו, כדי לראות מה קרה לכל אחת."""
    since = (date.today() - timedelta(days=30)).isoformat()
    # nested: היעד והסטופ מגיעים כרגליים של פקודת הכניסה ולא כשורות נפרדות
    ok, data = api("GET", f"{base()}/v2/orders",
                   params={"status": "all", "limit": 100, "direction": "desc",
                           "after": since, "nested": "true"})
    if not ok:
        return jsonify(data), 502
    ok_p, pos = api("GET", f"{base()}/v2/positions")
    held = {p.get("symbol") for p in pos} if ok_p and isinstance(pos, list) else set()
    return jsonify({"rows": [{
        "leg_target": leg_levels(o)[0], "leg_stop": leg_levels(o)[1],
        "id": o.get("id"), "ticker": o.get("symbol"), "side": o.get("side"),
        "type": o.get("type"), "qty": o.get("qty"), "filled_qty": o.get("filled_qty"),
        "limit_price": o.get("limit_price"), "stop_price": o.get("stop_price"),
        "filled_avg_price": o.get("filled_avg_price"),
        "status": o.get("status"), "class": o.get("order_class"),
        "submitted_at": o.get("submitted_at"), "filled_at": o.get("filled_at"),
        "held": o.get("symbol") in held,
    } for o in data]})


@app.get("/api/history/<sym>")
def api_history(sym: str):
    """מהלך המחיר מיום הקנייה, עם הכניסה, היעד, הסטופ והמועד.

    בלי ``order``: הפוזיציה הפתוחה של הסימול. עם ``order=<id>``: קנייה מסוימת
    שבוצעה, גם אם כבר נמכרה - ואז הגרף נגמר קצת אחרי המכירה ומסמן אותה.
    """
    sym = sym.strip().upper()
    if not sym.replace(".", "").replace("-", "").isalnum() or len(sym) > 10:
        return jsonify({"error": "סימול לא תקין"}), 400
    oid = (request.args.get("order") or "").strip()
    ok_p, pos = api("GET", f"{base()}/v2/positions/{sym}")
    closed = closed_orders() or []
    target = stop = None
    exit_at = exit_price = None

    if oid:
        buy = next((o for o in closed if o.get("id") == oid), None)
        if not buy or not buy.get("filled_at") or buy.get("side") != "buy":
            return jsonify({"error": "הפקודה לא נמצאה בין הקניות שבוצעו"}), 404
        bought = _ts(buy["filled_at"])
        ent = bought.date()
        entry = float(buy.get("filled_avg_price") or 0)
        target, stop = leg_levels(buy)
        sells = sorted((o for o in closed
                        if (o.get("symbol") or "").upper() == sym and o.get("side") == "sell"
                        and o.get("filled_at") and _ts(o["filled_at"]) and _ts(o["filled_at"]) > bought),
                       key=lambda o: _ts(o["filled_at"]))
        if sells:
            exit_at = _day(sells[0]["filled_at"])
            exit_price = float(sells[0].get("filled_avg_price") or 0) or None
    elif ok_p:
        entry = float(pos.get("avg_entry_price") or 0)
        ent = entry_dates().get(sym) or fill_dates(closed).get(sym)
    else:
        return jsonify(pos), 502

    still_held = ok_p and not exit_at
    if still_held and target is None:
        ok_o, orders = api("GET", f"{base()}/v2/orders", params={"status": "open", "limit": 500})
        mine = exit_orders_for(flatten_orders(orders), sym) if ok_o and isinstance(orders, list) else []
        target = next((float(o["limit_price"]) for o in mine
                       if o.get("type") == "limit" and o.get("limit_price")), None)
        stop = stop or next((float(o["stop_price"]) for o in mine if o.get("stop_price")), None)

    start = ent or (date.today() - timedelta(days=30))
    pts = price_history(sym, start - timedelta(days=3))
    if exit_at:
        pts = [p for p in pts if p[0] <= (exit_at + timedelta(days=14)).isoformat()]
    last = float(pos.get("current_price") or 0) if still_held else (exit_price or (pts[-1][1] if pts else 0))
    until = exit_at.isoformat() if exit_at else "9999"
    closes = [c for d, c in pts if (not ent or d >= ent.isoformat()) and d <= until]
    return jsonify({
        "ticker": sym, "entry": entry, "last": last,
        "entry_date": ent.isoformat() if ent else None,
        "deadline": deadline_for(ent).isoformat() if ent and not exit_at else None,
        "target": target or round(entry * (1 + PROFIT_TARGET), 2),
        "target_live": target is not None,
        "stop": stop,
        "exit_date": exit_at.isoformat() if exit_at else None,
        "exit_price": exit_price,
        "held": bool(still_held),
        "high": max(closes) if closes else None,
        "low": min(closes) if closes else None,
        "points": pts,
    })


@app.post("/api/preview")
def api_preview():
    body = request.get_json(silent=True) or {}
    try:
        entry = float(body.get("entry"))
    except (TypeError, ValueError):
        return jsonify({"error": "מחיר כניסה לא תקין"}), 400
    if entry <= 0:
        return jsonify({"error": "מחיר כניסה חייב להיות חיובי"}), 400
    atr = body.get("atr_pct")
    return jsonify(levels(entry, float(atr) if atr else None))


@app.post("/api/order")
def api_order():
    """שולח bracket. דורש confirm מפורש מהדפדפן - הכפתור לבדו לא מספיק."""
    body = request.get_json(silent=True) or {}
    if body.get("confirm") != "BUY":
        return jsonify({"error": "חסר אישור"}), 400
    sym = str(body.get("ticker") or "").strip().upper()
    try:
        qty = int(body.get("qty"))
        entry = float(body.get("entry"))
        target = float(body.get("target"))
    except (TypeError, ValueError):
        return jsonify({"error": "שדות חסרים או לא תקינים"}), 400
    use_stop = bool(body.get("use_stop"))
    stop = None
    if use_stop:
        try:
            stop = float(body.get("stop"))
        except (TypeError, ValueError):
            return jsonify({"error": "סטופ חסר או לא תקין"}), 400
    if not sym or qty <= 0 or entry <= 0:
        return jsonify({"error": "סימול, כמות ומחיר חייבים להיות תקינים"}), 400
    if not entry < target:
        return jsonify({"error": "היעד חייב להיות מעל מחיר הכניסה"}), 400
    if use_stop and not stop < entry:
        return jsonify({"error": "הסטופ חייב להיות מתחת למחיר הכניסה"}), 400

    # בדיקה מול המחיר בשוק. קנייה ב-limit מעל השוק מתבצעת מיד במחיר השוק, והיעד
    # והסטופ נשארים מחושבים ממחיר הכניסה שהוקלד - כך סטופ יכול לצאת מעל המחיר
    # בפועל ולמכור מיד בהפסד. אלפקה בודקת את הסטופ מול ה-limit, לא מול השוק.
    market = last_trade(sym)
    if market:
        if entry > market * (1 + MAX_ENTRY_ABOVE_MARKET):
            return jsonify({"error": (
                f"מחיר הכניסה {entry:.2f} גבוה ב-{(entry / market - 1) * 100:.0f}% מהמחיר בשוק "
                f"({market:.2f}). הקנייה הייתה מתבצעת מיד במחיר השוק, והיעד והסטופ היו "
                f"מחושבים ממחיר שלא שילמת. עדכן את מחיר הכניסה.")}), 400
        if use_stop and stop >= market:
            return jsonify({"error": (
                f"הסטופ {stop:.2f} לא מתחת למחיר בשוק ({market:.2f}); הוא היה מופעל מיד.")}), 400

    ok, data = api("POST", f"{base()}/v2/orders",
                   data=json.dumps(entry_order_body(sym, qty, entry, target, stop)))
    if not ok:
        return jsonify(data), 502
    return jsonify({
        "id": data.get("id"), "status": data.get("status"),
        "csv_row": f"{sym},{date.today().isoformat()},{entry:.2f},{qty},",
    })



@app.post("/api/renew")
def api_renew():
    """מבטל את פקודות היציאה הפתוחות של סימול ושולח חדשה לתשעים יום נוספים."""
    body = request.get_json(silent=True) or {}
    if body.get("confirm") != "RENEW":
        return jsonify({"error": "חסר אישור"}), 400
    sym = str(body.get("ticker") or "").strip().upper()
    if not sym:
        return jsonify({"error": "חסר סימול"}), 400
    use_stop = bool(body.get("use_stop"))

    ok, pos = api("GET", f"{base()}/v2/positions/{sym}")
    if not ok:
        return jsonify(pos), 502
    qty = pos.get("qty")
    avg = float(pos.get("avg_entry_price") or 0)
    cur = float(pos.get("current_price") or 0)
    if not qty or avg <= 0:
        return jsonify({"error": "לא הצלחתי לקרוא כמות ומחיר כניסה"}), 502
    atr = next((w.get("atr_pct") for w in watchlist() if w.get("ticker") == sym), None)
    L = levels(avg, atr)
    target = L["target"]
    stop = L["stop"] if use_stop else None
    if cur and target <= cur:
        return jsonify({"error": "המחיר כבר מעל היעד. לפי כלל גראהם זה זמן למכור, לא לחדש"}), 400
    if stop is not None and cur and stop >= cur:
        return jsonify({"error": "הסטופ לא מתחת למחיר הנוכחי"}), 400

    ok, orders = api("GET", f"{base()}/v2/orders", params={"status": "open", "limit": 500})
    if not ok:
        return jsonify(orders), 502
    ids = [o["id"] for o in exit_orders_for(orders, sym) if o.get("id")]
    for oid in ids:
        ok, err = api("DELETE", f"{base()}/v2/orders/{oid}")
        if not ok:
            return jsonify({"error": f"ביטול פקודה נכשל, לא נשלחה חדשה. {err.get('error', '')}"}), 502
    # הביטול אסינכרוני; פקודה חדשה לפני שהכמות משתחררת תידחה.
    for _ in range(15):
        ok, left = api("GET", f"{base()}/v2/orders", params={"status": "open", "limit": 500})
        if ok and not any(o.get("id") in ids for o in flatten_orders(left)):
            break
        time.sleep(1)

    ok, data = api("POST", f"{base()}/v2/orders",
                   data=json.dumps(exit_order_body(sym, qty, target, stop)))
    if not ok:
        data["error"] = ("הפקודות הישנות בוטלו אבל החדשה נדחתה, והפוזיציה כרגע בלי "
                         "פקודת יציאה. נסה שוב בעוד רגע. " + str(data.get("error", "")))
        return jsonify(data), 502
    return jsonify({"id": data.get("id"), "status": data.get("status"),
                    "expires_at": data.get("expires_at"), "target": target, "stop": stop,
                    "cancelled": len(ids)})


def main() -> int:
    global LIVE, REMOTE, TOKEN
    LIVE = "--live" in sys.argv
    REMOTE = "--tailscale" in sys.argv
    port = 5000
    for i, a in enumerate(sys.argv):
        if a == "--port" and i + 1 < len(sys.argv):
            port = int(sys.argv[i + 1])

    if not creds():
        print("אזהרה: לא נמצאו מפתחות. המסך ייפתח אבל לא יוכל לדבר עם אלפקה.")
        print("       צור קובץ .env בתיקייה הזאת עם ALPACA_API_KEY_ID ו-ALPACA_API_SECRET_KEY.\n")
    if LIVE:
        print("*** מצב חשבון אמיתי. כסף אמיתי. ***\n")

    print(f"המסך רץ. פתח בדפדפן:  http://127.0.0.1:{port}")
    if REMOTE:
        TOKEN = ensure_token()
        ts = tailscale_ip()
        print("\nגישה מהטלפון (Tailscale בלבד). פתח פעם אחת את הכתובת הזאת בטלפון:")
        if ts:
            print(f"  http://{ts}:{port}/?t={TOKEN}")
        else:
            print(f"  http://<כתובת ה-Tailscale של המחשב>:{port}/?t={TOKEN}")
            print("  (לא מצאתי את הפקודה tailscale; הכתובת מופיעה באפליקציה של Tailscale)")
        print("  אל תשתף את הכתובת: הטוקן שבה מאפשר לשלוח פקודות.")
    print("לעצירה: Ctrl+C\n")
    # בלי --tailscale: ‏127.0.0.1 בלבד. עם --tailscale: כל הממשקים, אבל guard()
    # דוחה כל כתובת שאינה המחשב עצמו או Tailscale, ודורש טוקן מ-Tailscale.
    app.run(host="0.0.0.0" if REMOTE else "127.0.0.1", port=port, debug=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
