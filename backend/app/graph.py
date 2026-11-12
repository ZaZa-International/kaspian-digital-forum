"""Dynamic routing over the corridor graph (NetworkX-free, pure stdlib).

The corridor is a directed graph: nodes are cities/ports/terminals,
edges are transport legs (rail/sea/road) with time, cost, CO2 and
risk. Routing uses:
- Dijkstra for the shortest path by a chosen objective (cost/co2);
- A* with an admissible Manhattan (L1) heuristic for the time objective;
- Yen's K-shortest paths (k=3) for alternatives;
- Pareto filtering for the time/cost/CO2 trade-off.

Edge weights come from the live forecast (model.predict_leg), so
weather, congestion and scenarios change the optimal route.
"""

from __future__ import annotations

import heapq
import math
from itertools import count

from app import data, model


def _adjacency(closed: frozenset[str]) -> dict[str, list[dict]]:
    adj: dict[str, list[dict]] = {}
    for e in data.GRAPH_EDGES:
        if e["from"] in closed or e["to"] in closed:
            continue
        adj.setdefault(e["from"], []).append(e)
    return adj


def _dijkstra(
    adj: dict, start: str, goal: str, cost_fn
) -> tuple[list[dict] | None, float]:
    dist = {start: 0.0}
    prev: dict[str, tuple[str, dict]] = {}
    pq = [(0.0, start)]
    seen = set()
    while pq:
        d, u = heapq.heappop(pq)
        if u in seen:
            continue
        seen.add(u)
        if u == goal:
            break
        for e in adj.get(u, []):
            v = e["to"]
            nd = d + cost_fn(e)
            if nd < dist.get(v, float("inf")):
                dist[v] = nd
                prev[v] = (u, e)
                heapq.heappush(pq, (nd, v))
    if goal not in prev and goal != start:
        return None, float("inf")
    path: list[dict] = []
    u = goal
    while u != start:
        pu, e = prev[u]
        path.append(e)
        u = pu
    path.reverse()
    return path, dist[goal]


def _l1_km(a: dict, b: dict) -> float:
    """Equirectangular Manhattan (L1) distance in km. L1 >= haversine on
    every node pair (verified max ratio 1.414), so it yields an admissible
    A* heuristic once scaled by a lower-bound speed."""
    mid = math.radians((a["lat"] + b["lat"]) / 2)
    dx = 6371.0 * math.radians(b["lon"] - a["lon"]) * math.cos(mid)
    dy = 6371.0 * math.radians(b["lat"] - a["lat"])
    return abs(dx) + abs(dy)


_COEFF: float | None = None


def _manhattan_coeff() -> float:
    """Provably admissible h/km coefficient: min over edges of base_h / L1_km,
    so coeff * L1_km(from, to) <= base_h <= predicted_h for every edge.
    Recomputed from data, stays admissible if the graph changes."""
    global _COEFF
    if _COEFF is None:
        ratios = [
            e["base_h"] / _l1_km(data.NODES[e["from"]], data.NODES[e["to"]])
            for e in data.GRAPH_EDGES
            if _l1_km(data.NODES[e["from"]], data.NODES[e["to"]]) > 1e-9
        ]
        _COEFF = min(ratios)
    return _COEFF


def _manhattan_heuristic(goal: str):
    coeff = _manhattan_coeff()
    g = data.NODES[goal]
    return lambda n: coeff * _l1_km(data.NODES[n], g)


def _astar(
    adj: dict, start: str, goal: str, cost_fn, h
) -> tuple[list[dict] | None, float, int]:
    """A* with an admissible, consistent heuristic. Returns (path, cost,
    expansions). Same optimum as Dijkstra, fewer/equal node expansions.
    On the current 11-node graph the expansion count matches Dijkstra;
    the heuristic's benefit grows with graph size."""
    dist = {start: 0.0}
    prev: dict[str, tuple[str, dict]] = {}
    pq = [(h(start), 0.0, start)]
    seen = set()
    expansions = 0
    while pq:
        _, d, u = heapq.heappop(pq)
        if u in seen:
            continue
        seen.add(u)
        expansions += 1
        if u == goal:
            break
        for e in adj.get(u, []):
            v = e["to"]
            nd = d + cost_fn(e)
            if nd < dist.get(v, float("inf")):
                dist[v] = nd
                prev[v] = (u, e)
                heapq.heappush(pq, (nd + h(v), nd, v))
    if goal not in prev and goal != start:
        return None, float("inf"), expansions
    path: list[dict] = []
    u = goal
    while u != start:
        pu, e = prev[u]
        path.append(e)
        u = pu
    path.reverse()
    return path, dist[goal], expansions


def _path_cost(path: list[dict]) -> float:
    return sum(e["_w"] for e in path)


