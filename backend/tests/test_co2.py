"""CO2e unit / factor / rounding tests (cycle 6)."""
from fastapi.testclient import TestClient

from app import data
from app.main import app

client = TestClient(app)

VERIFIED = {"rail": 25.83, "sea": 51.58, "road": 122.92}


def test_factors_match_verified_desnz_2026():
    assert data.CO2_G_PER_TKM == VERIFIED
    meta = data.CO2_FACTOR_META
    assert "DESNZ 2026" in meta["source"]
    assert "CO2e" in meta["unit"] and "UK proxy" in meta["scope"]


def test_total_is_sum_of_unrounded_legs():
    for rid in ("R1", "R2", "R3"):
        for w in (48, 1200):
            fc = client.get(f"/api/routes/{rid}/forecast?weight_t={w}").json()
            route = data.get_route(rid)
            exact = sum(l["distance_km"] * w * VERIFIED[l["mode"]] / 1000
                        for l in route["legs"])
            assert fc["co2_kg"] == round(exact), (rid, w)
            assert fc["co2_unit"].startswith("kg CO2e")


def test_breakdown_rounding_tolerance():
    fc = client.get("/api/routes/R1/forecast?weight_t=48").json()
    s = sum(b["co2_kg"] for b in fc["co2_breakdown"])
    # per-leg rounding may differ from the rounded total by at most ~0.5/leg
    assert abs(s - fc["co2_kg"]) <= 0.5 * len(fc["co2_breakdown"]) + 0.5
    for b in fc["co2_breakdown"]:
        assert b["factor_g_per_tkm"] == VERIFIED[b["mode"]]
        assert "UK proxy" in b["data_quality"]


def test_weight_scaling_and_mode_factors():
    a = client.get("/api/routes/R1/forecast?weight_t=48").json()["co2_kg"]
    b = client.get("/api/routes/R1/forecast?weight_t=96").json()["co2_kg"]
    assert abs(b - 2 * a) <= 1
    # road factor is the highest per tkm; R3 has a road leg.
    # leg-level values are PER TONNE (explicit field names); totals scale by weight.
    r3 = client.get("/api/routes/R3/forecast?weight_t=48").json()
    road = next(l for l in r3["legs"] if l["mode"] == "road")
    assert road["co2_kg_per_t"] == round(road["distance_km"] * 122.92 / 1000, 2)
    road_bd = next(b for b in r3["co2_breakdown"] if b["mode"] == "road")
    assert road_bd["co2_kg"] == round(road["distance_km"] * 48 * 122.92 / 1000)


def test_method_wording_honest():
    fc = client.get("/api/routes/R1/forecast").json()
    assert "ISO 14083" in fc["co2_method"]
    assert "без верификации" in fc["co2_method"]
    assert "GLEC v3.3" in fc["co2_method"]
    assert fc["co2_factor_meta"]["quality"].startswith("default factors")