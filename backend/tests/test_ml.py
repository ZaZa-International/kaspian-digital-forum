"""ML data validation, chronology, calibration, provenance and forecast semantics."""

import csv
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import data, ml
from app.main import app

client = TestClient(app)
START = datetime(2026, 1, 1, 6, tzinfo=timezone.utc)
CSV_COLUMNS = ml.CSV_REQUIRED + ["forecast_at", "observed_at", "shipment_id"]


def _row(i):
    sea = i % 2 == 1
    features = [0.3 + (i % 5) * 0.2 if sea else 0, 7 + i % 7 if sea else 0,
                30 + i % 60, 5 + i % 15, 930 if sea else 0,
                300 + i % 32 * 100, 48, int(sea)]
    delay = 3 + features[3] + (6 if features[2] > 60 else 0) + i % 3
    at = START + timedelta(days=i)
    offset = timezone(timedelta(hours=3 if i % 2 else -8))
    return [*features, delay, at.astimezone(offset).isoformat(),
            (at + timedelta(hours=4)).isoformat(), f"history-{i}"]


def _write_csv(path, rows, header=CSV_COLUMNS):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def test_info_synthetic_reproducibility_and_heldout_metrics():
    response = client.get("/api/ml/info")
    assert response.status_code == 200
    info = response.json()
    assert info["status"] == "ok"
    assert info["dataset"]["source_kind"] == "synthetic"
    assert info["dataset"]["scope"] == "remaining_route_snapshot"
    assert len(info["dataset"]["hash"]) == len(info["model_hash"]) == 64
    assert info["dataset"]["seed"] == 42 and "scikit-learn" in info["dataset"]["versions"]
    assert "synthetic benchmark" in info["honest_note"]
    assert [info[f"n_{name}"] for name in ml.MIN_PARTITIONS] == [440, 120, 120, 120]
    assert set(info["metrics"]) == {
        "random_forest", "hist_gradient_boosting", "ensemble_rf_hgb",
        "ensemble_equal_weight", "linear_regression", "median",
    }
    chosen = info["selected_model"]
    valid = info["selection"]["validation_metrics"]
    assert valid[chosen]["mae_h"] == min(m["mae_h"] for m in valid.values())
    test = info["evaluation"]["test_metrics"]
    assert test[chosen]["mae_h"] < test["median"]["mae_h"]
    assert test[chosen]["rmse_h"] >= test[chosen]["mae_h"] and test[chosen]["n"] == 120
    interval = info["evaluation"]["interval"]
    assert interval["level"] == 0.90 and interval["n_test"] == interval["n_calibration"] == 120
    assert interval["coverage"] == pytest.approx(interval["n_covered"] / 120, abs=1e-4)
    assert 0 < interval["mean_width_h"] <= 2 * interval["radius_h"]
    assert ml.info(ml._fit()) == info


def test_chronological_csv_utc_embargo_and_walk_forward(tmp_path):
    path = tmp_path / "history.csv"
    rows = [_row(i) for i in range(200)]
    # 109 and 110 are the same UTC instant, written with two different offsets.
    rows[110][-3] = (START + timedelta(days=109)).astimezone(timezone(timedelta(hours=14))).isoformat()
    rows[108][-2] = (START + timedelta(days=115)).isoformat()
    _write_csv(path, reversed(rows))
    X, y, timing = ml.load_history_csv(path)
    assert X.shape == (200, 8) and y.shape == (200,)
    assert timing["forecast_at"][0] == START
    assert timing["forecast_at"][109] == timing["forecast_at"][110]
    assert timing["shipment_identity_checked"]
    experiment = ml.info(ml._fit(path, run_walk_forward=True))
    assert experiment["dataset"]["source_kind"] == "csv"
    assert experiment["dataset"]["split"].startswith("chronological")
    parts = experiment["dataset"]["partitions"]
    assert parts["train"]["n_before_embargo"] == 111
    assert parts["train"]["excluded_unavailable_labels"] == 1
    for left, right in zip(list(parts.values()), list(parts.values())[1:]):
        assert datetime.fromisoformat(left["forecast_end"]) < datetime.fromisoformat(right["forecast_start"])
        assert datetime.fromisoformat(left["last_label_available_at"]) < datetime.fromisoformat(left["label_cutoff_at"])
    walk = experiment["evaluation"]["walk_forward"]
    assert walk["status"] == "evaluated" and len(walk["folds"]) == 2
    for fold in walk["folds"]:
        assert fold["partitions"]["test"]["forecast_end"] < parts["test"]["forecast_start"]
    _write_csv(path, rows)
    assert ml.info(ml._fit(path))["dataset"]["hash"] == experiment["dataset"]["hash"]


@pytest.mark.parametrize(("column", "value", "message"), [
    ("max_wave_m", "nan", "must be finite"),
    ("max_wind_ms", "inf", "must be finite"),
    ("max_congestion_pct", 101, "outside allowed range"),
    ("weight_t", 0, "outside allowed range"),
    ("sea_legs", 0.5, "must be integral"),
    ("sea_legs", 0, "sea metrics must be zero"),
    ("delay_h", -1, "outside allowed range"),
    ("delay_h", "text", "non-numeric"),
    ("forecast_at", "2026-01-01T00:00:00", "invalid forecast_at"),
    ("observed_at", "2025-01-01T00:00:00Z", "observed_at must be after forecast_at"),
    ("shipment_id", "history-0", "must be non-empty and unique"),
])
def test_invalid_history_fields(tmp_path, column, value, message):
    path = tmp_path / "bad.csv"
    rows = [_row(i) for i in range(120)]
    rows[1][CSV_COLUMNS.index(column)] = value
    _write_csv(path, rows)
    with pytest.raises(ValueError, match=message):
        ml.load_history_csv(path)


