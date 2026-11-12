"""Pre-clearance provenance tests (cycle 4).

Statuses are assigned by server-side simulated authority rules, never by a
client 'verified' field. Missing/unknown/rejected/expired documents never
become clearance. Ledger entries carry issuer/rule_version/doc snapshot and
remain tamper-evident.
"""
import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from app import model, store
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _clean():
    model.clear_ledger()
    yield
    model.clear_ledger()


def _docs(sid):
    return {d["doc_type"]: d for d in client.get(f"/api/clearance/documents/{sid}").json()["documents"]}


def test_missing_doc_never_clears_without_submission():
    r = client.post("/api/clearance/submit", json={"shipment_id": "SHP-1043", "docs": {}})
    assert r.json()["decision"] == "needs_documents"
    assert r.json()["missing"] == ["phytosanitary"]
    assert _docs("SHP-1043")["phytosanitary"]["status"] == "missing"


def test_empty_rejected_expired_refs_do_not_clear():
    for ref, expected in (("", "missing"), ("REJ-1", "rejected"), ("EXP-1", "expired")):
        r = client.post("/api/clearance/submit",
                        json={"shipment_id": "SHP-1043", "docs": {"phytosanitary": ref}})
        assert r.json()["decision"] == "needs_documents", ref
        assert _docs("SHP-1043")["phytosanitary"]["status"] == expected


def test_authority_assigns_status_with_provenance():
    r = client.post("/api/clearance/submit",
                    json={"shipment_id": "SHP-1043", "docs": {"phytosanitary": "PHY-9"}})
    assert r.json()["decision"] == "pre-cleared"
    doc = _docs("SHP-1043")["phytosanitary"]
    assert doc["status"] == "verified"
    assert doc["issuer"] == model.CLEARANCE_ISSUER
    assert doc["source"] == "client (demo)"
    assert doc["rule_version"] == model.DOC_RULE_VERSION
    assert doc["content_hash"] == hashlib.sha256(b"PHY-9").hexdigest()
    assert doc["decided_at"]
    entry = r.json()
    assert entry["issuer"] == model.CLEARANCE_ISSUER
    assert entry["rule_version"] == model.DOC_RULE_VERSION
    assert any(d["doc_type"] == "phytosanitary" and d["status"] == "verified"
               for d in entry["docs"])


def test_unknown_doc_type_does_not_replace_required():
    r = client.post("/api/clearance/submit",
                    json={"shipment_id": "SHP-1043", "docs": {"mystery_doc": "X-1"}})
    assert r.json()["decision"] == "needs_documents"
    assert _docs("SHP-1043")["mystery_doc"]["status"] == "unknown"


def test_exception_disappears_after_clearance():
    ex = client.get("/api/exceptions").json()["exceptions"]
    assert any(e.get("shipment_id") == "SHP-1043" and "phytosanitary" in e["title"] for e in ex)
    client.post("/api/clearance/submit",
                json={"shipment_id": "SHP-1043", "docs": {"phytosanitary": "PHY-9"}})
    ex = client.get("/api/exceptions").json()["exceptions"]
    assert not any(e.get("shipment_id") == "SHP-1043" and "phytosanitary" in e["title"] for e in ex)


def test_ledger_tamper_issuer_detected():
    client.post("/api/clearance/submit", json={"shipment_id": "SHP-1044", "docs": {}})
    assert store.verify_ledger() is True
    with store._conn() as conn:
        conn.execute("UPDATE ledger SET issuer='fake-authority' WHERE seq=(SELECT MIN(seq) FROM ledger)")
    assert store.verify_ledger() is False


def test_legacy_ledger_entry_still_verifies():
    prev = "0" * 64
    payload = f"{prev}|SHP-1044|pre-cleared|abc123"
    h = hashlib.sha256(payload.encode()).hexdigest()
    with store._conn() as conn:
        conn.execute(
            "INSERT INTO ledger (shipment_id,decision,missing_json,doc_hash,prev_hash,hash,ts) "
            "VALUES ('SHP-1044','pre-cleared','[]','abc123',?,?, '2026-01-01T00:00:00+00:00')",
            (prev, h))
    assert store.verify_ledger() is True


def test_documents_endpoint_404():
    assert client.get("/api/clearance/documents/NOPE").status_code == 404
    body = client.get("/api/clearance/documents/SHP-1042").json()
    assert body["rule_version"] == model.DOC_RULE_VERSION
    assert len(body["documents"]) == 4
    assert all(d["issuer"] for d in body["documents"] if d["status"] == "verified")