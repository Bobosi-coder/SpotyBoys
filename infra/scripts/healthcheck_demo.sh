#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:5173}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

"${PYTHON_BIN}" - "$BASE_URL" <<'PY'
import json
import http.cookiejar
import sys
import uuid
import urllib.error
import urllib.request

base = sys.argv[1].rstrip("/")
jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

checks = [
    ("proxy health", f"{base}/health"),
    ("recommendation health", f"{base}/recommendation-health"),
    ("recommendation ready", f"{base}/recommendation-ready"),
    ("event health", f"{base}/event-health"),
]

for label, url in checks:
    try:
        with opener.open(url, timeout=5) as response:
            status = response.status
            body = response.read(80)
    except Exception as exc:
        print(f"FAIL {label}: {exc}")
        raise SystemExit(1)
    if status >= 400:
        print(f"FAIL {label}: HTTP {status}")
        raise SystemExit(1)
    print(f"OK {label}: HTTP {status}")

signup_payload = {
    "email": f"healthcheck-{uuid.uuid4().hex}@spotiboys.local",
    "password": "spotiboys-demo-password",
    "display_name": "Healthcheck Listener",
}
request = urllib.request.Request(
    f"{base}/auth/signup",
    data=json.dumps(signup_payload).encode("utf-8"),
    headers={"Content-Type": "application/json"},
    method="POST",
)
with opener.open(request, timeout=5) as response:
    auth = json.loads(response.read().decode("utf-8"))
assert auth["auth_state"] == "authenticated"
print("OK auth signup/session cookie")

for label, url in [
    ("playable track", f"{base}/playable-track/trk_001"),
    ("stream mapped", f"{base}/stream/trk_001"),
]:
    with opener.open(url, timeout=5) as response:
        status = response.status
    if status >= 400:
        print(f"FAIL {label}: HTTP {status}")
        raise SystemExit(1)
    print(f"OK {label}: HTTP {status}")

with opener.open(f"{base}/stream/trk_001", timeout=5) as response:
    content_type = response.headers.get("content-type", "")
    prefix = response.read(8)
if "audio/mpeg" not in content_type and not prefix.startswith(b"ID3"):
    print(f"FAIL stream mapped is not real MP3/Navidrome fixture audio: {content_type} {prefix!r}")
    raise SystemExit(1)
print("OK stream mapped real fixture audio")

try:
    opener.open(f"{base}/stream/trk_missing", timeout=5)
except urllib.error.HTTPError as exc:
    if exc.code == 404:
        print("OK stream unmapped fail-closed: HTTP 404")
    else:
        print(f"FAIL stream unmapped: HTTP {exc.code}")
        raise SystemExit(1)
else:
    print("FAIL stream unmapped should fail closed")
    raise SystemExit(1)

with opener.open(f"{base}/session/bootstrap", timeout=5) as response:
    payload = json.loads(response.read().decode("utf-8"))
assert len(payload["browse_surface"]["featured_items"]) <= 4
assert len(payload["browse_surface"]["random_carousel_items"]) <= 10
assert payload["queue"]["drawer_default_open"] is False
print("OK contract caps/defaults")

recommendation_request = {
    "session_id": payload["session_id"],
    "user_id": payload["user_id"],
}
request = urllib.request.Request(
    f"{base}/recommendations/next",
    data=json.dumps(recommendation_request).encode("utf-8"),
    headers={"Content-Type": "application/json"},
    method="POST",
)
with opener.open(request, timeout=5) as response:
    rec = json.loads(response.read().decode("utf-8"))
assert len(rec["browse_surface"]["featured_items"]) <= 4
assert len(rec["browse_surface"]["random_carousel_items"]) <= 10
print("OK recommendations next")

visible_items = [
    {"track_id": item["track_id"], "surface_slot": item["surface_slot"]}
    for item in rec["browse_surface"]["featured_items"]
]
impression_payload = {
    "impression_id": rec["impression_id"],
    "request_id": rec["request_id"],
    "session_id": payload["session_id"],
    "user_id": payload["user_id"],
    "visible_items": visible_items,
    "surface": "browse_surface",
    "rendered_at": "2026-04-18T12:00:00Z",
}
request = urllib.request.Request(
    f"{base}/events/impression",
    data=json.dumps(impression_payload).encode("utf-8"),
    headers={"Content-Type": "application/json"},
    method="POST",
)
with opener.open(request, timeout=5) as response:
    first = json.loads(response.read().decode("utf-8"))
with opener.open(request, timeout=5) as response:
    duplicate = json.loads(response.read().decode("utf-8"))
assert first["duplicate"] is False
assert duplicate["duplicate"] is True
print("OK impression idempotency")

track = rec["queue"]["items"][0]
event_suffix = uuid.uuid4().hex
playback_payload = {
    "event_id": f"evt_healthcheck_playback_start_{event_suffix}",
    "event_type": "playback_start",
    "session_id": payload["session_id"],
    "user_id": payload["user_id"],
    "track_id": track["track_id"],
    "request_id": track["request_id"],
    "impression_id": track["impression_id"],
    "position_ms": 0,
    "playback_ms": 0,
    "occurred_at": "2026-04-18T12:00:01Z",
    "client_event_seq": 1,
}
request = urllib.request.Request(
    f"{base}/events/playback",
    data=json.dumps(playback_payload).encode("utf-8"),
    headers={"Content-Type": "application/json"},
    method="POST",
)
with opener.open(request, timeout=5) as response:
    first = json.loads(response.read().decode("utf-8"))
with opener.open(request, timeout=5) as response:
    duplicate = json.loads(response.read().decode("utf-8"))
assert first["duplicate"] is False
assert duplicate["duplicate"] is True
print("OK playback idempotency")

feedback_payload = {
    "event_id": f"evt_healthcheck_feedback_{event_suffix}",
    "feedback_type": "like",
    "session_id": payload["session_id"],
    "user_id": payload["user_id"],
    "track_id": track["track_id"],
    "request_id": track["request_id"],
    "impression_id": track["impression_id"],
    "occurred_at": "2026-04-18T12:00:02Z",
}
request = urllib.request.Request(
    f"{base}/events/feedback",
    data=json.dumps(feedback_payload).encode("utf-8"),
    headers={"Content-Type": "application/json"},
    method="POST",
)
with opener.open(request, timeout=5) as response:
    first = json.loads(response.read().decode("utf-8"))
with opener.open(request, timeout=5) as response:
    duplicate = json.loads(response.read().decode("utf-8"))
assert first["duplicate"] is False
assert duplicate["duplicate"] is True
print("OK feedback idempotency")
PY

if command -v docker >/dev/null 2>&1; then
  echo "Compose service status:"
  docker compose -f "${PROJECT_ROOT}/docker-compose.yml" ps postgres redis navidrome recommendation-api event-api frontend-web nginx
fi
