"""ההדמיה לאחור של שני החשבונות (replay_sim.py), על נתונים מלאכותיים."""
from datetime import datetime, timezone

import numpy as np
import pandas as pd

import replay_sim as rs
import swing


def frames(n=260, start="2025-09-01"):
    """מניה A במגמה עולה עם ירידה חדה בסוף (אות RSI), SPY במגמה עולה."""
    idx = pd.bdate_range(start, periods=n)
    a = np.linspace(50, 100, n)
    a[-12:-6] = np.linspace(100, 88, 6)          # ירידה: RSI מתחת ל-35, עדיין מעל ממוצע 200
    a[-6:] = 88
    spy = np.linspace(400, 500, n)
    C = pd.DataFrame({"AAA": a, "SPY": spy}, index=idx)
    O = C.shift(1).fillna(C)
    H, L = C * 1.01, C * 0.99
    V = pd.DataFrame(1e6, index=idx, columns=C.columns)
    return O, H, L, C, V


def test_swing_buys_next_open_and_sizes_by_quality():
    O, H, L, C, V = frames()
    sig = swing.signals(C, H, L, V)
    s = sig.index[sig["AAA"]][0]
    i = list(C.index).index(s)
    start = C.index[i - 2].date().isoformat()
    r = rs.swing_replay(O, H, L, C, V, start, 2_700.0)
    buy = next(t for t in r["trades"] if t["side"] == "buy")
    assert buy["day"] == C.index[i + 1].date().isoformat()      # למחרת האות, בפתיחה
    assert abs(buy["price"] - O["AAA"].iloc[i + 1]) < 1e-6
    assert buy["value"] <= 2_700.0 * swing.MAX_POSITION_PCT + 1e-6
    assert buy["quality"] > 0
    assert r["points"][0][0] == start and len(r["points"]) == len(C.index) - i + 2


def test_swing_stop_and_time_exit():
    O, H, L, C, V = frames(300)
    sig = swing.signals(C, H, L, V)
    i = list(C.index).index(sig.index[sig["AAA"]][0])
    # אחרי הכניסה: קריסה מתחת לסטופ
    L2 = L.copy()
    L2.iloc[i + 3, 0] = 1.0
    r = rs.swing_replay(O, H, L2, C, V, C.index[i].date().isoformat(), 2_700.0)
    sells = [t for t in r["trades"] if t["side"] == "sell"]
    assert sells and sells[0]["reason"] == "סטופ"
    # בלי קריסה: יציאה ביום ה-20
    r = rs.swing_replay(O, H, L, C, V, C.index[i].date().isoformat(), 2_700.0)
    sells = [t for t in r["trades"] if t["side"] == "sell"]
    if sells:
        assert sells[0]["reason"] in ("סטופ", f"יום {swing.HOLD_DAYS}")


def test_swing_no_entries_when_spy_below_200():
    O, H, L, C, V = frames()
    C = C.copy()
    C["SPY"] = np.linspace(500, 400, len(C))
    r = rs.swing_replay(O, H, L, C, V, C.index[-10].date().isoformat(), 2_700.0)
    assert r["buys"] == 0 and r["equity"] == 2_700.0


def graham_frames():
    idx = pd.bdate_range("2026-09-24", periods=8)
    C = pd.DataFrame({"X": [10, 10, 11, 12, 14, 16, 16, 16], "Y": [20] * 8}, index=idx, dtype=float)
    O = C.shift(1).fillna(C)                     # פתיחה = הסגירה הקודמת
    return O, C.copy(), C


LIST = "ticker,signal,signal_kind,value_rank\nY,כניסה,כניסה,2\nX,המתנה,המתנה,1\nZ,x,פסילה,0\n"


def test_list_for_uses_morning_commit():
    lists = [(datetime(2026, 9, 29, 11, 0, tzinfo=timezone.utc), "a"),
             (datetime(2026, 9, 30, 15, 0, tzinfo=timezone.utc), "b")]
    assert rs.list_for(lists, datetime(2026, 9, 28).date()) is None
    assert rs.list_for(lists, datetime(2026, 9, 29).date()) == "a"
    assert rs.list_for(lists, datetime(2026, 9, 30).date()) == "a"     # אחרי הפתיחה
    assert rs.list_for(lists, datetime(2026, 10, 1).date()) == "b"


def test_graham_equal_weight_cheapest_first_and_target():
    O, H, C = graham_frames()
    lists = [(datetime(2026, 9, 25, 10, tzinfo=timezone.utc), rs.parse_list(LIST))]
    r = rs.graham_replay(O, H, C, lists, "2026-09-24", 1_500.0, slots=15)
    buys = [t for t in r["trades"] if t["side"] == "buy"]
    assert [b["ticker"] for b in buys][:2] == ["X", "Y"]              # הזולה קודם, בלי פסילה
    assert buys[0]["day"] == "2026-09-25" and abs(buys[0]["value"] - 100.0) < 1e-6
    sells = [t for t in r["trades"] if t["side"] == "sell"]
    assert sells and sells[0]["ticker"] == "X" and sells[0]["reason"].startswith("יעד")
    assert abs(sells[0]["price"] - 15.0) < 1e-6                      # 10 * 1.5
    # אחרי המכירה מקום פנוי - X חוזרת למחרת, לא באותו יום
    again = [b for b in buys if b["ticker"] == "X"][1:]
    assert again and again[0]["day"] == "2026-10-02"


def test_ils_conversion():
    res = {"points": [["2026-09-25", 2_700.0], ["2026-09-28", 2_970.0]], "start_value": 2_700.0}
    fx = {"2026-09-25": 3.7, "2026-09-26": 3.6}
    out = rs.in_ils(res, fx, 9_990.0)
    assert out["points_ils"] == [["2026-09-25", 9_990.0], ["2026-09-28", 10_692.0]]
    assert abs(out["ret_ils"] - (10_692 / 9_990 - 1)) < 1e-9


if __name__ == "__main__":
    import sys
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("OK  ", name)
            except Exception as e:  # noqa: BLE001
                fails += 1
                print("FAIL", name, repr(e))
    print(f"{fails} failures")
    sys.exit(1 if fails else 0)
