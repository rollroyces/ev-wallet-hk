"""Stations domain — geo search + rate lookup + REST endpoints.

Public surface:
    :func:`build_router` — returns the FastAPI router for ``/api/v1/stations``.

Agent C owns this package.
"""

from __future__ import annotations

from fastapi import APIRouter

from .router import build_router


def build_stations_router() -> APIRouter:
    """Return the FastAPI APIRouter for stations."""
    return build_router()


__all__ = ["build_stations_router", "build_router"]
