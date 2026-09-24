"""בדיקות לבדיקה לאחור. בלי רשת, על נתונים סינתטיים."""

from datetime import date

import pandas as pd

import backtest as bt
import sec_facts as sf


def _flow(end, val, filed, start):
    return {"end": end, "val": val, "filed": filed, "form": "10-K", "start": start}


def _inst(end, val, filed):
    return {"end": end, "val": val, "filed": filed, "form": "10-K"}


def make_facts(net_income_by_year=None, revenue=5e9, ebit=1e9, gross=3e9,
               assets=1e10, debt=2e9, cash=1e9, dep=2e8, div=3e8, buy=2e8,
               shares=1e8):
    """חברה סינתטית עם חמש שנות דוחות, כל אחד מוגש בפברואר שאחרי."""
    years = range(2019, 2025)
    ni = net_income_by_year or {y: 8e8 for y in years}
    facts = {"revenue": [], "ebit": [], "gross_profit": [], "assets": [],
             "debt_long": [], "cash": [], "depreciation": [],
             "dividends_paid": [], "buybacks": [], "net_income": [],
             "shares_outstanding": []}
    for y in years:
        end = f"{y}-12-31"
        filed = f"{y+1}-02-15"
        start = f"{y}-01-01"
        facts["revenue"].append(_flow(end, revenue, filed, start))
        facts["ebit"].append(_flow(end, ebit, filed, start))
        facts["gross_profit"].append(_flow(end, gross, filed, start))
        facts["depreciation"].append(_flow(end, dep, filed, start))
        facts["dividends_paid"].append(_flow(end, div, filed, start))
        facts["buybacks"].append(_flow(end, buy, filed, start))
        facts["net_income"].append(_flow(end, ni[y], filed, start))
        facts["assets"].append(_inst(end, assets, filed))
        facts["debt_long"].append(_inst(end, debt, filed))
        facts["cash"].append(_inst(end, cash, filed))
        facts["shares_outstanding"].append(_inst(end, shares, filed))
    return facts


def test_metrics_use_only_what_was_filed():
    f = make_facts()
    # ב-30.6.2023 הדוח האחרון שהוגש הוא של 2022
    m = bt.metrics_at(f, date(2023, 6, 30), price=50.0)
    assert m["last_filed"] == "2023-02-15"
    # וב-30.6.2024 כבר זה של 2023
    m2 = bt.metrics_at(f, date(2024, 6, 30), price=50.0)
    assert m2["last_filed"] == "2024-02-15"


def test_market_cap_and_enterprise_value():
    f = make_facts(shares=1e8, debt=2e9, cash=1e9, ebit=1e9)
    m = bt.metrics_at(f, date(2024, 6, 30), price=50.0)
    # שווי שוק 5 מיליארד, חוב נטו מיליארד, שווי פעילות 6 מיליארד
    assert m["market_cap"] == 5e9
    assert abs(m["ebit_ev"] - 1e9 / 6e9) < 1e-9


def test_gross_profitability_and_payout():
    f = make_facts(gross=3e9, assets=1e10, div=3e8, buy=2e8, shares=1e8)
    m = bt.metrics_at(f, date(2024, 6, 30), price=50.0)
    assert abs(m["gross_profitability"] - 0.30) < 1e-9
    assert abs(m["net_payout_yield"] - 5e8 / 5e9) < 1e-9


def test_net_debt_to_ebitda():
    f = make_facts(ebit=1e9, dep=2e8, debt=2e9, cash=1e9)
    m = bt.metrics_at(f, date(2024, 6, 30), price=50.0)
    assert abs(m["net_debt_to_ebitda"] - 1e9 / 1.2e9) < 1e-9


def test_a_healthy_company_passes():
    f = make_facts()
    m = bt.metrics_at(f, date(2024, 6, 30), price=50.0)
    ok, checks = bt.passes_screen(m)
    assert ok, checks


def test_a_loss_year_breaks_stability():
    f = make_facts(net_income_by_year={2019: 8e8, 2020: -1e8, 2021: 8e8,
                                       2022: 8e8, 2023: 8e8, 2024: 8e8})
    m = bt.metrics_at(f, date(2024, 6, 30), price=50.0)
    ok, checks = bt.passes_screen(m)
    assert checks["stability"] is False
    assert ok is False


def test_an_expensive_price_fails_the_cheapness_test():
    f = make_facts()
    cheap = bt.metrics_at(f, date(2024, 6, 30), price=50.0)
    dear = bt.metrics_at(f, date(2024, 6, 30), price=500.0)
    assert bt.passes_screen(cheap)[1]["cheap"] is True
    assert bt.passes_screen(dear)[1]["cheap"] is False


def test_missing_price_gives_nothing():
    f = make_facts()
    assert bt.metrics_at(f, date(2024, 6, 30), price=None) is None


def test_price_on_never_looks_forward():
    idx = pd.to_datetime(["2024-06-27", "2024-06-28", "2024-07-01"])
    close = pd.DataFrame({"AAA": [10.0, 11.0, 99.0]}, index=idx)
    # ה-30 בחודש הוא ראשון; המחיר הקובע הוא של ה-28
    assert bt.price_on(close, "AAA", date(2024, 6, 30)) == 11.0
    assert bt.price_on(close, "AAA", date(2024, 6, 27)) == 10.0
    assert bt.price_on(close, "AAA", date(2024, 1, 1)) is None
    assert bt.price_on(close, "ZZZ", date(2024, 6, 30)) is None


def test_summary_compounds_and_counts():
    periods = [
        {"buy": "2020-06-30", "sell": "2021-06-30", "portfolio": 0.20,
         "benchmark": 0.10, "excess": 0.10, "n_held": 10, "n_passed": 12, "names": []},
        {"buy": "2021-06-30", "sell": "2022-06-30", "portfolio": -0.10,
         "benchmark": -0.05, "excess": -0.05, "n_held": 10, "n_passed": 11, "names": []},
    ]
    s = bt.summarise(periods, 400)
    # (1.20 * 0.90) ^ (1/2) - 1
    assert abs(s["portfolio_cagr"] - ((1.2 * 0.9) ** 0.5 - 1)) < 1e-4
    assert s["years_beating_benchmark"] == "1/2"
    assert s["worst_year"] == -0.10
    assert s["best_year"] == 0.20


def test_rebalance_dates():
    assert bt.rebalance_dates(2020, 2022, 6, 30) == [
        date(2020, 6, 30), date(2021, 6, 30), date(2022, 6, 30)]


def test_restated_numbers_do_not_leak_backwards():
    """אם דוח 2022 הוגש מחדש ב-2025, בדיקה ל-2023 עדיין רואה את המקור."""
    f = make_facts()
    f["ebit"].append(_flow("2022-12-31", 9e9, "2025-02-15", "2022-01-01"))
    m = bt.metrics_at(f, date(2023, 6, 30), price=50.0)
    ev = m["market_cap"] + 2e9 - 1e9
    assert abs(m["ebit_ev"] - 1e9 / ev) < 1e-9


if __name__ == "__main__":
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
    raise SystemExit(1 if failures else 0)
