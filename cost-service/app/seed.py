"""
cost-service – seed reference data (pricing table)
Called at startup, idempotent (INSERT … ON CONFLICT DO NOTHING).
"""
from decimal import Decimal

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Pricing

# Illustrative prices – NOT real production prices
SEED_PRICING = [
    {"model": "qwen3.5:4b",       "input_price_per_1k": Decimal("0.000000"), "output_price_per_1k": Decimal("0.000000"), "currency": "USD"},
    {"model": "llama3.1:8b",      "input_price_per_1k": Decimal("0.000000"), "output_price_per_1k": Decimal("0.000000"), "currency": "USD"},
    {"model": "gpt-4o-mini",      "input_price_per_1k": Decimal("0.000150"), "output_price_per_1k": Decimal("0.000600"), "currency": "USD"},
    {"model": "claude-haiku",     "input_price_per_1k": Decimal("0.000250"), "output_price_per_1k": Decimal("0.001250"), "currency": "USD"},
    {"model": "gemini-flash",     "input_price_per_1k": Decimal("0.000075"), "output_price_per_1k": Decimal("0.000300"), "currency": "USD"},
]


async def seed_pricing(session: AsyncSession) -> None:
    stmt = pg_insert(Pricing).values(SEED_PRICING)
    stmt = stmt.on_conflict_do_nothing(index_elements=["model"])
    await session.execute(stmt)
    await session.commit()
