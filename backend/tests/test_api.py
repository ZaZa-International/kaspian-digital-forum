"""Smoke tests for Smart Caspian Transit. Run: pytest -q (from backend/)."""
from fastapi.testclient import TestClient

from app.main import app
from app import model

client = TestClient(app)


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
    assert body["total_base_h"] == 100
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
    model.LEDGER.clear()
    r = client.post("/api/clearance/submit", json={
        "shipment_id": "SHP-1043",
        "docs": {"invoice": "verified", "packing_list": "verified",
                 "certificate_of_origin": "pending", "insurance": "verified",
                 "phytosanitary": "missing"}})
    assert r.status_code == 200
    assert r.json()["decision"] == "needs_documents"
    assert "phytosanitary" in r.json()["missing"]
    r2 = client.post("/api/clearance/submit", json={
        "shipment_id": "SHP-1044",
        "docs": {"invoice": "verified", "packing_list": "verified",
                 "certificate_of_origin": "verified", "insurance": "verified"}})
    assert r2.json()["decision"] == "pre-cleared"
    ledger = client.get("/api/clearance/ledger").json()
    assert len(ledger["entries"]) == 2
    assert ledger["chain_valid"] is True
    assert ledger["entries"][1]["prev_hash"] == ledger["entries"][0]["hash"]


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


def test_frontend_served():
    r = client.get("/")
    assert r.status_code == 200 and "Smart Caspian Transit" in r.text
