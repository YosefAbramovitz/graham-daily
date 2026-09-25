"""
שליחת פקודות לאלפקה מהמחשב שלך.

למה סקריפט ולא כפתור בדף
------------------------
הדף יושב על GitHub Pages, אתר ציבורי. מפתח שיושב שם גלוי לכל מי שנכנס.
כאן המפתח נשאר אצלך, והפקודה נשלחת מהמחשב שלך.

למה אין כאן לולאת ניטור
-----------------------
פקודת bracket שולחת את הכניסה, היעד והסטופ כיחידה אחת. משם **אלפקה**
שומרת עליהם: כשהמחיר נוגע בסטופ הפקודה מתבצעת אצלה, וכשהיעד נפגע הסטופ
מתבטל מעצמו. סקריפט שמנטר כל דקה מהמחשב שלך גרוע מזה בכל מדד - הוא עובד
רק כשהמחשב דולק, מפספס תנועות בתוך הדקה, ונעלם בדיוק כשהוא הכי נחוץ.

הדבר היחיד שאלפקה לא יודעת לעשות הוא לסגור לפי זמן, ולכן ``check``
בודק את מועדי היציאה. זו בדיקה יומית, לא דקתית.

התקנה
-----
    pip install alpaca-py requests

מפתחות - שתי אפשרויות, שתיהן משאירות אותם מחוץ לקוד::

    # אפשרות א: קובץ .env בתיקייה הזאת
    ALPACA_API_KEY_ID=PK...
    ALPACA_API_SECRET_KEY=...

    # אפשרות ב: משתני סביבה
    export ALPACA_API_KEY_ID=PK...
    export ALPACA_API_SECRET_KEY=...

**אל תוסיף את .env ל-git.** יש בקשה ל-.gitignore בדיוק בשביל זה.

שימוש
-----
    python order.py buy LULU --entry 182.40 --qty 10              # יעד בלבד
    python order.py buy LULU --entry 182.40 --qty 10 --with-stop  # יעד וסטופ
    python order.py positions
    python order.py check                  # כלל המכירה ומצב פקודות היציאה
    python order.py renew LULU             # חידוש פקודת יציאה שפגה או עומדת לפוג

אין סטופ כברירת מחדל: אצל גראהם ירידת מחיר בלי שינוי בשווי היא סיבה לקנות.
ואלפקה מבטלת פקודות GTC אחרי תשעים יום, כך שפקודת היציאה צריכה חידוש
במהלך אחזקה של שנתיים. ``check`` מתריע על כך ו-``renew`` מחדש.

הסקריפט מציג כל פקודה במלואה ומחכה שתקליד BUY לפני שליחה. הוא עובד מול
חשבון הנייר בלבד; מעבר לחשבון אמיתי דורש ``--live`` וגם אישור נוסף.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import requests

from exit_orders import (EXPIRY_NOTE, describe_exit, entry_order_body,
                         exit_order_body, exit_orders_for, exit_state,
                         flatten_orders, needs_renewal)

PAPER_BASE = "https://paper-api.alpaca.markets"
LIVE_BASE = "https://api.alpaca.markets"
DATA_BASE = "https://data.alpaca.markets"

# אותם מספרים בדיוק כמו בדף. גראהם קובע את היעד ואת השעון, ATR את הסטופ.
PROFIT_TARGET = 0.50
ATR_STOP_MULT = 2.0
HOLD_YEARS = 2
FALLBACK_STOP_PCT = 0.15
MAX_ENTRY_ABOVE_MARKET = 0.03   # כמו במסך: מעל זה, כנראה טעות הקלדה או מחיר ישן

TECH_CSV = ("https://raw.githubusercontent.com/YosefAbramovitz/graham-daily/"
            "main/tech_results.csv")


# ---------------------------------------------------------------------------
# מפתחות
# ---------------------------------------------------------------------------

def load_env() -> None:
    """קורא .env מהתיקייה הזאת, בלי לדרוס משתני סביבה קיימים."""
    path = Path(__file__).with_name(".env")
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


def credentials() -> tuple:
    load_env()
    key = os.environ.get("ALPACA_API_KEY_ID", "").strip()
    secret = os.environ.get("ALPACA_API_SECRET_KEY", "").strip()
    if not key or not secret:
        sys.exit(
            "חסרים מפתחות.\n"
            "  צור קובץ .env בתיקייה הזאת עם שתי השורות:\n"
            "    ALPACA_API_KEY_ID=...\n"
            "    ALPACA_API_SECRET_KEY=...\n"
            "  את המפתחות מוצאים באלפקה תחת Home, בקטע API Keys.\n"
            "  ודא שאלה המפתחות של חשבון ה-Paper ולא של חשבון אמיתי."
        )
    return key, secret


def headers() -> dict:
    key, secret = credentials()
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret,
            "Content-Type": "application/json"}


def base_url(live: bool) -> str:
    return LIVE_BASE if live else PAPER_BASE


# ---------------------------------------------------------------------------
# נתונים
# ---------------------------------------------------------------------------

def last_price(symbol: str) -> Optional[float]:
    try:
        r = requests.get(f"{DATA_BASE}/v2/stocks/{symbol}/trades/latest",
                         headers=headers(), timeout=15)
        if r.status_code == 200:
            return float(r.json()["trade"]["p"])
    except (requests.RequestException, KeyError, ValueError, TypeError):
        pass
    return None


_tech_cache: Optional[dict] = None


def atr_pct_for(symbol: str) -> Optional[float]:
    """ATR באחוזים מתוך תוצאות השלב הטכני, כדי שהסטופ יתאים לדף."""
    global _tech_cache
    if _tech_cache is None:
        _tech_cache = {}
        try:
            import csv, io
            r = requests.get(TECH_CSV, timeout=20)
            if r.status_code == 200:
                for row in csv.DictReader(io.StringIO(r.text)):
                    v = (row.get("atr_pct") or "").strip()
                    if v:
                        try:
                            _tech_cache[row["ticker"].strip().upper()] = float(v)
                        except ValueError:
                            pass
        except requests.RequestException:
            pass
    return _tech_cache.get(symbol.strip().upper())


def deadline_for(entry: date) -> date:
    return date(entry.year + HOLD_YEARS, 12, 31)


def levels(entry: float, atr_pct: Optional[float]) -> dict:
    tp = entry * (1 + PROFIT_TARGET)
    if atr_pct and atr_pct > 0:
        dist, how = entry * (atr_pct / 100) * ATR_STOP_MULT, f"{ATR_STOP_MULT}×ATR ({atr_pct:.1f}%)"
    else:
        dist, how = entry * FALLBACK_STOP_PCT, f"{FALLBACK_STOP_PCT*100:.0f}% (אין ATR)"
    sl = max(round(entry - dist, 2), 0.01)
    return {"tp": round(tp, 2), "sl": sl, "how": how,
            "rr": (tp - entry) / (entry - sl) if entry > sl else float("nan")}


# ---------------------------------------------------------------------------
# הפקודות
# ---------------------------------------------------------------------------

def confirm(word: str) -> bool:
    """אישור מפורש. Enter לבדו לא מספיק, בכוונה."""
    try:
        got = input(f"\nהקלד {word} כדי לשלוח, כל דבר אחר מבטל: ").strip()
    except (EOFError, KeyboardInterrupt):
        return False
    return got == word


def open_orders(live: bool):
    """כל הפקודות הפתוחות, משוטחות. None אם המשיכה נכשלה - וזה לא אותו דבר כמו ריק."""
    try:
        r = requests.get(f"{base_url(live)}/v2/orders", headers=headers(),
                         params={"status": "open", "limit": 500}, timeout=20)
    except requests.RequestException:
        return None
    if r.status_code != 200:
        return None
    return flatten_orders(r.json())


def cmd_buy(args) -> int:
    sym = args.symbol.strip().upper()
    entry = args.entry
    if entry is None:
        entry = last_price(sym)
        if entry is None:
            sys.exit(f"לא הצלחתי למשוך מחיר ל-{sym}. ציין --entry ידנית.")
        print(f"מחיר אחרון של {sym}: {entry:.2f}")
    else:
        # קנייה ב-limit מעל השוק מתבצעת מיד במחיר השוק, והיעד והסטופ נשארים
        # מחושבים ממחיר שלא שולם; סטופ כזה יכול לצאת מעל המחיר ולמכור מיד.
        market = last_price(sym)
        if market and entry > market * (1 + MAX_ENTRY_ABOVE_MARKET):
            sys.exit(f"מחיר הכניסה {entry:.2f} גבוה ב-{(entry / market - 1) * 100:.0f}% "
                     f"מהמחיר בשוק ({market:.2f}). עדכן את --entry.")

    use_stop = bool(args.with_stop or args.stop is not None)
    atr = args.atr if args.atr is not None else atr_pct_for(sym)
    L = levels(entry, atr)
    if args.stop is not None:
        L["sl"], L["how"] = args.stop, "ידני"
    if args.target is not None:
        L["tp"] = args.target
    if L["tp"] <= entry:
        sys.exit("היעד חייב להיות מעל מחיר הכניסה.")
    if use_stop and not L["sl"] < entry:
        sys.exit("הסטופ חייב להיות מתחת למחיר הכניסה.")

    reward = (L["tp"] - entry) * args.qty
    dl = deadline_for(date.today())
    if use_stop:
        kind = "bracket"
        stop_line = f"│  סטופ    stop   {L['sl']:>10.2f}   ({L['how']})"
        risk_line = (f"│  סיכון עד הסטופ {(entry - L['sl']) * args.qty:.2f}$"
                     f"    תשואה ביעד {reward:.2f}$")
    else:
        kind = "OTO"
        stop_line = "│  סטופ    ללא   (ברירת המחדל, לפי גראהם)"
        risk_line = (f"│  בלי סטופ, הסכום כולו בסיכון: {entry * args.qty:.2f}$"
                     f"    תשואה ביעד {reward:.2f}$")

    print(f"""
