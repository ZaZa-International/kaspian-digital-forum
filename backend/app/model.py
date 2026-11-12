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

import numpy as np

from app import data, store, weather

MODE_RU = {"rail": "ж/д", "sea": "море", "road": "авто"}
CO2_METHOD = (
    "Оценка по методологии, согласованной с ISO 14083 / GLEC v3.3 "
    "(без верификации). Факторы DESNZ 2026 WTW (UK proxy): ж/д 25,83, "
    "паром RoRo 51,58, авто 122,92 г CO2e/т-км. Не учтены: терминальные "
    "операции, перегрузка, холостые пробеги."
)


def _weather(sea: str, storm: bool) -> dict:
    return weather.current_sea_state(sea, storm)


def _sea_weather(leg: dict, storm: bool, scenario: dict) -> dict:
    """Single precedence rule for ALL endpoints and Monte Carlo:
    explicit scenario wave/wind > storm preset > live/fallback weather.
    Unspecified components keep the base value (wind-only works too)."""
    base = _weather(leg["sea"], storm)
    wave = scenario.get("wave_m")
    wind = scenario.get("wind_ms")
    if wave is None and wind is None:
        return base
    return {
        "wave_m": base["wave_m"] if wave is None else wave,
        "wind_ms": base["wind_ms"] if wind is None else wind,
        "source": "scenario override",
    }


def _expected_wait_h(leg: dict) -> float:
    """Expected ferry waiting: no fixed timetable on the Caspian, sailings
    ~every 72 h -> uniform(0, interval) wait, mean interval/2. Assumption,
    labelled as such; Monte Carlo samples the full uniform range."""
    if leg["mode"] != "sea":
        return 0.0
    return round(leg.get("sailing_interval_h", 72) / 2, 1)


def _delay_from_inputs(
    mode: str, wave: float, wind: float, congestion_pct: int, customs_h: float
) -> tuple[float, list[str]]:
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
    if leg["from"] in scenario.get("closed_nodes", []) or leg["to"] in scenario.get(
        "closed_nodes", []
    ):
        raise ValueError(f"leg {leg['id']} blocked: node closed by scenario")
    mode = leg["mode"]
    cong = scenario.get("congestion", {}).get(leg.get("port") or "", None)
    if cong is None:
        p = data.PORTS.get(leg["port"] or "")
        cong = p["congestion_pct"] if p else 0
    customs = leg.get("customs_h", 0) + scenario.get("customs_extra_h", 0)
    if mode == "sea":
        w = _sea_weather(leg, storm, scenario)
        wave, wind = w["wave_m"], w["wind_ms"]
    else:
        wave, wind = 0.0, 0.0
    delay, reasons = _delay_from_inputs(mode, wave, wind, cong, customs)
    wait_h = _expected_wait_h(leg)
    if wait_h:
        reasons.insert(
            0,
            f"ожидание отправления парома ~{wait_h} ч "
            f"(интервал ~{leg.get('sailing_interval_h', 72)} ч, "
            f"расписания нет — допущение)",
        )
    out = {
        **leg,
        "predicted_h": round(leg["base_h"] + wait_h + delay, 1),
        "wait_h": wait_h,
        "delay_h": delay,
        "reasons": reasons,
        "risk": "high" if delay >= 12 else "medium" if delay >= 5 else "low",
        "co2_kg_per_t": round(leg["distance_km"] * data.CO2_G_PER_TKM[mode] / 1000, 2),
        "cost_usd_per_t": round(leg["distance_km"] * data.COST_USD_PER_TKM[mode], 2),
    }
    if mode == "sea":
        out["weather"] = {"wave_m": wave, "wind_ms": wind, "source": w["source"]}
        out["sailing_window"] = {
            "interval_h": leg.get("sailing_interval_h", 72),
            "expected_wait_h": wait_h,
            "note": (
                "Рейсы по мере загрузки, без фиксированного расписания; "
                "средний интервал ~72 ч, ожидание 1–2 дня — норма."
            ),
        }
    return out


def _leg_weight_co2(leg: dict, weight_t: float) -> float:
    return leg["distance_km"] * weight_t * data.CO2_G_PER_TKM[leg["mode"]] / 1000


def _leg_weight_cost(leg: dict, weight_t: float) -> float:
    return leg["distance_km"] * weight_t * data.COST_USD_PER_TKM[leg["mode"]]


