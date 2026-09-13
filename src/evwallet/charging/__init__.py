"""Charging domain — WebSocket hub, telemetry writer, REST endpoints.

Public surface:
    :func:`build_router` — returns the FastAPI router for ``/api/v1/charging``.

Agent C owns this package.
"""

from __future__ import annotations

from fastapi import APIRouter

from .router import build_router


def build_charging_router() -> APIRouter:
    """Return the FastAPI APIRouter for charging sessions."""
    return build_router()


__all__ = ["build_charging_router", "build_router"]
