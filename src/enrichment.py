"""Enrichment logic for observations: holders, global timing metrics.

NEVER invent data. If REST fails, flag data_gap and leave fields null.
All enriched features MUST have observable provenance and timestamps.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from .models import Config
from .observation import Observation

log = logging.getLogger(__name__)

# REST enrichment constants
HOLDER_LIMIT = 50
EARLY_TRADE_WINDOW_SECONDS = 60.0
MAX_EARLY_TX_SEQUENCE = 100


class HolderEnricher:
    """Fetch holders from REST and compute top5_share with provenance."""

    def __init__(self, config: Config, client: httpx.AsyncClient | None = None) -> None:
        self.config = config
        self.data = config.data
        self._client = client
        self._owns_client = client is None

    async def __aenter__(self) -> HolderEnricher:
        if self._client is None:
            headers = {"Accept": "application/json"}
            if self.data.key:
                headers["Authorization"] = f"Bearer {self.data.key}"
            self._client = httpx.AsyncClient(
                base_url=self.data.rest_url,
                timeout=self.data.request_timeout,
                headers=headers,
            )
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("HolderEnricher used outside `async with`")
        return self._client

    async def enrich_observation(self, obs: Observation) -> bool:
        """Fetch holders and compute top5_share. Returns True on success."""
        if obs.holders_enriched:
            return True
        
        try:
            resp = await self.client.get(f"/coins/{obs.mint}/holders", params={"limit": HOLDER_LIMIT})
            resp.raise_for_status()
            holders_raw = resp.json()
        except Exception as exc:
            log.warning("holder fetch failed for %s: %s", obs.mint, exc)
            obs.flag_integrity("api_failure", detail=f"holders REST failed: {exc}")
            obs.holders_enrichment_incomplete = True
            return False

        if not holders_raw:
            log.info("no holders data for %s", obs.mint)
            obs.holders_enrichment_incomplete = True
            obs.flag_integrity("data_gap", detail="no holders returned from REST")
            return False

        # Compute top5_share
        holders = []
        for h in holders_raw:
            if not isinstance(h, dict):
                continue
            share = h.get("share")
            if share is None:
                pct = h.get("percentage")
                share = float(pct) / 100.0 if pct is not None else 0.0
            holders.append(float(share))

        if not holders:
            obs.holders_enrichment_incomplete = True
            obs.flag_integrity("data_gap", detail="no valid holder shares in REST response")
            return False

        # Sort descending and take top 5
        holders.sort(reverse=True)
        top5 = sum(holders[:5])
        
        # Record with provenance
        observed_at = time.time()
        obs.top5_share = round(min(1.0, top5), 4)
        obs.top5_share_observed_at = observed_at
        obs.top5_share_provenance = f"pump.fun REST /coins/{obs.mint}/holders at {observed_at}"
        obs.holders_enriched = True
        
        # Add as provenanced feature for agent consumption
        from .observation import ProvenancedFeature
        obs.add_feature(ProvenancedFeature(
            name="top5_share",
            value=obs.top5_share,
            observed_at=observed_at,
            source="pumpfun_rest_holders",
            provenance=obs.top5_share_provenance,
        ))
        
        log.info("enriched %s with top5_share=%.4f from %d holders", obs.mint, obs.top5_share, len(holders))
        return True


class GlobalMarketMetrics:
    """Track global timing metrics: launch_rate, migration_graduation_rate, sol_usd.
    
    Observable only — never invent. If data unavailable, fields remain null.
    """

    def __init__(self) -> None:
        self._launch_count = 0
        self._migration_count = 0
        self._window_start = time.time()
        self._sol_usd: float | None = None
        self._sol_usd_observed_at: float | None = None
        self._lock = asyncio.Lock()

    def record_launch(self) -> None:
        """Increment launch counter."""
        self._launch_count += 1

    def record_migration(self) -> None:
        """Increment migration counter."""
        self._migration_count += 1

    async def fetch_sol_price(self, client: httpx.AsyncClient | None = None) -> None:
        """Attempt to fetch SOL/USD price from public API (optional)."""
        # Example: could use CoinGecko, Binance, or other public API
        # For now, we leave this as a placeholder that keeps sol_usd=None
        # A real implementation would call an API here
        pass

    async def snapshot(self) -> dict[str, Any]:
        """Return current global metrics snapshot. Never invent — null if unavailable."""
        async with self._lock:
            now = time.time()
            elapsed = max(1.0, now - self._window_start)
            
            # Rates per hour
            launch_rate = (self._launch_count / elapsed) * 3600.0 if elapsed > 0 else 0.0
            migration_rate = (self._migration_count / elapsed) * 3600.0 if elapsed > 0 else 0.0
            
            return {
                "launch_rate": round(launch_rate, 2),  # launches per hour
                "migration_graduation_rate": round(migration_rate, 2),  # migrations per hour
                "sol_usd": self._sol_usd,  # null if not fetched
                "observed_at": now,
                "window_duration_seconds": round(elapsed, 1),
            }

    def reset_window(self) -> None:
        """Reset counters (e.g., daily or per session)."""
        self._launch_count = 0
        self._migration_count = 0
        self._window_start = time.time()
