"""Persistence regression tests (cycle 2): SQLite store replaces racy JSON.

Covers: concurrent creates (no lost rows), idempotency, monotonic IDs,
legacy JSON migration (incl. corrupt file), concurrent hash-chain appends,
demo reset, restart persistence.
"""
import json
import threading

import pytest
from fastapi.testclient import TestClient

from app import main, model, store

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def _clean():
    store.reset_demo()
    yield
    store.reset_demo()


def _create(i, key=None):
    headers = {"Idempotency-Key": key} if key else {}
    return client.post("/api/shipments", json={
        "name": f"T{i}", "cargo": "c", "weight_t": 1, "route_id": "R1"},
        headers=headers)


def test_concurrent_creates_no_lost_rows():
    results = []
    lock = threading.Lock()

    def worker(i):
        r = _create(i)
        with lock:
            results.append(r)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(12)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert all(r.status_code == 201 for r in results)
    ids = [r.json()["id"] for r in results]
    assert len(set(ids)) == 12
    assert len(store.list_shipments()) == 12


def test_idempotency_key():
    r1 = _create(1, key="demo-key-1")
    r2 = _create(2, key="demo-key-1")
    assert r1.status_code == r2.status_code == 201
    assert r1.json()["id"] == r2.json()["id"]
    assert len(store.list_shipments()) == 1


def test_ids_monotonic_after_reset():
    first = _create(1).json()["id"]
    store.clear_shipments()
    second = _create(2).json()["id"]
    assert int(second.split("-")[1]) > int(first.split("-")[1])


def test_persists_across_connection_reload():
    sid = _create(1).json()["id"]
    # fresh connection (as after process restart) sees the row
    fresh = store.get_shipment(sid)
    assert fresh is not None and fresh["name"] == "T1"
    assert any(s["id"] == sid for s in client.get("/api/shipments").json()["shipments"])


def test_concurrent_ledger_appends_chain_valid():
    def worker():
        client.post("/api/clearance/submit", json={
            "shipment_id": "SHP-1044",
            "docs": {"invoice": "verified", "packing_list": "verified",
                     "certificate_of_origin": "verified", "insurance": "verified"}})

    threads = [threading.Thread(target=worker) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    entries = store.ledger_entries()
    assert len(entries) == 8
    seqs = [e["seq"] for e in entries]
    assert seqs == list(range(seqs[0], seqs[0] + 8))  # unique, gapless, ordered
    assert store.verify_ledger() is True


def test_legacy_json_migration(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "sct.db")
    monkeypatch.setattr(store, "_migrated", False)
    (tmp_path / "shipments.json").write_text(json.dumps([
        {"id": "SHP-2005", "name": "legacy", "cargo": "c", "weight_t": 5,
         "route_id": "R1", "current_leg": 0, "status": "создан",
         "eta": "2026-11-16T14:00:00+00:00", "docs": {"invoice": "pending"}}]),
        encoding="utf-8")
    (tmp_path / "ledger.json").write_text("{corrupt", encoding="utf-8")
    rows = store.list_shipments()
    assert [r["id"] for r in rows] == ["SHP-2005"]
    assert (tmp_path / "shipments.json.migrated").exists()
    # corrupt ledger left untouched, never silently imported
    assert (tmp_path / "ledger.json").read_text() == "{corrupt"
    # counter advanced past migrated id -> no collision
    new = store.create_shipment_record(
        name="n", cargo="c", weight_t=1, route_id="R1", docs={},
        eta="2026-11-16T14:00:00+00:00")
    assert int(new["id"].split("-")[1]) > 2005


def test_demo_reset_keeps_seeds():
    _create(1)
    client.post("/api/clearance/submit", json={"shipment_id": "SHP-1044", "docs": {}})
    r = client.post("/api/demo/reset")
    assert r.status_code == 200
    assert r.json()["shipments_removed"] == 1
    assert r.json()["ledger_entries_removed"] == 1
    seeds = client.get("/api/shipments").json()["shipments"]
    assert [s["id"] for s in seeds] == ["SHP-1042", "SHP-1043", "SHP-1044"]
    assert store.ledger_entries() == []


def test_ledger_tamper_detected():
    client.post("/api/clearance/submit", json={"shipment_id": "SHP-1044", "docs": {}})
    client.post("/api/clearance/submit", json={"shipment_id": "SHP-1043", "docs": {}})
    assert store.verify_ledger() is True
    # tamper: flip a decision in the middle of the chain
    first = store.ledger_entries()[0]
    flipped = "needs_documents" if first["decision"] == "pre-cleared" else "pre-cleared"
    with store._conn() as conn:
        conn.execute("UPDATE ledger SET decision=? WHERE seq=?", (flipped, first["seq"]))
    assert store.verify_ledger() is False
    # tamper: delete the first entry -> next entry's prev_hash no longer matches genesis
    store.reset_demo()
    client.post("/api/clearance/submit", json={"shipment_id": "SHP-1044", "docs": {}})
    client.post("/api/clearance/submit", json={"shipment_id": "SHP-1043", "docs": {}})
    e = store.ledger_entries()
    with store._conn() as conn:
        conn.execute("DELETE FROM ledger WHERE seq=?", (e[0]["seq"],))
    assert store.verify_ledger() is False
    # tamper: reorder entries (swap seq) -> chain breaks
    store.reset_demo()
    client.post("/api/clearance/submit", json={"shipment_id": "SHP-1044", "docs": {}})
    client.post("/api/clearance/submit", json={"shipment_id": "SHP-1043", "docs": {}})
    e = store.ledger_entries()
    with store._conn() as conn:
        conn.execute("UPDATE ledger SET seq=-1 WHERE seq=?", (e[0]["seq"],))
        conn.execute("UPDATE ledger SET seq=? WHERE seq=?", (e[0]["seq"], e[1]["seq"]))
        conn.execute("UPDATE ledger SET seq=? WHERE seq=-1", (e[1]["seq"],))
    assert store.verify_ledger() is False