┌─ פקודת {kind} ─────────────────────────────
│  {sym}   כמות {args.qty}   חשבון {'אמיתי' if args.live else 'נייר'}
│
│  כניסה   limit  {entry:>10.2f}
│  יעד     limit  {L['tp']:>10.2f}   (+{(L['tp'] / entry - 1) * 100:.0f}%)
{stop_line}
│
{risk_line}
│  מועד יציאה אחרון: {dl.isoformat()}
└─────────────────────────────────────────────""")
    print(EXPIRY_NOTE)
    if use_stop:
        print("  הסטופ אינו חלק משיטת גראהם: אצלו ירידת מחיר בלי שינוי בשווי היא\n"
              "  סיבה לקנות, לא למכור.")
    else:
        print("  להוספת סטופ לפי ATR: --with-stop.")

    if args.live:
        print("\n  *** חשבון אמיתי. כסף אמיתי. ***")
        if not confirm("LIVE"):
            print("בוטל."); return 1
    if not confirm("BUY"):
        print("בוטל."); return 1

    body = entry_order_body(sym, args.qty, entry, L["tp"], L["sl"] if use_stop else None)
    r = requests.post(f"{base_url(args.live)}/v2/orders", headers=headers(),
                      data=json.dumps(body), timeout=30)
    if r.status_code not in (200, 201):
        print(f"\nאלפקה דחתה את הפקודה ({r.status_code}):\n{r.text}")
        return 1

    o = r.json()
    print(f"\nנשלח. מזהה {o.get('id')}, סטטוס {o.get('status')}")
    if o.get("expires_at"):
        print(f"הפקודה פוקעת אצל אלפקה ב-{str(o['expires_at'])[:10]}.")
    row = f"{sym},{date.today().isoformat()},{entry:.2f},{args.qty},"
    print(f"\nהוסף את השורה הזאת ל-positions.csv במאגר כדי שמועד היציאה ייאכף:\n  {row}")
    return 0

def cmd_positions(args) -> int:
    r = requests.get(f"{base_url(args.live)}/v2/positions", headers=headers(), timeout=20)
    if r.status_code != 200:
        print(f"שגיאה {r.status_code}: {r.text}"); return 1
    pos = r.json()
    if not pos:
        print("אין פוזיציות פתוחות."); return 0
    print(f"{'סימול':<8}{'כמות':>8}{'עלות':>10}{'שוק':>10}{'רווח':>10}")
    for p in pos:
        pl = float(p["unrealized_plpc"]) * 100
        print(f"{p['symbol']:<8}{p['qty']:>8}{float(p['avg_entry_price']):>10.2f}"
              f"{float(p['current_price']):>10.2f}{pl:>9.1f}%")
    return 0


def cmd_check(args) -> int:
    """כלל המכירה של גראהם ומצב פקודות היציאה. מדווח, לא מוכר."""
    r = requests.get(f"{base_url(args.live)}/v2/positions", headers=headers(), timeout=20)
    if r.status_code != 200:
        print(f"שגיאה {r.status_code}: {r.text}"); return 1
    pos = r.json()
    if not pos:
        print("אין פוזיציות פתוחות.")
        return 0

    orders = open_orders(args.live)
    if orders is None:
        print("[אזהרה] לא הצלחתי למשוך את הפקודות הפתוחות; מצב פקודות היציאה לא ידוע.")

    opened = {}
    try:
        import csv
        path = Path(__file__).with_name("positions.csv")
        if path.exists():
            for row in csv.DictReader(path.open(encoding="utf-8-sig")):
                tk = (row.get("ticker") or "").strip().upper()
                d = (row.get("entry_date") or "").strip()[:10]
                if tk and d:
                    opened[tk] = datetime.strptime(d, "%Y-%m-%d").date()
    except (OSError, ValueError):
        pass

    today = date.today()
    alerts = renew = 0
    for p in pos:
        sym = p["symbol"]
        gain = float(p["unrealized_plpc"])
        entered = opened.get(sym)
        due = deadline_for(entered) if entered else None
        note = []
        if gain >= PROFIT_TARGET:
            note.append(f"יעד: {gain*100:.0f}% מעל 50%")
        if due and today > due:
            note.append(f"חלף המועד ({due.isoformat()})")
        elif due:
            note.append(f"נותרו {(due - today).days} ימים")
        else:
            note.append("אין תאריך כניסה ב-positions.csv")
        ex = exit_state(orders, sym, today)
        note.append(describe_exit(ex))
        flag = "מכירה" if (gain >= PROFIT_TARGET or (due and today > due)) else "החזקה"
        if flag == "מכירה":
            alerts += 1
        if needs_renewal(ex) and flag != "מכירה":
            renew += 1
        print(f"{sym:<8}{gain*100:>7.1f}%  {flag:<8}{'; '.join(note)}")

    print(f"\n{alerts} התראות מכירה. הסקריפט לא מוכר — ההחלטה שלך.")
    if renew:
        print(f"{renew} פוזיציות בלי פקודת יציאה פעילה, או שהיא פוקעת בקרוב.\n"
              "  לחידוש: python order.py renew SYMBOL")
    return 0


def cmd_renew(args) -> int:
    """מבטל את פקודות היציאה הפתוחות של סימול ושולח חדשה, לתשעים יום נוספים."""
    import time
    sym = args.symbol.strip().upper()
    base = base_url(args.live)
    r = requests.get(f"{base}/v2/positions/{sym}", headers=headers(), timeout=20)
    if r.status_code == 404:
        sys.exit(f"אין פוזיציה פתוחה ב-{sym}.")
    if r.status_code != 200:
        sys.exit(f"שגיאה {r.status_code}: {r.text}")
    p = r.json()
    qty = p.get("qty")
    avg = float(p.get("avg_entry_price") or 0)
    cur = float(p.get("current_price") or 0)
    if not qty or avg <= 0:
        sys.exit("לא הצלחתי לקרוא כמות ומחיר כניסה מהפוזיציה.")

    use_stop = bool(args.with_stop or args.stop is not None)
    L = levels(avg, args.atr if args.atr is not None else atr_pct_for(sym))
    target = args.target if args.target is not None else L["tp"]
    stop = (args.stop if args.stop is not None else L["sl"]) if use_stop else None
    if cur and target <= cur:
        sys.exit(f"המחיר ({cur:.2f}) כבר מעל היעד ({target:.2f}). לפי כלל גראהם זה זמן\n"
                 "למכור, לא לחדש יעד.")
    if stop is not None and cur and stop >= cur:
        sys.exit(f"הסטופ ({stop:.2f}) לא מתחת למחיר הנוכחי ({cur:.2f}).")

    orders = open_orders(args.live)
    if orders is None:
        sys.exit("לא הצלחתי למשוך את הפקודות הפתוחות. לא ממשיך בלי לדעת מה קיים.")
    mine = exit_orders_for(orders, sym)

    print(f"""
