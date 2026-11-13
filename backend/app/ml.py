"""Experimental delay regression for snapshots of the remaining shipment route."""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

import numpy as np
import sklearn
from sklearn.dummy import DummyRegressor
from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error, r2_score, root_mean_squared_error

from app import data, model, weather

log = logging.getLogger("sct.ml")
FEATURES = [
    "max_wave_m", "max_wind_ms", "max_congestion_pct", "total_customs_h",
    "sea_km", "rail_km", "weight_t", "sea_legs",
]
N_SAMPLES = 800
RANDOM_STATE = 42
MODEL_VERSION = "sct-delay-v1"
INTERVAL_LEVEL = 0.90
DATA_SCOPE = "remaining_route_snapshot"
HISTORY_CSV_ENV = "SCT_ML_HISTORY_CSV"
CSV_REQUIRED = FEATURES + ["delay_h"]
MIN_CSV_ROWS = 120
MAX_CSV_ROWS = 50_000
MAX_CSV_BYTES = 16 * 1024 * 1024
MIN_PARTITIONS = {"train": 40, "validation": 15, "calibration": 15, "test": 15}
RANGES = {
    "max_wave_m": (0, 15), "max_wind_ms": (0, 60), "max_congestion_pct": (0, 100),
    "total_customs_h": (0, 3000), "sea_km": (0, 20_000), "rail_km": (0, 20_000),
    "weight_t": (0, 5000), "sea_legs": (0, 20), "delay_h": (0, 8760),
}
_MODEL_NAMES = {
    "random_forest": "RandomForest",
    "hist_gradient_boosting": "HistGradientBoosting",
    "ensemble_rf_hgb": "Ensemble RF + HGB",
    "linear_regression": "LinearRegression",
    "median": "Медиана обучающей выборки",
}
_MODEL_PARAMS = {
    "random_forest": {"n_estimators": 120, "max_depth": 14, "min_samples_leaf": 2,
                      "max_samples": 0.8, "n_jobs": 1, "random_state": RANDOM_STATE},
    "hist_gradient_boosting": {"max_iter": 125, "learning_rate": 0.08, "max_leaf_nodes": 23,
                               "min_samples_leaf": 10, "l2_regularization": 0.5,
                               "early_stopping": False, "random_state": RANDOM_STATE},
}
_BLEND_WEIGHTS = (0.25, 0.50, 0.75)
_TRAINED: dict | None = None
_TRAINED_CONFIG: tuple | None = None
_TRAIN_LOCK = Lock()


