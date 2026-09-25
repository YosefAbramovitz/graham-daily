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
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import requests
from flask import Flask, jsonify, request, send_from_directory

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
    charts = {}
    if syms and creds():
        ok, data = api("GET", f"{DATA_BASE}/v2/stocks/trades/latest",
                       params={"symbols": ",".join(syms)})
        if ok:
            for sym, t in (data.get("trades") or {}).items():
                quotes[sym] = t.get("p")
        ok, data = api("GET", f"{DATA_BASE}/v2/stocks/bars",
                       params={"symbols": ",".join(syms), "timeframe": "1Day",
                               "start": (date.today() - timedelta(days=90)).isoformat(),
                               "end": (date.today() + timedelta(days=1)).isoformat(),
                               "limit": 100, "feed": "iex", "sort": "asc"})
        if ok:
            for sym, bars in (data.get("bars") or {}).items():
                charts[sym] = [
                    round(float(bar["c"]), 4)
                    for bar in bars
                    if bar.get("c") is not None
                ]
    for sym in syms:
        if len(charts.get(sym, [])) >= 2:
            continue
        try:
            yahoo = requests.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}",
                params={"range": "3mo", "interval": "1d", "events": "history"},
                headers={"User-Agent": "Mozilla/5.0 (compatible; graham-daily/1.0)"},
                timeout=10,
            )
            yahoo.raise_for_status()
            result = (yahoo.json().get("chart") or {}).get("result") or []
            quote = ((result[0].get("indicators") or {}).get("quote") or [{}])[0]
            charts[sym] = [
                round(float(close), 4)
                for close in (quote.get("close") or [])
                if close is not None
            ]
        except (requests.RequestException, ValueError, IndexError, KeyError, TypeError):
            charts.setdefault(sym, [])
    for r in rows:
        r["last"] = quotes.get(r["ticker"]) or r["price"]
        r["chart"] = charts.get(r["ticker"], [])
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
    ok_o, orders = api("GET", f"{base()}/v2/orders", params={"status": "open", "limit": 500})
    orders = flatten_orders(orders) if ok_o and isinstance(orders, list) else None
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
            **{f"exit_{k}": v for k, v in exit_state(orders, sym, today).items()},
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
