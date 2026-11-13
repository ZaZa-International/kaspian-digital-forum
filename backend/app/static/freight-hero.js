const track = document.querySelector("#freight-track");
const scene = track.querySelector(".freight-scene");
const video = document.querySelector("#freight-video");
const services = document.querySelector("#freight-services");
const strip = document.querySelector("#freight-strip");
const lead = strip.querySelector(".freight-lead");
const mobileLeadSlot = track.querySelector(".freight-mobile-lead-slot");
const slot = track.querySelector(".freight-strip-slot");
const letters = track.querySelector(".freight-letters");
const motion = matchMedia("(prefers-reduced-motion: no-preference)");
const desktop = matchMedia("(min-width: 801px) and (min-height: 470px)");

const frameRate = 24;
const frameDuration = 1 / frameRate;
const frameRatio = 16 / 9;
const groundEnd = 0.448;
// Normalized floor height from frames 72–90 of the selected 24fps clip.
const risingFloor = [
  0.829, 0.821, 0.816, 0.805, 0.786, 0.771, 0.749, 0.729, 0.705, 0.675, 0.651,
  0.625, 0.563, 0.528, 0.506, 0.483, 0.463, 0.45, 0.448,
];
const clamp = (value) => Math.max(0, Math.min(1, value));
const interpolate = (from, to, progress) => from + (to - from) * progress;

let animationFrame = 0;
let mediaReady = false;
let mediaFailed = false;
let currentTime = 0;
let usingWideSource = false;
let geometry;

function measure() {
  const height = scene.clientHeight;
  const width = scene.clientWidth;
  const wide = desktop.matches;
  const frameWidth = wide ? Math.max(width, height * frameRatio) : width * 1.55;
  const frameHeight = frameWidth / frameRatio;
  const videoDistance = height * (wide ? 2.4 : 1.65);
  const padding = Math.max(24, width * 0.04);
  const stripWidth = strip.offsetWidth;
  const stripTravel = wide ? Math.max(0, stripWidth - width + padding) : 0;
  const stripDistance = wide
    ? ((stripWidth + padding) / width) * height * 1.1
    : 0;
  geometry = {
    height,
    width,
    wide,
    frameHeight,
    videoDistance,
    padding,
    stripTravel,
    stripDistance,
    lettersWidth: letters.offsetWidth,
  };
  track.style.height = `${height + videoDistance + stripDistance}px`;
  scene.style.setProperty("--freight-frame-width", `${frameWidth}px`);
  scene.style.setProperty("--freight-frame-height", `${frameHeight}px`);
  scene.style.setProperty(
    "--freight-frame-x",
    wide ? "50%" : `${width / 2 + frameWidth * 0.1}px`,
  );
}

function schedule() {
  if (motion.matches && !animationFrame)
    animationFrame = requestAnimationFrame(render);
}

function render() {
  animationFrame = 0;
  if (!geometry || document.hidden || mediaFailed) return;
  const { top, bottom } = track.getBoundingClientRect();
  const {
    height,
    width,
    wide,
    frameHeight,
    videoDistance,
    stripTravel,
    stripDistance,
    lettersWidth,
  } = geometry;
  if (top > window.innerHeight || bottom <= 0) return;

  const offset = Math.max(0, -top);
  const progress = clamp(offset / videoDistance);
  const length = mediaReady
    ? Math.max(0, video.duration - frameDuration / 2)
    : 0;
  const target = progress * length;
  const difference = target - currentTime;
  currentTime =
    Math.abs(difference) < 0.014 ? target : currentTime + difference * 0.27;

  if (
    mediaReady &&
    !video.seeking &&
    Math.abs(video.currentTime - currentTime) > 1 / 60
  ) {
    video.currentTime = currentTime;
  }

  // Keep the floor aligned to the clip's rising ground, even in portrait crop.
  const time = mediaReady ? video.currentTime : 0;
  const ground =
    risingFloor[
      Math.min(
        risingFloor.length - 1,
        Math.max(0, Math.floor(time * frameRate + 0.001) - 72),
      )
    ];
  const frameCenter = wide
    ? height / 2
    : height * 0.36 + frameHeight * (0.5 - groundEnd);
  const edge = frameCenter + (ground - 0.5) * frameHeight;
  scene.style.setProperty("--freight-frame-y", `${frameCenter}px`);
  scene.style.setProperty("--freight-edge", `${edge}px`);

  if (wide) {
    const serviceProgress = clamp((offset - videoDistance) / stripDistance);
    const entrance = clamp(serviceProgress * 3.2);
    const shift = clamp((serviceProgress - 0.4) / 0.54);
    const stripX =
      interpolate(width, geometry.padding, entrance) - stripTravel * shift;
    scene.style.setProperty("--freight-strip-x", `${stripX}px`);
    const letterProgress = clamp(
      (offset - videoDistance * 0.87) / (stripDistance + videoDistance * 0.13),
    );
    scene.style.setProperty(
      "--freight-letters-x",
      `${interpolate(width, -lettersWidth, letterProgress)}px`,
    );
  }

  if (Math.abs(difference) >= 0.014) schedule();
}

function showStatic() {
  if (animationFrame) cancelAnimationFrame(animationFrame);
  animationFrame = 0;
  geometry = undefined;
  currentTime = 0;
  track.classList.remove("is-scrubbed", "has-video");
  track.style.removeProperty("height");
  scene.removeAttribute("style");
  strip.prepend(lead);
  track.after(services);
}

function configure() {
  if (!motion.matches || mediaFailed) {
    showStatic();
    return;
  }
  track.classList.add("is-scrubbed");
  strip.prepend(lead);
  if (desktop.matches) slot.append(services);
  else if (services.parentElement !== track.parentElement)
    track.after(services);
  if (mediaReady) track.classList.add("has-video");
  measure();
  // Reuse the same heading in the portrait scene; shorter screens keep it in normal flow.
  const initialBandHeight =
    geometry.height * 0.64 -
    (risingFloor[0] - groundEnd) * geometry.frameHeight;
  if (!geometry.wide && initialBandHeight >= 300) mobileLeadSlot.append(lead);
  schedule();
  if (!video.getAttribute("src")) {
    video.preload = "auto";
    usingWideSource = desktop.matches && !navigator.connection?.saveData;
    video.src = usingWideSource ? video.dataset.srcWide : video.dataset.src;
  }
}

video.addEventListener("loadeddata", () => {
  if (!Number.isFinite(video.duration) || video.duration <= frameDuration)
    return;
  mediaReady = true;
  if (motion.matches) track.classList.add("has-video");
  schedule();
});
video.addEventListener("seeked", schedule);
video.addEventListener("error", () => {
  mediaReady = false;
  track.classList.remove("has-video");
  if (usingWideSource) {
    usingWideSource = false;
    video.src = video.dataset.src;
    return;
  }
  mediaFailed = true;
  showStatic();
});
const servicesLink = document.querySelector(
  '.freight-nav a[href="#freight-services"]',
);
servicesLink?.addEventListener("click", (event) => {
  if (!geometry?.wide || !track.classList.contains("is-scrubbed")) return;
  event.preventDefault();
  const top = track.getBoundingClientRect().top + window.scrollY;
  window.scrollTo({
    top: top + geometry.videoDistance + geometry.stripDistance * 0.315,
    behavior: "smooth",
  });
});
window.addEventListener("scroll", schedule, { passive: true });
window.addEventListener("resize", configure, { passive: true });
document.addEventListener("visibilitychange", schedule);
motion.addEventListener("change", configure);
desktop.addEventListener("change", configure);
document.fonts?.ready.then(configure);
configure();
