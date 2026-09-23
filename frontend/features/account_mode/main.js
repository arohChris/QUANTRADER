// Breaker panel bootstrap — thin HTML only; no business rules beyond M1.3 status render.
import { fetchStatus, postAccount, MODE_URL, AUTO_URL } from "../../services/account_api.js";
import { wireGuard } from "./guards.js";

const el = (id) => document.getElementById(id);
const statusEl = el("status");
const roleSel = el("role");
const guardLive = el("guard-live");
const guardAuto = el("guard-auto");

function setStatus(msg, cls) {
  statusEl.textContent = msg;
  statusEl.className = cls || "";
}

function lawText(body) {
  const mode = body.mode || "demo";
  const auto = body.auto || "OFF";
  const live =
    mode === "live"
      ? '<span class="live">LIVE</span>'
      : '<span class="demo">DEMO</span>';
  const a =
    auto === "ON"
      ? '<span class="on">AUTO ON</span>'
      : '<span class="off">AUTO OFF</span>';
  if (mode === "live" && auto === "ON") {
    return live + " · " + a + " — <span class=\"live\">real orders</span>";
  }
  if (mode === "live" && auto !== "ON") {
    return (
      live +
      " · " +
      a +
      ' — <span style="color:var(--amber)">armed but paused</span>'
    );
  }
  return live + " · " + a + " — quiet default";
}

function render(body) {
  const mode = body.mode || "demo";
  const auto = body.auto || "OFF";
  const armed = !!body.live_armed;
  const will = !!body.bot_will_trade;

  el("chip-mode").className = "chip " + (mode === "live" ? "live" : "demo");
  el("chip-mode-text").textContent = "MODE " + mode.toUpperCase();
  el("chip-auto").className = "chip " + (auto === "ON" ? "auto-on" : "auto-off");
  el("chip-auto-text").textContent = "AUTO " + auto;
  const willChip = el("chip-will");
  if (will) {
    willChip.className = "chip will will-yes";
    el("chip-will-text").textContent = "WILL_TRADE YES";
  } else if (mode === "live") {
    willChip.className = "chip will armed-pause";
    el("chip-will-text").textContent = "WILL_TRADE NO · AUTO OFF";
  } else {
    willChip.className = "chip will";
    el("chip-will-text").textContent = "WILL_TRADE NO";
  }

  el("law-line").innerHTML = lawText(body);

  const modeState = el("mode-state");
  modeState.textContent = mode.toUpperCase();
  modeState.className = "state " + (mode === "live" ? "live" : "demo");
  el("btn-demo").classList.toggle("active-demo", mode === "demo");
  el("btn-live").classList.toggle("active-live", mode === "live");
  el("btn-demo").disabled = mode === "demo";
  el("btn-live").disabled = mode === "live";

  const autoState = el("auto-state");
  autoState.textContent = auto;
  autoState.className = "state " + (auto === "ON" ? "on" : "off");
  el("btn-auto-off").classList.toggle("active-off", auto === "OFF");
  el("btn-auto-on").classList.toggle("active-on", auto === "ON");
  el("btn-auto-off").disabled = auto === "OFF";
  el("btn-auto-on").disabled = auto === "ON";

  el("meta").innerHTML =
    "<span>mode_source=" +
    (body.source || "—") +
    "</span>" +
    "<span>auto_source=" +
    (body.auto_source || "—") +
    "</span>" +
    "<span>live_armed=" +
    armed +
    "</span>" +
    "<span>bot_will_trade=" +
    will +
    "</span>";

  window.__gate = body;
  return body;
}

async function refresh() {
  const res = await fetchStatus();
  if (!res.ok) {
    setStatus(res.detail, "err");
    return;
  }
  render(res.body);
  const body = res.body;
  setStatus(
    "mode=" +
      body.mode +
      " · auto=" +
      body.auto +
      " · live_armed=" +
      body.live_armed +
      " · will_trade=" +
      body.bot_will_trade,
    ""
  );
}

async function postJson(url, payload) {
  setStatus("POST…", "");
  const res = await postAccount(url, payload, roleSel.value);
  if (!res.ok) {
    setStatus(
      (res.status ? "HTTP " + res.status + " — " : "") + res.detail,
      "err"
    );
    await refresh();
    return res;
  }
  render(res.body);
  const body = res.body;
  setStatus(
    "OK → mode=" +
      body.mode +
      " auto=" +
      body.auto +
      " (" +
      (body.source || "") +
      "/" +
      (body.auto_source || "") +
      ")",
    "ok"
  );
  return res;
}

el("btn-demo").addEventListener("click", async () => {
  await postJson(MODE_URL, { mode: "demo" });
});

el("btn-live").addEventListener("click", () => {
  guardLive.showModal();
});

el("btn-auto-off").addEventListener("click", async () => {
  await postJson(AUTO_URL, { auto: "OFF" });
});

el("btn-auto-on").addEventListener("click", () => {
  guardAuto.showModal();
});

wireGuard(
  guardLive,
  "input.ck-live",
  el("live-confirm"),
  el("live-cancel"),
  () => postJson(MODE_URL, { mode: "live" })
);
wireGuard(
  guardAuto,
  "input.ck-auto",
  el("auto-confirm"),
  el("auto-cancel"),
  () => postJson(AUTO_URL, { auto: "ON" })
);

roleSel.addEventListener("change", refresh);

document.addEventListener("keydown", (e) => {
  if (e.target && (e.target.tagName === "SELECT" || e.target.tagName === "INPUT"))
    return;
  if (e.key === "1") {
    e.preventDefault();
    el("btn-demo").focus();
  }
  if (e.key === "a" || e.key === "A") {
    e.preventDefault();
    el("btn-auto-on").focus();
  }
});

function tickClock() {
  const now = new Date();
  el("clock").textContent = now.toISOString().slice(11, 19);
}
tickClock();
setInterval(tickClock, 1000);
setInterval(refresh, 5000);

refresh();
