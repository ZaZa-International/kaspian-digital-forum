"""Fleet summaries and operator task transitions on isolated SQLite state."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import main, ml, operations, store, weather

NOW = datetime(2026, 11, 13, 10, tzinfo=timezone.utc)
WORKSPACE = "/api/workspace"
TASKS = WORKSPACE + "/tasks"


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(operations, "_now", lambda: NOW)
    monkeypatch.setattr(weather, "cached_hourly_forecast", lambda: {"status": "unavailable", "segments": {}})
    monkeypatch.setattr(main, "WRITE_TOKEN", "")
    main._rate_buckets.clear()
    with TestClient(main.app) as client:
        yield client
    main._rate_buckets.clear()


def _create(client, key="create-task", **fields):
    return client.post(TASKS, headers={"Idempotency-Key": key}, json={
        "shipment_id": "SHP-1042", "title": "Сверить документы", **fields,
    })


def test_portfolio_uses_current_accepted_projection_and_remains_read_only(client, monkeypatch):
    def unexpected_ml(*args, **kwargs):
        raise AssertionError("Fleet overview must not train or call ML")

    monkeypatch.setattr(ml, "get_trained", unexpected_ml)
    response = client.get(WORKSPACE)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    view = response.json()
    assert view["summary"]["shipment_count"] == view["summary"]["active_count"] == 3
    assert view["summary"]["alerts_count"] == len(view["alerts"])
    assert view["analytics"]["demo_shipments_count"] == 3
    assert sum(group["count"] for group in view["analytics"]["by_status"]) == 3
    assert view["analytics"]["remaining_cost_usd"] == pytest.approx(sum(
        row["remaining_cost_usd"] for row in view["shipments"]
    ), abs=0.01)
    assert view["analytics"]["remaining_co2_kg"] == pytest.approx(sum(
        row["remaining_co2_kg"] for row in view["shipments"]
    ), abs=0.1)
    assert client.get(WORKSPACE).json() == view

    scenario = "?storm=true"
    detail = client.get("/api/operations/SHP-1042" + scenario).json()
    proposal = detail["proposals"][0]
    accepted = client.post("/api/operations/SHP-1042/accept" + scenario, headers={"Idempotency-Key": "move"}, json={
        "proposal_id": proposal["id"], "version": 1,
    })
    assert accepted.status_code == 200
    fresh = client.get(WORKSPACE + scenario).json()
    row = next(row for row in fresh["shipments"] if row["id"] == "SHP-1042")
    ops = client.get("/api/operations/SHP-1042" + scenario).json()
    assert row["plan_version"] == 2 and row["active_leg_ids"] == proposal["leg_ids"]
    assert "Баку" in row["route_name"] and "Махачкала" == row["current_node_name"]
    assert row["remaining_cost_usd"] == ops["baseline"]["total_usd"]
    assert row["remaining_co2_kg"] == ops["baseline"]["co2_kg"]
    assert fresh["analytics"]["source"] == "scenario"
    assert any(entry["kind"] == "route_plan_accepted" for entry in fresh["activity"])


def test_document_alerts_public_metadata_and_blocked_exclusion(client):
    uploaded = client.post("/api/operations/SHP-1042/documents", data={"kind": "invoice"}, files={
        "file": ("invoice.txt", b"Shipment ID: SHP-SECRET\nWeight: 50 t\n", "text/plain"),
    })
    assert uploaded.status_code == 201
    view = client.get(WORKSPACE + "?close=amirabad").json()
    row = next(row for row in view["shipments"] if row["id"] == "SHP-1042")
    passport = next(doc for doc in view["documents"] if doc["shipment_id"] == row["id"])
    assert row["eta"] is None and row["remaining_cost_usd"] is None and row["remaining_co2_kg"] is None
    assert row["risk"] == "critical"
    assert view["analytics"]["unprojected_shipments_count"] == 1
    assert view["analytics"]["projected_shipments_count"] == 2
    assert view["summary"]["blocked_count"] == 1
    kinds = {a["kind"] for a in view["alerts"] if a["shipment_id"] == row["id"]}
    assert {"documents_mismatch", "documents_missing", "documents_review", "route_blocked"} <= kinds
    assert passport["status"] == "mismatch"
    invoice = next(check for check in passport["requirements"] if check["key"] == "invoice")
    assert invoice["mismatch_count"] == 2
    assert "mismatches" not in invoice and "fields" not in invoice
    metadata = passport["files"][0]
    assert metadata["filename"] == "invoice.txt" and metadata["size_bytes"] > 0
    assert "fields" not in metadata and "sha256" not in metadata and "SHP-SECRET" not in str(view)
    assert any(entry["kind"] == "document_uploaded" for entry in view["activity"])


def test_review_only_documents_count_until_missing_fields_are_supplied(client):
    shipment_id = "SHP-1042"
    documents = f"/api/operations/{shipment_id}/documents"

    def upload(kind, content):
        response = client.post(documents, data={"kind": kind}, files={
            "file": (f"{kind}.txt", content, "text/plain"),
        })
        assert response.status_code == 201

    detail = client.get(f"/api/operations/{shipment_id}").json()
    for requirement in detail["documents"]["requirements"]:
        upload(requirement["key"], b"Shipment ID: SHP-1042\n")
    ready = client.get(WORKSPACE).json()
    ready_row = next(row for row in ready["shipments"] if row["id"] == shipment_id)
    assert ready_row["document_status"] == "ready_for_review"
    assert not any(alert["target"] == "documents" and alert["shipment_id"] == shipment_id for alert in ready["alerts"])

    upload("invoice", b"Invoice without a shipment identifier, cargo, weight or destination.")
    needs_review = client.get(WORKSPACE).json()
    row = next(row for row in needs_review["shipments"] if row["id"] == shipment_id)
    assert row["document_status"] == "review_required"
    assert {alert["kind"] for alert in needs_review["alerts"]
            if alert["target"] == "documents" and alert["shipment_id"] == shipment_id} == {"documents_review"}
    assert needs_review["summary"]["documents_blocked_count"] == ready["summary"]["documents_blocked_count"] + 1


def test_task_updates_persist_and_do_not_clear_underlying_alerts(client):
    before = client.get(WORKSPACE).json()
    first = _create(client, title="  Сверить  ", owner="  Ирина ", priority="high", due_at="2026-11-13T11:00:00+03:00")
    assert first.status_code == 201
    task = first.json()
    assert task["title"] == "Сверить" and task["owner"] == "Ирина"
    assert task["due_at"] == "2026-11-13T08:00:00+00:00" and task["version"] == 1
    assert task["status"] == "open" and task["note"] == ""
    assert _create(client, title="Сверить", owner="Ирина", priority="high", due_at="2026-11-13T08:00:00Z").json() == task
    assert _create(client, title="Другая задача").status_code == 409
    view = client.get(WORKSPACE).json()
    assert view["summary"]["active_tasks_count"] == view["summary"]["overdue_tasks_count"] == 1
    assert view["tasks"] == [task]

    url = f"{TASKS}/{task['id']}"
    progress = client.patch(url, json={"expected_version": 1, "status": "in_progress", "note": "Запросили инвойс"})
    assert progress.status_code == 200 and progress.json()["version"] == 2
    assert client.patch(url, json={"expected_version": 1, "status": "done"}).status_code == 409
    completed = client.patch(url, json={"expected_version": 2, "status": "done"})
    assert completed.status_code == 200 and completed.json()["version"] == 3
    view = client.get(WORKSPACE).json()
    assert view["summary"]["active_tasks_count"] == view["summary"]["overdue_tasks_count"] == 0
    assert view["alerts"] == before["alerts"]
    assert any(entry["kind"] == "task_completed" for entry in view["activity"])

    opened = client.patch(url, json={"expected_version": 3, "status": "open", "due_at": None})
    assert opened.status_code == 200 and opened.json()["due_at"] is None and opened.json()["version"] == 4
    assert client.patch(url, json={"expected_version": 4, "status": "open"}).json() == opened.json()
    with TestClient(main.app) as reloaded:
        assert reloaded.get(WORKSPACE).json()["tasks"] == [opened.json()]
    assert _create(client, title="Сверить", owner="Ирина", priority="high", due_at="2026-11-13T08:00:00Z").json() == opened.json()


def test_concurrent_duplicate_creates_and_competing_updates(client):
    with ThreadPoolExecutor(max_workers=2) as pool:
        writes = list(pool.map(lambda _: _create(client), range(2)))
    assert [r.status_code for r in writes] == [201, 201]
    task_ids = {r.json()["id"] for r in writes}
    assert len(task_ids) == 1
    url = f"{TASKS}/{task_ids.pop()}"
    with ThreadPoolExecutor(max_workers=2) as pool:
        edits = list(pool.map(lambda owner: client.patch(url, json={"expected_version": 1, "owner": owner}), ["Олег", "Мария"]))
    assert sorted(r.status_code for r in edits) == [200, 409]
    only, = client.get(WORKSPACE).json()["tasks"]
    assert only["version"] == 2 and only["owner"] in {"Олег", "Мария"}


def test_task_write_guard_input_validation_and_shipment_reset(client, monkeypatch):
    assert client.post(TASKS, json={"shipment_id": "SHP-1042", "title": "x"}).status_code == 422
    assert _create(client, key=" ").status_code == 422
    assert _create(client, shipment_id="SHP-UNKNOWN").status_code == 404
    for fields in ({"title": "  "}, {"due_at": "2026-11-13T12:00:00"}, {"due_at": 123},
                   {"priority": "other"}, {"status": "archived"}, {"arbitrary_column": "bad"}):
        assert _create(client, **fields).status_code == 422
    monkeypatch.setattr(main, "WRITE_TOKEN", "secret")
    assert _create(client).status_code == 401
    assert client.patch(f"{TASKS}/bad", json={"expected_version": 1, "status": "done"}).status_code == 401
    valid = client.post(TASKS, headers={"X-Write-Token": "secret", "Idempotency-Key": "protected"}, json={
        "shipment_id": "SHP-1042", "title": "Проверка",
    })
    assert valid.status_code == 201
    monkeypatch.setattr(main, "WRITE_TOKEN", "")
    url = f"{TASKS}/{valid.json()['id']}"
    for fields in ({"expected_version": 1}, {"expected_version": 1, "owner": None},
                   {"expected_version": 1, "shipment_id": "SHP-1043"}, {"expected_version": True, "note": "x"}):
        assert client.patch(url, json=fields).status_code == 422
    created = client.post("/api/shipments", json={"name": "Груз", "cargo": "Ящики", "weight_t": 12, "route_id": "R3"})
    shipment_id = created.json()["id"]
    assert _create(client, shipment_id=shipment_id).status_code == 201
    assert len(client.get(WORKSPACE).json()["tasks"]) == 2
    store.clear_shipments()
    assert len(client.get(WORKSPACE).json()["tasks"]) == 1
    assert client.post("/api/demo/reset").status_code == 200
    assert client.get(WORKSPACE).json()["tasks"] == []
    assert client.patch(url, json={"expected_version": 1, "status": "done"}).status_code == 404


def test_delivered_shipment_is_counted_without_open_projection_or_alerts(client):
    created = client.post("/api/shipments", json={"name": "Тест", "cargo": "Техника", "weight_t": 2, "route_id": "R3"})
    shipment_id = created.json()["id"]
    for i in range(1, 4):
        result = client.post(f"/api/shipments/{shipment_id}/events", json={
            "event_type": "arrival", "leg_id": f"R3-L{i}", "source": "operator-test",
            "actual_ts": (NOW - timedelta(hours=4 - i)).isoformat(),
        })
        assert result.status_code == 201
    view = client.get(WORKSPACE).json()
    row = next(row for row in view["shipments"] if row["id"] == shipment_id)
    assert row["demo"] is False and row["risk"] == "complete"
    assert view["summary"]["shipment_count"] == 4 and view["summary"]["delivered_count"] == 1
    assert view["analytics"]["projected_shipments_count"] == 3
    assert not any(alert["shipment_id"] == shipment_id for alert in view["alerts"])


def test_legacy_user_shipment_without_economics_and_sparse_upload_metadata(client):
    old_eta = "2026-11-16 11:30"
    shipment = store.create_shipment_record(
        name="Архив", cargo="Детали", weight_t=12, route_id="R3", docs={}, eta=old_eta,
    )
    with store._conn() as conn:
        conn.execute(
            "INSERT INTO operation_files (id,shipment_id,kind,metadata_json,content) VALUES (?,?,?,?,?)",
            ("old-file", shipment["id"], "invoice", json.dumps({"filename": "old.txt"}), b"old"),
        )
    view = client.get(WORKSPACE).json()
    row = next(row for row in view["shipments"] if row["id"] == shipment["id"])
    assert row["demo"] is False
    assert row["deadline"] == "2026-11-16T11:30:00+00:00"
    assert row["remaining_cost_usd"] > 0 and row["financial_risk_usd"] == 0
    passport = next(doc for doc in view["documents"] if doc["shipment_id"] == shipment["id"])
    assert passport["files"][0]["uploaded_at"] is None
    assert passport["files"][0]["id"] == "old-file" and passport["files"][0]["kind"] == "invoice"
    assert any(check["status"] == "review_required" for check in passport["requirements"])
