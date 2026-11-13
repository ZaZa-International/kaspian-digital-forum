#!/usr/bin/env python3
"""Evaluate a fixed ML pipeline offline and report warm inference timing."""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app import data, ml  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", type=Path, help="Snapshot CSV; omit to evaluate the synthetic benchmark")
    parser.add_argument("--walk-forward", action="store_true", help="Add earlier chronological CSV folds")
    parser.add_argument("--samples", type=int, default=40, help="Number of warmed inference calls (default 40)")
    args = parser.parse_args()
    if not 1 <= args.samples <= 1000:
        parser.error("--samples must be between 1 and 1000")
    if args.walk_forward and args.history is None:
        parser.error("--walk-forward requires --history")

    started = perf_counter()
    try:
        trained = ml._fit(args.history, run_walk_forward=args.walk_forward)
    except (ValueError, OSError, csv.Error) as exc:
        parser.error(str(exc))
    training_ms = (perf_counter() - started) * 1000
    report = ml.info(trained)
    route = data.get_route("R1")
    snapshot = {
        **route,
        "legs": [{
            **leg,
            "effective_customs_h": leg.get("customs_h", 0),
            "congestion_pct": data.PORTS.get(leg.get("port"), {}).get("congestion_pct", 0),
            **({"weather": data.WEATHER[leg["sea"]]} if leg["mode"] == "sea" else {}),
        } for leg in route["legs"]],
    }
    timings = []
    with patch.object(ml, "get_trained", return_value=trained):
        ml.predict_delay(snapshot, 48, scoring_delay_h=9.2)
        for _ in range(args.samples):
            started = perf_counter()
            ml.predict_delay(snapshot, 48, scoring_delay_h=9.2)
            timings.append((perf_counter() - started) * 1000)
    report["timing"] = {
        "fit_evaluate_ms": round(training_ms, 2), "warm_predictions": len(timings),
        "warm_p50_ms": round(float(np.percentile(timings, 50)), 2),
        "warm_p95_ms": round(float(np.percentile(timings, 95)), 2),
        "scope": "local Python forecast with prepared features and explanations; excludes HTTP, weather and database",
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
