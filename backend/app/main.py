"""Smart Caspian Transit — predictive digital twin of the North-South
multimodal hub (Caspian link). FastAPI backend + static frontend.
Run:  ./run.sh        (or: uvicorn app.main:app --reload, from backend/)
Docs: /docs
"""
from __future__ import annotations

import os
import threading
import time
from collections import deque
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from app import data, graph, ml, model, weather

# Demo write protection. Without SCT_WRITE_TOKEN writes are open (demo mode,
# labelled in the UI); with it, POST endpoints require X-Write-Token.
WRITE_TOKEN = os.environ.get("SCT_WRITE_TOKEN", "")
MAX_BODY_BYTES = int(os.environ.get("SCT_MAX_BODY_BYTES", "32768"))
WRITE_RATE_LIMIT = int(os.environ.get("SCT_WRITE_RATE_LIMIT", "60"))  # per min per IP
_rate_buckets: dict[str, deque] = {}
_rate_lock = threading.Lock()

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


@app.middleware("http")
async def limit_body_size(request: Request, call_next):
    """Reject oversized payloads early (Content-Length check; chunked
    uploads are additionally bounded by the JSON parser limits below)."""
    cl = request.headers.get("content-length")
    if cl:
        try:
            if int(cl) > MAX_BODY_BYTES:
                return JSONResponse({"detail": "payload too large"}, status_code=413)
        except ValueError:
            return JSONResponse({"detail": "invalid Content-Length"}, status_code=400)
    return await call_next(request)


def guard_write(request: Request, x_write_token: str | None = Header(None)) -> None:
    """Token (when configured) + per-IP write rate limit."""
    if WRITE_TOKEN and x_write_token != WRITE_TOKEN:
        raise HTTPException(401, "write token required: set X-Write-Token header")
    ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    with _rate_lock:
        bucket = _rate_buckets.setdefault(ip, deque())
        while bucket and now - bucket[0] > 60.0:
            bucket.popleft()
        if len(bucket) >= WRITE_RATE_LIMIT:
            raise HTTPException(429, "too many write requests, retry later")
        bucket.append(now)


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
def weather_snapshot():
    return {"sea": data.WEATHER, "storm_scenario": data.STORM}


@app.get("/api/weather/live")
def weather_live():
    """Live wave/wind from Open-Meteo (cached 30 min), demo fallback offline."""
    return weather.get_live_weather()


@app.get("/api/corridor")
def corridor():
    return {"stats": data.CORRIDOR_STATS}


@app.get("/api/routes")
def routes():
    return {"routes": [
        {**r, "nodes": [data.NODES[n] for n in
                        [leg["from"] for leg in r["legs"]] + [r["legs"][-1]["to"]]]}
        for r in data.ROUTES
    ]}


def _parse_scenario(close: str = "", extra_customs_h: float = 0.0,
                    wave_m: float | None = None,
                    wind_ms: float | None = None) -> dict:
    sc: dict = {}
    if close:
        ids = [c.strip() for c in close.split(",") if c.strip()]
        unknown = [c for c in ids if c not in data.NODES]
        if unknown:
            raise HTTPException(422, f"unknown nodes in close: {', '.join(unknown)}")
        sc["closed_nodes"] = ids
    if extra_customs_h:
        sc["customs_extra_h"] = extra_customs_h
    if wave_m is not None:
        sc["wave_m"] = wave_m
    if wind_ms is not None:
        sc["wind_ms"] = wind_ms
    return sc


# Shared query constraints: physical ranges, useful 422 on violation.
WEIGHT_Q = Query(48, gt=0, le=5000, description="Вес груза, т (0 < w ≤ 5000)")
CUSTOMS_Q = Query(0.0, ge=0, le=168, description="Доп. таможенная очередь, ч (0–168)")
WAVE_Q = Query(None, ge=0, le=15, description="Волна, м (0–15; переопределяет погоду)")
WIND_Q = Query(None, ge=0, le=60, description="Ветер, м/с (0–60; переопределяет погоду)")


@app.get("/api/graph")
def graph_view():
    return {"nodes": list(data.NODES.values()),
            "edges": [{k: v for k, v in e.items() if k != "route"}
                      for e in data.GRAPH_EDGES]}


@app.get("/api/routes/search")
def routes_search(from_node: str = Query("moscow"), to_node: str = Query("bandar_abbas"),
                  weight_t: float = WEIGHT_Q, storm: bool = False,
                  objective: str = Query("time", pattern="^(time|cost|co2)$"),
                  k: int = Query(3, ge=1, le=5), close: str = ""):
    try:
        paths = graph.k_shortest_paths(
            from_node, to_node, weight_t, storm,
            _parse_scenario(close=close), objective, k)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return {"from": from_node, "to": to_node, "objective": objective,
            "storm": storm, "paths": paths}


