"""Smart Caspian Transit — predictive digital twin of the North-South
multimodal hub (Caspian link). FastAPI backend + static frontend.
Run:  ./run.sh        (or: uvicorn app.main:app --reload, from backend/)
Docs: /docs
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import data, ml, model

app = FastAPI(
    title="Smart Caspian Transit",
    description=(
        "Предиктивный цифровой двойник мультимодального хаба МТК "
        "«Север–Юг»: прогноз задержек, pre-clearance таможни, "
        "единое окно логиста с углеродным следом и альтернативными "
        "маршрутами. Данные — демонстрационные."
    ),
    version="0.1.0",
)

STATIC_DIR = Path(__file__).parent / "static"


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "smart-caspian-transit", "data": "demo-seed"}


@app.get("/api/nodes")
def nodes():
    return {"nodes": list(data.NODES.values())}


@app.get("/api/ports")
def ports():
    out = []
    for pid, p in data.PORTS.items():
        out.append({**p, "name": data.NODES[pid]["name"],
                    "lat": data.NODES[pid]["lat"], "lon": data.NODES[pid]["lon"]})
    return {"ports": out}


@app.get("/api/weather")
def weather():
    return {"sea": data.WEATHER, "storm_scenario": data.STORM}


@app.get("/api/routes")
def routes():
    return {"routes": [
        {**r, "nodes": [data.NODES[n] for n in
                        [leg["from"] for leg in r["legs"]] + [r["legs"][-1]["to"]]]}
        for r in data.ROUTES
    ]}


@app.get("/api/routes/{route_id}/forecast")
def route_forecast(route_id: str, weight_t: float = Query(48, gt=0),
                   storm: bool = False):
    route = data.get_route(route_id)
    if route is None:
        raise HTTPException(404, f"unknown route {route_id}")
    return model.forecast_route(route, weight_t, storm)


@app.get("/api/compare")
def compare(weight_t: float = Query(48, gt=0), storm: bool = False):
    rows = model.compare_routes(weight_t, storm)
    best_time = min(rows, key=lambda r: r["total_predicted_h"])["route_id"]
    best_co2 = min(rows, key=lambda r: r["co2_kg"])["route_id"]
    best_risk = min(rows, key=lambda r: r["delay_probability"])["route_id"]
    return {"storm": storm, "weight_t": weight_t, "routes": rows,
            "best": {"time": best_time, "co2": best_co2, "risk": best_risk}}


@app.get("/api/shipments")
def shipments():
    out = []
    for s in data.SHIPMENTS:
        route = data.get_route(s["route_id"])
        fc = model.forecast_route(route, s["weight_t"])
        out.append({**s, "route_name": route["name"],
                    "delay_h": fc["delay_h"],
                    "delay_probability": fc["delay_probability"],
                    "co2_kg": fc["co2_kg"]})
    return {"shipments": out}


@app.get("/api/shipments/{shipment_id}")
def shipment_detail(shipment_id: str, storm: bool = False):
    s = data.get_shipment(shipment_id)
    if s is None:
        raise HTTPException(404, f"unknown shipment {shipment_id}")
    route = data.get_route(s["route_id"])
    return {**s, "route": route,
            "forecast": model.forecast_route(route, s["weight_t"], storm),
            "doc_types": data.DOC_TYPES}


class ClearanceRequest(BaseModel):
    shipment_id: str
    docs: dict[str, str] = Field(default_factory=dict)


@app.post("/api/clearance/submit")
def clearance_submit(req: ClearanceRequest):
    try:
        entry = model.submit_clearance(req.shipment_id, req.docs)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return entry


@app.get("/api/clearance/ledger")
def clearance_ledger():
    return {"entries": model.LEDGER, "chain_valid": model.verify_ledger()}


@app.get("/api/ml/info")
def ml_info():
    return ml.info()


@app.get("/api/ml/forecast")
def ml_forecast(route_id: str, weight_t: float = Query(48, gt=0),
                storm: bool = False):
    route = data.get_route(route_id)
    if route is None:
        raise HTTPException(404, f"unknown route {route_id}")
    return ml.predict_delay(route, weight_t, storm)


@app.get("/api/ml/shipment/{shipment_id}")
def ml_shipment(shipment_id: str, storm: bool = False):
    s = data.get_shipment(shipment_id)
    if s is None:
        raise HTTPException(404, f"unknown shipment {shipment_id}")
    return ml.predict_delay(data.get_route(s["route_id"]), s["weight_t"], storm)


# --- frontend ---
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def index():
    index_file = STATIC_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(404, "frontend not built yet")
    return FileResponse(index_file)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=os.environ.get("HOST", "0.0.0.0"),
                port=int(os.environ.get("PORT", "8000")), reload=True)
