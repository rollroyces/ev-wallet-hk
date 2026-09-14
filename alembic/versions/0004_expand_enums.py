"""expand poles connector + speed_tier enum to include EPD-source values

The EPD HK charger XLSX uses connector codes not in the original canonical
enum:
  - bs1363 (UK 3-pin plug, common in older HK properties)
  - type1 (J1772, used by some early Nissan/older BEVs)
  - gbt_ac (GB/T 20234.2 AC, the China-mandated connector; some HK sites)
  - tesla_nacs (Tesla NACS connector, AC variant)
  - tesla_wc (Tesla Wall Connector)

These are real connectors present at HK chargers. Expanding the enum
keeps the EPD ingestion path working end-to-end.

Revision ID: 0004_expand_enums
Revises: 0003_stripe_webhook_events
Create Date: 2026-09-14 14:55:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_expand_enums"
down_revision = "0003_stripe_webhook_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Postgres CHECK constraints are not directly ALTER-able. Drop + recreate.
    op.drop_constraint("poles_connector_enum", "poles", type_="check")
    op.drop_constraint("poles_speed_tier_enum", "poles", type_="check")

    op.create_check_constraint(
        "poles_connector_enum",
        "poles",
        "connector IN ('ccs2','type2','chademo','tesla','bs1363','type1','gbt_ac','tesla_nacs','tesla_wc')",
    )
    op.create_check_constraint(
        "poles_speed_tier_enum",
        "poles",
        "speed_tier IN ('ac_slow','ac_fast','dc_fast','dc_ultra','unknown')",
    )


def downgrade() -> None:
    op.drop_constraint("poles_connector_enum", "poles", type_="check")
    op.drop_constraint("poles_speed_tier_enum", "poles", type_="check")

    op.create_check_constraint(
        "poles_connector_enum",
        "poles",
        "connector IN ('ccs2','type2','chademo','tesla')",
    )
    op.create_check_constraint(
        "poles_speed_tier_enum",
        "poles",
        "speed_tier IN ('ac_slow','ac_fast','dc_fast','dc_ultra')",
    )