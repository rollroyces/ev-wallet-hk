"""Pydantic request/response models for the /internal/* endpoints.

Lives in its own module (not router.py) to avoid circular imports with
``providers.py`` (which uses these types in its adapter return values).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field


class PoleUpsertIn(BaseModel):
    external_id: str
    connector: str
    speed_tier: str
    max_kw: Decimal
    qr_code: str | None = None  # generated server-side if missing
    status: str = "available"
    status_updated_at: datetime | None = None


class StationUpsertIn(BaseModel):
    provider_code: str
    external_id: str
    name: str
    address: str
    district: str | None = None
    latitude: Decimal
    longitude: Decimal
    parking_fee_hkd: Decimal = Decimal("0")
    amenities: list[str] = Field(default_factory=list)
    raw_payload: dict[str, Any] = Field(default_factory=dict)
    poles: list[PoleUpsertIn] = Field(default_factory=list)


class StationUpsertOut(BaseModel):
    station_id: uuid.UUID
    external_id: str
    poles_created: int
    poles_updated: int


class RateUpsertIn(BaseModel):
    pole_external_id: str
    station_external_id: str
    provider_code: str
    day_of_week: int = Field(ge=0, le=6)
    hour_start_local: int = Field(ge=0, le=23)
    price_per_kwh_hkd: Decimal
    parking_fee_hkd: Decimal = Decimal("0")
    valid_from: datetime
    valid_to: datetime | None = None


class BulkRateUpsertIn(BaseModel):
    rates: list[RateUpsertIn]


class BulkRateUpsertOut(BaseModel):
    rates_upserted: int


class AdminStationRow(BaseModel):
    id: uuid.UUID
    external_id: str
    provider_code: str
    name: str
    address: str
    district: str | None
    last_synced_at: datetime
    pole_count: int

    class Config:
        from_attributes = True


class AdminTransactionRow(BaseModel):
    id: uuid.UUID
    wallet_id: uuid.UUID
    user_email: str | None
    kind: str
    status: str
    amount: Decimal
    currency: str
    posted_at: datetime

    class Config:
        from_attributes = True


class PushTokenRegisterIn(BaseModel):
    token: str = Field(min_length=1, max_length=512)
    platform: str = Field(pattern="^(ios|android)$")
    device_id: str | None = None


class PushTokenRegisterOut(BaseModel):
    id: uuid.UUID
    token: str
    platform: str
    created_at: datetime


__all__ = [
    "AdminStationRow",
    "AdminTransactionRow",
    "BulkRateUpsertIn",
    "BulkRateUpsertOut",
    "PoleUpsertIn",
    "PushTokenRegisterIn",
    "PushTokenRegisterOut",
    "RateUpsertIn",
    "StationUpsertIn",
    "StationUpsertOut",
]
