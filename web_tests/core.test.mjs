import assert from "node:assert/strict";
import test from "node:test";

import {
  buildJoinUrl,
  estimateClockOffset,
  formatDuration,
  normalizeParticipants,
  parseJoinFragment,
  roomRemainingMs,
  stateLabel,
} from "../src/dutybell/static/core.mjs";

test("join fragments require a base32 room and credible secret", () => {
  const valid = parseJoinFragment("#room=abc234de&key=abcdefghijklmnopqrstuvwxyz");
  assert.deepEqual(valid, { roomId: "ABC234DE", key: "abcdefghijklmnopqrstuvwxyz" });
  assert.equal(parseJoinFragment("#room=bad&key=abcdefghijklmnopqrstuvwxyz"), null);
  assert.equal(parseJoinFragment("#room=ABC234DE&key=short"), null);
});

test("join links keep the secret in the URL fragment", () => {
  const url = new URL(buildJoinUrl("https://bell.example/", "ABC234DE", "secret-key-value-123456789"));
  assert.equal(url.search, "");
  assert.match(url.hash, /room=ABC234DE/);
  assert.match(url.hash, /key=secret-key-value-123456789/);
});

test("clock offset uses the request midpoint", () => {
  assert.equal(estimateClockOffset(1000, 1200, 1300), 200);
  assert.equal(estimateClockOffset(Number.NaN, 1200, 1300), 0);
});

test("remaining time follows the server anchored deadline", () => {
  assert.equal(roomRemainingMs({ status: "running", deadline_at_ms: 5000 }, 4000, 250), 750);
  assert.equal(roomRemainingMs({ status: "running", deadline_at_ms: 5000 }, 6000, 0), 0);
  assert.equal(roomRemainingMs({ status: "paused", paused_remaining_ms: 333 }, 9000, 0), 333);
  assert.equal(roomRemainingMs({ status: "idle" }, 0, 0), null);
});

test("duration and state labels are stable at boundaries", () => {
  assert.equal(formatDuration(0), "00:00:00");
  assert.equal(formatDuration(61_000), "00:01:01");
  assert.equal(formatDuration(90_061_000), "1d 01:01:01");
  assert.equal(stateLabel({ status: "running" }, 0), "Needs acknowledgement");
  assert.equal(stateLabel({ status: "paused" }, 100), "Paused");
});

test("participant normalization is ordered and case insensitive", () => {
  assert.deepEqual(normalizeParticipants(" Alex,Sam\nalex\n Morgan "), ["Alex", "Sam", "Morgan"]);
});
