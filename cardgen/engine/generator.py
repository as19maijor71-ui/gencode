import json
import logging
import re

import httpx
from pydantic import BaseModel, Field, ValidationError

from cardgen.config import settings
from cardgen.templates.categories import get_category_name
from cardgen.templates.prompts import build_prompt, COMPETITOR_PROMPT_TEMPLATE

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
        request_body: dict = {
            "model": settings.OPENROUTER_MODEL,
            "messages": [
                {"role": "user", "content": prompt + _strict_json_instruction(strict_json)}
            ],
            "max_tokens": max_tokens,
        }
        if strict_json:
            request_body["response_format"] = {"type": "json_object"}

        response = await client.post(
            settings.OPENROUTER_BASE_URL,
            headers={
                "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
            },
            json=request_body,
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


COMPETITOR_MIN_LENGTH = 50


async def analyze_competitor(
    my_description: str,
    my_category: str,
    competitor_text: str,
) -> str:
    if len(competitor_text) < COMPETITOR_MIN_LENGTH:
        return ""

    truncated = competitor_text[:settings.COMPETITOR_MAX_LENGTH]

    category_name = get_category_name(my_category)

    safe_description = my_description.replace("{", "{{").replace("}", "}}")
    safe_competitor = truncated.replace("{", "{{").replace("}", "}}")

    prompt = COMPETITOR_PROMPT_TEMPLATE.format(
        my_description=safe_description,
        my_category=category_name,
        competitor_text=safe_competitor,
    )

    try:
        raw = await call_ai(prompt, strict_json=False)
    except Exception as e:
        logger.warning(f"Competitor analysis AI call failed: {e}")
        return ""

    if not raw or len(raw.strip()) < 50:
        return ""

    return raw.strip()


def format_for_copy(result: CardResult, competitor_analysis: str = "") -> str:
    parts: list[str] = []

    parts.append("=== WILDBERRIES ===")
    parts.append(f"Заголовок: {result.wb_title or '—'}")
    parts.append(f"Описание: {result.wb_description or '—'}")
    parts.append(f"Ключевые слова: {', '.join(result.wb_keywords) if result.wb_keywords else '—'}")

    parts.append("")
    parts.append("=== OZON ===")
    parts.append(f"Заголовок: {result.ozon_title or '—'}")
    parts.append(f"Описание: {result.ozon_description or '—'}")
    parts.append(f"Ключевые слова: {', '.join(result.ozon_keywords) if result.ozon_keywords else '—'}")

    parts.append("")
    parts.append("=== СЦЕНАРИЙ ВИДЕО OZON ===")
    parts.append(result.ozon_video_script or "—")

    parts.append("")
    parts.append("=== ХАРАКТЕРИСТИКИ ===")
    if result.characteristics:
        for k, v in result.characteristics.items():
            parts.append(f"{k}: {v}")
    else:
        parts.append("—")

    parts.append("")
    parts.append("=== РЕКОМЕНДАЦИИ ПО ФОТО ===")
    parts.append(f"WB: {result.wb_photo_recommendations or '—'}")
    parts.append(f"Ozon: {result.ozon_photo_recommendations or '—'}")

    parts.append("")
    parts.append("=== СТРАТЕГИЯ ===")
    parts.append(result.strategy_notes or "—")

    if competitor_analysis:
        parts.append("")
        parts.append("=== КАК ОБОЙТИ КОНКУРЕНТА ===")
        parts.append(competitor_analysis)

    return "\n".join(parts)


def split_by_sections(text: str, max_len: int = 4096) -> list[str]:
    if len(text) <= max_len:
        return [text]

    raw_sections = re.split(r"\n(?====)", text)

    chunks: list[str] = []
    current = ""

    for section in raw_sections:
        if not current:
            current = section
        elif len(current) + len(section) + 1 <= max_len:
            current += "\n" + section
        else:
            chunks.append(current)
            current = section

    if current:
        if len(current) <= max_len:
            chunks.append(current)
        else:
            lines = current.split("\n")
            sub = ""
            for line in lines:
                if len(line) > max_len:
                    if sub:
                        chunks.append(sub)
                        sub = ""
                    for i in range(0, len(line), max_len):
                        chunks.append(line[i : i + max_len])
                    continue

                candidate = sub + "\n" + line if sub else line
                if len(candidate) > max_len:
                    chunks.append(sub)
                    sub = line
                else:
                    sub = candidate
            if sub:
                chunks.append(sub)

    return chunks
