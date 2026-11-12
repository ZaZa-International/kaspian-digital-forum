# Архитектура — Smart Caspian Transit

## Обзор

Монолит FastAPI + статический фронт, один процесс. Runtime-состояние
(созданные грузы, реестр документов, ledger, события) — в **SQLite (WAL)**
через `store.py`; справочные демо-данные — в `data.py`; погода — внешний
API с кэшем 30 мин и демо-фолбэком. Решение осознанное: для хакатона и
пилота на одном маршруте этого достаточно; горизонтальное масштабирование —
замена адаптеров данных и storage, а не переписывание.

```
browser ──► FastAPI (app.main)
              ├── data.py        узлы, граф (11/17), маршруты, seed-грузы,
              │                  факторы CO₂ (DESNZ 2026), ставки (оценка)
              ├── graph.py       Dijkstra + Yen k-shortest + Парето
              │                  (эвристика среди k кандидатов)
              ├── model.py       скоринг, Monte Carlo, единый сценарный
              │                  движок, таймлайн, исключения,
              │                  pre-clearance (серверные статусы), provenance
              ├── ml.py          ансамбль RF + HistGradientBoosting,
              │                  CSV-контракт, окклюзионная атрибуция
              ├── weather.py     Open-Meteo live (кэш 30 мин) + архив 90 дней
              ├── store.py       SQLite WAL: транзакции, монотонные ID,
              │                  идемпотентность, миграция legacy JSON
              └── static/        HTML/CSS/JS + PWA (sw.js v2, manifest),
                                 Leaflet вендорен локально (vendor/)
```

## Ключевые решения и обоснования

1. **Граф вместо трёх маршрутов.** Коридор — направленный граф
   (11 узлов, 17 рёбер: 9 плеч маршрутов R1–R3 + 8 дополнительных,
   включая подключение Энзели). Поиск — Dijkstra по прогнозному
   времени/стоимости/CO₂ + Yen k=3 (стабильный порядок на равных весах).
   Парето-метка — эвристика **среди возвращённых k кандидатов**, не
   глобальный многокритериальный поиск (подписано в API: `pareto_scope`).
   NetworkX не вводили: граф маленький, чистый stdlib быстрее и без
   зависимостей. Условные плечи (`conditional=true`): Махачкала–Амирабад,
   Махачкала–Энзели (линии не подтверждены), Актау–Бендер-Аббас (KTI,
   абстрактно). Актау = паромный терминал Курык.
2. **Гибридный прогноз.** Скоринг — объяснимый бейзлайн и фолбэк;
   ML-ансамбль — вторая оценка (эксперимент, синтетические метки).
   Если расходятся сильно — это сигнал, а не ошибка: показывается
   расхождение. ML обучается с нуля при старте, не «дообучается».
3. **Вероятностный ETA.** Monte Carlo (200 прогонов, seed 7): волна/ветер —
   bootstrap из реального архива, ожидание парома — Uniform(0, 72),
   загруженность/таможня — нормальные распределения вокруг демо-снимка.
   P50/P90/P95 + P(задержка > 24 ч) — **квантили симуляции**, не
   эмпирическая вероятность фактической доставки (подписано в API).
4. **Единый сценарный движок.** Приоритет один для всех эндпоинтов и
   Monte Carlo: явная волна/ветер > шторм > live/fallback. Сценарий
   (`close`, `extra_customs_h`, `wave_m`, `wind_ms`) применяется в
   forecast/compare/search/exceptions/shipments/timeline/ML. Время в
   пути = ход + ожидание парома (interval/2 = 36 ч на морское плечо,
   допущение) + задержки; пропуск рейса при волне ≥ 2 м — +48 ч.
   Индекс риска — эвристика delay/(delay+18) (пол 0,85 в шторм для
   морских маршрутов), не эмпирическая вероятность.
5. **Pre-clearance: серверные статусы + ledger v2.** Реестр документов
   в SQLite: клиент подаёт ссылки, статусы (missing/submitted/verified/
   rejected/expired/unknown) назначает сервер — симуляция таможенного
   органа «SCT Demo Customs Authority», правила `demo-rules-v1`
   (непустая ссылка → verified, `REJ-` → отказ, `EXP-` → истёк).
   Hash-chain: hash покрывает prev_hash + shipment + decision + doc_hash
   + rule_version + issuer; снимок документов хранится в записи.
   Добавление атомарно (BEGIN IMMEDIATE) — конкурентные submit не могут
   «раздвоить» цепь. Это tamper-evident демо-механика, **не DLT**:
   распределённого консенсуса нет.
