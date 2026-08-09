# HTTP API

The API is intentionally small. All bodies and responses use UTF-8 JSON. Unknown fields are
ignored in v0.1.0; clients must not depend on that behavior for future versions.

## Authentication

Room endpoints require the room access key:

```http
Authorization: Bearer ROOM_ACCESS_KEY
```

If the server was started with `--create-token` or `DUTYBELL_CREATE_TOKEN`, room creation also
requires:

```http
X-DutyBell-Create-Token: SERVER_CREATE_TOKEN
```

The two secrets have different scopes. A create token cannot read a room, and a room key cannot
create another room.

## Endpoints

### `GET /healthz`

Returns process liveness and version. It does not access a room or reveal database contents.

### `GET /api/meta`

Returns the version and whether a room-creation token is required.

### `POST /api/rooms`

Creates a room. Request:

```json
{
  "name": "Dog break",
  "interval_seconds": 7200,
  "participants": ["Alex", "Sam"],
  "repeat_on_ack": true,
  "rotate_on_ack": true,
  "start": true,
  "actor": "Alex"
}
```

`interval_seconds` must be from 1 through 604800. There may be at most 16 unique participant names.
Names are whitespace-normalized and length-limited. A successful `201` response contains `room`
and the only plaintext copy of `access_key`.

### `GET /api/rooms/{room_id}`

Returns the current public room snapshot, including server time, calculated remaining milliseconds,
due state, and monotonically increasing version.

### `GET /api/rooms/{room_id}/events`

Returns the append-only event history in ascending order.

### `GET /api/rooms/{room_id}/wait?after={version}&timeout={seconds}`

Waits up to 30 seconds. Returns `changed` and the latest room whether the wait changed or timed out.
Use the returned room version for the next request.

### `POST /api/rooms/{room_id}/actions`

Request envelope:

```json
{
  "action": "ack",
  "actor": "Sam",
  "expected_version": 4,
  "data": {}
}
```

| Action | Valid source | Optional `data` | Effect |
| --- | --- | --- | --- |
| `start` | any | `interval_seconds` | Starts a fresh interval |
| `pause` | running | none | Stores remaining time |
| `resume` | paused | none | Rebuilds deadline from stored remainder |
| `reset` | any | none | Starts the configured full interval |
| `ack` | running or paused | none | Records handling; optionally repeats and rotates |
| `stop` | any | none | Returns to idle |
| `claim` | any | none | Assigns the actor, constrained to participants if configured |
| `configure` | any | room fields | Updates name, interval, participants, repeat, and rotation |

## Errors

Errors have stable machine and human fields:

```json
{
  "error": "conflict",
  "message": "expected room version 3, current version is 4",
  "room": { "version": 4 }
}
```

| Status | Meaning | Repair |
| --- | --- | --- |
| `400` | Invalid JSON, query, or field | Fix request shape/value |
| `401` | Missing or wrong secret | Use the private room link or correct create token |
| `404` | Unknown room, endpoint, or static asset | Check room code/path |
| `409` | Stale `expected_version` | Render returned room, then retry intentional action |
| `413` | Body larger than 64 KiB | Send only supported fields |
| `415` | POST body is not JSON | Set `Content-Type: application/json` |
| `422` | Illegal state transition | Refresh and choose an action valid for current state |
| `500` | Unexpected server fault | Preserve logs and run `dutybell doctor` |

Cross-origin requests are not supported. Put integrations behind the same origin or write a small
trusted server-side client; do not disable the boundary by reflecting arbitrary origins.
