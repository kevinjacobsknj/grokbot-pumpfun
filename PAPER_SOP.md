# Paper research SOP

1. **Never** set `GROKBOT_MODE=live` or paste wallet keys. Live is refused.
2. Use `config.paper.yaml` (or `mode: paper` / `dry-run`). Confirm `solana.jito.enabled: false`.
3. Preflight: `grokbot doctor --config config.paper.yaml --offline`
4. Observation collection: `grokbot paper-monitor --data-dir data`
5. Inspect `data/observations.jsonl`, `data/rejects.jsonl`, `data/integrity.jsonl`
6. Freeze experiment config before OOS evaluation (`src.research.freeze.freeze_config`)
7. Run baselines: `grokbot evaluate-baselines --data-dir data`
8. Report comparative metrics only; require prospective OOS lift before claiming agent value
9. If a quote cannot be obtained → status UNTRADEABLE (never invent fills)

## Prohibited

- Implementing LiveExecutor / signing / Jito bundles on this branch
- Deleting losing observations
- Using features observed after `decision_timestamp`
- Changing frozen experiment configs because results look bad