6. **Persistence — SQLite, не файлы.** WAL, транзакции, монотонные ID
   из counter (не переиспользуются даже после сброса), идемпотентность
   по `Idempotency-Key`, однократная миграция legacy JSON (backup
   `*.migrated`). Concurrent-safe: потерянные записи и гонки JSON-файлов
   закрыты тестами. Сброс: `POST /api/demo/reset` или
   `scripts/reset_demo_data.py`. Docker: volume `sct-data`, non-root,
   healthcheck; `.dockerignore` держит контекст сборки малым (~330 КБ).
7. **События и provenance.** Таблица `events` (DCSA T&T-подобная модель:
   departure/arrival/gate_in/gate_out/load/discharge/customs_*/delay/note);
   `source` обязателен, `received_ts` ставит сервер. Таймлайн = модель
   (план/прогноз) + факты; демо-события seed-грузов маркированы
   `demo-simulation` (не GPS/AIS). `/api/provenance` — 11 источников с
   kind (live/historical/demo/synthetic/static/assumption).
8. **Безопасность.** Escaping всех динамических вставок в UI (stored XSS
   через имя/груз закрыт и проверен в браузере). Валидация диапазонов:
   волна 0–15 м, ветер 0–60 м/с, таможня 0–168 ч, вес 0 < w ≤ 5000 т,
   неизвестные узлы → 422. Опциональный `SCT_WRITE_TOKEN` (заголовок
   `X-Write-Token`), rate limit записей 60/мин/IP, лимит тела 32 КБ.
   Без токена демо-режим открыт и маркирован в UI.
9. **PWA и офлайн.** Service worker v2: network-first для навигации
   (свежий HTML онлайн, кэшированная оболочка офлайн), версионированный
   кэш статики, явный offline fallback; `/api/*` не кэшируется никогда.
   Иконки 192/512, полный manifest, офлайн-баннер, быстрая навигация,
   mobile CSS (touch targets 44px). Leaflet вендорен локально — CDN не
   нужен; тайлы OSM грузятся по сети (офлайн карта без тайлов).
10. **Честность данных.** Каждый источник помечен kind и оговорками
    (лицензии, proxy, допущения). См. DATA_SOURCES.md и RESEARCH.md.

## API

- `GET /api/health`, `/api/nodes`, `/api/ports`, `/api/weather`,
  `/api/weather/live`, `/api/corridor`
- `GET /api/graph` — узлы и рёбра
- `GET /api/routes`, `/api/routes/{id}/forecast`,
  `/api/routes/search?from_node&to_node&objective&k`
- `GET /api/compare` (+ P50/P90 симуляции в строках)
- `GET /api/shipments`, `/api/shipments/{id}`,
  `/api/shipments/{id}/timeline`, `/api/exceptions`
- `POST /api/shipments` (идемпотентность), `POST /api/shipments/{id}/events`
- `POST /api/clearance/submit`, `GET /api/clearance/ledger`,
  `GET /api/clearance/documents/{id}`
- `GET /api/provenance`, `POST /api/demo/reset`
- `GET /api/ml/info`, `/api/ml/forecast`, `/api/ml/shipment/{id}`

Прогнозные эндпоинты (forecast/compare/search/shipments/timeline/
exceptions/ML) принимают сценарий: `storm`, `close`, `extra_customs_h`,
`wave_m`, `wind_ms`. Версия приложения FastAPI — 0.1.0.

## Ограничения (известные, честные)

- Нет полноценной аутентификации/RBAC: опциональный write-токен и rate
  limit — демо-уровень; для пилота нужны API-ключи и роли.
- Загруженность портов и очереди таможни — демо-снимок; события —
  демо-симуляция/ручной ввод, не телеметрия.
- ML-метки синтетические: реальная точность появится только с историей
  план/факт (CSV-контракт готов, см. ML_METHODOLOGY.md).
- CO₂ — оценка (DESNZ 2026, UK proxy, без верификации, без хабов);
  ставки — оценка; индекс риска и квантили — не гарантии.
- Тайлы OSM — по сети; без интернета карта без тайлов (оболочка PWA,
  таблицы и двойник работают на кэше/фолбэке).
- SQLite — один узел, без репликации; для нескольких writers/инстансов
  нужен внешний storage (см. ROADMAP.md).