def test_insufficient_history_and_unavailable_scoring_fallback(tmp_path, monkeypatch):
    path = tmp_path / "short.csv"
    _write_csv(path, [_row(i) for i in range(25)])
    with pytest.raises(ValueError, match="at least 120"):
        ml.load_history_csv(path)
    monkeypatch.setenv(ml.HISTORY_CSV_ENV, str(path))
    info = client.get("/api/ml/info").json()
    assert info["status"] == "unavailable" and info["dataset"]["source_kind"] == "csv"
    body = client.get("/api/ml/forecast?route_id=R1").json()
    assert body["status"] == "unavailable" and body["ml_delay_h"] is None and body["interval"] is None
    assert body["reliability"]["status"] == "scoring_only"
    assert body["preferred_delay_h"] == body["scoring_delay_h"]

    # Repairing the configured file retrains on the new source revision.
    _write_csv(path, [_row(i) for i in range(160)])
    csv_info = client.get("/api/ml/info").json()
    body = client.get("/api/ml/forecast?route_id=R1").json()
    assert csv_info["status"] == "ok" and csv_info["dataset"]["kind"].startswith("csv")
    assert csv_info["model_hash"] == body["model_hash"] and body["dataset_kind"] == "csv"
    assert "synthetic" not in body["analogs_kind"] and body["interval"]["usable"] is False
    assert body["reliability"]["preferred_source"] == "scoring"


def test_label_available_in_training_and_calibration_independently(monkeypatch):
    X, y = ml._sample_history(np.random.default_rng(ml.RANDOM_STATE))
    partitions, _, _ = ml._partition(len(y), None)
    monkeypatch.setattr(ml, "_sample_history", lambda rng: (X, y))
    trained = ml._fit()
    y[partitions["test"]] += 500
    tested = ml._fit()
    assert tested["selection"] == trained["selection"] and tested["radius"] == trained["radius"]
    assert tested["evaluation"]["test_metrics"] != trained["evaluation"]["test_metrics"]
    y[partitions["calibration"]] += 500
    recalibrated = ml._fit()
    assert recalibrated["selection"] == trained["selection"]
    assert recalibrated["evaluation"]["test_metrics"] == tested["evaluation"]["test_metrics"]
    assert recalibrated["radius"] > tested["radius"] + 400
    assert ml._radius(np.arange(1, 20), np.zeros(19)) == 18


def test_ml_scenario_ood_and_fallback():
    calm = client.get("/api/ml/forecast?route_id=R1").json()
    storm = client.get("/api/ml/forecast?route_id=R1&wave_m=3&wind_ms=20").json()
    assert storm["features"]["max_wave_m"] == 3 and storm["features"]["max_wind_ms"] == 20
    assert storm["ml_delay_h"] > calm["ml_delay_h"]
    assert storm["reliability"]["status"] == "out_of_distribution"
    assert {r["feature"] for r in storm["reliability"]["range_violations"]} >= {"max_wave_m", "max_wind_ms"}
    assert storm["interval"]["calibration_applicable"] is False
    assert calm["interval"]["lower_h"] <= calm["ml_delay_h"] <= calm["interval"]["upper_h"]
    assert calm["interval"]["calibration_applicable"] is True
    assert calm["interval"]["usable"] is False
    assert calm["reliability"]["status"] == "synthetic_only"
    assert calm["preferred_delay_h"] == calm["scoring_delay_h"]
    customs = client.get("/api/ml/forecast?route_id=R1&extra_customs_h=20").json()
    assert customs["features"]["total_customs_h"] == calm["features"]["total_customs_h"] + 60
    assert client.get("/api/ml/forecast?route_id=R1&close=makhachkala").status_code == 422


def test_projection_uses_effective_inputs_and_batched_local_attributions(monkeypatch):
    route = data.get_route("R1")
    sea = {**route["legs"][1], "in_transit": True, "base_remaining_h": 25, "effective_customs_h": 0,
           "weather": {"wave_m": 1.2, "wind_ms": 8.5}, "congestion_pct": 70}
    prepared = {**route, "legs": [sea]}
    trained = ml.get_trained()
    calls = []
    for name, estimator in trained["estimators"].items():
        def capture(X, original=estimator.predict, tag=name):
            calls.append((tag, len(X)))
            return original(X)
        monkeypatch.setattr(estimator, "predict", capture)
    forecast = ml.predict_delay(prepared, 48, scoring_delay_h=7.5)
    assert forecast["features"]["sea_km"] == 465
    assert forecast["features"]["rail_km"] == forecast["features"]["total_customs_h"] == 0
    assert forecast["features"]["max_congestion_pct"] == 70
    assert forecast["features"]["max_wave_m"] == 1.2 and forecast["preferred_delay_h"] == 7.5
    assert "in_transit_phase_not_a_feature" in forecast["reliability"]["scope_limitations"]
    assert forecast["interval"]["calibration_applicable"] is False
    assert len(forecast["local_attribution"]) == 3
    assert calls.count(("random_forest", 9)) == calls.count(("hist_gradient_boosting", 9)) == 1
    assert all(size == 9 for _, size in calls)

    reported = ml.predict_delay(
        {**route, "legs": [{**route["legs"][1], "reported_delay_h": 12}]},
        48, scoring_delay_h=20,
    )
    assert "reported_delay_not_a_feature" in reported["reliability"]["scope_limitations"]
    assert reported["interval"]["calibration_applicable"] is False
    assert reported["preferred_delay_h"] == 20
