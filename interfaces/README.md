# Agent interfaces (paper research)

Structured JSON contracts for coordinating research agents.
No external bots are assumed running — these are schemas + examples + validators.

| Agent | Purpose |
|-------|---------|
| AUDITOR | Early tx / wallets / concentration / coordination → structured JSON only |
| NARRATIVE | Metadata / socials / evidence vs inference |
| TIMING | Market activity / launch & migration rates / SOL context |
| CHECKER | FALSIFY complete evidence packages (not confirm) |
| EXECUTOR | PAPER-ONLY candidates after checker |

Load/validate: `from src.interfaces import load_message, validate_message, write_message`
