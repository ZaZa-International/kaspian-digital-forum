"""Immutable live forecasts and evaluation against operator-reported arrivals."""

from __future__ import annotations

import csv
import io
import json
import math
import uuid
from datetime import datetime
from itertools import groupby
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Response

from app import data, ml, operations, store

_NON_FACT_SOURCES = {"demo-simulation", "operations-decision", "demo", "simulated", "synthetic"}
_REASONS = {
    "waiting_for_arrival": "Ожидается фактическое прибытие на последнем плече.",
    "demo_shipment": "Демонстрационный груз исключён из оценки и выгрузки.",
    "plan_changed": "После снимка принята другая версия маршрута.",
    "missing_actual_time": "У прибытия на последнем плече нет фактического времени.",
    "ambiguous_arrival": "Указаны разные фактические времена прибытия на последнем плече.",
    "arrival_before_forecast": "Фактическое прибытие произошло до снимка прогноза или одновременно с ним.",
    "waiting_for_record": "Запись о прибытии пока недоступна на момент оценки.",
    "movement_incomplete": "Прибытие пока не завершает маршрут по фактическим событиям движения.",
    "operator_arrival": "Сопоставлен с записью оператора о прибытии.",
}


def _timestamp(value: str | None) -> datetime | None:
    if value is not None:
        try:
            return operations._timestamp(value)
        except (OverflowError, ValueError):
            pass
    return None


def _arrival_state(shipment: dict, plan: dict, events: list[dict], now: datetime) -> tuple[dict, bool]:
    by_leg: dict[str, list[dict]] = {}
    known_movement = []
    for event in operations._effective_events(events):
        source = event["source"].strip().casefold()
        confidence = (event.get("confidence") or "").strip().casefold()
        if not event["leg_id"] or not source or source in _NON_FACT_SOURCES or confidence in _NON_FACT_SOURCES:
            continue
        if event["event_type"] == "arrival":
            by_leg.setdefault(event["leg_id"], []).append(event)
        if event["event_type"] in {"arrival", "departure"}:
            occurred_at = _timestamp(event["actual_ts"] or event["occurred_ts"])
            received_at = _timestamp(event["received_ts"])
            if occurred_at is not None and received_at is not None and max(occurred_at, received_at) <= now:
                known_movement.append(event)
    progress = operations._progress(shipment, plan, known_movement, now)
    return by_leg, progress["status"] == "delivered"


def _outcome(snapshot: dict, plan_version: int, arrival_state: tuple[dict, bool], now: datetime) -> tuple[str, str, dict | None]:
    if snapshot["demo"]:
        return "excluded", "demo_shipment", None
    if snapshot["plan_version"] != plan_version:
        return "excluded", "plan_changed", None
    arrivals, delivered = arrival_state
    final_leg = snapshot["remaining_leg_ids"][-1]
    events = arrivals.get(final_leg, ())
    if not events:
        return "pending", "waiting_for_arrival", None
    candidates = []
    for event in events:
        occurred_at = _timestamp(event["actual_ts"] or event["occurred_ts"])
        received_at = _timestamp(event["received_ts"])
        if occurred_at is not None and received_at is not None:
            candidates.append((occurred_at, received_at, event))
    if not candidates:
        return "pending", "missing_actual_time", None
    available = [row for row in candidates if max(row[0], row[1]) <= now]
    if not available:
        return "pending", "waiting_for_record", None
    if len({occurred for occurred, _, _ in available}) > 1:
        return "excluded", "ambiguous_arrival", None
    occurred_at, received_at, event = min(available, key=lambda row: (row[1], row[2]["id"]))
    forecast_at = operations._timestamp(snapshot["forecast_at"])
    if occurred_at <= forecast_at or received_at <= forecast_at:
        return "excluded", "arrival_before_forecast", None
    if not delivered:
        return "pending", "movement_incomplete", None
    elapsed = (occurred_at - forecast_at).total_seconds() / 3600
    delay = max(0.0, elapsed - snapshot["baseline"]["base_remaining_h"])
    return "matched", "operator_arrival", {
        "leg_id": final_leg, "event_id": event["id"], "source": event["source"],
        "correction_of": event["correction_of"], "occurred_at": occurred_at.isoformat(),
        "received_at": received_at.isoformat(), "observed_at": max(occurred_at, received_at).isoformat(),
        "elapsed_h": elapsed, "delay_h": delay,
    }


