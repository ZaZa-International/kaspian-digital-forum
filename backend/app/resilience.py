"""Scenario-only ferry connections and route resilience from the dispatcher's current plan."""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Annotated, Literal

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app import data, docking, operations, store, weather

SAMPLE_COUNT = 1024
SEED = 2718
STORM_HOLD_H = 48
CUSTOMS_SURGE_H = 12
_METRICS = ("remaining_h", "total_usd", "co2_kg")
_PROFILES = {"time": "remaining_h", "cost": "total_usd", "green": "co2_kg"}
_CONNECTION_REASONS = {
    "delivered": "Перевозка завершена.",
    "blocked": "Оставшийся план закрыт условиями сценария.",
    "land_only": "В оставшемся плане нет морских плеч.",
    "already_sailed": "Груз уже вышел на единственное оставшееся морское плечо. Стыковка пройдена.",
}


def _inputs(
    departure_in_h: Annotated[float, Query(ge=6, le=168)] = 24,
    cutoff_h: Annotated[float, Query(ge=0, le=24)] = 6,
    feeder_delay_h: Annotated[float, Query(ge=0, le=72)] = 0,
    customs_reduction_pct: Annotated[float, Query(ge=0, le=100)] = 0,
    profile: Literal["time", "cost", "green", "balanced"] = "balanced",
    plan_version: Annotated[int | None, Query(ge=1)] = None,
) -> dict:
    if cutoff_h > departure_in_h:
        raise HTTPException(422, "Погрузка должна закрываться между текущим моментом и отправлением")
    return {
        "departure_in_h": departure_in_h, "cutoff_h": cutoff_h,
        "feeder_delay_h": feeder_delay_h, "customs_reduction_pct": customs_reduction_pct,
        "profile": profile, "plan_version": plan_version,
    }


def _earliest_slot(ready_h, departure_in_h: float, cutoff_h: float, interval_h: float, closed_until_h: float):
    """Find the first permitted slot. A load ready at the cutoff is eligible."""
    from_readiness = np.ceil((ready_h + cutoff_h - departure_in_h) / interval_h - 1e-12)
    from_weather = math.ceil((closed_until_h - departure_in_h) / interval_h - 1e-12)
    return np.maximum(np.maximum(from_readiness, from_weather), 0).astype(int)


def _percentile(values: np.ndarray, level: float) -> float:
    # Inverted CDF preserves the distinct sailing slots in the sampled outcomes.
    return round(float(np.quantile(values, level, method="inverted_cdf")), 2)


def _point(ready_h: float, schedule: dict, closed_until_h: float) -> dict:
    departure = schedule["departure_in_h"]
    cutoff = schedule["cutoff_h"]
    interval = schedule["interval_h"]
    skipped = int(_earliest_slot(ready_h, departure, cutoff, interval, closed_until_h))
    catch = departure + skipped * interval
    return {
        "ready_h": ready_h, "margin_h": departure - cutoff - ready_h,
        "catch_in_h": catch, "misses_first": skipped > 0, "skipped_sailings": skipped,
    }


def _simulation(point_ready: float, sampled_ready: np.ndarray, downstream_h: float,
                schedule: dict, closed_until_h: float, now: datetime) -> tuple[dict, np.ndarray]:
    departure, cutoff, interval = (schedule[key] for key in ("departure_in_h", "cutoff_h", "interval_h"))
    skipped = _earliest_slot(sampled_ready, departure, cutoff, interval, closed_until_h)
    sail = departure + interval * skipped
    waits = sail - sampled_ready
    arrival = sail + downstream_h
    point = _point(point_ready, schedule, closed_until_h)
    report = {
        **{key: round(value, 3) if type(value) is float else value for key, value in point.items()},
        "catch_at": (now + timedelta(hours=point["catch_in_h"])).isoformat(),
        "wait_h": round(point["catch_in_h"] - point_ready, 2),
        "arrival_h": round(point["catch_in_h"] + downstream_h, 2),
        "p_miss": round(float(np.mean(skipped > 0)), 6),
        "wait_p50_h": _percentile(waits, 0.5), "wait_p90_h": _percentile(waits, 0.9),
        "arrival_p50_h": _percentile(arrival, 0.5), "arrival_p90_h": _percentile(arrival, 0.9),
        "sailing_distribution": [
            {"departure_in_h": round(departure + interval * int(k), 2),
             "frequency": round(int(count) / SAMPLE_COUNT, 6), "count": int(count)}
            for k, count in zip(*np.unique(skipped, return_counts=True))
        ],
    }
    return report, skipped


