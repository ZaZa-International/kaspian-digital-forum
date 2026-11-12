"""Explainable delay model + CO2/cost (ISO 14083 style) + pre-clearance
ledger (persistent hash-chain) + Monte Carlo ETA + event timeline.

This is a transparent scoring model with named rules, not a trained
ML model. Every predicted hour carries its reasons, so the demo can
show *why* a leg is late. The trained models live in ml.py behind
the same feature contract.

Iteration 1 upgrades (see docs/ENGINEERING_LOG.md):
- per-leg CO2/cost breakdown with method + data-quality labels;
- Monte Carlo ETA: P50/P90/P95 from real weather archive bootstrap;
- scenario overrides (closed nodes, congestion, customs, wave/wind);
- persistent ledger (JSON file, survives restart);
- shipment event timeline (digital-twin lifecycle);
- exceptions feed with recommended actions (project44-style).
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from app import data, weather

MODE_RU = {"rail": "ж/д", "sea": "море", "road": "авто"}
LEDGER_FILE = Path(__file__).parent / "data" / "ledger.json"
CO2_METHOD = ("ISO 14083 / GLEC v3.2, well-to-wheel, default factors "
              "(DESNZ 2026); hub operations not included — no primary data")


def _weather(sea: str, storm: bool) -> dict:
    return weather.current_sea_state(sea, storm)


def _delay_from_inputs(mode: str, wave: float, wind: float,
                       congestion_pct: int, customs_h: float) -> tuple[float, list[str]]:
    """Pure delay rules, shared by the point forecast and Monte Carlo."""
    delay = 0.0
    reasons: list[str] = []
    if congestion_pct >= 60:
        wait = round(congestion_pct * 0.12, 1)
        delay += wait
        reasons.append(f"загруженность порта {congestion_pct}% → ожидание ~{wait} ч")
    if mode == "sea":
        wave_delay = round(wave * 3, 1)
        delay += wave_delay
        reasons.append(f"волна {wave} м → +{wave_delay} ч хода")
        if wind > 14:
            delay += 6
            reasons.append(f"ветер {wind} м/с → +6 ч")
        if wave >= 2.0:
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
    elif mode == "road":
        delay += 1.0
        reasons.append("плановый резерв авто +1 ч")
    if customs_h:
        delay += customs_h
        reasons.append(f"таможенная очередь ~{customs_h} ч")
    return round(delay, 1), reasons


def predict_leg(leg: dict, storm: bool, scenario: dict | None = None) -> dict:
    """Delay hours for one leg, with human-readable reasons.

    scenario: {"closed_nodes": [...], "congestion": {port: pct},
               "customs_extra_h": h, "wave_m": x, "wind_ms": y}
    """
    scenario = scenario or {}
    if leg["from"] in scenario.get("closed_nodes", []) or \
            leg["to"] in scenario.get("closed_nodes", []):
        raise ValueError(f"leg {leg['id']} blocked: node closed by scenario")
    mode = leg["mode"]
    cong = scenario.get("congestion", {}).get(leg.get("port") or "", None)
    if cong is None:
        p = data.PORTS.get(leg["port"] or "")
        cong = p["congestion_pct"] if p else 0
    customs = leg.get("customs_h", 0) + scenario.get("customs_extra_h", 0)
    if mode == "sea":
        if scenario.get("wave_m") is not None or scenario.get("wind_ms") is not None:
            w = {"wave_m": scenario.get("wave_m", 1.0),
                 "wind_ms": scenario.get("wind_ms", 10.0)}
        else:
            w = _weather(leg["sea"], storm)
        wave, wind = w["wave_m"], w["wind_ms"]
    else:
        wave, wind = 0.0, 0.0
    delay, reasons = _delay_from_inputs(mode, wave, wind, cong, customs)
    out = {**leg, "predicted_h": round(leg["base_h"] + delay, 1),
           "delay_h": delay, "reasons": reasons,
           "risk": "high" if delay >= 12 else "medium" if delay >= 5 else "low",
           "co2_kg": round(leg["distance_km"] * data.CO2_G_PER_TKM[mode] / 1000, 1),
           "cost_usd": round(leg["distance_km"] * data.COST_USD_PER_TKM[mode], 2)}
    if mode == "sea":
        out["sailing_window"] = {
            "interval_h": leg.get("sailing_interval_h", 72),
            "note": ("Рейсы по мере загрузки, без фиксированного расписания; "
                     "средний интервал ~72 ч, ожидание 1–2 дня — норма."),
        }
    return out


def _leg_weight_co2(leg: dict, weight_t: float) -> float:
    return leg["distance_km"] * weight_t * data.CO2_G_PER_TKM[leg["mode"]] / 1000


def _leg_weight_cost(leg: dict, weight_t: float) -> float:
    return leg["distance_km"] * weight_t * data.COST_USD_PER_TKM[leg["mode"]]


def simulate_route(route: dict, weight_t: float, storm: bool = False,
                   scenario: dict | None = None, n: int = 200,
                   seed: int = 7) -> dict:
    """Monte Carlo ETA: sample wave/wind from the real 90-day archive,
    congestion and customs around their values; return P50/P90/P95 of
    total time and P(delay > 24h)."""
    scenario = scenario or {}
    rng = np.random.default_rng(seed)
    totals, delays = [], []
    for _ in range(n):
        total = 0.0
        for leg in route["legs"]:
            if leg["from"] in scenario.get("closed_nodes", []) or \
                    leg["to"] in scenario.get("closed_nodes", []):
                raise ValueError(f"leg {leg['id']} blocked: node closed by scenario")
            cong = scenario.get("congestion", {}).get(leg.get("port") or "", None)
            if cong is None:
                p = data.PORTS.get(leg["port"] or "")
                cong = int(np.clip(rng.normal(p["congestion_pct"], 8), 0, 100)) if p else 0
            customs = leg.get("customs_h", 0) + scenario.get("customs_extra_h", 0)
            customs = float(np.clip(rng.normal(customs, max(1.0, customs * 0.3)), 0, None))
            if leg["mode"] == "sea":
                if storm:
                    wave, wind = data.STORM["wave_m"], data.STORM["wind_ms"]
                elif scenario.get("wave_m") is not None:
                    wave = scenario["wave_m"]
                    wind = scenario.get("wind_ms", 10.0)
                else:
                    wave, wind = weather.bootstrap_sea_state(rng, leg["sea"])
            else:
                wave, wind = 0.0, 0.0
            d, _ = _delay_from_inputs(leg["mode"], wave, wind, cong, customs)
            total += leg["base_h"] + d
        base = sum(l["base_h"] for l in route["legs"])
        totals.append(total)
        delays.append(total - base)
    totals = np.array(totals)
    delays = np.array(delays)
    return {
        "n": n,
        "eta_p50_h": round(float(np.percentile(totals, 50)), 1),
        "eta_p90_h": round(float(np.percentile(totals, 90)), 1),
        "eta_p95_h": round(float(np.percentile(totals, 95)), 1),
        "prob_delay_over_24h": round(float(np.mean(delays > 24)), 2),
    }


def forecast_route(route: dict, weight_t: float, storm: bool = False,
                   scenario: dict | None = None) -> dict:
    legs = [predict_leg(leg, storm, scenario) for leg in route["legs"]]
    total_base = sum(leg["base_h"] for leg in legs)
    total_pred = round(sum(leg["predicted_h"] for leg in legs), 1)
    delay = round(total_pred - total_base, 1)
    bottleneck = max(legs, key=lambda leg: leg["delay_h"])
    # Delay probability: saturating curve. In a storm, sea routes get a
    # high floor — land routes (no sea legs) keep their own probability.
    prob = min(0.95, delay / (delay + 18)) if delay > 0 else 0.05
    if storm and any(leg["mode"] == "sea" for leg in legs):
        prob = max(prob, 0.85)
    co2_kg = round(sum(_leg_weight_co2(l, weight_t) for l in legs))
    cost_usd = round(sum(_leg_weight_cost(l, weight_t) for l in legs))
    if bottleneck["delay_h"] >= 12:
        rec = (f"Узкое место — {MODE_RU[bottleneck['mode']]} "
               f"{data.NODES[bottleneck['from']]['name']} → "
               f"{data.NODES[bottleneck['to']]['name']}. "
               f"Рассмотрите альтернативный маршрут до отправки.")
    elif delay >= 8:
        rec = "Задержка значительная: сдвиньте стыковку с паромом и предупредите получателя."
    else:
        rec = "Маршрут в графике: риски в пределах резерва."
    mc = simulate_route(route, weight_t, storm, scenario)
    return {
        "route_id": route["id"], "route_name": route["name"],
        "storm": storm, "legs": legs,
        "total_base_h": total_base, "total_predicted_h": total_pred,
        "delay_h": delay, "delay_probability": round(prob, 2),
        "bottleneck_leg_id": bottleneck["id"],
        "co2_kg": co2_kg, "cost_usd": cost_usd,
        "co2_method": CO2_METHOD,
        "co2_breakdown": [
            {"leg_id": l["id"], "mode": l["mode"],
             "from": data.NODES[l["from"]]["name"], "to": data.NODES[l["to"]]["name"],
             "distance_km": l["distance_km"],
             "co2_kg": round(_leg_weight_co2(l, weight_t)),
             "factor_g_per_tkm": data.CO2_G_PER_TKM[l["mode"]],
             "data_quality": "default (DESNZ 2026)"}
            for l in legs],
        "monte_carlo": mc,
        "eta_p50_h": mc["eta_p50_h"], "eta_p90_h": mc["eta_p90_h"],
        "eta_p95_h": mc["eta_p95_h"],
        "recommendation": rec,
    }


def compare_routes(weight_t: float, storm: bool = False,
                   scenario: dict | None = None) -> list[dict]:
    out = []
    for route in data.ROUTES:
        try:
            fc = forecast_route(route, weight_t, storm, scenario)
        except ValueError:
            continue  # route blocked by scenario
        out.append({
            "route_id": fc["route_id"], "route_name": fc["route_name"],
            "via": route["via"],
            "total_predicted_h": fc["total_predicted_h"],
            "delay_h": fc["delay_h"],
            "delay_probability": fc["delay_probability"],
            "co2_kg": fc["co2_kg"], "cost_usd": fc["cost_usd"],
            "bottleneck_leg_id": fc["bottleneck_leg_id"],
            "eta_p50_h": fc["eta_p50_h"], "eta_p90_h": fc["eta_p90_h"],
        })
    return out


# --- Shipments store: seed shipments + user-created ones (JSON file).
SHIPMENTS_FILE = Path(__file__).parent / "data" / "shipments.json"


def _load_created_shipments() -> list[dict]:
    try:
        with open(SHIPMENTS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save_created_shipments(rows: list[dict]) -> None:
    SHIPMENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(SHIPMENTS_FILE, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)


def all_shipments() -> list[dict]:
    return list(data.SHIPMENTS) + _load_created_shipments()


def get_shipment_any(shipment_id: str) -> dict | None:
    s = data.get_shipment(shipment_id)
    if s is not None:
        return s
    return next((x for x in _load_created_shipments() if x["id"] == shipment_id), None)


def create_shipment(name: str, cargo: str, weight_t: float, route_id: str,
                    docs: dict[str, str] | None = None) -> dict:
    route = data.get_route(route_id)
    if route is None:
        raise ValueError(f"unknown route {route_id}")
    rows = _load_created_shipments()
    n = len(rows) + 1
    sid = f"SHP-{2000 + n}"
    doc_types = list(data.DOC_TYPES.keys())
    shipment = {
        "id": sid, "name": name, "cargo": cargo, "weight_t": weight_t,
        "route_id": route_id, "current_leg": 0, "status": "создан",
        "eta": (datetime.now(timezone.utc) +
                timedelta(hours=sum(l["base_h"] for l in route["legs"]))).isoformat(),
        "docs": docs if docs is not None else {d: "pending" for d in doc_types[:4]},
    }
    rows.append(shipment)
    _save_created_shipments(rows)
    return shipment


def clear_created_shipments() -> None:
    _save_created_shipments([])


# --- Pre-clearance ledger: append-only hash chain, persistent JSON file.
# Mechanics shown (hash link + tamper-evidence), not a production DLT.
def _load_ledger() -> list[dict]:
    try:
        with open(LEDGER_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save_ledger() -> None:
    LEDGER_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LEDGER_FILE, "w", encoding="utf-8") as f:
        json.dump(LEDGER, f, ensure_ascii=False, indent=1)


LEDGER: list[dict] = _load_ledger()


def clear_ledger() -> None:
    LEDGER.clear()
    _save_ledger()


def ledger_last_hash() -> str:
    return LEDGER[-1]["hash"] if LEDGER else "0" * 64


def submit_clearance(shipment_id: str, docs: dict[str, str]) -> dict:
    shipment = get_shipment_any(shipment_id)
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
    _save_ledger()
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


# --- Digital twin: event timeline for a shipment.
def shipment_timeline(shipment: dict, storm: bool = False) -> dict:
    route = data.get_route(shipment["route_id"])
    fc = forecast_route(route, shipment["weight_t"], storm)
    try:
        eta = datetime.fromisoformat(shipment["eta"])
    except ValueError:
        eta = datetime.now(timezone.utc) + timedelta(hours=fc["total_predicted_h"])
    start = eta - timedelta(hours=fc["total_base_h"])
    events: list[dict] = [{
        "time": start.isoformat(), "title": "Груз принят на терминале",
        "detail": f"{data.NODES[route['legs'][0]['from']]['name']} — {shipment['cargo']}",
        "status": "done",
    }]
    t_plan = start
    t_pred = start
    for i, leg in enumerate(fc["legs"]):
        st = "done" if i < shipment["current_leg"] else \
             "in_progress" if i == shipment["current_leg"] else "planned"
        events.append({
            "time": t_plan.isoformat(),
            "predicted_time": t_pred.isoformat(),
            "title": (f"Отправление: {MODE_RU[leg['mode']]} "
                      f"{data.NODES[leg['from']]['name']} → {data.NODES[leg['to']]['name']}"),
            "detail": f"план {leg['base_h']} ч, прогноз {leg['predicted_h']} ч (+{leg['delay_h']} ч)",
            "status": st,
        })
        t_plan += timedelta(hours=leg["base_h"])
        t_pred += timedelta(hours=leg["predicted_h"])
        if leg.get("customs_h"):
            events.append({
                "time": t_plan.isoformat(),
                "predicted_time": t_pred.isoformat(),
                "title": f"Таможня: {data.NODES[leg['to']]['name']}",
                "detail": f"очередь ~{leg['customs_h']} ч",
                "status": st,
            })
        events.append({
            "time": t_plan.isoformat(),
            "predicted_time": t_pred.isoformat(),
            "title": f"Прибытие: {data.NODES[leg['to']]['name']}",
            "detail": ("узкое место" if leg["id"] == fc["bottleneck_leg_id"] else ""),
            "status": st,
        })
    last = next((e for e in reversed(LEDGER) if e["shipment_id"] == shipment["id"]), None)
    events.append({
        "time": start.isoformat(),
        "title": "Pre-clearance",
        "detail": ("пройден" if last and last["decision"] == "pre-cleared"
                   else "не хватает документов: " + ", ".join(last["missing"])
                   if last else "не подавался"),
        "status": "done" if last and last["decision"] == "pre-cleared" else "planned",
    })
    return {"shipment_id": shipment["id"], "route_id": route["id"],
            "planned_eta": eta.isoformat(),
            "predicted_eta": t_pred.isoformat(),
            "events": events}


# --- Exceptions feed (project44-style): what is wrong, what to do.
def compute_exceptions(storm: bool = False) -> list[dict]:
    out: list[dict] = []
    live = weather.get_live_weather()["segments"]
    for seg_id, s in live.items():
        if s["wave_m"] >= 2.0:
            out.append({
                "severity": "high",
                "title": f"Волна ≥ 2 м: {s['segment']}",
                "detail": f"волна {s['wave_m']} м — паромы не выходят, следующий рейс не раньше чем через ~48 ч.",
                "action": "Перенаправьте грузы на сухопутный маршрут через Баку до улучшения погоды.",
            })
    if storm:
        out.append({
            "severity": "high", "title": "Шторм на Каспии (сценарий)",
            "detail": "Ветер 19 м/с, волна 3,4 м — все морские плечи остановлены.",
            "action": "Пересчитайте маршруты: сухопутный R3 становится самым надёжным.",
        })
    for pid, p in data.PORTS.items():
        if p["congestion_pct"] >= 60:
            out.append({
                "severity": "medium",
                "title": f"Порт перегружен: {data.NODES[pid]['name']} — {p['congestion_pct']}%",
                "detail": f"Ожидание на терминале ~{round(p['congestion_pct'] * 0.12, 1)} ч, таможня ~{p['customs_queue_h']} ч.",
                "action": "Сдвиньте стыковку с паромом или выберите соседний порт.",
            })
    for s in all_shipments():
        route = data.get_route(s["route_id"])
        fc = forecast_route(route, s["weight_t"], storm)
        missing = [d for d, v in s["docs"].items() if v == "missing"]
        if missing:
            out.append({
                "severity": "high", "shipment_id": s["id"],
                "title": f"{s['id']}: нет документов — {', '.join(missing)}",
                "detail": "Pre-clearance не пройдёт, груз встанет на таможне.",
                "action": "Загрузите недостающие документы до отправки.",
            })
        if fc["delay_probability"] >= 0.6:
            others = [r for r in compare_routes(s["weight_t"], storm)
                      if r["route_id"] != s["route_id"]]
            alt = min(others, key=lambda r: r["total_predicted_h"]) if others else None
            out.append({
                "severity": "medium", "shipment_id": s["id"],
                "title": (f"{s['id']}: риск срыва {round(fc['delay_probability'] * 100)}%, "
                          f"задержка +{fc['delay_h']} ч"),
                "detail": fc["recommendation"],
                "action": (f"Рассмотрите {alt['route_id']}: {alt['total_predicted_h']} ч "
                           f"(экономия ~{round(fc['total_predicted_h'] - alt['total_predicted_h'], 1)} ч).")
                if alt else "Альтернатив нет — предупредите получателя.",
            })
    order = {"high": 0, "medium": 1, "low": 2}
    return sorted(out, key=lambda e: order[e["severity"]])
