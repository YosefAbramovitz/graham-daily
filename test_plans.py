"""תוכניות הקנייה לכל חשבון (plans.py)."""
import plans


def test_swing_plan():
    rows = [{"ticker": "AAA", "close": 50.0, "stop_dist": 2.0, "rank": 1, "quality": 3},
            {"ticker": "BBB", "close": 100.0, "stop_dist": 3.0, "rank": 2}]
    p = plans.plan_swing(rows, 10_000, 10_000, {"AAA": 50.0, "BBB": 130.0}, [], 0)
    assert [x["ticker"] for x in p] == ["AAA"]          # BBB: קפיצה של 30% = תקלה
    assert p[0]["qty"] == 40 and p[0]["stop"] == 48.0     # ציון 3: 1% = 100$ / 2$ = 50, תקרת 20% = 40
    rows[0]["quality"] = 2
    assert plans.plan_swing(rows, 10_000, 10_000, {"AAA": 50.0}, [], 0)[0]["qty"] == 25  # 0.5% = 50$ / 2$
    rows[0]["quality"] = 0
    assert plans.plan_swing(rows, 10_000, 10_000, {"AAA": 50.0}, [], 0) == []          # ציון 0: בלי כניסה
    b = plans.swing_order(p[0], "swing-AAA-1")
    assert b["order_class"] == "oto" and b["stop_loss"]["stop_price"] == "48.00"


def test_graham_plan_equal_weight_cheapest_first():
    rows = [{"ticker": "A", "value_rank": 3, "signal_kind": "כניסה"},
            {"ticker": "B", "value_rank": 1, "signal_kind": "המתנה"},
            {"ticker": "C", "value_rank": 2, "signal_kind": "פסילה"},
            {"ticker": "D", "value_rank": None}]
    prices = {"A": 20.0, "B": 50.0, "C": 10.0, "D": 10.0}
    p = plans.plan_graham(rows, 15_000, 15_000, prices, [], 0)
    assert [x["ticker"] for x in p] == ["B", "A", "D"]    # C נפסלה, D בלי דירוג בסוף
    assert p[0]["qty"] == 20 and p[0]["target"] == round(p[0]["price"] * 1.5, 2)
    one = plans.plan_graham(rows, 15_000, 15_000, prices, ["B"], 14)   # מקום אחד, B מוחזקת
    assert [x["ticker"] for x in one] == ["A"]
    assert len(plans.plan_graham(rows, 15_000, 15_000, prices, [], 15)) == 0

