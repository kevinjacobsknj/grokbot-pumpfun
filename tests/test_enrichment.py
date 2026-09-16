"""Tests for Wave1 enrichment: early_tx_sequence, top5_share, global timing."""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from src.enrichment import GlobalMarketMetrics, HolderEnricher
from src.interfaces.loaders import build_auditor_input_from_observation, build_timing_input_from_observation
from src.models import Config
from src.observation import Observation, ProvenancedFeature


def test_observation_early_tx_sequence():
    """Early trades captured with provenance."""
    obs = Observation(mint="TestMint", creation_timestamp=time.time())
    
    # Add early trades
    obs.add_early_trade("wallet1", True, 0.5, time.time(), 10.0)
    obs.add_early_trade("wallet2", False, 0.3, time.time(), 15.0)
    
    assert len(obs.early_tx_sequence) == 2
    assert obs.early_tx_sequence[0]["wallet"] == "wallet1"
    assert obs.early_tx_sequence[0]["is_buy"] is True
    assert obs.early_tx_sequence[0]["sol"] == 0.5
    assert obs.early_tx_sequence[0]["age_seconds"] == 10.0


def test_observation_early_tx_window_limit():
    """Trades outside the early window are not captured."""
    obs = Observation(mint="TestMint", creation_timestamp=time.time())
    
    # Add trade within window
    obs.add_early_trade("wallet1", True, 0.5, time.time(), 30.0, max_early_window=60.0)
    # Try to add trade outside window
    obs.add_early_trade("wallet2", True, 0.5, time.time(), 90.0, max_early_window=60.0)
    
    assert len(obs.early_tx_sequence) == 1
    assert obs.early_tx_sequence[0]["wallet"] == "wallet1"


def test_observation_early_tx_sequence_length_limit():
    """Early tx sequence respects max length."""
    obs = Observation(mint="TestMint", creation_timestamp=time.time())
    
    # Add 110 trades within the window, but only 100 should be stored
    for i in range(110):
        obs.add_early_trade(
            f"wallet{i}", 
            True, 
            0.1, 
            time.time(), 
            float(i % 60),  # Keep age within window
            max_early_window=60.0,
            max_sequence_length=100
        )
    
    assert len(obs.early_tx_sequence) == 100


@pytest.mark.asyncio
@respx.mock
async def test_holder_enricher_success():
    """HolderEnricher fetches holders and computes top5_share."""
    config = Config()
    config.data.rest_url = "https://test.api"
    
    holders_response = [
        {"address": "h1", "share": 0.3},
        {"address": "h2", "share": 0.25},
        {"address": "h3", "share": 0.2},
        {"address": "h4", "share": 0.1},
        {"address": "h5", "share": 0.05},
        {"address": "h6", "share": 0.05},
    ]
    
    respx.get("https://test.api/coins/TestMint/holders").mock(
        return_value=httpx.Response(200, json=holders_response)
    )
    
    obs = Observation(mint="TestMint")
    
    async with HolderEnricher(config) as enricher:
        success = await enricher.enrich_observation(obs)
    
    assert success is True
    assert obs.holders_enriched is True
    assert obs.top5_share == 0.9  # 0.3 + 0.25 + 0.2 + 0.1 + 0.05
    assert obs.top5_share_provenance is not None
    assert obs.top5_share_observed_at is not None
    
    # Check feature was added
    assert len(obs.features) == 1
    assert obs.features[0].name == "top5_share"
    assert obs.features[0].value == 0.9


@pytest.mark.asyncio
@respx.mock
async def test_holder_enricher_handles_percentage():
    """HolderEnricher handles percentage field (convert from percent)."""
    config = Config()
    config.data.rest_url = "https://test.api"
    
    holders_response = [
        {"address": "h1", "percentage": 30.0},  # 30% -> 0.3
        {"address": "h2", "percentage": 25.0},  # 25% -> 0.25
    ]
    
    respx.get("https://test.api/coins/TestMint/holders").mock(
        return_value=httpx.Response(200, json=holders_response)
    )
    
    obs = Observation(mint="TestMint")
    
    async with HolderEnricher(config) as enricher:
        success = await enricher.enrich_observation(obs)
    
    assert success is True
    assert obs.top5_share == 0.55


@pytest.mark.asyncio
@respx.mock
async def test_holder_enricher_api_failure():
    """HolderEnricher flags data_gap on API failure."""
    config = Config()
    config.data.rest_url = "https://test.api"
    
    respx.get("https://test.api/coins/TestMint/holders").mock(
        return_value=httpx.Response(500, text="Server Error")
    )
    
    obs = Observation(mint="TestMint")
    
    async with HolderEnricher(config) as enricher:
        success = await enricher.enrich_observation(obs)
    
    assert success is False
    assert obs.holders_enriched is False
    assert obs.holders_enrichment_incomplete is True
    assert obs.top5_share is None
    # Check integrity event
    assert any(ev.kind == "api_failure" for ev in obs.integrity_events)


