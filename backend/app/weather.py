"""Live weather for the Caspian sea legs (Open-Meteo, free, no key).

- Marine API: wave_height at the sea-leg midpoints.
- Forecast API: wind_speed_10m at the same points.
Results are cached in memory for 30 minutes; on any failure the
caller gets the demo fallback from data.WEATHER with source="demo".
The app therefore works fully offline — live data is a bonus, not a
dependency. Never raise: this module must not break the demo.
"""
from __future__ import annotations

import json
import time
import urllib.request
from datetime import datetime, timezone

from app import data

CACHE_TTL_S = 30 * 60
_cache: dict = {"at": 0.0, "payload": None}


def _fetch_one(lat: float, lon: float) -> tuple[float | None, float | None]:
    """(wave_m, wind_ms) right now at a point, or (None, None).
    Uses urllib: httpx chokes on this environment's proxy settings
    (InvalidURL on no_proxy entries), urllib handles them fine."""
    try:
        with urllib.request.urlopen(
            "https://marine-api.open-meteo.com/v1/marine?"
            f"latitude={lat}&longitude={lon}&hourly=wave_height"
            "&forecast_days=1&timezone=UTC", timeout=8) as r:
            marine = json.load(r)
        with urllib.request.urlopen(
            "https://api.open-meteo.com/v1/forecast?"
            f"latitude={lat}&longitude={lon}&hourly=wind_speed_10m"
            "&forecast_days=1&timezone=UTC", timeout=8) as r:
            fc = json.load(r)
    except Exception:
        return None, None
    wave = marine["hourly"]["wave_height"]
    wind = fc["hourly"]["wind_speed_10m"]
    now_hour = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:00")
    try:
        idx = marine["hourly"]["time"].index(now_hour)
    except ValueError:
        idx = 0
    wv = wave[idx] if idx < len(wave) else None
    wn = wind[idx] / 3.6 if idx < len(wind) and wind[idx] is not None else None
    return wv, wn


def get_live_weather() -> dict:
    """Live wave/wind per sea segment, or demo fallback. Always returns."""
    now = time.time()
    if _cache["payload"] is not None and now - _cache["at"] < CACHE_TTL_S:
        return _cache["payload"]
    segments = {}
    live_ok = True
    for seg_id, seg in data.SEA_SEGMENTS.items():
        wave, wind = _fetch_one(seg["lat"], seg["lon"])
        fallback = data.WEATHER[seg_id]
        if wave is None or wind is None:
            live_ok = False
            segments[seg_id] = {**fallback, "source": "demo (live недоступен)"}
        else:
            segments[seg_id] = {
                "segment": seg["segment"],
                "wind_ms": round(wind, 1), "wave_m": round(wave, 2),
                "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                "source": "live: Open-Meteo",
            }
    payload = {"segments": segments,
               "live": live_ok,
               "fetched_at": datetime.now(timezone.utc).isoformat()}
    _cache.update({"at": now, "payload": payload})
    return payload


def current_sea_state(sea: str, storm: bool) -> dict:
    """Wave/wind used by the delay model: storm override, else live."""
    if storm:
        return {"wind_ms": data.STORM["wind_ms"], "wave_m": data.STORM["wave_m"]}
    live = get_live_weather()["segments"].get(sea)
    if live:
        return {"wind_ms": live["wind_ms"], "wave_m": live["wave_m"]}
    w = data.WEATHER[sea]
    return {"wind_ms": w["wind_ms"], "wave_m": w["wave_m"]}
