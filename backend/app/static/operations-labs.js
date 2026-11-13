export const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (character) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character]);
const numberFormatter = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 1 });
export const number = (value) => Number.isFinite(value) ? numberFormatter.format(value) : "—";
export const unit = (value, suffix) => Number.isFinite(value) ? `${number(value)} ${suffix}` : "—";
export const hours = (value) => unit(value, "ч");
export const time = (value) => value ? new Date(value).toLocaleString("ru-RU", {
  day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
}) : "—";

export function showError(status, error, retry) {
  status.replaceChildren();
  const text = document.createElement("span");
  text.textContent = error.message;
  status.append(text);
  if (retry) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "lab-secondary";
    button.textContent = "Повторить";
    button.addEventListener("click", retry);
    status.append(button);
  }
}
