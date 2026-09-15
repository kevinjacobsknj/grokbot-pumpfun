"""Observation universe for paper-only launch research.

Every detected launch gets a unique observation_id. We store accepted AND
rejected candidates, dead tokens, migrations, missing data, API failures,
WebSocket disconnects, quote failures, and paper execution failures.

Features used for a decision MUST carry timestamps + provenance and must
have been observable BEFORE decision_timestamp (no look-ahead).
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field

# Early-life snapshot windows (seconds since creation).
OBSERVATION_WINDOWS: tuple[float, ...] = (
    0.0,
    10.0,
    20.0,
    30.0,
    60.0,
    120.0,
    180.0,
    300.0,
    600.0,
    1200.0,
    1800.0,
    3600.0,
)

WindowStatus = Literal["observed", "no_event", "missing", "incomplete"]
IntegrityKind = Literal[
    "ws_disconnect",
    "api_failure",
    "quote_failure",
    "paper_execution_failure",
    "missing_trade_stream",
    "incomplete",
    "reject",
    "accept",
    "migration",
    "non_migration",
    "dead_token",
    "data_gap",
]


def new_observation_id() -> str:
    return str(uuid.uuid4())


class ProvenancedFeature(BaseModel):
    """A single feature with explicit observability metadata."""

    name: str
    value: Any = None
    observed_at: float = 0.0
    decision_timestamp: float = 0.0
    source: str = ""
    provenance: str = ""
    # True iff observed_at <= decision_timestamp (when decision known).
    causal: bool = True

    def validate_causality(self) -> bool:
        if self.decision_timestamp <= 0:
            return True
        self.causal = self.observed_at <= self.decision_timestamp + 1e-9
        return self.causal


class WindowSnapshot(BaseModel):
    """State at a scheduled early-life window.

    Event-driven: if no event occurred by the window, status=no_event
    (do NOT fabricate a snapshot from later data).
    """

    window_seconds: float
    status: WindowStatus = "no_event"
    recorded_at: float = 0.0
    market_cap: float | None = None
    liquidity: float | None = None
    bonding_curve_state: dict[str, Any] = Field(default_factory=dict)
    trade_count: int | None = None
    buy_count: int | None = None
    sell_count: int | None = None
    unique_buyers: int | None = None
    unique_sellers: int | None = None
    volume: float | None = None
    note: str = ""


class IntegrityEvent(BaseModel):
    kind: IntegrityKind
    timestamp: float = Field(default_factory=time.time)
    observation_id: str = ""
    mint: str = ""
    detail: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


class Observation(BaseModel):
    """Full launch observation — never deleted when outcomes are bad."""

    observation_id: str = Field(default_factory=new_observation_id)
    mint: str = ""
    symbol: str | None = None
    name: str | None = None
    creator: str | None = None
    creation_timestamp: float = 0.0
    first_seen_timestamp: float = Field(default_factory=time.time)
    source: str = "pumpportal_ws"
    metadata: dict[str, Any] = Field(default_factory=dict)
    social_urls: dict[str, str | None] = Field(default_factory=dict)

    market_cap: float = 0.0
    bonding_curve_state: dict[str, Any] = Field(default_factory=dict)
    liquidity: float = 0.0
    trade_count: int = 0
    buy_count: int = 0
    sell_count: int = 0
    unique_buyers: int = 0
    unique_sellers: int = 0
    volume: float = 0.0
    creator_transactions: int = 0
    migration_state: str = "unknown"  # unknown | on_curve | migrated | dead

    # Filter / pipeline disposition
    candidate_status: str = "pending"  # pending | accepted | rejected | dead
    reject_reason: str = ""

    # Data quality flags — REST must NOT silently substitute for trade stream.
    missing_trade_stream: bool = False
    incomplete: bool = False
    trade_stream_events: int = 0
    rest_enrichment_only: bool = False

    windows: dict[str, WindowSnapshot] = Field(default_factory=dict)
    features: list[ProvenancedFeature] = Field(default_factory=list)
    integrity_events: list[IntegrityEvent] = Field(default_factory=list)

    def window_key(self, seconds: float) -> str:
        if seconds < 60:
            return f"{int(seconds)}s"
        if seconds < 3600:
            return f"{int(seconds // 60)}m"
        return f"{int(seconds // 3600)}h"

    def ensure_windows(self) -> None:
        for w in OBSERVATION_WINDOWS:
            key = self.window_key(w)
            if key not in self.windows:
                self.windows[key] = WindowSnapshot(
                    window_seconds=w,
                    status="no_event",
                    note="no observed event at this window",
                )

    def record_event_at(
        self,
        age_seconds: float,
        *,
        market_cap: float | None = None,
        liquidity: float | None = None,
        bonding_curve_state: dict[str, Any] | None = None,
        trade_count: int | None = None,
        buy_count: int | None = None,
        sell_count: int | None = None,
        unique_buyers: int | None = None,
        unique_sellers: int | None = None,
        volume: float | None = None,
    ) -> None:
        """Mark the largest window <= age as observed from a real event."""
        self.ensure_windows()
        eligible = [w for w in OBSERVATION_WINDOWS if w <= age_seconds + 1e-9]
        if not eligible:
            return
        target = max(eligible)
        key = self.window_key(target)
        snap = self.windows[key]
        # Only fill if still no_event — first event wins; never overwrite with later invent.
        if snap.status == "observed":
            return
        snap.status = "observed"
        snap.recorded_at = time.time()
        snap.market_cap = market_cap
        snap.liquidity = liquidity
        snap.bonding_curve_state = bonding_curve_state or {}
        snap.trade_count = trade_count
        snap.buy_count = buy_count
        snap.sell_count = sell_count
        snap.unique_buyers = unique_buyers
        snap.unique_sellers = unique_sellers
        snap.volume = volume
        snap.note = "from trade/create stream event"

    def add_feature(self, feature: ProvenancedFeature) -> None:
        feature.validate_causality()
        self.features.append(feature)

    def flag_integrity(self, kind: IntegrityKind, detail: str = "", **payload: Any) -> None:
        ev = IntegrityEvent(
            kind=kind,
            observation_id=self.observation_id,
            mint=self.mint,
            detail=detail,
            payload=payload,
        )
        self.integrity_events.append(ev)
        if kind == "missing_trade_stream":
            self.missing_trade_stream = True
            self.incomplete = True
        if kind in ("incomplete", "api_failure", "ws_disconnect", "data_gap"):
            self.incomplete = True
