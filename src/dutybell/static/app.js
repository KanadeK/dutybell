import {
  buildJoinUrl,
  estimateClockOffset,
  formatDuration,
  normalizeParticipants,
  parseJoinFragment,
  roomRemainingMs,
  stateLabel,
} from "/core.mjs";

const $ = (selector) => document.querySelector(selector);
const elements = {
  welcomeView: $("#welcomeView"), roomView: $("#roomView"), createForm: $("#createForm"),
  joinForm: $("#joinForm"), leaveButton: $("#leaveButton"), copyButton: $("#copyButton"),
  notifyButton: $("#notifyButton"), installButton: $("#installButton"), roomName: $("#roomName"),
  roomCode: $("#roomCode"), statePill: $("#statePill"), assigneeLabel: $("#assigneeLabel"),
  timerDigits: $("#timerDigits"), timerSentence: $("#timerSentence"), timerPanel: $("#timerPanel"),
  participantList: $("#participantList"), eventList: $("#eventList"), startButton: $("#startButton"),
  pauseButton: $("#pauseButton"), resetButton: $("#resetButton"), ackButton: $("#ackButton"),
  muteButton: $("#muteButton"), refreshHistoryButton: $("#refreshHistoryButton"),
  connectionDot: $("#connectionDot"), connectionText: $("#connectionText"), syncText: $("#syncText"),
  toast: $("#toast"),
};

const state = {
  room: null,
  key: "",
  actor: localStorage.getItem("dutybell.actor") || "",
  clockOffsetMs: 0,
  connected: false,
  muted: false,
  alerting: false,
  waitController: null,
  installPrompt: null,
};

let audioContext = null;
let beepTimer = null;
let toastTimer = null;

function showToast(message) {
  elements.toast.textContent = message;
  elements.toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { elements.toast.hidden = true; }, 3800);
}

function setConnection(kind, text) {
  state.connected = kind === "online";
  elements.connectionDot.className = `connection-dot ${kind}`;
  elements.connectionText.textContent = text;
}