┌─ חידוש פקודת יציאה ─────────────────────────
│  {sym}   כמות {qty}   מחיר כניסה ממוצע {avg:.2f}   נוכחי {cur:.2f}
│
│  יבוטלו: {len(mine)} פקודות מכירה פתוחות
│  יעד     limit  {target:>10.2f}
│  סטופ    {'stop   ' + format(stop, '>10.2f') if stop is not None else 'ללא'}
│  סוג     {'OCO' if stop is not None else 'limit'}, GTC לתשעים יום
└─────────────────────────────────────────────""")
    if args.live:
        print("\n  *** חשבון אמיתי. כסף אמיתי. ***")
        if not confirm("LIVE"):
            print("בוטל."); return 1
    if not confirm("RENEW"):
        print("בוטל."); return 1

    ids = [o["id"] for o in mine if o.get("id")]
    for oid in ids:
        d = requests.delete(f"{base}/v2/orders/{oid}", headers=headers(), timeout=20)
        if d.status_code not in (200, 204, 404, 422):
            sys.exit(f"ביטול {oid} נכשל ({d.status_code}): {d.text}\nלא נשלחה פקודה חדשה.")
    # הביטול אצל אלפקה אסינכרוני; פקודה חדשה לפני שהכמות משתחררת תידחה.
    for _ in range(15):
        left = open_orders(args.live)
        if left is not None and not any(o.get("id") in ids for o in left):
            break
        time.sleep(1)

    r = requests.post(f"{base}/v2/orders", headers=headers(),
                      data=json.dumps(exit_order_body(sym, qty, target, stop)), timeout=30)
    if r.status_code not in (200, 201):
        print(f"\nהפקודות הישנות בוטלו, אבל אלפקה דחתה את החדשה ({r.status_code}):\n{r.text}")
        print("הפוזיציה כרגע בלי פקודת יציאה. נסה שוב בעוד רגע.")
        return 1
    o = r.json()
    exp = str(o.get("expires_at") or "")[:10]
    print(f"\nנשלח. מזהה {o.get('id')}, סטטוס {o.get('status')}"
          + (f", פוקעת ב-{exp}" if exp else ""))
    return 0

def main() -> int:
    ap = argparse.ArgumentParser(description="פקודות לאלפקה מהמחשב שלך")
    ap.add_argument("--live", action="store_true",
                    help="חשבון אמיתי במקום נייר. דורש אישור נוסף.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("buy", help="קנייה עם יעד של 50%%. סטופ רק כשמבקשים")
    b.add_argument("symbol")
    b.add_argument("--qty", type=int, required=True)
    b.add_argument("--entry", type=float, help="מחיר כניסה. ריק = המחיר האחרון")
    b.add_argument("--with-stop", action="store_true",
                   help="להוסיף סטופ לפי ATR. לא חלק משיטת גראהם")
    b.add_argument("--stop", type=float, help="סטופ ידני. מפעיל סטופ")
    b.add_argument("--target", type=float, help="דריסת היעד של 50%%")
    b.add_argument("--atr", type=float, help="ATR באחוזים, אם אינו בקובץ")
    b.set_defaults(func=cmd_buy)

    p = sub.add_parser("positions", help="הפוזיציות הפתוחות")
    p.set_defaults(func=cmd_positions)

    c = sub.add_parser("check", help="כלל המכירה ומצב פקודות היציאה")
    c.set_defaults(func=cmd_check)

    rn = sub.add_parser("renew", help="חידוש פקודת היציאה של פוזיציה, לתשעים יום נוספים")
    rn.add_argument("symbol")
    rn.add_argument("--with-stop", action="store_true", help="יעד וסטופ יחד (OCO)")
    rn.add_argument("--stop", type=float, help="סטופ ידני. מפעיל סטופ")
    rn.add_argument("--target", type=float, help="דריסת היעד (ברירת מחדל: +50%% מהכניסה)")
    rn.add_argument("--atr", type=float, help="ATR באחוזים, אם אינו בקובץ")
    rn.set_defaults(func=cmd_renew)

    args = ap.parse_args()
    return args.func(args)

if __name__ == "__main__":
    sys.exit(main())
