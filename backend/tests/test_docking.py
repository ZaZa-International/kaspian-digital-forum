"""Discrete-event rail–ferry docking model (TZ: explicit synchronization).

Tests the catch/miss logic against the periodic ferry schedule, the
route-level Monte Carlo analysis, and the API endpoint. The schedule is an
assumption (no real published timetable); the synchronization model is real.
"""

import pytest
from fastapi.testclient import TestClient

from app import data, docking
from app.main import app

client = TestClient(app)


def test_connection_catch():
    c = docking.connection(10.0)  # arrives well before the 72 h departure
    assert c["missed"] is False
    assert c["departure_h"] == 72.0
    assert c["wait_h"] == 62.0
    assert c["missed_by_h"] == 0.0


def test_connection_miss():
    c = docking.connection(67.0)  # soonest dep 72, cutoff 66 -> 67 > 66 -> miss
    assert c["missed"] is True
    assert c["missed_by_h"] == 1.0
    assert c["departure_h"] == 144.0  # next sailing
    assert c["wait_h"] == 77.0


def test_connection_cutoff_boundary():
    assert docking.connection(66.0)["missed"] is False  # exactly at cutoff -> catch
    assert docking.connection(66.5)["missed"] is True  # past cutoff -> miss


def test_docking_analysis_sea_route():
    d = docking.docking_analysis(data.get_route("R1"), 48)
    assert d["has_connection"] is True
    c = d["connections"][0]
    assert 0.0 <= c["p_miss"] <= 1.0
    assert c["expected_wait_h"] >= 0
    assert c["wait_p90_h"] >= c["expected_wait_h"]
    assert c["example"]["verdict"]
    assert c["port_name"]
    assert "допущение" in d["schedule_assumption"]


def test_docking_analysis_land_route():
    d = docking.docking_analysis(data.get_route("R3"), 48)
    assert d["has_connection"] is False
    assert d["connections"] == []


def test_docking_api():
    r1 = client.get("/api/routes/R1/docking").json()
    assert r1["has_connection"] is True
    r3 = client.get("/api/routes/R3/docking").json()
    assert r3["has_connection"] is False
    assert client.get("/api/routes/NOPE/docking").status_code == 404


def test_docking_storm_runs():
    calm = docking.docking_analysis(data.get_route("R1"), 48, storm=False)
    storm = docking.docking_analysis(data.get_route("R1"), 48, storm=True)
    assert calm["has_connection"] and storm["has_connection"]
    # storm adds rail delay -> arrival later -> wait/miss cannot decrease expectation wildly
    assert storm["connections"][0]["expected_wait_h"] >= 0
