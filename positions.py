"""
כלל המכירה של גראהם, כהתראה.

למה זה קיים
-----------
המערכת ידעה לומר מה לקנות ולא ידעה לומר מתי לצאת, ושם נחתכת רוב התשואה.
לגראהם היה כלל יציאה מפורש, והוא פרסם אותו ב-1976 בראיון ל-Medical Economics:
למכור כשהרווח מגיע ל-50%, ואם לא הגיע — למכור עד סוף השנה הקלנדרית השנייה
שאחרי הקנייה, מה שקורה קודם.

הכלל נראה שרירותי, וזה בדיוק העניין. הוא לא מנסה לתזמן את השוק; הוא מונע
משתי הטעויות שהכי עולות כסף: להתאהב במניה שעלתה, ולהחזיק לנצח במניה שלא
זזה כי "בסוף היא תתקן". גראהם טען שהכלל הזה, על סל מגוון, נתן כ-15% בשנה
לאורך כחמישים שנות בדיקה.

איך משתמשים
-----------
עורכים את ``positions.csv`` בשורה לכל קנייה::

    ticker,entry_date,entry_price,shares,note
    KO,2026-03-14,58.20,100,

הריצה היומית מוסיפה את המחיר הנוכחי ומחשבת מה הכלל אומר. שום דבר לא נמכר
אוטומטית — זו התראה בלבד, וההחלטה שלך.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime
from typing import Optional

import pandas as pd

PROFIT_TARGET = 0.50        # יעד הרווח של גראהם
HOLD_YEARS = 2              # עד סוף השנה הקלנדרית השנייה שאחרי הקנייה
NEAR_TARGET = 0.40          # מתחת לזה עוד לא "מתקרב"
NEAR_DEADLINE_DAYS = 90     # רבעון לפני המועד: כדאי להתחיל לחשוב

TEMPLATE = "ticker,entry_date,entry_price,shares,note\n"


def deadline_for(entry: date) -> date:
    """סוף השנה הקלנדרית השנייה שאחרי שנת הקנייה."""
    return date(entry.year + HOLD_YEARS, 12, 31)


def _parse_date(value) -> Optional[date]:
    text = str(value or "").strip()[:10]
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def verdict(entry_date: date, entry_price: float, price: Optional[float],
            today: Optional[date] = None) -> dict:
    """מה כלל גראהם אומר על הפוזיציה הזאת היום."""
    today = today or date.today()
    due = deadline_for(entry_date)
    days_left = (due - today).days

    out = {
        "entry_date": entry_date.isoformat(),
        "entry_price": round(entry_price, 4),
        "price": round(price, 4) if price else "",
        "deadline": due.isoformat(),
        "days_left": days_left,
        "gain": "",
        "target_price": round(entry_price * (1 + PROFIT_TARGET), 4),
        "action": "",
        "reason": "",
    }

    if not price or not entry_price:
        out["action"] = "אין מחיר"
        out["reason"] = "לא הצלחתי למשוך מחיר עדכני"
        return out

    gain = price / entry_price - 1.0
    out["gain"] = round(gain, 4)

    if gain >= PROFIT_TARGET:
        out["action"] = "מכירה"
        out["reason"] = f"הרווח הגיע ל-{gain*100:.0f}%, מעל יעד ה-50% של גראהם"
    elif days_left < 0:
        out["action"] = "מכירה"
        out["reason"] = f"חלף המועד ({due.isoformat()}), הרווח נעצר על {gain*100:.0f}%"
    elif gain >= NEAR_TARGET:
        out["action"] = "מתקרב ליעד"
        out["reason"] = f"{gain*100:.0f}% מתוך 50%"
    elif days_left <= NEAR_DEADLINE_DAYS:
        out["action"] = "מתקרב למועד"
        out["reason"] = f"נותרו {days_left} ימים עד {due.isoformat()}"
    else:
        out["action"] = "החזקה"
        out["reason"] = f"{gain*100:+.0f}%, נותרו {days_left} ימים"
    return out


def current_prices(tickers) -> dict:
    """מחיר אחרון לכל סימול. שגיאה במניה אחת לא מפילה את השאר."""
    prices = {}
    try:
        import yfinance as yf
    except ImportError:
        return prices
    for tk in tickers:
        try:
            hist = yf.Ticker(tk).history(period="5d", interval="1d", auto_adjust=False)
            if hist is not None and not hist.empty:
                prices[tk] = float(hist["Close"].dropna().iloc[-1])
        except Exception:  # noqa: BLE001
            continue
    return prices


def review(path: str, today: Optional[date] = None,
           prices: Optional[dict] = None) -> pd.DataFrame:
    """קורא את קובץ הפוזיציות ומחזיר טבלת סטטוס."""
    if not os.path.exists(path):
        return pd.DataFrame()
    try:
        df = pd.read_csv(path)
    except Exception:  # noqa: BLE001
        return pd.DataFrame()
    if df.empty or "ticker" not in df.columns:
        return pd.DataFrame()

    df["ticker"] = df["ticker"].astype(str).str.strip().str.upper()
    df = df[df["ticker"] != ""]
    if df.empty:
        return pd.DataFrame()

    if prices is None:
        prices = current_prices(sorted(set(df["ticker"])))

    rows = []
    for _, src in df.iterrows():
        tk = src["ticker"]
        entry = _parse_date(src.get("entry_date"))
        try:
            entry_price = float(src.get("entry_price"))
        except (TypeError, ValueError):
            entry_price = 0.0
        if entry is None or entry_price <= 0:
            rows.append({"ticker": tk, "action": "שורה לא תקינה",
                         "reason": "חסר תאריך כניסה או מחיר כניסה"})
            continue
        row = {"ticker": tk}
        row.update(verdict(entry, entry_price, prices.get(tk), today))
        for extra in ("shares", "note"):
            if extra in src.index:
                row[extra] = src.get(extra)
        rows.append(row)

    out = pd.DataFrame(rows)
    order = {"מכירה": 0, "מתקרב ליעד": 1, "מתקרב למועד": 2, "החזקה": 3}
    out["_o"] = out["action"].map(order).fillna(9)
    return out.sort_values(["_o", "ticker"]).drop(columns="_o").reset_index(drop=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="כלל המכירה של גראהם על הפוזיציות הפתוחות")
    ap.add_argument("--in", dest="infile", default="positions.csv")
    ap.add_argument("--out", default="positions_status.csv")
    args = ap.parse_args()

    if not os.path.exists(args.infile):
        with open(args.infile, "w", encoding="utf-8") as fh:
            fh.write(TEMPLATE)
        print(f"נוצר קובץ פוזיציות ריק: {args.infile}")

    out = review(args.infile)
    if out.empty:
        print("אין פוזיציות פתוחות.")
        pd.DataFrame(columns=["ticker", "action", "reason"]).to_csv(
            args.out, index=False, encoding="utf-8-sig")
        return 0

    out.to_csv(args.out, index=False, encoding="utf-8-sig")
    sells = (out["action"] == "מכירה").sum()
    print(out[["ticker", "action", "reason"]].to_string(index=False))
    print(f"\n{sells} התראות מכירה מתוך {len(out)} פוזיציות. נשמר ל-{args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
