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
import io
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import requests
from flask import Flask, jsonify, request, send_from_directory

HERE = Path(__file__).parent
PAPER_BASE = "https://paper-api.alpaca.markets"
LIVE_BASE = "https://api.alpaca.markets"
DATA_BASE = "https://data.alpaca.markets"

PROFIT_TARGET = 0.50
ATR_STOP_MULT = 2.0
HOLD_YEARS = 2
FALLBACK_STOP_PCT = 0.15

TECH_CSV = ("https://raw.githubusercontent.com/YosefAbramovitz/graham-daily/"
            "main/tech_results.csv")

app = Flask(__name__, static_folder=None)
LIVE = False          # נדרס מ---live בשורת ההפעלה


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
    if r.status_code not in (200, 201):
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
    today = date.today()
    out = []
    for p in data:
        sym = p["symbol"]
        gain = float(p.get("unrealized_plpc") or 0)
        ent = opened.get(sym)
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
            why.append("אין תאריך כניסה ב-positions.csv")
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
        })
    return jsonify({"rows": out})


@app.get("/api/orders")
def api_orders():
    ok, data = api("GET", f"{base()}/v2/orders", params={"status": "open", "limit": 100})
    if not ok:
        return jsonify(data), 502
    return jsonify({"rows": [{
        "id": o.get("id"), "ticker": o.get("symbol"), "side": o.get("side"),
        "type": o.get("type"), "qty": o.get("qty"),
        "limit_price": o.get("limit_price"), "stop_price": o.get("stop_price"),
        "status": o.get("status"), "class": o.get("order_class"),
    } for o in data]})


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
        stop = float(body.get("stop"))
    except (TypeError, ValueError):
        return jsonify({"error": "שדות חסרים או לא תקינים"}), 400
    if not sym or qty <= 0 or entry <= 0:
        return jsonify({"error": "סימול, כמות ומחיר חייבים להיות תקינים"}), 400
    if not (stop < entry < target):
        return jsonify({"error": "הסדר חייב להיות סטופ < כניסה < יעד"}), 400

    ok, data = api("POST", f"{base()}/v2/orders", data=json.dumps({
        "symbol": sym, "qty": str(qty), "side": "buy", "type": "limit",
        "limit_price": f"{entry:.2f}", "time_in_force": "gtc",
        "order_class": "bracket",
        "take_profit": {"limit_price": f"{target:.2f}"},
        "stop_loss": {"stop_price": f"{stop:.2f}"},
    }))
    if not ok:
        return jsonify(data), 502
    return jsonify({
        "id": data.get("id"), "status": data.get("status"),
        "csv_row": f"{sym},{date.today().isoformat()},{entry:.2f},{qty},",
    })


def main() -> int:
    global LIVE
    LIVE = "--live" in sys.argv
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
    print("לעצירה: Ctrl+C\n")
    # 127.0.0.1 בלבד. לא 0.0.0.0 - אין סיבה שמחשב אחר ברשת יראה את זה.
    app.run(host="127.0.0.1", port=port, debug=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
