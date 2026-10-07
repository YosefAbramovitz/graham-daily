"""הדמיית S&P 500 המקומית (spy_sim.py)."""
import tempfile
from pathlib import Path

import spy_sim

CLOSES = [("2026-09-01", 500.0), ("2026-09-02", 510.0), ("2026-09-03", 480.0),
          ("2026-09-04", 520.0)]


def test_buy_and_hold_from_start():
    r = spy_sim.simulate(CLOSES, "2026-09-02", 10_000)
    assert r["since"] == "2026-09-02"
    assert abs(r["shares"] - 10_000 / 510) < 1e-9
    assert r["points"][0] == ["2026-09-02", 10_000.0]
    assert abs(r["equity"] - 10_000 * 520 / 510) < 0.01
    assert abs(r["maxdd"] - (480 / 510 - 1)) < 1e-5          # מ-510 ל-480
    assert abs(r["day_change"] - 10_000 * (520 - 480) / 510) < 0.01


def test_live_price_and_today():
    r = spy_sim.simulate(CLOSES, "2026-09-01", 10_000, live=550.0, today="2026-09-05")
    assert abs(r["equity"] - 11_000) < 0.01
    assert abs(r["day_change"] - (11_000 - 10_400)) < 0.01    # מול הסגירה של 4.9


def test_no_data_and_settings_roundtrip():
    r = spy_sim.simulate(CLOSES, "2026-10-01")
    assert r["points"] == [] and r["equity"] == spy_sim.CASH and r["ret"] == 0.0
    with tempfile.TemporaryDirectory() as t:
        p = Path(t) / "s.json"
        assert spy_sim.load_settings(p) == {}
        spy_sim.save_settings(p, {"start": "2026-09-02", "cash": 10_000})
        assert spy_sim.load_settings(p)["start"] == "2026-09-02"

def test_fund_ids_keep_spy_first():
    assert spy_sim.FUNDS[0][0] == "SPY"
    assert spy_sim.row_id("SPY") == "spy_sim"
    assert spy_sim.row_id("VT") == "sim_vt"
    assert len({f[0] for f in spy_sim.FUNDS}) == len(spy_sim.FUNDS)


def test_app_builds_a_row_per_fund():
    import app
    bars = {"SPY": [{"t": "2026-09-25T04:00:00Z", "c": 500.0}, {"t": "2026-09-28T04:00:00Z", "c": 510.0}],
            "VT": [{"t": "2026-09-25T04:00:00Z", "c": 100.0}, {"t": "2026-09-28T04:00:00Z", "c": 99.0}],
            "PPA": [{"t": "2026-09-25T04:00:00Z", "c": 120.0}, {"t": "2026-09-28T04:00:00Z", "c": 126.0}]}
    saved = (app.accounts_available, app._swing_bars, app.last_trade, app.SPY_SIM_FILE)
    with tempfile.TemporaryDirectory() as t:
        try:
            app.accounts_available = lambda: ["graham"]
            app._swing_bars = lambda syms, s, e: {k: v for k, v in bars.items() if k in syms}
            app.last_trade = lambda sym: None
            app.SPY_SIM_FILE = Path(t) / "s.json"
            spy_sim.save_settings(app.SPY_SIM_FILE, {"start": "2026-09-25", "cash": 10_000})
            app._spy_sim.update(at=0.0, key=None, data=None)
            rows = app.fund_sim_rows(today="2026-09-28")
            by = {r["id"]: r for r in rows}
            assert [r["id"] for r in rows] == [spy_sim.row_id(f[0]) for f in spy_sim.FUNDS]
            assert abs(by["spy_sim"]["equity"] - 10_200) < 0.01
            assert abs(by["sim_ppa"]["ret"] - 0.05) < 1e-9
            assert by["sim_cibr"].get("error")                 # בלי נרות: שגיאה, לא קריסה
            assert by["spy_sim"]["label"] == "S&P 500 · SPY (הדמיה)"
            assert by["spy_sim"]["fund"] == "SPDR S&P 500"
            assert app.spy_sim_row(today="2026-09-28")["id"] == "spy_sim"
        finally:
            (app.accounts_available, app._swing_bars, app.last_trade, app.SPY_SIM_FILE) = saved
            app._spy_sim.update(at=0.0, key=None, data=None)
