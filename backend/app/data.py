"""Demo seed data for Smart Caspian Transit.

HONESTY NOTE:
All numbers are DEMO data for the hackathon prototype. Distances and
times are rough corridor estimates, weather/congestion are illustrative,
CO2 and cost factors are published ranges (see README). The delay model
in model.py is an explainable scoring model, NOT a trained ML model —
we say so on the demo. Swap this module for real feeds (port APIs,
weather API, customs API) without touching the API or frontend.
"""
from __future__ import annotations

NODES = {
    "moscow": {"id": "moscow", "name": "Москва", "country": "Россия",
               "lat": 55.76, "lon": 37.62, "kind": "rail_hub"},
    "astrakhan": {"id": "astrakhan", "name": "Астрахань", "country": "Россия",
                  "lat": 46.35, "lon": 48.04, "kind": "port_rail"},
    "makhachkala": {"id": "makhachkala", "name": "Махачкала", "country": "Россия",
                    "lat": 42.98, "lon": 47.50, "kind": "port_rail"},
    "baku": {"id": "baku", "name": "Баку", "country": "Азербайджан",
             "lat": 40.37, "lon": 49.85, "kind": "port_rail"},
    "aktau": {"id": "aktau", "name": "Актау", "country": "Казахстан",
              "lat": 43.65, "lon": 51.15, "kind": "port_rail"},
    "turkmenbashi": {"id": "turkmenbashi", "name": "Туркменбаши", "country": "Туркменистан",
                     "lat": 40.02, "lon": 52.97, "kind": "port"},
    "enzeli": {"id": "enzeli", "name": "Энзели", "country": "Иран",
               "lat": 37.47, "lon": 49.46, "kind": "port"},
    "amirabad": {"id": "amirabad", "name": "Амирабад", "country": "Иран",
                 "lat": 36.85, "lon": 53.35, "kind": "port_rail"},
    "astara": {"id": "astara", "name": "Астара", "country": "Азербайджан / Иран",
               "lat": 38.45, "lon": 48.87, "kind": "border"},
    "tehran": {"id": "tehran", "name": "Тегеран", "country": "Иран",
               "lat": 35.69, "lon": 51.39, "kind": "rail_hub"},
    "bandar_abbas": {"id": "bandar_abbas", "name": "Бендер-Аббас", "country": "Иран",
                     "lat": 27.19, "lon": 56.27, "kind": "port_rail"},
}

# Weather on the Caspian sea legs (demo readings, 12 Nov 2026).
WEATHER = {
    "caspian_north": {"segment": "Астрахань — Актау", "wind_ms": 11, "wave_m": 1.2,
                      "updated": "2026-11-12 06:00"},
    "caspian_west": {"segment": "Махачкала — Амирабад", "wind_ms": 9, "wave_m": 0.9,
                     "updated": "2026-11-12 06:00"},
}
STORM = {"wind_ms": 19, "wave_m": 3.4, "label": "Шторм: ветер 19 м/с, волна 3,4 м"}

# Port congestion (0-100%) and customs queue, demo snapshot.
PORTS = {
    "astrakhan": {"node": "astrakhan", "congestion_pct": 62, "customs_queue_h": 9},
    "makhachkala": {"node": "makhachkala", "congestion_pct": 45, "customs_queue_h": 6},
    "aktau": {"node": "aktau", "congestion_pct": 71, "customs_queue_h": 11},
    "amirabad": {"node": "amirabad", "congestion_pct": 38, "customs_queue_h": 5},
    "baku": {"node": "baku", "congestion_pct": 55, "customs_queue_h": 7},
    "enzeli": {"node": "enzeli", "congestion_pct": 30, "customs_queue_h": 4},
}

# Emission factors, g CO2 per tonne-km (published ranges, estimates).
CO2_G_PER_TKM = {"rail": 22, "sea": 12, "road": 62}
# Cost, USD per tonne-km (rough corridor estimates).
COST_USD_PER_TKM = {"rail": 0.055, "sea": 0.028, "road": 0.12}

