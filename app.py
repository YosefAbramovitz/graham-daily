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

גישה מהטלפון, ברשת הביתית
-------------------------
    python app.py --lan

השרת מאזין אז גם מחוץ למחשב, אבל מקבל רק שני סוגי כתובות: המחשב עצמו,
וכתובות של רשת פנימית (10.x, ‏172.16-31.x, ‏192.168.x). כל כתובת אחרת מקבלת
403. ברשת הפנימית נדרש גם טוקן, כי כל מכשיר באותו Wi-Fi (אורחים, מכשירים
חכמים) יכול להגיע לשרת, ומהמסך אפשר לשלוח פקודות. הטוקן נוצר בהפעלה
הראשונה, נשמר ב-.env בשם APP_TOKEN, והשרת מדפיס כתובת שמכילה אותו. פותחים
אותה פעם אחת בטלפון, והמכשיר נרשם כמאושר (devices.json, עוגייה לעשר שנים):
מעכשיו הוא נכנס בלי טוקן. את המכשירים המאושרים רואים ומוחקים בחלון "מכשירים
מאושרים" במסך. מחיקת devices.json מבטלת את כולם. מהמחשב עצמו לא נדרש טוקן.

בכל הפעלה עם --lan הקישור נשלח גם לטלגרם, אם ב-.env מוגדרים
TELEGRAM_BOT_TOKEN (בוט שיוצרים ב-@BotFather) ו-TELEGRAM_CHAT_ID. את ה-chat_id
לא צריך לחפש: שולחים לבוט הודעה אחת, והשרת מוצא אותו בהפעלה הבאה ושומר.

אין הצפנה (http ולא https), ולכן זה מתאים לרשת הביתית ולא לרשת ציבורית.
בהפעלה הראשונה ווינדוס עשוי לשאול אם לאפשר לפייתון גישה לרשת: לאשר לרשת
פרטית בלבד.

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
import hashlib
import hmac
import io
import ipaddress
import json
import os
import re
import secrets
import socket
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests
from flask import Flask, jsonify, redirect, request, send_from_directory

from exit_orders import (entry_order_body, exit_order_body, exit_orders_for,
                         exit_state, flatten_orders, fraction_order_body,
                         split_amount, whole_shares)

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
REMOTE = False        # נדרס מ---lan בשורת ההפעלה
TOKEN = ""
COOKIE = "gd_token"

