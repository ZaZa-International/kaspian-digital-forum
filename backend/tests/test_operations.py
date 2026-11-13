"""Dispatcher workflows against isolated SQLite state."""

import hashlib
import io
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app import main, operation_documents, operations, store, weather

NOW = datetime(2026, 11, 12, 23, 0, tzinfo=timezone.utc)
OPERATIONS = "/api/operations/SHP-1042"
EVENTS = "/api/shipments/SHP-1042/events"


@pytest.fixture
def client(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(operations, "_now", lambda: NOW)
    monkeypatch.setattr(weather, "cached_hourly_forecast", lambda: {"status": "unavailable", "segments": {}})
    monkeypatch.setattr(main, "WRITE_TOKEN", "")
    main._rate_buckets.clear()
    with TestClient(main.app) as client:
        yield client
    main._rate_buckets.clear()


def _accept(client, proposal, key="plan-accept", query="?storm=true"):
    return client.post(
        OPERATIONS + "/accept" + query,
        json={"proposal_id": proposal["id"], "version": proposal["version"]},
        headers={"Idempotency-Key": key},
    )


def _event(client, kind, leg_id=None, **fields):
    return client.post(EVENTS, json={
        "event_type": kind, "leg_id": leg_id, "source": "test operator", **fields,
    })


def test_storm_business_case_and_persisted_version(client):
    calm = client.get(OPERATIONS).json()
    storm = client.get(OPERATIONS + "?storm=true").json()
    candidate = storm["proposals"][0]
    assert calm["shipment"]["late_h"] == 0
    assert storm["shipment"]["late_h"] > 45
    assert candidate["from_node"] == "makhachkala"
    assert candidate["nodes"] == ["makhachkala", "baku", "astara", "bandar_abbas"]
    assert candidate["delta_h"] < -100 and candidate["delta_usd"] < -3500
    assert candidate["transfer_usd"] == 430
    assert candidate["total_usd"] == pytest.approx(sum(candidate[key] for key in (
        "transport_usd", "idle_usd", "late_usd", "transfer_usd",
    )), abs=0.01)
    assert {"bill_of_lading", "rail_waybill"} <= {r["key"] for r in storm["documents"]["requirements"]}
    assert any(i["type"] == "sea_disruption" for i in storm["horizon"]["impacts"])

    accepted = _accept(client, candidate)
    assert accepted.status_code == 200
    saved = accepted.json()
    assert saved["plan"]["version"] == 2
    assert saved["plan"]["leg_ids"] == ["R1-L1", *candidate["leg_ids"]]
    assert saved["plan"]["completed_leg_ids"] == ["R1-L1"]
    assert saved["decision"]["event_type"] == "route_plan_accepted"
    assert _accept(client, candidate).json() == saved
    assert _accept(client, candidate, key="new-key").status_code == 409
    assert _accept(client, candidate, query="").status_code == 409

    with TestClient(main.app) as new_session:
        detail = new_session.get(OPERATIONS).json()
    assert detail["plan"]["leg_ids"] == saved["plan"]["leg_ids"]
    assert detail["decisions"] == [saved["decision"]]
    assert [e["id"] for e in detail["events"] if e["event_type"] == "route_plan_accepted"] == [saved["decision"]["id"]]
    assert detail["shipment"]["current_leg_id"] == "G-MAH-BAK"
    requirements = {r["key"] for r in detail["documents"]["requirements"]}
    assert "road_waybill" in requirements and "bill_of_lading" not in requirements


def test_accept_concurrent_requests_and_reset(client):
    proposals = client.get(OPERATIONS + "?storm=true").json()["proposals"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda i: _accept(client, proposals[i], f"parallel-{i}"), [0, 1]))
    assert sorted(response.status_code for response in responses) == [200, 409]
    assert len(client.get(OPERATIONS).json()["decisions"]) == 1
    assert client.post("/api/demo/reset").status_code == 200
    assert client.get(OPERATIONS).json()["plan"]["version"] == 1


