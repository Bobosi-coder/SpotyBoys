from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from packages.config import load_config


def bootstrap_navidrome() -> None:
    config = load_config()
    if _ping(config.navidrome_base_url, config.navidrome_username, config.navidrome_password):
        print("Navidrome Subsonic user already works")
        return
    payload = json.dumps(
        {"username": config.navidrome_username, "password": config.navidrome_password}
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{config.navidrome_base_url}/auth/createAdmin",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            if response.status >= 400:
                raise RuntimeError(f"Navidrome admin bootstrap failed: HTTP {response.status}")
    except urllib.error.HTTPError as exc:
        if exc.code not in {400, 409, 500}:
            raise
    if not _ping(config.navidrome_base_url, config.navidrome_username, config.navidrome_password):
        raise RuntimeError("Navidrome Subsonic credentials are not usable after bootstrap")
    print("Navidrome Subsonic user bootstrapped")


def _ping(base_url: str, username: str, password: str) -> bool:
    query = urllib.parse.urlencode(
        {"u": username, "p": password, "v": "1.16.1", "c": "spotiboys", "f": "json"}
    )
    try:
        with urllib.request.urlopen(f"{base_url}/rest/ping.view?{query}", timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return False
    return payload.get("subsonic-response", {}).get("status") == "ok"


if __name__ == "__main__":
    bootstrap_navidrome()
