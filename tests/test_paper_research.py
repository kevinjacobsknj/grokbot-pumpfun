"""Paper-research: PaperExecutor UNTRADEABLE, observation_id, rejects, provenance, freeze, baselines."""

from __future__ import annotations

import time

import httpx
import pytest

from src.executor import PAPER_TX, PaperExecutor, build_executor
from src.interfaces.loaders import example_payloads, load_message, write_message
from src.interfaces.validators import InterfaceValidationError, validate_message
from src.models import Config, ConfigError, Position, Token
from src.observation import (
    OBSERVATION_WINDOWS,
    Observation,
    ProvenancedFeature,
    new_observation_id,
)
from src.observation_monitor import ObservationMonitor
from src.observation_store import ObservationStore
from src.research.baselines import BASELINE_ARMS, run_baseline_arm
from src.research.evaluate import evaluate_arms
from src.research.freeze import ExperimentConfig, freeze_config, verify_frozen
from src.research.partition import Partition, partition_observations


def config(**market) -> Config:
    cfg = Config()
    for k, v in market.items():
        setattr(cfg.market, k, v)
    return cfg


def client(payload: dict | None) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload if payload is not None else {})

    return httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(handler))


LIVE_CURVE = {
    "virtual_sol_reserves": 45_000_000_000,
    "virtual_token_reserves": 715_333_460_666_667,
}


# --- PaperExecutor --------------------------------------------------------


async def test_paper_untradeable_no_invented_fill():
    ex = PaperExecutor(config(), client({}))
    result = await ex.buy(
        Token(mint="M1", market_cap_sol=0.0),
        0.2,
        signal_timestamp=1.0,
        decision_timestamp=2.0,
        observation_id="obs-1",
    )
    assert result.status == "UNTRADEABLE"
    assert not result.ok
    assert ex.last_attempt is not None
    assert ex.last_attempt.observation_id == "obs-1"
    assert ex.last_attempt.signal_timestamp == 1.0
    assert ex.last_attempt.decision_timestamp == 2.0
    assert ex.last_attempt.simulated_fill_price == 0.0


async def test_paper_filled_records_slippage_and_fees():
    ex = PaperExecutor(config(), client(LIVE_CURVE))
    result = await ex.buy(Token(mint="M1", market_cap_sol=60.0), 0.3, observation_id="obs-2")
    assert result.ok and result.status == "FILLED"
    assert result.tx_hash == PAPER_TX
    att = ex.last_attempt
    assert att is not None
    assert att.fees > 0
    assert att.estimated_slippage != 0 or att.simulated_fill_price > 0
    assert att.route_quote_info.get("venue") == "pumpfun_bonding_curve"


def test_build_executor_refuses_live_hard():
    cfg = config()
    cfg.mode = "live"
    with pytest.raises(ConfigError):
        build_executor(cfg)


# --- Observation universe -------------------------------------------------


def test_observation_id_assignment(tmp_path):
    store = ObservationStore(tmp_path)
    mon = ObservationMonitor(Config(), store=store)
    create = {
        "txType": "create",
        "mint": "MintObs1",
        "name": "Cat",
        "symbol": "CAT",
        "image": "http://img",
        "traderPublicKey": "Creator1",
        "timestamp": time.time() * 1000,
        "vSolInBondingCurve": 30.0,
        "marketCapSol": 30.0,
    }
    mon.handle_event(create)
    obs = mon.observation_for("MintObs1")
    assert obs is not None
    assert obs.observation_id
    assert store.count() >= 1
    loaded = store.get(obs.observation_id)
    assert loaded is not None
    assert loaded.mint == "MintObs1"


def test_reject_is_stored_not_deleted(tmp_path):
    store = ObservationStore(tmp_path)
    mon = ObservationMonitor(Config(), store=store)
    # require metadata — create without image/name -> reject on promote attempt
    cfg = Config()
    cfg.filter.require_metadata = True
    cfg.filter.min_age_seconds = 0
    cfg.filter.min_unique_buyers = 0
    mon = ObservationMonitor(cfg, store=store)
    create = {
        "txType": "create",
        "mint": "MintBad",
        "traderPublicKey": "C",
        "timestamp": time.time() * 1000,
        "vSolInBondingCurve": 30.0,
    }
    mon.handle_event(create)
    # force promote path via sweep after age — still no_metadata
    mon.sweep(now=time.time() + 10)
    # reject path: inject buy then promote check
    mon.handle_event({"txType": "buy", "mint": "MintBad", "traderPublicKey": "W1"})
    # Manually trigger skip via inner promote with no metadata
    from src.monitor import parse_create_event

    token = parse_create_event(create)
    assert token is not None
    mon._on_skip_bridge(token, "no_metadata")
    rejects = (tmp_path / "rejects.jsonl").read_text()
    assert "MintBad" in rejects
    assert "no_metadata" in rejects


