"""Adapter for the topology-backed route engine used by the Movebook."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any


class MovebookRouteError(RuntimeError):
    """Raised when the Movebook route engine cannot produce a route."""


class MovebookRouteEngine:
    """Run the existing Movebook route script with a bounded JSON request."""

    def __init__(self, script: str | Path, *, python: str = "/usr/bin/python3", timeout: int = 20):
        self.script = Path(script)
        self.python = python
        self.timeout = timeout

    def route(self, origin: str, destination: str, via_tiplocs: list[str] | None = None) -> dict[str, Any]:
        origin = origin.strip()
        destination = destination.strip()
        via = [code.strip().upper() for code in (via_tiplocs or []) if code.strip()]
        if not origin or not destination:
            raise ValueError("origin and destination are required")
        if len(via) > 12:
            raise ValueError("Choose up to 12 via TIPLOCs")
        if len(via) != len(set(via)):
            raise ValueError("A via TIPLOC can only be selected once")
        if not self.script.is_file():
            raise MovebookRouteError("The Movebook route engine is unavailable")

        try:
            completed = subprocess.run(
                [self.python, str(self.script)],
                input=json.dumps(
                    {"origin": origin, "destination": destination, "via_tiplocs": via}
                ),
                text=True,
                capture_output=True,
                timeout=self.timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise MovebookRouteError("The Movebook route engine did not respond") from exc

        try:
            payload = json.loads(completed.stdout)
        except (TypeError, json.JSONDecodeError) as exc:
            raise MovebookRouteError("The Movebook route engine returned an invalid response") from exc
        if completed.returncode != 0 or not isinstance(payload, dict) or payload.get("error"):
            message = payload.get("error") if isinstance(payload, dict) else None
            raise MovebookRouteError(str(message or "No connected railway route was found"))

        # Keep the Action response focused while retaining topology-backed alternatives.
        candidates = payload.get("candidates", [])
        payload["candidates"] = [
            {
                "tiploc": row.get("tiploc"),
                "label": row.get("label") or row.get("name") or row.get("tiploc"),
                "coordinate": row.get("coordinate"),
            }
            for row in candidates
            if isinstance(row, dict) and row.get("tiploc")
        ][:120]
        return payload