async function api(path, options = {}) {
  const started = Date.now();
  const headers = new Headers(options.headers || {});
  if (state.key) headers.set("Authorization", `Bearer ${state.key}`);
  if (options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const response = await fetch(path, { ...options, headers });
  const finished = Date.now();
  const payload = await response.json().catch(() => ({ error: "invalid_response", message: "Server returned invalid JSON" }));
  const sampledRoom = payload.room;
  if (sampledRoom && Number.isFinite(sampledRoom.server_now_ms)) {
    state.clockOffsetMs = estimateClockOffset(started, finished, sampledRoom.server_now_ms);
    elements.syncText.textContent = `Clock aligned within a ${finished - started} ms round trip.`;
  }
  if (!response.ok) {
    if (response.status === 409 && payload.room) updateRoom(payload.room);
    const error = new Error(payload.message || `Request failed (${response.status})`);
    error.status = response.status;
    throw error;
  }
  return payload;
}

function currentJoinUrl() {
  return buildJoinUrl(window.location.href.split("#")[0], state.room.room_id, state.key);
}

function updateRoom(room) {
  const previousVersion = state.room?.version;
  state.room = room;
  setConnection("online", "Synced");
  render();
  if (previousVersion && previousVersion !== room.version) void loadHistory();
}

function render() {
  const room = state.room;
  if (!room) return;
  const remaining = roomRemainingMs(room, Date.now(), state.clockOffsetMs);
  const due = room.status === "running" && remaining === 0;
  elements.roomName.textContent = room.name;
  elements.roomCode.textContent = room.room_id;
  elements.timerDigits.textContent = formatDuration(remaining);
  elements.statePill.textContent = stateLabel(room, remaining);
  elements.assigneeLabel.textContent = room.assignee ? `${room.assignee} is on duty` : "No one assigned";
  elements.timerPanel.classList.toggle("due", due);
  elements.startButton.disabled = room.status === "running";
  elements.pauseButton.disabled = room.status !== "running";
  elements.pauseButton.textContent = room.status === "paused" ? "Resume" : "Pause";
  elements.resetButton.disabled = room.status === "idle";
  elements.ackButton.disabled = !["running", "paused"].includes(room.status);
  elements.muteButton.hidden = !state.alerting;
  elements.timerSentence.textContent = due
    ? "This bell is due. Acknowledge once to update every connected device."
    : room.status === "running"
      ? `Next interval: ${room.interval_seconds} seconds${room.repeat_on_ack ? " · repeats after acknowledgement" : ""}.`
      : room.status === "paused"
        ? "The shared timer is paused for everyone."
        : room.status === "acknowledged"
          ? "Handled. Start again when the household is ready."
          : "Ready to start across every connected screen.";
  renderParticipants();
  if (due && !state.muted) startAlert();
  if (!due) stopAlert();
}

function renderParticipants() {
  const room = state.room;
  elements.participantList.replaceChildren();
  if (!room.participants.length) {
    const empty = document.createElement("p");
    empty.textContent = "No rotation list. Anyone with the link can claim duty.";
    empty.className = "boundary";
    elements.participantList.append(empty);
    return;
  }
  for (const name of room.participants) {
    const row = document.createElement("div");
    row.className = `person${name === room.assignee ? " on-duty" : ""}`;
    const label = document.createElement("span");
    label.textContent = name === room.assignee ? `${name} · on duty` : name;
    row.append(label);
    if (name.toLocaleLowerCase() === state.actor.toLocaleLowerCase() && name !== room.assignee) {
      const claim = document.createElement("button");
      claim.type = "button";
      claim.className = "quiet";
      claim.textContent = "Take duty";
      claim.addEventListener("click", () => void sendAction("claim"));
      row.append(claim);
    }
    elements.participantList.append(row);
  }
}

async function sendAction(action, data = {}) {
  if (!state.room) return;
  try {
    const payload = await api(`/api/rooms/${state.room.room_id}/actions`, {
      method: "POST",
      body: JSON.stringify({ action, actor: state.actor, expected_version: state.room.version, data }),
    });
    state.muted = false;
    updateRoom(payload.room);
  } catch (error) {
    showToast(error.status === 409 ? "Someone else changed the timer. State refreshed; try again." : error.message);
  }
}

async function connect(roomId, key, actor) {
  const cleanActor = actor.trim();
  if (!cleanActor) {
    showToast("Your name is required before joining a bell.");
    return;
  }
  state.key = key;
  state.actor = cleanActor;
  localStorage.setItem("dutybell.actor", state.actor);
  try {
    const payload = await api(`/api/rooms/${roomId}`);
    updateRoom(payload.room);
    window.location.hash = new URLSearchParams({ room: payload.room.room_id, key }).toString();
    elements.welcomeView.hidden = true;
    elements.roomView.hidden = false;
    await loadHistory();
    void waitLoop();
  } catch (error) {
    state.key = "";
    setConnection("offline", "Not connected");
    showToast(error.message);
  }
}

function openPrivateLink(parsed) {
  if (state.actor) {
    void connect(parsed.roomId, parsed.key, state.actor);
    return;
  }
  elements.joinForm.elements.namedItem("roomId").value = parsed.roomId;
  elements.joinForm.elements.namedItem("key").value = parsed.key;
  elements.joinForm.elements.namedItem("actor").focus();
  elements.joinForm.scrollIntoView({ behavior: "smooth", block: "center" });
  showToast("Private link ready. Add your name to join with clear event attribution.");
}

async function waitLoop() {
  state.waitController?.abort();
  const controller = new AbortController();
  state.waitController = controller;
  while (!controller.signal.aborted && state.room) {
    try {
      const version = state.room.version;
      const payload = await api(`/api/rooms/${state.room.room_id}/wait?after=${version}&timeout=25`, { signal: controller.signal });
      if (payload.room) updateRoom(payload.room);
    } catch (error) {
      if (error.name === "AbortError") return;
      setConnection("offline", "Reconnecting");
      elements.syncText.textContent = "The timer still runs locally while DutyBell reconnects.";
      await new Promise((resolve) => setTimeout(resolve, 1500));
    }
  }
}

async function loadHistory() {
  if (!state.room) return;
  try {
    const payload = await api(`/api/rooms/${state.room.room_id}/events`);
    elements.eventList.replaceChildren();
    for (const event of [...payload.events].reverse()) {
      const item = document.createElement("li");
      item.className = "event-item";
      const version = document.createElement("span");
      version.className = "event-version";
      version.textContent = event.version;
      const content = document.createElement("div");
      const title = document.createElement("strong");
      title.textContent = event.action.replaceAll("_", " ");
      const actor = document.createElement("p");
      actor.textContent = `by ${event.actor}`;
      content.append(title, actor);
      const time = document.createElement("time");
      time.dateTime = event.created_at;
      time.textContent = new Date(event.created_at).toLocaleString();
      item.append(version, content, time);
      elements.eventList.append(item);
    }
  } catch (error) {
    showToast(`History unavailable: ${error.message}`);
  }
}

function beep() {
  try {
    audioContext ||= new AudioContext();
    const oscillator = audioContext.createOscillator();
    const gain = audioContext.createGain();
    oscillator.frequency.value = 660;
    gain.gain.setValueAtTime(0.0001, audioContext.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.16, audioContext.currentTime + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, audioContext.currentTime + 0.32);
    oscillator.connect(gain).connect(audioContext.destination);
    oscillator.start();
    oscillator.stop(audioContext.currentTime + 0.34);
  } catch { /* Browsers may block audio until a user gesture. */ }
}

function startAlert() {
  if (state.alerting) return;
  state.alerting = true;
  beep();
  beepTimer = setInterval(beep, 1800);
  if ("Notification" in window && Notification.permission === "granted") {
    new Notification(`${state.room.name} needs acknowledgement`, { body: `${state.room.assignee || "Someone"} is on duty. Open DutyBell to handle it.`, icon: "/icon.svg", tag: `dutybell-${state.room.room_id}` });
  }
  render();
}

function stopAlert() {
  if (!state.alerting) return;
  state.alerting = false;
  clearInterval(beepTimer);
  beepTimer = null;
  elements.muteButton.hidden = true;
}

elements.createForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(elements.createForm);
  const minutes = Number(form.get("minutes"));
  const intervalSeconds = Math.max(1, Math.round(minutes * 60));
  const actor = String(form.get("actor") || "owner").trim();
  const headers = {};
  const createToken = String(form.get("createToken") || "");
  if (createToken) headers["X-DutyBell-Create-Token"] = createToken;
  try {
    const payload = await api("/api/rooms", {
      method: "POST",
      headers,
      body: JSON.stringify({
        name: form.get("name"), interval_seconds: intervalSeconds,
        participants: normalizeParticipants(String(form.get("participants") || "")),
        repeat_on_ack: form.get("repeat") === "on", rotate_on_ack: form.get("rotate") === "on",
        start: form.get("start") === "on", actor,
      }),
    });
    await connect(payload.room.room_id, payload.access_key, actor);
  } catch (error) { showToast(error.message); }
});

