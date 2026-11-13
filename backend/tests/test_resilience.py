"""Scenario calculations respect the current shipment and remain read-only."""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import main, operations, resilience, store, weather

NOW = datetime(2026, 11, 13, 10, tzinfo=timezone.utc)
SHIPMENT = "/api/operations/SHP-1042"
LAB = SHIPMENT + "/resilience"


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


def test_exact_cutoff_one_hour_and_multiple_missed_sailings(client):
    hit = client.get(LAB + "?departure_in_h=12&cutoff_h=6").json()["connection"]
    assert hit["baseline"]["ready_h"] == 6
    assert hit["baseline"]["margin_h"] == 0
    assert hit["baseline"]["misses_first"] is False
    assert hit["baseline"]["catch_in_h"] == 12
    assert hit["cliff"]["one_hour_later_misses"] is True
    assert hit["cliff"]["jump_h"] == 72
    assert hit["cliff"]["example"] == {
        "on_cutoff_ready_h": 6, "one_hour_late_ready_h": 7,
        "on_cutoff_catch_in_h": 12, "one_hour_late_catch_in_h": 84, "jump_h": 72,
    }
    late = client.get(LAB + "?departure_in_h=6&cutoff_h=6&feeder_delay_h=72").json()["connection"]
    assert late["intervention"]["skipped_sailings"] == 2
    assert late["intervention"]["catch_in_h"] == 150
    assert late["intervention"]["arrival_h"] >= late["intervention"]["catch_in_h"]


def test_paired_simulation_reduction_and_read_only(client):
    before = client.get(SHIPMENT).json()
    baseline = client.get(LAB).json()
    assert baseline == client.get(LAB).json()
    connection = baseline["connection"]
    assert connection["status"] == "eligible" and connection["already_at_port"]
    assert connection["feeder_leg_ids"] == []
    assert connection["baseline"] == connection["intervention"]
    assert all(v == 0 for v in connection["impact"].values())
    assert connection["simulation"]["common_random_draws"]
    assert baseline["scope"]["completed_leg_ids"] == ["R1-L1"]
    assert "R1-L1" not in baseline["decision"]["options"][0]["leg_ids"]
    held = client.get(LAB + "?feeder_delay_h=13").json()["connection"]
    rescued = client.get(LAB + "?feeder_delay_h=13&customs_reduction_pct=25").json()["connection"]
    assert held["baseline"] == rescued["baseline"]
    assert held["pivotal_cause"]["code"] == "terminal_hold"
    assert held["baseline"] == held["intervention"]
    assert held["reference"]["catch_in_h"] == 24
    assert held["feeder_impact"]["catch_h"] == 72
    assert rescued["pivotal_cause"]["code"] == "customs_recovery"
    assert rescued["baseline"]["catch_in_h"] == 96
    assert rescued["intervention"]["catch_in_h"] == 24
    assert rescued["impact"]["catch_h"] == -72
    assert held["intervention"]["p_miss"] > rescued["intervention"]["p_miss"] > 0
    assert held["impact"]["samples_later_pct"] == rescued["impact"]["samples_later_pct"] == 0
    assert rescued["impact"]["samples_earlier_pct"] > 0
    for simulation in (held["intervention"], rescued["intervention"]):
        assert sum(s["count"] for s in simulation["sailing_distribution"]) == resilience.SAMPLE_COUNT
    assert client.get(SHIPMENT).json() == before


def test_storm_gate_live_queue_and_land_stress_invariance(client):
    response = client.post("/api/ports/makhachkala", json={"congestion_pct": 45, "customs_queue_h": 9})
    assert response.status_code == 200
    calm = client.get(LAB).json()
    storm = client.get(LAB + "?storm=true&profile=time").json()
    assert calm["connection"]["readiness_components"]["origin_queue_source"] == "operator-reported"
    assert calm["connection"]["baseline"]["ready_h"] == 9
    assert storm["connection"]["weather"]["closed_until_h"] == 48
    assert storm["connection"]["baseline"]["p_miss"] == 1
    assert storm["connection"]["pivotal_cause"]["code"] == "weather_gate"
    assert storm["connection"]["cliff"]["example"]["on_cutoff_catch_in_h"] == 96
    decision = storm["decision"]
    assert decision["metric_units"] == {"remaining_h": "h", "total_usd": "USD", "co2_kg": "kg CO2e"}
    assert {row["id"] for row in decision["scenarios"]} == {"calm", "storm", "customs_surge"}
    best = next(o for o in decision["options"] if o["id"] == decision["selected_id"])
    stress = {row["id"]: row for row in best["stress"]}
    assert best["node_names"] == ["Махачкала", "Баку", "Астара", "Бендер-Аббас"]
    assert stress["storm"]["remaining_h"] == stress["calm"]["remaining_h"]
    assert stress["storm"]["total_usd"] == stress["calm"]["total_usd"]
    assert stress["customs_surge"]["remaining_h"] > stress["calm"]["remaining_h"]
    sea = next(o for o in decision["options"] if o["id"] == "baseline")
    stress = {row["id"]: row for row in sea["stress"]}
    assert stress["storm"]["remaining_h"] > stress["calm"]["remaining_h"]
    assert stress["storm"]["co2_kg"] == stress["calm"]["co2_kg"]