def test_events_move_current_node_and_change_eta_without_rewinding(client):
    before = client.get(OPERATIONS + "?storm=true").json()
    stale = before["proposals"][0]
    assert _event(client, "departure").status_code == 422
    assert _event(client, "arrival", "R1-L3").status_code == 409
    assert _event(client, "departure", "R1-L2", location="Энзели").status_code == 422
    assert _event(
        client, "departure", "R1-L2", actual_ts=(NOW - timedelta(hours=1)).isoformat(),
    ).status_code == 201
    assert _accept(client, stale).status_code == 409
    moving = client.get(OPERATIONS).json()
    assert moving["shipment"]["progress_status"] == "in_transit"
    assert moving["shipment"]["current_node"] is None
    assert moving["proposals"] == []
    departure = next(e for e in moving["events"] if e["event_type"] == "departure" and e["leg_id"] == "R1-L2")
    assert _event(
        client, "departure", "R1-L2", correction_of=departure["id"], planned_ts=NOW.isoformat(),
    ).status_code == 409

    assert _event(client, "delay", "R1-L2", delay_h=6, actual_ts=NOW.isoformat()).status_code == 201
    delayed = client.get(OPERATIONS).json()
    assert delayed["baseline"]["remaining_h"] == moving["baseline"]["remaining_h"] + 6
    arrival = _event(client, "arrival", "R1-L2", actual_ts=NOW.isoformat())
    assert arrival.status_code == 201
    arrived = client.get(OPERATIONS).json()
    assert arrived["shipment"]["current_node"] == "amirabad"
    assert arrived["plan"]["completed_leg_ids"] == ["R1-L1", "R1-L2"]
    assert arrived["baseline"]["remaining_h"] < delayed["baseline"]["remaining_h"]
    assert _event(
        client, "arrival", "R1-L2", correction_of=arrival.json()["id"], planned_ts=NOW.isoformat(),
    ).status_code == 409

    assert _event(client, "arrival", "R1-L3", actual_ts=NOW.isoformat()).status_code == 201
    delivered = client.get(OPERATIONS).json()
    assert delivered["shipment"]["progress_status"] == "delivered"
    assert delivered["shipment"]["eta"] == NOW.isoformat()
    assert delivered["baseline"]["remaining_h"] == 0 and delivered["proposals"] == []


def test_economics_stale_proposals_and_write_guard(client, monkeypatch):
    old = client.get(OPERATIONS + "?storm=true").json()["proposals"][0]
    inputs = {
        "deadline": (NOW + timedelta(hours=50)).isoformat(),
        "idle_cost_per_day": 240, "late_penalty_per_day": 2400, "reroute_transfer_cost": 500,
    }
    monkeypatch.setattr(main, "WRITE_TOKEN", "operator")
    assert client.put(OPERATIONS + "/economics", json=inputs).status_code == 401
    assert client.post(OPERATIONS + "/documents", data={"kind": "invoice"}, files={
        "file": ("test.txt", b"valid", "text/plain"),
    }).status_code == 401
    updated = client.put(OPERATIONS + "/economics", json=inputs, headers={"X-Write-Token": "operator"})
    assert updated.status_code == 200
    assert updated.json() == {**inputs, "currency": "USD", "source": "operator"}
    monkeypatch.setattr(main, "WRITE_TOKEN", "")
    assert _accept(client, old).status_code == 409
    detail = client.get(OPERATIONS + "?storm=true").json()
    assert detail["economics"] == updated.json()
    assert detail["baseline"]["idle_usd"] == detail["baseline"]["idle_h"] * 10
    assert detail["baseline"]["late_usd"] == pytest.approx(detail["baseline"]["late_h"] * 100)
    assert detail["proposals"][0]["transfer_usd"] == 500


def test_passport_upload_mismatches_private_storage_and_retry(client):
    path = OPERATIONS + "/documents"
    bad = b"Shipment ID: SHP-0000\nWeight: 50000 kg\nDestination: Tehran\n"
    response = client.post(path, data={"kind": "invoice"}, files={
        "file": ("../../invoice.txt", bad, "text/plain"),
    })
    assert response.status_code == 201
    file = response.json()["file"]
    assert file["filename"] == "invoice.txt"
    assert file["sha256"] == hashlib.sha256(bad).hexdigest()
    assert file["extraction_source"] == "UTF-8"
    assert file["fields"]["weight_t"] == 50
    assert {m["field"] for m in file["mismatches"]} == {"shipment_id", "weight_t", "destination"}
    assert client.get(OPERATIONS).json()["documents"]["status"] == "mismatch"
    with sqlite3.connect(store.DB_PATH) as conn:
        assert conn.execute("SELECT content FROM operation_files").fetchone()[0] == bad
    assert client.get("/static/invoice.txt").status_code == 404
    duplicate = client.post(path, data={"kind": "invoice"}, files={"file": ("invoice.txt", bad)})
    assert duplicate.json()["file"]["id"] == file["id"]

    good = b"Shipment ID: SHP-1042\nWeight (kg): 48000\nDestination: Bandar Abbas\n"
    corrected = client.post(path, data={"kind": "invoice"}, files={"file": ("invoice.txt", good)})
    assert corrected.status_code == 201
    invoice = next(r for r in corrected.json()["documents"]["requirements"] if r["key"] == "invoice")
    assert invoice["status"] == "matched" and invoice["mismatches"] == []
    assert corrected.json()["documents"]["status"] != "ready_for_review"
    assert client.post(path, data={"kind": "invoice"}, files={"file": ("invoice.html", b"<p>")}).status_code == 415
    assert client.post(path, data={"kind": "invoice"}, files={"file": ("invoice.png", b"fake")}).status_code == 422
    assert client.post(path, data={"kind": "invoice"}, files={
        "file": ("invoice.txt", b"x" * (2 * 1024 * 1024 + 1)),
    }).status_code == 413
    assert client.post("/api/shipments", content=iter([b"x" * main.MAX_BODY_BYTES, b"x"])).status_code == 413
    assert client.post("/api/demo/reset").status_code == 200
    assert client.get(OPERATIONS).json()["documents"]["files"] == []


