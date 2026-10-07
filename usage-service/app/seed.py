"""
usage-service – seed reference data (models table)
Idempotent: INSERT … ON CONFLICT DO NOTHING
"""
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Model

SEED_MODELS = [
    {"name": "qwen3.5:4b",   "provider": "Ollama/local",  "is_local": True},
    {"name": "llama3.1:8b",  "provider": "Ollama/local",  "is_local": True},
    {"name": "gpt-4o-mini",  "provider": "OpenAI",        "is_local": False},
    {"name": "claude-haiku", "provider": "Anthropic",     "is_local": False},
    {"name": "gemini-flash", "provider": "Google",        "is_local": False},
]


async def seed_models(session: AsyncSession) -> None:
    stmt = pg_insert(Model).values(SEED_MODELS)
    stmt = stmt.on_conflict_do_nothing(index_elements=["name"])
    await session.execute(stmt)
    await session.commit()