def simulate_route(
    route: dict,
    weight_t: float,
    storm: bool = False,
    scenario: dict | None = None,
    n: int = 200,
    seed: int = 7,
    return_samples: bool = False,
) -> dict:
    """Monte Carlo ETA. Samples: wave/wind from the real 90-day archive
    (fixed under storm/scenario overrides — same precedence as predict_leg),
    congestion ~ N(port, 8), customs ~ N(queue, 30%), ferry wait ~
    Uniform(0, sailing_interval). Returns SIMULATION quantiles P50/P90/P95
    of total time and P(delay > 24 h vs plan incl. expected wait).
    These are not empirical probabilities of actual delivery."""
    scenario = scenario or {}
    rng = np.random.default_rng(seed)
    base = sum(l["base_h"] for l in route["legs"])
    expected_wait = sum(_expected_wait_h(l) for l in route["legs"])
    totals, delays = [], []
    for _ in range(n):
        total = 0.0
        for leg in route["legs"]:
            if leg["from"] in scenario.get("closed_nodes", []) or leg[
                "to"
            ] in scenario.get("closed_nodes", []):
                raise ValueError(f"leg {leg['id']} blocked: node closed by scenario")
            cong = scenario.get("congestion", {}).get(leg.get("port") or "", None)
            if cong is None:
                p = data.PORTS.get(leg["port"] or "")
                cong = (
                    int(np.clip(rng.normal(p["congestion_pct"], 8), 0, 100)) if p else 0
                )
            customs = leg.get("customs_h", 0) + scenario.get("customs_extra_h", 0)
            customs = float(
                np.clip(rng.normal(customs, max(1.0, customs * 0.3)), 0, None)
            )
            wait = 0.0
            if leg["mode"] == "sea":
                overridden = (
                    storm
                    or scenario.get("wave_m") is not None
                    or scenario.get("wind_ms") is not None
                )
                if overridden:
                    w = _sea_weather(leg, storm, scenario)
                    wave, wind = w["wave_m"], w["wind_ms"]
                else:
                    wave, wind = weather.bootstrap_sea_state(rng, leg["sea"])
                wait = float(rng.uniform(0, leg.get("sailing_interval_h", 72)))
            else:
                wave, wind = 0.0, 0.0
            d, _ = _delay_from_inputs(leg["mode"], wave, wind, cong, customs)
            total += leg["base_h"] + wait + d
        totals.append(total)
        delays.append(total - base - expected_wait)
    totals = np.array(totals)
    delays = np.array(delays)
    out = {
        "n": n,
        "seed": seed,
        "kind": "simulation quantiles (weather bootstrap + synthetic congestion/customs)",
        "coverage_note": (
            "Квантили симуляции, не эмпирическая вероятность фактической "
            "доставки; фактических исходов рейсов в наборе данных нет"
        ),
        "expected_wait_h": round(expected_wait, 1),
        "eta_p50_h": round(float(np.percentile(totals, 50)), 1),
        "eta_p90_h": round(float(np.percentile(totals, 90)), 1),
        "eta_p95_h": round(float(np.percentile(totals, 95)), 1),
        "prob_delay_over_24h": round(float(np.mean(delays > 24)), 2),
    }
    if return_samples:
        out["samples"] = [round(float(t), 3) for t in totals]
    return out


def _w1_distance(a: np.ndarray, b: np.ndarray) -> float:
    """Empirical Wasserstein-1 (earth mover's) distance between two 1-D
    samples of equal size = mean |sort(a) - sort(b)|. This is the L1
    distance between the sorted quantiles — the 'Manhattan' metric on
    ETA distributions. Units: hours."""
    return float(np.mean(np.abs(np.sort(a) - np.sort(b))))


_STORM_PRICE_CACHE: dict[tuple, dict] = {}


def storm_price_h(
    route: dict, scenario: dict | None = None, n: int = 2000, seed: int = 7
) -> dict:
    """W1 'price of the storm' in hours: Wasserstein-1 distance between the
    calm and storm ETA distributions of the same route (Monte Carlo, real
    weather archive). A land route immune to the storm has price ~0; a sea
    route pays the full shift. Independent of cargo weight (ETA totals do
    not depend on weight). Cached per (route, scenario) — n=2000 for a
    stable W1 (the UI P90 keeps n=200)."""
    scenario = scenario or {}
    key = (route["id"], json.dumps(scenario, sort_keys=True), n, seed)
    if key in _STORM_PRICE_CACHE:
        return _STORM_PRICE_CACHE[key]
    calm = np.array(
        simulate_route(route, 48, False, scenario, n, seed, return_samples=True)[
            "samples"
        ]
    )
    storm = np.array(
        simulate_route(route, 48, True, scenario, n, seed, return_samples=True)[
            "samples"
        ]
    )
    w1 = _w1_distance(calm, storm)
    result = {
        "storm_price_h": round(w1, 1),
        "n": n,
        "seed": seed,
        "kind": "Wasserstein-1 (L1 between sorted ETA samples), calm vs storm",
        "note": (
            "Цена шторма в часах: сдвиг распределения ETA между спокойной "
            "погодой и штормом. Сухопутный маршрут не зависит от моря → ~0. "
            "Симуляция на реальном архиве погоды, не эмпирическая мера."
        ),
    }
    _STORM_PRICE_CACHE[key] = result
    return result