def _snapshot_status(snapshot: dict, plan_version: int, arrival_state: tuple[dict, bool], now: datetime) -> dict:
    status, reason, outcome = _outcome(snapshot, plan_version, arrival_state, now)
    return {**snapshot, "status": status, "reason_code": reason, "reason": _REASONS[reason], "outcome": outcome}


def _capture(shipment_id: str, scenario: dict, idempotency_key: str | None) -> dict:
    from app.operations_intelligence import forecast

    if operations._scenario_active(scenario):
        raise HTTPException(422, "В журнал можно сохранять только прогноз действующего плана без сценария.")
    if idempotency_key is not None and not idempotency_key.strip():
        raise HTTPException(422, "Idempotency-Key должен быть непустым")

    with store._conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        shipment = operations._shipment(conn, shipment_id)
        plan = operations._plan(conn, shipment)
        if idempotency_key is not None:
            existing = conn.execute(
                "SELECT payload_json FROM ml_snapshots WHERE shipment_id=? AND idempotency_key=?",
                (shipment_id, idempotency_key),
            ).fetchone()
            if existing:
                now = operations._now()
                arrivals = _arrival_state(shipment, plan, store.list_events(shipment_id, conn), now)
                return _snapshot_status(json.loads(existing["payload_json"]), plan["version"], arrivals, now)

        report = forecast(shipment_id, scenario, conn=conn)
        if report["status"] in {"blocked", "delivered"}:
            raise HTTPException(409, "Для снимка нужен доступный прогноз по оставшимся плечам.")
        if report["status"] != "ok":
            raise HTTPException(503, "ML-прогноз пока недоступен для сохранения.")

        remaining = report["scope"]["remaining_leg_ids"]
        baseline = report["baseline"]
        prediction = report["prediction"]
        if (not remaining or baseline["base_remaining_h"] is None
                or prediction["ml_delay_h"] is None or not prediction.get("model_hash")
                or not prediction.get("dataset_hash") or any(key not in prediction["features"] for key in ml.FEATURES)):
            raise HTTPException(503, "В прогнозе не хватает данных для сравнения с фактическим прибытием.")

        snapshot = {
            "id": uuid.uuid4().hex, "shipment_id": shipment_id,
            "forecast_at": operations._timestamp(report["generated_at"]).isoformat(timespec="microseconds"),
            "plan_version": plan["version"], "plan_leg_ids": plan["leg_ids"],
            "remaining_leg_ids": remaining, "baseline": baseline,
            "prediction": {key: prediction.get(key) for key in (
                "features", "ml_delay_h", "interval", "model_version", "model_hash", "dataset_hash",
                "dataset_kind", "selected_model", "reliability",
            )},
            "demo": data.get_shipment(shipment_id) is not None,
        }
        conn.execute(
            "INSERT INTO ml_snapshots (id,shipment_id,forecast_at,plan_version,payload_json,idempotency_key) "
            "VALUES (?,?,?,?,?,?)",
            (snapshot["id"], shipment_id, snapshot["forecast_at"], plan["version"],
             json.dumps(snapshot, ensure_ascii=False, allow_nan=False), idempotency_key),
        )
        now = operations._now()
        arrivals = _arrival_state(shipment, plan, store.list_events(shipment_id, conn), now)
        return _snapshot_status(snapshot, plan["version"], arrivals, now)


def _journal(shipment_id: str, limit: int) -> dict:
    counts = {"matched": 0, "pending": 0, "excluded": 0, "total": 0}
    errors, coverage, covered = [], 0, 0
    scoring_errors = []
    snapshots = []
    with store._conn() as conn:
        conn.execute("BEGIN")
        shipment = operations._shipment(conn, shipment_id)
        now = operations._now()
        plan = operations._plan(conn, shipment)
        arrivals = _arrival_state(shipment, plan, store.list_events(shipment_id, conn), now)
        rows = conn.execute(
            "SELECT payload_json FROM ml_snapshots WHERE shipment_id=? ORDER BY forecast_at DESC, rowid DESC",
            (shipment_id,),
        )
        for row in rows:
            snapshot = _snapshot_status(json.loads(row["payload_json"]), plan["version"], arrivals, now)
            counts[snapshot["status"]] += 1
            counts["total"] += 1
            if len(snapshots) < limit:
                snapshots.append(snapshot)
            if snapshot["status"] == "matched":
                prediction, actual = snapshot["prediction"], snapshot["outcome"]["delay_h"]
                errors.append(abs(prediction["ml_delay_h"] - actual))
                if (scoring := snapshot["baseline"].get("scoring_delay_h")) is not None:
                    scoring_errors.append(abs(scoring - actual))
                interval = prediction["interval"] or {}
                lower, upper = interval.get("lower_h"), interval.get("upper_h")
                if lower is not None and upper is not None and math.isfinite(lower) and math.isfinite(upper) and lower <= upper:
                    coverage += 1
                    covered += int(lower <= actual <= upper)

    return {
        "shipment_id": shipment_id, "snapshots": snapshots, "counts": counts,
        "metrics": {
            "unit": "snapshot", "interval_kind": "experimental",
            "n": len(errors), "mae_h": round(math.fsum(errors) / len(errors), 3) if errors else None,
            "scoring_n": len(scoring_errors),
            "scoring_mae_h": round(math.fsum(scoring_errors) / len(scoring_errors), 3) if scoring_errors else None,
            "interval_n": coverage, "interval_coverage": covered / coverage if coverage else None,
        },
        "observation_source": "operator-reported", "evaluated_at": now.isoformat(),
    }


