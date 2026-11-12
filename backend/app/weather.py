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
import logging
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock, Thread

from app import data

log = logging.getLogger("sct.weather")

ARCHIVE_PATH = Path(__file__).parent / "archive_weather.json"
_archive: dict | None = None


def load_archive() -> dict:
    """90-day Open-Meteo marine archive (wave/wind samples per sea leg)."""
    global _archive
    if _archive is None:
        with open(ARCHIVE_PATH, encoding="utf-8") as f:
            _archive = json.load(f)
    return _archive


def bootstrap_sea_state(rng, sea: str) -> tuple[float, float]:
    """One random (wave_m, wind_ms) hour from the real archive."""
    seg = load_archive()["segments"][sea]
    wave = float(rng.choice(seg["wave_m"]["samples"]))
    wind = float(rng.choice(seg["wind_ms"]["samples"]))
    return wave, wind

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
    """Wave/wind used by the delay model: storm override, else live.
    `source` documents provenance (storm scenario / live / demo fallback)."""
    if storm:
        return {"wind_ms": data.STORM["wind_ms"], "wave_m": data.STORM["wave_m"],
                "source": "storm scenario"}
    live = get_live_weather()["segments"].get(sea)
    if live:
        return {"wind_ms": live["wind_ms"], "wave_m": live["wave_m"],
                "source": live.get("source", "live: Open-Meteo")}
    w = data.WEATHER[sea]
    return {"wind_ms": w["wind_ms"], "wave_m": w["wave_m"],
            "source": "demo fallback (live unavailable)"}


_forecast_lock = Lock()
_forecast_cache: dict = {"at": 0.0, "payload": None, "refreshing": False}


def _hourly_field(host: str, path: str, lat: float, lon: float, field: str) -> dict:
    url = (
        f"https://{host}.open-meteo.com/v1/{path}?latitude={lat}&longitude={lon}"
        f"&hourly={field}&forecast_days=4&timezone=UTC"
    )
    if field == "wind_speed_10m":
        url += "&wind_speed_unit=ms"
    try:
        with urllib.request.urlopen(url, timeout=4) as response:
            hourly = json.load(response)["hourly"]
        return {
            datetime.fromisoformat(ts).replace(tzinfo=timezone.utc): value
            for ts, value in zip(hourly["time"], hourly[field])
            if isinstance(value, (int, float)) and 0 <= value < 100
        }
    except (OSError, ValueError, TypeError, KeyError):
        return {}


def _fetch_forecast() -> dict:
    segments = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        pending = {
            key: (
                pool.submit(_hourly_field, "marine-api", "marine", seg["lat"], seg["lon"], "wave_height"),
                pool.submit(_hourly_field, "api", "forecast", seg["lat"], seg["lon"], "wind_speed_10m"),
            )
            for key, seg in data.SEA_SEGMENTS.items()
        }
        for key, (marine, forecast) in pending.items():
            waves, winds = marine.result(), forecast.result()
            segments[key] = {
                ts: {"wave_m": round(waves[ts], 2), "wind_ms": round(winds[ts], 1)}
                for ts in sorted(waves.keys() & winds.keys())
            }
    return segments


def _refresh_forecast() -> None:
    payload = None
    try:
        payload = _fetch_forecast()
    except Exception:
        log.warning("Open-Meteo hourly forecast refresh failed", exc_info=True)
    finally:
        with _forecast_lock:
            _forecast_cache.update(at=time.monotonic(), payload=payload, refreshing=False)


def cached_hourly_forecast() -> dict:
    """Read immediately; refresh external forecast in the background."""
    with _forecast_lock:
        now = time.monotonic()
        age = now - _forecast_cache["at"]
        payload = _forecast_cache["payload"]
        ttl = CACHE_TTL_S if payload and any(payload.values()) else 120
        if not _forecast_cache["refreshing"] and (not _forecast_cache["at"] or age >= ttl):
            _forecast_cache["refreshing"] = True
            Thread(target=_refresh_forecast, daemon=True, name="sct-weather").start()
        return {
            "status": "loading" if _forecast_cache["refreshing"] else "unavailable",
            "segments": payload if payload and age < CACHE_TTL_S else {},
        }
