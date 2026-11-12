"""Security & input-boundary regression tests (cycle 1).

Covers: physical-range validation, unknown scenario nodes, stored-XSS
payload handling, write token, body size limit, write rate limit.
"""
import pytest
from fastapi.testclient import TestClient

from app import main, model

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def _clean_state():
    main._rate_buckets.clear()
    yield
    main._rate_buckets.clear()
    model.clear_created_shipments()
    model.clear_ledger()


@pytest.mark.parametrize("qs", [
    "wave_m=-1", "wind_ms=-1", "wave_m=99", "wind_ms=999",
    "extra_customs_h=-5", "extra_customs_h=9999",
    "weight_t=0", "weight_t=-3", "weight_t=99999",
])
def test_invalid_scenario_params_rejected(qs):
    r = client.get(f"/api/routes/R1/forecast?{qs}")
    assert r.status_code == 422, r.text


def test_invalid_params_on_compare_and_search():
    assert client.get("/api/compare?weight_t=0").status_code == 422
    assert client.get("/api/compare?extra_customs_h=-1").status_code == 422
    assert client.get("/api/routes/search?weight_t=99999").status_code == 422
    assert client.get("/api/ml/forecast?route_id=R1&weight_t=-1").status_code == 422


def test_unknown_close_node_rejected():
    assert client.get("/api/routes/R1/forecast?close=nope").status_code == 422
    assert client.get("/api/routes/search?close=nope").status_code == 422
    assert client.get("/api/compare?close=moscow,nope").status_code == 422
    # known nodes still accepted
    assert client.get("/api/routes/R1/forecast?close=makhachkala").status_code == 422  # blocked route
    assert client.get("/api/routes/search?close=makhachkala").status_code == 200


def test_xss_payload_is_data_not_html():
    payload = "<img src=x onerror=alert(1)>"
    r = client.post("/api/shipments", json={
        "name": payload, "cargo": "<script>alert(2)</script>",
        "weight_t": 10, "route_id": "R1"})
    assert r.status_code == 201
    assert r.headers["content-type"].startswith("application/json")
    sid = r.json()["id"]
    s = client.get(f"/api/shipments/{sid}").json()
    # stored verbatim as data; rendering escapes it (see static/app.js esc())
    assert s["name"] == payload
    assert "<img" not in client.get("/").text  # index itself has no payload


def test_write_token_required_when_configured(monkeypatch):
    monkeypatch.setattr(main, "WRITE_TOKEN", "secret-test-token")
    body = {"name": "T", "cargo": "c", "weight_t": 1, "route_id": "R1"}
    assert client.post("/api/shipments", json=body).status_code == 401
    assert client.post("/api/shipments", json=body,
                       headers={"X-Write-Token": "wrong"}).status_code == 401
    r = client.post("/api/shipments", json=body,
                    headers={"X-Write-Token": "secret-test-token"})
    assert r.status_code == 201
    assert client.post("/api/clearance/submit",
                       json={"shipment_id": "SHP-1044", "docs": {}}).status_code == 401


def test_oversized_body_rejected():
    r = client.post("/api/shipments", content=b"x" * (main.MAX_BODY_BYTES + 1),
                    headers={"content-type": "application/json"})
    assert r.status_code == 413


def test_write_rate_limit(monkeypatch):
    monkeypatch.setattr(main, "WRITE_RATE_LIMIT", 3)
    codes = [client.post("/api/clearance/submit",
                         json={"shipment_id": "SHP-1044", "docs": {}}).status_code
             for _ in range(5)]
    assert codes[:3] == [200, 200, 200]
    assert codes[3:] == [429, 429]


def test_docs_field_bounded():
    big = {f"doc{i}": "verified" for i in range(25)}
    r = client.post("/api/shipments", json={
        "name": "T", "cargo": "c", "weight_t": 1, "route_id": "R1", "docs": big})
    assert r.status_code == 422
    r = client.post("/api/clearance/submit",
                    json={"shipment_id": "SHP-1044", "docs": {"k" * 60: "v"}})
    assert r.status_code == 422