def k_shortest_paths(
    start: str,
    goal: str,
    weight_t: float,
    storm: bool = False,
    scenario: dict | None = None,
    objective: str = "time",
    k: int = 3,
) -> list[dict]:
    """Yen's K-shortest loopless paths. Each result carries the full
    forecast (time, delay, cost, CO2, risk) and a why-best note."""
    if start not in data.NODES or goal not in data.NODES:
        raise ValueError(f"unknown node {start} or {goal}")
    scenario = scenario or {}
    closed = frozenset(scenario.get("closed_nodes", []))
    if start in closed or goal in closed:
        return []

    adj = _adjacency(closed)
    # Weighted adjacency copy (never mutates shared data): each edge
    # carries its forecast-based weight for the chosen objective.
    wadj: dict[str, list[dict]] = {}
    for u, edges in adj.items():
        wadj[u] = []
        for e in edges:
            pred = model.predict_leg({**e, "id": e["id"]}, storm, scenario)
            w = (
                pred["cost_usd_per_t"]
                if objective == "cost"
                else pred["co2_kg_per_t"]
                if objective == "co2"
                else pred["predicted_h"]
            )
            wadj[u].append({**e, "_w": w, "_pred": pred})

    use_astar = objective == "time"
    h = _manhattan_heuristic(goal) if use_astar else (lambda n: 0.0)

    def dijkstra(a, s, g, banned_nodes=frozenset(), banned_edges=frozenset()):
        a2 = {
            u: [
                e
                for e in es
                if e["id"] not in banned_edges and e["to"] not in banned_nodes
            ]
            for u, es in a.items()
        }
        if use_astar:
            return _astar(a2, s, g, lambda e: e["_w"], h)
        path, cost = _dijkstra(a2, s, g, lambda e: e["_w"])
        return path, cost, 0

    first, _, first_exp = dijkstra(wadj, start, goal)
    if first is None:
        return []
    results = [first]
    candidates: list[tuple[float, int, list[dict]]] = []
    tie = count()  # stable heap order on equal costs (dicts are not comparable)
    for _ in range(k - 1):
        last = results[-1]
        for i in range(len(last)):
            spur_node = last[i]["from"]
            root = last[:i]
            banned_edges = set()
            banned_nodes = {n["from"] for n in root[1:]} | {n["to"] for n in root}
            for p in results:
                if len(p) > i and p[:i] == root:
                    banned_edges.add(p[i]["id"])
            spur, _, _ = dijkstra(wadj, spur_node, goal, banned_nodes, banned_edges)
            if spur is None:
                continue
            total = root + spur
            # avoid duplicate node sequences
            sig = tuple(e["id"] for e in total)
            if any(tuple(e["id"] for e in p) == sig for p in results):
                continue
            if any(tuple(e["id"] for e in p) == sig for _, _, p in candidates):
                continue
            heapq.heappush(candidates, (_path_cost(total), next(tie), total))
        if not candidates:
            break
        _, _, best = heapq.heappop(candidates)
        results.append(best)

    out = []
    for path in results:
        legs = [p["_pred"] for p in path]
        total_pred = round(sum(l["predicted_h"] for l in legs), 1)
        total_base = sum(l["base_h"] for l in legs)
        total_wait = round(sum(l.get("wait_h", 0) for l in legs), 1)
        delay = round(total_pred - total_base - total_wait, 1)
        prob = min(0.95, delay / (delay + 18)) if delay > 0 else 0.05
        if storm and any(l["mode"] == "sea" for l in legs):
            prob = max(prob, 0.85)
        mc = model.simulate_route(
            {"id": "search", "name": "", "legs": path}, weight_t, storm, scenario
        )
        # NOTE: predict_leg returns co2/cost PER TONNE; scale by weight.
        co2 = round(sum(l["co2_kg_per_t"] for l in legs) * weight_t)
        cost = round(sum(l["cost_usd_per_t"] for l in legs) * weight_t)
        out.append(
            {
                "path_id": " → ".join(e["id"] for e in path),
                "nodes": [start] + [e["to"] for e in path],
                "node_names": [data.NODES[start]["name"]]
                + [data.NODES[e["to"]]["name"] for e in path],
                "legs": legs,
                "total_base_h": total_base,
                "total_wait_h": total_wait,
                "total_predicted_h": total_pred,
                "delay_h": delay,
                "delay_probability": round(prob, 2),
                "co2_kg": co2,
                "cost_usd": cost,
                "modes": [e["mode"] for e in path],
                "has_conditional_legs": any(l.get("conditional") for l in legs),
                "eta_p50_h": mc["eta_p50_h"],
                "eta_p90_h": mc["eta_p90_h"],
                "search_algorithm": (
                    "A* (admissible Manhattan heuristic)" if use_astar else "Dijkstra"
                ),
                "expansions_first_search": first_exp,
            }
        )
    # Pareto filter on (time, cost, co2) AMONG THE RETURNED CANDIDATES.
    # This is a heuristic label, not a global multi-objective Pareto search.
    pareto = []
    for r in out:
        dominated = any(
            o is not r
            and o["total_predicted_h"] <= r["total_predicted_h"]
            and o["cost_usd"] <= r["cost_usd"]
            and o["co2_kg"] <= r["co2_kg"]
            and (
                o["total_predicted_h"] < r["total_predicted_h"]
                or o["cost_usd"] < r["cost_usd"]
                or o["co2_kg"] < r["co2_kg"]
            )
            for o in out
        )
        if not dominated:
            pareto.append(r)
    for r in out:
        r["pareto_optimal"] = r in pareto
        r["pareto_scope"] = "among returned k candidates (heuristic, not global Pareto)"
    return out
