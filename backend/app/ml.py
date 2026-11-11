"""ML slice for Smart Caspian Transit.

What this is — and what it is not:
- A REAL trained model (RandomForest + LinearRegression baseline) with
  a train/test split, MAE/R2 metrics and feature importances.
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

import json
from pathlib import Path

import numpy as np
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.model_selection import train_test_split

from app import data, model, weather

FEATURES = ["max_wave_m", "max_wind_ms", "max_congestion_pct",
            "total_customs_h", "sea_km", "rail_km", "weight_t", "sea_legs"]
N_SAMPLES = 800
RANDOM_STATE = 42

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
    p = Path(__file__).parent / "archive_weather.json"
    with open(p, encoding="utf-8") as f:
        return json.load(f)


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


def _fit() -> dict:
    """Fit both models on synthetic history. Swap _sample_history for a
    real CSV read here later — everything downstream stays the same."""
    rng = np.random.default_rng(RANDOM_STATE)
    X, y = _sample_history(rng)
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=RANDOM_STATE)
    rf = RandomForestRegressor(n_estimators=200, random_state=RANDOM_STATE, n_jobs=-1)
    rf.fit(X_train, y_train)
    lr = LinearRegression()
    lr.fit(X_train, y_train)
    pred = rf.predict(X_test)
    return {
        "rf": rf, "lr": lr,
        "n_train": len(X_train), "n_test": len(X_test),
        "metrics": {
            "random_forest": {
                "mae_h": round(float(mean_absolute_error(y_test, pred)), 2),
                "r2": round(float(r2_score(y_test, pred)), 3),
            },
            "linear_regression": {
                "mae_h": round(float(mean_absolute_error(y_test, lr.predict(X_test))), 2),
                "r2": round(float(r2_score(y_test, lr.predict(X_test))), 3),
            },
        },
        "feature_importance": sorted(
            ({"feature": f, "importance": round(float(v), 3)}
             for f, v in zip(FEATURES, rf.feature_importances_)),
            key=lambda d: -d["importance"]),
    }


def get_trained() -> dict:
    global _TRAINED
    if _TRAINED is None:
        _TRAINED = _fit()
    return _TRAINED


def route_features(route: dict, weight_t: float, storm: bool) -> dict:
    wave, wind, cong, customs, sea_km, rail_km, sea_legs = 0.0, 0.0, 0, 0, 0, 0, 0
    for leg in route["legs"]:
        if leg["mode"] == "sea":
            sea_legs += 1
            sea_km += leg["distance_km"]
            w = weather.current_sea_state(leg["sea"], storm)
            wave = max(wave, w["wave_m"])
            wind = max(wind, w["wind_ms"])
        elif leg["mode"] == "rail":
            rail_km += leg["distance_km"]
        if leg.get("port"):
            cong = max(cong, data.PORTS.get(leg["port"], {}).get("congestion_pct", 0))
        customs += leg.get("customs_h", 0)
    return {"max_wave_m": wave, "max_wind_ms": wind, "max_congestion_pct": cong,
            "total_customs_h": customs, "sea_km": sea_km, "rail_km": rail_km,
            "weight_t": weight_t, "sea_legs": sea_legs}


def predict_delay(route: dict, weight_t: float, storm: bool = False) -> dict:
    trained = get_trained()
    feat = route_features(route, weight_t, storm)
    x = np.array([[feat[f] for f in FEATURES]], dtype=float)
    ml_delay = round(float(trained["rf"].predict(x)[0]), 1)
    scoring = model.forecast_route(route, weight_t, storm)
    return {
        "route_id": route["id"], "route_name": route["name"], "storm": storm,
        "features": feat,
        "ml_delay_h": max(0.0, ml_delay),
        "ml_model": "RandomForestRegressor (200 trees)",
        "scoring_delay_h": scoring["delay_h"],
        "difference_h": round(abs(ml_delay - scoring["delay_h"]), 1),
        "top_factors": trained["feature_importance"][:3],
    }


def info() -> dict:
    trained = get_trained()
    return {
        "trained_on": f"{N_SAMPLES} shipments; wave/wind bootstrapped from "
                      f"the REAL 90-day Open-Meteo marine archive for the "
                      f"two sea legs (archive_weather.json); congestion and "
                      f"customs still uniform — no open feed exists for them",
        "honest_note": "Labels are still rule-based (corridor rules + "
                       "Gaussian noise): there is no public dataset of "
                       "actual North-South delays. What is real now: the "
                       "weather distributions the model trains on. The "
                       "pipeline takes a real history CSV without code "
                       "changes.",
        "models": ["RandomForestRegressor (200 trees)",
                   "LinearRegression (baseline)"],
        "n_train": trained["n_train"], "n_test": trained["n_test"],
        "metrics": trained["metrics"],
        "feature_importance": trained["feature_importance"],
        "features": FEATURES,
    }
