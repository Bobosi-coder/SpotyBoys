from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request

from packages.config import load_config


def bootstrap_navidrome() -> None:
    config = load_config()
    deadline = time.monotonic() + 180
    last_error = ""
    while time.monotonic() < deadline:
        _touch_extauth_session(config.navidrome_base_url, config.navidrome_username)
        if _ping(config.navidrome_base_url, config.navidrome_username):
            print("Navidrome ExtAuth Subsonic access works")
            return
        last_error = f"ExtAuth ping failed for Remote-User={config.navidrome_username}"
        time.sleep(5)
    raise RuntimeError(f"Navidrome ExtAuth is not usable: {last_error}")


def _touch_extauth_session(base_url: str, username: str) -> None:
    """Trigger Navidrome's ExtAuth user auto-provisioning for service users."""
    for path in ("/app/", "/api/keepalive"):
        request = urllib.request.Request(
            f"{base_url}{path}",
            headers={"Remote-User": str(username)},
        )
        try:
            with urllib.request.urlopen(request, timeout=5):
                pass
        except Exception:
            continue


def _ping(base_url: str, username: str) -> bool:
    query = urllib.parse.urlencode(
        {"u": username, "v": "1.16.1", "c": "spotiboys", "f": "json"}
    )
    request = urllib.request.Request(
        f"{base_url}/rest/ping.view?{query}",
        headers={"Remote-User": str(username)},
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return False
    return payload.get("subsonic-response", {}).get("status") == "ok"


if __name__ == "__main__":
    bootstrap_navidrome()
