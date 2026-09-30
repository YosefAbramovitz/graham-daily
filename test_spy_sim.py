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