def eta_distribution(
    route: dict,
    weight_t: float,
    scenario: dict | None = None,
    n: int = 2000,
    seed: int = 7,
    bins: int = 28,
) -> dict:
    """Calm vs storm ETA histograms over a shared bin range, with P50/P90/P95
    markers and the W1 storm price. Powers the distribution chart so the
    Monte Carlo uncertainty is visible, not just a number. Simulation on the
    real weather archive — not empirical outcomes."""
    scenario = scenario or {}
    calm = simulate_route(
        route, weight_t, False, scenario, n, seed, return_samples=True
    )
    storm = simulate_route(
        route, weight_t, True, scenario, n, seed, return_samples=True
    )
    ca, sa = np.array(calm["samples"]), np.array(storm["samples"])
    lo, hi = float(min(ca.min(), sa.min())), float(max(ca.max(), sa.max()))
    edges = np.linspace(lo, hi, bins + 1)
    cc, _ = np.histogram(ca, bins=edges)
    sc, _ = np.histogram(sa, bins=edges)
    return {
        "route_id": route["id"],
        "n": n,
        "seed": seed,
        "bins": bins,
        "edges": [round(float(e), 1) for e in edges],
        "calm": {
            "counts": cc.tolist(),
            "p50": calm["eta_p50_h"],
            "p90": calm["eta_p90_h"],
            "p95": calm["eta_p95_h"],
        },
        "storm": {
            "counts": sc.tolist(),
            "p50": storm["eta_p50_h"],
            "p90": storm["eta_p90_h"],
            "p95": storm["eta_p95_h"],
        },
        "storm_price_h": storm_price_h(route, scenario, n, seed)["storm_price_h"],
        "kind": "Monte Carlo ETA histograms (calm vs storm), real weather archive",
        "note": "Симуляция, не эмпирические исходы рейсов. W1 — сдвиг распределения в часах.",
    }


def forecast_route(
    route: dict, weight_t: float, storm: bool = False, scenario: dict | None = None
) -> dict:
    legs = [predict_leg(leg, storm, scenario) for leg in route["legs"]]
    total_base = sum(leg["base_h"] for leg in legs)
    total_wait = round(sum(leg["wait_h"] for leg in legs), 1)
    total_pred = round(sum(leg["predicted_h"] for leg in legs), 1)
    delay = round(total_pred - total_base - total_wait, 1)
    bottleneck = max(legs, key=lambda leg: leg["delay_h"])
    # Heuristic risk index (NOT an empirical probability): saturating curve.
    # In a storm, sea routes get a high floor — land routes keep their own.
    prob = min(0.95, delay / (delay + 18)) if delay > 0 else 0.05
    if storm and any(leg["mode"] == "sea" for leg in legs):
        prob = max(prob, 0.85)
    co2_kg = round(sum(_leg_weight_co2(l, weight_t) for l in legs))
    cost_usd = round(sum(_leg_weight_cost(l, weight_t) for l in legs))
    if bottleneck["delay_h"] >= 12:
        rec = (
            f"Узкое место — {MODE_RU[bottleneck['mode']]} "
            f"{data.NODES[bottleneck['from']]['name']} → "
            f"{data.NODES[bottleneck['to']]['name']}. "
            f"Рассмотрите альтернативный маршрут до отправки."
        )
    elif delay >= 8:
        rec = "Задержка значительная: сдвиньте стыковку с паромом и предупредите получателя."
    else:
        rec = "Маршрут в графике: риски в пределах резерва."
    mc = simulate_route(route, weight_t, storm, scenario)
    return {
        "route_id": route["id"],
        "route_name": route["name"],
        "storm": storm,
        "legs": legs,
        "total_base_h": total_base,
        "total_wait_h": total_wait,
        "total_predicted_h": total_pred,
        "delay_h": delay,
        "delay_probability": round(prob, 2),
        "delay_probability_kind": "heuristic index (not an empirical probability)",
        "bottleneck_leg_id": bottleneck["id"],
        "co2_kg": co2_kg,
        "cost_usd": cost_usd,
        "co2_unit": "kg CO2e (total, AR5)",
        "cost_unit": "USD (estimate)",
        "co2_method": CO2_METHOD,
        "co2_factor_meta": data.CO2_FACTOR_META,
        "co2_breakdown": [
            {
                "leg_id": l["id"],
                "mode": l["mode"],
                "from": data.NODES[l["from"]]["name"],
                "to": data.NODES[l["to"]]["name"],
                "distance_km": l["distance_km"],
                "co2_kg": round(_leg_weight_co2(l, weight_t)),
                "factor_g_per_tkm": data.CO2_G_PER_TKM[l["mode"]],
                "data_quality": "default (DESNZ 2026, UK proxy — indicative)",
            }
            for l in legs
        ],
        "monte_carlo": mc,
        "eta_p50_h": mc["eta_p50_h"],
        "eta_p90_h": mc["eta_p90_h"],
        "eta_p95_h": mc["eta_p95_h"],
        "recommendation": rec,
    }