def _cliff(point_ready: float, schedule: dict, closed_until_h: float) -> dict:
    point = _point(point_ready, schedule, closed_until_h)
    plus_hour = _point(point_ready + 1, schedule, closed_until_h)
    # Use the first eligible *weather-permitted* slot for the exact boundary
    # example so a cancelled departure cannot create a fictional loading cliff.
    first_weather_slot = _point(0, schedule, closed_until_h)["catch_in_h"]
    cutoff_time = first_weather_slot - schedule["cutoff_h"]
    exact = _point(cutoff_time, schedule, closed_until_h)
    late = _point(cutoff_time + 1, schedule, closed_until_h)
    return {
        "one_hour_later_misses": plus_hour["skipped_sailings"] > point["skipped_sailings"],
        "jump_h": round(plus_hour["catch_in_h"] - point["catch_in_h"], 2),
        "one_hour_later_catch_in_h": plus_hour["catch_in_h"],
        "example": {
            "on_cutoff_ready_h": round(cutoff_time, 2), "one_hour_late_ready_h": round(cutoff_time + 1, 2),
            "on_cutoff_catch_in_h": exact["catch_in_h"], "one_hour_late_catch_in_h": late["catch_in_h"],
            "jump_h": round(late["catch_in_h"] - exact["catch_in_h"], 2),
        },
    }


def _cause(reference: dict, base: dict, change: dict, schedule: dict,
           input_delay: float, feeder: list[dict],
           reduced_queue_h: float, cliff: dict) -> dict:
    if change["catch_in_h"] < base["catch_in_h"]:
        return {"code": "customs_recovery", "label": "Сокращение очереди возвращает рейс",
                "detail": f"Сокращение расчётной очереди на {reduced_queue_h:g} ч даёт более ранний рейс в модели."}
    if base["catch_in_h"] > reference["catch_in_h"] and input_delay:
        return {"code": "feeder_delay" if feeder else "terminal_hold",
                "label": "Задержка подвоза" if feeder else "Задержка в терминале",
                "detail": f"Добавленные {input_delay:g} ч сдвигают готовность за время закрытия погрузки."}
    ready_catch = _point(change["ready_h"], schedule, 0)["catch_in_h"]
    if change["catch_in_h"] > ready_catch:
        return {"code": "weather_gate", "label": "Ограничение по волне",
                "detail": "Груз готов к погрузке. В сценарии выход парома откладывается до окончания погодного окна."}
    if change["misses_first"] and change["margin_h"] < 0:
        return {"code": "loading_cutoff", "label": "Закрытие погрузки",
                "detail": f"Груз готов на {abs(change['margin_h']):g} ч позже порога первого рейса."}
    if cliff["one_hour_later_misses"]:
        return {"code": "near_cutoff", "label": "Час меняет рейс",
                "detail": f"Дополнительный час переносит отправление на {cliff['jump_h']:g} ч в этом расписании."}
    return {"code": "slack", "label": "Резерв до погрузки",
            "detail": f"Расчётный запас до закрытия погрузки составляет {change['margin_h']:g} ч."}


def _connection(detail: dict, inputs: dict, ports: dict, now: datetime) -> dict:
    shipment, baseline = detail["shipment"], detail["baseline"]
    if shipment["progress_status"] == "delivered":
        status = "delivered"
    elif baseline["blocked"]:
        status = "blocked"
    else:
        legs = baseline["legs"]
        sea_index = next((i for i, leg in enumerate(legs) if leg["mode"] == "sea" and not leg["in_transit"]), None)
        if sea_index is not None:
            return _eligible_connection(legs, sea_index, inputs, ports, now)
        status = "already_sailed" if any(leg["mode"] == "sea" for leg in legs) else "land_only"
    return {"status": status, "reason": _CONNECTION_REASONS[status], "port_id": None, "port_name": None,
            "sea_leg_id": None, "feeder_leg_ids": [], "weather": None, "schedule": None,
            "reference": None, "baseline": None, "intervention": None, "impact": None,
            "feeder_impact": None, "cliff": None, "pivotal_cause": None}


