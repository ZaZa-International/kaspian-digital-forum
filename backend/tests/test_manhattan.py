"""Manhattan (L1) package: W1 storm price, L1 extrapolation alarm + kNN
analogs, and A* with an admissible Manhattan heuristic.

All metrics are simulation/synthetic (see docs/PITCH.md). Tests assert
structural properties — admissibility, optimality, calm/storm separation,
land immunity — not real-world accuracy or fragile exact numbers.
"""

import math

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import data, graph, ml, model
from app.main import app

client = TestClient(app)


# --- W1 storm price ---------------------------------------------------------
def test_w1_distance_basic():
    a = np.array([1.0, 2.0, 3.0])
    assert model._w1_distance(a, a) == 0.0
    b = np.array([4.0, 5.0, 6.0])  # shifted by 3
    assert model._w1_distance(a, b) == pytest.approx(3.0)
    assert model._w1_distance(a, b) == pytest.approx(model._w1_distance(b, a))


def test_storm_price_land_immune_sea_pays():
    r3 = model.storm_price_h(data.get_route("R3"))["storm_price_h"]
    r1 = model.storm_price_h(data.get_route("R1"))["storm_price_h"]
    r2 = model.storm_price_h(data.get_route("R2"))["storm_price_h"]
    assert r3 == pytest.approx(0.0, abs=0.5)  # land route immune to the storm
    assert r1 > 20.0 and r2 > 20.0  # sea routes pay the full shift


def test_storm_price_in_compare_api():
    body = client.get("/api/compare?weight_t=48").json()
    for row in body["routes"]:
        assert "storm_price_h" in row and row["storm_price_h"] >= 0
    by_id = {r["route_id"]: r["storm_price_h"] for r in body["routes"]}
    assert by_id["R3"] < by_id["R1"]


def test_storm_price_reproducible():
    r = data.get_route("R1")
    assert (
        model.storm_price_h(r)["storm_price_h"]
        == model.storm_price_h(r)["storm_price_h"]
    )


# --- L1 extrapolation alarm + kNN analogs -----------------------------------
def test_extrapolation_calm_inside_storm_outside():
    r1 = data.get_route("R1")
    calm = ml.predict_delay(r1, 48, False)["extrapolation"]
    storm = ml.predict_delay(r1, 48, True)["extrapolation"]
    assert calm["is_extrapolating"] is False
    assert storm["is_extrapolating"] is True
    assert storm["l1_to_median"] > calm["l1_to_median"]
    assert calm["l1_to_median"] <= calm["train_p95"]
    assert storm["l1_to_median"] > storm["train_p95"]


def test_l1_analogs_structure():
    analogs = ml.predict_delay(data.get_route("R1"), 48, True)["analogs"]
    assert len(analogs) == 3
    assert analogs[0]["l1"] <= analogs[1]["l1"] <= analogs[2]["l1"]
    for a in analogs:
        assert a["delay_h"] >= 0 and a["l1"] >= 0


def test_ml_api_exposes_extrapolation_and_analogs():
    body = client.get("/api/ml/forecast?route_id=R1&storm=true").json()
    assert body["extrapolation"]["is_extrapolating"] is True
    assert len(body["analogs"]) == 3
    assert "synthetic" in body["analogs_kind"]


# --- A* admissible Manhattan heuristic --------------------------------------
def test_heuristic_admissible_on_all_edges():
    coeff = graph._manhattan_coeff()
    for e in data.GRAPH_EDGES:
        l1 = graph._l1_km(data.NODES[e["from"]], data.NODES[e["to"]])
        assert coeff * l1 <= e["base_h"] + 1e-9


def test_l1_ge_haversine_all_pairs():
    nodes = list(data.NODES.values())
    for i, a in enumerate(nodes):
        for b in nodes[i + 1 :]:
            la1, lo1, la2, lo2 = map(
                math.radians, (a["lat"], a["lon"], b["lat"], b["lon"])
            )
            h = (
                math.sin((la2 - la1) / 2) ** 2
                + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
            )
            hav = 2 * 6371.0 * math.asin(math.sqrt(h))
            assert graph._l1_km(a, b) >= hav - 1e-6


def test_astar_same_optimum_as_dijkstra():
    adj = graph._adjacency(frozenset())
    wadj = {
        u: [
            {
                **e,
                "_w": model.predict_leg({**e, "id": e["id"]}, False, {})["predicted_h"],
            }
            for e in es
        ]
        for u, es in adj.items()
    }
    _, dc = graph._dijkstra(wadj, "moscow", "bandar_abbas", lambda e: e["_w"])
    _, ac, exp = graph._astar(
        wadj,
        "moscow",
        "bandar_abbas",
        lambda e: e["_w"],
        graph._manhattan_heuristic("bandar_abbas"),
    )
    assert ac == pytest.approx(dc)
    assert exp >= 1


def test_search_api_algorithm_metadata():
    body = client.get("/api/routes/search?objective=time").json()
    p = body["paths"][0]
    assert p["search_algorithm"].startswith("A*")
    assert p["expansions_first_search"] >= 1
    cost = client.get("/api/routes/search?objective=cost").json()["paths"][0]
    assert cost["search_algorithm"] == "Dijkstra"


# --- visualization endpoints (ETA distribution + Pareto frontier) -----------
def test_eta_distribution_api():
    body = client.get("/api/routes/R1/eta-distribution?weight_t=48").json()
    assert body["bins"] > 0
    assert len(body["edges"]) == body["bins"] + 1
    assert sum(body["calm"]["counts"]) == body["n"]
    assert sum(body["storm"]["counts"]) == body["n"]
    assert body["storm_price_h"] >= 0
    assert body["storm"]["p90"] >= body["calm"]["p90"]  # storm shifts ETA right


def test_eta_distribution_unknown_route():
    assert client.get("/api/routes/NOPE/eta-distribution").status_code == 404


def test_pareto_api_frontier_nondominated():
    body = client.get("/api/routes/pareto").json()
    pts = body["points"]
    assert pts and any(p["pareto"] for p in pts)
    assert pts == sorted(pts, key=lambda p: p["time_h"])
    front = [p for p in pts if p["pareto"]]
    for p in front:
        for o in pts:
            if o is p:
                continue
            dominates = (
                o["time_h"] <= p["time_h"]
                and o["cost_usd"] <= p["cost_usd"]
                and o["co2_kg"] <= p["co2_kg"]
                and (
                    o["time_h"] < p["time_h"]
                    or o["cost_usd"] < p["cost_usd"]
                    or o["co2_kg"] < p["co2_kg"]
                )
            )
            assert not dominates
