"""Data for Smart Caspian Transit — with REAL sources where we have them.

What is real here (checked 12 Nov 2026):
- Distances: haversine between ports/nodes from coordinates, x1.1-1.15
  route factor for sea legs. Makhachkala-Amirabad 844 km straight
  (NOT 540 as in v0.1 — that was wrong and is fixed here).
- Weather: live via Open-Meteo marine API (see weather.py); 90-day
  archive stats baked into archive_weather.json (wave/wind
  distributions per sea segment, fetched 2026-11-12).
- Corridor: 21.5M t in 2025; Jan-Sep 2026 rail 8.9M t (+9.6% YoY),
  trans-Caspian route +19.3%; 2030 forecast 32M t (Government of RF,
  via RZD / press, Oct 2026). Makhachkala port: 3.5M t in 2025,
  Jan-Aug 2026 >2.9M t (+42.5%), capacity ~10-11M t by 2030.
- Ferries: NO fixed timetable on the Caspian — sail when loaded and
  weather allows; Baku-Turkmenbashi ~17h, Kuryk-Baku <24h; waits of
  1-2 days are normal, up to a week at congestion (Wikivoyage,
  GetTransport 2026). Hence sailing_interval_h = 72.
- CO2 factors: DESNZ 2026 (UK Government), well-to-wheel: rail
  freight 32.74, RoRo ferry 63.28, articulated HGV 97.49 g/tkm.
  Our sea legs are RoRo ferries — hence sea = 63, not 12.
- Freight rates: Argus Caspian Transport, Oct 2025: Aktau-Iranian
  ports $40-45/t, Astrakhan-Iranian ports $63-65/t. Sea rate 0.045
  $/t-km reproduces ~$42/t for the 930 km Makhachkala-Amirabad leg.

Still demo (no live feed exists in the open): port congestion %,
customs queues, shipment list. Swap in data.py only.
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
    "aktau": {"id": "aktau", "name": "Актау / Курык", "country": "Казахстан",
              "lat": 43.65, "lon": 51.15, "kind": "port_rail",
              "note": "Паромный терминал — Курык (южнее Актау); порт Актау не принимает паромы с 2018 (Caravanistan, 06.2026)."},
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

# Sea-leg midpoints for live weather (Open-Meteo marine API).
SEA_SEGMENTS = {
    "caspian_north": {"segment": "Астрахань — Актау", "lat": 45.0, "lon": 49.6},
    "caspian_west": {"segment": "Махачкала — Амирабад", "lat": 39.9, "lon": 50.4},
}

# Demo fallback weather (used only when live fetch fails / offline).
WEATHER = {
    "caspian_north": {"segment": "Астрахань — Актау", "wind_ms": 11, "wave_m": 1.2,
                      "updated": "2026-11-12 06:00"},
    "caspian_west": {"segment": "Махачкала — Амирабад", "wind_ms": 9, "wave_m": 0.9,
                     "updated": "2026-11-12 06:00"},
}
STORM = {"wind_ms": 19, "wave_m": 3.4,
         "label": "Шторм: ветер 19 м/с, волна 3,4 м (экстремум — за 90 дней макс. волна 2,52 м)"}

# Port congestion (0-100%) and customs queue — demo snapshot.
PORTS = {
    "astrakhan": {"node": "astrakhan", "congestion_pct": 62, "customs_queue_h": 9},
    "makhachkala": {"node": "makhachkala", "congestion_pct": 45, "customs_queue_h": 6},
    "aktau": {"node": "aktau", "congestion_pct": 71, "customs_queue_h": 11},
    "amirabad": {"node": "amirabad", "congestion_pct": 38, "customs_queue_h": 5},
    "baku": {"node": "baku", "congestion_pct": 55, "customs_queue_h": 7},
    "enzeli": {"node": "enzeli", "congestion_pct": 30, "customs_queue_h": 4},
}

# CO2, g per tonne-km (DESNZ 2026, well-to-wheel).
CO2_G_PER_TKM = {"rail": 33, "sea": 63, "road": 97}
# Cost, USD per tonne-km (estimates; sea calibrated to Argus Oct 2025).
COST_USD_PER_TKM = {"rail": 0.04, "sea": 0.045, "road": 0.12}

# Real corridor numbers for the pitch (sources in README).
CORRIDOR_STATS = [
    {"value": "21,5 млн т", "label": "грузопоток МТК «Север–Юг» в 2025 году"},
    {"value": "+19,3%", "label": "транскаспийский маршрут, янв–сен 2026 к 2025"},
    {"value": "8,9 млн т", "label": "ж/д перевозки по коридору, янв–сен 2026 (+9,6%)"},
    {"value": "15–20 дней", "label": "доставка Россия–Иран вместо 35–45 через Суэц"},
    {"value": "32 млн т", "label": "прогноз грузопотока коридора к 2030 году"},
    {"value": "+42,5%", "label": "порт Махачкала, янв–авг 2026 (2,9 млн т)"},
]

ROUTES = [
    {
        "id": "R1", "name": "Западный — через Махачкалу",
        "via": "Махачкала → паром → Амирабад",
        "legs": [
            {"id": "R1-L1", "mode": "rail", "from": "moscow", "to": "makhachkala",
             "distance_km": 1820, "base_h": 40, "port": "makhachkala", "customs_h": 0},
            {"id": "R1-L2", "mode": "sea", "from": "makhachkala", "to": "amirabad",
             "distance_km": 930, "base_h": 50, "sea": "caspian_west",
             "sailing_interval_h": 72, "port": "amirabad", "customs_h": 5,
             "conditional": True,
             "note": "Условное плечо: регулярная линия Махачкала–Амирабад не подтверждена "
                     "(тестовый рейс 2020, анонсы 2026 — см. docs/RESEARCH.md)."},
            {"id": "R1-L3", "mode": "rail", "from": "amirabad", "to": "bandar_abbas",
             "distance_km": 1300, "base_h": 32, "port": None, "customs_h": 0},
        ],
    },
    {
        "id": "R2", "name": "Восточный — через Астрахань и Актау",
        "via": "Астрахань → паром → Актау → Иран",
        "legs": [
            {"id": "R2-L1", "mode": "rail", "from": "moscow", "to": "astrakhan",
             "distance_km": 1280, "base_h": 30, "port": "astrakhan", "customs_h": 0},
            {"id": "R2-L2", "mode": "sea", "from": "astrakhan", "to": "aktau",
             "distance_km": 445, "base_h": 26, "sea": "caspian_north",
             "sailing_interval_h": 72, "port": "aktau", "customs_h": 11},
            {"id": "R2-L3", "mode": "rail", "from": "aktau", "to": "bandar_abbas",
             "distance_km": 2100, "base_h": 52, "port": None, "customs_h": 4,
             "conditional": True,
             "note": "Условное длинное плечо: ж/д через Казахстан–Туркменистан–Иран (KTI), "
                     "маршрут абстрактный, точный километраж не верифицирован."},
        ],
    },
    {
        "id": "R3", "name": "Сухопутный — через Баку",
        "via": "Баку → Астара → Тегеран (без моря)",
        "legs": [
            {"id": "R3-L1", "mode": "rail", "from": "moscow", "to": "baku",
             "distance_km": 2200, "base_h": 52, "port": "baku", "customs_h": 0},
            {"id": "R3-L2", "mode": "road", "from": "baku", "to": "astara",
             "distance_km": 300, "base_h": 6, "port": None, "customs_h": 7},
            {"id": "R3-L3", "mode": "rail", "from": "astara", "to": "bandar_abbas",
             "distance_km": 1500, "base_h": 36, "port": None, "customs_h": 0},
        ],
    },
]

# Full corridor graph: every route leg + extra edges that enable
# alternative paths (Baku-Turkmenbashi and Aktau-Baku ferries,
# Astara-Tehran rail, Turkmenbashi-Tehran rail, Astrakhan-Makhachkala
# rail). Distances: haversine x1.1 route factor for sea, rail/road
# estimates from node coordinates. Dynamic routing (graph.py) searches
# this graph instead of the three hardcoded routes.
GRAPH_EDGES: list[dict] = []
for _r in ROUTES:
    for _leg in _r["legs"]:
        GRAPH_EDGES.append({**_leg, "route": _r["id"]})
GRAPH_EDGES += [
    {"id": "G-BAK-TBS", "mode": "sea", "from": "baku", "to": "turkmenbashi",
     "distance_km": 620, "base_h": 34, "sea": "caspian_west",
     "sailing_interval_h": 72, "port": "turkmenbashi", "customs_h": 4},
    {"id": "G-AKT-BAK", "mode": "sea", "from": "aktau", "to": "baku",
     "distance_km": 480, "base_h": 28, "sea": "caspian_north",
     "sailing_interval_h": 72, "port": "baku", "customs_h": 6},
    {"id": "G-AST-TEH", "mode": "rail", "from": "astara", "to": "tehran",
     "distance_km": 450, "base_h": 12, "port": None, "customs_h": 0},
    {"id": "G-TEH-BAB", "mode": "rail", "from": "tehran", "to": "bandar_abbas",
     "distance_km": 1150, "base_h": 28, "port": None, "customs_h": 0},
    {"id": "G-TBS-TEH", "mode": "rail", "from": "turkmenbashi", "to": "tehran",
     "distance_km": 600, "base_h": 15, "port": None, "customs_h": 0},
    {"id": "G-ASR-MAH", "mode": "rail", "from": "astrakhan", "to": "makhachkala",
     "distance_km": 450, "base_h": 11, "port": "makhachkala", "customs_h": 0},
    # Enzeli (case-listed port) connected via conditional ferry + rail to Tehran.
    {"id": "G-MAH-ENZ", "mode": "sea", "from": "makhachkala", "to": "enzeli",
     "distance_km": 700, "base_h": 32, "sea": "caspian_west",
     "sailing_interval_h": 72, "port": "enzeli", "customs_h": 4,
     "conditional": True,
     "note": "Условное плечо: линия Махачкала–Энзели не подтверждена как регулярная "
             "(docs/RESEARCH.md); расстояние haversine×1.1, время — оценка."},
    {"id": "G-ENZ-TEH", "mode": "rail", "from": "enzeli", "to": "tehran",
     "distance_km": 340, "base_h": 10, "port": None, "customs_h": 0,
     "note": "Ж/д Энзели–Тегеран через Решт; расстояние оценочное (горный маршрут)."},
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
