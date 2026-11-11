"""Seed/demo data for the Caspian Twin prototype.

HONESTY NOTE (read before the demo):
Every number here is DEMO data. The *trend* follows published facts:
- Caspian level falls ~9 cm/year on average (stated at the Caspian
  Environmental Summit, 9 Oct 2026);
- water-surface area shrank from 392.3k to 357.9k km2 since 2006
  (State Oceanographic Institute, via press);
- digital-twin scenarios to 2050: -32 m (positive) / -34 m (negative)
  (Central Asian Institute for Environmental Research, Forbes.kz).
The yearly series below is an illustrative straight-line
approximation of that trend, NOT measured observations. On the demo
we say this out loud — judges forgive demo data, they do not forgive
fake precision. Swap this module for a real source (CSV/API) without
touching the API or the frontend: everything reads through here.
"""
from __future__ import annotations

# Level in metres (Baltic height system), illustrative series 2006-2026.
LEVEL_HISTORY = [
    {"year": 2006, "level_m": -26.90},
    {"year": 2007, "level_m": -26.99},
    {"year": 2008, "level_m": -27.07},
    {"year": 2009, "level_m": -27.18},
    {"year": 2010, "level_m": -27.24},
    {"year": 2011, "level_m": -27.33},
    {"year": 2012, "level_m": -27.44},
    {"year": 2013, "level_m": -27.51},
    {"year": 2014, "level_m": -27.62},
    {"year": 2015, "level_m": -27.71},
    {"year": 2016, "level_m": -27.79},
    {"year": 2017, "level_m": -27.88},
    {"year": 2018, "level_m": -27.97},
    {"year": 2019, "level_m": -28.06},
    {"year": 2020, "level_m": -28.15},
    {"year": 2021, "level_m": -28.24},
    {"year": 2022, "level_m": -28.33},
    {"year": 2023, "level_m": -28.42},
    {"year": 2024, "level_m": -28.52},
    {"year": 2025, "level_m": -28.61},
    {"year": 2026, "level_m": -28.70},
]

CURRENT_YEAR = 2026
CURRENT_LEVEL_M = -28.70
AREA_2006_KM2 = 392_300
AREA_2026_KM2 = 357_900

# Forecast scenarios: level in 2050, metres. Linear interpolation.
SCENARIOS = {
    "optimistic": {
        "id": "optimistic",
        "name": "Позитивный",
        "level_2050_m": -32.0,
        "description": "Сток Волги стабилизируется, испарение растёт умеренно.",
    },
    "baseline": {
        "id": "baseline",
        "name": "Базовый",
        "level_2050_m": -33.0,
        "description": "Тренд 2006–2026 продолжается без резких изменений.",
    },
    "pessimistic": {
        "id": "pessimistic",
        "name": "Негативный",
        "level_2050_m": -34.0,
        "description": "Жаркие маловодные годы, сток падает, испарение растёт.",
    },
}

PORTS = [
    {"id": "makhachkala", "name": "Махачкала", "country": "Россия",
     "lat": 42.98, "lon": 47.50, "depth_m": 7.0, "cargo": "нефть, зерно, генеральные грузы"},
    {"id": "kaspiysk", "name": "Каспийск", "country": "Россия",
     "lat": 42.88, "lon": 47.64, "depth_m": 5.5, "cargo": "рыбный порт, каботаж"},
    {"id": "derbent", "name": "Дербент", "country": "Россия",
     "lat": 42.06, "lon": 48.29, "depth_m": 4.5, "cargo": "туризм, каботаж"},
    {"id": "astrakhan", "name": "Астрахань", "country": "Россия",
     "lat": 46.35, "lon": 48.04, "depth_m": 4.2, "cargo": "река–море, зерно, контейнеры"},
    {"id": "baku", "name": "Баку", "country": "Азербайджан",
     "lat": 40.37, "lon": 49.85, "depth_m": 8.5, "cargo": "нефть, паромы, контейнеры"},
    {"id": "aktau", "name": "Актау", "country": "Казахстан",
     "lat": 43.65, "lon": 51.15, "depth_m": 6.5, "cargo": "нефть, зерно, паромы"},
    {"id": "turkmenbashi", "name": "Туркменбаши", "country": "Туркменистан",
     "lat": 40.02, "lon": 52.97, "depth_m": 6.0, "cargo": "нефть, паромы, генеральные грузы"},
    {"id": "enzeli", "name": "Энзели", "country": "Иран",
     "lat": 37.47, "lon": 49.46, "depth_m": 5.0, "cargo": "рыба, каботаж, туризм"},
]

# Simplified Caspian shoreline polygon (demo geometry, not survey data).
SEA_POLYGON = [
    [47.10, 49.00], [46.90, 50.80], [46.30, 51.70], [45.60, 52.40],
    [44.90, 52.90], [44.00, 52.70], [43.40, 51.90], [42.90, 51.30],
    [42.40, 51.00], [41.70, 51.20], [41.00, 51.80], [40.30, 52.60],
    [39.80, 53.10], [39.10, 53.40], [38.40, 53.10], [37.70, 52.20],
    [37.20, 51.00], [37.00, 50.00], [37.40, 49.40], [38.20, 49.20],
    [39.00, 49.30], [39.90, 49.60], [40.80, 49.90], [41.70, 49.80],
    [42.50, 49.30], [43.20, 49.00], [44.00, 48.90], [44.90, 48.70],
    [45.70, 48.60], [46.40, 48.70], [47.10, 49.00],
]