ROUTES = [
    {
        "id": "R1", "name": "Западный — через Махачкалу",
        "via": "Махачкала → паром → Амирабад",
        "legs": [
            {"id": "R1-L1", "mode": "rail", "from": "moscow", "to": "makhachkala",
             "distance_km": 1820, "base_h": 40, "port": "makhachkala", "customs_h": 0},
            {"id": "R1-L2", "mode": "sea", "from": "makhachkala", "to": "amirabad",
             "distance_km": 540, "base_h": 32, "sea": "caspian_west",
             "sailing_interval_h": 24, "port": "amirabad", "customs_h": 5},
            {"id": "R1-L3", "mode": "rail", "from": "amirabad", "to": "bandar_abbas",
             "distance_km": 1150, "base_h": 28, "port": None, "customs_h": 0},
        ],
    },
    {
        "id": "R2", "name": "Восточный — через Астрахань и Актау",
        "via": "Астрахань → паром → Актау → Иран",
        "legs": [
            {"id": "R2-L1", "mode": "rail", "from": "moscow", "to": "astrakhan",
             "distance_km": 1280, "base_h": 30, "port": "astrakhan", "customs_h": 0},
            {"id": "R2-L2", "mode": "sea", "from": "astrakhan", "to": "aktau",
             "distance_km": 460, "base_h": 28, "sea": "caspian_north",
             "sailing_interval_h": 24, "port": "aktau", "customs_h": 11},
            {"id": "R2-L3", "mode": "rail", "from": "aktau", "to": "bandar_abbas",
             "distance_km": 1750, "base_h": 44, "port": None, "customs_h": 4},
        ],
    },
    {
        "id": "R3", "name": "Сухопутный — через Баку",
        "via": "Баку → Астара → Тегеран (без моря)",
        "legs": [
            {"id": "R3-L1", "mode": "rail", "from": "moscow", "to": "baku",
             "distance_km": 2200, "base_h": 52, "port": "baku", "customs_h": 0},
            {"id": "R3-L2", "mode": "rail", "from": "baku", "to": "astara",
             "distance_km": 320, "base_h": 9, "port": None, "customs_h": 7},
            {"id": "R3-L3", "mode": "rail", "from": "astara", "to": "bandar_abbas",
             "distance_km": 1350, "base_h": 34, "port": None, "customs_h": 0},
        ],
    },
]

DOC_TYPES = {
    "invoice": "Счёт-фактура",
    "packing_list": "Упаковочный лист",
    "certificate_of_origin": "Сертификат происхождения",
    "insurance": "Страховой полис",
    "phytosanitary": "Фитосанитарный сертификат",
}

SHIPMENTS = [
    {"id": "SHP-1042", "name": "Электроника", "cargo": "2 × 40ft, бытовая техника",
     "weight_t": 48, "route_id": "R1", "current_leg": 1,
     "status": "в пути", "eta": "2026-11-16 14:00",
     "docs": {"invoice": "verified", "packing_list": "verified",
              "certificate_of_origin": "verified", "insurance": "pending"}},
    {"id": "SHP-1043", "name": "Зерно", "cargo": "насыпью, пшеница",
     "weight_t": 1200, "route_id": "R2", "current_leg": 0,
     "status": "погрузка", "eta": "2026-11-18 09:00",
     "docs": {"invoice": "verified", "packing_list": "verified",
              "certificate_of_origin": "pending", "insurance": "verified",
              "phytosanitary": "missing"}},
    {"id": "SHP-1044", "name": "Стройматериалы", "cargo": "металлоконструкции",
     "weight_t": 96, "route_id": "R3", "current_leg": 1,
     "status": "в пути", "eta": "2026-11-15 20:00",
     "docs": {"invoice": "verified", "packing_list": "verified",
              "certificate_of_origin": "verified", "insurance": "verified"}},
]


def get_route(route_id: str) -> dict | None:
    return next((r for r in ROUTES if r["id"] == route_id), None)


def get_shipment(shipment_id: str) -> dict | None:
    return next((s for s in SHIPMENTS if s["id"] == shipment_id), None)
