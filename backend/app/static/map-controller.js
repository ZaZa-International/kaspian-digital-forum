const PREFERENCE = "sct-map-mode";
const modes = new Set(["2d", "3d"]);

function stylesheet(href) {
  if (document.querySelector(`link[href="${href}"]`)) return;
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = href;
  document.head.append(link);
}

function preference() {
  try { return localStorage.getItem(PREFERENCE); } catch { return null; }
}

/** Keep one geographic selection while exchanging its renderer. */
export async function createMapController(container, options = {}) {
  if (!container) throw new Error("Карта требует контейнер");
  stylesheet("/static/flat-map.css");
  const wrapper = document.createElement("section");
  wrapper.className = "map-controller";
  wrapper.dataset.purpose = options.mode || "ops";
  wrapper.setAttribute("aria-label", "Карта транспортного коридора");
  const bar = document.createElement("div");
  bar.className = "map-modebar";
  const label = document.createElement("span");
  label.textContent = "Карта коридора";
  const group = document.createElement("div");
  group.className = "map-mode-switch";
  group.setAttribute("role", "group");
  group.setAttribute("aria-label", "Режим карты");
  const buttons = new Map();
  for (const [mode, title] of [["3d", "3D"], ["2d", "2D"]]) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = title;
    button.dataset.mapMode = mode;
    button.setAttribute("aria-label", `${title} карта`);
    button.setAttribute("aria-pressed", "false");
    button.addEventListener("click", () => setMode(mode));
    group.append(button);
    buttons.set(mode, button);
  }
  const content = document.createElement("div");
  content.className = "map-mode-content";
  const status = document.createElement("p");
  status.className = "map-load-status";
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");
  bar.append(label, group);
  wrapper.append(bar, content, status);
  container.append(wrapper);
  let renderer = null, currentMode = null, generation = 0, disposed = false;
  const state = { route: "R1", progress: 0, scenario: { storm: false, horizon: 0 }, story: null, port: null };

  const callbacks = {
    ...options,
    onPortSelect(id, node) {
      state.port = id;
      options.onPortSelect?.(id, node);
    },
    onRouteSelect(id) {
      state.route = id;
      options.onRouteSelect?.(id);
    },
  };

  async function setMode(mode) {
    if (!modes.has(mode)) throw new Error("Неизвестный режим карты");
    if (disposed || (mode === currentMode && renderer)) return;
    const version = ++generation;
    renderer?.dispose();
    renderer = null;
    currentMode = null;
    const host = document.createElement("div");
    host.className = "map-renderer-host";
    content.replaceChildren(host);
    content.setAttribute("aria-busy", "true");
    status.hidden = false;
    status.textContent = `Загружаем ${mode.toUpperCase()} карту…`;

    const timeout = setTimeout(() => {
      if (disposed || generation !== version) return;
      if (mode === "3d" && options.mode !== "hero") {
        void setMode("2d");
      } else {
        status.textContent = `Загрузка ${mode.toUpperCase()} карты задерживается. Выберите другой режим.`;
      }
    }, 5000);
    try {
      const next = mode === "3d"
        ? await (await import("./caspian-scene.js")).createCaspianScene(host, callbacks)
        : await (await import("./flat-map.js")).createFlatMap(host, callbacks);
      if (disposed || generation !== version) { next.dispose(); return; }
      renderer = next;
      currentMode = mode;
      wrapper.dataset.mapMode = mode;
      label.textContent = mode === "3d" && host.querySelector('[data-renderer="svg"]')
        ? "3D недоступен · резервная схема" : "Карта коридора";
      renderer.setRoute(state.route);
      renderer.setScenario(state.scenario);
      renderer.setProgress(state.progress);
      if (state.story !== null) renderer.setStoryProgress?.(state.story);
      if (state.port) renderer.focusPort(state.port, { pan: false });
      renderer.resize();
      for (const [value, button] of buttons) button.setAttribute("aria-pressed", String(mode === value));
      try { localStorage.setItem(PREFERENCE, mode); } catch { /* Private browsing can disable storage. */ }
      status.hidden = true;
      container.dispatchEvent(new CustomEvent("mapmodechange", { detail: { mode } }));
    } catch (error) {
      if (disposed || generation !== version) return;
      content.replaceChildren();
      status.textContent = `Не удалось открыть ${mode.toUpperCase()} карту. Выберите режим, чтобы повторить.`;
      console.warn("Map renderer failed", error);
    } finally {
      clearTimeout(timeout);
      if (!disposed && generation === version) {
        content.setAttribute("aria-busy", "false");
      }
    }
  }

  const api = {
    setMode,
    getMode: () => currentMode,
    setRoute(route) { state.route = route; state.port = null; renderer?.setRoute(route); },
    setProgress(progress) { state.progress = Math.max(0, Math.min(1, Number(progress) || 0)); renderer?.setProgress(state.progress); },
    setScenario(scenario = {}) { state.scenario = { ...state.scenario, ...scenario }; renderer?.setScenario(state.scenario); },
    setStoryProgress(progress) { state.story = progress; renderer?.setStoryProgress?.(progress); },
    focusPort(id) { state.port = id; renderer?.focusPort(id); },
    resetView() { renderer?.resetView(); },
    fitRoute() { (renderer?.fitRoute || renderer?.resetView)?.call(renderer); },
    resize() { renderer?.resize(); },
    dispose() {
      disposed = true;
      generation += 1;
      renderer?.dispose();
      renderer = null;
      wrapper.remove();
    },
  };
  const initial = options.initialMode || preference();
  void setMode(modes.has(initial) ? initial : (options.mode === "hero" ? "3d" : "2d"));
  return api;
}
