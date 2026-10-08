"""עדכון יומי של קבצי ההיסטוריה (bars_update.py), על נתונים מלאכותיים."""
import tempfile
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

import bars_update as bu


def wide(idx, cols, base):
    return {f: pd.DataFrame({c: base[c] for c in cols}, index=idx).astype("float32")
            for f in ("open", "high", "low", "close", "volume")}


def test_appends_new_days_and_refetches_after_dividend():
    old_idx = pd.bdate_range("2025-12-01", "2025-12-31")
    new_idx = pd.bdate_range("2025-12-19", "2026-01-09")
    full_idx = pd.bdate_range("2025-12-01", "2026-01-09")
    old = wide(old_idx, ["A", "B"], {"A": np.full(len(old_idx), 10.0), "B": np.full(len(old_idx), 20.0)})
    # B: דיבידנד - כל ההיסטוריה המתואמת ירדה ב-1%
    fresh = {"A": np.full(len(new_idx), 10.0), "B": np.full(len(new_idx), 19.8)}
    calls = []

    def fetcher(syms, start, end):
        calls.append((sorted(syms), start))
        if start == date(2025, 12, 1):
            return wide(full_idx, syms, {"B": np.full(len(full_idx), 19.8)})
        return wide(new_idx, ["A", "B"], fresh)

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "bars.pkl.gz"
        pd.to_pickle(old, p)
        msg = bu.update(p, today=date(2026, 1, 9), fetcher=fetcher)
        out = pd.read_pickle(p)
    c = out["close"]
    assert c.index.max() == pd.Timestamp("2026-01-09") and c.index.min() == pd.Timestamp("2025-12-01")
    assert calls[1] == (["B"], date(2025, 12, 1))                # רק B הורדה מחדש
    assert abs(c.loc["2025-12-01", "B"] - 19.8) < 1e-4           # ההיסטוריה של B עודכנה
    assert abs(c.loc["2025-12-01", "A"] - 10.0) < 1e-4           # A נשארה
    assert abs(c.loc["2026-01-09", "A"] - 10.0) < 1e-4
    assert not c.index.duplicated().any() and "1 מניות" in msg


def test_up_to_date_does_nothing():
    idx = pd.bdate_range("2026-01-02", "2026-01-09")
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "bars.pkl.gz"
        pd.to_pickle(wide(idx, ["A"], {"A": np.ones(len(idx))}), p)
        msg = bu.update(p, today=date(2026, 1, 9), fetcher=lambda *a: 1 / 0)
    assert "כבר מעודכן" in msg


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
