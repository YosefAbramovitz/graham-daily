"""מבחני איכות הרווח בשלב האיכות: רווח חד-פעמי ורווח שקרס.

המספרים לקוחים מהדוחות האמיתיים (yahoo, ספטמבר 2026).
"""
import pandas as pd

from quality_screener import earnings_collapse, one_off_gain, pick, OPERATING_ROWS


def _inc(rows):
    return pd.DataFrame(rows).T


def test_one_off_boyd():
    # Boyd 2025: מכירת החזקה ב-FanDuel
    inc = _inc({"Pretax Income": [2329.7e6, 752.0e6], "Operating Income": [876.8e6, 938.3e6],
                "EBIT": [2487.3e6, 929.4e6]})
    assert one_off_gain(inc)
    # תשואת הרווח התפעולי משתמשת ברווח התפעולי ולא ב-EBIT של yahoo
    assert pick(inc, OPERATING_ROWS) == 876.8e6


def test_no_one_off_normal_company():
    inc = _inc({"Pretax Income": [853.9e6], "Operating Income": [907.7e6]})   # H&R Block
    assert not one_off_gain(inc)
    inc = _inc({"Pretax Income": [547.5e6], "Operating Income": [527.6e6]})   # Federated Hermes
    assert not one_off_gain(inc)


def test_one_off_edge_cases():
    assert one_off_gain(_inc({"Pretax Income": [100.0], "Operating Income": [-20.0]}))
    assert not one_off_gain(_inc({"Pretax Income": [-100.0], "Operating Income": [50.0]}))
    assert not one_off_gain(_inc({"Operating Income": [50.0]}))
    assert not one_off_gain(pd.DataFrame())


def test_collapse_molina():
    inc = _inc({"Diluted EPS": [8.92, 20.42, 18.71]})
    assert earnings_collapse(inc, 0.15)


def test_no_collapse():
    inc = _inc({"Diluted EPS": [5.66, 4.39, 4.12]})           # H&R Block
    assert not earnings_collapse(inc, 5.69)
    # Cal-Maine: הירידה צפויה אבל עוד לא בדוחות - לא מסומן
    assert not earnings_collapse(_inc({"Diluted EPS": [6.63, 24.95, 5.69]}), 6.63)


def test_collapse_edge_cases():
    inc = _inc({"Diluted EPS": [2.0, 2.0, 2.0]})
    assert earnings_collapse(inc, -0.5)
    assert not earnings_collapse(inc, None)
    assert not earnings_collapse(inc, "nan")
    assert not earnings_collapse(_inc({"Diluted EPS": [-1.0, -2.0]}), -3.0)
    assert not earnings_collapse(_inc({"Diluted EPS": [2.0]}), 0.1)