def _options(current, stress):
    def metrics(values):
        return {"remaining_h": values[0], "total_usd": values[1], "co2_kg": values[2], "blocked": values[0] is None}

    return [{"id": name, "pareto": name != "b", "conditional": False,
             **metrics(values), "stress": [metrics(row) for row in stress[i]]}
            for i, (name, values) in enumerate(zip("abc", current))]


def test_robust_profile_retains_dominated_current_option_and_penalizes_block():
    scenarios = [{"id": "storm"}, {"id": "customs_surge"}]
    options = _options(
        [(10, 10, 10), (11, 11, 11), (100, 100, 100)],
        [[(90, 90, 90), (75, 75, 75)], [(10, 10, 10), (20, 20, 20)], [(100, 100, 100), (100, 100, 100)]],
    )
    assert resilience._dominates(options[0], options[1])
    robust = resilience._policy(options, "balanced", scenarios)
    assert robust["selected_id"] == "b"
    assert not options[1]["pareto"]
    assert resilience._policy(options, "time", scenarios)["selected_id"] == "a"
    assert resilience._policy(options, "cost", scenarios)["selected_id"] == "a"
    assert resilience._policy(options, "green", scenarios)["selected_id"] == "a"

    blocked = _options(
        [(10, 10, 10), (20, 20, 20)],
        [[(None, None, None), (10, 10, 10)], [(20, 20, 20), (20, 20, 20)]],
    )
    robust = resilience._policy(blocked, "balanced", scenarios)
    assert robust["selected_id"] == "b"
    assert blocked[0]["balanced_score"] == 2
    assert blocked[1]["balanced_score"] <= 1


def test_feeder_active_ferry_delivered_and_invalid_plan(client):
    created = client.post("/api/shipments", json={"name": "Feeder", "cargo": "Boxes", "weight_t": 48, "route_id": "R1"})
    assert created.status_code == 201
    shipment_id = created.json()["id"]
    base = f"/api/operations/{shipment_id}/resilience"
    assert client.get(base).json()["connection"]["feeder_leg_ids"] == ["R1-L1"]

    def event(leg, kind):
        return client.post(f"/api/shipments/{shipment_id}/events", json={
            "leg_id": leg, "event_type": kind, "actual_ts": NOW.isoformat(), "source": "test-operator",
        })

    assert event("R1-L1", "departure").status_code == 201
    active_feeder = client.get(base).json()
    assert active_feeder["scope"]["progress_status"] == "in_transit"
    assert active_feeder["connection"]["status"] == "eligible"
    assert active_feeder["connection"]["feeder_leg_ids"] == ["R1-L1"]
    assert len(active_feeder["decision"]["options"]) == 1
    assert event("R1-L1", "arrival").status_code == 201
    assert event("R1-L2", "departure").status_code == 201
    assert client.get(base).json()["connection"]["status"] == "already_sailed"
    assert event("R1-L2", "arrival").status_code == 201
    assert client.get(base).json()["connection"]["status"] == "land_only"
    assert event("R1-L3", "arrival").status_code == 201
    delivered = client.get(base).json()
    assert delivered["status"] == "delivered"
    assert delivered["decision"]["options"] == [] and delivered["decision"]["selected_id"] is None
    assert client.get(LAB + "?plan_version=999").status_code == 409
    assert client.get("/api/operations/nope/resilience").status_code == 404
    for query in ("departure_in_h=5", "departure_in_h=nan", "cutoff_h=7&departure_in_h=6", "feeder_delay_h=73",
                  "customs_reduction_pct=101", "profile=other", "plan_version=0"):
        assert client.get(LAB + "?" + query).status_code == 422


def test_closed_path_and_accepted_route_follow_current_version(client):
    blocked = client.get(LAB + "?close=amirabad").json()
    assert blocked["connection"]["status"] == "blocked"
    assert blocked["decision"]["options"][0]["blocked"]
    assert blocked["decision"]["selected_id"] != "baseline"
    all_closed = client.get(LAB + "?close=makhachkala").json()
    assert all_closed["decision"]["selected_id"] is None
    detail = client.get(SHIPMENT + "?storm=true").json()
    proposal = detail["proposals"][0]
    result = client.post(SHIPMENT + "/accept?storm=true", json={"proposal_id": proposal["id"], "version": 1},
                         headers={"Idempotency-Key": "accept-land"})
    assert result.status_code == 200
    assert client.get(LAB + "?plan_version=1").status_code == 409
    land = client.get(LAB + "?plan_version=2").json()
    assert land["plan_version"] == 2 and land["status"] == "land_only"
    assert land["scope"]["remaining_leg_ids"] == proposal["leg_ids"]