def compare_routes(
    weight_t: float, storm: bool = False, scenario: dict | None = None
) -> list[dict]:
    out = []
    for route in data.ROUTES:
        try:
            fc = forecast_route(route, weight_t, storm, scenario)
        except ValueError:
            continue  # route blocked by scenario
        out.append(
            {
                "route_id": fc["route_id"],
                "route_name": fc["route_name"],
                "via": route["via"],
                "total_predicted_h": fc["total_predicted_h"],
                "total_wait_h": fc["total_wait_h"],
                "delay_h": fc["delay_h"],
                "delay_probability": fc["delay_probability"],
                "co2_kg": fc["co2_kg"],
                "cost_usd": fc["cost_usd"],
                "bottleneck_leg_id": fc["bottleneck_leg_id"],
                "eta_p50_h": fc["eta_p50_h"],
                "eta_p90_h": fc["eta_p90_h"],
                "storm_price_h": storm_price_h(route, scenario)["storm_price_h"],
            }
        )
    return out


# --- Shipments store: seed shipments + user-created ones (SQLite, see store.py).
def _load_created_shipments() -> list[dict]:
    return store.list_shipments()


def all_shipments() -> list[dict]:
    return list(data.SHIPMENTS) + store.list_shipments()


def get_shipment_any(shipment_id: str) -> dict | None:
    s = data.get_shipment(shipment_id)
    if s is not None:
        return s
    return store.get_shipment(shipment_id)


def create_shipment(
    name: str,
    cargo: str,
    weight_t: float,
    route_id: str,
    docs: dict[str, str] | None = None,
    idempotency_key: str | None = None,
) -> dict:
    route = data.get_route(route_id)
    if route is None:
        raise ValueError(f"unknown route {route_id}")
    doc_types = list(data.DOC_TYPES.keys())
    eta = (
        datetime.now(timezone.utc)
        + timedelta(hours=sum(l["base_h"] for l in route["legs"]))
    ).isoformat()
    return store.create_shipment_record(
        name=name,
        cargo=cargo,
        weight_t=weight_t,
        route_id=route_id,
        docs=docs if docs is not None else {d: "pending" for d in doc_types[:4]},
        eta=eta,
        idempotency_key=idempotency_key,
    )


def clear_created_shipments() -> None:
    store.clear_shipments()


# --- Pre-clearance: document registry + append-only hash chain (store.py).
# Statuses are assigned by SERVER rules (simulated customs authority), never
# by a client-supplied "verified" field. The hash chain is a tamper-evident
# demo mechanic, not a production DLT (no distributed consensus).
DOC_RULE_VERSION = "demo-rules-v1"
CLEARANCE_ISSUER = "SCT Demo Customs Authority (симуляция)"
CLEARANCE_NOTE = (
    "Проверка симулируется демо-правилами: документ считается проверенным, "
    "если подана непустая ссылка; префикс REJ- → отказ, EXP- → истёк. "
    "Реальной таможенной системы нет."
)


def ensure_documents(shipment: dict) -> list[dict]:
    """Initialise the registry from seed/creation data once, with provenance."""
    docs = store.get_documents(shipment["id"])
    if docs:
        return docs
    for dtype, status in shipment.get("docs", {}).items():
        if status == "verified":
            store.upsert_document(
                shipment["id"],
                dtype,
                status="verified",
                ref="demo-seed",
                issuer=CLEARANCE_ISSUER,
                source="demo-seed",
                note="Проверен в демо-наборе (симуляция)",
                rule_version=DOC_RULE_VERSION,
            )
        elif status == "missing":
            store.upsert_document(
                shipment["id"],
                dtype,
                status="missing",
                source="demo-seed",
                note="Документ не подан",
            )
        else:
            store.upsert_document(
                shipment["id"],
                dtype,
                status="submitted",
                ref="demo-pending",
                source="demo-seed",
                note="Подан, ожидает проверки (симуляция)",
            )
    return store.get_documents(shipment["id"])


def get_documents(shipment_id: str) -> list[dict]:
    shipment = get_shipment_any(shipment_id)
    if shipment is None:
        raise ValueError(f"unknown shipment {shipment_id}")
    return ensure_documents(shipment)


