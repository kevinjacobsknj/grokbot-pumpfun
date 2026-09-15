"""Pydantic schemas for AUDITOR / NARRATIVE / TIMING / CHECKER / EXECUTOR I/O."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

AgentName = Literal["auditor", "narrative", "timing", "checker", "executor"]


class EvidenceItem(BaseModel):
    """Observed evidence vs inference must be distinguished."""

    kind: Literal["observed", "inference"] = "observed"
    claim: str
    source: str = ""
    timestamp: float = 0.0
    confidence: float = 0.0


class AuditorIn(BaseModel):
    observation_id: str
    mint: str
    early_tx_sequence: list[dict[str, Any]] = Field(default_factory=list)
    wallets: list[str] = Field(default_factory=list)
    creator_activity: dict[str, Any] = Field(default_factory=dict)
    holder_concentration: dict[str, Any] = Field(default_factory=dict)
    timing: dict[str, Any] = Field(default_factory=dict)
    tx_sizes: list[float] = Field(default_factory=list)
    suspected_coordinated_behavior: list[str] = Field(default_factory=list)
    provenance: list[dict[str, Any]] = Field(default_factory=list)
    decision_timestamp: float = 0.0


class AuditorOut(BaseModel):
    observation_id: str
    coordinated_buying: bool = True
    wash_trading: bool = True
    creator_dump_prep: bool = True
    bundled_launch: bool = True
    organic_buyer_share: float = 0.0
    confidence: float = 0.0
    flags: list[str] = Field(default_factory=list)
    reasoning: str = ""
    evidence: list[EvidenceItem] = Field(default_factory=list)


class NarrativeIn(BaseModel):
    observation_id: str
    mint: str
    token_metadata: dict[str, Any] = Field(default_factory=dict)
    image_description: str = ""
    social_urls: dict[str, str | None] = Field(default_factory=dict)
    creator_social_info: dict[str, Any] = Field(default_factory=dict)
    current_narrative_evidence: list[EvidenceItem] = Field(default_factory=list)
    timestamped_sources: list[dict[str, Any]] = Field(default_factory=list)
    decision_timestamp: float = 0.0


class NarrativeOut(BaseModel):
    observation_id: str
    trend_fit: float = 0.0
    virality: float = 0.0
    community_signals: float = 0.0
    launch_timing: float = 0.0
    reasoning: str = ""
    observed_evidence: list[EvidenceItem] = Field(default_factory=list)
    inferences: list[EvidenceItem] = Field(default_factory=list)


class TimingIn(BaseModel):
    observation_id: str = ""
    pumpfun_market_activity: dict[str, Any] = Field(default_factory=dict)
    launch_rate: float = 0.0
    migration_graduation_rate: float = 0.0
    volume: float = 0.0
    sol_context: dict[str, Any] = Field(default_factory=dict)
    broader_market_context: dict[str, Any] = Field(default_factory=dict)
    timestamp: float = 0.0


class TimingOut(BaseModel):
    observation_id: str = ""
    market_sentiment: float = 0.0
    meme_season: float = 0.0
    volume_level: float = 0.0
    anomalies: list[str] = Field(default_factory=list)
    reasoning: str = ""
    fetched_at: float = 0.0


class CheckerIn(BaseModel):
    """COMPLETE evidence package. Purpose: FALSIFY, not confirm."""

    observation_id: str
    mint: str
    auditor: dict[str, Any] = Field(default_factory=dict)
    narrative: dict[str, Any] = Field(default_factory=dict)
    timing: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)
    features_with_provenance: list[dict[str, Any]] = Field(default_factory=list)
    paper_fill_assumptions: dict[str, Any] = Field(default_factory=dict)
    decision_timestamp: float = 0.0


class CheckerOut(BaseModel):
    observation_id: str
    approve: bool = False
    reason: str = ""
    flags: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    falsification_notes: list[str] = Field(default_factory=list)
    # Explicit checks the checker should attempt to fail
    checks: dict[str, bool] = Field(
        default_factory=lambda: {
            "data_leakage": False,
            "stale_data": False,
            "missing_provenance": False,
            "coordinated_wallets": False,
            "creator_involvement": False,
            "concentration": False,
            "contradictory_signals": False,
            "bad_execution_assumptions": False,
            "insufficient_liquidity": False,
            "selection_bias": False,
        }
    )


class ExecutorIn(BaseModel):
    """Candidate ONLY after checker approval. PAPER-ONLY."""

    observation_id: str
    mint: str
    size_sol: float
    checker_approved: bool = False
    signal_timestamp: float = 0.0
    decision_timestamp: float = 0.0
    mode: Literal["paper", "dry-run"] = "paper"


class ExecutorOut(BaseModel):
    observation_id: str
    status: Literal["FILLED", "FAILED", "UNTRADEABLE", "PARTIAL", "REFUSED"] = "REFUSED"
    attempt: dict[str, Any] = Field(default_factory=dict)
    error: str = ""


SCHEMA_BY_AGENT: dict[str, dict[str, type[BaseModel]]] = {
    "auditor": {"in": AuditorIn, "out": AuditorOut},
    "narrative": {"in": NarrativeIn, "out": NarrativeOut},
    "timing": {"in": TimingIn, "out": TimingOut},
    "checker": {"in": CheckerIn, "out": CheckerOut},
    "executor": {"in": ExecutorIn, "out": ExecutorOut},
}
