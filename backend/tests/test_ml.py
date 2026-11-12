"""ML honesty & reproducibility tests (cycle 6)."""
import csv

import pytest
from fastapi.testclient import TestClient

from app import ml
from app.main import app

client = TestClient(app)


def test_info_labels_synthetic_and_reproducible():
    info = client.get("/api/ml/info").json()
    assert info["dataset"]["kind"].startswith("synthetic")
    assert info["dataset"]["hash"] and info["dataset"]["seed"] == 42
    assert "scikit-learn" in info["dataset"]["versions"]
    assert "synthetic benchmark" in info["honest_note"]
    assert set(info["metrics"]) == {"random_forest", "hist_gradient_boosting",
                                    "ensemble_rf_hgb", "linear_regression"}
    # retrain from scratch -> identical metrics and dataset hash
    ml._TRAINED = None
    again = client.get("/api/ml/info").json()
    assert again["metrics"] == info["metrics"]
    assert again["dataset"]["hash"] == info["dataset"]["hash"]


def _write_csv(path, rows, header=None):
    header = header or (ml.FEATURES + ["delay_h"])
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def _row(i, ts=None):
    r = [0.5 + (i % 5) * 0.2, 8 + i % 7, 30 + i % 60, 5 + i % 15,
         930 if i % 2 else 0, 3120, 48, 1 if i % 2 else 0, 10 + (i % 20)]
    if ts is not None:
        r.append(f"2026-09-{(i % 28) + 1:02d}T06:00:00+00:00")
    return r


def test_csv_contract_loads_and_trains(tmp_path, monkeypatch):
    p = tmp_path / "history.csv"
    _write_csv(p, [_row(i) for i in range(40)])
    X, y, meta = ml.load_history_csv(p)
    assert X.shape == (40, 8) and meta["n"] == 40
    monkeypatch.setenv(ml.HISTORY_CSV_ENV, str(p))
    ml._TRAINED = None
    info = client.get("/api/ml/info").json()
    assert info["dataset"]["kind"].startswith("csv")
    assert info["n_train"] + info["n_test"] == 40
    assert info["dataset"]["split"].startswith("random")
    ml._TRAINED = None
    monkeypatch.delenv(ml.HISTORY_CSV_ENV)


def test_csv_chronological_split(tmp_path, monkeypatch):
    p = tmp_path / "history_ts.csv"
    _write_csv(p, [_row(i, ts=True) for i in range(40)],
               header=ml.FEATURES + ["delay_h", "departed_at"])
    monkeypatch.setenv(ml.HISTORY_CSV_ENV, str(p))
    ml._TRAINED = None
    info = client.get("/api/ml/info").json()
    assert info["dataset"]["split"].startswith("chronological")
    ml._TRAINED = None
    monkeypatch.delenv(ml.HISTORY_CSV_ENV)


def test_csv_validation(tmp_path):
    p = tmp_path / "bad.csv"
    _write_csv(p, [_row(1)], header=ml.FEATURES[:-1] + ["delay_h"])
    with pytest.raises(ValueError, match="missing columns"):
        ml.load_history_csv(p)
    _write_csv(p, [[0.5, 9, 40, 10, 930, 3120, 48, 1, -3]])
    with pytest.raises(ValueError, match="negative"):
        ml.load_history_csv(p)
    _write_csv(p, [[0.5, 9, 40, 10, 930, 3120, 48, 1, "oops"]])
    with pytest.raises(ValueError, match="non-numeric"):
        ml.load_history_csv(p)


def test_ml_forecast_honors_scenario():
    calm = client.get("/api/ml/forecast?route_id=R1").json()
    stormy = client.get("/api/ml/forecast?route_id=R1&wave_m=3&wind_ms=20").json()
    assert stormy["features"]["max_wave_m"] == 3
    assert stormy["features"]["max_wind_ms"] == 20
    assert stormy["ml_delay_h"] > calm["ml_delay_h"]
    assert stormy["label_kind"].startswith("synthetic")
    customs = client.get("/api/ml/forecast?route_id=R1&extra_customs_h=20").json()
    assert customs["features"]["total_customs_h"] == calm["features"]["total_customs_h"] + 60
    assert client.get("/api/ml/forecast?route_id=R1&close=makhachkala").status_code == 422
    assert client.get("/api/ml/shipment/SHP-1042?close=makhachkala").status_code == 422