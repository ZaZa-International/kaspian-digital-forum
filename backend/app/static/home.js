import { createCaspianScene } from "./caspian-scene.js";

const container = document.querySelector("#hero-map");
const readout = document.querySelector("#hero-readout");
const dialog = document.querySelector("#sources-dialog");
let map;

document.querySelectorAll('[data-open="sources-dialog"]').forEach((button) =>
  button.addEventListener("click", () => dialog.showModal()));
dialog?.querySelector("[data-close]")?.addEventListener("click", () => dialog.close());

if (container) {
  createCaspianScene(container, {
    mode: "hero",
    onPortSelect: (_id, node) => {
      if (node?.name) readout.textContent = `${node.name} · ${node.country || "Узел коридора"}`;
    },
  }).then((scene) => { map = scene; }).catch(() => {
    container.textContent = "Карта временно недоступна. Маршруты можно открыть в платформе.";
  });
}
window.addEventListener("pagehide", (event) => { if (!event.persisted) map?.dispose(); });
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
