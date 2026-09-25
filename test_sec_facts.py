"""בדיקות לשכבת ה-SEC. רצות בלי רשת, על נתונים סינתטיים."""

from datetime import date

import sec_facts as sf


def _point(end, val, filed, form="10-K", start=None, fy=None):
    p = {"end": end, "val": val, "filed": filed, "form": form, "fy": fy}
    if start:
        p["start"] = start
    return p


RAW = {
    "cik": 1234,
    "entityName": "Test Co",
    "facts": {
        "us-gaap": {
            "Revenues": {"units": {"USD": [
                _point("2022-12-31", 1000, "2023-02-15", start="2022-01-01"),
                _point("2023-12-31", 1200, "2024-02-15", start="2023-01-01"),
                # אותה שנה, הוגשה מחדש כעבור שנה עם מספר מתוקן
                _point("2023-12-31", 1150, "2025-02-15", start="2023-01-01"),
                # רבעון: לא אמור להיכנס לסדרה השנתית
                _point("2023-03-31", 300, "2023-05-01", form="10-Q", start="2023-01-01"),
            ]}},
            "Assets": {"units": {"USD": [
                _point("2022-12-31", 5000, "2023-02-15"),
                _point("2023-12-31", 5500, "2024-02-15"),
            ]}},
            "OperatingIncomeLoss": {"units": {"USD": [
                _point("2023-03-31", 25, "2023-05-01", form="10-Q", start="2023-01-01"),
                _point("2023-06-30", 30, "2023-08-01", form="10-Q", start="2023-04-01"),
                _point("2023-09-30", 35, "2023-11-01", form="10-Q", start="2023-07-01"),
                _point("2023-12-31", 40, "2024-02-15", form="10-K", start="2023-10-01"),
            ]}},
            "EarningsPerShareDiluted": {"units": {"USD/shares": [
                _point("2023-12-31", 2.5, "2024-02-15", start="2023-01-01"),
            ]}},
        },
        "dei": {
            "EntityCommonStockSharesOutstanding": {"units": {"shares": [
                _point("2024-01-31", 900, "2024-02-15"),
            ]}},
        },
    },
}


def test_extract_picks_the_fields_we_need():
    compact = sf.extract(RAW)
    assert set(compact) >= {"revenue", "assets", "ebit", "eps_diluted",
                            "shares_outstanding"}
    assert len(compact["revenue"]) == 4
    assert compact["eps_diluted"][0]["val"] == 2.5
    assert compact["shares_outstanding"][0]["val"] == 900


def test_annual_series_drops_quarters_and_sorts_newest_first():
    compact = sf.extract(RAW)
    series = sf.annual_series(compact, "revenue")
    assert [f.end.isoformat() for f in series] == ["2023-12-31", "2022-12-31"]


def test_restatement_does_not_rewrite_history():
    """מה שהמשקיע ראה בזמנו הוא 1200, גם אם אחר כך תוקן ל-1150."""
    compact = sf.extract(RAW)
    series = sf.annual_series(compact, "revenue")
    assert series[0].val == 1200


def test_nothing_is_visible_before_it_was_filed():
    compact = sf.extract(RAW)
    # יום לפני הגשת הדוח של 2023
    series = sf.annual_series(compact, "revenue", as_of_date=date(2024, 2, 14))
    assert [f.end.year for f in series] == [2022]
    assert series[0].val == 1000

    # ויום אחרי
    series = sf.annual_series(compact, "revenue", as_of_date=date(2024, 2, 16))
    assert series[0].end.year == 2023


def test_as_of_snapshot_respects_the_filing_date():
    compact = sf.extract(RAW)
    early = sf.as_of(compact, date(2023, 6, 1))
    assert early["revenue"] == 1000
    assert early["assets"] == 5000
    assert early["_last_filed"] == "2023-02-15"

    late = sf.as_of(compact, date(2024, 6, 1))
    assert late["revenue"] == 1200
    assert late["assets"] == 5500


def test_ttm_sums_four_quarters():
    compact = sf.extract(RAW)
    assert sf.ttm(compact, "ebit") == 25 + 30 + 35 + 40


def test_ttm_falls_back_to_the_annual_figure():
    compact = sf.extract(RAW)
    # להכנסות אין ארבעה רבעונים, ולכן נופלים לשנה האחרונה
    assert sf.ttm(compact, "revenue") == 1200


def test_history_gives_several_years():
    compact = sf.extract(RAW)
    snap = sf.as_of(compact, date(2024, 6, 1), years=5)
    assert snap["_history"]["revenue"] == [1200, 1000]
    assert snap["_period_ends"]["revenue"] == ["2023-12-31", "2022-12-31"]


def test_missing_concept_is_simply_absent():
    compact = sf.extract({"facts": {"us-gaap": {}}})
    assert compact == {}
    assert sf.annual_series(compact, "revenue") == []
    assert sf.ttm(compact, "revenue") is None


