import asyncio
import base64
import json
import os
from concurrent.futures import ThreadPoolExecutor
from functools import partial

import boto3
from botocore.config import Config as BotocoreConfig

# Dedicated thread pool for Bedrock — the default executor has only cpu_count+4 threads
# which causes queuing when many charts run in parallel. 24 workers allows up to 24
# concurrent Bedrock calls without blocking the event loop.
_BEDROCK_EXECUTOR = ThreadPoolExecutor(max_workers=24, thread_name_prefix="bedrock")

AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_SESSION_TOKEN = os.getenv("AWS_SESSION_TOKEN")

# Configurable model IDs — set via env vars, fall back to Claude 3 defaults
BEDROCK_SONNET_MODEL = os.getenv("BEDROCK_SONNET_MODEL_ID", "anthropic.claude-3-sonnet-20240229-v1:0")
BEDROCK_HAIKU_MODEL = os.getenv("BEDROCK_HAIKU_MODEL_ID", "anthropic.claude-3-haiku-20240307-v1:0")
BEDROCK_VISION_MODEL = os.getenv("BEDROCK_VISION_MODEL_ID", "anthropic.claude-3-sonnet-20240229-v1:0")

BEDROCK_MAX_TOKENS = int(os.getenv("BEDROCK_MAX_TOKENS", "2048"))
BEDROCK_TEMPERATURE = float(os.getenv("BEDROCK_TEMPERATURE", "0.0"))


_BEDROCK_CONFIG = BotocoreConfig(
    connect_timeout=10,   # fail fast if can't reach AWS
    read_timeout=120,     # 2 min max for vision/LLM response
    retries={"max_attempts": 1},
)


def get_bedrock_client():
    kwargs = {
        "service_name": "bedrock-runtime",
        "region_name": AWS_REGION,
        "config": _BEDROCK_CONFIG,
    }
    if AWS_ACCESS_KEY_ID:
        kwargs["aws_access_key_id"] = AWS_ACCESS_KEY_ID
        kwargs["aws_secret_access_key"] = AWS_SECRET_ACCESS_KEY
    if AWS_SESSION_TOKEN:
        kwargs["aws_session_token"] = AWS_SESSION_TOKEN
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
        return result["content"][0]["text"]

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_BEDROCK_EXECUTOR, _invoke)


async def bedrock_invoke_with_history(
    model_id: str,
    system_prompt: str,
    messages: list[dict],
    max_tokens: int = 4096,
    temperature: float = 0.3,
) -> str:
    def _invoke():
        client = get_bedrock_client()
        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system_prompt,
            "messages": messages,
        }
        response = client.invoke_model(
            modelId=model_id,
            body=json.dumps(body),
            contentType="application/json",
            accept="application/json",
        )
        result = json.loads(response["body"].read())
        return result["content"][0]["text"]

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_BEDROCK_EXECUTOR, _invoke)


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
        return result["content"][0]["text"]

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_BEDROCK_EXECUTOR, _invoke)
