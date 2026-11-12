"""ML slice for Smart Caspian Transit.

What this is — and what it is not:
- A REAL trained model (RandomForest + LinearRegression baseline) with
  a train/test split, MAE/R2 metrics and feature importances.
- v0.3: ensemble — RandomForest + HistGradientBoosting (sklearn),
  prediction = mean of the two; per-prediction occlusion attribution
  (replace a feature by its training median, measure the change).
- v0.2: wave/wind features are BOOTSTRAPPED FROM REAL DATA — the
  90-day Open-Meteo marine archive for the two sea legs
  (archive_weather.json, fetched 2026-11-12: north mean wave 0.45 m,
  west mean 0.71 m, west wave >= 2 m in 2.3% of hours). Congestion
  and customs are still uniform — no open feed exists for them.
- Labels are still rule-based (corridor rules + Gaussian noise):
  there is no public dataset of actual North-South delays. We say
  this in /api/ml/info and on screen.
- The pipeline is production-shaped: the same `predict_delay`
  signature will take a real history CSV later; only the data source
  changes (see `_fit` docstring).

Train once at import (fast: <2 s for 800 rows).
"""

from __future__ import annotations

import csv
import hashlib
import os
from pathlib import Path

import numpy as np
import sklearn
from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.model_selection import train_test_split

from app import data, model, weather

FEATURES = [
    "max_wave_m",
    "max_wind_ms",
    "max_congestion_pct",
    "total_customs_h",
    "sea_km",
    "rail_km",
    "weight_t",
    "sea_legs",
]
N_SAMPLES = 800
RANDOM_STATE = 42
HISTORY_CSV_ENV = "SCT_ML_HISTORY_CSV"
CSV_REQUIRED = FEATURES + ["delay_h"]
CSV_OPTIONAL_TS = "departed_at"

_TRAINED: dict | None = None


def _generative_delay(feat: dict) -> float:
    """Same corridor rules as model.predict_leg, collapsed to one route."""
    delay = 0.0
    if feat["max_congestion_pct"] >= 60:
        delay += feat["max_congestion_pct"] * 0.12
    if feat["sea_legs"] > 0:
        delay += feat["max_wave_m"] * 3 * feat["sea_legs"]
        if feat["max_wind_ms"] > 14:
            delay += 6
        if feat["max_wave_m"] >= 2.0:
            delay += 48  # missed sailing, no fixed timetable on the Caspian
    delay += 1.5 * (3 - feat["sea_legs"])  # rail legs reserve
    delay += feat["total_customs_h"]
    return delay


def _load_archive() -> dict:
    return weather.load_archive()


