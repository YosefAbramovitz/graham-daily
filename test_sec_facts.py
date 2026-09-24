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