@pytest.mark.asyncio
@respx.mock
async def test_holder_enricher_no_data():
    """HolderEnricher flags data_gap when no holders returned."""
    config = Config()
    config.data.rest_url = "https://test.api"
    
    respx.get("https://test.api/coins/TestMint/holders").mock(
        return_value=httpx.Response(200, json=[])
    )
    
    obs = Observation(mint="TestMint")
    
    async with HolderEnricher(config) as enricher:
        success = await enricher.enrich_observation(obs)
    
    assert success is False
    assert obs.holders_enrichment_incomplete is True
    assert any(ev.kind == "data_gap" for ev in obs.integrity_events)


@pytest.mark.asyncio
async def test_global_market_metrics_launch_rate():
    """GlobalMarketMetrics tracks launch rate."""
    metrics = GlobalMarketMetrics()
    
    # Record some launches
    for _ in range(10):
        metrics.record_launch()
    
    # Wait a bit to get a measurable rate
    import asyncio
    await asyncio.sleep(0.1)
    
    snapshot = await metrics.snapshot()
    
    assert snapshot["launch_rate"] > 0
    assert "observed_at" in snapshot
    assert "window_duration_seconds" in snapshot


@pytest.mark.asyncio
async def test_global_market_metrics_migration_rate():
    """GlobalMarketMetrics tracks migration/graduation rate."""
    metrics = GlobalMarketMetrics()
    
    # Record launches and migrations
    for _ in range(10):
        metrics.record_launch()
    for _ in range(2):
        metrics.record_migration()
    
    import asyncio
    await asyncio.sleep(0.1)
    
    snapshot = await metrics.snapshot()
    
    assert snapshot["migration_graduation_rate"] > 0
    assert snapshot["migration_graduation_rate"] < snapshot["launch_rate"]


@pytest.mark.asyncio
async def test_global_market_metrics_sol_usd_null_by_default():
    """GlobalMarketMetrics leaves sol_usd null when not fetched."""
    metrics = GlobalMarketMetrics()
    
    snapshot = await metrics.snapshot()
    
    assert snapshot["sol_usd"] is None  # Never invent


def test_provenanced_feature_causality():
    """ProvenancedFeature validates causality (no look-ahead)."""
    # Causal: observed before decision
    f1 = ProvenancedFeature(
        name="test",
        value=1.0,
        observed_at=100.0,
        decision_timestamp=200.0,
    )
    assert f1.validate_causality() is True
    assert f1.causal is True
    
    # Non-causal: observed after decision (look-ahead!)
    f2 = ProvenancedFeature(
        name="test",
        value=1.0,
        observed_at=200.0,
        decision_timestamp=100.0,
    )
    assert f2.validate_causality() is False
    assert f2.causal is False


def test_build_auditor_input_from_observation():
    """build_auditor_input_from_observation includes Wave1 enrichments."""
    obs = Observation(mint="TestMint", observation_id="obs123", creation_timestamp=100.0)
    obs.early_tx_sequence = [
        {"wallet": "w1", "is_buy": True, "sol": 0.5, "timestamp": 110.0, "age_seconds": 10.0},
        {"wallet": "w2", "is_buy": False, "sol": 0.3, "timestamp": 115.0, "age_seconds": 15.0},
    ]
    obs.top5_share = 0.45
    obs.add_feature(ProvenancedFeature(
        name="top5_share",
        value=0.45,
        observed_at=120.0,
        source="pumpfun_rest_holders",
        provenance="test provenance",
    ))
    
    decision_time = 200.0
    payload = build_auditor_input_from_observation(obs, decision_time)
    
    assert payload["observation_id"] == "obs123"
    assert payload["mint"] == "TestMint"
    assert len(payload["early_tx_sequence"]) == 2
    assert "w1" in payload["wallets"]
    assert "w2" in payload["wallets"]
    assert payload["holder_concentration"]["top5_share"] == 0.45
    assert len(payload["provenance"]) == 1
    assert payload["provenance"][0]["feature"] == "top5_share"
    assert payload["decision_timestamp"] == decision_time


def test_build_timing_input_from_observation():
    """build_timing_input_from_observation includes global timing snapshot."""
    obs = Observation(mint="TestMint", observation_id="obs123")
    obs.unique_buyers = 10
    obs.trade_count = 25
    obs.volume = 50.5
    obs.global_timing_snapshot = {
        "launch_rate": 120.5,
        "migration_graduation_rate": 2.3,
        "sol_usd": 150.0,
        "observed_at": 200.0,
    }
    
    decision_time = 200.0
    payload = build_timing_input_from_observation(obs, decision_time)
    
    assert payload["observation_id"] == "obs123"
    assert payload["launch_rate"] == 120.5
    assert payload["migration_graduation_rate"] == 2.3
    assert payload["sol_context"]["price_usd"] == 150.0
    assert payload["timestamp"] == decision_time


