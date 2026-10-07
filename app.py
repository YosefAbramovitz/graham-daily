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
חכמים) יכול להגיע לשרת, ומהמסך אפשר לשלוח פקודות. בכל שליחה של קישור (בכל
הפעלה, ובפקודה /link בבוט) נוצר טוקן חדש, והקודמים בטלים (link_tokens.json).
פותחים את הקישור פעם אחת בטלפון, והמכשיר נרשם כמאושר (devices.json, עוגייה
לעשר שנים): מעכשיו הוא נכנס בלי טוקן. מכשיר מאושר שנכנס עם קישור ישן מוסר
מהמאושרים עד שייכנס עם הקישור האחרון. את המכשירים המאושרים רואים ומוחקים
בחלון "מכשירים מאושרים" במסך. מחיקת devices.json מבטלת את כולם. מהמחשב עצמו
לא נדרש טוקן.

בכל הפעלה עם --lan הקישור נשלח גם לטלגרם, אם ב-.env מוגדרים
TELEGRAM_BOT_TOKEN (בוט שיוצרים ב-@BotFather) ו-TELEGRAM_CHAT_ID. את ה-chat_id
לא צריך לחפש: שולחים לבוט הודעה אחת, והשרת מוצא אותו בהפעלה הבאה ושומר.

אין הצפנה (http ולא https), ולכן זה מתאים לרשת הביתית ולא לרשת ציבורית.

גישה מכל מקום, ב-HTTPS
----------------------
    python app.py --public

בנוסף ל---lan: השרת מקבל גם כתובות מהאינטרנט (דרך הפניית פורטים בראוטר), אבל
רק ב-HTTPS, ותמיד רק עם טוקן או ממכשיר מאושר. חמישה טוקנים שגויים מאותה כתובת
חוסמים אותה לרבע שעה. התעודה לשם שב-PUBLIC_HOST ב-.env (cert.py): כברירת מחדל
מ-CA פרטי שמוגבל לשם הזה, ואותו מתקינים פעם אחת בטלפון (נשלח לטלגרם כשהוא
נוצר, ונגיש ב-/ca.crt). CERT_MODE=letsencrypt ב-.env עובר ל-Let's Encrypt
(צריך פורט 80 פתוח בראוטר). המחשב עצמו נכנס
ב-http://127.0.0.1:5001, בלי תעודה. הקישור לטלגרם הוא https://<host>:5000/?t=...
בהפעלה הראשונה ווינדוס עשוי לשאול אם לאפשר לפייתון גישה לרשת: לאשר לרשת
פרטית בלבד.

להחלפת הטוקן: /link בבוט, או הפעלה מחדש.

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
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import requests
from flask import (Flask, has_request_context, jsonify, make_response, redirect, request,
                   send_from_directory)

import plans
import replay_sim
import spy_sim
import swing
from exit_orders import (entry_order_body, exit_order_body, exit_orders_for,
                         exit_state, flatten_orders, fraction_order_body,
                         split_amount, whole_shares)

HERE = Path(__file__).parent
PAPER_BASE = "https://paper-api.alpaca.markets"
LIVE_BASE = "https://api.alpaca.markets"
DATA_BASE = "https://data.alpaca.markets"

PROFIT_TARGET = 0.50
ATR_STOP_MULT = 2.0
HOLD_DAYS = 365      # גראהם: יעד +50% או שנה אחרי הקנייה (עד ספט' 2026: סוף השנה השנייה)
FALLBACK_STOP_PCT = 0.15
# כמה מעל המחיר בשוק מותר להציע בקנייה, לפני שהשרת מסרב (טעות הקלדה, מחיר ישן)
MAX_ENTRY_ABOVE_MARKET = 0.03

TECH_CSV = ("https://raw.githubusercontent.com/YosefAbramovitz/graham-daily/"
            "main/tech_results.csv")

app = Flask(__name__, static_folder=None)
LIVE = False          # נדרס מ---live בשורת ההפעלה
REMOTE = False        # נדרס מ---lan או --public בשורת ההפעלה
PUBLIC = False        # --public: פתוח גם לאינטרנט, ב-HTTPS בלבד
TOKEN = ""
LOCAL_PORT = 5001     # במצב --public: HTTP רגיל למחשב עצמו בלבד
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


# טוקן חדש בכל קישור שנשלח. הטוקנים הקודמים נשמרים כגיבוב בלבד ב-link_tokens.json
# (מחוץ ל-git), כדי לזהות כניסה עם קישור ישן: מכשיר מאושר שנכנס כך מוסר
# מהמאושרים עד שייכנס עם הקישור האחרון.
TOKENS_FILE = HERE / "link_tokens.json"
OLD_TOKENS_KEEP = 200
_token_lock = threading.Lock()


def _token_hash(tok: str) -> str:
    return hashlib.sha256(tok.encode()).hexdigest()


def _load_tokens() -> dict:
    try:
        data = json.loads(TOKENS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def rotate_token() -> str:
    """מייצר טוקן חדש, הופך את הנוכחי לישן, ושומר. מחזיר את החדש."""
    global TOKEN
    with _token_lock:
        data = _load_tokens()
        old = [h for h in data.get("old", []) if isinstance(h, str)]
        # הטוקן הקבוע מהגרסה הקודמת (APP_TOKEN ב-.env) נחשב גם הוא ישן
        load_env()
        for prev in (data.get("current", ""), os.environ.get("APP_TOKEN", "").strip(), TOKEN):
            if prev and _token_hash(prev) not in old:
                old.append(_token_hash(prev))
        tok = secrets.token_urlsafe(24)
        data = {"current": tok, "old": old[-OLD_TOKENS_KEEP:], "issued": now_iso()}
        tmp = TOKENS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        os.replace(tmp, TOKENS_FILE)
        TOKEN = tok
    return tok


def is_old_token(given: str) -> bool:
    h = _token_hash(given)
    return any(hmac.compare_digest(h, o) for o in _load_tokens().get("old", []) if isinstance(o, str))


LINK_BASE = ""        # למשל https://host:5000/ ; נקבע ב-main


def fresh_link() -> str:
    return f"{LINK_BASE}?t={rotate_token()}"


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
    resp.set_cookie(DEVICE_COOKIE, did, max_age=DEVICE_MAX_AGE, httponly=True, samesite="Lax",
                    secure=request.is_secure)
    resp.delete_cookie(COOKIE)
    return resp


# חסימת ניחושים: 5 טוקנים שגויים מאותה כתובת ברבע שעה חוסמים אותה לרבע שעה.
FAIL_WINDOW = 15 * 60
FAIL_LIMIT = 5
_failures: dict = {}


def record_failure(ip: str) -> None:
    _failures.setdefault(ip, []).append(time.time())


def too_many_failures(ip: str) -> bool:
    now = time.time()
    recent = [t for t in _failures.get(ip, []) if now - t < FAIL_WINDOW]
    _failures[ip] = recent
    return len(recent) >= FAIL_LIMIT


def current_device() -> Optional[str]:
    did = request.cookies.get(DEVICE_COOKIE, "")
    return did if did and did in devices() else None


def blank_page(status: int):
    """דף ריק בלי שום הסבר: מי שלא מאושר לא לומד כלום על המסך או על הטוקן."""
    resp = make_response("<!doctype html><title></title>", status)
    resp.headers["Content-Type"] = "text/html; charset=utf-8"
    return resp


@app.before_request
def guard():
    """במצב --lan: רק המחשב עצמו או הרשת הפנימית. מהרשת: מכשיר שאושר פעם אחת
    עם הטוקן נכנס מעכשיו בלי טוקן; מכשיר חדש צריך את הקישור עם הטוקן."""
    if not REMOTE:
        return None
    who = request.remote_addr or ""
    if is_local(who):
        return None
    if not is_lan(who) and not PUBLIC:
        return blank_page(403)
    if request.path == "/ca.crt":            # תעודת ה-CA הפרטי: ציבורית, בלי טוקן
        return None
    if PUBLIC and not request.is_secure:
        return blank_page(403)
    given = request.args.get("t")
    if given is not None:
        if too_many_failures(who):
            return blank_page(429)
        if TOKEN and hmac.compare_digest(given, TOKEN):
            if current_device():
                return redirect(request.path)
            return approve_device(redirect(request.path))
        if is_old_token(given):
            # קישור ישן: מכשיר מאושר מוסר עד שייכנס עם הקישור האחרון. דף ריק, ולא נחשב ניחוש.
            did = current_device()
            if did:
                del devices()[did]
                save_devices()
            resp = blank_page(401)
            resp.delete_cookie(DEVICE_COOKIE)
            resp.delete_cookie(COOKIE)
            return resp
        record_failure(who)
        return blank_page(401)
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
    return blank_page(401)


def device_key(did: str) -> str:
    """מזהה קצר להצגה ולהסרה. המזהה עצמו משמש כסיסמה ולא יוצא מהשרת."""
    return hashlib.sha256(did.encode()).hexdigest()[:12]


@app.get("/ca.crt")
def ca_cert():
    """תעודת ה-CA הפרטי, להתקנה בטלפון. מידע ציבורי - אין בה מפתח."""
    import cert
    if not cert.CA_EXPORT.exists():
        return ("אין CA פרטי.", 404, {"Content-Type": "text/plain; charset=utf-8"})
    return send_from_directory(cert.CA_EXPORT.parent, cert.CA_EXPORT.name,
                               mimetype="application/x-x509-ca-cert", as_attachment=True)


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


# ---------------------------------------------------------------------------
# כמה חשבונות: כל אסטרטגיה בחשבון paper משלה (מפתחות ב-.env), והמסך עובד מול
# החשבון שנבחר (עוגייה gd_acct). נתוני שוק לא תלויים בחשבון - כל מפתח טוב להם.
# ---------------------------------------------------------------------------

# S&P 500 כבר לא חשבון באלפקה (בוטל, ספט' 2026) אלא הדמיה מקומית - spy_sim.py ו-spy_sim_row.
ACCOUNTS = [  # (מזהה, שם, קידומת במשתני הסביבה)
    ("swing", "סווינג", "ALPACA_SWING"),
    ("graham", "גראהם", "ALPACA_GRAHAM"),
    ("main", "ראשי", "ALPACA_API"),
]
ACCT_COOKIE = "gd_acct"
_acct_local = threading.local()


def acct_keys(acct: str) -> Optional[tuple]:
    load_env()
    prefix = dict((a, p) for a, _, p in ACCOUNTS).get(acct)
    if not prefix:
        return None
    k = os.environ.get(f"{prefix}_KEY_ID", "").strip()
    s = os.environ.get(f"{prefix}_SECRET_KEY", "").strip()
    return (k, s) if k and s else None


def accounts_available() -> list:
    return [a for a, _, _ in ACCOUNTS if acct_keys(a)]


def current_acct() -> str:
    a = getattr(_acct_local, "acct", None)
    if not a and has_request_context():
        a = request.args.get("acct") or request.cookies.get(ACCT_COOKIE)
    avail = accounts_available()
    return a if a in avail else (avail[0] if avail else "main")


class using:
    """עבודה מול חשבון מסוים בתוך בלוק (לולאות רקע, מסך ההשוואה)."""

    def __init__(self, acct):
        self.acct = acct

    def __enter__(self):
        self.prev = getattr(_acct_local, "acct", None)
        _acct_local.acct = self.acct

    def __exit__(self, *a):
        _acct_local.acct = self.prev


def creds() -> Optional[tuple]:
    return acct_keys(current_acct())


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
    return date.fromordinal(d.toordinal() + HOLD_DAYS)


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


# הרשימה מהשלב הטכני. השלב רץ ב-GitHub פעם ביום (בבוקר), ולכן השרת בודק כל
# WATCH_TTL שניות אם פורסמה רשימה חדשה - בבקשה מותנית (ETag), שכשאין שינוי
# מחזירה 304 בלי תוכן. כך האיתות מתחלף בלי להפעיל את השרת מחדש.
WATCH_TTL = 600
TECH_COMMITS = ("https://api.github.com/repos/YosefAbramovitz/graham-daily/commits"
                "?path=tech_results.csv&per_page=1")
_watch = {"rows": [], "etag": None, "checked": 0.0, "as_of": None}


def _parse_watch(text: str) -> list:
    rows = []
    for row in csv.DictReader(io.StringIO(text)):
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
            "ebit_ev": f("ebit_ev"), "value_rank": f("value_rank"),
            "value_top": (row.get("value_top") or "").strip() == "כן",
        })
    return [r for r in rows if r["ticker"]]


