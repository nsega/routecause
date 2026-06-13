"""Minimal Prometheus HTTP API client (the only metrics source; no web lookups)."""

from __future__ import annotations

import httpx

from .config import PROM_URL_DEFAULT


class PrometheusError(RuntimeError):
    pass


class PrometheusClient:
    def __init__(self, base_url: str = PROM_URL_DEFAULT, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def query(self, promql: str) -> list[dict]:
        try:
            r = httpx.get(
                f"{self.base_url}/api/v1/query",
                params={"query": promql},
                timeout=self.timeout,
            )
            r.raise_for_status()
        except httpx.HTTPError as e:  # pragma: no cover - network failure path
            raise PrometheusError(f"query failed ({promql!r}): {e}") from e
        data = r.json()
        if data.get("status") != "success":
            raise PrometheusError(f"prometheus returned {data.get('status')}: {data}")
        return data["data"]["result"]

    def scalar(self, promql: str) -> float | None:
        """Single pool-wide value, or None when no series matched."""
        result = self.query(promql)
        if not result:
            return None
        return float(result[0]["value"][1])

    def by_backend(self, promql: str) -> dict[str, float]:
        """Map ``backend`` label -> value (falls back to ``pod`` label)."""
        out: dict[str, float] = {}
        for series in self.query(promql):
            label = series["metric"].get("backend") or series["metric"].get("pod", "?")
            out[label] = float(series["value"][1])
        return out
