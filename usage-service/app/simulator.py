"""
usage-service – model call simulator and live Ollama client
"""
import asyncio
import hashlib
import logging
import os
import time
from typing import Tuple

import httpx

logger = logging.getLogger(__name__)

MODEL_MODE = os.environ.get("MODEL_MODE", "mock")
MOCK_LATENCY_SCALE = float(os.environ.get("MOCK_LATENCY_SCALE", "1.0"))
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://host.docker.internal:11434")

# Per-model base latency in ms (mock mode)
MODEL_BASE_LATENCY_MS = {
    "qwen3.5:4b":   40,
    "llama3.1:8b":  70,
    "gpt-4o-mini":  90,
    "claude-haiku": 80,
    "gemini-flash": 60,
}
DEFAULT_BASE_LATENCY_MS = 80

# Deterministic failure rate: ~3% (by hash, only when simulate_failures=True)
FAILURE_THRESHOLD = int(0.03 * 0xFFFFFFFF)


def _hash_int(key: str) -> int:
    """Return a deterministic unsigned 32-bit integer from a string."""
    return int(hashlib.sha256(key.encode()).hexdigest()[:8], 16)


def _mock_tokens(model: str, agent_id: int, prompt: str) -> Tuple[int, int]:
    """Deterministically derive input/output tokens from (model, agent_id, prompt)."""
    h = _hash_int(f"{model}|{agent_id}|{prompt}")
    input_tokens = max(10, len(prompt) // 4) if prompt else (20 + (h % 80))
    # output tokens: 80–400
    output_tokens = 80 + (h % 321)
    return input_tokens, output_tokens


def _mock_latency_ms(model: str, agent_id: int, prompt: str) -> int:
    """Deterministically derive latency from (model, agent_id, prompt)."""
    base = MODEL_BASE_LATENCY_MS.get(model, DEFAULT_BASE_LATENCY_MS)
    jitter_key = f"jitter|{model}|{agent_id}|{prompt}"
    jitter = _hash_int(jitter_key) % 31   # 0–30 ms jitter
    return int((base + jitter) * MOCK_LATENCY_SCALE)


def _mock_fails(model: str, agent_id: int, prompt: str) -> bool:
    """~3% failure rate, deterministic."""
    h = _hash_int(f"fail|{model}|{agent_id}|{prompt}")
    return h < FAILURE_THRESHOLD


async def call_model_mock(
    model: str,
    agent_id: int,
    prompt: str,
    simulate_failures: bool,
) -> Tuple[int, int, int, str, str]:
    """Returns (input_tokens, output_tokens, latency_ms, status, error_message)."""
    latency_ms = _mock_latency_ms(model, agent_id, prompt)
    await asyncio.sleep(latency_ms / 1000.0)

    if simulate_failures and _mock_fails(model, agent_id, prompt):
        return 0, 0, latency_ms, "failed", "Simulated model failure"

    inp, out = _mock_tokens(model, agent_id, prompt)
    return inp, out, latency_ms, "success", ""


async def call_model_ollama(
    client: httpx.AsyncClient,
    model: str,
    agent_id: int,
    prompt: str,
    simulate_failures: bool,
) -> Tuple[int, int, int, str, str]:
    """Call real Ollama; auto-fallback to mock if unreachable."""
    try:
        t0 = time.perf_counter()
        resp = await client.post(
            f"{OLLAMA_URL}/api/generate",
            json={"model": model, "prompt": prompt or "Hello", "stream": False},
            timeout=30.0,
        )
        latency_ms = int((time.perf_counter() - t0) * 1000)
        if resp.status_code == 200:
            data = resp.json()
            inp = data.get("prompt_eval_count", 0)
            out = data.get("eval_count", 0)
            return inp, out, latency_ms, "success", ""
        else:
            logger.warning("Ollama returned %d, falling back to mock", resp.status_code)
    except Exception as exc:
        logger.warning("Ollama unreachable (%s), falling back to mock", exc)

    return await call_model_mock(model, agent_id, prompt, simulate_failures)


async def call_model(
    client: httpx.AsyncClient,
    model: str,
    agent_id: int,
    prompt: str,
    simulate_failures: bool,
    force_ollama: bool = False,
) -> Tuple[int, int, int, str, str]:
    mode = "ollama" if force_ollama else MODEL_MODE
    if mode == "ollama":
        return await call_model_ollama(client, model, agent_id, prompt, simulate_failures)
    return await call_model_mock(model, agent_id, prompt, simulate_failures)
