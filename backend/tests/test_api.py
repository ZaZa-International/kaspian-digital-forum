"""Smoke tests for Smart Caspian Transit. Run: pytest -q (from backend/)."""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app import model

client = TestClient(app)


@pytest.fixture(autouse=True)
def _isolated_runtime_state():
    model.clear_ledger()
    model.clear_created_shipments()
    yield
    model.clear_ledger()
    model.clear_created_shipments()


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_routes_and_nodes():
    routes = client.get("/api/routes").json()["routes"]
    assert [r["id"] for r in routes] == ["R1", "R2", "R3"]
    assert len(client.get("/api/nodes").json()["nodes"]) == 11


def test_forecast_base_math():
    r = client.get("/api/routes/R1/forecast?weight_t=48")
    assert r.status_code == 200
    body = r.json()
    # R1: 40 (rail) + 50 (sea, 930 km) + 32 (rail) = 122
    assert body["total_base_h"] == 122
    assert body["total_predicted_h"] > body["total_base_h"]
    assert body["bottleneck_leg_id"] in ("R1-L1", "R1-L2", "R1-L3")
    assert body["co2_kg"] > 0 and body["cost_usd"] > 0


def test_storm_makes_sea_routes_worse():
    calm = client.get("/api/compare?weight_t=48&storm=false").json()
    storm = client.get("/api/compare?weight_t=48&storm=true").json()
    calm_r1 = next(r for r in calm["routes"] if r["route_id"] == "R1")
    storm_r1 = next(r for r in storm["routes"] if r["route_id"] == "R1")
    assert storm_r1["delay_h"] > calm_r1["delay_h"]
    # Land route R3 has no sea legs: storm must not change it.
    calm_r3 = next(r for r in calm["routes"] if r["route_id"] == "R3")
    storm_r3 = next(r for r in storm["routes"] if r["route_id"] == "R3")
    assert storm_r3["delay_h"] == calm_r3["delay_h"]
    assert storm["best"]["risk"] == "R3"


def test_shipment_detail():
    r = client.get("/api/shipments/SHP-1042")
    assert r.status_code == 200
    assert r.json()["forecast"]["route_id"] == "R1"
    assert client.get("/api/shipments/NOPE").status_code == 404


def test_clearance_missing_docs_then_fixed():
    r = client.post("/api/clearance/submit", json={
        "shipment_id": "SHP-1043", "docs": {}})
    assert r.status_code == 200
    assert r.json()["decision"] == "needs_documents"
    assert r.json()["missing"] == ["phytosanitary"]
    r2 = client.post("/api/clearance/submit", json={
        "shipment_id": "SHP-1043", "docs": {"phytosanitary": "PHY-123"}})
    assert r2.json()["decision"] == "pre-cleared"
    r3 = client.post("/api/clearance/submit", json={
        "shipment_id": "SHP-1044", "docs": {}})
    assert r3.json()["decision"] == "pre-cleared"
    ledger = client.get("/api/clearance/ledger").json()
    assert len(ledger["entries"]) == 3
    assert ledger["chain_valid"] is True
    assert ledger["entries"][1]["prev_hash"] == ledger["entries"][0]["hash"]


def test_live_weather_and_corridor():
    r = client.get("/api/weather/live")
    assert r.status_code == 200
    body = r.json()
    assert set(body["segments"]) == {"caspian_north", "caspian_west"}
    for seg in body["segments"].values():
        assert seg["wave_m"] >= 0 and seg["wind_ms"] >= 0
        assert seg["source"].startswith(("live:", "demo"))
    stats = client.get("/api/corridor").json()["stats"]
    assert len(stats) == 6
    assert any("21,5" in s["value"] for s in stats)


def test_ml_info_and_forecast():
    info = client.get("/api/ml/info").json()
    assert info["n_train"] == 640 and info["n_test"] == 160
    assert info["metrics"]["random_forest"]["r2"] > 0.6
    assert info["metrics"]["random_forest"]["mae_h"] < 6
    calm = client.get("/api/ml/forecast?route_id=R1&weight_t=48").json()
    storm = client.get("/api/ml/forecast?route_id=R1&weight_t=48&storm=true").json()
    assert storm["ml_delay_h"] > calm["ml_delay_h"]
    assert calm["ml_delay_h"] >= 0
    assert calm["top_factors"][0]["feature"] in (
        "max_wave_m", "max_congestion_pct", "total_customs_h", "sea_km")
    r3 = client.get("/api/ml/forecast?route_id=R3&weight_t=48&storm=true").json()
    assert r3["features"]["sea_legs"] == 0
    assert client.get("/api/ml/forecast?route_id=RX").status_code == 404


