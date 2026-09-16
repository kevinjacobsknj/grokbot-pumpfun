"""Persistent launch monitor for the paper observation universe.

Prefer PumpPortal WebSocket for signals. REST is enrichment only — never
silently substitute for missing trade-stream data (flag missing_trade_stream).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

import websockets

from .models import Config, Token
from .monitor import LaunchMonitor, parse_create_event
from .observation import Observation, new_observation_id
from .observation_store import ObservationStore as Store

log = logging.getLogger(__name__)

__all__ = ["ObservationMonitor", "Observation", "new_observation_id"]


class ObservationMonitor:
    """Wraps LaunchMonitor: assigns observation_id to EVERY launch, stores all."""

    def __init__(
        self,
        config: Config,
        store: Store | None = None,
        on_skip: Callable[[Token, str], None] | None = None,
    ) -> None:
        self.config = config
        self.store = store or Store()
        self._inner = LaunchMonitor(config, on_skip=self._on_skip_bridge)
        self._user_on_skip = on_skip
        self._by_mint: dict[str, Observation] = {}
        self._sellers: dict[str, set[str]] = {}
        self._buy_counts: dict[str, int] = {}
        self._sell_counts: dict[str, int] = {}
        self._volume: dict[str, float] = {}
        self._creator_tx: dict[str, int] = {}
        self._trade_events: dict[str, int] = {}

    def _on_skip_bridge(self, token: Token, reason: str) -> None:
        obs = self._by_mint.get(token.mint)
        if obs is not None:
            obs.candidate_status = "rejected"
            obs.reject_reason = reason
            obs.flag_integrity("reject", detail=reason)
            self._sync_token_fields(obs, token)
            self.store.write_observation(obs)
            self.store.write_integrity(obs.integrity_events[-1])
        if self._user_on_skip:
            self._user_on_skip(token, reason)

    def _ensure_obs(self, token: Token, source: str = "pumpportal_ws") -> Observation:
        if token.mint in self._by_mint:
            return self._by_mint[token.mint]
        obs = Observation(
            mint=token.mint,
            symbol=token.symbol,
            name=token.name,
            creator=token.creator,
            creation_timestamp=token.created_timestamp,
            first_seen_timestamp=time.time(),
            source=source,
            metadata={
                "description": token.description,
                "image_uri": token.image_uri,
                "metadata_uri": token.metadata_uri,
            },
            social_urls={
                "twitter": token.twitter,
                "telegram": token.telegram,
                "website": token.website,
            },
            market_cap=token.market_cap_sol,
            liquidity=token.sol_in_curve,
            migration_state="on_curve",
        )
        obs.ensure_windows()
        age = max(0.0, time.time() - (token.created_timestamp or time.time()))
        obs.record_event_at(
            age,
            market_cap=token.market_cap_sol,
            liquidity=token.sol_in_curve,
            unique_buyers=token.unique_buyers,
        )
        self._by_mint[token.mint] = obs
        self._sellers.setdefault(token.mint, set())
        self._buy_counts.setdefault(token.mint, 0)
        self._sell_counts.setdefault(token.mint, 0)
        self._volume.setdefault(token.mint, 0.0)
        self._creator_tx.setdefault(token.mint, 0)
        self._trade_events.setdefault(token.mint, 0)
        return obs

    def _sync_token_fields(self, obs: Observation, token: Token) -> None:
        obs.market_cap = token.market_cap_sol
        obs.liquidity = token.sol_in_curve
        obs.unique_buyers = token.unique_buyers
        obs.unique_sellers = len(self._sellers.get(token.mint, set()))
        obs.buy_count = self._buy_counts.get(token.mint, 0)
        obs.sell_count = self._sell_counts.get(token.mint, 0)
        obs.trade_count = obs.buy_count + obs.sell_count
        obs.volume = self._volume.get(token.mint, 0.0)
        obs.creator_transactions = self._creator_tx.get(token.mint, 0)
        obs.trade_stream_events = self._trade_events.get(token.mint, 0)
        obs.bonding_curve_state = {
            "sol_in_curve": token.sol_in_curve,
            "curve_progress": token.curve_progress,
            "market_cap_sol": token.market_cap_sol,
        }

    def handle_event(self, payload: dict[str, Any]) -> Token | None:
        """Process one WS payload: always record observation; maybe promote token."""
        tx_type = payload.get("txType")

        if tx_type in ("create", "created"):
            token = parse_create_event(payload)
            if token is None:
                return None
            obs = self._ensure_obs(token)
            self._sync_token_fields(obs, token)
            # Persist create immediately so rejects/deaths still leave a trail.
            self.store.write_observation(obs)
            # Delegate promotion logic to inner monitor.
            return self._inner.handle_event(payload)

        mint = payload.get("mint")
        if not mint:
            return None

        # Trade / other events for known or unknown mints
        if mint in self._by_mint or mint in self._inner.pending:
            if mint not in self._by_mint and mint in self._inner.pending:
                self._ensure_obs(self._inner.pending[mint])
            obs = self._by_mint.get(mint)
            if obs is not None:
                self._trade_events[mint] = self._trade_events.get(mint, 0) + 1
                wallet = payload.get("traderPublicKey") or payload.get("wallet")
                sol_amt = float(payload.get("solAmount") or payload.get("sol_amount") or 0.0)
                if tx_type == "buy":
                    self._buy_counts[mint] = self._buy_counts.get(mint, 0) + 1
                    self._volume[mint] = self._volume.get(mint, 0.0) + sol_amt
                elif tx_type == "sell":
                    self._sell_counts[mint] = self._sell_counts.get(mint, 0) + 1
                    self._volume[mint] = self._volume.get(mint, 0.0) + sol_amt
                    if wallet:
                        self._sellers.setdefault(mint, set()).add(wallet)
                if wallet and obs.creator and wallet == obs.creator:
                    self._creator_tx[mint] = self._creator_tx.get(mint, 0) + 1
                if payload.get("complete") or payload.get("raydium_pool"):
                    obs.migration_state = "migrated"
                    obs.flag_integrity("migration", detail="bonding curve complete")

                # Update token via inner first so unique_buyers etc. stay in sync
                promoted = self._inner.handle_event(payload)
                token_ref = self._inner.pending.get(mint) or (
                    promoted if promoted is not None else None
                )
                if token_ref is not None:
                    self._sync_token_fields(obs, token_ref)
                    age = max(0.0, time.time() - (token_ref.created_timestamp or time.time()))
                    obs.record_event_at(
                        age,
                        market_cap=token_ref.market_cap_sol,
                        liquidity=token_ref.sol_in_curve,
                        bonding_curve_state=obs.bonding_curve_state,
                        trade_count=obs.trade_count,
                        buy_count=obs.buy_count,
                        sell_count=obs.sell_count,
                        unique_buyers=obs.unique_buyers,
                        unique_sellers=obs.unique_sellers,
                        volume=obs.volume,
                    )
                if promoted is not None:
                    obs.candidate_status = "accepted"
                    obs.reject_reason = ""
                    self.store.write_observation(obs)
                    self._attach_id(promoted, obs.observation_id)
                    return promoted
                return None

        return self._inner.handle_event(payload)

    def mark_enrichment_without_stream(self, mint: str, detail: str = "") -> None:
        """REST enrichment arrived but trade stream was missing — must flag."""
        obs = self._by_mint.get(mint)
        if obs is None:
            return
        obs.rest_enrichment_only = True
        obs.flag_integrity(
            "missing_trade_stream",
            detail=detail or "REST enrichment without trade-stream events",
        )
        self.store.write_integrity(obs.integrity_events[-1])
        self.store.write_observation(obs)

    def sweep(self, now: float | None = None) -> list[Token]:
        ready = self._inner.sweep(now=now)
        for token in ready:
            obs = self._by_mint.get(token.mint)
            if obs is not None:
                obs.candidate_status = "accepted"
                self._sync_token_fields(obs, token)
                if obs.trade_stream_events == 0 and obs.unique_buyers == 0:
                    obs.flag_integrity(
                        "missing_trade_stream",
                        detail="promoted without observed trade-stream buys",
                    )
                    self.store.write_integrity(obs.integrity_events[-1])
                self.store.write_observation(obs)
                self._attach_id(token, obs.observation_id)
        return ready

    @staticmethod
    def _attach_id(token: Token, observation_id: str) -> None:
        """Stamp observation_id on Token (extra='allow')."""
        try:
            token.observation_id = observation_id  # type: ignore[attr-defined]
        except Exception:
            extra = getattr(token, "__pydantic_extra__", None)
            if isinstance(extra, dict):
                extra["observation_id"] = observation_id

    def observation_for(self, mint: str) -> Observation | None:
        return self._by_mint.get(mint)

    async def stream(self) -> AsyncIterator[Token]:
        """WS stream that records the full observation universe."""
        sweeper_delay = 10.0
        backoff = 1.0
        while True:
            try:
                async with websockets.connect(self.config.data.ws_url) as ws:
                    await ws.send(json.dumps({"method": "subscribeNewToken"}))
                    log.info("observation monitor connected to %s", self.config.data.ws_url)
                    backoff = 1.0
                    last_sweep = time.time()
                    while True:
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=sweeper_delay)
                        except TimeoutError:
                            raw = None
                        if raw:
                            try:
                                payload = json.loads(raw)
                            except json.JSONDecodeError:
                                continue
                            if isinstance(payload, dict):
                                token = self.handle_event(payload)
                                if token is not None:
                                    await LaunchMonitor._subscribe_trades(ws, token.mint, off=True)
                                    yield token
                                elif payload.get("txType") in ("create", "created"):
                                    mint = payload.get("mint")
                                    if mint:
                                        await LaunchMonitor._subscribe_trades(ws, mint)
                        if time.time() - last_sweep >= sweeper_delay:
                            last_sweep = time.time()
                            for token in self.sweep():
                                yield token
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.store.log_ws_disconnect(detail=str(exc))
                log.warning(
                    "observation monitor disconnected (%s), reconnect in %.0fs",
                    exc,
                    backoff,
                )
                with contextlib.suppress(asyncio.CancelledError):
                    await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)
