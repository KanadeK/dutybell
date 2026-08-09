export function parseJoinFragment(fragment) {
  const raw = fragment.startsWith("#") ? fragment.slice(1) : fragment;
  const params = new URLSearchParams(raw);
  const roomId = (params.get("room") || "").trim().toUpperCase();
  const key = (params.get("key") || "").trim();
  if (!/^[A-Z2-7]{8}$/.test(roomId) || key.length < 20 || key.length > 256) {
    return null;
  }
  return { roomId, key };
}

export function buildJoinUrl(baseUrl, roomId, key) {
  const url = new URL(baseUrl);
  url.hash = new URLSearchParams({ room: roomId, key }).toString();
  return url.toString();
}

export function estimateClockOffset(startedAtMs, finishedAtMs, serverNowMs) {
  if (![startedAtMs, finishedAtMs, serverNowMs].every(Number.isFinite)) {
    return 0;
  }
  const midpoint = startedAtMs + (finishedAtMs - startedAtMs) / 2;
  return serverNowMs - midpoint;
}

export function roomRemainingMs(room, clientNowMs, clockOffsetMs = 0) {
  if (!room) return null;
  if (room.status === "paused") return Math.max(0, room.paused_remaining_ms || 0);
  if (room.status !== "running" || room.deadline_at_ms == null) return null;
  return Math.max(0, room.deadline_at_ms - (clientNowMs + clockOffsetMs));
}

export function formatDuration(milliseconds) {
  if (milliseconds == null || !Number.isFinite(milliseconds)) return "--:--";
  const totalSeconds = Math.max(0, Math.ceil(milliseconds / 1000));
  const days = Math.floor(totalSeconds / 86400);
  const hours = Math.floor((totalSeconds % 86400) / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  const pairs = [hours, minutes, seconds].map((value) => String(value).padStart(2, "0"));
  return days > 0 ? `${days}d ${pairs.join(":")}` : pairs.join(":");
}

export function stateLabel(room, remaining) {
  if (!room) return "Not connected";
  if (room.status === "running" && remaining === 0) return "Needs acknowledgement";
  const labels = {
    idle: "Ready",
    running: "Counting down",
    paused: "Paused",
    acknowledged: "Acknowledged",
  };
  return labels[room.status] || room.status;
}

export function normalizeParticipants(text) {
  const seen = new Set();
  return text
    .split(/[\n,]/)
    .map((value) => value.trim().replace(/\s+/g, " "))
    .filter((value) => {
      const key = value.toLocaleLowerCase();
      if (!value || seen.has(key)) return false;
      seen.add(key);
      return true;
    });
}
