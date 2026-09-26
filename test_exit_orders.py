"""בדיקות לפקודות הכניסה והיציאה. בלי רשת ובלי מפתחות."""

from datetime import date

import exit_orders as eo


def test_entry_without_stop_is_oto_with_target_only():
    b = eo.entry_order_body("KO", 10, 60.0, 90.0)
    assert b["order_class"] == "oto"
    assert b["take_profit"] == {"limit_price": "90.00"}
    assert "stop_loss" not in b
    assert b["side"] == "buy" and b["time_in_force"] == "gtc"


def test_entry_with_stop_is_bracket():
    b = eo.entry_order_body("KO", 10, 60.0, 90.0, stop=54.5)
    assert b["order_class"] == "bracket"
    assert b["stop_loss"] == {"stop_price": "54.50"}


def test_exit_without_stop_is_plain_limit_sell():
    b = eo.exit_order_body("KO", "10", 90.0)
    assert b["side"] == "sell" and b["type"] == "limit"
    assert b["limit_price"] == "90.00"
    assert "order_class" not in b


def test_exit_with_stop_is_oco():
    b = eo.exit_order_body("KO", "10", 90.0, stop=54.5)
    assert b["order_class"] == "oco" and b["type"] == "limit"
    assert b["take_profit"] == {"limit_price": "90.00"}
    assert b["stop_loss"] == {"stop_price": "54.50"}
    assert "limit_price" not in b


TODAY = date(2026, 9, 25)


def test_no_sell_order_means_none():
    orders = [{"symbol": "KO", "side": "buy", "expires_at": "2026-12-01T21:15:00Z"}]
    s = eo.exit_state(orders, "KO", TODAY)
    assert s["state"] == "none"
    assert eo.needs_renewal(s)


def test_failed_fetch_is_unknown_not_none():
    s = eo.exit_state(None, "KO", TODAY)
    assert s["state"] == "unknown"
    assert not eo.needs_renewal(s)


def test_far_expiry_is_ok():
    orders = [{"symbol": "KO", "side": "sell", "expires_at": "2026-12-20T21:15:00Z"}]
    s = eo.exit_state(orders, "ko", TODAY)
    assert s["state"] == "ok" and s["days_left"] == 86


def test_near_expiry_warns_and_takes_the_earliest_leg():
    orders = [
        {"symbol": "KO", "side": "sell", "expires_at": "2026-12-20T21:15:00Z"},
        {"symbol": "KO", "side": "sell", "expires_at": "2026-10-01T21:15:00Z"},
    ]
    s = eo.exit_state(orders, "KO", TODAY)
    assert s["state"] == "expiring" and s["days_left"] == 6
    assert s["expires"] == "2026-10-01"


def test_nested_legs_are_found():
    orders = [{"symbol": "KO", "side": "buy", "legs": [
        {"symbol": "KO", "side": "sell", "expires_at": "2026-12-20T21:15:00Z"}]}]
    assert eo.exit_state(orders, "KO", TODAY)["state"] == "ok"


def test_other_symbols_are_ignored():
    orders = [{"symbol": "PEP", "side": "sell", "expires_at": "2026-12-20T21:15:00Z"}]
    assert eo.exit_state(orders, "KO", TODAY)["state"] == "none"


def test_amount_splits_into_whole_shares_and_a_fraction():
    assert eo.split_amount(1000, 182.40) == (5, 0.482456)


def test_a_fraction_under_one_dollar_is_dropped():
    assert eo.split_amount(365.0, 182.40) == (2, 0.0)


def test_amount_below_one_share_is_only_a_fraction():
    whole, frac = eo.split_amount(100, 182.40)
    assert whole == 0 and abs(frac - 0.548246) < 1e-6


def test_fraction_order_is_a_day_limit_without_exit_legs():
    b = eo.fraction_order_body("LULU", 0.482456, 182.4)
    assert b["time_in_force"] == "day" and b["type"] == "limit"
    assert b["qty"] == "0.482456" and "order_class" not in b and "take_profit" not in b


def test_whole_shares_floors_fractional_positions():
    assert eo.whole_shares("5.482456") == 5
    assert eo.whole_shares("0.5") == 0
    assert eo.whole_shares(None) == 0


if __name__ == "__main__":
    import sys
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ok   {name}")
            except AssertionError as exc:
                failures += 1
                print(f"  FAIL {name}: {exc}")
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print(f"  ERR  {name}: {type(exc).__name__}: {exc}")
    print(f"\n{failures} failures")
    sys.exit(1 if failures else 0)