def _tech_date() -> Optional[str]:
    """מתי השלב הטכני שמר את הרשימה האחרונה (זמן ה-commit שלה ב-GitHub)."""
    try:
        r = requests.get(TECH_COMMITS, timeout=10,
                         headers={"Accept": "application/vnd.github+json"})
        if r.ok and r.json():
            return r.json()[0]["commit"]["committer"]["date"]
    except (requests.RequestException, ValueError, KeyError, IndexError):
        pass
    return None


def watchlist() -> list:
    """המניות מהשלב הטכני, עם ה-ATR שלהן. מתרענן לבד כשמתפרסמת רשימה חדשה."""
    now = time.time()
    if _watch["rows"] and now - _watch["checked"] < WATCH_TTL:
        return _watch["rows"]
    headers = {"If-None-Match": _watch["etag"]} if _watch["etag"] and _watch["rows"] else {}
    try:
        r = requests.get(TECH_CSV, timeout=20, headers=headers)
        if r.status_code == 200:
            rows = _parse_watch(r.text)
            if rows:
                changed = [x["ticker"] + x["signal"] for x in rows] != \
                          [x["ticker"] + x["signal"] for x in _watch["rows"]]
                _watch.update(rows=rows, etag=r.headers.get("ETag"))
                if changed or not _watch["as_of"]:
                    _watch["as_of"] = _tech_date() or _watch["as_of"]
        _watch["checked"] = now          # 200 או 304: הבדיקה הבאה בעוד WATCH_TTL
    except requests.RequestException:
        _watch["checked"] = now - WATCH_TTL + 60   # תקלה: לנסות שוב בעוד דקה
    return _watch["rows"]


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


@app.get("/api/accounts")
def api_accounts():
    avail = accounts_available()
    cur = current_acct()
    return jsonify({"current": cur, "rows": [{"id": a, "label": l, "ok": a in avail}
                                             for a, l, _ in ACCOUNTS if a in avail]})


@app.post("/api/account")
def api_account():
    a = str((request.get_json(silent=True) or {}).get("id") or "")
    if a not in accounts_available():
        return jsonify({"error": "אין מפתחות לחשבון הזה"}), 400
    resp = jsonify({"current": a})
    resp.set_cookie(ACCT_COOKIE, a, max_age=DEVICE_MAX_AGE, httponly=True, samesite="Lax",
                    secure=request.is_secure)
    return resp


@app.get("/api/status")
def status():
    has = creds() is not None
    out = {"keys": has, "live": LIVE, "market_open": None, "next_open": None,
           "acct": current_acct(), "acct_label": dict((a, l) for a, l, _ in ACCOUNTS).get(current_acct())}
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


# גרפים קטנים למסך המועמדות: סגירות יומיות של כשלושה חודשים לכל הרשימה (מתאים
# להחזקה של עד שנתיים - חודש הוא בעיקר רעש), בבקשה מרוכזת
# אחת לאלפקה (לא בקשה לכל מניה - זה מה שהאט את המסך בעבר), ושמורות 30 דקות.
SPARK_TTL = 1800
SPARK_DAYS = 63                                  # כשלושה חודשי מסחר
_sparks = {"at": 0.0, "syms": frozenset(), "data": {}}


def ny_today() -> date:
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date()


def spark_data(syms) -> dict:
    syms = frozenset(syms)
    if syms <= _sparks["syms"] and time.time() - _sparks["at"] < SPARK_TTL:
        return _sparks["data"]
    bars: dict = {}
    params = {"symbols": ",".join(sorted(syms)), "timeframe": "1Day", "limit": 10000,
              "start": (date.today() - timedelta(days=100)).isoformat(),
              "feed": "iex", "adjustment": "all"}
    for _ in range(5):                                  # דפדוף, אם יש
        ok, data = api("GET", f"{DATA_BASE}/v2/stocks/bars", params=params)
        if not ok:
            return _sparks["data"]                      # תקלה: מה שהיה
        for sym, rows in (data.get("bars") or {}).items():
            bars.setdefault(sym, []).extend(rows)
        if not data.get("next_page_token"):
            break
        params["page_token"] = data["next_page_token"]
    today = ny_today().isoformat()
    out = {}
    for sym, rows in bars.items():
        # רק ימים שנסגרו: היום (אם יש) מוחלף בדף במחיר העדכני
        closes = [float(b["c"]) for b in rows if str(b.get("t", ""))[:10] < today]
        if closes:
            out[sym] = {"c": [round(c, 4) for c in closes[-SPARK_DAYS:]], "prev": closes[-1]}
    _sparks.update(at=time.time(), syms=syms, data=out)
    return out


@app.get("/api/sparks")
def api_sparks():
    syms = [r["ticker"] for r in watchlist()]
    return jsonify({"sparks": spark_data(syms) if syms and creds() else {}, "days": SPARK_DAYS})


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
    return jsonify({"rows": rows, "as_of": _watch["as_of"]})


# שמות חברות לפוזיציות ולפקודות: קודם מהרשימה (שם כמו במועמדות), ולסימול שאינו
# ברשימה (סווינג, מניה שירדה ממנה) - מאלפקה, פעם אחת לכל סימול, שמור בזיכרון.
_asset_names: dict = {}
NAME_TAILS = (" Class A Common Stock", " Class B Common Stock", " Class C Common Stock",
              " Common Stock", " Ordinary Shares", " Common Shares")


def clean_asset_name(name: str) -> str:
    name = (name or "").strip()
    for tail in NAME_TAILS:
        if name.endswith(tail):
            return name[: -len(tail)].strip()
    return name


def company_names(symbols) -> dict:
    try:
        known = {r["ticker"]: r["name"] for r in watchlist() if r.get("name")}
    except Exception:
        known = {}
    out = {}
    for s in {s for s in symbols if s}:
        if s in known:
            out[s] = known[s]
            continue
        if s not in _asset_names:
            ok, a = api("GET", f"{base()}/v2/assets/{quote(s, safe='')}")
            if ok and isinstance(a, dict):
                _asset_names[s] = clean_asset_name(a.get("name"))
        out[s] = _asset_names.get(s, "")
    return out


