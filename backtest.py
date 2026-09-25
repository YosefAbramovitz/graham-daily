"""
בדיקה לאחור של המסך, בלי לרמות.

השאלה
-----
כל סף שקבענו — EBIT/EV מעל 10%, רווחיות גולמית מעל 20% — מגיע ממחקרים על
יקום אחר ובתקופה אחרת. השאלה היחידה שבאמת חשובה היא אם הצירוף הזה, על
היקום הזה, הוסיף משהו. עד שלא בודקים, לא יודעים.

איך נמנעים מהטעות הרגילה
------------------------
הטעות שהורגת כל בדיקה לאחור ביתית היא להשתמש במספר שלא היה ידוע אז. דוח
שנתי של 2023 מתפרסם בפברואר 2024; מי שמחשב קריטריון ל-1 בינואר 2024 על
אותו דוח "קנה" עם מידע מהעתיד, והתוצאה תיראה מצוינת ולא תחזור לעולם.

ל-SEC יש לכל נתון שדה ``filed``. המודול הזה משתמש רק במה שהוגש עד תאריך
האיזון, ולכן הבעיה הזאת פשוט לא קיימת כאן. כשדוח הוגש מחדש עם מספרים
מתוקנים — נלקחת הגרסה המקורית, זו שהמשקיע ראה.

מה עדיין מוטה, ואי אפשר לתקן
-----------------------------
היקום הוא רשימת המדד של **היום**. חברות שפשטו רגל, נמחקו או נרכשו אינן בו.
זו הטיית שרידות, והיא מטה את התוצאה כלפי מעלה — כנראה באחוז עד שניים בשנה
לפי הספרות. בלי מסד נתונים בתשלום אי אפשר לתקן אותה, ולכן כל מספר שיוצא
מכאן צריך להיקרא כתקרה, לא כהערכה.

שאר ההסתייגויות: אין דיבידנדים בתשואה (רק שינוי מחיר מתואם), אין מס, ועלות
המסחר היא אומדן גס.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from typing import Dict, List, Optional

import pandas as pd

import sec_facts as sf
from graham_screener import (MAX_NET_DEBT_TO_EBITDA, MIN_EBIT_EV,
                             MIN_GROSS_PROFITABILITY, MIN_NET_PAYOUT_YIELD,
                             MIN_REVENUE_INDUSTRIAL)

DEFAULT_COST_BPS = 20        # עלות מסחר בכל צד, בנקודות בסיס
STABILITY_YEARS = 5          # כמה שנים של רווח חיובי נדרשות


# ---------------------------------------------------------------------------
# חישוב הקריטריונים בתאריך נתון
# ---------------------------------------------------------------------------

def metrics_at(compact: Dict[str, List[dict]], when: date,
               price: Optional[float]) -> Optional[dict]:
    """מחשב את מדדי המסך מנתונים שהיו ידועים בתאריך ``when``."""
    snap = sf.as_of(compact, when, years=STABILITY_YEARS + 1)
    history = snap.get("_history", {})

    def val(name):
        v = snap.get(name)
        return float(v) if isinstance(v, (int, float)) else None

    revenue = val("revenue")
    assets = val("assets")
    ebit = val("ebit")
    shares = val("shares_outstanding")
    if not price or not shares or not assets:
        return None

    market_cap = price * shares
    debt = sf.total_debt(val) or 0.0
    liquid = (val("cash") or 0.0) + (val("short_term_investments") or 0.0)
    ev = market_cap + debt - liquid

    gross = val("gross_profit")
    if gross is None and revenue is not None and val("cogs") is not None:
        gross = revenue - val("cogs")

    depreciation = val("depreciation")
    ebitda = (ebit + depreciation) if (ebit is not None and depreciation is not None) else None

    payout = (val("dividends_paid") or 0.0) + (val("buybacks") or 0.0)
    issued = val("stock_issued") or 0.0

    m = {
        "market_cap": market_cap,
        "revenue": revenue,
        "ebit_ev": (ebit / ev) if (ebit is not None and ev and ev > 0) else None,
        "gross_profitability": (gross / assets) if (gross is not None and assets) else None,
        "net_payout_yield": ((payout - issued) / market_cap) if market_cap else None,
        "net_debt_to_ebitda": ((debt - liquid) / ebitda) if (ebitda and ebitda > 0) else None,
        "last_filed": snap.get("_last_filed"),
    }

    earnings = history.get("net_income", [])
    m["years_of_data"] = len(earnings)
    m["earnings_stable"] = (len(earnings) >= STABILITY_YEARS
                            and all(e > 0 for e in earnings[:STABILITY_YEARS]))
    return m


def passes_screen(m: dict, thresholds: Optional[dict] = None) -> tuple:
    """הקריטריונים של המסך המודרני. מחזיר (עבר, מילון בדיקות).

    הספים ניתנים לדריסה כדי שאפשר יהיה לסרוק אותם. זה נועד לענות על שאלה
    מבנית - האם יש טווח שבו המסך מייצר תיק בגודל סביר - ולא לכייל עד
    שהמספר נראה יפה. סף שנבחר אחרי שראית את התוצאה הוא לא ממצא.
    """
    t = thresholds or {}
    checks = {
        "size": bool(m.get("revenue") is not None
                     and m["revenue"] >= t.get("min_revenue", MIN_REVENUE_INDUSTRIAL)),
        "stability": bool(m.get("earnings_stable")),
        "strength": bool(m.get("net_debt_to_ebitda") is not None
                         and m["net_debt_to_ebitda"] <= t.get("max_net_debt_to_ebitda",
                                                              MAX_NET_DEBT_TO_EBITDA)),
        "payout": bool(m.get("net_payout_yield") is not None
                       and m["net_payout_yield"] >= t.get("min_net_payout_yield",
                                                          MIN_NET_PAYOUT_YIELD)),
        "cheap": bool(m.get("ebit_ev") is not None
                      and m["ebit_ev"] >= t.get("min_ebit_ev", MIN_EBIT_EV)),
        "profitable": bool(m.get("gross_profitability") is not None
                           and m["gross_profitability"] >= t.get("min_gross_profitability",
                                                                 MIN_GROSS_PROFITABILITY)),
    }
    return all(checks.values()), checks


# ---------------------------------------------------------------------------
# מחירים
# ---------------------------------------------------------------------------

def _yahoo_closes(tickers: List[str], start: date, end: date) -> pd.DataFrame:
    """סגירות מתואמות מ-yfinance, בקבוצות של ארבעים."""
    import yfinance as yf
    frames = []
    for i in range(0, len(tickers), 40):
        chunk = tickers[i:i + 40]
        try:
            data = yf.download(chunk, start=start.isoformat(), end=end.isoformat(),
                               auto_adjust=True, progress=False, group_by="column",
                               threads=True)
        except Exception:  # noqa: BLE001
            continue
        if data is None or data.empty:
            continue
        if isinstance(data.columns, pd.MultiIndex):
            if "Close" not in data.columns.get_level_values(0):
                continue
            part = data["Close"]
        else:
            part = data[["Close"]].rename(columns={"Close": chunk[0]})
        frames.append(part)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, axis=1).sort_index()
    return out.loc[:, ~out.columns.duplicated()]


def load_prices(tickers: List[str], start: date, end: date) -> pd.DataFrame:
    """סגירות מתואמות יומיות, משני המקורות יחד.

    Alpaca הוא ממשק אמיתי ולכן המקור המועדף, אבל בתוכנית החינמית הוא מכסה
    כ-80% מהיקום ומתחיל ב-2016. yfinance מכסה טווח ארוך יותר ושמות אחרים,
    ונופל בדרכים משלו. לקחת את שניהם ולמלא חורים נותן כיסוי טוב יותר מכל
    אחד לבדו, וזה משנה: כל מניה חסרה יוצאת גם מהתיק וגם ממדד ההשוואה.
    """
    alpaca = pd.DataFrame()
    try:
        import alpaca_prices
        if alpaca_prices.available():
            print("מושך מחירים מ-Alpaca...", flush=True)
            alpaca = alpaca_prices.daily_closes(tickers, start, end, quiet=False)
            got = int(alpaca.notna().any().sum()) if not alpaca.empty else 0
            print(f"  Alpaca: {got}/{len(tickers)} סימולים", flush=True)
    except ImportError:
        pass

    have = set()
    if not alpaca.empty:
        have = {c for c in alpaca.columns if alpaca[c].notna().any()}
    missing = [t for t in tickers if t not in have]

    yahoo = pd.DataFrame()
    if missing:
        print(f"משלים {len(missing)} סימולים מ-yahoo...", flush=True)
        yahoo = _yahoo_closes(missing, start, end)
        got_y = int(yahoo.notna().any().sum()) if not yahoo.empty else 0
        print(f"  yahoo: {got_y}/{len(missing)} סימולים", flush=True)

    if alpaca.empty:
        return yahoo
    if yahoo.empty:
        return alpaca

    # איחוד: Alpaca קובע איפה שיש לו נתון, yahoo ממלא את השאר
    combined = alpaca.join(yahoo[[c for c in yahoo.columns if c not in alpaca.columns]],
                           how="outer")
    overlap = [c for c in yahoo.columns if c in alpaca.columns]
    for c in overlap:
        combined[c] = combined[c].combine_first(yahoo[c])
    return combined.sort_index()


def price_on(close: pd.DataFrame, ticker: str, when: date) -> Optional[float]:
    """המחיר האחרון בתאריך או לפניו. בלי להציץ קדימה."""
    if ticker not in close.columns:
        return None
    series = close[ticker].dropna()
    series = series[series.index.date <= when]
    if series.empty:
        return None
    return float(series.iloc[-1])


# ---------------------------------------------------------------------------
# הריצה
# ---------------------------------------------------------------------------

def rebalance_dates(start_year: int, end_year: int, month: int, day: int,
                    freq: str = "annual") -> List[date]:
    """תאריכי האיזון.

    איזון רבעוני נותן פי ארבעה תצפיות, וזו הדרך המעשית היחידה להגיע לכוח
    סטטיסטי על היסטוריה של עשר שנים. שימו לב שהתצפיות אינן בלתי תלויות -
    אותן מניות ממשיכות מרבעון לרבעון - ולכן גודל המדגם האפקטיבי קטן מפי
    ארבעה, וה-t שיוצא הוא גבול עליון אופטימי.
    """
    if freq == "quarterly":
        out = []
        for y in range(start_year, end_year + 1):
            for m in (month, month + 3, month + 6, month + 9):
                yy, mm = y + (m - 1) // 12, (m - 1) % 12 + 1
                d = min(day, 28)
                if yy <= end_year:
                    out.append(date(yy, mm, d))
        return sorted(set(out))
    return [date(y, month, day) for y in range(start_year, end_year + 1)]


def run(tickers: List[str], start_year: int, end_year: int,
        month: int = 6, day: int = 30, cost_bps: float = DEFAULT_COST_BPS,
        max_names: int = 30, quiet: bool = False,
        thresholds: Optional[dict] = None, freq: str = "annual") -> dict:
    """בדיקה לאחור עם איזון שנתי והחזקה שווה."""
    dates = rebalance_dates(start_year, end_year, month, day, freq)
    close = load_prices(tickers, dates[0] - timedelta(days=400),
                        dates[-1] + timedelta(days=400))
    if close.empty:
        raise RuntimeError("לא התקבלו מחירים בכלל")

    # כיסוי הנתונים נבדק לפני שמחשבים משהו. בדיקה לאחור שרצה על שליש
    # מהיקום מחזירה מספר שנראה סביר לגמרי ואינו אומר דבר, וזה בדיוק סוג
    # הכשל השקט שכבר עלה לנו ביום עבודה.
    with_prices = int(close.notna().any().sum())
    price_cover = with_prices / max(len(tickers), 1)
    first, last = close.index.min().date(), close.index.max().date()
    if not quiet:
        print(f"מחירים: {with_prices} מתוך {len(tickers)} מניות "
              f"({price_cover*100:.0f}%), {first} עד {last}", flush=True)
    if first > dates[0]:
        print(f"[אזהרה] המחירים מתחילים ב-{first}, אחרי תאריך האיזון הראשון "
              f"({dates[0]}). התקופה הראשונה תהיה ריקה.", flush=True)
    if price_cover < 0.5:
        raise RuntimeError(
            f"רק {with_prices} מתוך {len(tickers)} מניות קיבלו מחירים. "
            "זה נמוך מכדי להסיק משהו - בדוק את מקור המחירים לפני שתסמוך על התוצאה.")

    cik_map = sf.ticker_to_cik(quiet=quiet)
    if not cik_map:
        raise RuntimeError(
            "אין מיפוי מסימול ל-CIK. בלעדיו אי אפשר למשוך דוחות. "
            "בדוק שהסוד SEC_USER_AGENT מוגדר.")

    facts: Dict[str, dict] = {}
    unmapped = 0
    for i, tk in enumerate(tickers, 1):
        cik = cik_map.get(tk.upper())
        if cik is None:
            unmapped += 1
            continue
        compact = sf.company_facts(cik)
        if compact:
            facts[tk] = compact
        if not quiet and i % 50 == 0:
            print(f"  דוחות: {i}/{len(tickers)} (נמצאו {len(facts)})", flush=True)

    facts_cover = len(facts) / max(len(tickers), 1)
    if not quiet:
        print(f"דוחות: {len(facts)} מתוך {len(tickers)} מניות "
              f"({facts_cover*100:.0f}%), {unmapped} ללא CIK\n", flush=True)
    if facts_cover < 0.5:
        raise RuntimeError(
            f"רק {len(facts)} מתוך {len(tickers)} מניות קיבלו דוחות מה-SEC. "
            "התוצאה לא תהיה מייצגת.")

    coverage = {"tickers": len(tickers), "with_prices": with_prices,
                "with_facts": len(facts), "unmapped": unmapped}

    periods = []
    for i in range(len(dates) - 1):
        buy, sell = dates[i], dates[i + 1]
        picked, universe_returns = [], []
        priced = scored = 0

        for tk, compact in facts.items():
            p0 = price_on(close, tk, buy)
            p1 = price_on(close, tk, sell)
            if not p0 or not p1:
                continue
            priced += 1
            ret = p1 / p0 - 1.0
            universe_returns.append(ret)

            m = metrics_at(compact, buy, p0)
            if not m:
                continue
            scored += 1
            ok, checks = passes_screen(m, thresholds)
            if ok:
                picked.append({"ticker": tk, "ret": ret, "ebit_ev": m["ebit_ev"],
                               "gross_profitability": m["gross_profitability"]})

        # כשעוברים יותר מדי, נלקחות הזולות ביותר — כמו בשלב האיכות
        picked.sort(key=lambda r: r["ebit_ev"] or 0.0, reverse=True)
        held = picked[:max_names]

        cost = 2 * cost_bps / 10_000.0
        # תקופה שבה אף מניה לא עברה את המסך היא ישיבה במזומן, לא תשואת אפס
        # של תיק. ההבדל חשוב: אם חצי מהתקופות ריקות, ממוצע התשואה נראה נורא
        # ומתאר משהו אחר לגמרי ממה שנדמה. נספר ומדווח בנפרד.
        port = (sum(r["ret"] for r in held) / len(held) - cost) if held else 0.0
        bench = sum(universe_returns) / len(universe_returns) if universe_returns else 0.0

        periods.append({
            "buy": buy.isoformat(), "sell": sell.isoformat(),
            "n_priced": priced, "n_scored": scored,
            "n_passed": len(picked), "n_held": len(held),
            "portfolio": round(port, 4), "benchmark": round(bench, 4),
            "excess": round(port - bench, 4),
            "names": [r["ticker"] for r in held],
        })
        if not quiet:
            print(f"{buy.isoformat()} -> {sell.isoformat()}: "
                  f"יקום {priced}, נמדדו {scored}, "
                  f"עברו {len(picked)}, הוחזקו {len(held)}, "
                  f"תיק {port*100:+.1f}%, יקום {bench*100:+.1f}%, "
                  f"עודף {(port-bench)*100:+.1f}%", flush=True)

    out = summarise(periods, len(facts))
    out['coverage'] = coverage
    return out


UNIVERSE_JUMP_RATIO = 1.5    # שינוי של פי 1.5 בכיסוי בין תקופות נחשב קפיצה


def _span_years(periods: List[dict]) -> float:
    span_days = (date.fromisoformat(periods[-1]["sell"])
                 - date.fromisoformat(periods[0]["buy"])).days
    return max(span_days / 365.25, 1e-9)


def _annualised(periods: List[dict], key: str) -> float:
    """תשואה שנתית מצטברת.

    התשואה מוצמדת לזמן שחלף בפועל ולא למספר התקופות. באיזון רבעוני ארבעים
    תקופות הן עשר שנים, ולחלק בארבעים היה מנפח את התשואה השנתית פי ארבעה.
    """
    total = 1.0
    for p in periods:
        total *= (1 + p[key])
    return total ** (1 / _span_years(periods)) - 1


def universe_jumps(periods: List[dict], ratio: float = UNIVERSE_JUMP_RATIO) -> List[dict]:
    """תקופות שבהן מספר המניות עם מחיר השתנה בחדות.

    קפיצה כזאת היא כמעט תמיד כיסוי נתונים ולא שינוי אמיתי במדד. העודף בכל
    תקופה נשאר הוגן, כי התיק ומדד ההשוואה נמדדים על אותן מניות. אבל מה
    שמדד ההשוואה מייצג משתנה: לפני הקפיצה הוא בערך S&P 500, אחריה S&P 1500.
    """
    jumps = []
    for prev, cur in zip(periods, periods[1:]):
        a, b = prev.get("n_priced") or 0, cur.get("n_priced") or 0
        if a and b and max(a, b) / min(a, b) >= ratio:
            jumps.append({"at": cur["buy"], "from": a, "to": b})
    return jumps


def summarise(periods: List[dict], n_universe: int) -> dict:
    if not periods:
        return {"periods": [], "note": "אין תקופות"}

    # תקופות מזומן *בתחילת* הבדיקה, כשאף מניה מתוך מאות לא עוברת, הן סימן
    # לחור בנתונים ולא להחלטה של המסך. לספור אותן כאפס מול מדד עולה מעניש
    # את המסך על משהו שאינו קשור אליו. לכן המדידה מתחילה בתקופה המושקעת
    # הראשונה. מזומן *אחרי* זה נשאר בחישוב - שם זו באמת תוצאה של המסך.
    # החלון המלא נשמר ב-full_window, לשקיפות.
    first = next((i for i, p in enumerate(periods) if p["n_held"] > 0), None)
    measured = periods[first:] if first is not None else []

    invested = [p for p in measured if p["n_held"] > 0]
    wins = sum(1 for p in invested if p["excess"] > 0)

    out = {
        "periods": periods,
        "periods_count": len(periods),
        "years": round(_span_years(periods), 2),
        "measured_from": measured[0]["buy"] if measured else None,
        "measured_years": round(_span_years(measured), 2) if measured else 0.0,
        "leading_cash_periods": first if first is not None else len(periods),
        "periods_invested": len(invested),
        "periods_in_cash": len(measured) - len(invested),
        "periods_beating_benchmark": (f"{wins}/{len(invested)}" if invested
                                      else "אין תקופות מושקעות"),
        "universe_size": n_universe,
        "universe_jumps": universe_jumps(periods),
        "avg_names_held": (round(sum(p["n_held"] for p in measured) / len(measured), 1)
                           if measured else 0.0),
        "worst_period": min(p["portfolio"] for p in measured) if measured else None,
        "best_period": max(p["portfolio"] for p in measured) if measured else None,
    }

    if measured:
        port, bench = _annualised(measured, "portfolio"), _annualised(measured, "benchmark")
        out.update(portfolio_cagr=round(port, 4), benchmark_cagr=round(bench, 4),
                   excess_cagr=round(port - bench, 4))
    else:
        out.update(portfolio_cagr=None, benchmark_cagr=None, excess_cagr=None)

    fport, fbench = _annualised(periods, "portfolio"), _annualised(periods, "benchmark")
    out["full_window"] = {"portfolio_cagr": round(fport, 4),
                          "benchmark_cagr": round(fbench, 4),
                          "excess_cagr": round(fport - fbench, 4)}
    return out

CAVEATS = """
מה המספרים האלה אינם אומרים
---------------------------
* היקום הוא חברי המדד של היום. מי שפשט רגל או נמחק לא נמצא כאן, ולכן
  התוצאה מוטה כלפי מעלה. קראו אותה כתקרה.
