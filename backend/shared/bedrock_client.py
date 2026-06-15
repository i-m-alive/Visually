import asyncio
import base64
import contextvars
import json
import os
from concurrent.futures import ThreadPoolExecutor
from typing import NamedTuple

import boto3
from botocore.config import Config as BotocoreConfig

# Load .env from the repo root (visually/.env) so that all services pick up the
# correct model IDs and credentials regardless of which directory they start from.
# override=False means system env vars set externally still win, but .env values
# fill in anything that isn't already in the environment.
try:
    from dotenv import load_dotenv, find_dotenv
    _dotenv_path = find_dotenv(usecwd=False)
    if _dotenv_path:
        load_dotenv(_dotenv_path, override=True)
except ImportError:
    pass

# Dedicated thread pool for Bedrock — the default executor has only cpu_count+4 threads
# which causes queuing when many charts run in parallel. 24 workers allows up to 24
# concurrent Bedrock calls without blocking the event loop.
_BEDROCK_EXECUTOR = ThreadPoolExecutor(max_workers=24, thread_name_prefix="bedrock")

# Configurable model IDs — read from env so .env values always win over any
# stale system-level env vars that point to old model IDs.
BEDROCK_SONNET_MODEL = os.getenv("BEDROCK_SONNET_MODEL_ID", "us.anthropic.claude-sonnet-4-5-20250929-v1:0")
BEDROCK_HAIKU_MODEL  = os.getenv("BEDROCK_HAIKU_MODEL_ID",  "us.anthropic.claude-sonnet-4-5-20250929-v1:0")
BEDROCK_OPUS_MODEL   = os.getenv("BEDROCK_OPUS_MODEL_ID",   "us.anthropic.claude-opus-4-5-20251101-v1:0")
BEDROCK_VISION_MODEL = os.getenv("BEDROCK_VISION_MODEL_ID", "us.anthropic.claude-opus-4-5-20251101-v1:0")

BEDROCK_MAX_TOKENS = int(os.getenv("BEDROCK_MAX_TOKENS", "8192"))
BEDROCK_TEMPERATURE = float(os.getenv("BEDROCK_TEMPERATURE", "0.0"))


class _TokenUsage(NamedTuple):
    model_id: str
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


_token_bucket: contextvars.ContextVar[list | None] = contextvars.ContextVar("_token_bucket", default=None)


def start_token_tracking() -> None:
    """Call at the start of a pipeline run to begin accumulating token usage."""
    _token_bucket.set([])


def get_token_summary() -> dict:
    """Return aggregated token counts per model_id → {input_tokens, output_tokens, calls}."""
    bucket = _token_bucket.get()
    if not bucket:
        return {}
    agg: dict = {}
    for entry in bucket:
        m = entry.model_id
        if m not in agg:
            agg[m] = {
                "input_tokens": 0, "output_tokens": 0, "calls": 0,
                "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
            }
        agg[m]["input_tokens"] += entry.input_tokens
        agg[m]["output_tokens"] += entry.output_tokens
        agg[m]["cache_read_input_tokens"] += entry.cache_read_input_tokens
        agg[m]["cache_creation_input_tokens"] += entry.cache_creation_input_tokens
        agg[m]["calls"] += 1
    return agg


def _track_usage(model_id: str, result: dict) -> None:
    bucket = _token_bucket.get()
    if bucket is None:
        return
    usage = result.get("usage", {})
    bucket.append(_TokenUsage(
        model_id=model_id,
        input_tokens=usage.get("input_tokens", 0),
        output_tokens=usage.get("output_tokens", 0),
        cache_read_input_tokens=usage.get("cache_read_input_tokens", 0),
        cache_creation_input_tokens=usage.get("cache_creation_input_tokens", 0),
    ))


_BEDROCK_CONFIG = BotocoreConfig(
    connect_timeout=10,   # fail fast if can't reach AWS
    read_timeout=120,     # 2 min max for vision/LLM response
    retries={"max_attempts": 3},
)