def _sample_history(rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    archive = weather.load_archive()["segments"]
    edge_map = {edge["id"]: edge for edge in data.GRAPH_EDGES}
    routes = [route["legs"] for route in data.ROUTES] + [
        [edge_map[key] for key in path] for path in (
            ("R1-L1", "G-MAH-BAK", "R3-L2", "R3-L3"),
            ("R2-L1", "R2-L2", "G-AKT-BAK", "R3-L2", "R3-L3"),
            ("R1-L1", "G-MAH-ENZ", "G-ENZ-TEH", "G-TEH-BAB"),
            ("R3-L1", "G-BAK-TBS", "G-TBS-TEH", "G-TEH-BAB"),
        )
    ]
    X, y = [], []
    for _ in range(N_SAMPLES):
        route = routes[int(rng.integers(len(routes)))]
        start = 0 if rng.random() < 0.55 else int(rng.integers(len(route)))
        legs = route[start:]
        progress = float(rng.uniform(0.1, 0.9)) if rng.random() < 0.2 else 0.0
        weight = float(rng.choice([12, 24, 48, 96, 300, 650, 1200, 1800]))
        wave = wind = customs = sea_km = rail_km = 0.0
        max_congestion = sea_legs = 0
        delay = 0.0
        for index, leg in enumerate(legs):
            remaining_fraction = 1 - progress if index == 0 else 1.0
            mode = leg["mode"]
            congestion = int(rng.integers(25, 91)) if leg.get("port") else 0
            queue = float(rng.uniform(0.65, 1.45) * leg.get("customs_h", 0))
            leg_wave = leg_wind = 0.0
            if mode == "sea":
                segment = archive[leg["sea"]]
                # The archived arrays have different lengths and no shared time index.
                leg_wave = float(rng.choice(segment["wave_m"]["samples"]))
                leg_wind = float(rng.choice(segment["wind_ms"]["samples"]))
                sea_legs += 1
                sea_km += leg["distance_km"] * remaining_fraction
                wave, wind = max(wave, leg_wave), max(wind, leg_wind)
            elif mode == "rail":
                rail_km += leg["distance_km"] * remaining_fraction
            customs += queue
            max_congestion = max(max_congestion, congestion)
            leg_delay, _ = model._delay_from_inputs(mode, leg_wave, leg_wind, congestion, queue)
            if mode == "sea" and leg_wave >= 2 and index == 0 and progress:
                leg_delay -= 48  # a departed ship has already passed the departure gate
            delay += leg_delay
        scale = 1.6 + 0.45 * len(legs) + (0.4 * wave if sea_legs else 0)
        if rng.random() < 0.03:
            delay += float(rng.uniform(3, 12))  # unmodelled operational disruption in the simulation
        y.append(max(0.0, round(delay + float(rng.normal(0, scale)), 1)))
        X.append([wave, wind, max_congestion, customs, sea_km, rail_km, weight, sea_legs])
    return np.asarray(X, dtype=float), np.asarray(y, dtype=float)


def _utc(value: str | None, column: str, row: int) -> datetime:
    if not value or "T" not in value:
        raise ValueError(f"history CSV row {row}: {column} needs an ISO 8601 timestamp with offset")
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.utcoffset() is None:
            raise ValueError
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"history CSV row {row}: invalid {column}; use an ISO 8601 timestamp with offset") from exc


def _numeric_row(row: dict, index: int) -> list[float]:
    values = []
    for column in CSV_REQUIRED:
        try:
            number = float(row[column])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"history CSV row {index}: non-numeric {column}") from exc
        lower, upper = RANGES[column]
        if not math.isfinite(number):
            raise ValueError(f"history CSV row {index}: {column} must be finite")
        if not lower <= number <= upper or (column == "weight_t" and number == 0):
            raise ValueError(f"history CSV row {index}: {column} outside allowed range {lower}–{upper}")
        if column == "sea_legs" and not number.is_integer():
            raise ValueError(f"history CSV row {index}: sea_legs must be integral")
        values.append(number)
    row_values = dict(zip(CSV_REQUIRED, values))
    if not row_values["sea_legs"] and any(row_values[key] for key in ("max_wave_m", "max_wind_ms", "sea_km")):
        raise ValueError(f"history CSV row {index}: sea metrics must be zero without sea legs")
    return values


def load_history_csv(path: str | Path) -> tuple[np.ndarray, np.ndarray, dict]:
    """Validate the historical snapshot contract without exposing row contents."""
    path = Path(path)
    if not path.is_file():
        raise ValueError("history CSV is unavailable")
    if path.stat().st_size > MAX_CSV_BYTES:
        raise ValueError(f"history CSV exceeds {MAX_CSV_BYTES // 1024 // 1024} MiB")
    records, seen = [], set()
    with path.open(newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source, strict=True)
        columns = reader.fieldnames or []
        if len(set(columns)) != len(columns):
            raise ValueError("history CSV has duplicate column names")
        if missing := [c for c in CSV_REQUIRED if c not in columns]:
            raise ValueError(f"history CSV missing columns: {', '.join(missing)}")
        forecast_col = "forecast_at" if "forecast_at" in columns else "departed_at"
        observed_col = "observed_at" if "observed_at" in columns else "label_available_at"
        if forecast_col not in columns or observed_col not in columns:
            raise ValueError("history CSV requires forecast_at and observed_at timestamps")
        for index, row in enumerate(reader, 2):
            if len(records) >= MAX_CSV_ROWS:
                raise ValueError(f"history CSV exceeds {MAX_CSV_ROWS} records")
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"history CSV row {index}: invalid column count")
            values = _numeric_row(row, index)
            forecast = _utc(row[forecast_col], forecast_col, index)
            observed = _utc(row[observed_col], observed_col, index)
            if observed <= forecast:
                raise ValueError(f"history CSV row {index}: observed_at must be after forecast_at")
            identity = row.get("shipment_id", "").strip()
            if "shipment_id" in columns:
                if not identity or len(identity) > 128 or identity in seen:
                    raise ValueError(f"history CSV row {index}: shipment_id must be non-empty and unique")
                seen.add(identity)
            records.append((forecast, observed, tuple(values)))
    if len(records) < MIN_CSV_ROWS:
        raise ValueError(f"history CSV needs at least {MIN_CSV_ROWS} records; received {len(records)}")
    # Lexicographic order is on parsed UTC instants, never on raw timestamp strings.
    records.sort()
    values = np.asarray([row[2] for row in records], dtype=float)
    return values[:, :-1], values[:, -1], {
        "n": len(records), "has_timestamps": True, "source": path.name,
        "forecast_at": [row[0] for row in records], "observed_at": [row[1] for row in records],
        "shipment_identity_checked": "shipment_id" in columns,
    }