def test_graph_search_and_scenario():
    r = client.get("/api/routes/search?from_node=moscow&to_node=bandar_abbas&weight_t=48")
    assert r.status_code == 200
    paths = r.json()["paths"]
    assert len(paths) >= 2
    assert paths[0]["total_predicted_h"] <= paths[1]["total_predicted_h"]
    # co2/cost are totals for the weight, not per-tonne values
    assert paths[0]["co2_kg"] > 1000 and paths[0]["cost_usd"] > 1000
    assert all(p["pareto_optimal"] in (True, False) for p in paths)
    # closing Makhachkala must kill the R1 path (via makhachkala)
    r2 = client.get("/api/routes/search?from_node=moscow&to_node=bandar_abbas&close=makhachkala")
    assert all("makhachkala" not in p["nodes"] for p in r2.json()["paths"])
    # closing the origin yields no paths
    r3 = client.get("/api/routes/search?from_node=moscow&to_node=bandar_abbas&close=moscow")
    assert r3.json()["paths"] == []
    # forecast of a blocked route is 422
    assert client.get("/api/routes/R1/forecast?close=makhachkala").status_code == 422
    assert client.get("/api/routes/search?from_node=nope&to_node=bandar_abbas").status_code == 404


def test_monte_carlo_p50_p90():
    calm = client.get("/api/routes/R1/forecast?weight_t=48").json()
    storm = client.get("/api/routes/R1/forecast?weight_t=48&storm=true").json()
    mc = calm["monte_carlo"]
    assert 0 <= calm["eta_p50_h"] <= calm["eta_p90_h"] <= calm["eta_p95_h"]
    assert storm["eta_p90_h"] > calm["eta_p90_h"]
    assert 0 <= mc["prob_delay_over_24h"] <= 1
    assert calm["co2_breakdown"] and sum(b["co2_kg"] for b in calm["co2_breakdown"]) > 0
    assert "ISO 14083" in calm["co2_method"]


def test_timeline_and_exceptions():
    tl = client.get("/api/shipments/SHP-1042/timeline").json()
    assert tl["shipment_id"] == "SHP-1042"
    assert len(tl["events"]) >= 5
    assert all(e["status"] in ("done", "in_progress", "planned") for e in tl["events"])
    assert tl["events"][0]["title"].startswith("Груз принят")
    ex = client.get("/api/exceptions").json()["exceptions"]
    assert isinstance(ex, list) and ex
    assert all("action" in e and "severity" in e for e in ex)
    assert any(e["shipment_id"] == "SHP-1043" for e in ex)  # missing phytosanitary
    assert client.get("/api/shipments/NOPE/timeline").status_code == 404


def test_ledger_persists_across_reload():
    model.clear_ledger()
    client.post("/api/clearance/submit", json={
        "shipment_id": "SHP-1044",
        "docs": {"invoice": "verified", "packing_list": "verified",
                 "certificate_of_origin": "verified", "insurance": "verified"}})
    reloaded = model._load_ledger()
    assert len(reloaded) == 1 and reloaded[0]["decision"] == "pre-cleared"
    assert model.verify_ledger() is True
    model.clear_ledger()
    assert model._load_ledger() == []


def test_ml_ensemble_and_attribution():
    info = client.get("/api/ml/info").json()
    assert "hist_gradient_boosting" in info["metrics"]
    assert "ensemble_rf_hgb" in info["metrics"]
    assert info["metrics"]["ensemble_rf_hgb"]["r2"] > 0.6
    fc = client.get("/api/ml/forecast?route_id=R1&weight_t=48").json()
    assert "ensemble_delay_h" not in fc  # ml_delay_h IS the ensemble
    assert fc["ml_model"].startswith("Ensemble")
    assert fc["rf_delay_h"] >= 0 and fc["hgb_delay_h"] >= 0
    assert len(fc["local_attribution"]) == 3
    assert all("contribution_h" in a for a in fc["local_attribution"])


def test_create_shipment():
    model.clear_created_shipments()
    r = client.post("/api/shipments", json={
        "name": "Тест", "cargo": "1 × 20ft", "weight_t": 20, "route_id": "R3"})
    assert r.status_code == 201
    sid = r.json()["id"]
    assert sid.startswith("SHP-")
    assert client.get(f"/api/shipments/{sid}").status_code == 200
    assert client.get(f"/api/shipments/{sid}/timeline").status_code == 200
    assert client.get(f"/api/ml/shipment/{sid}").status_code == 200
    assert any(s["id"] == sid for s in client.get("/api/shipments").json()["shipments"])
    assert client.post("/api/shipments", json={
        "name": "X", "cargo": "y", "weight_t": 1, "route_id": "RX"}).status_code == 404
    model.clear_created_shipments()


def test_frontend_served():
    r = client.get("/")
    assert r.status_code == 200 and "Smart Caspian Transit" in r.text
    assert client.get("/static/vendor/leaflet.js").status_code == 200
    assert client.get("/static/manifest.json").status_code == 200
    assert client.get("/sw.js").status_code == 200
