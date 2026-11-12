"""Hermetic test environment + CI import path.

- Makes `app` importable both for `cd backend && pytest` and for
  `pytest backend/tests` from the repo root (CI).
- No live network calls: live weather is mocked to deterministic values;
  the offline fallback is exercised explicitly in test_weather.py.
  ML trains from the local archive file, Monte Carlo uses a fixed seed —
  the whole suite is reproducible.
"""

import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Hermetic runtime state: tests must never touch the live demo DB
# (backend/app/data/sct.db). store.py reads SCT_DATA_DIR at import time,
# so point it at a fresh temp dir before any app module is imported.
os.environ["SCT_DATA_DIR"] = tempfile.mkdtemp(prefix="sct-tests-")

from app import weather  # noqa: E402


@pytest.fixture(autouse=True)
def _mock_live_weather(monkeypatch):
    def fake_fetch(lat, lon):
        if abs(lat - 45.0) < 0.1:  # caspian_north midpoint
            return 1.2, 11.0
        return 0.9, 9.0  # caspian_west midpoint

    monkeypatch.setattr(weather, "_fetch_one", fake_fetch)
    weather._cache.update({"at": 0.0, "payload": None})
    yield
    weather._cache.update({"at": 0.0, "payload": None})
