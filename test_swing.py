"""הכלל לטווח קצר (swing.py)."""
from datetime import date

import numpy as np
import pandas as pd

import swing


def test_holidays_and_exit_date():
    assert not swing.is_trading_day(date(2025, 12, 25))      # חג המולד
    assert not swing.is_trading_day(date(2025, 4, 18))       # יום שישי הטוב
    assert not swing.is_trading_day(date(2026, 7, 3))        # 4 ביולי נופל בשבת
    assert swing.is_trading_day(date(2025, 10, 13))          # קולומבוס: הבורסה פתוחה
    # כניסה ביום שני 22.12.2025: היום ה-15 כולל הכניסה, בדילוג על 25.12 ו-1.1
    assert swing.exit_date(date(2025, 12, 22)) == date(2026, 1, 13)
    assert swing.trading_days_between(date(2025, 12, 22), date(2025, 12, 26)) == 4


def test_size_risk_and_cap():
    s = swing.size(100_000, 50.0, 47.5)          # 0.5% = 500$, סיכון 2.5$ למניה
    assert s["qty"] == 200 and s["risk"] == 500.0 and not s["capped"]
    s = swing.size(100_000, 50.0, 49.9)          # סטופ צמוד: תקרת 20% = 400 מניות
    assert s["qty"] == 400 and s["capped"]
    assert swing.size(100_000, 50.0, 48.0, risk_usd=100)["qty"] == 50
    assert swing.size(100_000, 50.0, 48.0, cash=1_000)["qty"] == 20
    assert swing.size(100_000, 50.0, 51.0)["qty"] == 0


def test_order_body():
    b = swing.order_body("ABC", 10, 50.0, 47.5, "swing-ABC-1")
    assert b["order_class"] == "oto" and b["time_in_force"] == "gtc"
    assert b["stop_loss"] == {"stop_price": "47.50"} and "take_profit" not in b


def test_swing_entries():
    orders = [  # מהחדשה לישנה
        {"symbol": "AAA", "side": "buy", "filled_at": "2026-09-20T14:00:00Z",
         "client_order_id": "swing-AAA-x"},
        {"symbol": "BBB", "side": "sell", "filled_at": "2026-09-21T14:00:00Z"},
        {"symbol": "BBB", "side": "buy", "filled_at": "2026-09-10T14:00:00Z",
         "client_order_id": "swing-BBB-x"},
        {"symbol": "CCC", "side": "buy", "filled_at": "2026-09-01T14:00:00Z",
         "client_order_id": "abc"},
        {"symbol": "AAA", "side": "buy", "filled_at": None, "client_order_id": "swing-AAA-y"},
    ]
    assert swing.swing_entries(orders) == {"AAA": date(2026, 9, 20)}


def test_scan_finds_pullback_in_uptrend():
    idx = pd.bdate_range("2024-01-01", periods=300)
    up = np.linspace(20, 60, 300)
    up[-6:] = up[-7] * np.array([0.98, 0.96, 0.95, 0.94, 0.93, 0.92])   # ירידה חדה בסוף
    C = pd.DataFrame({"AAA": up, "SPY": np.linspace(300, 500, 300)}, index=idx)
    H, L = C * 1.01, C * 0.99
    V = pd.DataFrame(2_000_000.0, index=idx, columns=C.columns)
    r = swing.scan(C, H, L, V)
    assert r["market_ok"] is True
    assert [x["ticker"] for x in r["rows"]] == ["AAA"]
    row = r["rows"][0]
    assert row["rsi"] < swing.RSI_MAX and row["stop_dist"] > 0


def test_scan_sector_alone_flag():
    idx = pd.bdate_range("2024-01-01", periods=300)
    a = np.linspace(20, 60, 300)
    a[-6:] = a[-7] * np.array([0.98, 0.96, 0.95, 0.94, 0.93, 0.92])   # AAA יורדת
    b = np.linspace(20, 60, 300)
    b[-10:] = b[-11] * np.linspace(1.03, 1.30, 10)                     # BBB עולה חזק
    C = pd.DataFrame({"AAA": a, "BBB": b, "SPY": np.linspace(300, 500, 300)}, index=idx)
    H, L = C * 1.01, C * 0.99
    V = pd.DataFrame(2_000_000.0, index=idx, columns=C.columns)
    row = swing.scan(C, H, L, V, sectors={"AAA": "X", "BBB": "X"})["rows"][0]
    assert row["ticker"] == "AAA" and row["alone"] is True and row["sector10"] > 0
    row = swing.scan(C, H, L, V, sectors={"AAA": "X", "BBB": "Y"})["rows"][0]
    assert row["alone"] is False and row["sector10"] < 0                # הענף = AAA לבדה
    row = swing.scan(C, H, L, V)["rows"][0]
    assert row["alone"] is None and row["sector"] is None


def test_quality_score():
    idx = pd.bdate_range("2024-01-01", periods=300)
    C = pd.DataFrame({"AAA": np.linspace(20, 60, 300)}, index=idx)
    V = pd.DataFrame(1_000_000.0, index=idx, columns=C.columns)
    ind = swing.indicators(C, C * 1.01, C * 0.99, V)
    q = swing.quality(ind)
    # מגמה חזקה (מעל 200), אבל לא תיקון (מעל 50), מחזור רגיל = 2 מתוך 3
    assert int(q.iloc[-1, 0]) == 2
    V.iloc[-5:] = 3_000_000.0           # קפיצת מחזור בימים האחרונים
    ind = swing.indicators(C, C * 1.01, C * 0.99, V)
    assert int(swing.quality(ind).iloc[-1, 0]) == 1
