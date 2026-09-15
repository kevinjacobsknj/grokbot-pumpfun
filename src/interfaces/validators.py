"""Validate agent interface payloads against schemas."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ValidationError

from .schemas import SCHEMA_BY_AGENT


class InterfaceValidationError(ValueError):
    pass


def validate_message(agent: str, direction: str, payload: dict[str, Any] | BaseModel) -> BaseModel:
    if agent not in SCHEMA_BY_AGENT:
        raise InterfaceValidationError(f"unknown agent: {agent}")
    if direction not in ("in", "out"):
        raise InterfaceValidationError(f"direction must be in|out, got {direction}")
    model_cls = SCHEMA_BY_AGENT[agent][direction]
    if isinstance(payload, model_cls):
        return payload
    if isinstance(payload, BaseModel):
        payload = payload.model_dump()
    try:
        return model_cls.model_validate(payload)
    except ValidationError as exc:
        raise InterfaceValidationError(str(exc)) from exc
