# Дизайн-контракт — Smart Caspian Transit «Caspian Watch»

Источник решений: ui-ux-pro-max (style: `dark-mode-oled`, адаптирован;
palette: `Logistics/Delivery` — tracking blue + delivery orange, адаптация
под тёмный фон; typography: Unbounded — кириллица подтверждена в базе,
IBM Plex Sans/Mono — суперсемейство, кириллица). Leaflet API сверен
через context7 (`/leaflet/leaflet` v1.9.4: panes, Path.className,
CARTO dark basemap). Шрифты self-hosted (woff2) — демо работает офлайн.

## Бриф (8 строк)

```
Purpose:    Ops-консоль цифрового двойника Каспия: прогноз задержек, сравнение маршрутов, действие до срыва
Audience:   Логисты + жюри хакатона; desktop 1440, mobile 390, возможен офлайн
Tone:       «Caspian Watch» — тёмная штурманская рубка: ECDIS-карты + ops-дашборды, полуночная вахта
Reference:  Судовой мостик, диспетчерские авиации; сетка Swiss, данные моноширинно
Palette:    Каспийская глубина (teal-navy), пена-чернила, морской сигнал-синий, шторм-янтарь, danger-красный, safe-зелёный
Type:       Unbounded (display, кириллица) + IBM Plex Sans (UI) + IBM Plex Mono (телеметрия)
Memorable:  ШТОРМ-брейкер: один физический тумблер переводит всю консоль в аварийный режим — янтарная подсветка, пульс морских плеч, тикер волны
Restraint:  Без glassmorphism, без gradient-headlines, без emoji-иконок (SVG sprite), один radius 4px (pill 999 — единственное исключение), две тени, motion только со смыслом + prefers-reduced-motion
```

## Токены (primitive → semantic → component)

```css
:root {
  /* PRIMITIVE — глубина Каспия + сигнальные тона логистики (DB), AA на тёмном */
  --c-abyss:#061219; --c-deep:#0A1C26; --c-shelf:#102A38; --c-line:#1D3A4A;
  --c-foam:#E9F2F4; --c-spray:#A7BFC9; --c-mist:#7C99A6;
  --c-sea:#56B4E9; --c-storm:#F5A524; --c-red:#F0564A; --c-green:#3FB97F;
  --c-rail:#3FB97F; --c-road:#F5A524;

  /* SEMANTIC */
  --bg:var(--c-abyss); --surface:var(--c-deep); --surface-2:var(--c-shelf);
  --line:var(--c-line); --ink:var(--c-foam); --ink-2:var(--c-spray); --ink-3:var(--c-mist);
  --accent:var(--c-sea); --warn:var(--c-storm); --danger:var(--c-red); --ok:var(--c-green);
  --on-accent:#04121B;

  /* TYPE — major third 1.25, один ratio */
  --font-display:"Unbounded", "IBM Plex Sans", sans-serif;
  --font-text:"IBM Plex Sans", system-ui, sans-serif;
  --font-mono:"IBM Plex Mono", ui-monospace, monospace;
  --step--1:0.8rem; --step-0:1rem; --step-1:1.25rem; --step-2:1.5625rem;
  --step-3:1.9531rem; --step-4:2.4414rem; --step-5:3.0518rem;

  /* SPACE — база 4px, dense dashboard (density 8/10) */
  --sp-1:4px; --sp-2:8px; --sp-3:12px; --sp-4:16px; --sp-5:24px;
  --sp-6:32px; --sp-7:48px; --sp-8:64px;

  /* FORM — один radius, две тени */
  --radius:4px; --radius-pill:999px;
  --shadow-1:0 1px 2px rgba(0,0,0,.4);
  --shadow-2:0 12px 32px rgba(2,10,16,.55);

  /* MOTION — три длительности, два easing */
  --dur-1:120ms; --dur-2:220ms; --dur-3:420ms;
  --ease:cubic-bezier(.2,0,0,1); --ease-out:cubic-bezier(.16,1,.3,1);
}
```

