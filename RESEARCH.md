# Paper-only research redesign

**Objective (falsifiable):** whether information observable in the first minutes of a Pump.fun launch predicts subsequent outcomes **after realistic execution costs**.

This is **not** "find profitable memecoins."

## Safety (forever)

- No wallet connect, private keys, signing, constructing/submitting txs, buying/selling real tokens, sending SOL, or Jito execution.
- `LiveExecutor` permanently disabled; `build_executor` raises `ConfigError` on `mode: live`.
- Default `jito.enabled = false`. Research configs use `mode: paper` or `dry-run`.
- `solders` / `solana` are optional `[live]` extras and unused at runtime.

## Architecture additions

| Module | Role |
|--------|------|
| `src/executor.py` (`PaperExecutor`) | Simulated fills from curve quotes; UNTRADEABLE if no quote |
| `src/observation.py` | Observation model, windows, provenance features |
| `src/observation_store.py` | JSONL + SQLite under `data/` |
| `src/observation_monitor.py` | WS monitor recording ALL launches |
| `src/interfaces/` | Agent I/O schemas + validators |
| `src/research/` | Partitions, freeze hashes, baselines, eval |
| `interfaces/` | Example JSON payloads per agent |
| `experiments/` | Frozen experiment configs |

## Data integrity

- Store accepted, rejected, dead, migrated, missing, API/WS failures, quote failures, paper failures.
- Never delete losing observations.
- Features carry `observed_at` + provenance; must be causal vs `decision_timestamp`.
- REST enrichment must not silently replace missing trade-stream data (`missing_trade_stream`).

## Experimental partitions

See `src/research/partition.py`: DEVELOPMENT 60% / VALIDATION 20% / LOCKED_OUT_OF_SAMPLE 20% by creation time (or hash of `observation_id`).

Frozen configs: `experiments/<id>/config.yaml` + `config.sha256`. Do not edit after freeze to chase results.

## Baselines

`random_eligible_launch`, `basic_deterministic_filter_only`, `monitor_quantitative_signals_only`, `monitor_plus_auditor`, `monitor_plus_auditor_plus_narrative`, `full_system`.

Compare on the same observation set under paper fills. Do not claim agent value without prospective OOS improvement.

## How to run

```bash
source /workspace/grokbot-venv/bin/activate
cd /workspace/grokbot-pumpfun

# Observation universe only (no Grok required if key missing — warn and continue)
cp config.paper.yaml config.yaml   # set GROKBOT_GROK_API_KEY only if running full pipeline
grokbot paper-monitor --config config.paper.yaml --data-dir data --max-launches 10

# Full paper pipeline (needs Grok key)
GROKBOT_MODE=paper grokbot run --config config.paper.yaml

# Baseline comparison on stored observations
grokbot evaluate-baselines --data-dir data --json

# Doctor offline
grokbot doctor --config config.paper.yaml --offline
```

See `PAPER_SOP.md` for the standard operating procedure.