def _dataset_hash(X: np.ndarray, y: np.ndarray, timing: dict | None) -> str:
    digest = hashlib.sha256()
    digest.update(json.dumps([CSV_REQUIRED, DATA_SCOPE]).encode())
    digest.update(X.astype("<f8", copy=False).tobytes())
    digest.update(y.astype("<f8", copy=False).tobytes())
    if timing:
        digest.update(json.dumps({key: [t.isoformat() for t in timing[key]] for key in (
            "forecast_at", "observed_at",
        )}, sort_keys=True).encode())
    return digest.hexdigest()


def _partition(n: int, timing: dict | None) -> tuple[dict, dict, str]:
    cuts = [int(n * part) for part in (0.55, 0.70, 0.85)]
    order = np.random.default_rng(RANDOM_STATE).permutation(n)
    if timing:
        order = np.arange(n)
        instants = timing["forecast_at"]
        # A forecast timestamp belongs entirely to one window, even when the input has ties.
        for i, cut in enumerate(cuts):
            if i:
                cut = max(cut, cuts[i - 1])
            while cut < n and instants[cut - 1] == instants[cut]:
                cut += 1
            cuts[i] = cut
    partitions, metadata = {}, {}
    for name, first, end in zip(MIN_PARTITIONS, [0, *cuts], [*cuts, n]):
        indexes = order[first:end]
        info = {"n_before_embargo": len(indexes), "excluded_unavailable_labels": 0}
        if timing and len(indexes):
            forecasts, observed = timing["forecast_at"], timing["observed_at"]
            if end < n:
                next_start = forecasts[end]
                indexes = np.asarray([i for i in indexes if observed[i] < next_start], dtype=int)
                info["label_cutoff_at"] = next_start.isoformat()
                info["excluded_unavailable_labels"] = end - first - len(indexes)
            if len(indexes):
                info.update(
                    forecast_start=forecasts[indexes[0]].isoformat(),
                    forecast_end=forecasts[indexes[-1]].isoformat(),
                    last_label_available_at=max(observed[i] for i in indexes).isoformat(),
                )
        if len(indexes) < MIN_PARTITIONS[name]:
            raise ValueError(
                f"history CSV partition {name} has {len(indexes)} usable rows; "
                f"needs at least {MIN_PARTITIONS[name]} after timestamp grouping and label embargo"
            )
        partitions[name] = indexes
        metadata[name] = {**info, "n": len(indexes)}
    split = (
        "chronological 55/15/15/15 (UTC snapshot time, label availability embargo)" if timing
        else "random 55/15/15/15 (deterministic synthetic holdouts)"
    )
    return partitions, metadata, split


def _estimators(X: np.ndarray, y: np.ndarray) -> dict:
    estimators = {
        "random_forest": RandomForestRegressor(**_MODEL_PARAMS["random_forest"]),
        "hist_gradient_boosting": HistGradientBoostingRegressor(**_MODEL_PARAMS["hist_gradient_boosting"]),
        "linear_regression": LinearRegression(),
        "median": DummyRegressor(strategy="median"),
    }
    for estimator in estimators.values():
        estimator.fit(X, y)
    return estimators


