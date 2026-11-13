"""ML comparison over the *remaining accepted plan*, using one operations snapshot.

The operational ETA remains rule-based. Experimental ML estimates cannot silently
change route acceptance, price, or an operator deadline. Reading this endpoint
does not save a training sample; the journal has an explicit write action.
"""

from __future__ import annotations

from datetime import timedelta

from app import ml, operations, store, weather


def _arrival(prediction: dict | None, projection: dict, deadline: str, now) -> dict:
    empty = {
        "estimate": None, "earliest": None, "latest": None,
        "delay_budget_h": None, "deadline_position": "unavailable", "usable": False,
        "note": "ML-интервал недоступен; используйте оперативный расчёт.",
    }
    if not prediction or prediction.get("ml_delay_h") is None or projection["blocked"]:
        return empty
    base = projection["base_remaining_h"]
    if base is None:
        return empty
    deadline_dt = operations._timestamp(deadline)
    budget = (deadline_dt - now).total_seconds() / 3600 - base
    point = now + timedelta(hours=base + prediction["ml_delay_h"])
    interval = prediction.get("interval") or {}
    lower, upper = interval.get("lower_h"), interval.get("upper_h")
    result = {**empty, "estimate": point.isoformat(), "delay_budget_h": round(budget, 1)}
    if lower is None or upper is None:
        return result
    earliest, latest = (now + timedelta(hours=base + h) for h in (lower, upper))
    position = (
        "before_interval" if deadline_dt < earliest
        else "after_interval" if deadline_dt >= latest
        else "within_interval"
    )
    return {
        **result, "earliest": earliest.isoformat(), "latest": latest.isoformat(),
        "deadline_position": position,
        "usable": bool(interval.get("usable")) and prediction.get("reliability", {}).get("preferred_source") == "ml",
        "note": (
            "Окно получено прибавлением ML-задержки к оставшемуся базовому времени. "
            "Это экспериментальная оценка, не вероятность выполнения срока. "
            "Рабочее ETA и принятие маршрута используют оперативный расчёт."
        ),
    }


def _prediction(projection: dict, shipment: dict, route_id: str, scenario: dict, trained: dict) -> dict | None:
    if projection["blocked"] or not projection["legs"]:
        return None
    route = {"id": route_id, "name": "Оставшийся путь", "legs": projection["legs"]}
    delay = max(0, projection["remaining_h"] - projection["base_remaining_h"])
    return ml.predict_delay(
        route, shipment["weight_t"], scenario["storm"], scenario,
        scoring_delay_h=round(delay, 1), trained=trained,
    )


def forecast(shipment_id: str, scenario: dict, *, conn=None) -> dict:
    """Supply conn to freeze a journal entry in its existing transaction."""
    scenario = {
        "storm": False, "closed_nodes": [], "customs_extra_h": 0,
        "wave_m": None, "wind_ms": None, **scenario,
    }
    now = operations._now()
    hourly = weather.cached_hourly_forecast()
    if conn is None:
        with store._conn() as connection:
            connection.execute("BEGIN")
            detail = operations._detail(
                connection, operations._shipment(connection, shipment_id), scenario,
                operations._port_state(connection), hourly, now,
            )
    else:
        detail = operations._detail(
            conn, operations._shipment(conn, shipment_id), scenario,
            operations._port_state(conn), hourly, now,
        )
    shipment, plan, baseline = detail["shipment"], detail["plan"], detail["baseline"]
    # Pin the model revision for the entire comparison, even if a history
    # file is replaced while this request is evaluating alternatives.
    trained = ml.get_trained()
    prediction = _prediction(baseline, shipment, f"{shipment_id}:v{plan['version']}", scenario, trained)
    alternatives = []
    for proposal in detail["proposals"][:5]:
        alt_prediction = _prediction(proposal, shipment, proposal["id"], scenario, trained)
        alternatives.append({
            **{key: proposal[key] for key in (
                "id", "node_names", "remaining_h", "base_remaining_h", "total_usd",
                "delta_h", "delta_usd", "conditional",
            )},
            "prediction": alt_prediction,
            "arrival": _arrival(alt_prediction, proposal, shipment["deadline"], now),
        })
    info = ml.info(trained)
    status = (
        "delivered" if shipment["progress_status"] == "delivered"
        else "blocked" if baseline["blocked"]
        else "unavailable" if not prediction or prediction.get("status") == "unavailable"
        else "ok"
    )
    return {
        "status": status, "shipment_id": shipment_id, "plan_version": plan["version"],
        "generated_at": now.isoformat(), "scenario": scenario,
        "what_if": operations._scenario_active(scenario),
        "scope": {
            "label": "Оставшийся путь", "remaining_leg_ids": shipment["active_leg_ids"],
            "in_transit": shipment["progress_status"] == "in_transit",
            "completed_leg_ids": shipment["completed_leg_ids"],
            "shipment_source": shipment["source"],
        },
        "baseline": {
            **{key: baseline[key] for key in (
                "eta", "remaining_h", "base_remaining_h", "late_h", "total_usd", "blocked",
            )},
            "scoring_delay_h": None if baseline["blocked"] else round(max(
                0, (baseline["remaining_h"] or 0) - (baseline["base_remaining_h"] or 0)
            ), 1),
            "deadline": shipment["deadline"],
        },
        "prediction": prediction,
        "arrival": _arrival(prediction, baseline, shipment["deadline"], now),
        "alternatives": alternatives, "model": info,
        "notice": (
            "ML сравнивает только непройденные этапы текущей версии плана. "
            "Изменения условий здесь — предварительный просмотр. "
            "Оперативное ETA, стоимость и выбор плана не меняются автоматически."
        ),
    }