@app.get("/api/routes/{route_id}/forecast")
def route_forecast(route_id: str, weight_t: float = WEIGHT_Q,
                   storm: bool = False, close: str = "",
                   extra_customs_h: float = CUSTOMS_Q,
                   wave_m: float | None = WAVE_Q, wind_ms: float | None = WIND_Q):
    route = data.get_route(route_id)
    if route is None:
        raise HTTPException(404, f"unknown route {route_id}")
    try:
        return model.forecast_route(
            route, weight_t, storm,
            _parse_scenario(close, extra_customs_h, wave_m, wind_ms))
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.get("/api/compare")
def compare(weight_t: float = WEIGHT_Q, storm: bool = False,
            close: str = "", extra_customs_h: float = CUSTOMS_Q):
    rows = model.compare_routes(
        weight_t, storm, _parse_scenario(close, extra_customs_h))
    if not rows:
        raise HTTPException(422, "all routes blocked by scenario")
    best_time = min(rows, key=lambda r: r["total_predicted_h"])["route_id"]
    best_co2 = min(rows, key=lambda r: r["co2_kg"])["route_id"]
    best_risk = min(rows, key=lambda r: r["delay_probability"])["route_id"]
    return {"storm": storm, "weight_t": weight_t, "routes": rows,
            "best": {"time": best_time, "co2": best_co2, "risk": best_risk}}


@app.get("/api/shipments")
def shipments():
    out = []
    for s in model.all_shipments():
        route = data.get_route(s["route_id"])
        fc = model.forecast_route(route, s["weight_t"])
        out.append({**s, "route_name": route["name"],
                    "delay_h": fc["delay_h"],
                    "delay_probability": fc["delay_probability"],
                    "co2_kg": fc["co2_kg"]})
    return {"shipments": out}


class ShipmentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    cargo: str = Field(min_length=1, max_length=200)
    weight_t: float = Field(gt=0, le=5000)
    route_id: str = Field(max_length=20)
    docs: dict[str, str] | None = None

    @field_validator("docs")
    @classmethod
    def docs_bounded(cls, v: dict[str, str] | None) -> dict[str, str] | None:
        if v is None:
            return v
        if len(v) > 20:
            raise ValueError("too many document entries (max 20)")
        for k, val in v.items():
            if len(k) > 50 or len(val) > 50:
                raise ValueError("document keys/values must be ≤ 50 chars")
        return v


@app.post("/api/shipments", status_code=201, dependencies=[Depends(guard_write)])
def create_shipment(req: ShipmentCreate):
    try:
        return model.create_shipment(req.name, req.cargo, req.weight_t,
                                     req.route_id, req.docs)
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.get("/api/shipments/{shipment_id}")
def shipment_detail(shipment_id: str, storm: bool = False):
    s = model.get_shipment_any(shipment_id)
    if s is None:
        raise HTTPException(404, f"unknown shipment {shipment_id}")
    route = data.get_route(s["route_id"])
    return {**s, "route": route,
            "forecast": model.forecast_route(route, s["weight_t"], storm),
            "doc_types": data.DOC_TYPES}


@app.get("/api/shipments/{shipment_id}/timeline")
def shipment_timeline(shipment_id: str, storm: bool = False):
    s = model.get_shipment_any(shipment_id)
    if s is None:
        raise HTTPException(404, f"unknown shipment {shipment_id}")
    return model.shipment_timeline(s, storm)


@app.get("/api/exceptions")
def exceptions(storm: bool = False):
    return {"storm": storm, "exceptions": model.compute_exceptions(storm)}


class ClearanceRequest(BaseModel):
    shipment_id: str = Field(max_length=30)
    docs: dict[str, str] = Field(default_factory=dict)

    @field_validator("docs")
    @classmethod
    def docs_bounded(cls, v: dict[str, str]) -> dict[str, str]:
        if len(v) > 20:
            raise ValueError("too many document entries (max 20)")
        for k, val in v.items():
            if len(k) > 50 or len(val) > 50:
                raise ValueError("document keys/values must be ≤ 50 chars")
        return v


@app.post("/api/clearance/submit", dependencies=[Depends(guard_write)])
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
def ml_forecast(route_id: str, weight_t: float = WEIGHT_Q,
                storm: bool = False):
    route = data.get_route(route_id)
    if route is None:
        raise HTTPException(404, f"unknown route {route_id}")
    return ml.predict_delay(route, weight_t, storm)


@app.get("/api/ml/shipment/{shipment_id}")
def ml_shipment(shipment_id: str, storm: bool = False):
    s = model.get_shipment_any(shipment_id)
    if s is None:
        raise HTTPException(404, f"unknown shipment {shipment_id}")
    return ml.predict_delay(data.get_route(s["route_id"]), s["weight_t"], storm)


# --- frontend ---
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/sw.js")
def service_worker():
    """Served from root so the SW scope covers the whole app."""
    return FileResponse(STATIC_DIR / "sw.js", media_type="application/javascript")


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