def get_bedrock_client():
    # Reload .env on every call so rotating STS session tokens take effect
    # without restarting the backend process.
    # Explicit absolute path — find_dotenv can silently miss on Windows.
    try:
        from dotenv import load_dotenv as _ld
        import os as _os
        _env = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), '..', '..', '.env')
        if _os.path.exists(_env):
            _ld(_env, override=True)
    except ImportError:
        pass

    access_key    = os.getenv("AWS_ACCESS_KEY_ID")
    secret_key    = os.getenv("AWS_SECRET_ACCESS_KEY")
    session_token = os.getenv("AWS_SESSION_TOKEN")
    region        = os.getenv("AWS_REGION", "us-east-1")

    kwargs: dict = {
        "service_name": "bedrock-runtime",
        "region_name": region,
        "config": _BEDROCK_CONFIG,
    }
    if access_key:
        kwargs["aws_access_key_id"] = access_key
        kwargs["aws_secret_access_key"] = secret_key
    if session_token:
        kwargs["aws_session_token"] = session_token
    return boto3.client(**kwargs)


async def bedrock_invoke(
    model_id: str,
    system_prompt: str,
    user_message: str,
    max_tokens: int = 4096,
    temperature: float = 0.1,
) -> str:
    import time
    def _invoke():
        client = get_bedrock_client()
        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_message}],
        }
        t0 = time.time()
        print(f"[bedrock] → invoke_model  model={model_id}  max_tokens={max_tokens}", flush=True)
        response = client.invoke_model(
            modelId=model_id,
            body=json.dumps(body),
            contentType="application/json",
            accept="application/json",
        )
        result = json.loads(response["body"].read())
        print(f"[bedrock] ← done  {time.time()-t0:.1f}s", flush=True)
        _track_usage(model_id, result)
        return result["content"][0]["text"]

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_BEDROCK_EXECUTOR, _invoke)


async def bedrock_invoke_with_history(
    model_id: str,
    system_prompt,  # str | list[dict] — a list of content blocks enables prompt caching
    messages: list[dict],            #   via a {"cache_control": {"type": "ephemeral"}} marker
    max_tokens: int = 4096,
    temperature: float = 0.3,
) -> str:
    import time
    def _invoke():
        client = get_bedrock_client()
        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": max_tokens,
            "temperature": temperature,
            # Bedrock/Claude accepts `system` as a plain string OR a list of
            # content blocks. The list form lets us mark a cache breakpoint so the
            # stable prefix (instructions + schema) is cached for ~5 min.
            "system": system_prompt,
            "messages": messages,
        }
        t0 = time.time()
        response = client.invoke_model(
            modelId=model_id,
            body=json.dumps(body),
            contentType="application/json",
            accept="application/json",
        )
        result = json.loads(response["body"].read())
        usage = result.get("usage", {})
        print(
            f"[bedrock] ← chat done  {time.time()-t0:.1f}s  "
            f"in={usage.get('input_tokens', 0)}  out={usage.get('output_tokens', 0)}  "
            f"cache_read={usage.get('cache_read_input_tokens', 0)}  "
            f"cache_write={usage.get('cache_creation_input_tokens', 0)}",
            flush=True,
        )
        _track_usage(model_id, result)
        return result["content"][0]["text"]

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_BEDROCK_EXECUTOR, _invoke)