def _sample_history(rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    archive = _load_archive()["segments"]
    segs = ["caspian_north", "caspian_west"]
    X, y = [], []
    for _ in range(N_SAMPLES):
        sea_legs = 1 if rng.random() < 0.6 else 0
        if sea_legs:
            seg = archive[segs[int(rng.integers(0, 2))]]
            wave = float(rng.choice(seg["wave_m"]["samples"]))
            wind = float(rng.choice(seg["wind_ms"]["samples"]))
            sea_km = int(rng.choice([445, 930]))
        else:
            wave, wind, sea_km = 0.0, 0.0, 0
        feat = {
            "max_wave_m": wave,
            "max_wind_ms": wind,
            "max_congestion_pct": int(rng.integers(20, 95)),
            "total_customs_h": int(rng.integers(0, 20)),
            "sea_km": sea_km,
            "rail_km": int(rng.integers(1200, 3600)),
            "weight_t": int(rng.choice([48, 96, 300, 1200])),
            "sea_legs": sea_legs,
        }
        target = _generative_delay(feat) + float(rng.normal(0, 2.5))
        X.append([feat[f] for f in FEATURES])
        y.append(max(0.0, round(target, 1)))
    return np.array(X, dtype=float), np.array(y, dtype=float)


def load_history_csv(path: str | Path) -> tuple[np.ndarray, np.ndarray, dict]:
    """Real-history CSV contract (see docs/ML_DATA_CONTRACT.md).

    Required columns: the 8 FEATURES + delay_h (actual observed delay, hours).
    Optional departed_at (ISO timestamp) enables a chronological split.
    Rows must be numeric; delay_h >= 0. Raises ValueError on bad input —
    never silently trains on malformed data."""
    path = Path(path)
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        cols = reader.fieldnames or []
        missing = [c for c in CSV_REQUIRED if c not in cols]
        if missing:
            raise ValueError(f"history CSV missing columns: {missing}")
        rows = list(reader)
    if not rows:
        raise ValueError("history CSV is empty")
    X, y, ts = [], [], []
    for i, r in enumerate(rows):
        try:
            X.append([float(r[c]) for c in FEATURES])
            d = float(r["delay_h"])
        except (TypeError, ValueError):
            raise ValueError(f"history CSV row {i + 2}: non-numeric value")
        if d < 0:
            raise ValueError(f"history CSV row {i + 2}: negative delay_h")
        y.append(d)
        ts.append(r.get(CSV_OPTIONAL_TS) or "")
    meta = {"n": len(rows), "has_timestamps": all(ts), "source": str(path)}
    return np.array(X, dtype=float), np.array(y, dtype=float), meta


def _dataset_hash(X: np.ndarray, y: np.ndarray) -> str:
    return hashlib.sha256(X.tobytes() + y.tobytes()).hexdigest()[:16]


def _fit() -> dict:
    """Fit RF + HistGradientBoosting + LinearRegression baseline.

    Data source: SCT_ML_HISTORY_CSV (real observed outcomes, CSV contract)
    or the built-in SYNTHETIC generator (rule labels + noise; wave/wind
    bootstrapped from the real Open-Meteo archive). The synthetic benchmark
    measures how well models learn the generator — NOT real-world accuracy."""
    csv_path = os.environ.get(HISTORY_CSV_ENV, "")
    if csv_path:
        X, y, meta = load_history_csv(csv_path)
        dataset_kind = (
            "csv (user-provided; verify provenance before claiming real-world accuracy)"
        )
        if meta["has_timestamps"]:
            order = np.argsort([r for r in _csv_timestamps(csv_path)])
            X, y = X[order], y[order]
            cut = int(len(X) * 0.8)
            X_train, X_test, y_train, y_test = X[:cut], X[cut:], y[:cut], y[cut:]
            split = "chronological 80/20 (by departed_at)"
        else:
            X_train, X_test, y_train, y_test = train_test_split(
                X, y, test_size=0.2, random_state=RANDOM_STATE
            )
            split = "random 80/20 (no timestamps in CSV)"
    else:
        rng = np.random.default_rng(RANDOM_STATE)
        X, y = _sample_history(rng)
        dataset_kind = "synthetic (rule-generated labels + Gaussian noise)"
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=RANDOM_STATE
        )
        split = "random 80/20 (synthetic i.i.d. samples)"
    rf = RandomForestRegressor(n_estimators=200, random_state=RANDOM_STATE, n_jobs=-1)
    rf.fit(X_train, y_train)
    hgb = HistGradientBoostingRegressor(random_state=RANDOM_STATE)
    hgb.fit(X_train, y_train)
    lr = LinearRegression()
    lr.fit(X_train, y_train)
    pred_rf = rf.predict(X_test)
    pred_hgb = hgb.predict(X_test)
    pred_ens = (pred_rf + pred_hgb) / 2
    medians = np.median(X_train, axis=0)
    mad = np.median(np.abs(X_train - medians), axis=0)
    mad[mad == 0] = 1.0
    train_l1 = np.sum(np.abs(X_train - medians) / mad, axis=1)

    def _metrics(p):
        return {
            "mae_h": round(float(mean_absolute_error(y_test, p)), 2),
            "r2": round(float(r2_score(y_test, p)), 3),
        }

    return {
        "rf": rf,
        "hgb": hgb,
        "lr": lr,
        "medians": medians,
        "X_train": X_train,
        "y_train": y_train,
        "mad": mad,
        "l1_train_p95": float(np.percentile(train_l1, 95)),
        "l1_train_max": float(train_l1.max()),
        "n_train": len(X_train),
        "n_test": len(X_test),
        "dataset": {
            "kind": dataset_kind,
            "hash": _dataset_hash(X, y),
            "n": len(X),
            "split": split,
            "seed": RANDOM_STATE,
            "versions": {"scikit-learn": sklearn.__version__, "numpy": np.__version__},
        },
        "metrics": {
            "random_forest": _metrics(pred_rf),
            "hist_gradient_boosting": _metrics(pred_hgb),
            "ensemble_rf_hgb": _metrics(pred_ens),
            "linear_regression": _metrics(lr.predict(X_test)),
        },
        "feature_importance": sorted(
            (
                {"feature": f, "importance": round(float(v), 3)}
                for f, v in zip(FEATURES, rf.feature_importances_)
            ),
            key=lambda d: -d["importance"],
        ),
    }