def _authority_check(doc: dict) -> None:
    """Simulated customs authority decision on a submitted document."""
    ref = doc.get("ref") or ""
    if not ref:
        store.upsert_document(
            doc["shipment_id"],
            doc["doc_type"],
            status="missing",
            source=doc.get("source"),
            note="Пустая ссылка — документ не подан",
        )
    elif ref.startswith("REJ-"):
        store.upsert_document(
            doc["shipment_id"],
            doc["doc_type"],
            status="rejected",
            ref=ref,
            issuer=CLEARANCE_ISSUER,
            source=doc.get("source"),
            note="Отказ демо-правила: префикс REJ-",
            rule_version=DOC_RULE_VERSION,
        )
    elif ref.startswith("EXP-"):
        store.upsert_document(
            doc["shipment_id"],
            doc["doc_type"],
            status="expired",
            ref=ref,
            issuer=CLEARANCE_ISSUER,
            source=doc.get("source"),
            note="Истёк срок действия (демо-правило: префикс EXP-)",
            rule_version=DOC_RULE_VERSION,
        )
    else:
        store.upsert_document(
            doc["shipment_id"],
            doc["doc_type"],
            status="verified",
            ref=ref,
            issuer=CLEARANCE_ISSUER,
            source=doc.get("source"),
            note="Проверен симуляцией: ссылка подана, формат валиден",
            rule_version=DOC_RULE_VERSION,
        )