async def bedrock_invoke_stream(
    model_id: str,
    system_prompt,  # str | list[dict] (cache_control blocks supported)
    messages: list[dict],
    max_tokens: int = 2048,
    temperature: float = 0.3,
):
    """Async generator over a streaming Bedrock call. Bridges boto3's blocking
    event iterator to asyncio via a queue fed from a worker thread.

    Yields tuples:
      ("text", str)   — an incremental text delta
      ("usage", dict) — final token usage (incl. cache_read/cache_write)
      ("error", str)  — a fatal error; the generator then ends
    """
    import time
    loop = asyncio.get_event_loop()
    q: asyncio.Queue = asyncio.Queue()
    _DONE = object()

    def _worker():
        try:
            client = get_bedrock_client()
            body = {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": max_tokens,
                "temperature": temperature,
                "system": system_prompt,
                "messages": messages,
            }
            t0 = time.time()
            resp = client.invoke_model_with_response_stream(
                modelId=model_id,
                body=json.dumps(body),
                contentType="application/json",
                accept="application/json",
            )
            usage: dict = {}
            for event in resp.get("body", []):
                chunk = event.get("chunk")
                if not chunk:
                    continue
                data = json.loads(chunk.get("bytes").decode())
                dtype = data.get("type")
                if dtype == "content_block_delta":
                    delta = data.get("delta", {})
                    if delta.get("type") == "text_delta" and delta.get("text"):
                        loop.call_soon_threadsafe(q.put_nowait, ("text", delta["text"]))
                elif dtype == "message_start":
                    usage.update(data.get("message", {}).get("usage", {}) or {})
                elif dtype == "message_delta":
                    usage.update(data.get("usage", {}) or {})
            print(
                f"[bedrock] ← stream done  {time.time()-t0:.1f}s  "
                f"in={usage.get('input_tokens', 0)}  out={usage.get('output_tokens', 0)}  "
                f"cache_read={usage.get('cache_read_input_tokens', 0)}  "
                f"cache_write={usage.get('cache_creation_input_tokens', 0)}",
                flush=True,
            )
            loop.call_soon_threadsafe(q.put_nowait, ("usage", usage))
        except Exception as exc:  # noqa: BLE001
            loop.call_soon_threadsafe(q.put_nowait, ("error", f"{type(exc).__name__}: {exc}"))
        finally:
            loop.call_soon_threadsafe(q.put_nowait, _DONE)

    _BEDROCK_EXECUTOR.submit(_worker)
    while True:
        item = await q.get()
        if item is _DONE:
            break
        yield item


async def bedrock_invoke_with_image(
    model_id: str,
    system_prompt: str,
    user_text: str,
    image_bytes: bytes,
    image_media_type: str = "image/png",
    max_tokens: int = BEDROCK_MAX_TOKENS,
    temperature: float = 0.1,
) -> str:
    """Call a Claude multimodal model on Bedrock with a single image + text."""
    import time
    def _invoke():
        client = get_bedrock_client()
        image_b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system_prompt,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": image_media_type,
                                "data": image_b64,
                            },
                        },
                        {"type": "text", "text": user_text},
                    ],
                }
            ],
        }
        t0 = time.time()
        img_kb = len(image_bytes) // 1024
        print(f"[bedrock-vision] → invoke_model  model={model_id}  image={img_kb}KB", flush=True)
        try:
            response = client.invoke_model(
                modelId=model_id,
                body=json.dumps(body),
                contentType="application/json",
                accept="application/json",
            )
            result = json.loads(response["body"].read())
            print(f"[bedrock-vision] ← done  {time.time()-t0:.1f}s", flush=True)
            _track_usage(model_id, result)
            return result["content"][0]["text"]
        except Exception as exc:
            print(f"[bedrock-vision] ✗ FAILED after {time.time()-t0:.1f}s: {type(exc).__name__}: {exc}", flush=True)
            raise

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_BEDROCK_EXECUTOR, _invoke)


async def bedrock_invoke_with_multiple_images(
    model_id: str,
    system_prompt: str,
    user_text: str,
    images: list[dict],  # [{"bytes": bytes, "media_type": "image/png"}]
    max_tokens: int = 4096,
    temperature: float = 0.1,
) -> str:
    """Call a Claude multimodal model on Bedrock with multiple images."""
    def _invoke():
        client = get_bedrock_client()
        content = []
        for img in images:
            img_b64 = base64.standard_b64encode(img["bytes"]).decode("utf-8")
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": img.get("media_type", "image/png"),
                    "data": img_b64,
                },
            })
        content.append({"type": "text", "text": user_text})
        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system_prompt,
            "messages": [{"role": "user", "content": content}],
        }
        response = client.invoke_model(
            modelId=model_id,
            body=json.dumps(body),
            contentType="application/json",
            accept="application/json",
        )
        result = json.loads(response["body"].read())
        _track_usage(model_id, result)
        return result["content"][0]["text"]

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_BEDROCK_EXECUTOR, _invoke)
