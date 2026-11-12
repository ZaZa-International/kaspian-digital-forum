"""Scenario-engine consistency tests (cycle 3).

One semantics everywhere: explicit wave/wind > storm > live/fallback;
extra_customs_h applied on every endpoint; ferry wait modelled explicitly;
Monte Carlo agrees with the point forecast; risk/quantiles honestly labelled.
"""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _sea_leg(fc):
    return next(l for l in fc["legs"] if l["mode"] == "sea")


def test_wind_only_changes_point_and_monte_carlo():
    base = client.get("/api/routes/R1/forecast").json()
    windy = client.get("/api/routes/R1/forecast?wind_ms=25").json()
    assert windy["delay_h"] > base["delay_h"]
    assert windy["monte_carlo"] != base["monte_carlo"]
    assert windy["eta_p90_h"] > base["eta_p90_h"]
    assert _sea_leg(windy)["weather"]["wind_ms"] == 25


def test_wave_only_keeps_base_wind():
    fc = client.get("/api/routes/R1/forecast?wave_m=3").json()
    w = _sea_leg(fc)["weather"]
    assert w["wave_m"] == 3 and w["wind_ms"] > 0
    assert w["source"] == "scenario override"
    assert fc["delay_h"] >= 48  # missed sailing penalty


def test_storm_and_override_same_precedence_everywhere():
    # explicit calm override wins over storm in BOTH point and MC
    fc = client.get("/api/routes/R1/forecast?storm=true&wave_m=0.5&wind_ms=5").json()
    w = _sea_leg(fc)["weather"]
    assert (w["wave_m"], w["wind_ms"]) == (0.5, 5)
    storm_only = client.get("/api/routes/R1/forecast?storm=true").json()
    assert fc["delay_h"] < storm_only["delay_h"]
    assert fc["eta_p50_h"] < storm_only["eta_p50_h"]
    # MC quantiles must be consistent with the overridden point forecast
    assert abs(fc["eta_p50_h"] - fc["total_predicted_h"]) < 40


def test_land_route_ignores_weather_scenarios():
    a = client.get("/api/routes/R3/forecast").json()
    b = client.get("/api/routes/R3/forecast?wave_m=5&wind_ms=30&storm=true").json()
    assert a["delay_h"] == b["delay_h"]
    assert a["total_predicted_h"] == b["total_predicted_h"]


def test_search_honors_extra_customs():
    a = client.get("/api/routes/search?k=5").json()["paths"]
    b = client.get("/api/routes/search?k=5&extra_customs_h=30").json()["paths"]
    by_nodes = {tuple(p["nodes"]): p for p in b}
    common = [p for p in a if tuple(p["nodes"]) in by_nodes]
    assert common, "expected at least one shared path"
    for p in common:
        q = by_nodes[tuple(p["nodes"])]
        # +30 h on every leg of the path (customs applies to all legs)
        assert abs(q["total_predicted_h"] - p["total_predicted_h"] - 30 * len(p["legs"])) < 0.2


def test_search_honors_wave_and_compare_too():
    a = client.get("/api/routes/search").json()["paths"]
    b = client.get("/api/routes/search?wave_m=3").json()["paths"]
    assert max(p["delay_h"] for p in b) > max(p["delay_h"] for p in a)
    ca = client.get("/api/compare").json()["routes"]
    cb = client.get("/api/compare?wave_m=3&wind_ms=20").json()["routes"]
    r1a = next(r for r in ca if r["route_id"] == "R1")
    r1b = next(r for r in cb if r["route_id"] == "R1")
    r3a = next(r for r in ca if r["route_id"] == "R3")
    r3b = next(r for r in cb if r["route_id"] == "R3")
    assert r1b["delay_h"] > r1a["delay_h"]
    assert r3a["delay_h"] == r3b["delay_h"]


def test_exceptions_honor_scenario():
    ex = client.get("/api/exceptions?close=makhachkala").json()["exceptions"]
    assert any("Узел закрыт" in e["title"] for e in ex)
    assert any(e.get("shipment_id") == "SHP-1042" and "заблокирован" in e["title"] for e in ex)
    ex = client.get("/api/exceptions?extra_customs_h=20").json()["exceptions"]
    assert any("таможенная очередь +20" in e["title"] for e in ex)
    ex = client.get("/api/exceptions?wave_m=3").json()["exceptions"]
    assert any("Волна ≥ 2 м" in e["title"] and "сценарий" in e["title"] for e in ex)
    # scenario reset -> scenario exceptions disappear
    ex = client.get("/api/exceptions").json()["exceptions"]
    assert not any("Узел закрыт" in e["title"] for e in ex)


def test_ferry_wait_model_explicit():
    fc = client.get("/api/routes/R1/forecast").json()
    sea = _sea_leg(fc)
    assert sea["wait_h"] == 36.0
    assert sea["sailing_window"]["expected_wait_h"] == 36.0
    assert any("ожидание отправления парома" in r for r in sea["reasons"])
    assert fc["total_wait_h"] == 36.0
    assert abs(fc["total_predicted_h"] - fc["total_base_h"] - fc["total_wait_h"] - fc["delay_h"]) < 0.2
    assert fc["monte_carlo"]["expected_wait_h"] == 36.0
    # land route has no ferry wait
    r3 = client.get("/api/routes/R3/forecast").json()
    assert r3["total_wait_h"] == 0


def test_monte_carlo_deterministic_and_labelled():
    a = client.get("/api/routes/R1/forecast").json()["monte_carlo"]
    b = client.get("/api/routes/R1/forecast").json()["monte_carlo"]
    assert a == b
    assert a["kind"].startswith("simulation quantiles")
    assert "не эмпирическая" in a["coverage_note"]
    fc = client.get("/api/routes/R1/forecast").json()
    assert fc["delay_probability_kind"].startswith("heuristic")


def test_forecast_and_compare_agree():
    fc = client.get("/api/routes/R1/forecast?weight_t=120&storm=true").json()
    row = next(r for r in client.get("/api/compare?weight_t=120&storm=true").json()["routes"]
               if r["route_id"] == "R1")
    for key in ("total_predicted_h", "delay_h", "co2_kg", "cost_usd", "eta_p90_h", "total_wait_h"):
        assert fc[key] == row[key], key


def test_shipments_list_and_detail_honor_scenario():
    calm = client.get("/api/shipments").json()["shipments"]
    storm = client.get("/api/shipments?storm=true").json()["shipments"]
    s1042_calm = next(s for s in calm if s["id"] == "SHP-1042")
    s1042_storm = next(s for s in storm if s["id"] == "SHP-1042")
    assert s1042_storm["delay_h"] > s1042_calm["delay_h"]
    d = client.get("/api/shipments/SHP-1042?wave_m=3").json()
    assert d["forecast"]["delay_h"] >= 48
    assert client.get("/api/shipments/SHP-1042?close=makhachkala").status_code == 422
    assert client.get("/api/shipments/SHP-1042/timeline?close=makhachkala").status_code == 422
    tl = client.get("/api/shipments/SHP-1042/timeline?wave_m=3").json()
    assert tl["events"]