elements.joinForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const form = new FormData(elements.joinForm);
  void connect(String(form.get("roomId")), String(form.get("key")), String(form.get("actor")));
});

elements.startButton.addEventListener("click", () => void sendAction("start"));
elements.pauseButton.addEventListener("click", () => void sendAction(state.room.status === "paused" ? "resume" : "pause"));
elements.resetButton.addEventListener("click", () => void sendAction("reset"));
elements.ackButton.addEventListener("click", () => void sendAction("ack"));
elements.muteButton.addEventListener("click", () => { state.muted = true; stopAlert(); showToast("Muted on this device only. The shared bell is still due."); });
elements.refreshHistoryButton.addEventListener("click", () => void loadHistory());
elements.copyButton.addEventListener("click", async () => {
  try { await navigator.clipboard.writeText(currentJoinUrl()); showToast("Private join link copied. It contains the shared room key."); }
  catch { showToast("Could not access the clipboard. Copy the URL from the address bar."); }
});
elements.notifyButton.addEventListener("click", async () => {
  if (!("Notification" in window)) return showToast("This browser does not support notifications.");
  const result = await Notification.requestPermission();
  elements.notifyButton.textContent = result === "granted" ? "Browser notifications enabled" : "Notifications not enabled";
  beep();
});
elements.leaveButton.addEventListener("click", () => {
  state.waitController?.abort(); stopAlert(); state.room = null; state.key = ""; window.location.hash = "";
  elements.roomView.hidden = true; elements.welcomeView.hidden = false;
});

window.addEventListener("beforeinstallprompt", (event) => { event.preventDefault(); state.installPrompt = event; elements.installButton.hidden = false; });
elements.installButton.addEventListener("click", async () => { if (!state.installPrompt) return; await state.installPrompt.prompt(); state.installPrompt = null; elements.installButton.hidden = true; });
window.addEventListener("hashchange", () => {
  const parsed = parseJoinFragment(window.location.hash);
  if (parsed && (!state.room || parsed.roomId !== state.room.room_id)) openPrivateLink(parsed);
});

setInterval(() => { if (state.room) render(); }, 250);
if ("serviceWorker" in navigator) void navigator.serviceWorker.register("/sw.js");

const initial = parseJoinFragment(window.location.hash);
if (initial) openPrivateLink(initial);
