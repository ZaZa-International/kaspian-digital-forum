#!/usr/bin/env python3
"""Re-fetch the 90-day Open-Meteo marine archive for the two sea legs
and rewrite backend/app/archive_weather.json (bootstrap distributions
for the ML model). Run: python3 scripts/fetch_weather_archive.py
"""
import json
import statistics
import urllib.request
from pathlib import Path

SEGMENTS = [("caspian_north", 45.0, 49.6), ("caspian_west", 39.9, 50.4)]


def get(url):
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.load(r)


def main():
    out = {"source": "Open-Meteo marine + forecast API, 90-day archive",
           "segments": {}}
    for name, lat, lon in SEGMENTS:
        d = get(f"https://marine-api.open-meteo.com/v1/marine?latitude={lat}"
                f"&longitude={lon}&hourly=wave_height&past_days=90"
                "&forecast_days=1&timezone=UTC")
        wh = [x for x in d["hourly"]["wave_height"] if x is not None]
        w = get(f"https://api.open-meteo.com/v1/forecast?latitude={lat}"
                f"&longitude={lon}&hourly=wind_speed_10m&past_days=90"
                "&forecast_days=1&timezone=UTC")
        ws = [x / 3.6 for x in w["hourly"]["wind_speed_10m"] if x is not None]
        s = sorted(wh)

        def pct(a, p):
            return round(a[int(len(a) * p)], 2)

        out["segments"][name] = {
            "lat": lat, "lon": lon, "n_hours": len(wh),
            "wave_m": {
                "mean": round(statistics.mean(wh), 2), "p50": pct(s, .5),
                "p90": pct(s, .9), "max": round(max(wh), 2),
                "pct_ge_2m": round(sum(1 for x in wh if x >= 2) / len(wh) * 100, 1),
                "pct_ge_1_5m": round(sum(1 for x in wh if x >= 1.5) / len(wh) * 100, 1),
                "samples": [round(x, 2) for x in wh[::6]]},
            "wind_ms": {
                "mean": round(statistics.mean(ws), 1), "p90": pct(sorted(ws), .9),
                "max": round(max(ws), 1),
                "pct_gt_14": round(sum(1 for x in ws if x > 14) / len(ws) * 100, 1),
                "samples": [round(x, 1) for x in ws[::6]]},
        }
        print(name, "saved")
    dest = Path(__file__).resolve().parent.parent / "backend" / "app" / "archive_weather.json"
    dest.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {dest}")


if __name__ == "__main__":
    main()