def _predictions(estimators: dict, X: np.ndarray) -> dict:
    return {name: np.maximum(0.0, estimator.predict(X)) for name, estimator in estimators.items()}


def _blended(predictions: dict, weight: float) -> np.ndarray:
    return predictions["random_forest"] * weight + predictions["hist_gradient_boosting"] * (1 - weight)


def _metric(y: np.ndarray, p: np.ndarray) -> dict:
    return {
        "mae_h": round(float(mean_absolute_error(y, p)), 3),
        "rmse_h": round(float(root_mean_squared_error(y, p)), 3),
        "r2": round(float(r2_score(y, p)), 4) if np.ptp(y) else None,
        "n": len(y),
    }


def _selection(validation_y: np.ndarray, predicted: dict) -> tuple[str, float, dict]:
    def mae(p):
        return float(mean_absolute_error(validation_y, p))

    weight = min(_BLEND_WEIGHTS, key=lambda w: (mae(_blended(predicted, w)), abs(w - 0.5)))
    candidates = {
        **predicted,
        "ensemble_rf_hgb": _blended(predicted, weight),
        "ensemble_equal_weight": _blended(predicted, 0.50),
    }
    tie_order = ("median", "linear_regression", "random_forest", "hist_gradient_boosting", "ensemble_rf_hgb")
    selected = min(tie_order, key=lambda name: mae(candidates[name]))
    return selected, weight, {name: _metric(validation_y, p) for name, p in candidates.items()}


def _radius(y: np.ndarray, p: np.ndarray, level: float = INTERVAL_LEVEL) -> float:
    scores = np.abs(y - p)
    rank = math.ceil((len(scores) + 1) * level)
    if rank > len(scores):
        raise ValueError("calibration set too small for the selected interval level")
    return float(np.partition(scores, rank - 1)[rank - 1])


def _limits(prediction, radius):
    return np.maximum(0.0, prediction - radius), prediction + radius


def _experiment(X, y, partitions):
    train, valid, calibration, test = (partitions[name] for name in MIN_PARTITIONS)
    estimators = _estimators(X[train], y[train])
    selected, weight, validation = _selection(y[valid], _predictions(estimators, X[valid]))
    calibration_predictions = _predictions(estimators, X[calibration])
    calibration_predictions["ensemble_rf_hgb"] = _blended(calibration_predictions, weight)
    radius = _radius(y[calibration], calibration_predictions[selected])

    test_predictions = _predictions(estimators, X[test])
    test_predictions["ensemble_rf_hgb"] = _blended(test_predictions, weight)
    test_predictions["ensemble_equal_weight"] = _blended(test_predictions, 0.50)
    metrics = {name: _metric(y[test], p) for name, p in test_predictions.items()}
    lower, upper = _limits(test_predictions[selected], radius)
    covered = (y[test] >= lower) & (y[test] <= upper)
    interval = {
        "level": INTERVAL_LEVEL, "method": "split_conformal_absolute_residual",
        "coverage": round(float(covered.mean()), 4), "n_covered": int(covered.sum()),
        "mean_width_h": round(float(np.mean(upper - lower)), 3),
        "n_test": len(test), "n_calibration": len(calibration), "radius_h": round(radius, 3),
    }
    return estimators, selected, weight, validation, radius, metrics, interval


