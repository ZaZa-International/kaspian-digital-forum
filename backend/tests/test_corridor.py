"""Signed corridor assessment through real document and dispatcher endpoints."""

import copy
import json
import stat
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import corridor, main, operations, store, weather

PATH = "/api/operations/SHP-1042"
ISSUER = "/api/operations/corridor/issuer"
NOW = datetime(2026, 11, 13, 8, tzinfo=timezone.utc)


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(operations, "_now", lambda: NOW)
    monkeypatch.setattr(weather, "cached_hourly_forecast", lambda: {"status": "unavailable", "segments": {}})
    monkeypatch.setattr(main, "WRITE_TOKEN", "")
    main._rate_buckets.clear()
    with TestClient(main.app) as c:
        yield c
    main._rate_buckets.clear()


def _overview(client):
    response = client.get(PATH + "/corridor")
    assert response.status_code == 200, response.text
    return response.json()


def _upload(client, kind, text):
    response = client.post(PATH + "/documents", data={"kind": kind}, files={"file": (f"{kind}.txt", text)})
    assert response.status_code == 201, response.text
    return response.json()["file"]


def _request(assessment):
    return {key: assessment[key] for key in ("plan_version", "evidence_hash")}


def _issue(client, assessment, key="corridor-issue", token=None):
    headers = {"Idempotency-Key": key}
    if token is not None:
        headers["X-Write-Token"] = token
    return client.post(PATH + "/corridor/issue", headers=headers, json=_request(assessment))


def _verify(client, receipt):
    response = client.post(PATH + "/corridor/verify", json=receipt)
    assert response.status_code == 200, response.text
    return response.json()


def test_documents_issue_verify_and_detect_tampering(client, tmp_path):
    before = _overview(client)
    assert before["issuer"] is None
    assert client.get(ISSUER).json() == {"issuer": None}
    assert not list(tmp_path.glob("*.ed25519"))
    invoice = next(rule for rule in before["assessment"]["rules"] if rule["key"] == "invoice")
    assert invoice["status"] == "review_required" and invoice["source"] == "demo"

    wrong = _upload(client, "invoice", "Shipment ID: SHP-1042\nWeight: 60 t\nDestination: Bandar Abbas")
    mismatch = _overview(client)["assessment"]
    assert mismatch["decision"] == "mismatch"
    invoice = next(rule for rule in mismatch["rules"] if rule["key"] == "invoice")
    assert invoice["document_sha256"] == wrong["sha256"]
    assert invoice["mismatches"][0]["field"] == "weight_t"
    issued_mismatch = _issue(client, mismatch, "mismatch").json()
    assert issued_mismatch["verification"]["status"] == "valid"
    assert issued_mismatch["receipt"]["payload"]["decision"] == "mismatch"
    assert issued_mismatch["receipt"]["payload"]["rules"][0]["mismatches"] == ["weight_t"]

    for rule in mismatch["rules"]:
        _upload(client, rule["key"], "Shipment ID: SHP-1042\nWeight: 48 t\nDestination: Bandar Abbas")
    ready = _overview(client)["assessment"]
    assert ready["decision"] == "ready_for_review"
    assert all(rule["status"] == "pass" for rule in ready["rules"])
    issued = _issue(client, ready).json()
    receipt = issued["receipt"]
    payload = receipt["payload"]
    assert payload["purpose"] == "pre_clearance_assessment" and payload["demo"] is True
    assert issued["verification"] == {
        "receipt_id": payload["id"], "signature_valid": True, "chain_valid": True, "current": True, "status": "valid",
    }
    assert payload["document_hashes"] == ready["document_hashes"]
    assert receipt["previous_hash"] == issued_mismatch["receipt"]["hash"]
    assert _verify(client, issued_mismatch["receipt"])["status"] == "stale"

    detail_path = PATH + "/corridor/receipts/" + payload["id"]
    assert client.get(detail_path).json() == issued
    download = client.get(detail_path + "?download=true")
    assert download.headers["content-disposition"] == f'attachment; filename="corridor-{payload["id"]}.json"'
    assert download.json() == receipt
    round_trip = json.loads(json.dumps(download.json(), ensure_ascii=True))
    assert _verify(client, round_trip)["status"] == "valid"
    for part, value in (("decision", "mismatch"), ("sequence", 99), ("shipment_id", "SHP-9999")):
        tampered = copy.deepcopy(receipt)
        tampered["payload"][part] = value
        verification = _verify(client, tampered)
        assert verification["status"] == "invalid" and not verification["signature_valid"]
    tampered = {**receipt, "previous_hash": "1" * 64}
    assert _verify(client, tampered)["status"] == "invalid"
    tampered_hash = _verify(client, {**receipt, "hash": "1" * 64})
    assert tampered_hash["signature_valid"] and not tampered_hash["chain_valid"]
    assert _verify(client, {"payload": ["invalid"], "signature": None})["status"] == "invalid"
    assert _verify(client, {"payload": {"id": "z" * 32}})["receipt_id"] is None
    assert client.post(PATH + "/corridor/verify", content='{"payload": {}, "payload": {}}').status_code == 422
    assert len(_overview(client)["receipts"]) == 2
    keyfile = next(tmp_path.glob("*.ed25519"))
    assert stat.S_IMODE(keyfile.stat().st_mode) == 0o600
    issuer = client.get(ISSUER).json()["issuer"]
    assert issuer == _overview(client)["issuer"]
    assert set(issuer) == {"algorithm", "key_id", "public_key"}
    assert issuer["key_id"] == receipt["key_id"]


