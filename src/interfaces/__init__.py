"""Structured JSON interfaces for research agent coordination.

These schemas prepare inbox/outbox contracts for other Grok bots.
No live bots are assumed present — schemas + validators only.
"""

from .loaders import load_message, write_message
from .validators import validate_message

__all__ = ["load_message", "write_message", "validate_message"]