def _csv_timestamps(path: str) -> list[str]:
    with open(path, newline="", encoding="utf-8") as f:
        return [r.get(CSV_OPTIONAL_TS) or "" for r in csv.DictReader(f)]


def _ensemble_predict(trained: dict, x: np.ndarray) -> float:
    return float((trained["rf"].predict(x)[0] + trained["hgb"].predict(x)[0]) / 2)


def _occlusion_attribution(trained: dict, x: np.ndarray) -> list[dict]:
    """Per-prediction local explanation: replace each feature by its
    training median and measure the ensemble's change. Honest and
    cheap; documented as occlusion attribution, not SHAP."""
    base = _ensemble_predict(trained, x)
    out = []
    for i, f in enumerate(FEATURES):
        x2 = x.copy()
        x2[0, i] = trained["medians"][i]
        delta = round(base - _ensemble_predict(trained, x2), 1)
        out.append({"feature": f, "contribution_h": delta})
    return sorted(out, key=lambda d: -abs(d["contribution_h"]))[:3]


def get_trained() -> dict:
    global _TRAINED
    if _TRAINED is None:
        _TRAINED = _fit()
    return _TRAINED


def _l1_to_median(trained: dict, x: np.ndarray) -> float:
    """MAD-scaled Manhattan distance from a feature vector to the training
    median — robust to outliers (an anomaly grows linearly, not quadratically
    as in L2). The 'Manhattan' metric in feature space."""
    return float(np.sum(np.abs(x[0] - trained["medians"]) / trained["mad"]))


def extrapolation(trained: dict, x: np.ndarray) -> dict:
    """Out-of-distribution alarm: when the current shipment is farther from
    the training experience than 95% of training samples, the ensemble is
    extrapolating — trust the explainable scoring instead. Verified: calm R1
    ~10.1 (inside), storm R1 ~25.1 (outside; train p95 ~12.1, max ~17.5)."""
    d = _l1_to_median(trained, x)
    p95 = trained["l1_train_p95"]
    return {
        "l1_to_median": round(d, 2),
        "train_p95": round(p95, 2),
        "train_max": round(trained["l1_train_max"], 2),
        "is_extrapolating": d > p95,
        "metric": "MAD-scaled L1 (Manhattan) to training median",
        "note": (
            "Флаг экстраполяции: модель за пределами своего опыта — "
            "доверяйте объяснимому скорингу. Метки синтетические."
        ),
    }


def l1_analogs(trained: dict, x: np.ndarray, k: int = 3) -> list[dict]:
    """k nearest training scenarios by MAD-scaled L1. Illustrative 'similar
    cases', NOT a precise predictor (calm analogs spread widely). Labels are
    synthetic — shown as demo analogs."""
    d = np.sum(np.abs(trained["X_train"] - x[0]) / trained["mad"], axis=1)
    idx = np.argsort(d)[:k]
    out = []
    for i in idx:
        row = dict(zip(FEATURES, trained["X_train"][i]))
        out.append(
            {
                "l1": round(float(d[i]), 2),
                "delay_h": round(float(trained["y_train"][i]), 1),
                "max_wave_m": round(float(row["max_wave_m"]), 2),
                "max_wind_ms": round(float(row["max_wind_ms"]), 1),
                "max_congestion_pct": int(row["max_congestion_pct"]),
                "total_customs_h": round(float(row["total_customs_h"]), 1),
            }
        )
    return out