_EXPORT_COLUMNS = (*ml.FEATURES, "delay_h", "forecast_at", "outcome_at", "observed_at", "shipment_id",
                   "snapshot_id", "plan_version", "base_remaining_h", "model_hash", "dataset_hash",
                   "event_id", "event_source", "event_received_at")


def _history_csv(shipment_id: str | None = None) -> Response:
    selected = []
    with store._conn() as conn:
        conn.execute("BEGIN")
        if shipment_id is not None:
            operations._shipment(conn, shipment_id)
        now = operations._now()
        condition = " WHERE shipment_id=?" if shipment_id is not None else ""
        rows = conn.execute(
            "SELECT shipment_id,payload_json FROM ml_snapshots" + condition +
            " ORDER BY shipment_id, forecast_at, rowid", (shipment_id,) if shipment_id is not None else (),
        )
        for key, group in groupby(rows, key=lambda row: row["shipment_id"]):
            shipment = store.get_shipment(key, conn)
            if shipment is None:
                continue  # Static demo shipments never contribute to history.
            plan = operations._plan(conn, shipment)
            arrivals = _arrival_state(shipment, plan, store.list_events(key, conn), now)
            for row in group:
                snapshot = json.loads(row["payload_json"])
                status, _, outcome = _outcome(snapshot, plan["version"], arrivals, now)
                if status != "matched":
                    continue
                prediction = snapshot["prediction"]
                selected.append({
                    **prediction["features"], "delay_h": outcome["delay_h"],
                    "forecast_at": snapshot["forecast_at"], "outcome_at": outcome["occurred_at"],
                    "observed_at": outcome["observed_at"], "shipment_id": key, "snapshot_id": snapshot["id"],
                    "plan_version": snapshot["plan_version"], "base_remaining_h": snapshot["baseline"]["base_remaining_h"],
                    "model_hash": prediction["model_hash"], "dataset_hash": prediction["dataset_hash"],
                    "event_id": outcome["event_id"], "event_source": "operator-reported:" + outcome["source"],
                    "event_received_at": outcome["received_at"],
                })
                break

    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, _EXPORT_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(sorted(selected, key=lambda row: (operations._timestamp(row["forecast_at"]), row["shipment_id"])))
    return Response(output.getvalue(), media_type="text/csv", headers={
        "Content-Disposition": 'attachment; filename="ml-shipment-history.csv"', "Cache-Control": "no-store",
    })


def create_router(guard_write) -> APIRouter:
    router = APIRouter(prefix="/api/operations", tags=["intelligence journal"])

    @router.post("/{shipment_id}/intelligence/snapshots", status_code=201, dependencies=[Depends(guard_write)])
    def capture(
        shipment_id: str,
        scenario: Annotated[dict, Depends(operations._scenario)],
        idempotency_key: Annotated[str | None, Header(min_length=1, max_length=100)] = None,
        body: Annotated[dict | None, Body()] = None,
    ):
        if body:
            raise HTTPException(422, "Снимок сохраняет текущий прогноз без параметров в теле запроса.")
        return _capture(shipment_id, scenario, idempotency_key)

    @router.get("/{shipment_id}/intelligence/snapshots")
    def journal(shipment_id: str, response: Response, limit: Annotated[int, Query(ge=1, le=200)] = 50):
        response.headers["Cache-Control"] = "no-store"
        return _journal(shipment_id, limit)

    @router.get("/{shipment_id}/intelligence/snapshots.csv")
    def shipment_history(shipment_id: str):
        return _history_csv(shipment_id)

    @router.get("/intelligence/snapshots.csv")
    def fleet_history():
        return _history_csv()

    return router
