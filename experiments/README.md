# Experiments

Each experiment lives under `experiments/<id>/` with:
- `config.yaml` — frozen feature defs, thresholds, entry/exit, paper fills
- `config.sha256` — integrity hash
- `FROZEN` — marker file (do not edit configs to chase results)

Create via:
```python
from src.research.freeze import ExperimentConfig, freeze_config
cfg = ExperimentConfig(experiment_id="example", features=["unique_buyers"], thresholds={"min_buyers": 5})
freeze_config(cfg, "experiments/example/config.yaml")
```