def test_empty_input_does_not_explode():
    assert sf.extract({}) == {}
    assert sf.extract(None) == {}


# חברה שעברה ל-ASC 606: התגית הישנה עד 2017, החדשה מ-2018 עם השוואה לשנתיים
# קודמות - אבל ההשוואה הזאת הוגשה רק ב-2019.
SWITCH = {"facts": {"us-gaap": {
    "SalesRevenueNet": {"units": {"USD": [
        _point("2015-12-31", 900, "2016-02-15", start="2015-01-01"),
        _point("2016-12-31", 1000, "2017-02-15", start="2016-01-01"),
        _point("2017-12-31", 1100, "2018-02-15", start="2017-01-01"),
    ]}},
    "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [
        _point("2016-12-31", 990, "2019-02-15", start="2016-01-01"),
        _point("2017-12-31", 1090, "2019-02-15", start="2017-01-01"),
        _point("2018-12-31", 1200, "2019-02-15", start="2018-01-01"),
    ]}},
}}}


def test_old_tag_history_survives_a_tag_switch():
    """לפני שהתגית החדשה הוגשה, התגית הישנה היא מה שהיה ידוע."""
    compact = sf.extract(SWITCH)
    snap = sf.as_of(compact, date(2017, 6, 30), years=5)
    assert snap["revenue"] == 1000
    assert snap["_history"]["revenue"] == [1000, 900]


def test_preferred_tag_wins_once_it_was_filed():
    compact = sf.extract(SWITCH)
    snap = sf.as_of(compact, date(2019, 6, 30), years=5)
    # 2018 רק בתגית החדשה; 2017 ו-2016 בשתיהן, והחדשה מועדפת
    assert snap["_history"]["revenue"] == [1200, 1090, 990, 900]


def test_new_tag_does_not_leak_backwards():
    """ב-2018 ההשוואה בתגית החדשה עוד לא הוגשה, ולכן אסור לראות אותה."""
    compact = sf.extract(SWITCH)
    snap = sf.as_of(compact, date(2018, 6, 30), years=5)
    assert snap["_history"]["revenue"] == [1100, 1000, 900]



def _debt(**kw):
    return sf.total_debt(lambda k: kw.get(k))


def test_debt_noncurrent_plus_current_parts():
    # AAPL: לא-שוטף + החלק השוטף + נייר ערך מסחרי
    assert _debt(debt_long=78.3, debt_short=12.35, short_borrowings=7.98) == 78.3 + 12.35 + 7.98


def test_debt_current_total_is_not_added_twice():
    # JNJ: DebtCurrent כבר כולל את החלק השוטף של החוב הארוך
    assert _debt(debt_long=39.4, debt_short=2.0, debt_current=8.5) == 39.4 + 8.5


def test_long_term_total_already_includes_current_portion():
    # דווח רק LongTermDebt: אסור להוסיף לו שוב את LongTermDebtCurrent
    assert _debt(debt_total=49.4, debt_short=4.97) == 49.4
    assert _debt(debt_total=49.4, debt_short=4.97, short_borrowings=4.46) == 49.4 + 4.46
    assert abs(_debt(debt_total=41.4, debt_short=2.0, debt_current=8.5) - (41.4 + 6.5)) < 1e-9


def test_no_debt_fields_gives_none():
    assert _debt() is None
    assert _debt(short_borrowings=0.0) == 0.0


def test_a_tag_dropped_years_ago_is_not_current():
    raw = {"facts": {"us-gaap": {
        "Assets": {"units": {"USD": [_point("2025-12-31", 900, "2026-02-15")]}},
        "DebtCurrent": {"units": {"USD": [_point("2016-12-31", 50, "2017-02-15")]}},
        "LongTermDebtNoncurrent": {"units": {"USD": [_point("2025-12-31", 300, "2026-02-15")]}},
    }}}
    snap = sf.as_of(sf.extract(raw), date(2026, 6, 30))
    assert "debt_current" not in snap
    assert snap["debt_long"] == 300


def test_predecessor_history_is_merged(monkeypatch=None):
    new, old = 2115436, 34088
    payload = {
        new: {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [
            _point("2025-12-31", 300, "2026-02-15", start="2025-01-01")]}}}}},
        old: {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [
            _point("2016-12-31", 200, "2017-02-15", start="2016-01-01")]}}}}},
    }
    real_get = sf._get
    sf._get = lambda url, timeout=30: payload[int(url.split("CIK")[1].split(".")[0])]
    try:
        compact = sf.company_facts(new, use_cache=False)
    finally:
        sf._get = real_get
    assert sf.as_of(compact, date(2017, 6, 30))["revenue"] == 200
    assert sf.as_of(compact, date(2026, 6, 30))["revenue"] == 300


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
