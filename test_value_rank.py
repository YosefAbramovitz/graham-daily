"""דירוג הזולות בשלב האיכות (ראו variants_sim.py לבדיקה לאחור)."""
import pandas as pd

from quality_screener import add_value_rank


def test_cheapest_first_and_top_flag():
    df = pd.DataFrame({"ticker": list("ABCD"), "ebit_ev": [0.10, 0.25, "", 0.15]})
    out = add_value_rank(df, top=2)
    assert list(out["value_rank"]) == ["3", "1", "", "2"]
    assert list(out["value_top"]) == ["", "כן", "", "כן"]


def test_missing_column():
    out = add_value_rank(pd.DataFrame({"ticker": ["A"]}))
    assert list(out["value_rank"]) == [""] and list(out["value_top"]) == [""]
