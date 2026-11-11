"""Explainable delay model + CO2/cost estimates + pre-clearance ledger.

This is a transparent scoring model with named rules, not a trained
ML model. Every predicted hour carries its reasons, so the demo can
show *why* a leg is late. A trained model would plug in here behind
the same `forecast_route` signature.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from app import data, weather

MODE_RU = {"rail": "ж/д", "sea": "море", "road": "авто"}


def _weather(sea: str, storm: bool) -> dict:
    return weather.current_sea_state(sea, storm)


def predict_leg(leg: dict, storm: bool) -> dict:
    """Delay hours for one leg, with human-readable reasons."""
    delay = 0.0
    reasons: list[str] = []
    mode = leg["mode"]

    if leg.get("port"):
        p = data.PORTS.get(leg["port"])
        if p and p["congestion_pct"] >= 60:
            wait = round(p["congestion_pct"] * 0.12, 1)
            delay += wait
            reasons.append(
                f"загруженность порта {data.NODES[leg['port']]['name']} "
                f"{p['congestion_pct']}% → ожидание ~{wait} ч"
            )

    if mode == "sea":
        w = _weather(leg["sea"], storm)
        wave_delay = round(w["wave_m"] * 3, 1)
        delay += wave_delay
        reasons.append(f"волна {w['wave_m']} м → +{wave_delay} ч хода")
        if w["wind_ms"] > 14:
            delay += 6
            reasons.append(f"ветер {w['wind_ms']} м/с → +6 ч")
        if w["wave_m"] >= 2.0:
            # No fixed timetable on the Caspian: ferries sail when loaded
            # and weather allows; waits of 1-2 days are normal, up to a
            # week at congestion. We charge a conservative +48h.
            delay += 48
            reasons.append(
                "волна ≥ 2 м — паром не выходит, следующий рейс не раньше "
                "чем через ~48 ч (расписания у каспийских паромов нет)"
            )
    elif mode == "rail":
        delay += 1.5
        reasons.append("плановый резерв ж/д +1,5 ч")

    if leg.get("customs_h"):
        delay += leg["customs_h"]
        reasons.append(f"таможенная очередь ~{leg['customs_h']} ч")

    delay = round(delay, 1)
    risk = "high" if delay >= 12 else "medium" if delay >= 5 else "low"
    return {**leg, "predicted_h": round(leg["base_h"] + delay, 1),
            "delay_h": delay, "reasons": reasons, "risk": risk}


def forecast_route(route: dict, weight_t: float, storm: bool = False) -> dict:
    legs = [predict_leg(leg, storm) for leg in route["legs"]]
    total_base = sum(leg["base_h"] for leg in legs)
    total_pred = round(sum(leg["predicted_h"] for leg in legs), 1)
    delay = round(total_pred - total_base, 1)
    bottleneck = max(legs, key=lambda leg: leg["delay_h"])
    # Delay probability: saturating curve. In a storm, sea routes get a
    # high floor — land routes (no sea legs) keep their own probability.
    prob = min(0.95, delay / (delay + 18)) if delay > 0 else 0.05
    if storm and any(leg["mode"] == "sea" for leg in legs):
        prob = max(prob, 0.85)
    co2_kg = round(sum(
        leg["distance_km"] * weight_t * data.CO2_G_PER_TKM[leg["mode"]] / 1000
        for leg in legs
    ))
    cost_usd = round(sum(
        leg["distance_km"] * weight_t * data.COST_USD_PER_TKM[leg["mode"]]
        for leg in legs
    ))
    if bottleneck["delay_h"] >= 12:
        rec = (f"Узкое место — {MODE_RU[bottleneck['mode']]} "
               f"{data.NODES[bottleneck['from']]['name']} → "
               f"{data.NODES[bottleneck['to']]['name']}. "
               f"Рассмотрите альтернативный маршрут до отправки.")
    elif delay >= 8:
        rec = "Задержка значительная: сдвиньте стыковку с паромом и предупредите получателя."
    else:
        rec = "Маршрут в графике: риски в пределах резерва."
    return {
        "route_id": route["id"], "route_name": route["name"],
        "storm": storm, "legs": legs,
        "total_base_h": total_base, "total_predicted_h": total_pred,
        "delay_h": delay, "delay_probability": round(prob, 2),
        "bottleneck_leg_id": bottleneck["id"],
        "co2_kg": co2_kg, "cost_usd": cost_usd,
        "recommendation": rec,
    }


def compare_routes(weight_t: float, storm: bool = False) -> list[dict]:
    out = []
    for route in data.ROUTES:
        fc = forecast_route(route, weight_t, storm)
        out.append({
            "route_id": fc["route_id"], "route_name": fc["route_name"],
            "via": route["via"],
            "total_predicted_h": fc["total_predicted_h"],
            "delay_h": fc["delay_h"],
            "delay_probability": fc["delay_probability"],
            "co2_kg": fc["co2_kg"], "cost_usd": fc["cost_usd"],
            "bottleneck_leg_id": fc["bottleneck_leg_id"],
        })
    return out


# --- Pre-clearance ledger: append-only hash chain (simplified DLT demo).
# In-memory on purpose: the prototype shows the *mechanics* (hash link
# + tamper-evidence), not a production ledger.
LEDGER: list[dict] = []


def ledger_last_hash() -> str:
    return LEDGER[-1]["hash"] if LEDGER else "0" * 64


def submit_clearance(shipment_id: str, docs: dict[str, str]) -> dict:
    shipment = data.get_shipment(shipment_id)
    if shipment is None:
        raise ValueError(f"unknown shipment {shipment_id}")
    required = list(shipment["docs"].keys())
    missing = [d for d in required if docs.get(d) != "verified"]
    doc_hash = hashlib.sha256(
        json.dumps({"shipment": shipment_id, "docs": docs},
                   sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    prev = ledger_last_hash()
    decision = "pre-cleared" if not missing else "needs_documents"
    payload = f"{prev}|{shipment_id}|{decision}|{doc_hash}"
    entry = {
        "seq": len(LEDGER) + 1,
        "shipment_id": shipment_id,
        "decision": decision,
        "missing": missing,
        "doc_hash": doc_hash,
        "prev_hash": prev,
        "hash": hashlib.sha256(payload.encode()).hexdigest(),
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    LEDGER.append(entry)
    return entry


def verify_ledger() -> bool:
    prev = "0" * 64
    for e in LEDGER:
        if e["prev_hash"] != prev:
            return False
        payload = f"{e['prev_hash']}|{e['shipment_id']}|{e['decision']}|{e['doc_hash']}"
        if hashlib.sha256(payload.encode()).hexdigest() != e["hash"]:
            return False
        prev = e["hash"]
    return True