def _walk_forward(X, y, timing):
    """Two earlier expanding windows, with an independent fit inside each."""
    folds = []
    for fraction in (0.65, 0.80):
        n = int(fraction * len(X))
        if n < MIN_CSV_ROWS:
            continue
        instants = timing["forecast_at"]
        while n < len(instants) and instants[n - 1] == instants[n]:
            n += 1
        if n >= len(instants):
            continue
        prefix = {key: timing[key][:n] for key in ("forecast_at", "observed_at")}
        try:
            folded, dates, _ = _partition(n, prefix)
        except ValueError:
            continue
        # Simulate evaluation before the next forecast, including the fold's
        # test outcomes only if they had already arrived at that point.
        fold_test = folded["test"]
        known = np.asarray([i for i in fold_test if timing["observed_at"][i] < instants[n]], dtype=int)
        if len(known) < MIN_PARTITIONS["test"]:
            continue
        dates["test"].update(
            n=len(known),
            forecast_start=instants[known[0]].isoformat(),
            forecast_end=instants[known[-1]].isoformat(),
            last_label_available_at=max(timing["observed_at"][i] for i in known).isoformat(),
            label_cutoff_at=instants[n].isoformat(),
            excluded_unavailable_labels=len(fold_test) - len(known),
        )
        folded["test"] = known
        _, selected, _, _, _, metrics, interval = _experiment(X, y, folded)
        folds.append({
            "selected_model": selected, "partitions": dates,
            "test_metrics": metrics[selected], "interval": interval,
        })
    return {"status": "evaluated" if folds else "insufficient_data", "folds": folds}


def _fit(csv_path: str | Path | None = None, *, run_walk_forward: bool = False) -> dict:
    timing = None
    if csv_path:
        X, y, timing = load_history_csv(csv_path)
        kind = "csv (user-provided; provenance unverified)"
    else:
        X, y = _sample_history(np.random.default_rng(RANDOM_STATE))
        kind = "synthetic (corridor-rule delay labels with noise; archive weather marginals)"
    partitions, partition_info, split = _partition(len(X), timing)
    train = partitions["train"]
    X_train, y_train = X[train], y[train]
    estimators, selected, weight, validation, radius, metrics, interval = _experiment(X, y, partitions)
    walk_forward = {"status": "not_run" if timing else "not_applicable"}
    if timing and run_walk_forward:
        walk_forward = _walk_forward(X, y, timing)
    medians = np.median(X_train, axis=0)
    mad = np.median(np.abs(X_train - medians), axis=0)
    train_min, train_max = X_train.min(axis=0), X_train.max(axis=0)
    # A zero MAD often signals a mixed land/sea or sparse feature. Scale by
    # half the observed span so a legitimate nonzero value is not arbitrarily huge.
    mad = np.where(mad > 0, mad, np.maximum(1.0, (train_max - train_min) / 2))
    distances = np.sum(np.abs(X_train - medians) / mad, axis=1)
    data_hash = _dataset_hash(X, y, timing)
    versions = {"scikit-learn": sklearn.__version__, "numpy": np.__version__}
    model_hash = hashlib.sha256(json.dumps({
        "version": MODEL_VERSION, "dataset": data_hash, "params": _MODEL_PARAMS,
        "model": selected, "rf_weight": weight, "versions": versions, "split": split,
        "seed": RANDOM_STATE, "level": INTERVAL_LEVEL, "radius": radius,
    }, sort_keys=True).encode()).hexdigest()
    return {
        "status": "ok", "estimators": estimators, "selected_model": selected,
        "selection": {
            "metric": "validation_mae_h", "model": selected,
            "blend_weights": {"random_forest": weight, "hist_gradient_boosting": 1 - weight},
            "validation_metrics": validation,
        },
        "evaluation": {
            "test_metrics": metrics,
            "interval": interval,
            "walk_forward": walk_forward,
        },
        "radius": radius, "medians": medians, "mad": mad, "train_min": train_min, "train_max": train_max,
        "X_train": X_train, "y_train": y_train, "l1_train_p95": float(np.percentile(distances, 95)),
        "l1_train_max": float(distances.max()), "model_hash": model_hash,
        "dataset": {
            "kind": kind, "source_kind": "csv" if timing else "synthetic", "scope": DATA_SCOPE,
            "hash": data_hash, "n": len(X), "split": split, "partitions": partition_info,
            "seed": RANDOM_STATE, "versions": versions,
            "source": timing["source"] if timing else "Open-Meteo archive + corridor simulator",
            "shipment_identity_checked": timing["shipment_identity_checked"] if timing else None,
        },
        "feature_importance": sorted((
            {"feature": name, "importance": round(float(value), 4)} for name, value in
            zip(FEATURES, estimators["random_forest"].feature_importances_)
        ), key=lambda item: -item["importance"]),
    }