def submit_clearance(shipment_id: str, docs: dict[str, str] | None = None) -> dict:
    """docs = {doc_type: reference} — client submits document REFERENCES.
    The server registers them, runs the simulated authority check and only
    then decides. A client cannot assign 'verified' itself."""
    shipment = get_shipment_any(shipment_id)
    if shipment is None:
        raise ValueError(f"unknown shipment {shipment_id}")
    ensure_documents(shipment)
    required = list(shipment.get("docs", {}).keys())
    for dtype, ref in (docs or {}).items():
        if dtype not in data.DOC_TYPES:
            store.upsert_document(
                shipment_id,
                dtype,
                status="unknown",
                ref=str(ref),
                source="client (demo)",
                note="Неизвестный тип документа — не участвует в решении",
            )
        else:
            store.upsert_document(
                shipment_id,
                dtype,
                status="submitted",
                ref=str(ref),
                source="client (demo)",
                note="Подан клиентом, ожидает проверки",
            )
    for doc in store.get_documents(shipment_id):
        if doc["doc_type"] in required and doc["status"] == "submitted":
            _authority_check(doc)
    registry = store.get_documents(shipment_id)
    by_type = {d["doc_type"]: d for d in registry}
    missing = [t for t in required if by_type.get(t, {}).get("status") != "verified"]
    decision = "pre-cleared" if not missing else "needs_documents"
    snapshot = [
        {
            "doc_type": d["doc_type"],
            "status": d["status"],
            "content_hash": d["content_hash"],
            "issuer": d["issuer"],
            "source": d["source"],
            "rule_version": d["rule_version"],
        }
        for d in registry
    ]
    doc_hash = hashlib.sha256(
        json.dumps(
            {"shipment": shipment_id, "docs": snapshot},
            sort_keys=True,
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    return store.append_ledger_entry(
        shipment_id=shipment_id,
        decision=decision,
        missing=missing,
        doc_hash=doc_hash,
        issuer=CLEARANCE_ISSUER,
        rule_version=DOC_RULE_VERSION,
        docs_snapshot=snapshot,
    )


def verify_ledger() -> bool:
    return store.verify_ledger()


def _load_ledger() -> list[dict]:
    return store.ledger_entries()


def ledger_entries() -> list[dict]:
    return store.ledger_entries()


def clear_ledger() -> None:
    store.clear_ledger()
    store.clear_documents()


def ledger_last_hash() -> str:
    last = store.last_ledger_entry()
    return last["hash"] if last else "0" * 64


# --- Digital twin: event timeline for a shipment.
EVENT_RU = {
    "departure": "Отправление",
    "arrival": "Прибытие",
    "gate_in": "Въезд на терминал",
    "gate_out": "Выезд с терминала",
    "load": "Погрузка",
    "discharge": "Выгрузка",
    "customs_start": "Таможня: начало",
    "customs_done": "Таможня: завершено",
    "delay": "Задержка",
    "note": "Событие",
}


def _seed_demo_events(shipment: dict) -> None:
    """Demo actual-event log for seed shipments (labelled simulation, NOT telemetry)."""
    if shipment["id"] not in {s["id"] for s in data.SHIPMENTS}:
        return
    if store.list_events(shipment["id"]):
        return
    route = data.get_route(shipment["route_id"])
    try:
        eta = datetime.fromisoformat(shipment["eta"])
    except ValueError:
        return
    t = eta - timedelta(hours=sum(l["base_h"] for l in route["legs"]))
    for i, leg in enumerate(route["legs"]):
        if i > shipment["current_leg"]:
            break
        store.add_event(
            shipment_id=shipment["id"],
            event_type="departure",
            leg_id=leg["id"],
            planned_ts=t.isoformat(),
            actual_ts=t.isoformat(),
            occurred_ts=t.isoformat(),
            source="demo-simulation",
            location=data.NODES[leg["from"]]["name"],
            mode=leg["mode"],
            confidence="demo",
            note="Демо-симуляция, не GPS/AIS/телематрия",
        )
        t += timedelta(hours=leg["base_h"])
        if i < shipment["current_leg"]:
            store.add_event(
                shipment_id=shipment["id"],
                event_type="arrival",
                leg_id=leg["id"],
                planned_ts=t.isoformat(),
                actual_ts=t.isoformat(),
                occurred_ts=t.isoformat(),
                source="demo-simulation",
                location=data.NODES[leg["to"]]["name"],
                mode=leg["mode"],
                confidence="demo",
                note="Демо-симуляция, не GPS/AIS/телематрия",
            )


def add_shipment_event(shipment_id: str, **fields) -> dict:
    if get_shipment_any(shipment_id) is None:
        raise ValueError(f"unknown shipment {shipment_id}")
    return store.add_event(shipment_id=shipment_id, **fields)


def shipment_timeline(
    shipment: dict, storm: bool = False, scenario: dict | None = None
) -> dict:
    route = data.get_route(shipment["route_id"])
    fc = forecast_route(route, shipment["weight_t"], storm, scenario)
    _seed_demo_events(shipment)
    try:
        eta = datetime.fromisoformat(shipment["eta"])
    except ValueError:
        eta = datetime.now(timezone.utc) + timedelta(hours=fc["total_predicted_h"])
    start = eta - timedelta(hours=fc["total_base_h"])
    events: list[dict] = [
        {
            "time": start.isoformat(),
            "title": "Груз принят на терминале",
            "detail": f"{data.NODES[route['legs'][0]['from']]['name']} — {shipment['cargo']}",
            "status": "done",
        }
    ]
    t_plan = start
    t_pred = start
    for i, leg in enumerate(fc["legs"]):
        st = (
            "done"
            if i < shipment["current_leg"]
            else "in_progress"
            if i == shipment["current_leg"]
            else "planned"
        )
        events.append(
            {
                "time": t_plan.isoformat(),
                "predicted_time": t_pred.isoformat(),
                "title": (
                    f"Отправление: {MODE_RU[leg['mode']]} "
                    f"{data.NODES[leg['from']]['name']} → {data.NODES[leg['to']]['name']}"
                ),
                "detail": (
                    f"план {leg['base_h']} ч"
                    + (
                        f" + ожидание парома {leg['wait_h']} ч"
                        if leg.get("wait_h")
                        else ""
                    )
                    + f", прогноз {leg['predicted_h']} ч (+{leg['delay_h']} ч задержки)"
                ),
                "status": st,
            }
        )
        t_plan += timedelta(hours=leg["base_h"])
        t_pred += timedelta(hours=leg["predicted_h"])
        if leg.get("customs_h"):
            events.append(
                {
                    "time": t_plan.isoformat(),
                    "predicted_time": t_pred.isoformat(),
                    "title": f"Таможня: {data.NODES[leg['to']]['name']}",
                    "detail": f"очередь ~{leg['customs_h']} ч",
                    "status": st,
                }
            )
        events.append(
            {
                "time": t_plan.isoformat(),
                "predicted_time": t_pred.isoformat(),
                "title": f"Прибытие: {data.NODES[leg['to']]['name']}",
                "detail": (
                    "узкое место" if leg["id"] == fc["bottleneck_leg_id"] else ""
                ),
                "status": st,
            }
        )
    last = store.last_ledger_entry(shipment["id"])
    events.append(
        {
            "time": start.isoformat(),
            "title": "Pre-clearance",
            "detail": (
                "пройден"
                if last and last["decision"] == "pre-cleared"
                else "не хватает документов: " + ", ".join(last["missing"])
                if last
                else "не подавался"
            ),
            "status": "done"
            if last and last["decision"] == "pre-cleared"
            else "planned",
        }
    )
    for e in events:
        e["kind"] = "model"
    actual = [
        {
            **ev,
            "kind": "actual",
            "title": f"{EVENT_RU.get(ev['event_type'], ev['event_type'])} (факт)",
            "time": ev["occurred_ts"] or ev["received_ts"],
        }
        for ev in store.list_events(shipment["id"])
    ]
    return {
        "shipment_id": shipment["id"],
        "route_id": route["id"],
        "planned_eta": eta.isoformat(),
        "predicted_eta": t_pred.isoformat(),
        "timeline_kind": "model (план/прогноз) + actual events (демо-симуляция или введённые факты)",
        "events": events,
        "actual_events": actual,
    }


# --- Exceptions feed (project44-style): event -> impact -> action.
def compute_exceptions(storm: bool = False, scenario: dict | None = None) -> list[dict]:
    scenario = scenario or {}
    out: list[dict] = []
    live = weather.get_live_weather()["segments"]
    for seg_id, seg in data.SEA_SEGMENTS.items():
        s = live.get(seg_id, seg)
        wave = scenario.get("wave_m", s.get("wave_m", 0))
        src = (
            "сценарий"
            if scenario.get("wave_m") is not None
            else s.get("source", "demo")
        )
        if wave >= 2.0:
            out.append(
                {
                    "severity": "high",
                    "title": f"Волна ≥ 2 м: {seg['segment']} ({src})",
                    "detail": f"волна {wave} м — паромы не выходят, следующий рейс не раньше чем через ~48 ч.",
                    "action": "Перенаправьте грузы на сухопутный маршрут через Баку до улучшения погоды.",
                }
            )
    if storm:
        out.append(
            {
                "severity": "high",
                "title": "Шторм на Каспии (сценарий)",
                "detail": "Ветер 19 м/с, волна 3,4 м — все морские плечи остановлены.",
                "action": "Пересчитайте маршруты: сухопутный R3 становится самым надёжным.",
            }
        )
    for node_id in scenario.get("closed_nodes", []):
        out.append(
            {
                "severity": "high",
                "title": f"Узел закрыт (сценарий): {data.NODES[node_id]['name']}",
                "detail": "Все плечи через этот узел исключены из поиска и прогнозов.",
                "action": "Перестройте маршрут: динамический поиск уже обходит закрытый узел.",
            }
        )
    if scenario.get("customs_extra_h"):
        out.append(
            {
                "severity": "medium",
                "title": f"Доп. таможенная очередь +{scenario['customs_extra_h']} ч (сценарий)",
                "detail": "Применяется ко всем плечам с таможней во всех прогнозах и сравнениях.",
                "action": "Заложите резерв времени или подайте pre-clearance заранее.",
            }
        )
    for pid, p in data.PORTS.items():
        if p["congestion_pct"] >= 60:
            out.append(
                {
                    "severity": "medium",
                    "title": f"Порт перегружен: {data.NODES[pid]['name']} — {p['congestion_pct']}%",
                    "detail": f"Ожидание на терминале ~{round(p['congestion_pct'] * 0.12, 1)} ч, таможня ~{p['customs_queue_h']} ч.",
                    "action": "Сдвиньте стыковку с паромом или выберите соседний порт.",
                }
            )
    for s in all_shipments():
        route = data.get_route(s["route_id"])
        try:
            fc = forecast_route(route, s["weight_t"], storm, scenario)
        except ValueError:
            out.append(
                {
                    "severity": "high",
                    "shipment_id": s["id"],
                    "title": f"{s['id']}: маршрут заблокирован сценарием",
                    "detail": f"Маршрут {route['id']} проходит через закрытый узел.",
                    "action": "Выберите альтернативный маршрут в динамическом поиске.",
                }
            )
            continue
        registry = ensure_documents(s)
        missing = [d["doc_type"] for d in registry if d["status"] == "missing"]
        if missing:
            out.append(
                {
                    "severity": "high",
                    "shipment_id": s["id"],
                    "title": f"{s['id']}: нет документов — {', '.join(missing)}",
                    "detail": "Pre-clearance не пройдёт, груз встанет на таможне.",
                    "action": "Загрузите недостающие документы до отправки.",
                }
            )
        if fc["delay_probability"] >= 0.6:
            others = [
                r
                for r in compare_routes(s["weight_t"], storm, scenario)
                if r["route_id"] != s["route_id"]
            ]
            alt = min(others, key=lambda r: r["total_predicted_h"]) if others else None
            out.append(
                {
                    "severity": "medium",
                    "shipment_id": s["id"],
                    "title": (
                        f"{s['id']}: индекс риска {round(fc['delay_probability'] * 100)}%, "
                        f"задержка +{fc['delay_h']} ч"
                    ),
                    "detail": fc["recommendation"],
                    "action": (
                        f"Рассмотрите {alt['route_id']}: {alt['total_predicted_h']} ч "
                        f"(экономия ~{round(fc['total_predicted_h'] - alt['total_predicted_h'], 1)} ч)."
                    )
                    if alt
                    else "Альтернатив нет — предупредите получателя.",
                }
            )
    order = {"high": 0, "medium": 1, "low": 2}
    return sorted(out, key=lambda e: order[e["severity"]])


# --- Data provenance: every fact labelled by kind and source.
def provenance() -> list[dict]:
    live = weather.get_live_weather()
    return [
        {
            "id": "weather_live",
            "kind": "live" if live["live"] else "fallback",
            "what": "Волна/ветер на морских плечах (кэш 30 мин)",
            "source": "Open-Meteo marine + forecast API",
            "license": "CC BY 4.0; free tier — только non-commercial",
            "retrieved": live["fetched_at"],
            "note": "Атрибуция: Weather data by Open-Meteo.com. Fallback — демо-снимок.",
        },
        {
            "id": "weather_archive",
            "kind": "historical",
            "what": "90-дневный архив волны/ветра для bootstrap (Monte Carlo, ML)",
            "source": "Open-Meteo archive API, 2026-08-14..2026-11-11",
            "license": "CC BY 4.0",
            "retrieved": "2026-11-12",
            "note": "Прорежен каждый 6-й час (364 точки/сегмент); статистики в метаданных — по полному архиву.",
        },
        {
            "id": "ports",
            "kind": "demo",
            "what": "Загруженность портов и таможенные очереди",
            "source": "Демо-снимок (открытого live-фида нет)",
            "retrieved": "2026-11-12",
            "note": "Замена — data.PORTS; снапшоты portkuryk.kz / ships.asco.az — без открытой лицензии (docs/RESEARCH.md).",
        },
        {
            "id": "corridor_stats",
            "kind": "static",
            "what": "Грузопоток коридора, порт Махачкала",
            "source": "Правительство РФ / РЖД / Росморречфлот через прессу, окт 2026",
            "retrieved": "2026-11-12",
            "note": "Первичные сайты недоступны (403) — верифицировано через СМИ.",
        },
        {
            "id": "co2_factors",
            "kind": "static",
            "what": "Факторы CO2e по видам транспорта",
            **data.CO2_FACTOR_META,
        },
        {
            "id": "cost_rates",
            "kind": "assumption",
            "what": "Ставки $/т-км",
            "source": "Оценка; порядок величины — Argus Caspian Transport, окт 2025",
            "note": "Точная калибровка по паре портов не выполнена.",
        },
        {
            "id": "ferry_wait",
            "kind": "assumption",
            "what": "Ожидание парома ~interval/2 (интервал 72 ч), пропуск рейса +48 ч при волне ≥ 2 м",
            "source": "Caravanistan (06.2026), Wikivoyage, снапшот ships.asco.az (12.11.2026)",
            "note": "Фиксированного расписания нет; ожидание — допущение, Monte Carlo сэмплирует Uniform(0, 72).",
        },
        {
            "id": "conditional_legs",
            "kind": "assumption",
            "what": "Условные плечи: Махачкала–Амирабад/Энзели, Актау–Бендер-Аббас (KTI)",
            "source": "docs/RESEARCH.md: регулярные линии не подтверждены / маршрут абстрактный",
            "note": "Маркируются conditional=true в API и UI.",
        },
        {
            "id": "shipments_seed",
            "kind": "demo",
            "what": "Грузы SHP-1042..1044, их статусы и ETA",
            "source": "Демо-набор; события — demo-simulation, не телеметрия",
        },
        {
            "id": "ml_labels",
            "kind": "synthetic",
            "what": "Метки задержек для обучения ML",
            "source": "Правила коридора + гауссов шум (генератор ml.py)",
            "note": "Метрики — synthetic benchmark, не точность на реальных рейсах. Реальные данные: CSV-контракт + POST /api/shipments/{id}/events.",
        },
        {
            "id": "distances",
            "kind": "assumption",
            "what": "Расстояния плеч",
            "source": "Haversine по координатам ×1.1 (море); ж/д/авто — оценки",
            "note": "Не фактические тарифные километражи.",
        },
        {
            "id": "storm_price_w1",
            "kind": "synthetic",
            "what": "Цена шторма W1 (Вассерштейн-1) по маршрутам, часы",
            "source": "L1 между отсортированными ETA-сэмплами calm vs storm, Monte Carlo n=2000 seed=7",
            "note": "Симуляция на реальном архиве погоды; сдвиг распределения, не эмпирическая мера. Сухопутный R3 ≈ 0.",
        },
        {
            "id": "ml_extrapolation_l1",
            "kind": "synthetic",
            "what": "Флаг экстраполяции ML + kNN-аналоги (MAD-масштабированный L1)",
            "source": "L1-расстояние до медианы обучающей выборки; порог = p95 опыта",
            "note": "Метки синтетические; аналоги — иллюстративные похожие сценарии, не точный предиктор. calm R1 ≈ 10.1 (внутри), storm R1 ≈ 25.1 (вне опыта).",
        },
        {
            "id": "astar_heuristic",
            "kind": "assumption",
            "what": "A* с манхэттенской эвристикой для objective=time",
            "source": "h = coeff·L1_km, coeff = min(base_h/L1_km) по рёбрам (допустима)",
            "note": "L1 ≥ haversine на всех 55 парах (max ratio 1.414). Оптимум как у Dijkstra; на 11 узлах раскрытия совпадают, выигрыш растёт с размером графа.",
        },
    ]