* התשואה היא שינוי מחיר מתואם בלבד. אין מס.
* עלות המסחר היא אומדן גס וזהה לכל המניות.
* מספר התקופות קטן. הפרש של אחוז או שניים בשנה על פני עשר שנים אינו
  ראיה סטטיסטית לכלום.
"""


def main() -> int:
    ap = argparse.ArgumentParser(description="בדיקה לאחור של המסך המודרני")
    ap.add_argument("--universe", choices=["sp500", "sp1500"], default="sp500")
    ap.add_argument("--tickers", default="", help="רשימה ידנית במקום יקום")
    ap.add_argument("--start", type=int, default=2015)
    ap.add_argument("--end", type=int, default=date.today().year)
    ap.add_argument("--freq", choices=["annual", "quarterly"], default="annual",
                    help="תדירות האיזון. רבעוני נותן פי ארבעה תצפיות")
    ap.add_argument("--month", type=int, default=6, help="חודש האיזון הראשון")
    ap.add_argument("--day", type=int, default=30)
    ap.add_argument("--max-names", type=int, default=30)
    ap.add_argument("--cost-bps", type=float, default=DEFAULT_COST_BPS)
    ap.add_argument("--min-ebit-ev", type=float, default=None,
                    help="דריסת הסף של תשואת הרווח התפעולי (ברירת מחדל 0.10)")
    ap.add_argument("--min-gross-profitability", type=float, default=None,
                    help="דריסת סף הרווחיות הגולמית (ברירת מחדל 0.20)")
    ap.add_argument("--min-net-payout-yield", type=float, default=None)
    ap.add_argument("--max-net-debt-to-ebitda", type=float, default=None)
    ap.add_argument("--out", default="backtest_results.json")
    args = ap.parse_args()

    if args.tickers.strip():
        tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    else:
        from graham_screener import get_sp500_tickers, get_sp1500_tickers
        tickers = (get_sp1500_tickers() if args.universe == "sp1500"
                   else get_sp500_tickers())

    thresholds = {k: v for k, v in {
        "min_ebit_ev": args.min_ebit_ev,
        "min_gross_profitability": args.min_gross_profitability,
        "min_net_payout_yield": args.min_net_payout_yield,
        "max_net_debt_to_ebitda": args.max_net_debt_to_ebitda,
    }.items() if v is not None}

    print(f"בדיקה לאחור על {len(tickers)} מניות, {args.start}-{args.end}")
    if thresholds:
        print(f"ספים שנדרסו: {thresholds}")
    print()
    result = run(tickers, args.start, args.end, args.month, args.day,
                 args.cost_bps, args.max_names, thresholds=thresholds,
                 freq=args.freq)
    result["thresholds"] = thresholds or "ברירת מחדל"

    print("\n" + "=" * 60)
    if result.get("portfolio_cagr") is None:
        print("אף מניה לא עברה את המסך באף תקופה. אין מה למדוד.")
    else:
        print(f"נמדד מ-{result['measured_from']}, {result['measured_years']} שנים\n")
        print(f"תשואה שנתית ממוצעת, התיק:  {result['portfolio_cagr']*100:+.2f}%")
        print(f"תשואה שנתית ממוצעת, היקום: {result['benchmark_cagr']*100:+.2f}%")
        print(f"עודף:                      {result['excess_cagr']*100:+.2f}%")
        print(f"תקופות שבהן היכה את היקום: {result['periods_beating_benchmark']}")
        print(f"מניות בתיק בממוצע:         {result['avg_names_held']}")
    if result.get("leading_cash_periods") and result.get("portfolio_cagr") is not None:
        fw = result["full_window"]
        print(f"\n[שים לב] {result['leading_cash_periods']} התקופות הראשונות היו ריקות "
              "ולא נכללו במדידה.\n"
              f"        על כל החלון, כולל המזומן: תיק {fw['portfolio_cagr']*100:+.2f}%, "
              f"יקום {fw['benchmark_cagr']*100:+.2f}%.")
    if result.get("periods_in_cash"):
        print(f"\n[שים לב] ב-{result['periods_in_cash']} תקופות אחרי תחילת המדידה התיק "
              "ישב במזומן. הן נספרות כאפס.")
    for j in result.get("universe_jumps", []):
        print(f"\n[שים לב] היקום קפץ מ-{j['from']} ל-{j['to']} מניות ב-{j['at']}.\n"
              "        העודף בכל תקופה נשאר הוגן, אבל מדד ההשוואה לפני ואחרי אינו אותו מדד.")
    cov = result.get("coverage", {})
    if cov:
        print(f"\nכיסוי: {cov['with_prices']}/{cov['tickers']} עם מחירים, "
              f"{cov['with_facts']}/{cov['tickers']} עם דוחות")
    print(CAVEATS)

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
    print(f"נשמר ל-{args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