# טווחי הכתובות של רשת פנימית (RFC 1918, ו-IPv6 מקומי)
LAN_NETS = tuple(ipaddress.ip_network(n) for n in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7", "fe80::/10"))


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


def is_lan(value: str) -> bool:
    ip = _addr(value)
    return bool(ip and any(ip.version == net.version and ip in net for net in LAN_NETS))


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


def lan_ip() -> Optional[str]:
    """הכתובת של המחשב ברשת הביתית. חיבור UDP לא שולח דבר; הוא רק בוחר ממשק."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sk:
            sk.connect(("192.168.0.1", 9))
            ip = sk.getsockname()[0]
        if is_lan(ip):
            return ip
    except OSError:
        pass
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if is_lan(ip):
                return ip
    except OSError:
        pass
    return None


# מכשירים שאושרו פעם אחת עם הטוקן. לכל מכשיר מזהה אקראי משלו בעוגייה, ורשימת
# המזהים נשמרת ב-devices.json (מחוץ ל-git). כך אפשר לבטל מכשיר אחד בלי להחליף
# את הטוקן, ומכשיר מאושר נכנס בלי טוקן גם אחרי הפעלה מחדש של השרת.
DEVICES_FILE = HERE / "devices.json"
DEVICE_COOKIE = "gd_device"
DEVICE_MAX_AGE = 10 * 365 * 24 * 3600
_devices: Optional[dict] = None


def devices() -> dict:
    global _devices
    if _devices is None:
        try:
            _devices = json.loads(DEVICES_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _devices = {}
    return _devices


def save_devices() -> None:
    tmp = DEVICES_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(devices(), ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, DEVICES_FILE)


def device_label(ua: str) -> str:
    ua = ua or ""
    os_name = next((n for k, n in (("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"),
                                   ("Windows", "Windows"), ("Mac OS", "Mac")) if k in ua), "מכשיר")
    app = next((n for k, n in (("Telegram", "טלגרם"), ("EdgA", "Edge"), ("Edg/", "Edge"),
                               ("SamsungBrowser", "Samsung"), ("CriOS", "Chrome"), ("Chrome", "Chrome"),
                               ("FxiOS", "Firefox"), ("Firefox", "Firefox"), ("Safari", "Safari")) if k in ua), "")
    return f"{os_name} {app}".strip()


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def approve_device(resp):
    """רושם את המכשיר הנוכחי כמאושר ומצמיד לו עוגייה לעשר שנים."""
    did = secrets.token_urlsafe(24)
    devices()[did] = {"name": device_label(request.headers.get("User-Agent", "")),
                      "ip": request.remote_addr, "first_seen": now_iso(), "last_seen": now_iso()}
    save_devices()
    # Lax ולא Strict: הקישור נפתח מטלגרם (אתר אחר), ודפדפן לא שולח עוגייה
    # Strict בשרשרת ניווט שהתחילה מאתר אחר - גם לא אחרי ההפניה. פעולות
    # שמשנות משהו הן POST, ו-Lax לא שולח אותן מאתר אחר.
    resp.set_cookie(DEVICE_COOKIE, did, max_age=DEVICE_MAX_AGE, httponly=True, samesite="Lax")
    resp.delete_cookie(COOKIE)
    return resp


def current_device() -> Optional[str]:
    did = request.cookies.get(DEVICE_COOKIE, "")
    return did if did and did in devices() else None


@app.before_request
def guard():
    """במצב --lan: רק המחשב עצמו או הרשת הפנימית. מהרשת: מכשיר שאושר פעם אחת
    עם הטוקן נכנס מעכשיו בלי טוקן; מכשיר חדש צריך את הקישור עם הטוקן."""
    if not REMOTE:
        return None
    who = request.remote_addr or ""
    if is_local(who):
        return None
    if not is_lan(who):
        return ("גישה רק מהמחשב עצמו או מהרשת הביתית.", 403,
                {"Content-Type": "text/plain; charset=utf-8"})
    given = request.args.get("t")
    if given is not None:
        if hmac.compare_digest(given, TOKEN):
            if current_device():
                return redirect(request.path)
            return approve_device(redirect(request.path))
        return ("טוקן שגוי.", 401, {"Content-Type": "text/plain; charset=utf-8"})
    did = current_device()
    if did:
        d = devices()[did]
        # לא לכתוב לקובץ בכל בקשה: עדכון "נראה לאחרונה" פעם בשעה מספיק
        if d.get("last_seen", "")[:13] != now_iso()[:13]:
            d["last_seen"], d["ip"] = now_iso(), who
            save_devices()
        return None
    # עוגייה מהגרסה הקודמת (הטוקן עצמו, ל-30 יום): הופכים אותה למכשיר מאושר
    if TOKEN and hmac.compare_digest(request.cookies.get(COOKIE, ""), TOKEN):
        if request.method == "GET" and not request.path.startswith("/api/"):
            return approve_device(redirect(request.full_path.rstrip("?")))
        return None
    return ("המכשיר לא מאושר. פתח פעם אחת את הקישור עם הטוקן (נשלח לטלגרם בהפעלה).", 401,
            {"Content-Type": "text/plain; charset=utf-8"})


def device_key(did: str) -> str:
    """מזהה קצר להצגה ולהסרה. המזהה עצמו משמש כסיסמה ולא יוצא מהשרת."""
    return hashlib.sha256(did.encode()).hexdigest()[:12]


@app.get("/api/devices")
def api_devices():
    me = current_device()
    rows = [{"id": device_key(k), "name": v.get("name"), "ip": v.get("ip"), "first_seen": v.get("first_seen"),
             "last_seen": v.get("last_seen"), "current": k == me}
            for k, v in devices().items()]
    rows.sort(key=lambda r: r.get("last_seen") or "", reverse=True)
    return jsonify({"rows": rows, "lan": REMOTE})


@app.post("/api/devices/remove")
def api_devices_remove():
    body = request.get_json(silent=True) or {}
    key = str(body.get("id") or "")
    did = next((k for k in devices() if device_key(k) == key), None)
    if not did:
        return jsonify({"error": "מכשיר לא נמצא"}), 404
    del devices()[did]
    save_devices()
    return jsonify({"removed": key})


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
    # פקודות שבוטלו (כולל אלה שהוחלפו בחידוש) הן רעש: לא קרה בהן כלום.
    data = [o for o in data if o.get("status") != "canceled"]
    return jsonify({"rows": [{
        "leg_target": leg_levels(o)[0], "leg_stop": leg_levels(o)[1],
        "id": o.get("id"), "ticker": o.get("symbol"), "side": o.get("side"),
        "type": o.get("type"), "qty": o.get("qty"), "filled_qty": o.get("filled_qty"),
        "limit_price": o.get("limit_price"), "stop_price": o.get("stop_price"),
        "filled_avg_price": o.get("filled_avg_price"),
        "status": o.get("status"), "class": o.get("order_class"),
        "submitted_at": o.get("submitted_at"), "filled_at": o.get("filled_at"),
        "held": o.get("symbol") in held,
        "cancelable": o.get("status") in OPEN_STATUSES,
    } for o in data]})


# סטטוסים של פקודה שעוד אפשר לבטל אצל אלפקה
OPEN_STATUSES = {"new", "accepted", "pending_new", "partially_filled", "held",
                 "accepted_for_bidding", "calculated", "pending_replace"}
ORDER_ID = re.compile(r"^[0-9a-fA-F-]{36}$")


@app.post("/api/cancel")
def api_cancel():
    """ביטול פקודה פתוחה אחת. ברגליים של OTO/bracket אלפקה מבטלת גם את היעד והסטופ."""
    body = request.get_json(silent=True) or {}
    if body.get("confirm") != "CANCEL":
        return jsonify({"error": "חסר אישור"}), 400
    oid = str(body.get("id") or "")
    if not ORDER_ID.match(oid):
        return jsonify({"error": "מזהה פקודה לא תקין"}), 400
    ok, o = api("GET", f"{base()}/v2/orders/{oid}")
    if not ok:
        return jsonify(o), 502
    if o.get("status") not in OPEN_STATUSES:
        return jsonify({"error": f"הפקודה כבר במצב {o.get('status')} ואי אפשר לבטל אותה"}), 409
    ok, err = api("DELETE", f"{base()}/v2/orders/{oid}")
    if not ok:
        return jsonify(err), 502
    return jsonify({"id": oid, "ticker": o.get("symbol"), "side": o.get("side"),
                    "status": "pending_cancel"})


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
    """שולח את הקנייה. דורש confirm מפורש מהדפדפן - הכפתור לבדו לא מספיק.

    לפי כמות: מניות שלמות, OTO או bracket כרגיל. לפי סכום: המניות השלמות
    בפקודה הרגילה, והשבר שנשאר בפקודת יום נפרדת בלי יעד, כי אלפקה לא מקבלת
    שבר מניה ב-GTC או בפקודה מרובת רגליים.
    """
    body = request.get_json(silent=True) or {}
    if body.get("confirm") != "BUY":
        return jsonify({"error": "חסר אישור"}), 400
    sym = str(body.get("ticker") or "").strip().upper()
    try:
        entry = float(body.get("entry"))
        target = float(body.get("target"))
        if body.get("mode") == "amount":
            amount = float(body.get("amount"))
            qty, frac = split_amount(amount, entry)
        else:
            amount, qty, frac = None, int(body.get("qty")), 0.0
    except (TypeError, ValueError):
        return jsonify({"error": "שדות חסרים או לא תקינים"}), 400
    use_stop = bool(body.get("use_stop"))
    stop = None
    if use_stop:
        try:
            stop = float(body.get("stop"))
        except (TypeError, ValueError):
            return jsonify({"error": "סטופ חסר או לא תקין"}), 400
    if not sym or entry <= 0 or (qty <= 0 and frac <= 0):
        return jsonify({"error": "סימול, כמות או סכום, ומחיר חייבים להיות תקינים"
                        + (" (הסכום קטן מדולר אחד של מניה)" if amount else "")}), 400
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

    out = {"qty": qty, "fraction": frac}
    if qty > 0:
        ok, data = api("POST", f"{base()}/v2/orders",
                       data=json.dumps(entry_order_body(sym, qty, entry, target, stop)))
        if not ok:
            return jsonify(data), 502
        out.update({"id": data.get("id"), "status": data.get("status")})
    if frac > 0:
        ok, data = api("POST", f"{base()}/v2/orders",
                       data=json.dumps(fraction_order_body(sym, frac, entry)))
        if not ok:
            msg = str(data.get("error", ""))
            if qty > 0:
                # המניות השלמות כבר נשלחו; לא מסתירים את זה בגלל השבר
                out["fraction_error"] = msg
            else:
                return jsonify(data), 502
        else:
            out.update({"fraction_id": data.get("id"), "fraction_status": data.get("status")})
    total = qty + frac
    out["csv_row"] = f"{sym},{date.today().isoformat()},{entry:.2f},{total:g},"
    return jsonify(out)


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
    # פקודת GTC לא מקבלת שבר מניה; מחדשים רק את המניות השלמות.
    qty = whole_shares(pos.get("qty"))
    avg = float(pos.get("avg_entry_price") or 0)
    cur = float(pos.get("current_price") or 0)
    if avg <= 0:
        return jsonify({"error": "לא הצלחתי לקרוא כמות ומחיר כניסה"}), 502
    if qty < 1:
        return jsonify({"error": ("בפוזיציה יש רק שבר מניה. אלפקה לא מקבלת שבר בפקודת GTC, "
                                  "ולכן אין לו פקודת יציאה; מכירה תהיה ידנית.")}), 400
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


# ---------------------------------------------------------------------------
# מסך סטטוס הפוזיציות - רענון מהיר
# ---------------------------------------------------------------------------

# תאריכי הביצוע דורשים משיכה של 500 פקודות סגורות. ברענון כל כמה שניות זה
# בזבוז, והם כמעט לא משתנים - לכן נשמרים לכמה דקות, ומתרעננים מיד כשמופיע
# סימול חדש (קנייה שזה עתה בוצעה).
FILL_CACHE_SECONDS = 600
_fills = {"at": 0.0, "syms": frozenset(), "data": {}}


def cached_fill_dates(syms) -> dict:
    now = time.time()
    syms = frozenset(syms)
    if (now - _fills["at"] > FILL_CACHE_SECONDS) or not syms <= _fills["syms"]:
        _fills.update(at=now, syms=syms, data=fill_dates(closed_orders()))
    return _fills["data"]


def latest_prices(syms) -> dict:
    """מחיר העסקה האחרונה לכל הסימולים, בבקשה אחת."""
    if not syms:
        return {}
    ok, data = api("GET", f"{DATA_BASE}/v2/stocks/trades/latest",
                   params={"symbols": ",".join(syms)})
    if not ok:
        return {}
    out = {}
    for sym, t in (data.get("trades") or {}).items():
        try:
            out[sym] = {"p": float(t["p"]), "t": t.get("t")}
        except (KeyError, TypeError, ValueError):
            pass
    return out


def _f(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def live_row(p: dict, quote: Optional[dict], ent: Optional[date], from_csv: bool,
             orders: Optional[list], today: date) -> dict:
    """שורה אחת במסך הסטטוס. המחיר מהעסקה האחרונה אם יש, אחרת מאלפקה."""
    sym = p["symbol"]
    qty = _f(p.get("qty")) or 0.0
    entry = _f(p.get("avg_entry_price")) or 0.0
    last = (quote or {}).get("p") or _f(p.get("current_price")) or 0.0
    prev = _f(p.get("lastday_price"))
    gain = (last / entry - 1) if entry else 0.0
    target = round(entry * (1 + PROFIT_TARGET), 2) if entry else None
    due = deadline_for(ent) if ent else None
    exits = exit_orders_for(orders or [], sym)
    stop = next((_f(o.get("stop_price")) for o in exits if o.get("stop_price")), None)
    limit = next((_f(o.get("limit_price")) for o in exits
                  if o.get("type") == "limit" and o.get("limit_price")), None)

    action, why = "החזקה", []
    if gain >= PROFIT_TARGET:
        action = "מכירה"; why.append(f"הרווח {gain*100:.0f}% מעל יעד ה-50%")
    if due and today > due:
        action = "מכירה"; why.append(f"חלף המועד {due.isoformat()}")
    elif due and action == "החזקה" and (due - today).days <= 90:
        action = "מתקרב למועד"
    if action == "החזקה" and gain >= 0.40:
        action = "מתקרב ליעד"
    if not ent:
        why.append("אין תאריך כניסה")
    elif not from_csv:
        why.append("תאריך הכניסה מאלפקה")

    return {
        "ticker": sym, "qty": qty, "entry": entry, "last": last,
        "quote_time": (quote or {}).get("t"),
        "value": round(qty * last, 2), "cost": round(qty * entry, 2),
        "pl": round(qty * (last - entry), 2), "gain": round(gain, 4),
        "day_change": round(last / prev - 1, 4) if prev else None,
        "day_pl": round(qty * (last - prev), 2) if prev else None,
        "target": target, "exit_limit": limit, "stop": stop,
        # כמה מהדרך מהכניסה ליעד עברנו, 0 עד 1 (שלילי כשמתחת לכניסה)
        "progress": round(gain / PROFIT_TARGET, 4) if entry else None,
        "entry_date": ent.isoformat() if ent else None,
        "held_days": (today - ent).days if ent else None,
        "deadline": due.isoformat() if due else None,
        "deadline_days": (due - today).days if due else None,
        "action": action, "why": "; ".join(why),
        **{f"exit_{k}": v for k, v in exit_state(orders, sym, today).items()},
    }


@app.get("/positions")
def positions_page():
    return send_from_directory(HERE, "positions_ui.html")


@app.get("/api/live")
def api_live():
    """כל מה שמסך הסטטוס צריך, בקריאה אחת: חשבון, שוק, פוזיציות ופקודות ממתינות."""
    ok, pos = api("GET", f"{base()}/v2/positions")
    if not ok:
        return jsonify(pos), 502
    ok_c, clock = api("GET", f"{base()}/v2/clock")
    ok_a, acct = api("GET", f"{base()}/v2/account")
    ok_o, raw = api("GET", f"{base()}/v2/orders",
                    params={"status": "open", "limit": 500, "nested": "true"})
    orders = flatten_orders(raw) if ok_o and isinstance(raw, list) else None

    syms = [p["symbol"] for p in pos]
    opened = entry_dates()
    need = [s for s in syms if s not in opened]
    filled = cached_fill_dates(need) if need else {}
    quotes = latest_prices(syms)
    today = date.today()
    rows = [live_row(p, quotes.get(p["symbol"]), opened.get(p["symbol"]) or filled.get(p["symbol"]),
                     p["symbol"] in opened, orders, today) for p in pos]
    rows.sort(key=lambda r: -(r["value"] or 0))

    # קניות שנשלחו ועוד לא בוצעו (או בוצעו חלקית) - עוד לא פוזיציה
    pending = []
    if ok_o and isinstance(raw, list):
        for o in raw:
            if o.get("side") != "buy":
                continue
            target, stop = leg_levels(o)
            pending.append({
                "id": o.get("id"), "ticker": o.get("symbol"), "status": o.get("status"),
                "type": o.get("type"), "class": o.get("order_class"),
                "qty": o.get("qty"), "notional": o.get("notional"),
                "filled_qty": o.get("filled_qty"), "limit_price": o.get("limit_price"),
                "tif": o.get("time_in_force"), "submitted_at": o.get("submitted_at"),
                "target": target, "stop": stop,
            })

    equity = _f(acct.get("equity")) if ok_a else None
    last_eq = _f(acct.get("last_equity")) if ok_a else None
    return jsonify({
        "now": datetime.now(timezone.utc).isoformat(),
        "live": LIVE,
        "market_open": clock.get("is_open") if ok_c else None,
        "next_open": clock.get("next_open") if ok_c else None,
        "next_close": clock.get("next_close") if ok_c else None,
        "account": {
            "equity": equity, "cash": _f(acct.get("cash")) if ok_a else None,
            "buying_power": _f(acct.get("buying_power")) if ok_a else None,
            "day_pl": round(equity - last_eq, 2) if equity is not None and last_eq else None,
            "day_pct": round(equity / last_eq - 1, 4) if equity is not None and last_eq else None,
        },
        "totals": {
            "value": round(sum(r["value"] for r in rows), 2),
            "cost": round(sum(r["cost"] for r in rows), 2),
            "pl": round(sum(r["pl"] for r in rows), 2),
        },
        "orders_ok": orders is not None,
        "rows": rows, "pending": pending,
    })


# ---------------------------------------------------------------------------
# הקישור לטלפון בטלגרם
# ---------------------------------------------------------------------------

def _env_set(key: str, value: str) -> None:
    """מוסיף שורה ל-.env (בלי לגעת בשאר) ומעדכן את הסביבה."""
    path = HERE / ".env"
    prev = path.read_text(encoding="utf-8") if path.exists() else ""
    sep = "" if (not prev or prev.endswith("\n")) else "\n"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(f"{sep}{key}={value}\n")
    os.environ[key] = value


def telegram_chat(bot: str) -> Optional[str]:
    """ה-chat_id מ-.env, או מההודעה האחרונה שנשלחה לבוט (ואז נשמר ב-.env)."""
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if chat:
        return chat
    try:
        r = requests.get(f"https://api.telegram.org/bot{bot}/getUpdates", timeout=15)
        ups = r.json().get("result") or []
    except (requests.RequestException, ValueError):
        return None
    for u in reversed(ups):
        msg = u.get("message") or u.get("edited_message") or {}
        cid = (msg.get("chat") or {}).get("id")
        if cid is not None and (msg.get("chat") or {}).get("type") == "private":
            _env_set("TELEGRAM_CHAT_ID", str(cid))
            return str(cid)
    return None


def send_link_telegram(link: str) -> str:
    """שולח את קישור הכניסה לטלפון. מחזיר שורה להדפסה. לא זורק שגיאות."""
    load_env()
    bot = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not bot:
        return ("טלגרם: לא מוגדר. להפעלה: צור בוט ב-@BotFather, הוסף ל-.env שורה "
                "TELEGRAM_BOT_TOKEN=..., שלח לבוט הודעה כלשהי, והפעל מחדש.")
    chat = telegram_chat(bot)
    if not chat:
        return "טלגרם: לא מצאתי צ'אט. שלח לבוט הודעה כלשהי (למשל /start) והפעל מחדש."
    text = ("מסך המסחר עלה. לפתוח בטלפון, על ה-Wi-Fi של הבית:\n" + link +
            "\n\nהקישור מאפשר לשלוח פקודות. אל תעביר אותו הלאה.")
    try:
        r = requests.post(f"https://api.telegram.org/bot{bot}/sendMessage", timeout=15,
                          json={"chat_id": chat, "text": text,
                                "disable_web_page_preview": True})
        if r.ok and r.json().get("ok"):
            return "טלגרם: הקישור נשלח לטלפון."
        return f"טלגרם: השליחה נכשלה ({r.status_code}). בדוק את TELEGRAM_BOT_TOKEN ו-TELEGRAM_CHAT_ID."
    except (requests.RequestException, ValueError) as exc:
        return f"טלגרם: אין חיבור ({type(exc).__name__})."



def main() -> int:
    global LIVE, REMOTE, TOKEN
    LIVE = "--live" in sys.argv
    REMOTE = "--lan" in sys.argv
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
        ip = lan_ip()
        print("\nגישה מהטלפון (רשת ביתית בלבד, הטלפון על אותו Wi-Fi). פתח פעם אחת בטלפון:")
        if ip:
            link = f"http://{ip}:{port}/?t={TOKEN}"
            print(f"  {link}")
            print("  " + send_link_telegram(link))
        else:
            print(f"  http://<כתובת המחשב ברשת>:{port}/?t={TOKEN}")
            print("  (לא מצאתי כתובת רשת פנימית; בדוק עם ipconfig)")
        print("  אל תשתף את הכתובת: הטוקן שבה מאפשר לשלוח פקודות.")
    print("לעצירה: Ctrl+C\n")
    # בלי --lan: ‏127.0.0.1 בלבד. עם --lan: כל הממשקים, אבל guard() דוחה כל
    # כתובת שאינה המחשב עצמו או הרשת הפנימית, ודורש טוקן מהרשת.
    app.run(host="0.0.0.0" if REMOTE else "127.0.0.1", port=port, debug=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
