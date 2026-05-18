import json
import logging
import re
from urllib.parse import urlparse

import httpx

from cardgen.config import settings

logger = logging.getLogger(__name__)

class CompetitorFetchError(Exception):
    pass


_WB_PATH_RE = re.compile(r"^/catalog/\d+/detail\.aspx")
# NOTE: Short links like /product?card=... (from WB app) are NOT detected.
# Plan explicitly limits to /catalog/ paths. Users can always paste text manually.
_OZON_PATH_RE = re.compile(r"^/product/")


def detect_platform(url: str) -> str | None:
    try:
        parsed = urlparse(url)
    except Exception:
        return None

    if parsed.scheme not in ("http", "https"):
        return None

    hostname = parsed.hostname or ""
    hostname = hostname.lower().removeprefix("www.").removeprefix("m.")

    # NOTE: only .ru domains supported. by.wildberries.ru, kz.ozon.ru etc.
    # are not detected. MVP scope — Russian market only.
    if hostname == "wildberries.ru" and _WB_PATH_RE.match(parsed.path):
        return "wb"
    if hostname == "ozon.ru" and _OZON_PATH_RE.match(parsed.path):
        return "ozon"
    return None


async def fetch_product_page(url: str) -> str:
    # NOTE: AsyncClient is re-created per request — no connection pooling.
    # Acceptable for MVP (<5s timeout, <50 users). Production: use shared client.
    # NOTE: follow_redirects=True — theoretical SSRF if WB/Ozon redirects to
    # internal address. Mitigated by detect_platform() validating hostname first.
    # Risk: extremely low (requires compromise of target domain).
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ru",
        "Referer": "https://www.google.com",
    }
    try:
        async with httpx.AsyncClient(timeout=settings.COMPETITOR_FETCH_TIMEOUT) as client:
            response = await client.get(url, headers=headers, follow_redirects=True)
        if response.status_code == 404:
            raise CompetitorFetchError("Карточка не найдена")
        if response.status_code in (403, 429):
            raise CompetitorFetchError(f"Доступ запрещён (HTTP {response.status_code})")
        response.raise_for_status()
        return response.text
    except CompetitorFetchError:
        raise
    except httpx.TimeoutException:
        raise CompetitorFetchError("Таймаут при загрузке карточки")
    except Exception as e:
        raise CompetitorFetchError(f"Ошибка при загрузке: {e}")


_NEXT_DATA_RE = re.compile(r'<script\s+id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL)
_LD_JSON_RE = re.compile(r'<script\s+type="application/ld\+json">(.*?)</script>', re.DOTALL)


def extract_product_text(html: str, platform: str) -> str:
    try:
        if platform == "wb":
            return _extract_wb_text(html)
        elif platform == "ozon":
            return _extract_ozon_text(html)
        else:
            return ""
    except Exception as e:
        logger.warning(f"Failed to extract product text for {platform}: {e}")
        return ""


def _extract_wb_text(html: str) -> str:
    match = _NEXT_DATA_RE.search(html)
    if not match:
        return ""

    raw = match.group(1)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return ""

    product = (
        data.get("props", {})
        .get("pageProps", {})
        .get("product", {})
    )
    if not isinstance(product, dict):
        return ""

    parts: list[str] = []

    title = product.get("name") or product.get("title") or ""
    if title:
        parts.append(title)

    description = product.get("description") or ""
    if description:
        parts.append(description)

    characteristics = product.get("characteristics") or product.get("options") or []
    if isinstance(characteristics, list):
        chars_text = _flatten_characteristics(characteristics)
        if chars_text:
            parts.append(chars_text)

    brand = product.get("brand") or product.get("brandName") or ""
    if brand:
        parts.append(f"Бренд: {brand}")

    text = "\n".join(parts)
    return text[:settings.COMPETITOR_MAX_LENGTH]


def _extract_ozon_text(html: str) -> str:
    match = _LD_JSON_RE.search(html)
    if not match:
        return ""

    raw = match.group(1)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return ""

    if isinstance(data, list):
        data = data[0] if data else {}

    parts: list[str] = []

    name = data.get("name") or ""
    if name:
        parts.append(name)

    description = data.get("description") or ""
    if description:
        parts.append(description)

    text = "\n".join(parts)
    return text[:settings.COMPETITOR_MAX_LENGTH]


def _flatten_characteristics(chars: list) -> str:
    lines: list[str] = []
    for item in chars:
        if isinstance(item, dict):
            name = item.get("name") or item.get("title") or ""
            value = item.get("value") or item.get("text") or ""
            if name and value:
                lines.append(f"{name}: {value}")
    return "\n".join(lines)
