"""Client for the loopback-only internal interface (run via `docker exec`).

    python -m orchid.tools.faultctl GET /status
    python -m orchid.tools.faultctl POST /set '{"metric":"temperature","value":26}'
    python -m orchid.tools.faultctl POST /fault '{"device":"temp01","fault":"frozen"}'
    python -m orchid.tools.faultctl POST /fault/actuator '{"member":"fan02","fault":"STUCK_OFF"}'
"""
from __future__ import annotations

import json
import sys
import urllib.request

from orchid.core.internal_http import DEFAULT_PORT, LOOPBACK


def call(method: str, path: str, body: dict | None = None, timeout: float = 10.0) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"http://{LOOPBACK}:{DEFAULT_PORT}{path}", data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - loopback only
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return {"error": exc.code, "body": exc.read().decode(errors="replace")[:500]}


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[0] not in ("GET", "POST") or not argv[1].startswith("/"):
        print(__doc__, file=sys.stderr)
        return 2
    body = json.loads(argv[2]) if len(argv) > 2 else None
    print(json.dumps(call(argv[0], argv[1], body), default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
