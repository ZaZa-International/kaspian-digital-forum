# Дизайн-контракт Smart Caspian Transit — «Caspian Ops»

Редизайн фронтенда (ноябрь 2026). Источник решений: ui-ux-pro-max
(`--design-system`, density 8 / motion 2 / variance 4):

- **Стиль:** Data-Dense Dashboard (light) — плотная сетка, таблицы, минимум паддингов.
- **Палитра:** Logistics/Delivery (tracking blue + delivery orange), адаптирована
  под предмет: чернильный navy вместо синего foreground, холодные нейтралы.
- **Типографика:** Developer Mono — IBM Plex Sans (UI) + JetBrains Mono (данные).
  Обе гарнитуры имеют кириллицу; шрифты вендорятся локально (проект без CDN).
- Анти-паттерны из выдачи: ornate design, emoji-as-icons — запрещены.

Направление: **«Caspian Ops»** — диспетчерский пульт логистического SaaS.
Один радиус (6px), одна шкала шага (4px), один ratio типографики (1.200),
ровно один акцент surfaces (синий) + сигнальный оранжевый только для шторма/сценариев.

## Токены (primitive → semantic → component)

```css
:root {
  /* PRIMITIVE — палитра Logistics/Delivery + каспийские нейтралы */
  --blue-600:#2563EB; --blue-700:#1D4ED8; --blue-100:#DBE7FD; --blue-050:#EEF3FE;
  --navy-950:#0B1626; --navy-900:#122036;
  --orange-600:#EA580C; --orange-800:#9A3412; --orange-100:#FCE4D2; --orange-050:#FDF1E7;
  --green-700:#166534; --green-100:#DFF0E6;
  --red-600:#DC2626; --red-700:#B91C1C; --red-100:#FBE3E3;
  --slate-900:#16233B; --slate-600:#46586E; --slate-500:#5A6779;
  --slate-200:#D9E0EA; --slate-100:#E7ECF3; --slate-050:#F4F6FA;

  /* SEMANTIC */
  --color-bg:var(--slate-050);          /* страница */
  --color-surface:#FFFFFF;              /* панели */
  --color-surface-2:var(--slate-050);   /* шапки панелей, зебра */
  --color-ink:var(--navy-950);          /* основной текст */
  --color-ink-2:var(--slate-600);       /* вторичный */
  --color-ink-3:var(--slate-500);       /* мета (контраст ≥4.5) */
  --color-line:var(--slate-200);        /* хайрлайны */
  --color-line-soft:var(--slate-100);
  --color-primary:var(--blue-600);      /* действия, ссылки, серия «спокойно» */
  --color-primary-hover:var(--blue-700);
  --color-primary-soft:var(--blue-050);
  --color-signal:var(--orange-600);     /* шторм, сценарии, серия «шторм» */
  --color-signal-ink:var(--orange-800); /* текст на signal-soft */
  --color-signal-soft:var(--orange-050);
  --color-ok:var(--green-700);   --color-ok-soft:var(--green-100);
  --color-danger:var(--red-600); --color-danger-ink:var(--red-700); --color-danger-soft:var(--red-100);
  --color-ring:var(--blue-600);
  --route-r1:var(--blue-600);           /* Западный */
  --route-r2:#0F8A5F;                   /* Восточный */
  --route-r3:var(--orange-600);         /* Сухопутный */

  /* TYPE — ratio 1.200 (minor third), base 16 */
  --font-ui:"IBM Plex Sans",system-ui,sans-serif;
  --font-data:"JetBrains Mono",ui-monospace,monospace;
  --fs-xs:.694rem;   /* 11.1 — мета, mono-лейблы */
  --fs-sm:.833rem;   /* 13.3 — таблицы, вторичный текст */
  --fs-base:1rem;    /* 16   — body */
  --fs-md:1.2rem;    /* 19.2 — заголовки панелей */
  --fs-lg:1.44rem;   /* 23   — заголовки секций */
  --fs-xl:1.728rem;  /* 27.6 — KPI */
  --fs-2xl:2.074rem; /* 33.2 — крупные числа сводки */

  /* SPACE — шаг 4px */
  --sp-1:4px; --sp-2:8px; --sp-3:12px; --sp-4:16px;
  --sp-5:24px; --sp-6:32px; --sp-7:48px; --sp-8:64px;

  /* FORM / MOTION */
  --radius:6px;                 /* единственное значение, включая чипы */
  --border:1px solid var(--color-line);
  --shadow-near:0 1px 2px rgba(11,22,38,.08), 0 4px 12px -4px rgba(11,22,38,.12);
  --shadow-far:0 8px 28px -8px rgba(11,22,38,.28);
  --dur-fast:120ms; --dur:180ms;
  --ease:cubic-bezier(.2,0,0,1);
}
```

Component-токены: `--btn-bg/--btn-bg-hover`, `--panel-bg`, `--chip-*`,
`--table-*` — алиасы semantic-слоя в `styles.css`.

## Правила

1. Любое значение в CSS/JS/SVG трассируется к токену; сырых hex в компонентах нет
   (JS читает цвета маршрутов через `getComputedStyle`).
2. Иконки — inline-SVG спрайт (штрих 1.75, скругления как у Lucide), без эмодзи.
3. Числа везде `font-variant-numeric: tabular-nums`; ID/хеши/коды — `--font-data`.
4. Статусы — прямоугольные чипы 6px с точкой, не pill; семантические цвета
   только по смыслу (ok/warn/danger), не декоративно.
5. Все интерактивные элементы: rest / hover / active / focus-visible / disabled /
   busy (`aria-busy`, блокировка повторных кликов).
6. Sticky appbar 56px; `scroll-padding-top` compensates anchors; мобильные
   touch-targets ≥44px; `prefers-reduced-motion` respected.
7. Контраст: body ≥4.5:1, meta ≥4.5:1, крупный текст ≥3:1 (проверено расчётом).
8. Honest-data маркировка (live/демо/синтетика) сохраняется во всех местах.
9. Текст чипов маршрутов — `color-mix(route 72%, ink)` (контраст ≥4.5 на тинте);
   линии/свотчи — полный хрома (≥3:1 как графика). Активный шторм-стейт —
   фон `signal-ink` с белым текстом. Мета-текст `ink-3` проверен на всех
   поверхностях (bg, surface, surface-2, danger-soft, ok-soft) ≥4.5:1.

## Карта секций

01 Сводка (KPI-полоса + коридор в цифрах) · 02 Коридор (карта + легенда +
шторм-тумблер, таблица сравнения, ETA-распределение, порты) · 03 Поиск маршрута
(+ Парето) · 04 Исключения · 05 Расчёт груза и сценарии · 06 Двойник груза +
pre-clearance (две колонки) · 07 Источники.