def route_features(
    route: dict, weight_t: float, storm: bool, scenario: dict | None = None
) -> dict:
    """Same scenario semantics as the scoring model (model._sea_weather):
    explicit wave/wind > storm > live; customs extra on every leg; closed
    nodes raise ValueError."""
    scenario = scenario or {}
    wave, wind, cong, customs, sea_km, rail_km, sea_legs = 0.0, 0.0, 0, 0, 0, 0, 0
    for leg in route["legs"]:
        if leg["from"] in scenario.get("closed_nodes", []) or leg["to"] in scenario.get(
            "closed_nodes", []
        ):
            raise ValueError(f"leg {leg['id']} blocked: node closed by scenario")
        if leg["mode"] == "sea":
            sea_legs += 1
            sea_km += leg["distance_km"]
            w = model._sea_weather(leg, storm, scenario)
            wave = max(wave, w["wave_m"])
            wind = max(wind, w["wind_ms"])
        elif leg["mode"] == "rail":
            rail_km += leg["distance_km"]
        if leg.get("port"):
            c = scenario.get("congestion", {}).get(leg["port"])
            if c is None:
                c = data.PORTS.get(leg["port"], {}).get("congestion_pct", 0)
            cong = max(cong, c)
        customs += leg.get("customs_h", 0) + scenario.get("customs_extra_h", 0)
    return {
        "max_wave_m": wave,
        "max_wind_ms": wind,
        "max_congestion_pct": cong,
        "total_customs_h": customs,
        "sea_km": sea_km,
        "rail_km": rail_km,
        "weight_t": weight_t,
        "sea_legs": sea_legs,
    }


def predict_delay(
    route: dict, weight_t: float, storm: bool = False, scenario: dict | None = None
) -> dict:
    trained = get_trained()
    feat = route_features(route, weight_t, storm, scenario)
    x = np.array([[feat[f] for f in FEATURES]], dtype=float)
    ml_delay = round(_ensemble_predict(trained, x), 1)
    scoring = model.forecast_route(route, weight_t, storm, scenario)
    return {
        "route_id": route["id"],
        "route_name": route["name"],
        "storm": storm,
        "features": feat,
        "ml_delay_h": max(0.0, ml_delay),
        "ml_model": "Ensemble: RandomForest (200 trees) + HistGradientBoosting",
        "label_kind": trained["dataset"]["kind"],
        "rf_delay_h": round(float(trained["rf"].predict(x)[0]), 1),
        "hgb_delay_h": round(float(trained["hgb"].predict(x)[0]), 1),
        "scoring_delay_h": scoring["delay_h"],
        "difference_h": round(abs(ml_delay - scoring["delay_h"]), 1),
        "top_factors": trained["feature_importance"][:3],
        "local_attribution": _occlusion_attribution(trained, x),
        "extrapolation": extrapolation(trained, x),
        "analogs": l1_analogs(trained, x),
        "analogs_kind": (
            "synthetic demo analogs (kNN by MAD-scaled L1); "
            "illustrative similar cases, not a precise predictor"
        ),
    }


def info() -> dict:
    trained = get_trained()
    return {
        "trained_on": f"{trained['dataset']['n']} samples; wave/wind bootstrapped from "
        f"the REAL 90-day Open-Meteo marine archive for the "
        f"two sea legs (archive_weather.json); congestion and "
        f"customs still uniform — no open feed exists for them",
        "honest_note": "Экспериментальная модель. Метки задержек синтетические "
        "(правила коридора + шум): открытого датасета фактических "
        "задержек МТК «Север–Юг» нет. Метрики ниже — synthetic "
        "benchmark (качество воспроизведения генератора), НЕ точность "
        "прогноза реальных рейсов. Реальные данные подключаются через "
        f"CSV-контракт ({HISTORY_CSV_ENV}, docs/ML_DATA_CONTRACT.md) "
        "и сбор событий (POST /api/shipments/{id}/events).",
        "dataset": trained["dataset"],
        "models": [
            "Ensemble: RandomForestRegressor (200 trees) + "
            "HistGradientBoostingRegressor",
            "LinearRegression (baseline)",
        ],
        "n_train": trained["n_train"],
        "n_test": trained["n_test"],
        "metrics": trained["metrics"],
        "feature_importance": trained["feature_importance"],
        "features": FEATURES,
    }