def _training_config() -> tuple:
    configured = os.environ.get(HISTORY_CSV_ENV, "")
    if not configured:
        return (None,)
    try:
        path = Path(configured).resolve()
        stat = path.stat()
        return str(path), stat.st_mtime_ns, stat.st_size, stat.st_ino
    except (OSError, RuntimeError, ValueError):
        return (configured,)


def get_trained() -> dict:
    """Fit once per configured dataset revision; cache rejected histories too."""
    global _TRAINED, _TRAINED_CONFIG
    config = _training_config()
    if _TRAINED is not None and _TRAINED_CONFIG == config:
        return _TRAINED
    with _TRAIN_LOCK:
        if _TRAINED is None or _TRAINED_CONFIG != config:
            try:
                _TRAINED = _fit(config[0])
            except (ValueError, OSError, csv.Error) as exc:
                reason = str(exc) if isinstance(exc, ValueError) and str(exc).startswith("history CSV") else (
                    "history CSV could not be read" if config[0] else "synthetic dataset could not be prepared"
                )
                log.warning("ML unavailable: %s", reason)
                _TRAINED = {"status": "unavailable", "error": reason,
                            "dataset": {"source_kind": "csv" if config[0] else "synthetic", "scope": DATA_SCOPE}}
            _TRAINED_CONFIG = config
        return _TRAINED


def _l1_to_median(trained: dict, x: np.ndarray) -> float:
    return float(np.sum(np.abs(x[0] - trained["medians"]) / trained["mad"]))


def extrapolation(trained: dict, x: np.ndarray) -> dict:
    distance = _l1_to_median(trained, x)
    p95 = trained["l1_train_p95"]
    outside = [
        {"feature": feature, "value": float(value), "train_min": float(low), "train_max": float(high)}
        for feature, value, low, high in zip(FEATURES, x[0], trained["train_min"], trained["train_max"])
        if value < low - 1e-9 or value > high + 1e-9
    ]
    return {
        "l1_to_median": round(distance, 2), "train_p95": round(p95, 2),
        "train_max": round(trained["l1_train_max"], 2),
        "is_extrapolating": bool(outside or distance > p95), "range_violations": outside,
        "metric": "MAD-scaled L1 (Manhattan) to training median; training min/max",
        "note": "Выход за диапазон признака или выше 95-го перцентиля L1. Для решения используйте скоринг.",
    }


def l1_analogs(trained: dict, x: np.ndarray, k: int = 3) -> list[dict]:
    distances = np.sum(np.abs(trained["X_train"] - x[0]) / trained["mad"], axis=1)
    closest = np.argsort(distances, kind="stable")[:k]
    return [{
        "l1": round(float(distances[index]), 2), "delay_h": round(float(trained["y_train"][index]), 1),
        **{key: round(float(trained["X_train"][index, FEATURES.index(key)]), precision) for key, precision in (
            ("max_wave_m", 2), ("max_wind_ms", 1), ("max_congestion_pct", 1), ("total_customs_h", 1),
        )},
    } for index in closest]


def _remaining_distance(leg: dict) -> float:
    if leg.get("in_transit") and "base_remaining_h" in leg and leg["base_h"] > 0:
        return leg["distance_km"] * min(1.0, max(0.0, leg["base_remaining_h"] / leg["base_h"]))
    return float(leg["distance_km"])