@app.get("/api/positions")
def api_positions():
    ok, data = api("GET", f"{base()}/v2/positions")
    if not ok:
        return jsonify(data), 502
    opened = entry_dates()
    co = closed_orders()
    filled = fill_dates(co)
    today = date.today()
    ok_o, orders = api("GET", f"{base()}/v2/orders", params={"status": "open", "limit": 500})
    orders = flatten_orders(orders) if ok_o and isinstance(orders, list) else None
    out = []
    smap = swing_map(co)
    names = company_names(p.get("symbol") for p in data)
    for p in data:
        sym = p["symbol"]
        gain = float(p.get("unrealized_plpc") or 0)
        # positions.csv קובע כשיש בו שורה; אחרת תאריך הביצוע אצל אלפקה.
        ent = opened.get(sym) or filled.get(sym)
        due = deadline_for(ent) if ent else None
        action, why = "החזקה", []
        if sym in smap:
            action, w, due = swing_advice(smap[sym], today)
            out.append({
                "ticker": sym, "name": names.get(sym, ""), "qty": p.get("qty"),
                "entry": float(p.get("avg_entry_price") or 0),
                "last": float(p.get("current_price") or 0),
                "gain": round(gain, 4), "pl": float(p.get("unrealized_pl") or 0),
                "entry_date": smap[sym].isoformat(), "deadline": due.isoformat(),
                "action": action, "why": w, "swing": True,
                **{f"exit_{k}": v for k, v in exit_state(orders, sym, today).items()},
            })
            continue
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
            "ticker": sym, "name": names.get(sym, ""), "qty": p.get("qty"),
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
    names = company_names(o.get("symbol") for o in data)
    return jsonify({"rows": [{
        "leg_target": leg_levels(o)[0], "leg_stop": leg_levels(o)[1],
        "id": o.get("id"), "ticker": o.get("symbol"), "name": names.get(o.get("symbol"), ""),
        "side": o.get("side"),
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
    if sym in swing_map(force=True):
        return jsonify({"error": "זו פוזיציית סווינג: היציאה שלה לפי זמן, לא ביעד +50%"}), 400

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
    sw = swing_map().get(sym)
    if sw:
        ent = sw
        action, w, due = swing_advice(sw, today)
        target = None
        why.append(w)
    else:
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
        "progress": round(gain / PROFIT_TARGET, 4) if entry and not sw else None,
        "swing": bool(sw),
        "entry_date": ent.isoformat() if ent else None,
        "held_days": (today - ent).days if ent else None,
        "deadline": due.isoformat() if due else None,
        "deadline_days": (due - today).days if due else None,
        "action": action, "why": "; ".join(why),
        **{f"exit_{k}": v for k, v in exit_state(orders, sym, today).items()},
    }


@app.get("/tabletools.js")
def tabletools_js():
    return send_from_directory(HERE, "tabletools.js", mimetype="text/javascript", max_age=300)


@app.get("/compare")
def compare_page():
    return send_from_directory(HERE, "compare_ui.html")


_compare = {"at": 0.0, "data": None}
SPY_SIM_FILE = HERE / "spy_sim.json"
_spy_sim = {"at": 0.0, "key": None, "data": None}


def fund_sim_rows(since_hint: Optional[str] = None, today: Optional[str] = None) -> List[dict]:
    """קרנות סל בהדמיה (spy_sim.FUNDS): 10,000$ בכל אחת מיום ההתחלה (נקבע בפעם הראשונה
    ונשמר ב-spy_sim.json). הנרות והמחיר החי מאלפקה עם מפתח של חשבון כלשהו (נתוני שוק לא
    תלויים בחשבון). קרן שנכשלה מקבלת שורת שגיאה ולא מפילה את האחרות."""
    key = today or date.today().isoformat()
    if _spy_sim["data"] is not None and _spy_sim["key"] == key and time.time() - _spy_sim["at"] < 120:
        return _spy_sim["data"]
    avail = accounts_available()
    if not avail:
        return []
    st = spy_sim.load_settings(SPY_SIM_FILE)
    if not st.get("start"):
        st = {"start": since_hint or date.today().isoformat(), "cash": spy_sim.CASH}
        spy_sim.save_settings(SPY_SIM_FILE, st)
    start = date.fromisoformat(st["start"])
    syms = [f[0] for f in spy_sim.FUNDS]
    with using(avail[0]):
        all_bars = _swing_bars(syms, start - timedelta(days=7), date.today()) or {}
        lives = {s: last_trade(s) for s in syms}
    rows = []
    for sym, name, fund in spy_sim.FUNDS:
        label = f"{name} · {sym} (הדמיה)"
        bars = all_bars.get(sym) or []
        if not bars:
            rows.append({"id": spy_sim.row_id(sym), "label": label, "sim": True,
                         "error": f"אין נרות ל-{sym}"})
            continue
        closes = [(str(b["t"])[:10], float(b["c"])) for b in bars]
        r = spy_sim.simulate(closes, st["start"], float(st.get("cash") or spy_sim.CASH),
                             lives.get(sym), today)
        rows.append({"id": spy_sim.row_id(sym), "label": label, "sim": True, "symbol": sym, "fund": fund,
                     "equity": r["equity"], "cash": 0.0, "start": r["start_value"],
                     "ret": r["ret"], "maxdd": r["maxdd"],
                     "positions": 1 if r["shares"] else 0, "buys": 1 if r["shares"] else 0,
                     "sells": 0, "since": r["since"] or st["start"], "points": r["points"],
                     "shares": round(r["shares"], 4), "price": r["price"],
                     "day_change": r["day_change"]})
    _spy_sim.update(at=time.time(), key=key, data=rows)
    return rows


def spy_sim_row(since_hint: Optional[str] = None, today: Optional[str] = None) -> Optional[dict]:
    """שורת S&P 500 מתוך fund_sim_rows."""
    return next((r for r in fund_sim_rows(since_hint, today) if r["id"] == "spy_sim"), None)


@app.get("/api/compare")
def api_compare():
    """שלושת החשבונות זה מול זה: שווי, תשואה, ירידה מקסימלית, עסקאות, והיסטוריה יומית."""
    if _compare["data"] and time.time() - _compare["at"] < 120:
        return jsonify(_compare["data"])
    rows = []
    for a, label, _ in ACCOUNTS:
        if not acct_keys(a):
            continue
        with using(a):
            ok, acct = api("GET", f"{base()}/v2/account")
            ok_h, hist = api("GET", f"{base()}/v2/account/portfolio/history",
                             params={"period": "1A", "timeframe": "1D"})
            ok_o, closed = api("GET", f"{base()}/v2/orders",
                               params={"status": "closed", "limit": 500, "direction": "desc"})
            ok_p, pos = api("GET", f"{base()}/v2/positions")
        if not ok:
            rows.append({"id": a, "label": label, "error": acct.get("error")})
            continue
        eq = [v for v in (hist.get("equity") or []) if v] if ok_h else []
        ts = (hist.get("timestamp") or [])[-len(eq):] if eq else []
        # ימים לפני שהחשבון קיבל כסף (שווי 0) לא נכנסים
        pts = [[datetime.fromtimestamp(t, timezone.utc).date().isoformat(), round(float(v), 2)]
               for t, v in zip(ts, eq) if v and v > 0]
        equity = _f(acct.get("equity")) or 0.0
        start = pts[0][1] if pts else equity
        peak, mdd = 0.0, 0.0
        for _, v in pts + [["now", equity]]:
            peak = max(peak, v)
            mdd = min(mdd, v / peak - 1) if peak else mdd
        fills = [o for o in (closed if ok_o and isinstance(closed, list) else [])
                 if o.get("filled_at")]
        rows.append({
            "id": a, "label": label, "equity": equity, "cash": _f(acct.get("cash")),
            "start": start, "ret": (equity / start - 1) if start else None, "maxdd": mdd,
            "positions": len(pos) if ok_p and isinstance(pos, list) else None,
            "buys": sum(1 for o in fills if o.get("side") == "buy"),
            "sells": sum(1 for o in fills if o.get("side") == "sell"),
            "since": pts[0][0] if pts else None, "points": pts,
        })
    sinces = [r["since"] for r in rows if r.get("since")]
    try:
        rows.extend(fund_sim_rows(min(sinces) if sinces else None))
    except Exception as e:  # ההדמיה לא מפילה את מסך ההשוואה
        rows.append({"id": "spy_sim", "label": "קרנות סל (הדמיה)", "error": str(e)})
    out = {"rows": rows, "at": now_iso()}
    _compare.update(at=time.time(), data=out)
    return jsonify(out)


# ---------------------------------------------------------------------------
# הדמיה לאחור: מה היה אילו סווינג וגראהם היו מתחילים ביום מסוים (replay_sim.py).
# החישוב כבד (נרות של S&P 1500), ולכן רץ ברקע ונשמר ל-replay_cache.json ליום מסחר.
# ---------------------------------------------------------------------------

REPLAY_CACHE = HERE / "replay_cache.json"
REPLAY_LISTS = HERE / "replay_lists"         # tech_results.csv לפי commit, לא משתנה לעולם
REPLAY_VERSION = 2          # 2: דולרים, קרנות סל מהפתיחה של היום הראשון
TECH_HISTORY = ("https://api.github.com/repos/YosefAbramovitz/graham-daily/commits"
                "?path=tech_results.csv&per_page=100&since={since}")
TECH_RAW = "https://raw.githubusercontent.com/YosefAbramovitz/graham-daily/{sha}/tech_results.csv"
_replay = {"running": None, "error": None, "data": None}
_replay_lock = threading.Lock()


def replay_page_cache() -> dict:
    if _replay["data"] is None:
        try:
            _replay["data"] = json.loads(REPLAY_CACHE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _replay["data"] = {}
    return _replay["data"]


def _tech_lists(since: date) -> list:
    """[(זמן ה-commit, שורות)] לכל גרסה של tech_results.csv מ-since, ועוד אחת לפניו."""
    r = requests.get(TECH_HISTORY.format(since=f"{since - timedelta(days=10)}T00:00:00Z"),
                     timeout=20, headers={"Accept": "application/vnd.github+json"})
    r.raise_for_status()
    out = []
    REPLAY_LISTS.mkdir(exist_ok=True)
    for c in r.json():
        sha = c["sha"]
        ts = datetime.fromisoformat(c["commit"]["committer"]["date"].replace("Z", "+00:00"))
        path = REPLAY_LISTS / f"{sha}.csv"
        if path.exists():
            text = path.read_text(encoding="utf-8")
        else:
            raw = requests.get(TECH_RAW.format(sha=sha), timeout=20)
            if not raw.ok:
                continue
            text = raw.content.decode("utf-8-sig")
            path.write_text(text, encoding="utf-8")
        out.append((ts, replay_sim.parse_list(text)))
    return out


def _replay_frames(bars: dict, end: date):
    import pandas as pd
    fields = {k: {} for k in "ohlcv"}
    last = end.isoformat()
    for sym, rows in bars.items():
        rows = [b for b in rows if str(b.get("t", ""))[:10] <= last]
        if not rows:
            continue
        idx = pd.to_datetime([str(b["t"])[:10] for b in rows])
        for k in fields:
            fields[k][sym] = pd.Series([float(b[k]) for b in rows], index=idx)
    mk = lambda k: pd.DataFrame(fields[k]).sort_index().pipe(lambda d: d[~d.index.duplicated()])
    return tuple(mk(k) for k in "ohlcv")


def _replay_run(start: date, day: date, key: str) -> None:
    try:
        avail = accounts_available()
        if not avail:
            raise RuntimeError("חסרים מפתחות אלפקה ב-.env")
        lists = _tech_lists(start)
        gsyms = sorted({r["ticker"] for _, rows in lists for r in rows})
        with using(avail[0]):
            fsyms = [f[0] for f in spy_sim.FUNDS]
            syms = sorted((set(_swing_universe()) | set(gsyms) | set(fsyms)) - {"SPY"}) + ["SPY"]
            bars = _swing_bars(syms, start - timedelta(days=swing.HISTORY_DAYS), day)
        O, H, L, C, V = _replay_frames(bars, day)
        cash = replay_sim.CASH
        accounts = {
            "swing": replay_sim.swing_replay(O, H, L, C, V, start.isoformat(), cash, day.isoformat()),
            "graham": replay_sim.graham_replay(O, H, C, lists, start.isoformat(), cash, day.isoformat()),
        }
        funds = []
        for sym, name, fund in spy_sim.FUNDS:
            if sym not in C.columns:
                funds.append({"id": sym, "label": name, "fund": fund, "symbol": sym,
                              "error": f"אין נרות ל-{sym}"})
                continue
            r = replay_sim.hold_replay(O, C, sym, start.isoformat(), cash, day.isoformat())
            funds.append(dict(r, id=sym, label=name, fund=fund))
        out = {"start": start.isoformat(), "day": day.isoformat(), "version": REPLAY_VERSION,
               "computed_at": now_iso(), "cash": cash,
               "universe": len(syms) - 1, "priced": int(C.shape[1]),
               "lists": [ts.isoformat() for ts, _ in sorted(lists, key=lambda x: x[0])],
               "accounts": accounts, "funds": funds}
        with _replay_lock:
            cache = replay_page_cache()
            for k in [k for k in cache if not k.endswith(f"|{REPLAY_VERSION}")]:
                del cache[k]                 # תוצאות של גרסה קודמת לא יוצגו שוב
            cache[key] = out
            try:
                REPLAY_CACHE.write_text(json.dumps(replay_page_cache(), ensure_ascii=False),
                                        encoding="utf-8")
            except OSError:
                pass
        _replay["error"] = None
    except Exception as exc:  # noqa: BLE001 - ההדמיה לא מפילה את השרת
        _replay["error"] = f"ההדמיה נכשלה: {exc}"
    finally:
        _replay["running"] = None


@app.get("/replay")
def replay_page():
    return send_from_directory(HERE, "replay_ui.html")


@app.get("/api/replay")
def api_replay():
    """ההדמיה מיום start (ברירת מחדל replay_sim.DEFAULT_START) עד יום המסחר האחרון שנסגר.
    refresh=1 מחשב מחדש גם אם יש תוצאה שמורה."""
    try:
        start = date.fromisoformat(request.args.get("start") or replay_sim.DEFAULT_START)
    except ValueError:
        return jsonify({"error": "תאריך לא תקין"}), 400
    day = last_closed_day()
    if start > day or start < day - timedelta(days=3 * 365):
        return jsonify({"error": "תאריך ההתחלה צריך להיות בשלוש השנים האחרונות"}), 400
    key = f"{start}|{day}|{REPLAY_VERSION}"
    with _replay_lock:
        data = replay_page_cache().get(key)
        if (data is None or request.args.get("refresh") == "1") and not _replay["running"]:
            _replay.update(running=key, error=None)
            threading.Thread(target=_replay_run, args=(start, day, key), daemon=True).start()
    return jsonify({"data": data, "running": _replay["running"] == key, "error": _replay["error"]})


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
# סווינג: טווח קצר (swing.py). הסריקה רצה פעם ביום מסחר, ברקע.
# ---------------------------------------------------------------------------

SWING_CACHE = HERE / "swing_cache.json"
SWING_SCAN_VERSION = 4      # 4: סטופ 4 ATR (stop_dist). להעלות כשמבנה הסריקה משתנה, כדי שסריקה ישנה תחושב מחדש
_swing = {"data": None, "running": False, "error": None, "universe": None, "uday": None}
_swing_lock = threading.Lock()
_swing_orders = {"at": 0.0, "map": {}}


def last_closed_day() -> date:
    """יום המסחר האחרון שנסגר (בשעון ניו יורק, עם רבע שעה ליישור הנתונים)."""
    now = datetime.now(timezone.utc) - timedelta(hours=4)
    d = now.date()
    if not (swing.is_trading_day(d) and (now.hour, now.minute) >= (16, 15)):
        d -= timedelta(days=1)
        while not swing.is_trading_day(d):
            d -= timedelta(days=1)
    return d


def _swing_universe() -> list:
    if _swing["universe"] and _swing["uday"] == date.today():
        return _swing["universe"]
    from graham_screener import get_sp1500_tickers
    u = sorted(set(get_sp1500_tickers()))
    _swing.update(universe=u, uday=date.today())
    return u


def _swing_fetch(chunk: list, start: date, end: date, feed: str):
    """(ok, bars) לקבוצת סימולים, עם דפדוף וניסיון חוזר על תקלה רגעית."""
    got: dict = {}
    params = {"symbols": ",".join(chunk), "timeframe": "1Day", "limit": 10000,
              "start": start.isoformat(), "end": end.isoformat(),
              "adjustment": "all", "feed": feed, "sort": "asc"}
    for _ in range(40):
        for attempt in range(3):
            ok, data = api("GET", f"{DATA_BASE}/v2/stocks/bars", params=params)
            err = str(data.get("error", "")) if not ok else ""
            # רק תקלה רגעית שווה ניסיון נוסף: חריגת קצב, שגיאת שרת או חיבור
            if ok or not any(x in err for x in ("429", "השיבה 5", "אין חיבור")):
                break
            time.sleep(2 * (attempt + 1))
        if not ok:
            return False, got
        for sym, rows in (data.get("bars") or {}).items():
            got.setdefault(sym, []).extend(rows)
        if not data.get("next_page_token"):
            return True, got
        params["page_token"] = data["next_page_token"]
    return True, got


def _swing_bars(syms: list, start: date, end: date) -> dict:
    """נרות יומיים מלאים, בקבוצות של מאה. SIP קודם (מחזור מלא), IEX כגיבוי.

    סימול אחד שאלפקה לא מכירה מפיל את כל הבקשה (400), ולכן קבוצה שנכשלה
    מתפצלת לחצאים עד שהסימול הבעייתי נשאר לבד ונזרק. סימולים עם מקף
    (BRK-B) נשלחים עם נקודה, כמו שאלפקה כותבת אותם.
    """
    to_api = {s: s.replace("-", ".") for s in syms}
    back = {v: k for k, v in to_api.items()}
    out: dict = {}
    todo = [[to_api[s] for s in syms[i:i + 100]] for i in range(0, len(syms), 100)]
    while todo:
        chunk = todo.pop()
        ok, got = _swing_fetch(chunk, start, end, "sip")
        if not ok:
            ok, got = _swing_fetch(chunk, start, end, "iex")
        if ok:
            out.update({back.get(k, k): v for k, v in got.items()})
        elif len(chunk) > 1:
            todo += [chunk[:len(chunk) // 2], chunk[len(chunk) // 2:]]
    return out


def _swing_frames(bars: dict, end: date):
    import pandas as pd
    fields = {"o": {}, "h": {}, "l": {}, "c": {}, "v": {}}
    last = end.isoformat()
    for sym, rows in bars.items():
        rows = [b for b in rows if str(b.get("t", ""))[:10] <= last]
        if not rows:
            continue
        idx = pd.to_datetime([str(b["t"])[:10] for b in rows])
        for k in fields:
            fields[k][sym] = pd.Series([float(b[k]) for b in rows], index=idx)
    mk = lambda k: pd.DataFrame(fields[k]).sort_index().pipe(lambda d: d[~d.index.duplicated()])
    return mk("c"), mk("h"), mk("l"), mk("v")


SWING_SECTORS = HERE / "swing_sectors.json"


def _swing_sectors() -> dict:
    """סימול -> ענף GICS מוויקיפדיה, נשמר לחודש. בלי רשת: הקובץ הקיים או של הבדיקה."""
    try:
        if time.time() - SWING_SECTORS.stat().st_mtime < 30 * 86400:
            return json.loads(SWING_SECTORS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    try:
        from variants_build import sectors
        m = sectors()
        if len(m) > 1000:
            SWING_SECTORS.write_text(json.dumps(m), encoding="utf-8")
            return m
    except Exception:  # noqa: BLE001 - בלי ענפים הסריקה עדיין עובדת
        pass
    for p in (SWING_SECTORS, HERE / "variants_cache" / "sectors.json"):
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    return {}


def _swing_run(day: date) -> None:
    try:
        syms = _swing_universe() + ["SPY"]
        bars = _swing_bars(syms, day - timedelta(days=swing.HISTORY_DAYS), day)
        C, H, L, V = _swing_frames(bars, day)
        res = swing.scan(C, H, L, V, sectors=_swing_sectors())
        res.update(computed_at=now_iso(), universe=len(syms) - 1, priced=int(C.shape[1]),
                   day=day.isoformat(), version=SWING_SCAN_VERSION)
        if res.get("as_of") != day.isoformat():
            res["warning"] = f"הנתונים האחרונים מ-{res.get('as_of')}, לא מ-{day.isoformat()}"
        _swing.update(data=res, error=None)
        try:
            SWING_CACHE.write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
    except Exception as exc:  # noqa: BLE001 - הסריקה לא מפילה את השרת
        _swing["error"] = f"הסריקה נכשלה: {exc}"
    finally:
        _swing["running"] = False


def swing_state() -> dict:
    """הסריקה האחרונה, ומפעיל חדשה ברקע אם היא לא של יום המסחר האחרון."""
    if _swing["data"] is None and SWING_CACHE.exists():
        try:
            _swing["data"] = json.loads(SWING_CACHE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    day = last_closed_day()
    fresh = ((_swing["data"] or {}).get("day") == day.isoformat()
             and (_swing["data"] or {}).get("version") == SWING_SCAN_VERSION)
    with _swing_lock:
        if not fresh and not _swing["running"] and creds():
            _swing.update(running=True, error=None)
            threading.Thread(target=_swing_run, args=(day,), daemon=True).start()
    return {"scan": _swing["data"], "running": _swing["running"], "error": _swing["error"]}


def swing_map(orders: Optional[list] = None, force: bool = False) -> dict:
    """סימול -> תאריך כניסה, לפוזיציות סווינג פתוחות. נשמר כמה דקות."""
    if orders is not None:
        _swing_orders.update(at=time.time(), map=swing.swing_entries(orders))
    elif force or time.time() - _swing_orders["at"] > FILL_CACHE_SECONDS:
        co = closed_orders()
        if co is not None:
            _swing_orders.update(at=time.time(), map=swing.swing_entries(co))
    return _swing_orders["map"]


def swing_advice(ent: date, today: date) -> tuple:
    """(פעולה, הסבר, מועד יציאה) לפוזיציית סווינג."""
    due = swing.exit_date(ent)
    n = swing.trading_days_between(ent, today)
    if today > due:
        return "מכירה", f"סווינג: חלף מועד היציאה {due.isoformat()}", due
    if today == due:
        return "מכירה היום", f"סווינג: היום ה-{swing.HOLD_DAYS}, יציאה בסגירה", due
    return "סווינג", f"סווינג: יום {n} מתוך {swing.HOLD_DAYS}, יציאה ב-{due.isoformat()}", due


@app.get("/api/swing")
def api_swing():
    st = swing_state()
    ok_a, acct = api("GET", f"{base()}/v2/account")
    ok_p, pos = api("GET", f"{base()}/v2/positions")
    ok_o, raw = api("GET", f"{base()}/v2/orders",
                    params={"status": "open", "limit": 500, "nested": "true"})
    orders = flatten_orders(raw) if ok_o and isinstance(raw, list) else None
    smap = swing_map(force=True)
    today = (datetime.now(timezone.utc) - timedelta(hours=4)).date()
    held = []
    for p in (pos if ok_p and isinstance(pos, list) else []):
        sym = p["symbol"]
        if sym not in smap:
            continue
        ent = smap[sym]
        action, why, due = swing_advice(ent, today)
        stop = next((_f(o.get("stop_price")) for o in exit_orders_for(orders or [], sym)
                     if o.get("stop_price")), None)
        held.append({
            "ticker": sym, "qty": _f(p.get("qty")), "entry": _f(p.get("avg_entry_price")),
            "last": _f(p.get("current_price")), "gain": _f(p.get("unrealized_plpc")),
            "pl": _f(p.get("unrealized_pl")), "stop": stop, "entry_date": ent.isoformat(),
            "day": swing.trading_days_between(ent, today), "exit_date": due.isoformat(),
            "action": action, "why": why,
        })
    pending = [o.get("symbol") for o in (raw if ok_o and isinstance(raw, list) else [])
               if str(o.get("client_order_id") or "").startswith(swing.ORDER_PREFIX)
               and o.get("side") == "buy"]
    nxt = date.fromisoformat(st["scan"]["as_of"]) + timedelta(days=1) if (st["scan"] or {}).get("as_of") \
        else today
    return jsonify({
        **st, "backtest": swing.BACKTEST, "held": held, "pending": pending,
        "slots_left": max(0, swing.MAX_POSITIONS - len(held) - len(pending)),
        "rules": {"rsi_max": swing.RSI_MAX, "stop_atr": swing.STOP_ATR, "hold_days": swing.HOLD_DAYS,
                  "risk_pct": swing.RISK_PCT,
                  "risk_mult": {str(q): swing.risk_mult(q) for q in swing.QUALITY_RISK},
                  "max_position_pct": swing.MAX_POSITION_PCT,
                  "max_positions": swing.MAX_POSITIONS},
        "equity": _f(acct.get("equity")) if ok_a else None,
        "cash": _f(acct.get("cash")) if ok_a else None,
        "entry_exit_date": swing.exit_date(nxt).isoformat(),
    })


@app.post("/api/swing/order")
def api_swing_order():
    """קניית סווינג: limit עם סטופ צמוד (OTO), מסומנת swing- כדי לזהות אותה אחר כך."""
    body = request.get_json(silent=True) or {}
    if body.get("confirm") != "BUY":
        return jsonify({"error": "חסר אישור"}), 400
    sym = str(body.get("ticker") or "").strip().upper()
    try:
        entry, stop, qty = float(body.get("entry")), float(body.get("stop")), int(body.get("qty"))
    except (TypeError, ValueError):
        return jsonify({"error": "מחיר, סטופ או כמות לא תקינים"}), 400
    scan = (_swing["data"] or {})
    if not any(r["ticker"] == sym for r in scan.get("rows", [])):
        return jsonify({"error": f"{sym} לא ברשימת האותות של היום"}), 400
    if scan.get("market_ok") is False:
        return jsonify({"error": "SPY מתחת לממוצע 200: לפי הכלל שנבדק אין כניסות חדשות"}), 400
    if qty <= 0 or entry <= 0 or not stop < entry:
        return jsonify({"error": "הכמות חייבת להיות חיובית והסטופ מתחת לכניסה"}), 400
    ok_a, acct = api("GET", f"{base()}/v2/account")
    equity = _f(acct.get("equity")) if ok_a else None
    if equity and qty * entry > equity * swing.MAX_POSITION_PCT * 1.001:
        return jsonify({"error": f"הפוזיציה מעל {swing.MAX_POSITION_PCT*100:.0f}% מההון"}), 400
    ok_p, pos = api("GET", f"{base()}/v2/positions")
    smap = swing_map(force=True)
    open_swing = sum(1 for p in (pos if ok_p and isinstance(pos, list) else []) if p["symbol"] in smap)
    if ok_p and isinstance(pos, list) and any(p["symbol"] == sym for p in pos):
        return jsonify({"error": f"כבר יש פוזיציה ב-{sym}"}), 400
    ok_o, raw = api("GET", f"{base()}/v2/orders", params={"status": "open", "limit": 500})
    pend = [o for o in (raw if ok_o and isinstance(raw, list) else [])
            if str(o.get("client_order_id") or "").startswith(swing.ORDER_PREFIX)]
    if any(o.get("symbol") == sym for o in pend):
        return jsonify({"error": f"כבר נשלחה קניית סווינג ל-{sym}"}), 400
    if open_swing + len(pend) >= swing.MAX_POSITIONS:
        return jsonify({"error": f"כבר {swing.MAX_POSITIONS} פוזיציות סווינג פתוחות או ממתינות"}), 400
    market = last_trade(sym)
    if market:
        if entry > market * (1 + MAX_ENTRY_ABOVE_MARKET):
            return jsonify({"error": f"מחיר הכניסה {entry:.2f} גבוה מדי מול השוק ({market:.2f})"}), 400
        if stop >= market:
            return jsonify({"error": f"הסטופ {stop:.2f} לא מתחת למחיר בשוק ({market:.2f})"}), 400
    cid = f"{swing.ORDER_PREFIX}{sym}-{datetime.now():%Y%m%d%H%M%S}-{secrets.token_hex(2)}"
    ok, data = api("POST", f"{base()}/v2/orders",
                   data=json.dumps(swing.order_body(sym, qty, entry, stop, cid)))
    if not ok:
        return jsonify(data), 502
    return jsonify({"id": data.get("id"), "status": data.get("status"), "qty": qty})


def swing_close(sym: str):
    """יציאה מפוזיציית סווינג במחיר השוק בחשבון הנוכחי. מחזיר (ok, payload)."""
    sym = str(sym or "").strip().upper()
    if sym not in swing_map(force=True):
        return False, {"error": f"{sym} אינה פוזיציית סווינג"}
    ok, orders = api("GET", f"{base()}/v2/orders", params={"status": "open", "limit": 500})
    if not ok:
        return False, orders
    ids = [o["id"] for o in flatten_orders(orders) if o.get("symbol") == sym and o.get("id")]
    for oid in ids:
        ok, err = api("DELETE", f"{base()}/v2/orders/{oid}")
        if not ok:
            return False, {"error": f"ביטול הסטופ נכשל, לא נמכר. {err.get('error', '')}"}
    for _ in range(15):
        ok, left = api("GET", f"{base()}/v2/orders", params={"status": "open", "limit": 500})
        if ok and not any(o.get("id") in ids for o in flatten_orders(left)):
            break
        time.sleep(1)
    ok, data = api("DELETE", f"{base()}/v2/positions/{sym}")
    if not ok:
        data["error"] = ("הסטופ בוטל אבל המכירה נדחתה, והפוזיציה כרגע בלי סטופ. "
                         + str(data.get("error", "")))
        return False, data
    return True, {"id": data.get("id"), "status": data.get("status"), "cancelled": len(ids)}


@app.post("/api/swing/close")
def api_swing_close():
    """יציאה מפוזיציית סווינג במחיר השוק: ביטול הסטופ, ואז סגירת הפוזיציה."""
    body = request.get_json(silent=True) or {}
    if body.get("confirm") != "SELL":
        return jsonify({"error": "חסר אישור"}), 400
    ok, data = swing_close(body.get("ticker"))
    if ok:
        return jsonify(data)
    return jsonify(data), (400 if "אינה פוזיציית סווינג" in str(data.get("error", "")) else 502)


# ---------------------------------------------------------------------------
# קנייה יומית בלחיצה אחת לכל חשבון (plans.py): תצוגה מקדימה, ואז אישור BUY
# ---------------------------------------------------------------------------

def _book():
    """חשבון, פוזיציות ופקודות פתוחות של החשבון הנוכחי."""
    ok_a, acct = api("GET", f"{base()}/v2/account")
    ok_p, pos = api("GET", f"{base()}/v2/positions")
    ok_o, raw = api("GET", f"{base()}/v2/orders", params={"status": "open", "limit": 500})
    if not (ok_a and ok_p and ok_o):
        return None
    equity = _f(acct.get("equity")) or 0.0
    cash = min(_f(acct.get("cash")) or 0.0, _f(acct.get("buying_power")) or 0.0)
    held = {p["symbol"] for p in pos}
    return {"equity": equity, "cash": cash, "held": held, "orders": raw,
            "busy": held | {o.get("symbol") for o in raw}}


def swing_plan() -> dict:
    swing_state()
    scan = (_swing["data"] or {})
    if _swing["running"]:
        return {"plan": [], "reason": "הסריקה של היום עוד רצה - נסה שוב בעוד דקה"}
    if scan.get("day") != last_closed_day().isoformat():
        return {"plan": [], "reason": "אין סריקה מיום המסחר האחרון"}
    if scan.get("market_ok") is False:
        return {"plan": [], "reason": "SPY מתחת לממוצע 200: לפי הכלל אין כניסות חדשות"}
    rows = scan.get("rows") or []
    if not rows:
        return {"plan": [], "reason": "אין אותות היום"}
    b = _book()
    if not b:
        return {"plan": [], "reason": "לא הצלחתי לקרוא את החשבון"}
    smap = swing_map(force=True)
    open_swing = len(b["held"] & set(smap)) + sum(
        1 for o in b["orders"] if o.get("side") == "buy"
        and str(o.get("client_order_id") or "").startswith(swing.ORDER_PREFIX))
    prices = {k: v["p"] for k, v in latest_prices([r["ticker"] for r in rows]).items()}
    plan = plans.plan_swing(rows, b["equity"], b["cash"], prices, b["busy"], open_swing)
    return {"plan": plan, "equity": b["equity"], "cash": b["cash"], "open": open_swing,
            "reason": None if plan else "אין מקום פנוי או מזומן"}


def graham_plan() -> dict:
    rows = watchlist()
    if not rows:
        return {"plan": [], "reason": "רשימת המועמדות לא נטענה"}
    b = _book()
    if not b:
        return {"plan": [], "reason": "לא הצלחתי לקרוא את החשבון"}
    pending = {o.get("symbol") for o in b["orders"] if o.get("side") == "buy"}
    prices = {k: v["p"] for k, v in latest_prices([r["ticker"] for r in rows]).items()}
    plan = plans.plan_graham(rows, b["equity"], b["cash"], prices, b["busy"],
                             len(b["held"] | pending))
    return {"plan": plan, "equity": b["equity"], "cash": b["cash"], "held": len(b["held"]),
            "reason": None if plan else "התיק מלא (15) או שאין מזומן"}


PLAN_KINDS = ("swing", "graham")


def _wrong_acct(kind: str):
    """כל תוכנית שייכת לחשבון שלה, אם הוא מוגדר - שלא ייקנה סווינג בחשבון גראהם."""
    if kind in accounts_available() and current_acct() != kind:
        label = dict((a, l) for a, l, _ in ACCOUNTS)[kind]
        return f"זו תוכנית של חשבון {label}. עבור אליו בבורר החשבונות למעלה."
    return None


def plan_for(kind: str) -> Optional[dict]:
    """התוכנית של סוג מסוים בחשבון הנוכחי. None לסוג לא מוכר."""
    if kind == "swing":
        return swing_plan()
    if kind == "graham":
        return graham_plan()
    return None


def execute_plan(kind: str, want) -> list:
    """שולח את הפקודות של התוכנית - רק הסימולים שאושרו, מחושבים מחדש עכשיו."""
    want = {str(t).upper() for t in want or []}
    plan = [p for p in (plan_for(kind) or {}).get("plan", []) if p["ticker"] in want]
    out = []
    for p in plan:
        if kind == "swing":
            cid = f"{swing.ORDER_PREFIX}{p['ticker']}-{datetime.now():%Y%m%d%H%M%S}-{secrets.token_hex(2)}"
            order = plans.swing_order(p, cid)
        elif kind == "graham":
            order = entry_order_body(p["ticker"], p["qty"], p["price"], p["target"])
        else:
            continue
        ok, data = api("POST", f"{base()}/v2/orders", data=json.dumps(order))
        out.append({"ticker": p["ticker"], "qty": p["qty"], "ok": ok,
                    "status": data.get("status") if ok else None,
                    "error": None if ok else data.get("error")})
        time.sleep(0.2)
    return out


@app.get("/api/plan/<kind>")
def api_plan(kind: str):
    bad = _wrong_acct(kind)
    if bad:
        return jsonify({"plan": [], "reason": bad})
    res = plan_for(kind)
    if res is None:
        return jsonify({"error": "סוג לא מוכר"}), 404
    return jsonify(res)


@app.post("/api/plan/<kind>/buy")
def api_plan_buy(kind: str):
    """שולח את התוכנית שהמשתמש ראה ואישר (רק הסימולים שאושרו, מחושבים מחדש)."""
    body = request.get_json(silent=True) or {}
    if body.get("confirm") != "BUY":
        return jsonify({"error": "חסר אישור"}), 400
    bad = _wrong_acct(kind)
    if bad:
        return jsonify({"error": bad}), 400
    if kind not in PLAN_KINDS:
        return jsonify({"error": "סוג לא מוכר"}), 404
    return jsonify({"rows": execute_plan(kind, body.get("tickers")), "acct": current_acct()})


# ---------------------------------------------------------------------------
# תזכורות לטלגרם, פעם ביום: אותות סווינג ופוזיציות שהגיעו ליום היציאה (swing.HOLD_DAYS)
# (הודעה בלבד - שום פקודה לא נשלחת מכאן)
# ---------------------------------------------------------------------------

REMIND_FILE = HERE / "reminders.json"


def _remind_state() -> dict:
    try:
        d = json.loads(REMIND_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _remind_save(d: dict) -> None:
    REMIND_FILE.write_text(json.dumps(d), encoding="utf-8")


def remind_tick() -> None:
    state = _remind_state()
    last = state.get("day")
    if not accounts_available():
        return
    ok, clock = api("GET", f"{base()}/v2/clock")
    if not ok or not clock.get("is_open"):
        return
    day = str(clock.get("timestamp", ""))[:10]
    if day == last:
        return
    state["day"] = day
    _remind_save(state)
    lines = []
    due = []
    st = swing_state()
    for _ in range(30):
        if not _swing["running"]:
            break
        time.sleep(10)
    scan = _swing["data"] or {}
    rows = scan.get("rows") or []
    if scan.get("market_ok") is False:
        lines.append("סווינג: SPY מתחת לממוצע 200 - אין כניסות היום.")
    elif rows:
        top = [r["ticker"] for r in rows if r.get("quality") == 3][:8]
        lines.append(f"סווינג: {len(rows)} אותות היום" + (f" (ציון 3: {', '.join(top)})" if top else "") + ".")
    if "swing" in accounts_available():
        with using("swing"):
            smap = swing_map(force=True)
            ok_p, pos = api("GET", f"{base()}/v2/positions")
        today = date.fromisoformat(day)
        due = [s for s, d in smap.items() if ok_p and s in {p["symbol"] for p in pos}
               and swing.exit_date(d) <= today]
        if due:
            lines.append("סווינג - יום 15, למכור היום: " + ", ".join(sorted(due))
                         + " (כפתור מכירה בהודעה נפרדת)")
    if "swing" in accounts_available() and due:
        tg_offer_sell(due)
    sent, parts = tg_offer(list(PLAN_KINDS), quiet=True)   # הודעה אחת עם כל התוכניות לאישור
    if sent:
        lines.append("קניות לאישור: בהודעה נפרדת עם כפתור אישור.")
    else:
        why = [f"{PLAN_LABEL[k]} - {r.get('reason') or 'ריק'}" for k, r in parts.items()
               if not r.get("plan")]
        lines.append("אין קניות לאישור היום" + (": " + "; ".join(why) if why else "."))
    if lines:
        telegram_send("מסך המסחר - היום:\n" + "\n".join(lines))


def remind_loop() -> None:
    while True:
        for tick in (remind_tick, summary_tick):
            try:
                tick()
            except Exception as exc:  # noqa: BLE001 - תזכורת שנכשלה לא מפילה את השרת
                print(f"תזכורת: {tick.__name__} נכשלה ({type(exc).__name__}: {exc})", flush=True)
        time.sleep(300)


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


CA_HOWTO = (
    "זו תעודת ה-CA הפרטי של מסך המסחר. היא מוגבלת לחתום רק על {host}, ולכן לא "
    "יכולה לשמש להתחזות לאתר אחר. מתקינים פעם אחת בכל מכשיר:\n\n"
    "אייפון: לפתוח את הקובץ > הגדרות > פרופיל שהורד > התקנה. אחר כך: הגדרות > כללי > "
    "אודות > הגדרות אמון בתעודות > להפעיל את graham-daily.\n\n"
    "אנדרואיד: להוריד את הקובץ > הגדרות > אבטחה > הצפנה ופרטי כניסה > התקנת תעודה > "
    "תעודת CA > לבחור את הקובץ.")


def send_ca_telegram(host: str) -> str:
    """שולח את קובץ ה-CA לטלגרם, עם הוראות התקנה."""
    import cert
    load_env()
    bot = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = telegram_chat(bot) if bot else None
    if not (bot and chat):
        return "טלגרם: לא מוגדר, קובץ ה-CA לא נשלח (אפשר להוריד מ-/ca.crt)."
    try:
        with cert.CA_EXPORT.open("rb") as fh:
            r = requests.post(f"https://api.telegram.org/bot{bot}/sendDocument", timeout=20,
                              data={"chat_id": chat, "caption": CA_HOWTO.format(host=host)[:1000]},
                              files={"document": ("graham-daily-ca.crt", fh,
                                                  "application/x-x509-ca-cert")})
        return ("טלגרם: קובץ ה-CA נשלח." if r.ok and r.json().get("ok")
                else f"טלגרם: שליחת ה-CA נכשלה ({r.status_code}).")
    except (requests.RequestException, ValueError, OSError) as exc:
        return f"טלגרם: שליחת ה-CA נכשלה ({type(exc).__name__})."


def telegram_send(text: str) -> bool:
    """הודעה לטלגרם. לא זורק שגיאות; False אם לא נשלחה."""
    load_env()
    bot = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = telegram_chat(bot) if bot else None
    if not chat:
        return False
    try:
        r = requests.post(f"https://api.telegram.org/bot{bot}/sendMessage", timeout=15,
                          json={"chat_id": chat, "text": text, "disable_web_page_preview": True})
        return bool(r.ok and r.json().get("ok"))
    except (requests.RequestException, ValueError):
        return False


# ---------------------------------------------------------------------------
# אישור בטלגרם. כל יום עם פתיחת השוק: הודעה אחת עם כל התוכניות (סווינג /
# גראהם), כפתור לכל מניה (להוריד/להחזיר) וכפתור "אשר הכל"; פוזיציות
# סווינג ביום היציאה עם כפתור "מכור"; ואחרי הסגירה סיכום יומי. שום פקודה לא
# נשלחת בלי לחיצה על אישור בצ'אט המוגדר. הקנייה מחושבת מחדש ברגע האישור.
# ---------------------------------------------------------------------------

PLAN_LABEL = {"swing": "סווינג", "graham": "גראהם"}
KIND_CODE = {"swing": "s", "graham": "g"}
CODE_KIND = {v: k for k, v in KIND_CODE.items()}
TG_TTL = 6 * 3600          # אחרי זה ההודעה פגה ונדרשת /plan חדשה
_tg = {"pending": {}, "offset": None}
_tg_lock = threading.Lock()


def tg_chat() -> str:
    return os.environ.get("TELEGRAM_CHAT_ID", "").strip()


def tg_call(method: str, **payload):
    """קריאה ל-Bot API. מחזיר את result או None. לא זורק שגיאות."""
    load_env()
    bot = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not bot:
        return None
    try:
        r = requests.post(f"https://api.telegram.org/bot{bot}/{method}", json=payload,
                          timeout=payload.get("timeout", 0) + 15)
        j = r.json()
        return j.get("result") if j.get("ok") else None
    except (requests.RequestException, ValueError):
        return None


def _plan_line(kind: str, p: dict, on: bool) -> str:
    if kind == "swing":
        extra = f"סטופ {p['stop']}"
    elif kind == "graham":
        extra = f"לימיט {p['price']}, יעד {p['target']}"
    else:
        extra = "מרקט"
    return f"{'✅' if on else '⬜'} {p['ticker']}: {p['qty']} × {p['price']} = ${p['value']:,.0f} ({extra})"


def tg_plan_text(st: dict) -> str:
    lines = ["קניות לאישור (חשבונות paper)"]
    total = 0.0
    for kind, res in st["parts"].items():
        rows = res.get("plan") or []
        lines.append(f"\n{PLAN_LABEL[kind]}" + (f" - מזומן ${res['cash']:,.0f}" if res.get("cash") is not None else ""))
        if not rows:
            lines.append(f"  אין מה לקנות: {res.get('reason') or 'התוכנית ריקה'}")
        for p in rows:
            on = (kind, p["ticker"]) in st["selected"]
            lines.append(_plan_line(kind, p, on))
            total += p["value"] if on else 0.0
    lines.append(f"\nסה\"כ לאישור: ${total:,.0f} ({len(st['selected'])} פקודות)")
    lines.append("לחיצה על מניה מורידה או מחזירה אותה. המחירים יחושבו מחדש ברגע האישור.")
    return "\n".join(lines)


def tg_plan_keyboard(tok: str, st: dict) -> dict:
    grid = []
    for kind, res in st["parts"].items():
        btns = [{"text": ("✅ " if (kind, p["ticker"]) in st["selected"] else "⬜ ")
                 + f"{KIND_CODE[kind].upper()}:{p['ticker']}",
                 "callback_data": f"t|{tok}|{KIND_CODE[kind]}|{p['ticker']}"}
                for p in res.get("plan") or []]
        grid += [btns[i:i + 3] for i in range(0, len(btns), 3)]
    grid.append([{"text": f"אשר הכל ({len(st['selected'])})", "callback_data": f"ok|{tok}"},
                 {"text": "בטל", "callback_data": f"no|{tok}"}])
    return {"inline_keyboard": grid}


def _remember(st: dict, msg) -> None:
    if msg:
        st["msg"], st["at"] = msg["message_id"], time.time()
        with _tg_lock:
            _tg["pending"][st["tok"]] = st


def tg_offer(kinds, quiet: bool = False) -> tuple:
    """שולח הודעה אחת עם התוכניות של kinds. quiet=True: כלום אם אין מה לקנות."""
    chat = tg_chat()
    if not chat:
        return False, {}
    if isinstance(kinds, str):
        kinds = [kinds]
    parts = {}
    for kind in kinds:
        if kind not in accounts_available():
            parts[kind] = {"plan": [], "reason": "החשבון לא מוגדר ב-.env"}
            continue
        with using(kind):
            parts[kind] = plan_for(kind) or {"plan": []}
    selected = {(k, p["ticker"]) for k, r in parts.items() for p in r.get("plan") or []}
    if not selected:
        if not quiet:
            tg_call("sendMessage", chat_id=chat, text=tg_plan_text(
                {"parts": parts, "selected": set()}).split("\nסה")[0])
        return False, parts
    if quiet:       # בהודעה האוטומטית לא מציגים חשבון שאין בו מה לקנות
        parts = {k: r for k, r in parts.items() if r.get("plan")}
    st = {"type": "buy", "tok": secrets.token_hex(4), "parts": parts, "selected": selected}
    msg = tg_call("sendMessage", chat_id=chat, text=tg_plan_text(st),
                  reply_markup=tg_plan_keyboard(st["tok"], st))
    print(f"טלגרם: תוכניות {','.join(parts)} ({len(selected)}) {'נשלחו' if msg else 'לא נשלחו'}",
          flush=True)
    _remember(st, msg)
    return bool(msg), parts


def tg_sell_text(st: dict) -> str:
    lines = ["סווינג - יום 15: למכור לפי הכלל (מרקט, הסטופ מבוטל קודם)"]
    for sym in sorted(st["syms"]):
        lines.append(f"{'✅' if sym in st['selected'] else '⬜'} {sym}")
    return "\n".join(lines)


def tg_sell_keyboard(st: dict) -> dict:
    btns = [{"text": ("✅ " if s in st["selected"] else "⬜ ") + s,
             "callback_data": f"t|{st['tok']}|x|{s}"} for s in sorted(st["syms"])]
    grid = [btns[i:i + 3] for i in range(0, len(btns), 3)]
    grid.append([{"text": f"מכור ({len(st['selected'])})", "callback_data": f"ok|{st['tok']}"},
                 {"text": "לא עכשיו", "callback_data": f"no|{st['tok']}"}])
    return {"inline_keyboard": grid}


def tg_offer_sell(syms) -> None:
    chat = tg_chat()
    syms = {s.upper() for s in syms}
    if not chat or not syms:
        return
    st = {"type": "sell", "tok": secrets.token_hex(4), "syms": syms, "selected": set(syms)}
    msg = tg_call("sendMessage", chat_id=chat, text=tg_sell_text(st),
                  reply_markup=tg_sell_keyboard(st))
    print(f"טלגרם: מכירת יום 15 ({len(syms)}) {'נשלחה' if msg else 'לא נשלחה'}", flush=True)
    _remember(st, msg)


def tg_result_text(kind: str, out: list) -> str:
    if not out:
        return f"{PLAN_LABEL[kind]}: לא נשלחה אף פקודה (אולי המחיר, המזומן או התיק השתנו)."
    lines = [f"{PLAN_LABEL[kind]} - נשלח:"]
    for r in out:
        lines.append(("✅ " if r["ok"] else "❌ ") + f"{r['ticker']} {r['qty']}"
                     + (f" ({r['status']})" if r["ok"] else f" - {r['error']}"))
    return "\n".join(lines)


def _execute(st: dict) -> str:
    if st["type"] == "sell":
        lines = ["סווינג - מכירה:"]
        with using("swing"):
            for sym in sorted(st["selected"]):
                ok, data = swing_close(sym)
                lines.append(("✅ " if ok else "❌ ") + sym
                             + (f" ({data.get('status')})" if ok else f" - {data.get('error')}"))
        return "\n".join(lines) if st["selected"] else "לא נבחרה אף מניה, לא נמכר כלום."
    parts = []
    for kind in st["parts"]:
        want = {sym for k, sym in st["selected"] if k == kind}
        if not want:
            continue
        with using(kind):
            parts.append(tg_result_text(kind, execute_plan(kind, want)))
    return "\n\n".join(parts) or "לא נבחרה אף מניה, לא נשלחו פקודות."


def tg_handle_callback(cq: dict) -> None:
    chat = tg_chat()
    frm = str((cq.get("from") or {}).get("id", ""))
    if not chat or frm != chat:
        tg_call("answerCallbackQuery", callback_query_id=cq["id"], text="לא מורשה")
        return
    parts = str(cq.get("data") or "").split("|")
    tok = parts[1] if len(parts) > 1 else ""
    with _tg_lock:
        st = _tg["pending"].get(tok)
        if st and time.time() - st["at"] > TG_TTL:
            _tg["pending"].pop(tok, None)
            st = None
        if st and parts[0] == "t" and len(parts) == 4:
            key = parts[3] if st["type"] == "sell" else (CODE_KIND.get(parts[2]), parts[3])
            st["selected"] ^= {key}
        elif st and parts[0] in ("ok", "no"):
            _tg["pending"].pop(tok, None)     # לחיצה כפולה לא שולחת פעמיים
    if not st:
        tg_call("answerCallbackQuery", callback_query_id=cq["id"],
                text="פג תוקף. שלח /plan לתוכנית חדשה", show_alert=True)
        return
    if parts[0] == "t":
        tg_call("answerCallbackQuery", callback_query_id=cq["id"])
        if st["type"] == "sell":
            text, kb = tg_sell_text(st), tg_sell_keyboard(st)
        else:
            text, kb = tg_plan_text(st), tg_plan_keyboard(tok, st)
        tg_call("editMessageText", chat_id=chat, message_id=st["msg"], text=text, reply_markup=kb)
        return
    if parts[0] == "no":
        tg_call("answerCallbackQuery", callback_query_id=cq["id"], text="בוטל")
        tg_call("editMessageText", chat_id=chat, message_id=st["msg"],
                text="בוטל, לא נשלחו פקודות.")
        return
    tg_call("answerCallbackQuery", callback_query_id=cq["id"], text="שולח...")
    try:
        result = _execute(st)
    except Exception as exc:  # noqa: BLE001 - שהמשתמש יראה מה קרה ולא כפתור שלא עושה כלום
        result = f"שגיאה בשליחה: {type(exc).__name__}: {exc}\nבדוק במסך המסחר מה נשלח."
    if not tg_call("editMessageText", chat_id=chat, message_id=st["msg"], text=result[:4000]):
        tg_call("sendMessage", chat_id=chat, text=result[:4000])


# ---------------------------------------------------------------------------
# סיכום יומי אחרי הסגירה
# ---------------------------------------------------------------------------

def day_summary(day: str) -> str:
    lines = [f"סיכום יום המסחר {day}"]
    for acct in accounts_available():
        with using(acct):
            ok_a, a = api("GET", f"{base()}/v2/account")
            ok_p, pos = api("GET", f"{base()}/v2/positions")
            ok_o, orders = api("GET", f"{base()}/v2/orders",
                               params={"status": "closed", "limit": 200, "nested": "true",
                                       "after": f"{day}T00:00:00Z"})
        if not ok_a:
            lines.append(f"\n{dict((x, l) for x, l, _ in ACCOUNTS).get(acct, acct)}: לא הצלחתי לקרוא")
            continue
        eq = _f(a.get("equity")) or 0.0
        last = _f(a.get("last_equity")) or eq
        chg = eq - last
        pct = (chg / last * 100) if last else 0.0
        label = dict((x, l) for x, l, _ in ACCOUNTS).get(acct, acct)
        lines.append(f"\n{label}: ${eq:,.0f} ({'+' if chg >= 0 else ''}{chg:,.0f}, "
                     f"{'+' if pct >= 0 else ''}{pct:.2f}%) | {len(pos) if ok_p else '?'} פוזיציות"
                     f" | מזומן ${_f(a.get('cash')) or 0:,.0f}")
        fills = [o for o in flatten_orders(orders if ok_o else [])
                 if o.get("status") == "filled" and str(o.get("filled_at") or "")[:10] == day]
        buys = [f"{o['symbol']} {o.get('filled_qty')}@{_f(o.get('filled_avg_price')) or 0:.2f}"
                for o in fills if o.get("side") == "buy"]
        stops = [f"{o['symbol']} @{_f(o.get('filled_avg_price')) or 0:.2f}"
                 for o in fills if o.get("side") == "sell" and o.get("type") in ("stop", "stop_limit")]
        targets = [f"{o['symbol']} @{_f(o.get('filled_avg_price')) or 0:.2f}"
                   for o in fills if o.get("side") == "sell" and o.get("type") == "limit"]
        other = [f"{o['symbol']} @{_f(o.get('filled_avg_price')) or 0:.2f}"
                 for o in fills if o.get("side") == "sell" and o.get("type") == "market"]
        if buys:
            lines.append("  נקנו: " + ", ".join(buys))
        if stops:
            lines.append("  סטופ הופעל: " + ", ".join(stops))
        if targets:
            lines.append("  יעד הושג: " + ", ".join(targets))
        if other:
            lines.append("  נמכרו: " + ", ".join(other))
    try:
        sims = fund_sim_rows(today=day)
    except Exception:
        sims = []
    first = True
    for sim in sims:
        if sim.get("error"):
            continue
        eq, chg = sim["equity"], sim["day_change"]
        pct = chg / (eq - chg) * 100 if eq - chg else 0.0
        lines.append(f"{chr(10) if first else ''}{sim['label']}: ${eq:,.0f} "
                     f"({'+' if chg >= 0 else ''}{chg:,.0f}, "
                     f"{'+' if pct >= 0 else ''}{pct:.2f}%) | תשואה מ-{sim['since']}: "
                     f"{sim['ret'] * 100:+.2f}%")
        first = False
    return "\n".join(lines)


def summary_tick() -> None:
    """פעם ביום מסחר, אחרי 16:10 בניו יורק, כשהשוק סגור."""
    if not accounts_available():
        return
    ok, clock = api("GET", f"{base()}/v2/clock")
    if not ok or clock.get("is_open"):
        return
    try:
        now = datetime.fromisoformat(str(clock.get("timestamp")).replace("Z", "+00:00"))
    except ValueError:
        return
    day = now.date()
    if not swing.is_trading_day(day) or (now.hour, now.minute) < (16, 10):
        return
    state = _remind_state()
    if state.get("summary") == day.isoformat():
        return
    state["summary"] = day.isoformat()
    _remind_save(state)
    telegram_send(day_summary(day.isoformat()))


TG_HELP = ("פקודות: /plan - כל התוכניות לאישור; /swing, /graham - תוכנית אחת; "
           "/summary - סיכום היום; /link - קישור כניסה חדש למסך.\nשום פקודה לא נשלחת בלי לחיצה על אישור.")


def tg_handle_message(msg: dict) -> None:
    chat = tg_chat()
    if not chat or str((msg.get("chat") or {}).get("id", "")) != chat:
        return
    cmd = str(msg.get("text") or "").strip().split("@")[0].lower()
    if cmd == "/link":
        if not LINK_BASE:
            tg_call("sendMessage", chat_id=chat, text="המסך לא רץ עם --lan או --public, אין קישור מהטלפון.")
            return
        tg_call("sendMessage", chat_id=chat, disable_web_page_preview=True, text=link_message(fresh_link()))
        return
    if cmd == "/summary":
        ok, clock = api("GET", f"{base()}/v2/clock")
        day = str(clock.get("timestamp", ""))[:10] if ok else date.today().isoformat()
        tg_call("sendMessage", chat_id=chat, text=day_summary(day))
        return
    kinds = {"/plan": list(PLAN_KINDS), "/swing": ["swing"], "/graham": ["graham"]}.get(cmd)
    if kinds is None:
        if cmd.startswith("/"):
            tg_call("sendMessage", chat_id=chat, text=TG_HELP)
        return
    tg_offer(kinds)


def tg_loop() -> None:
    """מאזין לכפתורים ולפקודות (long polling). רק מופע אחד של השרת צריך לרוץ."""
    # מדלגים על מה שנלחץ לפני שהשרת עלה: אחרי הפעלה מחדש אין תוכניות ממתינות
    old = tg_call("getUpdates", offset=-1, timeout=0) or []
    if old:
        _tg["offset"] = old[-1]["update_id"] + 1
    while True:
        try:
            _tg_poll_once()
        except Exception as exc:  # noqa: BLE001 - המאזין לא מת: אחרת כפתורי האישור מפסיקים לעבוד בשקט
            try:
                print(f"טלגרם: שגיאה במאזין ({type(exc).__name__}: {exc})", flush=True)
            except Exception:  # noqa: BLE001
                pass
            time.sleep(5)


def _tg_poll_once() -> None:
    if True:
        ups = tg_call("getUpdates", offset=_tg["offset"], timeout=50,
                      allowed_updates=["message", "callback_query"])
        if ups is None:
            print("טלגרם: getUpdates נכשל, מנסה שוב", flush=True)
            time.sleep(10)
            return
        for u in ups:
            _tg["offset"] = u["update_id"] + 1
            try:
                if "callback_query" in u:
                    print(f"טלגרם: לחיצה {str(u['callback_query'].get('data', ''))[:3]}", flush=True)
                    tg_handle_callback(u["callback_query"])
                elif "message" in u:
                    print(f"טלגרם: פקודה {str(u['message'].get('text', ''))[:20]}", flush=True)
                    tg_handle_message(u["message"])
            except Exception as exc:  # noqa: BLE001 - עדכון אחד שנכשל לא עוצר את המאזין
                print(f"טלגרם: שגיאה בטיפול בעדכון ({type(exc).__name__}: {exc})", flush=True)
                tg_call("sendMessage", chat_id=tg_chat(),
                        text=f"שגיאה בטיפול בלחיצה/פקודה: {type(exc).__name__}: {exc}"[:500])


def link_message(link: str, head: str = "קישור כניסה חדש למסך המסחר:") -> str:
    return (f"{head}\n{link}\n\nהקישור מאפשר לשלוח פקודות. אל תעביר אותו הלאה.\n"
            "כל קישור קודם בטל: מכשיר מאושר שייכנס עם קישור ישן יוסר עד שייכנס עם זה.")


def send_link_telegram(link: str, where: str = "על ה-Wi-Fi של הבית") -> str:
    """שולח את קישור הכניסה לטלפון. מחזיר שורה להדפסה. לא זורק שגיאות."""
    load_env()
    bot = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not bot:
        return ("טלגרם: לא מוגדר. להפעלה: צור בוט ב-@BotFather, הוסף ל-.env שורה "
                "TELEGRAM_BOT_TOKEN=..., שלח לבוט הודעה כלשהי, והפעל מחדש.")
    chat = telegram_chat(bot)
    if not chat:
        return "טלגרם: לא מצאתי צ'אט. שלח לבוט הודעה כלשהי (למשל /start) והפעל מחדש."
    text = link_message(link, f"מסך המסחר עלה. לפתוח בטלפון, {where}:")
    try:
        r = requests.post(f"https://api.telegram.org/bot{bot}/sendMessage", timeout=15,
                          json={"chat_id": chat, "text": text,
                                "disable_web_page_preview": True})
        if r.ok and r.json().get("ok"):
            return "טלגרם: הקישור נשלח לטלפון."
        return f"טלגרם: השליחה נכשלה ({r.status_code}). בדוק את TELEGRAM_BOT_TOKEN ו-TELEGRAM_CHAT_ID."
    except (requests.RequestException, ValueError) as exc:
        return f"טלגרם: אין חיבור ({type(exc).__name__})."



class LazyTLS:
    """עוטף SSLContext כך שלחיצת היד של TLS תקרה בתהליכון של החיבור, לא בלולאת
    ה-accept הראשית. בלי זה, לקוח אחד שפותח חיבור ולא משלים לחיצת יד (סורק,
    לקוח תקוע) משתק את כל השרת - ובאינטרנט הפתוח זה קורה כל הזמן."""

    def __init__(self, ctx):
        self.ctx = ctx

    def wrap_socket(self, sock, server_side=True, **_):
        return self.ctx.wrap_socket(sock, server_side=True, do_handshake_on_connect=False)


def request_handler():
    """מטפל בקשות עם זמן קצוב לחיבור: לקוח איטי או תקוע משחרר את התהליכון."""
    from werkzeug.serving import WSGIRequestHandler

    class TimedHandler(WSGIRequestHandler):
        timeout = 30
    return TimedHandler


def start_public(port: int):
    """--public: תעודה (מנפיק/מחדש לפי הצורך), HTTPS לכולם ו-HTTP מקומי למחשב."""
    import ssl
    import threading
    from werkzeug.serving import make_server
    import cert

    host = os.environ.get("PUBLIC_HOST", "").strip()
    if not host:
        print("חסר PUBLIC_HOST ב-.env (למשל PUBLIC_HOST=graham-daily.myddns.me).")
        sys.exit(1)
    mode = os.environ.get("CERT_MODE", "private").strip() or "private"
    had_ca = cert.CA_EXPORT.exists()
    try:
        changed, msg = cert.ensure(host, mode=mode)
        print("תעודה: " + msg)
        if mode == "private" and not had_ca and cert.CA_EXPORT.exists():
            print("  " + send_ca_telegram(host))
    except Exception as exc:
        print(f"תעודה: לא הצלחתי להנפיק או לחדש ({exc})")
        if cert.days_left() is None:
            sys.exit(1)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(cert.FULLCHAIN, cert.PRIVKEY)

    def renew_daily():
        while True:
            time.sleep(24 * 3600)
            try:
                changed, msg = cert.ensure(host, mode=mode)
                if changed:
                    ctx.load_cert_chain(cert.FULLCHAIN, cert.PRIVKEY)   # בלי הפעלה מחדש
                print("תעודה: " + msg)
            except Exception as exc:
                print(f"תעודה: החידוש נכשל ({exc})")
    threading.Thread(target=renew_daily, daemon=True).start()

    local = make_server("127.0.0.1", LOCAL_PORT, app, threaded=True)
    threading.Thread(target=local.serve_forever, daemon=True).start()
    return host, ctx


class _SafeStream:
    """פלט שלא זורק: אם קובץ הלוג נעלם (סנכרון Drive), הדפסה לא מפילה את התהליכון."""

    def __init__(self, s):
        self._s = s

    def write(self, x):
        try:
            return self._s.write(x)
        except (OSError, ValueError, AttributeError):
            return 0

    def flush(self):
        try:
            self._s.flush()
        except (OSError, ValueError, AttributeError):
            pass

    def __getattr__(self, name):
        return getattr(self._s, name)


def main() -> int:
    global LIVE, REMOTE, PUBLIC, LINK_BASE
    sys.stdout, sys.stderr = _SafeStream(sys.stdout), _SafeStream(sys.stderr)
    LIVE = "--live" in sys.argv
    PUBLIC = "--public" in sys.argv
    REMOTE = PUBLIC or "--lan" in sys.argv
    port = 5000
    for i, a in enumerate(sys.argv):
        if a == "--port" and i + 1 < len(sys.argv):
            port = int(sys.argv[i + 1])

    if not creds():
        print("אזהרה: לא נמצאו מפתחות. המסך ייפתח אבל לא יוכל לדבר עם אלפקה.")
        print("       צור קובץ .env בתיקייה הזאת עם ALPACA_API_KEY_ID ו-ALPACA_API_SECRET_KEY.\n")
    if LIVE:
        print("*** מצב חשבון אמיתי. כסף אמיתי. ***\n")

    ctx = None
    if PUBLIC:
        load_env()
        host, ctx = start_public(port)
        print(f"המסך רץ. במחשב הזה:  http://127.0.0.1:{LOCAL_PORT}")
        LINK_BASE = f"https://{host}:{port}/"
        link = fresh_link()
        print("\nגישה מבחוץ (HTTPS, טוקן או מכשיר מאושר). פתח פעם אחת בכל מכשיר:")
        print(f"  {link}")
        print("  " + send_link_telegram(link, "מכל מקום"))
    else:
        print(f"המסך רץ. פתח בדפדפן:  http://127.0.0.1:{port}")
    if REMOTE and not PUBLIC:
        ip = lan_ip()
        print("\nגישה מהטלפון (רשת ביתית בלבד, הטלפון על אותו Wi-Fi). פתח פעם אחת בטלפון:")
        if ip:
            LINK_BASE = f"http://{ip}:{port}/"
            link = fresh_link()
            print(f"  {link}")
            print("  " + send_link_telegram(link))
        else:
            print(f"  http://<כתובת המחשב ברשת>:{port}/?t={rotate_token()}")
            print("  (לא מצאתי כתובת רשת פנימית; בדוק עם ipconfig)")
    if REMOTE:
        print("  אל תשתף את הכתובת: הטוקן שבה מאפשר לשלוח פקודות.")
    threading.Thread(target=remind_loop, daemon=True).start()
    if os.environ.get("TELEGRAM_BOT_TOKEN", "").strip():
        threading.Thread(target=tg_loop, daemon=True).start()
        print("טלגרם: אישור קניות פעיל (/plan בבוט).")
    print("לעצירה: Ctrl+C\n")
    # בלי --lan/--public: ‏127.0.0.1 בלבד. עם --lan: כל הממשקים, אבל guard() מקבל
    # רק את המחשב והרשת הפנימית, ודורש טוקן או מכשיר מאושר מהרשת. עם --public:
    # HTTPS לכל כתובת, ותמיד טוקן או מכשיר מאושר (חוץ מהמחשב עצמו).
    app.run(host="0.0.0.0" if REMOTE else "127.0.0.1", port=port, debug=False,
            ssl_context=LazyTLS(ctx) if ctx else None, threaded=True,
            request_handler=request_handler())
    return 0

if __name__ == "__main__":
    sys.exit(main())
