"""Load / write agent interface JSON files under data/inbox|outbox or interfaces/."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .schemas import SCHEMA_BY_AGENT
from .validators import validate_message

DEFAULT_INBOX = Path("data/inbox")
DEFAULT_OUTBOX = Path("data/outbox")


def write_message(
    agent: str,
    direction: str,
    payload: dict[str, Any] | Any,
    *,
    root: Path | None = None,
    name: str | None = None,
) -> Path:
    """Validate and write a message. direction is 'in' or 'out'."""
    model = validate_message(agent, direction, payload)
    base = root or (DEFAULT_INBOX if direction == "in" else DEFAULT_OUTBOX)
    agent_dir = base / agent
    agent_dir.mkdir(parents=True, exist_ok=True)
    obs = getattr(model, "observation_id", "") or "anon"
    filename = name or f"{obs}_{direction}.json"
    path = agent_dir / filename
    path.write_text(model.model_dump_json(indent=2), encoding="utf-8")
    return path


def load_message(agent: str, direction: str, path: str | Path) -> Any:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return validate_message(agent, direction, data)


def example_payloads() -> dict[str, dict[str, Any]]:
    """Return minimal valid example payloads for each agent."""
    return {
        "auditor_in": {
            "observation_id": "obs-example-1",
            "mint": "MintExample111",
            "early_tx_sequence": [{"wallet": "W1", "is_buy": True, "sol": 0.1, "ts": 1.0}],
            "wallets": ["W1", "W2"],
            "creator_activity": {"buys": 1, "sells": 0},
            "holder_concentration": {"top5_share": 0.4},
            "timing": {"age_seconds": 120},
            "tx_sizes": [0.1, 0.2],
            "suspected_coordinated_behavior": [],
            "provenance": [{"feature": "top5_share", "observed_at": 1.0, "source": "rest"}],
            "decision_timestamp": 2.0,
        },
        "auditor_out": {
            "observation_id": "obs-example-1",
            "coordinated_buying": False,
            "wash_trading": False,
            "creator_dump_prep": False,
            "bundled_launch": False,
            "organic_buyer_share": 0.8,
            "confidence": 0.7,
            "flags": [],
            "reasoning": "example",
            "evidence": [{"kind": "observed", "claim": "diverse buyers", "source": "ws"}],
        },
        "narrative_in": {
            "observation_id": "obs-example-1",
            "mint": "MintExample111",
            "token_metadata": {"name": "Cat", "symbol": "CAT"},
            "image_description": "a cat meme",
            "social_urls": {"twitter": "https://x.com/example"},
            "current_narrative_evidence": [
                {"kind": "observed", "claim": "twitter link present", "source": "metadata"}
            ],
            "decision_timestamp": 2.0,
        },
        "narrative_out": {
            "observation_id": "obs-example-1",
            "trend_fit": 0.5,
            "virality": 0.4,
            "community_signals": 0.3,
            "launch_timing": 0.5,
            "reasoning": "example",
            "observed_evidence": [
                {"kind": "observed", "claim": "has twitter", "source": "metadata"}
            ],
            "inferences": [
                {"kind": "inference", "claim": "may fit cat meta", "confidence": 0.4}
            ],
        },
        "timing_in": {
            "observation_id": "obs-example-1",
            "pumpfun_market_activity": {"launches_per_hour": 100},
            "launch_rate": 100.0,
            "migration_graduation_rate": 0.02,
            "volume": 500.0,
            "sol_context": {"price_usd": 150.0},
            "timestamp": 2.0,
        },
        "timing_out": {
            "observation_id": "obs-example-1",
            "market_sentiment": 0.5,
            "meme_season": 0.5,
            "volume_level": 0.5,
            "anomalies": [],
            "reasoning": "example",
            "fetched_at": 2.0,
        },
        "checker_in": {
            "observation_id": "obs-example-1",
            "mint": "MintExample111",
            "auditor": {"organic_buyer_share": 0.8},
            "narrative": {"virality": 0.4},
            "timing": {"market_sentiment": 0.5},
            "metrics": {"top5_share": 0.3},
            "features_with_provenance": [
                {"name": "top5_share", "observed_at": 1.0, "decision_timestamp": 2.0, "causal": True}
            ],
            "paper_fill_assumptions": {"fee_pct": 1.0, "max_impact_pct": 3.0},
            "decision_timestamp": 2.0,
        },
        "checker_out": {
            "observation_id": "obs-example-1",
            "approve": False,
            "reason": "example falsification: insufficient liquidity",
            "flags": ["insufficient_liquidity"],
            "confidence": 0.9,
            "falsification_notes": ["liquidity below paper fill assumption"],
            "checks": {
                "data_leakage": False,
                "stale_data": False,
                "missing_provenance": False,
                "coordinated_wallets": False,
                "creator_involvement": False,
                "concentration": False,
                "contradictory_signals": False,
                "bad_execution_assumptions": False,
                "insufficient_liquidity": True,
                "selection_bias": False,
            },
        },
        "executor_in": {
            "observation_id": "obs-example-1",
            "mint": "MintExample111",
            "size_sol": 0.1,
            "checker_approved": True,
            "signal_timestamp": 1.0,
            "decision_timestamp": 2.0,
            "mode": "paper",
        },
        "executor_out": {
            "observation_id": "obs-example-1",
            "status": "UNTRADEABLE",
            "attempt": {},
            "error": "example: no quote",
        },
    }


def build_auditor_input_from_observation(obs: Any, decision_timestamp: float) -> dict[str, Any]:
    """Build AuditorIn payload from Observation with enriched Wave1 data."""
    # Extract early tx sequence with provenance
    early_tx = obs.early_tx_sequence if hasattr(obs, "early_tx_sequence") else []
    
    # Build holder concentration from enriched top5_share
    holder_concentration = {}
    if hasattr(obs, "top5_share") and obs.top5_share is not None:
        holder_concentration["top5_share"] = obs.top5_share
    
    # Extract wallets from early tx
    wallets = list({tx["wallet"] for tx in early_tx if "wallet" in tx})
    
    # Build provenance list
    provenance = []
    if hasattr(obs, "features"):
        provenance = [
            {
                "feature": f.name,
                "observed_at": f.observed_at,
                "source": f.source,
                "provenance": f.provenance,
            }
            for f in obs.features
        ]
    
    return {
        "observation_id": obs.observation_id,
        "mint": obs.mint,
        "early_tx_sequence": early_tx,
        "wallets": wallets,
        "creator_activity": {"transactions": getattr(obs, "creator_transactions", 0)},
        "holder_concentration": holder_concentration,
        "timing": {"age_seconds": decision_timestamp - obs.creation_timestamp},
        "tx_sizes": [tx["sol"] for tx in early_tx if "sol" in tx],
        "suspected_coordinated_behavior": [],
        "provenance": provenance,
        "decision_timestamp": decision_timestamp,
    }


def build_timing_input_from_observation(obs: Any, decision_timestamp: float) -> dict[str, Any]:
    """Build TimingIn payload with global timing snapshot.
    
    Uses decision_timestamp consistently - never fetched_at after decision.
    """
    snapshot = obs.global_timing_snapshot if hasattr(obs, "global_timing_snapshot") else {}
    if not snapshot:
        snapshot = {}
    
    return {
        "observation_id": obs.observation_id,
        "pumpfun_market_activity": {
            "unique_buyers": getattr(obs, "unique_buyers", 0),
            "trade_count": getattr(obs, "trade_count", 0),
        },
        "launch_rate": snapshot.get("launch_rate", 0.0),
        "migration_graduation_rate": snapshot.get("migration_graduation_rate", 0.0),
        "volume": getattr(obs, "volume", 0.0),
        "sol_context": {"price_usd": snapshot.get("sol_usd")},
        "broader_market_context": snapshot,
        "timestamp": decision_timestamp,  # Always use decision_timestamp, never fetched_at
    }


# Silence unused import warning path for SCHEMA re-export consumers
_SCHEMAS = SCHEMA_BY_AGENT
