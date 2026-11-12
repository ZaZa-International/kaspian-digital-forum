"""Routing semantics tests (cycle 5): Yen stability on equal weights,
loopless paths, unreachable/closed nodes, Enzeli connectivity, honest
Pareto scope, conditional-leg labelling.
"""
import pytest
from fastapi.testclient import TestClient

from app import data, graph
from app.main import app

client = TestClient(app)


def _edge(eid, frm, to, base_h=10, km=100):
    return {"id": eid, "mode": "rail", "from": frm, "to": to,
            "distance_km": km, "base_h": base_h, "port": None, "customs_h": 0}


@pytest.fixture
def synthetic_graph(monkeypatch):
    """Three equal-cost parallel paths + a cycle back to moscow."""
    edges = [
        _edge("e1", "moscow", "astrakhan"), _edge("e2", "astrakhan", "bandar_abbas"),
        _edge("e3", "moscow", "makhachkala"), _edge("e4", "makhachkala", "bandar_abbas"),
        _edge("e5", "moscow", "baku"), _edge("e6", "baku", "bandar_abbas"),
        _edge("e7", "astrakhan", "moscow"),  # cycle
    ]
    monkeypatch.setattr(data, "GRAPH_EDGES", edges)
    return edges


def test_equal_weights_no_typeerror_and_stable(synthetic_graph):
    a = graph.k_shortest_paths("moscow", "bandar_abbas", 48, objective="time", k=3)
    b = graph.k_shortest_paths("moscow", "bandar_abbas", 48, objective="time", k=3)
    assert len(a) == 3
    assert [p["path_id"] for p in a] == [p["path_id"] for p in b]
    assert len({p["path_id"] for p in a}) == 3
    # loopless despite the cycle edge
    for p in a:
        assert len(p["nodes"]) == len(set(p["nodes"]))


def test_unreachable_node_returns_empty(synthetic_graph):
    assert graph.k_shortest_paths("moscow", "enzeli", 48) == []
    assert graph.k_shortest_paths("enzeli", "moscow", 48) == []


def test_closed_edge_node_excluded(synthetic_graph):
    paths = graph.k_shortest_paths("moscow", "bandar_abbas", 48,
                                   scenario={"closed_nodes": ["astrakhan"]}, k=3)
    assert len(paths) == 2
    assert all("astrakhan" not in p["nodes"] for p in paths)
    assert graph.k_shortest_paths("moscow", "bandar_abbas", 48,
                                  scenario={"closed_nodes": ["moscow"]}) == []


def test_unknown_nodes_raise():
    with pytest.raises(ValueError):
        graph.k_shortest_paths("nope", "bandar_abbas", 48)


def test_enzeli_connected_in_real_graph():
    r = client.get("/api/routes/search?from_node=moscow&to_node=enzeli").json()
    assert r["paths"], "Enzeli must be reachable (case-listed port)"
    r2 = client.get("/api/routes/search?from_node=enzeli&to_node=bandar_abbas").json()
    assert r2["paths"]
    assert any(e["from"] == "makhachkala" and e["to"] == "baku" for e in data.GRAPH_EDGES)


def test_conditional_legs_labelled():
    fc = client.get("/api/routes/R1/forecast").json()
    sea = next(l for l in fc["legs"] if l["id"] == "R1-L2")
    assert sea["conditional"] is True and sea["note"]
    r2 = client.get("/api/routes/R2/forecast").json()
    assert next(l for l in r2["legs"] if l["id"] == "R2-L3")["conditional"] is True
    paths = client.get("/api/routes/search?from_node=moscow&to_node=enzeli").json()["paths"]
    assert any(p["has_conditional_legs"] for p in paths)


def test_pareto_scope_honest_and_deterministic():
    a = client.get("/api/routes/search?k=4").json()["paths"]
    b = client.get("/api/routes/search?k=4").json()["paths"]
    assert [p["path_id"] for p in a] == [p["path_id"] for p in b]
    for p in a:
        assert p["pareto_scope"].startswith("among returned")
        assert isinstance(p["pareto_optimal"], bool)
    # at least the objective-best path is non-dominated
    assert a[0]["pareto_optimal"] is True


def test_search_totals_match_forecast_semantics():
    paths = client.get("/api/routes/search?objective=time").json()["paths"]
    for p in paths:
        assert abs(p["total_predicted_h"] - p["total_base_h"] - p["total_wait_h"] - p["delay_h"]) < 0.2
        assert p["co2_kg"] > 0 and p["cost_usd"] > 0