def _impact(before: dict, after: dict, before_slots: np.ndarray, after_slots: np.ndarray) -> dict:
    return {
        "miss_probability_pp": round((after["p_miss"] - before["p_miss"]) * 100, 2),
        "arrival_p50_h": round(after["arrival_p50_h"] - before["arrival_p50_h"], 2),
        "arrival_p90_h": round(after["arrival_p90_h"] - before["arrival_p90_h"], 2),
        "catch_h": round(after["catch_in_h"] - before["catch_in_h"], 2),
        "samples_later_pct": round(float(np.mean(after_slots > before_slots)) * 100, 2),
        "samples_earlier_pct": round(float(np.mean(after_slots < before_slots)) * 100, 2),
    }


def _eligible_connection(legs: list[dict], index: int, inputs: dict, ports: dict, now: datetime) -> dict:
    ferry = legs[index]
    pre_sailing = legs[:index]
    feeder = [leg for leg in pre_sailing if leg["mode"] in {"rail", "road"}]
    port = ferry["from"]
    interval = float(ferry.get("sailing_interval_h", docking.DEFAULT_INTERVAL_H))
    departure, cutoff = inputs["departure_in_h"], inputs["cutoff_h"]
    first_cutoff = departure - cutoff
    schedule = {
        "departure_in_h": departure, "cutoff_h": cutoff, "loading_closes_in_h": first_cutoff,
        "interval_h": interval, "first_departure_at": (now + timedelta(hours=departure)).isoformat(),
        "loading_closes_at": (now + timedelta(hours=first_cutoff)).isoformat(),
        "source": "assumed_periodic_sailings",
    }
    conditions = ferry["weather"]
    closed_until_h = STORM_HOLD_H if conditions["wave_m"] >= 2 else 0
    # Destination-side queue on the projected ferry is part of the downstream
    # route. For readiness we separately take the origin terminal queue.
    origin_port = ports.get(port, {})
    queue_h = float(origin_port.get("customs_queue_h", 0))
    feeder_customs_h = sum(min(leg["effective_customs_h"], leg["remaining_h"]) for leg in feeder)
    reducible_h = queue_h + feeder_customs_h
    pre_sailing_h = sum(leg["remaining_h"] for leg in pre_sailing)
    movement_h = max(0.0, pre_sailing_h - feeder_customs_h)
    downstream_h = max(0.0, ferry["remaining_h"] - ferry["wait_h"] - closed_until_h) + sum(
        leg["remaining_h"] for leg in legs[index + 1:]
    )
    rng = np.random.default_rng(SEED)
    movement_scale = min(4.0, movement_h * 0.10)
    movement = np.maximum(0.0, movement_h + rng.triangular(-1, 0, 1, SAMPLE_COUNT) * movement_scale)
    queue = reducible_h * rng.triangular(0.6, 1.0, 1.4, SAMPLE_COUNT)
    delay = inputs["feeder_delay_h"]
    reduction = inputs["customs_reduction_pct"] / 100
    point_reference = movement_h + reducible_h
    point_after = movement_h + reducible_h * (1 - reduction) + delay
    reference, reference_slots = _simulation(point_reference, movement + queue, downstream_h,
                                             schedule, closed_until_h, now)
    base, base_slots = _simulation(point_reference + delay, movement + queue + delay, downstream_h,
                                   schedule, closed_until_h, now)
    change, change_slots = _simulation(point_after, movement + queue * (1 - reduction) + delay, downstream_h,
                                       schedule, closed_until_h, now)
    cliff = _cliff(point_after, schedule, closed_until_h)
    return {
        "status": "eligible", "reason": None, "port_id": port, "port_name": data.NODES[port]["name"],
        "sea_leg_id": ferry["id"], "feeder_leg_ids": [leg["id"] for leg in feeder],
        "already_at_port": not pre_sailing,
        "weather": {**conditions, "closed_until_h": closed_until_h,
                    "gate_source": "corridor_model_assumption"},
        "schedule": schedule, "reference": reference, "baseline": base, "intervention": change,
        "impact": _impact(base, change, base_slots, change_slots),
        "feeder_impact": _impact(reference, base, reference_slots, base_slots),
        "cliff": cliff, "pivotal_cause": _cause(reference, base, change, schedule, delay, feeder,
                                               round(reducible_h * reduction, 2), cliff),
        "readiness_components": {
            "movement_h": round(movement_h, 2), "feeder_customs_h": round(feeder_customs_h, 2),
            "origin_customs_h": queue_h, "origin_queue_source": origin_port.get("source", "demo" if origin_port else "none"),
            "origin_queue_role": "assumed_pre_loading_queue",
            "assumed_downstream_h": round(downstream_h, 2),
        },
        "simulation": {"n": SAMPLE_COUNT, "seed": SEED, "source": "scenario_simulation",
                       "common_random_draws": True, "p_miss_means": "simulated share that misses the first assumed sailing"},
    }