def route_features(route: dict, weight_t: float, storm: bool, scenario: dict | None = None) -> dict:
    """Aggregate a route or a dispatcher projection at the forecast time."""
    scenario = scenario or {}
    wave = wind = congestion = customs = sea_km = rail_km = 0.0
    sea_legs = 0
    closed = set(scenario.get("closed_nodes", []))
    for leg in route["legs"]:
        if leg["to"] in closed or (leg["from"] in closed and not leg.get("in_transit")):
            raise ValueError(f"leg {leg['id']} blocked: node closed by scenario")
        if leg["mode"] == "sea":
            sea_legs += 1
            sea_km += _remaining_distance(leg)
            conditions = leg.get("weather") or model._sea_weather(leg, storm, scenario)
            wave = max(wave, conditions["wave_m"])
            wind = max(wind, conditions["wind_ms"])
        elif leg["mode"] == "rail":
            rail_km += _remaining_distance(leg)
        port = leg.get("port")
        if "congestion_pct" in leg:
            congestion = max(congestion, leg["congestion_pct"])
        elif port in data.PORTS:
            override = scenario.get("congestion", {}).get(port)
            congestion = max(congestion, override if override is not None else data.get_port(port)["congestion_pct"])
        customs += leg.get("effective_customs_h", leg.get("customs_h", 0) + scenario.get("customs_extra_h", 0))
    return dict(zip(FEATURES, (wave, wind, congestion, customs, sea_km, rail_km, weight_t, sea_legs)))


def _predict_batch(trained: dict, x: np.ndarray) -> tuple[dict, list[dict]]:
    samples = np.repeat(x, len(FEATURES) + 1, axis=0)
    idx = np.arange(len(FEATURES))
    samples[idx + 1, idx] = trained["medians"]
    estimators = trained["estimators"]
    names = {"random_forest", "hist_gradient_boosting", trained["selected_model"]} & estimators.keys()
    predictions = _predictions({name: estimators[name] for name in names}, samples)
    predictions["ensemble_rf_hgb"] = _blended(predictions, trained["selection"]["blend_weights"]["random_forest"])
    chosen = predictions[trained["selected_model"]]
    factors = sorted((
        {"feature": key, "contribution_h": round(float(chosen[0] - chosen[index + 1]), 2)}
        for index, key in enumerate(FEATURES)
    ), key=lambda entry: -abs(entry["contribution_h"]))[:3]
    return {name: float(values[0]) for name, values in predictions.items()}, factors


def _reliability(trained: dict, route: dict, ood: dict) -> dict:
    scope_limitations = []
    if any(leg["mode"] == "road" for leg in route["legs"]):
        scope_limitations.append("road_distance_not_modelled")
    if any(leg.get("in_transit") for leg in route["legs"]):
        scope_limitations.append("in_transit_phase_not_a_feature")
    if any(leg.get("reported_delay_h", 0) > 0 for leg in route["legs"]):
        scope_limitations.append("reported_delay_not_a_feature")
    if trained["status"] != "ok":
        status, reason = "scoring_only", "ML недоступна для текущего источника истории."
    elif ood["is_extrapolating"]:
        status, reason = "out_of_distribution", "Параметры вне области обучающей выборки."
    elif trained["dataset"]["source_kind"] == "synthetic":
        status, reason = "synthetic_only", "Метки смоделированы. Прогноз показан для сравнения."
    else:
        status, reason = "unverified_history", "Источник и репрезентативность предоставленной истории требуют проверки."
    return {
        "status": status, "preferred_source": "scoring", "reason": reason,
        "range_violations": ood.get("range_violations", []), "scope_limitations": scope_limitations,
    }


