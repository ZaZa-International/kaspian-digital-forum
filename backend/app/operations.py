"""Shipment progress, versioned route decisions and the dispatcher dashboard."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field, field_validator

from app import data, graph, model, operation_documents as documents, store, weather

HORIZON_HOURS = 72
_DEMO_ECONOMICS = {
    "SHP-1042": (50, 520, 2400, 430),
    "SHP-1043": (0, 1800, 7000, 1300),
    "SHP-1044": (0, 760, 2200, 580),
}


class Economics(BaseModel):
    deadline: datetime
    idle_cost_per_day: float = Field(ge=0, le=100_000_000, allow_inf_nan=False)
    late_penalty_per_day: float = Field(ge=0, le=100_000_000, allow_inf_nan=False)
    reroute_transfer_cost: float = Field(ge=0, le=100_000_000, allow_inf_nan=False)

    @field_validator("deadline")
    @classmethod
    def deadline_iso(cls, value: datetime) -> datetime:
        try:
            return _timestamp(value)
        except OverflowError as exc:
            raise ValueError("Срок доставки выходит за допустимый диапазон UTC") from exc


class AcceptProposal(BaseModel):
    proposal_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    version: int = Field(ge=1)


def _timestamp(value: datetime | str) -> datetime:
    t = datetime.fromisoformat(value) if isinstance(value, str) else value
    # Legacy seed and operator timestamps without an explicit offset use UTC.
    return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t.astimezone(timezone.utc)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _scenario(
    storm: bool = False,
    close: str = Query("", max_length=256),
    extra_customs_h: float = Query(0, ge=0, le=168),
    wave_m: float | None = Query(None, ge=0, le=15),
    wind_ms: float | None = Query(None, ge=0, le=60),
) -> dict:
    closed = sorted(set(node.strip() for node in close.split(",") if node.strip()))
    if unknown := set(closed) - data.NODES.keys():
        raise HTTPException(422, "Неизвестные узлы: " + ", ".join(sorted(unknown)))
    return {
        "storm": storm, "closed_nodes": closed, "customs_extra_h": extra_customs_h,
        "wave_m": wave_m, "wind_ms": wind_ms,
    }


def _scenario_active(scenario: dict) -> bool:
    return bool(
        scenario["storm"] or scenario["closed_nodes"] or scenario["customs_extra_h"]
        or scenario["wave_m"] is not None or scenario["wind_ms"] is not None
    )


def _shipment(conn, shipment_id: str) -> dict:
    shipment = data.get_shipment(shipment_id) or store.get_shipment(shipment_id, conn)
    if shipment is None:
        raise HTTPException(404, f"unknown shipment {shipment_id}")
    return shipment


def _economics(conn, shipment: dict) -> dict:
    row = conn.execute(
        "SELECT deadline,idle_cost_per_day,late_penalty_per_day,reroute_transfer_cost "
        "FROM operation_economics WHERE shipment_id=?", (shipment["id"],),
    ).fetchone()
    if row:
        return {**dict(row), "currency": "USD", "source": "operator"}
    defaults = _DEMO_ECONOMICS.get(shipment["id"])
    if defaults:
        hours, idle, late, transfer = defaults
        deadline = _timestamp(shipment["eta"]) + timedelta(hours=hours)
        source = "demo"
    else:
        deadline, idle, late, transfer = _timestamp(shipment["eta"]), 0, 0, 0
        source = "default"
    return {
        "currency": "USD", "deadline": deadline.isoformat(), "idle_cost_per_day": idle,
        "late_penalty_per_day": late, "reroute_transfer_cost": transfer, "source": source,
    }


def _nodes(legs: list[dict], empty_node: str) -> dict:
    nodes = [legs[0]["from"], *(leg["to"] for leg in legs)] if legs else [empty_node]
    return {"nodes": nodes, "node_names": [data.NODES[node]["name"] for node in nodes]}


def _plan(conn, shipment: dict, rows: list | None = None) -> dict:
    initial = data.get_route(shipment["route_id"])["legs"]
    if rows is None:
        row = conn.execute(
            "SELECT * FROM operation_plans WHERE shipment_id=? ORDER BY version DESC LIMIT 1",
            (shipment["id"],),
        ).fetchone()
    else:
        row = rows[0] if rows else None
    if row:
        indexed = {leg["id"]: leg for leg in data.GRAPH_EDGES}
        leg_ids = json.loads(row["leg_ids_json"])
        return {
            "version": row["version"], "leg_ids": leg_ids,
            "legs": [indexed[key] for key in leg_ids], "source": "operator",
            "completed_leg_ids": json.loads(row["completed_leg_ids_json"]),
            "transfer_usd": json.loads(row["decision_json"])["transfer_usd"],
        }
    return {
        "version": 1, "leg_ids": [leg["id"] for leg in initial], "legs": initial,
        "source": "demo" if shipment["id"] in _DEMO_ECONOMICS else "initial",
        "completed_leg_ids": [leg["id"] for leg in initial[:shipment["current_leg"]]],
        "transfer_usd": 0,
    }


def _event_time(event: dict) -> datetime | None:
    when = event["actual_ts"] or event["occurred_ts"]
    if when is None and event["planned_ts"] is None:
        when = event["received_ts"]
    if when:
        try:
            return _timestamp(when)
        except (ValueError, OverflowError):
            pass
    return None


def _effective_events(events: list[dict]) -> list[dict]:
    replaced = {event["correction_of"] for event in events if event.get("correction_of")}
    return [event for event in events if event["id"] not in replaced]


def _movement_time(movements: list[tuple], kind: str) -> datetime | None:
    return max(
        ((event["source"] != "demo-simulation", ts) for ts, event in movements
         if event["event_type"] == kind),
        default=(False, None),
    )[1]


def _progress(shipment: dict, plan: dict, events: list[dict], now: datetime) -> dict:
    by_leg: dict = {}
    for event in [*model.demo_events(shipment), *_effective_events(events)]:
        when = _event_time(event)
        if when is not None and when <= now and event["leg_id"]:
            by_leg.setdefault(event["leg_id"], []).append((when, event))
    arrivals: dict = {}
    departures: dict = {}
    completed = list(plan["completed_leg_ids"])
    for leg in plan["legs"]:
        leg_id = leg["id"]
        movements = by_leg.get(leg_id, [])
        start = _movement_time(movements, "departure")
        end = _movement_time(movements, "arrival")
        if start is not None:
            departures[leg_id] = start
        if end is not None and (start is None or end >= start):
            arrivals[leg_id] = end
        if leg_id in completed:
            continue
        if leg_id not in arrivals:
            break
        completed.append(leg_id)
    remaining = plan["legs"][len(completed):]
    next_leg = remaining[0] if remaining else None
    is_seed_motion = shipment.get("demo_in_transit") and plan["version"] == 1 and len(completed) == 1
    departure = departures.get(next_leg["id"]) if next_leg else None
    if is_seed_motion and departure is None:
        departure = now
    position = (
        "delivered" if next_leg is None else "in_transit" if departure is not None else "at_node"
    )
    node = plan["legs"][-1]["to"] if next_leg is None else next_leg["from"] if departure is None else None
    delays = {}
    cleared = set()
    for leg in remaining:
        observed = sorted(by_leg.get(leg["id"], []), key=lambda entry: entry[0], reverse=True)
        latest_delay = next((event["delay_h"] for _, event in observed if event.get("delay_h") is not None), 0)
        delays[leg["id"]] = latest_delay
        clearance = next((event["event_type"] for _, event in observed if event["event_type"] in {
            "customs_start", "customs_done"
        }), None)
        if clearance == "customs_done":
            cleared.add(leg["id"])
    return {
        "status": position, "node": node, "next_leg": next_leg, "departure": departure,
        "remaining": remaining, "completed_leg_ids": completed, "arrivals": arrivals,
        "delays": delays, "cleared": cleared,
    }


def _port_state(conn) -> dict:
    return {**data.PORTS, **store.get_port_updates(conn)}


def _sea_state(leg: dict, at: datetime, scenario: dict, forecast: dict) -> dict:
    segment = leg["sea"]
    if scenario["storm"]:
        return {**data.STORM, "source": "storm scenario"}
    forecast_hours = forecast["segments"].get(segment, {})
    point = forecast_hours.get(at.replace(minute=0, second=0, microsecond=0))
    if point is not None:
        return {**point, "source": "Open-Meteo forecast"}
    return {**data.WEATHER[segment], "source": "demo fallback"}


def _projection(
    legs: list[dict], shipment: dict, progress: dict, economics: dict, scenario: dict,
    ports: dict, forecast: dict, now: datetime, transfer_usd: float = 0,
) -> dict:
    cursor = now
    transport = co2 = idle = 0.0
    predicted = []
    blocked_leg = None
    for index, leg in enumerate(legs):
        active = index == 0 and progress["departure"] is not None
        port = ports.get(leg.get("port"), {})
        at_customs = leg.get("customs_h") or data.NODES[leg["to"]]["kind"] == "border"
        customs = port.get("customs_queue_h", leg.get("customs_h", 0)) if at_customs else 0
        customs_extra = scenario["customs_extra_h"] if at_customs else 0
        if leg["id"] in progress["cleared"]:
            customs = customs_extra = 0
        extra = {
            **scenario, "congestion": {key: value["congestion_pct"] for key, value in ports.items()},
            "leg_customs_h": customs, "customs_extra_h": customs_extra,
        }
        # Once the leg has departed its origin no longer gates this movement.
        if active:
            extra["closed_nodes"] = [key for key in extra["closed_nodes"] if key != leg["from"]]
        if leg["mode"] == "sea":
            at_sea = now if active else cursor + timedelta(hours=model._expected_wait_h(leg))
            extra["sea_state"] = _sea_state(leg, at_sea, scenario, forecast)
        try:
            prediction = model.predict_leg(leg, scenario["storm"], extra)
        except ValueError:
            blocked_leg = {"id": leg["id"], "from": leg["from"], "to": leg["to"], "at": cursor.isoformat()}
            break
        travel_h, idle_h = prediction["predicted_h"], prediction["idle_h"]
        reported_delay = progress["delays"].get(leg["id"], 0)
        if active:
            predeparture_h = prediction["wait_h"]
            if leg["mode"] == "sea" and prediction["weather"]["wave_m"] >= 2:
                predeparture_h += 48
            elapsed_h = max(0, (now - progress["departure"]).total_seconds() / 3600)
            travel_h = max(0, travel_h + reported_delay - predeparture_h - elapsed_h)
            idle_h = min(travel_h, max(0, idle_h + reported_delay - predeparture_h))
        else:
            travel_h += reported_delay
            idle_h += reported_delay
        ends = cursor + timedelta(hours=travel_h)
        predicted.append({
            **prediction, "starts_at": cursor.isoformat(), "ends_at": ends.isoformat(),
            "remaining_h": round(travel_h, 1), "idle_h": round(idle_h, 1),
            "reported_delay_h": reported_delay, "in_transit": active,
        })
        cursor = ends
        idle += idle_h
        # The transport on a departed leg is already committed.
        transport += 0 if active else model._leg_weight_cost(leg, shipment["weight_t"])
        co2 += model._leg_weight_co2(leg, shipment["weight_t"])
    if not legs:
        cursor = max(progress["arrivals"].values(), default=now)
    deadline = _timestamp(economics["deadline"])
    late = max(0.0, (cursor - deadline).total_seconds() / 3600)
    idle_usd = round(idle * economics["idle_cost_per_day"] / 24, 2)
    penalty = round(late * economics["late_penalty_per_day"] / 24, 2)
    return {
        "eta": None if blocked_leg else cursor.isoformat(),
        "remaining_h": None if blocked_leg else round(max(0, (cursor - now).total_seconds() / 3600), 1),
        "late_h": None if blocked_leg else round(late, 1), "transport_usd": None if blocked_leg else round(transport, 2),
        "idle_h": None if blocked_leg else round(idle, 1), "idle_usd": None if blocked_leg else idle_usd,
        "late_usd": None if blocked_leg else penalty, "transfer_usd": transfer_usd,
        "total_usd": None if blocked_leg else round(transport + idle_usd + penalty + transfer_usd, 2),
        "financial_risk_usd": None if blocked_leg else round(idle_usd + penalty, 2),
        "co2_kg": None if blocked_leg else round(co2, 1), "legs": predicted,
        "blocked": bool(blocked_leg), "blocked_leg": blocked_leg,
        "source": "scenario" if _scenario_active(scenario) else "model",
    }


def _proposal_id(
    shipment: dict, plan: dict, events: list[dict], legs: list[dict],
    economics: dict, scenario: dict, ports: dict, projection: dict, baseline: dict,
) -> str:
    inputs = [
        shipment["id"], plan["version"],
        [event["id"] for event in events if event["source"] != "demo-simulation"],
        [leg["id"] for leg in legs], economics, scenario, ports,
        [[leg.get("weather") for leg in result["legs"]] for result in (projection, baseline)],
    ]
    return hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()[:32]


def _alternatives(shipment, plan, progress, events, economics, scenario, ports, forecast, now, baseline) -> list[dict]:
    if progress["status"] != "at_node":
        return []
    start, goal = progress["node"], plan["legs"][-1]["to"]
    completed_nodes = {leg["from"] for leg in plan["legs"][:len(progress["completed_leg_ids"])]}
    excluded = completed_nodes | set(scenario["closed_nodes"])
    current_ids = [leg["id"] for leg in progress["remaining"]]
    proposals = []
    for legs in graph.available_paths(start, goal, excluded):
        leg_ids = [leg["id"] for leg in legs]
        if leg_ids == current_ids:
            continue
        projection = _projection(
            legs, shipment, progress, economics, scenario, ports, forecast, now,
            transfer_usd=economics["reroute_transfer_cost"],
        )
        proposals.append({
            **projection, **_nodes(legs, start), "id": _proposal_id(
                shipment, plan, events, legs, economics, scenario, ports, projection, baseline
            ),
            "version": plan["version"], "from_node": start, "to_node": goal, "leg_ids": leg_ids,
            "conditional": any(leg.get("conditional") for leg in legs),
            "delta_usd": None if baseline["blocked"] else round(projection["total_usd"] - baseline["total_usd"], 2),
            "delta_h": None if baseline["blocked"] else round(projection["remaining_h"] - baseline["remaining_h"], 1),
            "source": projection["source"],
        })
    return sorted(proposals, key=lambda proposal: (proposal["total_usd"], proposal["remaining_h"], proposal["id"]))


def _weather_horizon(forecast: dict, now: datetime, until: datetime) -> dict:
    segments = []
    for key, segment in data.SEA_SEGMENTS.items():
        hours = forecast["segments"].get(key, {})
        points = [(t, point) for t, point in sorted(hours.items()) if now <= t < until]
        risky = next(
            (t.isoformat() for t, p in points if p["wave_m"] >= 2 or p["wind_ms"] > 14), None
        )
        segments.append({
            "id": key, "name": segment["segment"], "forecast_hours": len(points),
            "peak_wave_m": max((p["wave_m"] for _, p in points), default=None),
            "peak_wind_ms": max((p["wind_ms"] for _, p in points), default=None),
            "first_risk_at": risky,
        })
    status = forecast["status"]
    available = sum(bool(seg["forecast_hours"]) for seg in segments)
    if available:
        status = "available" if available == len(segments) else "partial"
    return {
        "status": status, "source": "Open-Meteo",
        "url": "https://open-meteo.com/en/docs/marine-weather-api",
        "segments": segments,
    }


def _horizon(details: list[dict], scenario: dict, forecast: dict, now: datetime) -> dict:
    until = now + timedelta(hours=HORIZON_HOURS)
    impacts = []
    for entry in details:
        shipment = entry["shipment"]
        baseline = entry["baseline"]
        if shipment["progress_status"] == "delivered":
            continue
        risk = baseline["financial_risk_usd"]
        if baseline["blocked"]:
            at = _timestamp(baseline["blocked_leg"]["at"])
            if at < until:
                impacts.append({
                    "shipment_id": shipment["id"], "type": "blocked", "at": at.isoformat(),
                    "delay_h": None, "financial_risk_usd": None, "source": "scenario",
                    "leg_id": baseline["blocked_leg"]["id"],
                })
        deadline = _timestamp(shipment["deadline"])
        if baseline["late_h"] and deadline < until:
            # A missed commitment becomes an operational impact at its deadline,
            # even when projected arrival is beyond the dashboard horizon.
            impacts.append({
                "shipment_id": shipment["id"], "type": "late",
                "at": max(now, deadline).isoformat(),
                "delay_h": baseline["late_h"], "financial_risk_usd": risk,
                "source": baseline["source"], "leg_id": None,
            })
        for leg in baseline["legs"]:
            at = _timestamp(leg["starts_at"]) + timedelta(hours=leg["wait_h"] if not leg["in_transit"] else 0)
            if at >= until:
                break
            sea = leg.get("weather", {})
            delay_h = leg.get("reported_delay_h", 0)
            disruption = leg["mode"] == "sea" and not leg["in_transit"] and sea.get("wave_m", 0) >= 2
            if disruption or delay_h:
                impacts.append({
                    "shipment_id": shipment["id"], "type": "sea_disruption" if disruption else "reported_delay",
                    "at": at.isoformat(), "delay_h": max(48 if disruption else 0, delay_h),
                    "financial_risk_usd": risk, "source": sea["source"] if disruption else "operator event",
                    "leg_id": leg["id"],
                })
    return {
        "hours": HORIZON_HOURS, "as_of": now.isoformat(), "until": until.isoformat(),
        "scenario_label": (
            "Шторм · сценарий" if scenario["storm"] else "Пользовательский сценарий"
            if _scenario_active(scenario) else "Модель текущего плана"
        ),
        "impacts": sorted(impacts, key=lambda item: (item["at"], item["shipment_id"], item["type"])),
        "weather": _weather_horizon(forecast, now, until),
    }


def _projected_plan_legs(plan: dict, baseline: dict) -> list[dict]:
    projected = {leg["id"]: leg for leg in baseline["legs"]}
    return [projected.get(leg["id"], leg) for leg in plan["legs"]]


def _detail(conn, shipment, scenario, ports, forecast, now, include_proposals: bool = True) -> dict:
    shipment_id = shipment["id"]
    if include_proposals:
        rows = list(conn.execute(
            "SELECT * FROM operation_plans WHERE shipment_id=? ORDER BY version DESC", (shipment_id,)
        ))
    else:
        rows = None
    plan = _plan(conn, shipment, rows)
    events = [event for event in store.list_events(shipment_id, conn) if event["source"] != "demo-simulation"]
    progress = _progress(shipment, plan, events, now)
    economics = _economics(conn, shipment)
    baseline = _projection(progress["remaining"], shipment, progress, economics, scenario, ports, forecast, now)
    goal = plan["legs"][-1]["to"]
    passport = documents.passport(conn, shipment, progress["remaining"], goal)
    current_leg = progress["next_leg"]
    status = {"at_node": "в узле", "in_transit": "в пути", "delivered": "доставлен"}[progress["status"]]
    view = {
        **{key: shipment[key] for key in ("id", "name", "cargo", "weight_t", "route_id")},
        "status": status,
        "current_leg": len(progress["completed_leg_ids"]),
        "plan_version": plan["version"],
        "current_node": progress["node"],
        "current_leg_id": current_leg["id"] if current_leg else None,
        "progress_status": progress["status"],
        "completed_leg_ids": progress["completed_leg_ids"],
        "active_leg_ids": [leg["id"] for leg in progress["remaining"]],
        "deadline": economics["deadline"], "eta": baseline["eta"], "late_h": baseline["late_h"],
        "financial_risk_usd": baseline["financial_risk_usd"], "document_status": passport["status"],
        "blocked": baseline["blocked"], "destination": goal,
        "source": (
            "operator" if any(event["source"] != "demo-simulation" for event in events)
            else "demo" if shipment_id in _DEMO_ECONOMICS else "initial"
        ),
    }
    proposals = _alternatives(
        shipment, plan, progress, events, economics, scenario, ports, forecast, now, baseline
    ) if include_proposals else []
    return {
        "shipment": view,
        "plan": {
            **plan, **_nodes(plan["legs"], goal),
            "legs": _projected_plan_legs(plan, baseline),
            "completed_leg_ids": progress["completed_leg_ids"],
        },
        "economics": economics, "baseline": baseline, "proposals": proposals, "documents": passport,
        "decisions": [json.loads(row["decision_json"]) for row in rows or ()],
        "events": [
            {**event, "time": (_event_time(event) or _timestamp(event["received_ts"])).isoformat()}
            for event in [*model.demo_events(shipment), *events]
        ],
    }


def _dashboard(scenario: dict) -> dict:
    now, forecast = _now(), weather.cached_hourly_forecast()
    with store._conn() as conn:
        conn.execute("BEGIN")
        ports = _port_state(conn)
        shipments = [
            *data.SHIPMENTS,
            *(store._shipment_dict(row) for row in conn.execute("SELECT * FROM shipments ORDER BY id")),
        ]
        details = [_detail(conn, shipment, scenario, ports, forecast, now, False) for shipment in shipments]
    rows = [entry["shipment"] for entry in details]
    return {
        "shipments": rows,
        "summary": {
            "shipment_count": len(rows),
            "late_count": sum(bool(s["late_h"]) for s in rows if s["progress_status"] != "delivered"),
            "blocked_count": sum(s["blocked"] for s in rows),
            "financial_risk_usd": round(sum(
                s["financial_risk_usd"] or 0 for s in rows if s["progress_status"] != "delivered"
            ), 2),
            "documents_blocked_count": sum(s["document_status"] in {"missing", "mismatch"} for s in rows),
        },
        "horizon": _horizon(details, scenario, forecast, now),
    }


def _accepted_payload(row: dict) -> dict:
    decision = json.loads(row["decision_json"])
    leg_ids = json.loads(row["leg_ids_json"])
    legs = {leg["id"]: leg for leg in data.GRAPH_EDGES}
    path = [legs[key] for key in leg_ids]
    return {
        "decision": decision,
        "plan": {
            "version": row["version"], "leg_ids": leg_ids, "legs": path, "source": "operator",
            "transfer_usd": decision["transfer_usd"],
            "completed_leg_ids": json.loads(row["completed_leg_ids_json"]),
            **_nodes(path, decision["to_node"]),
        },
    }


def _accept(shipment_id: str, body: AcceptProposal, scenario: dict, idempotency_key: str) -> dict:
    forecast = weather.cached_hourly_forecast()
    with store._conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        now = _now()
        shipment = _shipment(conn, shipment_id)
        saved = conn.execute(
            "SELECT * FROM operation_plans WHERE shipment_id=? AND idempotency_key=?",
            (shipment_id, idempotency_key),
        ).fetchone()
        if saved:
            prior = _accepted_payload(saved)
            if (
                prior["decision"]["proposal_id"] != body.proposal_id
                or prior["decision"]["previous_version"] != body.version
                or prior["decision"]["scenario"] != scenario
            ):
                raise HTTPException(409, "Ключ запроса уже использован для другого решения")
            return prior
        detail = _detail(conn, shipment, scenario, _port_state(conn), forecast, now)
        plan, view = detail["plan"], detail["shipment"]
        if plan["version"] != body.version or view["progress_status"] != "at_node":
            raise HTTPException(409, "План или положение груза изменились. Обновите варианты")
        proposal = next((p for p in detail["proposals"] if p["id"] == body.proposal_id), None)
        if proposal is None:
            raise HTTPException(409, "Вариант устарел. Обновите расчёт")
        completed = plan["completed_leg_ids"]
        accepted_ids = [*completed, *proposal["leg_ids"]]
        version = plan["version"] + 1
        decision = {
            "id": uuid.uuid4().hex, "event_type": "route_plan_accepted", "ts": now.isoformat(),
            "shipment_id": shipment_id, "previous_version": plan["version"], "version": version,
            "proposal_id": body.proposal_id, "from_node": proposal["from_node"],
            "to_node": proposal["to_node"], "leg_ids": accepted_ids,
            "completed_leg_ids": completed, "scenario": scenario, "source": "operator",
            "eta": proposal["eta"], "total_usd": proposal["total_usd"], "delta_usd": proposal["delta_usd"],
            "remaining_h": proposal["remaining_h"], "delta_h": proposal["delta_h"],
            "transfer_usd": proposal["transfer_usd"],
            "conditional": proposal["conditional"],
        }
        row = {
            "shipment_id": shipment_id, "version": version,
            "leg_ids_json": json.dumps(accepted_ids),
            "completed_leg_ids_json": json.dumps(completed),
            "decision_json": json.dumps(decision, ensure_ascii=False),
            "idempotency_key": idempotency_key,
        }
        conn.execute(
            "INSERT INTO operation_plans (shipment_id,version,leg_ids_json,completed_leg_ids_json,"
            "decision_json,idempotency_key) VALUES (:shipment_id,:version,:leg_ids_json,"
            ":completed_leg_ids_json,:decision_json,:idempotency_key)", row,
        )
        store.add_event(
            shipment_id=shipment_id, event_type="route_plan_accepted", occurred_ts=now.isoformat(),
            source="operations-decision", note=f"Принят план v{version}", event_id=decision["id"], conn=conn,
        )
        return _accepted_payload(row)


def record_event(shipment_id: str, fields: dict) -> dict:
    """Append an operator fact against the current plan under the same write lock as acceptance."""
    with store._conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        shipment = _shipment(conn, shipment_id)
        plan = _plan(conn, shipment)
        events = store.list_events(shipment_id, conn)
        events = [event for event in events if event["source"] != "demo-simulation"]
        now = _now()
        progress = _progress(shipment, plan, events, now)
        leg_id, kind = fields["leg_id"], fields["event_type"]
        active = progress["next_leg"]
        if fields.get("delay_h") is not None and kind != "delay":
            raise HTTPException(422, "Часы задержки допустимы для события delay")
        if fields["source"] in {"demo-simulation", "operations-decision"}:
            raise HTTPException(422, "Указанный источник зарезервирован")
        target = None
        if correction_id := fields.get("correction_of"):
            target = next((e for e in _effective_events(events) if e["id"] == correction_id), None)
            if not target or target["source"] == "operations-decision" or target["event_type"] != kind or target["leg_id"] != leg_id:
                raise HTTPException(409, "Исправление должно ссылаться на действующее событие того же этапа и вида")
        if leg_id is None:
            location = fields.get("location")
            if location and kind in {"arrival", "departure"}:
                completed = plan["legs"][:len(progress["completed_leg_ids"])]
                candidates = [active, *reversed(completed)] if active else list(reversed(completed))
                for leg in candidates:
                    node = leg["to" if kind == "arrival" else "from"]
                    if location.strip().casefold() in {node.casefold(), data.NODES[node]["name"].casefold()}:
                        fields["leg_id"] = leg_id = leg["id"]
                        break
        if leg_id and leg_id not in plan["leg_ids"]:
            raise HTTPException(409, "Этап отсутствует в действующем плане")
        if not leg_id and (kind in {"departure", "arrival"} or kind == "delay" and fields.get("delay_h") is not None):
            raise HTTPException(422, "Укажите этап события")
        when = _event_time({**fields, "received_ts": now.isoformat()})
        if correction_id and leg_id in progress["completed_leg_ids"] and kind in {"arrival", "departure"}:
            simulated = {
                **fields, "id": "candidate-correction", "received_ts": now.isoformat(),
            }
            after = _progress(shipment, plan, [*events, simulated], now)
            if not set(progress["completed_leg_ids"]).issubset(after["completed_leg_ids"]):
                raise HTTPException(409, "Исправление не может вернуть груз на завершённый этап")
        movement = kind in {"departure", "arrival"} and when is not None
        if not movement and target and kind == "departure" and _event_time(target) is not None:
            raise HTTPException(409, "Для исправления фактического отправления укажите фактическое время")
        if movement and when > now:
            raise HTTPException(422, "Фактическое время события находится в будущем")
        if movement:
            leg = plan["legs"][plan["leg_ids"].index(leg_id)]
            node = leg["to" if kind == "arrival" else "from"]
            if fields["location"] and fields["location"].strip().casefold() not in {
                node.casefold(), data.NODES[node]["name"].casefold(),
            }:
                raise HTTPException(422, "Указанное место не соответствует этапу")
            if fields["mode"] and fields["mode"] != leg["mode"]:
                raise HTTPException(422, "Вид транспорта не соответствует этапу")
            fields["location"] = fields["location"] or data.NODES[node]["name"]
            fields["mode"] = fields["mode"] or leg["mode"]
            if leg_id not in progress["completed_leg_ids"] and (active is None or active["id"] != leg_id):
                raise HTTPException(409, "Событие относится к этапу, до которого груз ещё не дошёл")
            leg_index = plan["leg_ids"].index(leg_id)
            if leg_index:
                previous = progress["arrivals"].get(plan["leg_ids"][leg_index - 1])
                if previous is not None and when < previous:
                    raise HTTPException(422, "Событие не может предшествовать прибытию на предыдущий узел")
            if kind == "arrival" and progress["departure"] is not None and active and active["id"] == leg_id:
                if when < progress["departure"]:
                    raise HTTPException(422, "Прибытие не может предшествовать отправлению")
            if kind == "departure" and active and active["id"] == leg_id and progress["departure"] is not None:
                reported = any(
                    e["event_type"] == "departure" and e["leg_id"] == leg_id and _event_time(e) is not None
                    for e in _effective_events(events)
                )
                if not correction_id and reported:
                    raise HTTPException(409, "Отправление уже зафиксировано. Используйте исправление")
            if leg_id in progress["completed_leg_ids"] and kind == "departure" and not correction_id:
                raise HTTPException(409, "Этап уже завершён")
        return store.add_event(shipment_id=shipment_id, conn=conn, **fields)


def create_router(guard_write) -> APIRouter:
    router = APIRouter(prefix="/api/operations", tags=["operations"])
    writes = [Depends(guard_write)]

    @router.get("")
    def dashboard(scenario: Annotated[dict, Depends(_scenario)]):
        return _dashboard(scenario)

    @router.get("/{shipment_id}")
    def shipment_detail(shipment_id: str, scenario: Annotated[dict, Depends(_scenario)]):
        now, forecast = _now(), weather.cached_hourly_forecast()
        with store._conn() as conn:
            conn.execute("BEGIN")
            result = _detail(conn, _shipment(conn, shipment_id), scenario, _port_state(conn), forecast, now)
        return {**result, "horizon": _horizon([result], scenario, forecast, now)}

    @router.post("/{shipment_id}/accept", dependencies=writes)
    def accept(
        shipment_id: str, body: AcceptProposal, scenario: Annotated[dict, Depends(_scenario)],
        idempotency_key: Annotated[str, Header(min_length=1, max_length=100)],
    ):
        if not idempotency_key.strip():
            raise HTTPException(422, "Idempotency-Key должен быть непустым")
        return _accept(shipment_id, body, scenario, idempotency_key)

    @router.put("/{shipment_id}/economics", dependencies=writes)
    def update_economics(shipment_id: str, body: Economics):
        values = body.model_dump(mode="json")
        values["deadline"] = body.deadline.isoformat()
        with store._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            _shipment(conn, shipment_id)
            conn.execute(
                "INSERT INTO operation_economics (shipment_id,deadline,idle_cost_per_day,"
                "late_penalty_per_day,reroute_transfer_cost) VALUES (:shipment_id,:deadline,"
                ":idle_cost_per_day,:late_penalty_per_day,:reroute_transfer_cost) "
                "ON CONFLICT(shipment_id) DO UPDATE SET deadline=excluded.deadline, "
                "idle_cost_per_day=excluded.idle_cost_per_day, late_penalty_per_day=excluded.late_penalty_per_day, "
                "reroute_transfer_cost=excluded.reroute_transfer_cost",
                {"shipment_id": shipment_id, **values},
            )
        return {**values, "currency": "USD", "source": "operator"}

    @router.post("/{shipment_id}/documents", dependencies=writes, status_code=201)
    def upload_document(
        shipment_id: str, kind: Annotated[str, Form(max_length=32)], file: Annotated[UploadFile, File()],
    ):
        with store._conn() as conn:
            _shipment(conn, shipment_id)
        metadata, content = documents.prepare_upload(kind, file)
        with store._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            shipment = _shipment(conn, shipment_id)
            plan = _plan(conn, shipment)
            progress = _progress(shipment, plan, store.list_events(shipment_id, conn), _now())
            saved = documents.save_upload(conn, shipment_id, metadata, content)
            passport = documents.passport(conn, shipment, progress["remaining"], plan["legs"][-1]["to"])
        return {"file": next(f for f in passport["files"] if f["id"] == saved["id"]), "documents": passport}

    return router