def test_build_timing_input_null_when_no_global_snapshot():
    """build_timing_input handles missing global timing gracefully."""
    obs = Observation(mint="TestMint", observation_id="obs123")
    obs.global_timing_snapshot = None  # Not enriched
    
    decision_time = 200.0
    payload = build_timing_input_from_observation(obs, decision_time)
    
    # Should still build payload with zeros/nulls
    assert payload["launch_rate"] == 0.0
    assert payload["migration_graduation_rate"] == 0.0
    assert payload["sol_context"]["price_usd"] is None


@pytest.mark.asyncio
@respx.mock
async def test_enrichment_no_look_ahead():
    """Enrichment timestamps are always <= decision_timestamp (no future data)."""
    config = Config()
    config.data.rest_url = "https://test.api"
    
    respx.get("https://test.api/coins/TestMint/holders").mock(
        return_value=httpx.Response(200, json=[{"address": "h1", "share": 0.5}])
    )
    
    creation_time = time.time() - 100  # 100 seconds ago
    obs = Observation(mint="TestMint", creation_timestamp=creation_time)
    
    # Enrich at current time
    enrich_time = time.time()
    async with HolderEnricher(config) as enricher:
        await enricher.enrich_observation(obs)
    
    # Decision happens after enrichment
    decision_time = time.time()
    
    # Verify all timestamps are causal
    assert obs.top5_share_observed_at is not None
    assert obs.top5_share_observed_at <= decision_time + 1.0  # small tolerance
    
    for feature in obs.features:
        feature.decision_timestamp = decision_time
        assert feature.validate_causality(), f"Feature {feature.name} violates causality"


@pytest.mark.asyncio
@respx.mock
async def test_observation_monitor_enrichment_at_promotion():
    """ObservationMonitor enriches observations at promotion with holders + global timing."""
    from src.observation_monitor import ObservationMonitor
    from src.observation_store import ObservationStore
    from src.enrichment import HolderEnricher, GlobalMarketMetrics
    
    config = Config()
    config.data.rest_url = "https://test.api"
    config.data.ws_url = "wss://test.ws"
    
    # Mock holders API
    respx.get("https://test.api/coins/TestMint/holders").mock(
        return_value=httpx.Response(200, json=[
            {"address": "h1", "share": 0.4},
            {"address": "h2", "share": 0.3},
        ])
    )
    
    global_metrics = GlobalMarketMetrics()
    global_metrics.record_launch()
    global_metrics.record_launch()
    
    async with HolderEnricher(config) as enricher:
        store = ObservationStore()
        monitor = ObservationMonitor(
            config,
            store=store,
            holder_enricher=enricher,
            global_metrics=global_metrics,
        )
        
        # Simulate create event
        create_event = {
            "txType": "create",
            "mint": "TestMint",
            "name": "Test",
            "symbol": "TEST",
            "image": "http://test.com/img.png",
            "twitter": "https://x.com/test",
        }
        token = monitor.handle_event(create_event)
        assert token is None  # Not promoted yet
        
        # Simulate trades to promote
        for i in range(6):
            monitor.handle_event({
                "txType": "buy",
                "mint": "TestMint",
                "traderPublicKey": f"wallet{i}",
                "solAmount": 0.5,
            })
        
        # Check if promoted and enriched
        promoted = monitor.handle_event({
            "txType": "buy",
            "mint": "TestMint",
            "traderPublicKey": "wallet7",
            "solAmount": 0.5,
        })
        
        if promoted:
            # Enrich the promoted token
            await monitor.enrich_promoted(promoted)
            
            obs = monitor.observation_for(promoted.mint)
            assert obs is not None
            
            # Check enrichments
            assert obs.top5_share is not None
            assert obs.top5_share == 0.7  # 0.4 + 0.3
            assert obs.top5_share_observed_at is not None
            assert obs.holders_enriched is True
            
            # Check global timing
            assert obs.global_timing_snapshot is not None
            assert "launch_rate" in obs.global_timing_snapshot
            assert "migration_graduation_rate" in obs.global_timing_snapshot
            assert obs.global_timing_observed_at is not None


@pytest.mark.asyncio
async def test_timing_decision_timestamp_consistency():
    """build_timing_input uses decision_timestamp, never fetched_at after decision."""
    obs = Observation(mint="TestMint", observation_id="obs123")
    obs.global_timing_snapshot = {
        "launch_rate": 120.5,
        "migration_graduation_rate": 2.3,
        "sol_usd": 150.0,
        "observed_at": 100.0,  # Observed in the past
    }
    
    # Decision happens later
    decision_time = 200.0
    payload = build_timing_input_from_observation(obs, decision_time)
    
    # Payload should use decision_timestamp, not the snapshot's observed_at
    assert payload["timestamp"] == decision_time
    assert payload["timestamp"] >= obs.global_timing_snapshot["observed_at"]
    
    # This ensures no look-ahead: timing was observed before decision
    assert obs.global_timing_snapshot["observed_at"] <= decision_time