def predict_delay(
    route: dict, weight_t: float, storm: bool = False, scenario: dict | None = None, *,
    scoring_delay_h: float | None = None,
    trained: dict | None = None,
) -> dict:
    feat = route_features(route, weight_t, storm, scenario)
    if scoring_delay_h is None:
        scoring_delay_h = sum(
            model.predict_leg(leg, storm, scenario)["delay_h"] for leg in route["legs"]
        )
    scoring_delay_h = round(max(0.0, scoring_delay_h), 1)
    trained = get_trained() if trained is None else trained
    common = {
        "route_id": route["id"], "route_name": route["name"], "storm": storm,
        "features": feat, "model_version": MODEL_VERSION, "dataset_kind": trained["dataset"]["source_kind"],
        "dataset_scope": DATA_SCOPE,
        "scoring_delay_h": scoring_delay_h, "preferred_delay_h": scoring_delay_h,
    }
    if trained["status"] != "ok":
        return {
            **common, "status": "unavailable", "error": trained["error"],
            "selected_model": None, "model_hash": None, "dataset_hash": None,
            "ml_delay_h": None, "ml_model": None, "label_kind": trained["dataset"]["source_kind"],
            "rf_delay_h": None, "hgb_delay_h": None, "difference_h": None,
            "top_factors": [], "local_attribution": [], "analogs": [], "analogs_kind": None,
            "interval": None, "extrapolation": {}, "reliability": _reliability(trained, route, {}),
        }
    x = np.asarray([[feat[key] for key in FEATURES]], dtype=float)
    points, factors = _predict_batch(trained, x)
    selected = trained["selected_model"]
    point = points[selected]
    lower, upper = _limits(point, trained["radius"])
    ood = extrapolation(trained, x)
    reliability = _reliability(trained, route, ood)
    kind = trained["dataset"]["source_kind"]
    return {
        **common, "status": "ok", "model_hash": trained["model_hash"],
        "dataset_hash": trained["dataset"]["hash"], "selected_model": selected,
        "ml_delay_h": round(point, 1), "ml_model": _MODEL_NAMES[selected],
        "label_kind": trained["dataset"]["kind"], "rf_delay_h": round(points["random_forest"], 1),
        "hgb_delay_h": round(points["hist_gradient_boosting"], 1),
        "difference_h": round(abs(round(point, 1) - scoring_delay_h), 1),
        "top_factors": trained["feature_importance"][:3], "local_attribution": factors,
        "explanation_method": "one_feature_at_a_time_training_median_occlusion",
        "extrapolation": ood, "reliability": reliability, "analogs": l1_analogs(trained, x),
        "analogs_kind": "synthetic demo analogs" if kind == "synthetic" else "user-provided CSV training analogs",
        "interval": {
            "lower_h": math.floor(lower * 10) / 10, "upper_h": math.ceil(upper * 10) / 10,
            "level": INTERVAL_LEVEL, "method": "split_conformal_absolute_residual",
            "scope": "synthetic benchmark" if kind == "synthetic" else "user-provided history",
            "in_distribution": not ood["is_extrapolating"],
            "calibration_applicable": not ood["is_extrapolating"] and not reliability["scope_limitations"],
            "usable": reliability["preferred_source"] == "ml",
        },
    }


def info(trained: dict | None = None) -> dict:
    trained = get_trained() if trained is None else trained
    if trained["status"] != "ok":
        return {
            "status": "unavailable", "error": trained["error"], "model_version": MODEL_VERSION,
            "model_hash": None, "selected_model": None, "dataset": trained["dataset"],
            "features": FEATURES, "models": [], "metrics": {}, "feature_importance": [],
            "evaluation": None, "selection": None, "trained_on": "История недоступна",
            "honest_note": "ML недоступна. Применяется объяснимый скоринг.",
            **{f"n_{name}": 0 for name in MIN_PARTITIONS},
        }
    dataset = trained["dataset"]
    synthetic = dataset["source_kind"] == "synthetic"
    return {
        "status": "ok", "model_version": MODEL_VERSION, "model_hash": trained["model_hash"],
        "selected_model": trained["selected_model"], "selection": trained["selection"],
        "dataset": dataset, "evaluation": trained["evaluation"], "features": FEATURES,
        "trained_on": (
            f"{dataset['n']} симуляций. Волна и ветер независимо взяты из архивных распределений Open-Meteo."
            if synthetic else f"{dataset['n']} снимков из предоставленного CSV."
        ),
        "honest_note": (
            "synthetic benchmark. Цели сгенерированы правилами и шумом. Результат измеряет качество на симуляциях."
            if synthetic else
            "Хронологическая оценка по предоставленному CSV. Происхождение и полноту истории проверяет владелец данных."
        ),
        "models": [*list(_MODEL_NAMES.values()), "Ensemble RF + HGB (50/50 reference)"],
        "metrics": trained["evaluation"]["test_metrics"],
        "feature_importance": trained["feature_importance"], "feature_importance_kind": "random_forest_impurity",
        **{f"n_{name}": partition["n"] for name, partition in dataset["partitions"].items()},
    }