def _metric_row(projection: dict) -> dict:
    return {**{key: projection[key] for key in _METRICS}, "blocked": projection["blocked"]}


def _dominates(left: dict, right: dict) -> bool:
    return all(left[key] <= right[key] for key in _METRICS) and any(left[key] < right[key] for key in _METRICS)


def _policy(options: list[dict], profile: str, scenarios: list[dict]) -> dict:
    unblocked = [option for option in options if not option["blocked"]]
    candidates = unblocked if profile == "balanced" else [option for option in unblocked if option["pareto"]]
    texts = {
        "time": "Минимум оставшегося времени. При равенстве сравниваются стоимость и CO₂e.",
        "cost": "Минимум расчётной стоимости. При равенстве сравниваются время и CO₂e.",
        "green": "Минимум расчётного CO₂e. При равенстве сравниваются время и стоимость.",
        "balanced": (
            "Минимум максимального нормированного отставания от лучшего варианта по времени, стоимости и CO₂e "
            "в текущих и стрессовых условиях. Блокировка получает 2 балла при шкале 0–1 для доступных вариантов. "
            "При равенстве сравнивается среднее отставание."
        ),
    }
    if profile == "balanced":
        windows = [unblocked, *[
            [option["stress"][i] for option in unblocked] for i in range(len(scenarios))
        ]]
        ranges = []
        for window in windows:
            available = [row for row in window if not row["blocked"]]
            ranges.append({
                metric: (min(row[metric] for row in available), max(row[metric] for row in available))
                for metric in _METRICS
            } if available else {})
        for i, option in enumerate(unblocked):
            regrets = []
            for window, bounds in zip(windows, ranges):
                if window[i]["blocked"]:
                    regrets.extend([2.0] * len(_METRICS))
                    continue
                for metric, (low, high) in bounds.items():
                    regrets.append((window[i][metric] - low) / (high - low) if high > low else 0.0)
            option["balanced_score"] = round(max(regrets), 6)
            option["balanced_mean_regret"] = round(sum(regrets) / len(regrets), 6)

        def rank(option):
            return (option["balanced_score"], option["balanced_mean_regret"], option["conditional"],
                    option["remaining_h"], option["total_usd"], option["id"])
    else:
        first = _PROFILES[profile]
        keys = (first, *(key for key in _METRICS if key != first))

        def rank(option):
            return (*(option[key] for key in keys), option["conditional"], option["id"])
    selected = min(candidates, key=rank, default=None)
    return {
        "profile": profile, "selected_id": selected["id"] if selected else None,
        "options": options, "scenarios": scenarios, "policy": texts[profile],
        "metric_units": {"remaining_h": "h", "total_usd": "USD", "co2_kg": "kg CO2e"},
        "pareto_scope": "current operational projection, accepted continuation and available alternatives",
        "source": "operational_model_and_stress_scenarios",
    }


