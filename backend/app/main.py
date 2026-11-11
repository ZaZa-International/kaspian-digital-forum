"""Caspian Twin — mini digital twin of the Caspian Sea.

FastAPI backend + static frontend (no build step, no npm).
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

from app import data

app = FastAPI(
    title="Caspian Twin — цифровой двойник Каспия",
    description=(
        "Мини-двойник Каспийского моря: уровень воды, порты, станции "
        "мониторинга и сценарный прогноз до 2050 года. "
        "Данные — демонстрационные (см. data.py)."
    ),
    version="0.1.0",
)

STATIC_DIR = Path(__file__).parent / "static"


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "caspian-twin", "data": "demo-seed"}


@app.get("/api/level/history")
def level_history():
    return {
        "unit": "m (Baltic system)",
        "source_note": "Illustrative series, trend ~-9 cm/yr per published data",
        "series": data.LEVEL_HISTORY,
        "current": {"year": data.CURRENT_YEAR, "level_m": data.CURRENT_LEVEL_M},
        "area_km2": {"2006": data.AREA_2006_KM2, "2026": data.AREA_2026_KM2},
    }


@app.get("/api/level/forecast")
def level_forecast(
    scenario: str = Query("baseline", pattern="^(optimistic|baseline|pessimistic)$"),
    year: int = Query(2050, ge=2027, le=2050),
):
    return {
        "scenario": data.SCENARIOS[scenario],
        "year": year,
        "level_m": data.forecast_level(scenario, year),
        "series": [
            {"year": y, "level_m": data.forecast_level(scenario, y)}
            for y in range(2026, 2051, 2)
        ],
    }


@app.get("/api/ports")
def ports():
    return {"ports": data.PORTS, "sea_polygon": data.SEA_POLYGON}


@app.get("/api/stations")
def stations():
    return {"stations": data.STATIONS, "protected_areas": data.PROTECTED_AREAS}


@app.get("/api/alerts")
def alerts():
    return {"alerts": data.ALERTS}


class SimulateRequest(BaseModel):
    year: int = Field(2040, ge=2027, le=2050)
    scenario: str = Field("baseline", pattern="^(optimistic|baseline|pessimistic)$")


@app.post("/api/scenarios/simulate")
def simulate(req: SimulateRequest):
    return data.simulate(req.year, req.scenario)


@app.get("/api/scenarios")
def scenarios():
    return {"scenarios": list(data.SCENARIOS.values())}


# --- frontend (must be last: catch-all would shadow /api otherwise) ---
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

    uvicorn.run(
        "app.main:app",
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8000")),
        reload=True,
    )