def test_pdf_upload_reads_text_layer_and_handles_truncated_file(client, monkeypatch):
    writer = PdfWriter()
    page = writer.add_blank_page(width=595, height=842)
    name = NameObject
    page[name("/Resources")] = DictionaryObject({
        name("/Font"): DictionaryObject({
            name("/F1"): DictionaryObject({
                name("/Type"): name("/Font"),
                name("/Subtype"): name("/Type1"),
                name("/BaseFont"): name("/Helvetica"),
            }),
        }),
    })
    text = DecodedStreamObject()
    text.set_data(
        b"BT /F1 12 Tf 24 800 Td (Shipment ID: SHP-1042) Tj "
        b"0 -18 Td (Weight: 48 t) Tj 0 -18 Td (Destination: Bandar Abbas) Tj ET"
    )
    page[name("/Contents")] = text.flate_encode()
    output = io.BytesIO()
    writer.write(output)

    url = OPERATIONS + "/documents"
    response = client.post(
        url, data={"kind": "invoice"}, files={"file": ("invoice.pdf", output.getvalue(), "application/pdf")},
    )
    assert response.status_code == 201
    payload = response.json()
    assert payload["file"]["extraction_status"] == "text"
    assert payload["file"]["fields"] == {
        "shipment_id": "SHP-1042", "weight_t": 48, "destination": "Bandar Abbas",
    }
    assert payload["file"]["mismatches"] == []

    broken = client.post(
        url, data={"kind": "invoice"}, files={"file": ("truncated.pdf", b"%PDF-1.7\ntruncated", "application/pdf")},
    )
    assert broken.status_code == 201
    payload = broken.json()
    assert payload["file"]["extraction_status"] == "unavailable"
    assert payload["file"]["fields"] == {}
    invoice = next(doc for doc in payload["documents"]["requirements"] if doc["key"] == "invoice")
    assert invoice["status"] == "review_required"

    monkeypatch.setattr(
        operation_documents,
        "_PDF_LIMITS",
        operation_documents._PDF_LIMITS.with_overwrites(zlib_maximum_output_length=16),
    )
    limited = client.post(
        url, data={"kind": "packing_list"}, files={"file": ("invoice.pdf", output.getvalue(), "application/pdf")},
    )
    assert limited.status_code == 201
    assert limited.json()["file"]["extraction_status"] == "unavailable"
    assert limited.json()["file"]["fields"] == {}


def test_horizon_weather_is_timestamped_and_partial(client, monkeypatch):
    forecasts = {
        "caspian_west": {
            NOW + timedelta(hours=12): {"wave_m": 2.3, "wind_ms": 10},
            NOW + timedelta(hours=36): {"wave_m": 3.1, "wind_ms": 17},
            NOW + timedelta(hours=75): {"wave_m": 9, "wind_ms": 40},
        },
    }
    monkeypatch.setattr(weather, "cached_hourly_forecast", lambda: {"status": "unavailable", "segments": forecasts})
    detail = client.get(OPERATIONS).json()
    horizon = detail["horizon"]
    assert horizon["hours"] == 72
    assert horizon["weather"]["status"] == "partial"
    west = next(seg for seg in horizon["weather"]["segments"] if seg["id"] == "caspian_west")
    assert west["first_risk_at"] == (NOW + timedelta(hours=12)).isoformat()
    assert west["forecast_hours"] == 2 and west["peak_wave_m"] == 3.1
    assert detail["baseline"]["legs"][0]["weather"]["source"] == "Open-Meteo forecast"
    assert any(i["type"] == "sea_disruption" and i["source"] == "Open-Meteo forecast" for i in horizon["impacts"])
    closed = client.get(OPERATIONS + "?close=amirabad").json()
    assert closed["baseline"]["blocked"] and closed["baseline"]["eta"] is None
    assert closed["proposals"] and all("amirabad" not in p["nodes"] for p in closed["proposals"])