def _decision(detail: dict, shipment: dict, progress: dict, scenario: dict, ports: dict,
              now: datetime, profile: str) -> dict:
    scenarios = [
        {"id": "calm", "label": "Спокойное море", "source": "demo_weather_assumption"},
        {"id": "storm", "label": "Шторм", "source": "storm_scenario_assumption"},
        {"id": "customs_surge", "label": "Таможня +12 ч", "source": "customs_scenario_assumption"},
    ]
    if detail["shipment"]["progress_status"] == "delivered":
        return _policy([], profile, scenarios)
    neutral = {**scenario, "storm": False, "wave_m": None, "wind_ms": None}
    stress_conditions = [
        neutral, {**neutral, "storm": True},
        {**neutral, "customs_extra_h": scenario["customs_extra_h"] + CUSTOMS_SURGE_H},
    ]
    # Calm/surge deliberately use stable demo weather, while the current row
    # retains actual cached forecast inputs and operator-reported port states.
    reference_weather = {"status": "scenario", "segments": {}}
    current_legs = progress["remaining"]
    options = []
    choices = [("baseline", detail["baseline"], current_legs), *[
        (proposal["id"], proposal, proposal["legs"]) for proposal in detail["proposals"]
    ]]
    for key, projection, legs in choices:
        transfer = projection["transfer_usd"]
        stress = [{"id": row["id"], **_metric_row(operations._projection(
            legs, shipment, progress, detail["economics"], conditions, ports, reference_weather, now,
            transfer_usd=transfer,
        ))} for row, conditions in zip(scenarios, stress_conditions)]
        options.append({
            "id": key, "kind": "baseline" if key == "baseline" else "proposal",
            **operations._nodes(legs, detail["shipment"]["destination"]),
            "leg_ids": [leg["id"] for leg in legs], "conditional": any(leg.get("conditional") for leg in legs),
            **_metric_row(projection), "stress": stress, "pareto": False,
            "source": projection["source"],
        })
    valid = [option for option in options if not option["blocked"]]
    for option in valid:
        option["pareto"] = not any(_dominates(other, option) for other in valid)
    return _policy(options, profile, scenarios)


def analyze(shipment_id: str, scenario: dict, inputs: dict) -> dict:
    forecast = weather.cached_hourly_forecast()
    with store._conn() as conn:
        conn.execute("BEGIN")
        now = operations._now()
        shipment = operations._shipment(conn, shipment_id)
        ports = operations._port_state(conn)
        detail = operations._detail(conn, shipment, scenario, ports, forecast, now)
        plan, view = detail["plan"], detail["shipment"]
        if inputs["plan_version"] is not None and inputs["plan_version"] != plan["version"]:
            raise HTTPException(409, "Версия плана изменилась. Обновите перевозку и запустите сценарий заново.")
        progress = operations._progress(
            shipment, plan, [event for event in detail["events"] if event["source"] != "demo-simulation"], now,
        )
    connection = _connection(detail, inputs, ports, now)
    return {
        "shipment_id": shipment_id, "plan_version": plan["version"], "generated_at": now.isoformat(),
        "status": connection["status"],
        "scope": {key: view[key] for key in ("current_node", "current_leg_id", "progress_status")}
        | {"remaining_leg_ids": view["active_leg_ids"], "completed_leg_ids": view["completed_leg_ids"]},
        "inputs": {key: value for key, value in inputs.items() if key != "plan_version"},
        "connection": connection, "decision": _decision(detail, shipment, progress, scenario, ports, now, inputs["profile"]),
        "notice": (
            "Стыковка использует заданное условное расписание, а доля пропусков вычисляется по симуляции. "
            "Сравнение маршрутов использует оперативную модель с ожидаемым временем ожидания парома. "
            "Сценарий не меняет принятый план и не бронирует рейс."
        ),
    }


def create_router() -> APIRouter:
    router = APIRouter(prefix="/api/operations", tags=["resilience"])

    @router.get("/{shipment_id}/resilience")
    def resilience(shipment_id: str, response: Response,
                   scenario: Annotated[dict, Depends(operations._scenario)],
                   inputs: Annotated[dict, Depends(_inputs)]):
        response.headers["Cache-Control"] = "no-store"
        return analyze(shipment_id, scenario, inputs)

    return router
