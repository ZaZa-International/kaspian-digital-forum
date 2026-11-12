"""Discrete-event rail–ferry docking model (TZ: explicit synchronization).

Caspian ferries have no fixed published timetable. We model departures as a
periodic schedule (interval ~72 h, configurable phase) with a loading cutoff:
a train arriving at the port catches the next departure only if it arrives
before `departure − cutoff`; otherwise it misses the sailing and waits for the
next one (+interval). This is an explicit discrete-event synchronization model
("поезд прибыл в X, паром ушёл в Y, опоздали → +72 ч"), shown alongside the ETA
(the ETA Monte Carlo keeps the interval/2 ferry-wait assumption).

Honesty: the schedule is an ASSUMPTION (no real published timetable exists);
the synchronization model itself is real. Labelled as such in the API/UI.
"""

from __future__ import annotations

import math

import numpy as np

from app import data

DEFAULT_INTERVAL_H = 72.0
DEFAULT_CUTOFF_H = 6.0  # loading closes 6 h before departure
DEFAULT_PHASE_H = 0.0


def connection(
    arrival_h: float,
    interval_h: float = DEFAULT_INTERVAL_H,
    cutoff_h: float = DEFAULT_CUTOFF_H,
    phase_h: float = DEFAULT_PHASE_H,
) -> dict:
    """One discrete-event connection: train arrival (hours from epoch) vs the
    periodic ferry schedule. Returns the caught departure, the wait, and
    whether the soonest departure was missed (and by how many hours)."""
    k0 = math.ceil((arrival_h - phase_h) / interval_h)
    soonest = phase_h + k0 * interval_h
    missed = arrival_h > soonest - cutoff_h
    departure = soonest + interval_h if missed else soonest
    missed_by = arrival_h - (soonest - cutoff_h) if missed else 0.0
    return {
        "arrival_h": round(arrival_h, 1),
        "departure_h": round(departure, 1),
        "wait_h": round(departure - arrival_h, 1),
        "missed": bool(missed),
        "missed_by_h": round(missed_by, 1),
        "soonest_departure_h": round(soonest, 1),
        "interval_h": interval_h,
        "cutoff_h": cutoff_h,
    }


def _clock(h: float) -> str:
    """Human clock illustration from hours-from-start (start = day 1 00:00)."""
    day = int(h // 24) + 1
    rem = h % 24
    hh, mm = int(rem), int(round((rem - int(rem)) * 60))
    if mm == 60:
        hh, mm = hh + 1, 0
    return f"д{day} {hh:02d}:{mm:02d}"


def _arrival_samples(
    route: dict, weight_t: float, storm: bool, scenario: dict, n: int, seed: int
):
    """Monte Carlo arrival time at the first sea port = cumulative time of the
    preceding legs (same delay rules as model.simulate_route). Returns
    (arrivals array, sea leg) or (None, None) if there is no rail/road→sea
    connection."""
    from app import model  # lazy: avoid circular import

    legs = route["legs"]
    sea_idx = next((i for i, l in enumerate(legs) if l["mode"] == "sea"), None)
    if sea_idx is None or sea_idx == 0:
        return None, None
    preceding = legs[:sea_idx]
    if not any(l["mode"] in ("rail", "road") for l in preceding):
        return None, None
    rng = np.random.default_rng(seed)
    arrivals = []
    for _ in range(n):
        t = 0.0
        for leg in preceding:
            cong = scenario.get("congestion", {}).get(leg.get("port") or "", None)
            if cong is None:
                p = data.PORTS.get(leg.get("port") or "")
                cong = (
                    int(np.clip(rng.normal(p["congestion_pct"], 8), 0, 100)) if p else 0
                )
            customs = leg.get("customs_h", 0) + scenario.get("customs_extra_h", 0)
            customs = float(
                np.clip(rng.normal(customs, max(1.0, customs * 0.3)), 0, None)
            )
            wait = 0.0
            if leg["mode"] == "sea":
                w = model._sea_weather(leg, storm, scenario)
                wave, wind = w["wave_m"], w["wind_ms"]
                wait = float(rng.uniform(0, leg.get("sailing_interval_h", 72)))
            else:
                wave, wind = 0.0, 0.0
            d, _ = model._delay_from_inputs(leg["mode"], wave, wind, cong, customs)
            t += leg["base_h"] + wait + d
        arrivals.append(t)
    return np.array(arrivals), legs[sea_idx]


def docking_analysis(
    route: dict,
    weight_t: float,
    storm: bool = False,
    scenario: dict | None = None,
    n: int = 1000,
    seed: int = 7,
) -> dict:
    """Discrete-event rail–ferry synchronization for a route. For each Monte
    Carlo arrival at the port, compute catch/miss against the ferry schedule;
    aggregate P(miss), expected wait, wait P90, and a worked example."""
    scenario = scenario or {}
    arrivals, sea_leg = _arrival_samples(route, weight_t, storm, scenario, n, seed)
    if arrivals is None:
        return {
            "route_id": route["id"],
            "has_connection": False,
            "connections": [],
            "kind": "discrete-event rail-ferry docking",
            "note": "Нет стыковки ж/д–паром (сухопутный маршрут или море без подвоза).",
        }
    interval = float(sea_leg.get("sailing_interval_h", DEFAULT_INTERVAL_H))
    conns = [connection(float(a), interval) for a in arrivals]
    waits = np.array([c["wait_h"] for c in conns])
    misses = np.array([c["missed"] for c in conns])
    point_arrival = float(
        sum(l["base_h"] for l in route["legs"][: route["legs"].index(sea_leg)])
    )
    example = connection(point_arrival, interval)
    port = sea_leg.get("from")
    return {
        "route_id": route["id"],
        "has_connection": True,
        "connections": [
            {
                "port": port,
                "port_name": data.NODES[port]["name"],
                "sea_leg_id": sea_leg["id"],
                "interval_h": interval,
                "cutoff_h": DEFAULT_CUTOFF_H,
                "p_miss": round(float(np.mean(misses)), 3),
                "expected_wait_h": round(float(np.mean(waits)), 1),
                "wait_p90_h": round(float(np.percentile(waits, 90)), 1),
                "example": {
                    **example,
                    "arrival_clock": _clock(example["arrival_h"]),
                    "departure_clock": _clock(example["departure_h"]),
                    "verdict": (
                        f"опоздали на {example['missed_by_h']} ч → следующий паром через {interval:.0f} ч"
                        if example["missed"]
                        else f"успеваем, ожидание {example['wait_h']} ч"
                    ),
                },
            }
        ],
        "n": n,
        "seed": seed,
        "kind": "discrete-event rail-ferry docking (Monte Carlo arrivals vs periodic ferry schedule)",
        "schedule_assumption": (
            "Расписание парома — допущение: интервал ~72 ч, погрузка закрывается "
            "за 6 ч до отправления. Реального опубликованного расписания нет."
        ),
        "note": (
            "Явная синхронизация «поезд прибыл ↔ паром ушёл»: если поезд не успевает к погрузке — "
            "пропуск рейса и ожидание следующего. P(пропуск) и ожидание — из Monte Carlo прибытий."
        ),
    }