def test_write_guard_stale_inputs_and_idempotency(client, monkeypatch):
    before = _overview(client)["assessment"]
    _upload(client, "invoice", "Shipment ID: SHP-1042")
    after = _overview(client)["assessment"]
    monkeypatch.setattr(main, "WRITE_TOKEN", "test-token")
    assert _issue(client, before).status_code == 401
    assert _issue(client, before, token="test-token").status_code == 409
    assert _issue(client, after, key=" ", token="test-token").status_code == 422
    assert client.post(PATH + "/corridor/issue", headers={"X-Write-Token": "test-token"}, json=_request(after)).status_code == 422
    assert client.get(ISSUER).json()["issuer"] is None

    issued = _issue(client, after, token="test-token")
    assert issued.status_code == 201
    assert _issue(client, after, token="test-token").json() == issued.json()
    assert _verify(client, issued.json()["receipt"])["status"] == "valid"
    assert len(_overview(client)["receipts"]) == 1
    monkeypatch.setattr(main, "WRITE_TOKEN", "")
    _upload(client, "invoice", "Shipment ID: SHP-1042\nWeight: 48 t")
    newer = _overview(client)["assessment"]
    retry = _issue(client, after).json()
    assert retry["receipt"] == issued.json()["receipt"]
    assert retry["verification"]["status"] == "stale"
    assert _issue(client, newer).status_code == 409


def test_plan_change_and_restart_preserve_old_signature(client):
    assessment = _overview(client)["assessment"]
    receipt = _issue(client, assessment).json()["receipt"]
    issuer = client.get(ISSUER).json()
    proposal = client.get(PATH + "?storm=true").json()["proposals"][0]
    accepted = client.post(PATH + "/accept?storm=true", json={
        "proposal_id": proposal["id"], "version": proposal["version"],
    }, headers={"Idempotency-Key": "corridor-reroute"})
    assert accepted.status_code == 200
    current = _overview(client)["assessment"]
    assert current["plan_version"] == assessment["plan_version"] + 1
    assert "road_waybill" in {rule["key"] for rule in current["rules"]}
    assert "bill_of_lading" not in {rule["key"] for rule in current["rules"]}
    assert _issue(client, assessment, "old-version").status_code == 409
    with TestClient(main.app) as restarted:
        assert restarted.get(ISSUER).json() == issuer
        verification = _verify(restarted, receipt)
        assert verification["signature_valid"] and verification["chain_valid"] and verification["status"] == "stale"


def test_parallel_issues_link_and_chain_corruption_is_detected(client):
    assessment = _overview(client)["assessment"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        issues = list(pool.map(lambda i: _issue(client, assessment, f"parallel-{i}"), range(4)))
    assert all(response.status_code == 201 for response in issues)
    receipts = sorted((r.json()["receipt"] for r in issues), key=lambda row: row["payload"]["sequence"])
    assert [receipt["payload"]["sequence"] for receipt in receipts] == [1, 2, 3, 4]
    assert [receipt["previous_hash"] for receipt in receipts] == [corridor.GENESIS, *(r["hash"] for r in receipts[:-1])]
    assert len({receipt["key_id"] for receipt in receipts}) == 1
    assert _overview(client)["chain_valid"]
    wrong_shipment = client.post("/api/operations/SHP-1043/corridor/verify", json=receipts[0]).json()
    assert wrong_shipment["signature_valid"] and not wrong_shipment["chain_valid"]

    broken = {**receipts[0], "signature": "f" * 128}
    with store._conn() as conn:
        conn.execute("UPDATE corridor_receipts SET receipt_json=? WHERE id=?",
                     (json.dumps(broken), receipts[0]["payload"]["id"]))
    verification = _verify(client, receipts[-1])
    assert verification["signature_valid"] and not verification["chain_valid"] and verification["status"] == "invalid"
    assert not _overview(client)["chain_valid"]
    assert _issue(client, assessment, "after-corruption").status_code == 409


def test_reset_removes_receipts_and_preserves_issuer_identity(client):
    receipt = _issue(client, _overview(client)["assessment"]).json()["receipt"]
    issuer = client.get(ISSUER).json()
    assert client.post("/api/demo/reset").status_code == 200
    assert _overview(client)["receipts"] == []
    assert client.get(ISSUER).json() == issuer
    result = _verify(client, receipt)
    assert result["signature_valid"] and not result["chain_valid"] and result["status"] == "invalid"
