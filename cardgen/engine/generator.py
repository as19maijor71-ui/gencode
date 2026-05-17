import json
import logging
import re

import httpx
from pydantic import BaseModel, Field, ValidationError

from cardgen.config import settings
from cardgen.templates.prompts import build_prompt

logger = logging.getLogger(__name__)


class CardResult(BaseModel):
    wb_title: str
    wb_description: str
    wb_keywords: list[str]
    wb_photo_recommendations: str

    ozon_title: str
    ozon_description: str
    ozon_keywords: list[str]
    ozon_video_script: str
    ozon_photo_recommendations: str

    characteristics: dict[str, str] = Field(default_factory=dict)
    strategy_notes: str

    raw_response: str = ""


def _extract_json(text: str) -> str:
    json_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if json_match:
        return json_match.group(1).strip()

    brace_start = text.find("{")
    if brace_start == -1:
        return text

    depth = 0
    for i, ch in enumerate(text[brace_start:], start=brace_start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[brace_start : i + 1]

    return text[brace_start:]


def _strict_json_instruction(strict: bool) -> str:
    if not strict:
        return ""
    return "\n\nТы должен вернуть ТОЛЬКО валидный JSON. Без пояснений, без markdown. Только JSON."


async def call_openrouter(prompt: str, max_tokens: int, strict_json: bool = False) -> str:
    async with httpx.AsyncClient(timeout=settings.REQUEST_TIMEOUT) as client:
        response = await client.post(
            settings.OPENROUTER_BASE_URL,
            headers={
                "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.OPENROUTER_MODEL,
                "messages": [
                    {"role": "user", "content": prompt + _strict_json_instruction(strict_json)}
                ],
                "max_tokens": max_tokens,
                "response_format": {"type": "json_object"},
            },
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]


async def call_yandexgpt(prompt: str, max_tokens: int, strict_json: bool = False) -> str:
    async with httpx.AsyncClient(timeout=settings.REQUEST_TIMEOUT) as client:
        response = await client.post(
            settings.YANDEXGPT_BASE_URL,
            headers={
                "Authorization": f"Api-Key {settings.YANDEXGPT_API_KEY}",
                "x-folder-id": settings.YANDEXGPT_FOLDER_ID,
            },
            json={
                "modelUri": f"gpt://{settings.YANDEXGPT_FOLDER_ID}/yandexgpt/latest",
                "completionOptions": {
                    "stream": False,
                    "temperature": 0.3,
                    "maxTokens": str(max_tokens),
                },
                "messages": [
                    {"role": "user", "text": prompt + _strict_json_instruction(strict_json)}
                ],
            },
        )
        response.raise_for_status()
        data = response.json()
        return data["result"]["alternatives"][0]["message"]["text"]


async def call_ai(prompt: str, strict_json: bool = False) -> str:
    primary = settings.AI_PROVIDER

    if primary == "yandexgpt":
        if not settings.YANDEXGPT_API_KEY:
            raise RuntimeError("YandexGPT selected but YANDEXGPT_API_KEY is not set")
        return await call_yandexgpt(prompt, settings.DEFAULT_MAX_TOKENS, strict_json)

    if not settings.OPENROUTER_API_KEY:
        if settings.YANDEXGPT_API_KEY:
            logger.warning("OpenRouter key not set, falling back to YandexGPT")
            return await call_yandexgpt(prompt, settings.DEFAULT_MAX_TOKENS, strict_json)
        raise RuntimeError("No AI provider configured")

    try:
        return await call_openrouter(prompt, settings.DEFAULT_MAX_TOKENS, strict_json)
    except Exception as e:
        logger.warning(f"OpenRouter failed: {e}")
        if settings.YANDEXGPT_API_KEY:
            logger.info("Falling back to YandexGPT")
            return await call_yandexgpt(prompt, settings.DEFAULT_MAX_TOKENS, strict_json)
        raise RuntimeError(f"OpenRouter failed and no YandexGPT fallback configured: {e}") from e


def parse_response(raw: str) -> CardResult | None:
    json_str = _extract_json(raw)
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError:
        return None

    try:
        return CardResult.model_validate(data | {"raw_response": raw})
    except (ValidationError, TypeError, KeyError):
        return None


async def generate_card(
    description: str,
    category_key: str,
    target_audience: str = "широкая аудитория",
    brand: str = "Без бренда",
    category_fields: list[str] | None = None,
) -> CardResult:
    prompt = build_prompt(description, category_key, target_audience, brand, category_fields)

    last_raw = ""
    for attempt in range(settings.MAX_RETRIES + 1):
        is_retry = attempt > 0
        try:
            raw = await call_ai(prompt, strict_json=is_retry)
        except Exception as e:
            logger.error(f"AI call failed: {e}")
            return CardResult(
                wb_title="",
                wb_description=f"Ошибка при вызове AI: {e}",
                wb_keywords=[],
                wb_photo_recommendations="",
                ozon_title="",
                ozon_description="",
                ozon_keywords=[],
                ozon_video_script="",
                ozon_photo_recommendations="",
                characteristics={},
                strategy_notes="",
                raw_response=str(e),
            )

        last_raw = raw
        result = parse_response(raw)
        if result is not None:
            return result

    return CardResult(
        wb_title="",
        wb_description=f"Не удалось распарсить ответ AI после {settings.MAX_RETRIES + 1} попыток.\n\nСырой ответ:\n{last_raw[:2000]}",
        wb_keywords=[],
        wb_photo_recommendations="",
        ozon_title="",
        ozon_description="",
        ozon_keywords=[],
        ozon_video_script="",
        ozon_photo_recommendations="",
        characteristics={},
        strategy_notes="",
        raw_response=last_raw,
    )