STATIONS = [
    {"id": "st-makhachkala", "name": "Буй «Махачкала»", "lat": 43.05, "lon": 47.75,
     "temp_c": 14.2, "pollution_index": 42, "status": "ok"},
    {"id": "st-derbent", "name": "Пост «Дербент»", "lat": 42.10, "lon": 48.45,
     "temp_c": 14.8, "pollution_index": 38, "status": "ok"},
    {"id": "st-volga", "name": "Устье Волги", "lat": 46.10, "lon": 48.90,
     "temp_c": 12.6, "pollution_index": 61, "status": "warning"},
    {"id": "st-aktau", "name": "Буй «Актау»", "lat": 43.70, "lon": 51.35,
     "temp_c": 13.9, "pollution_index": 47, "status": "ok"},
    {"id": "st-baku", "name": "Пост «Баку»", "lat": 40.30, "lon": 50.10,
     "temp_c": 15.4, "pollution_index": 73, "status": "warning"},
    {"id": "st-enzeli", "name": "Пост «Энзели»", "lat": 37.50, "lon": 49.70,
     "temp_c": 16.1, "pollution_index": 35, "status": "ok"},
]

PROTECTED_AREAS = [
    {"id": "pa-dagestan", "name": "Самурский заказник", "lat": 41.85, "lon": 48.55,
     "note": "Дельта Самура, нерестилища и водоплавающие птицы"},
    {"id": "pa-volga", "name": "Астраханский заповедник", "lat": 46.05, "lon": 48.75,
     "note": "Дельта Волги, крупнейшее место гнездования на Каспии"},
    {"id": "pa-tyuleniy", "name": "Тюлений архипелаг", "lat": 44.80, "lon": 50.40,
     "note": "Лежбища каспийской нерпы"},
]

ALERTS = [
    {"id": "al-1", "level": "warning", "title": "Уровень у Астрахани: подходной канал мельчает",
     "text": "Глубина на подходе упала ниже 4 м — суда с осадкой от 3,5 м идут с ограничением.",
     "place": "Астрахань", "time": "2026-11-11 09:20"},
    {"id": "al-2", "level": "warning", "title": "Индекс загрязнения у Баку — 73 из 100",
     "text": "Превышение фона в 1,8 раза. Рекомендован повторный забор проб.",
     "place": "Баку", "time": "2026-11-10 17:40"},
    {"id": "al-3", "level": "info", "title": "Прогноз: к 2050 году уровень −32…−34 м",
     "text": "По сценариям цифрового двойника северный Каспий теряет до 30% акватории.",
     "place": "Весь Каспий", "time": "2026-11-09 12:00"},
    {"id": "al-4", "level": "ok", "title": "Нерест в дельте Самура проходит штатно",
     "text": "Посты Самурского заказника не фиксируют отклонений за неделю.",
     "place": "Самур", "time": "2026-11-08 08:15"},
]


def forecast_level(scenario_id: str, year: int) -> float:
    """Linear interpolation from 2026 level to the scenario's 2050 level."""
    target = SCENARIOS[scenario_id]["level_2050_m"]
    frac = (year - CURRENT_YEAR) / (2050 - CURRENT_YEAR)
    frac = max(0.0, min(1.0, frac))
    return round(CURRENT_LEVEL_M + (target - CURRENT_LEVEL_M) * frac, 2)


def simulate(year: int, scenario_id: str) -> dict:
    """What happens to ports and shipping at a given year + scenario."""
    level = forecast_level(scenario_id, year)
    drop = round(CURRENT_LEVEL_M - level, 2)  # metres lost vs 2026
    ports = []
    for p in PORTS:
        remaining = round(p["depth_m"] - drop, 2)
        if remaining < 3.0:
            status = "critical"
        elif remaining < 4.5:
            status = "warning"
        else:
            status = "ok"
        ports.append({**p, "remaining_depth_m": remaining, "status": status})
    at_risk = sum(1 for p in ports if p["status"] != "ok")
    area_loss_pct = round((AREA_2006_KM2 - AREA_2026_KM2) / AREA_2006_KM2 * 100, 1)
    return {
        "year": year,
        "scenario": SCENARIOS[scenario_id],
        "level_m": level,
        "drop_m": drop,
        "ports": ports,
        "ports_at_risk": at_risk,
        "area_lost_since_2006_pct": area_loss_pct,
        "verdict": (
            "Критично: часть портов теряет судоходные глубины — нужны дноуглубление и перенос причалов."
            if at_risk >= 4 else
            "Риски растут: мелководные порты первыми теряют глубины, северный Каспий мелеет."
            if at_risk >= 2 else
            "Умеренный риск: глубоководные порты держатся, но мелководье уже требует внимания."
        ),
    }