def test_windows_no_event_not_fabricated():
    obs = Observation(mint="M", creation_timestamp=time.time())
    obs.ensure_windows()
    assert len(obs.windows) == len(OBSERVATION_WINDOWS)
    assert all(w.status == "no_event" for w in obs.windows.values())
    obs.record_event_at(15.0, market_cap=1.0, unique_buyers=2)
    # 10s window observed; 20s still no_event
    assert obs.windows["10s"].status == "observed"
    assert obs.windows["20s"].status == "no_event"
    assert obs.windows["20s"].market_cap is None


def test_provenance_causality():
    feat = ProvenancedFeature(
        name="unique_buyers",
        value=5,
        observed_at=10.0,
        decision_timestamp=5.0,  # feature AFTER decision -> leakage
        source="ws",
        provenance="trade_stream",
    )
    assert not feat.validate_causality()
    assert feat.causal is False
    ok = ProvenancedFeature(
        name="unique_buyers",
        value=5,
        observed_at=5.0,
        decision_timestamp=10.0,
        source="ws",
    )
    assert ok.validate_causality()


def test_missing_trade_stream_flagged(tmp_path):
    store = ObservationStore(tmp_path)
    mon = ObservationMonitor(Config(), store=store)
    create = {
        "txType": "create",
        "mint": "MintX",
        "name": "X",
        "symbol": "X",
        "image": "i",
        "timestamp": time.time() * 1000,
    }
    mon.handle_event(create)
    mon.mark_enrichment_without_stream("MintX")
    obs = mon.observation_for("MintX")
    assert obs is not None
    assert obs.missing_trade_stream
    assert obs.incomplete
    integrity = (tmp_path / "integrity.jsonl").read_text()
    assert "missing_trade_stream" in integrity


# --- Freeze / partition ---------------------------------------------------


def test_freeze_hash_stable(tmp_path):
    cfg = ExperimentConfig(
        experiment_id="t1",
        features=["a"],
        thresholds={"x": 1.0},
        entry_logic="e",
        exit_logic="x",
    )
    path = tmp_path / "config.yaml"
    frozen = freeze_config(cfg, path)
    ok, digest = verify_frozen(path)
    assert ok
    assert digest == frozen.config_sha256
    # Tamper
    text = path.read_text()
    path.write_text(text.replace("t1", "t1-tampered"))
    ok2, msg = verify_frozen(path)
    assert not ok2


def test_partition_time_and_hash():
    obs = [
        Observation(observation_id=f"id-{i}", creation_timestamp=float(i))
        for i in range(10)
    ]
    parts = partition_observations(obs, method="time")
    assert sum(len(v) for v in parts.values()) == 10
    assert Partition.LOCKED_OUT_OF_SAMPLE in parts
    parts_h = partition_observations(obs, method="hash")
    assert sum(len(v) for v in parts_h.values()) == 10


# --- Baselines smoke ------------------------------------------------------


def test_baselines_smoke():
    obs = []
    for i in range(20):
        o = Observation(
            observation_id=f"o{i}",
            mint=f"M{i}",
            candidate_status="accepted",
            unique_buyers=i,
            bonding_curve_state={"curve_progress": 0.1},
        )
        obs.append(o)
    for arm in BASELINE_ARMS:
        kwargs = {}
        if arm == "full_system":
            kwargs = {
                "checker_approve": {o.observation_id: True for o in obs},
                "auditor_scores": {o.observation_id: 0.9 for o in obs},
                "narrative_scores": {o.observation_id: 0.9 for o in obs},
                "timing_scores": {o.observation_id: 0.9 for o in obs},
            }
        selected = run_baseline_arm(arm, obs, **kwargs)
        assert isinstance(selected, list)
    results = evaluate_arms(
        obs,
        paper_pnls={o.observation_id: 0.01 for o in obs},
        arm_kwargs={
            "full_system": {
                "checker_approve": {o.observation_id: True for o in obs},
                "auditor_scores": {o.observation_id: 0.9 for o in obs},
                "narrative_scores": {o.observation_id: 0.9 for o in obs},
                "timing_scores": {o.observation_id: 0.9 for o in obs},
            }
        },
    )
    assert set(results) == set(BASELINE_ARMS)
    assert results["random_eligible_launch"]["n_selected"] >= 0


# --- Interfaces -----------------------------------------------------------


def test_interface_examples_validate(tmp_path):
    payloads = example_payloads()
    validate_message("auditor", "in", payloads["auditor_in"])
    validate_message("checker", "out", payloads["checker_out"])
    validate_message("executor", "in", payloads["executor_in"])
    path = write_message(
        "auditor", "in", payloads["auditor_in"], root=tmp_path / "inbox"
    )
    loaded = load_message("auditor", "in", path)
    assert loaded.observation_id == "obs-example-1"
    with pytest.raises(InterfaceValidationError):
        validate_message("auditor", "in", {"mint": "only"})


def test_new_observation_id_unique():
    ids = {new_observation_id() for _ in range(50)}
    assert len(ids) == 50