## Компонентные правила

- **Шапка-командный пост**: sticky, brand + live-тикер погоды + ШТОРМ-брейкер
  (role="switch", aria-checked) — всегда виден, не в модалке.
- **Секции**: mono-индекс `01 /` + заголовок Unbounded; группировка пространством,
  карточки только там, где контент реально изолирован.
- **Карта**: CARTO dark_nolabels + labels-pane (context7), офлайн — фон-сетка
  graticule; маршруты R1=#56B4E9 / R2=#3FB97F / R3=#F5A524 (разделимы яркостью
  + прямые подписи), узлы circleMarker, попапы тёмные.
- **Шторм-режим**: `body.storm` — янтарный glow фона, alert-ribbon, морские
  плечи пульсируют (dash-animation), тикер волны краснеет. reduced-motion: без пульса.
- **Таблицы/данные**: tabular-nums, mono для hash/времени, hover строк,
  best-бейджи (⏱→SVG clock, 🌱→leaf, 🛡→shield).
- **Иконки**: inline SVG sprite (ship/rail/truck/wave/wind/alert/doc/shield/
  search/box/clock/leaf/coin/route/plus/check/x/anchor/radar) — никогда solo,
  всегда с текстом или aria-label.
- **Состояния**: rest/hover/active/focus-visible/disabled у всех кнопок,
  селектов, инпутов; focus-ring 2px accent, никогда outline:none.
- **Скроллбары**: в палитре (thumb --c-line, hover --c-sea).
- **Контраст**: body ≥ 4.5:1 (foam/spray на deep), meta --c-mist ≥ 4.5 на surface.

## DOM-контракт (не ломать)

Все id из app.js сохранены: kpi-*, corridor-stats, storm-btn/note, live-badge,
map, compare-table, search-*, exceptions-list, quote-*, scenario-*, ports-grid,
shipment-*, new-*, create-*, timeline, events-timeline, verdict, ml-box,
docs-list, clearance-*, ledger, sources. Классы JS-генерации: pill ok/medium/high,
path-card, port-card, bar i.hot/warm, stat, hint, why, bottleneck, exc, action,
hash, dot verified/pending/missing, storm-btn.on.

---

## v2 — «Caspian Chart» (редизайн после ревью: тёмный teal = нейрослоп)

Арт-дирекшн: **Admiralty sea-chart + судоходная газета Lloyd's**. Светлая
карточная бумага, чернильный navy, карминная магента (реальный цвет маршрутов
на морских картах), батиметрический teal. Serif-дисплей Playfair Display
(кириллица) + IBM Plex Sans/Mono. Открытые секции с линейками вместо
карточек-коробок; газета-масthead с двойной линейкой; KPI-лента с колонками;
карта в рамке с компасной розой, масштабной линейкой и надписью «Каспийское
море» разрядкой. Тайлы — Esri Light Gray (без API-ключа; CARTO сломан —
«KEY REQUIRED»), лёгкий sepia-фильтр под бумагу. Шторм: бумага теплеет,
морские плечи R1/R2 краснеют и пульсируют, alert-ribbon красный.

Палитра v2: paper #F4EEE1 / paper-2 #EAE1CE / paper-3 #FBF7EE; ink #1C2B36 /
ink-2 #46586A / ink-3 #566878; rule #CDC2A9 / rule-2 #B7A98C; sea #D3E2E1 /
sea-2 #A9CBCD; magenta #BE1E68 / magenta-2 #9C1453; red #C0392B; amber #B26A00
(fill #E39A2D); green #2E6B4F; teal #1F7A8C. Маршруты: R1 #BE1E68, R2 #1F7A8C,
R3 #D98324 (разделимы hue + luminance). Radius 3px, pill 999. Тени: hairline +
plate. Motion: 120/220/420ms, ease (.2,0,0,1) / out (.16,1,.3,1).