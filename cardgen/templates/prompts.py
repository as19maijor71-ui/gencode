from cardgen.engine.ozon_algorithm import get_ozon_rules
from cardgen.engine.wb_algorithm import get_wb_rules
from cardgen.templates.categories import get_category_fields, get_category_name

PROMPT_TEMPLATE = """Ты — AI-маркетолог, который создаёт продающие карточки товаров для российских маркетплейсов Wildberries и Ozon. Твоя задача — на основе описания товара селлера и заданной категории сгенерировать полную стратегию карточки.

Не текст. Стратегия.

## Данные о товаре

**Категория:** {category_name}
**Целевая аудитория:** {target_audience}
**Бренд:** {brand}
**Описание товара от селлера:**
{description}

## Поля характеристик для этой категории
{category_fields}

{wb_rules}

{ozon_rules}

## Требования к ответу

Ты должен вернуть ТОЛЬКО валидный JSON-объект без комментариев, без markdown-обрамления ```json, без пояснений. Все поля обязательны. Если ты не уверен в значении, предложи наиболее вероятное на основе опыта.

Структура ответа:
{{
    "wb_title": "строка 60-100 символов",
    "wb_description": "длинный продающий текст, 5-7 блоков с эмодзи-заголовками",
    "wb_keywords": ["ключ1", "ключ2", ...],  // ровно 15 штук
    "wb_photo_recommendations": "рекомендации по фото для WB",
    "ozon_title": "строка до 150 символов",
    "ozon_description": "структурированный текст с Markdown-заголовками",
    "ozon_keywords": ["ключ1", "ключ2", ...],  // ровно 25 штук, английские SEO-ключи, не транслит
    "ozon_video_script": "сценарий видео 15-60 секунд с таймингом",
    "ozon_photo_recommendations": "рекомендации по фото для Ozon",
    "characteristics": {{
        "Название поля 1": "значение",
        "Название поля 2": "значение"
    }},
    "strategy_notes": "3-5 пунктов стратегии: что делать прямо сейчас для максимальных продаж"
}}

В wb_keywords — ровно 15 ключей.
В ozon_keywords — ровно 25 ключей, английские SEO-слова (например: \"cat bed\", \"face cream\"), НЕ транслитерация русских слов латиницей.
В characteristics заполни ВСЕ поля из списка выше. Значения должны быть конкретными, не «стандартный», не «обычный», не «разные».
    strategy_notes — 5 конкретных пунктов. Каждый с новой строки, начинается с цифры и точки. Указывай: скидки в %, тайминги (дни), конкретные ключи для рекламы, типы фото/видео. Не пиши общих фраз вроде «запустить рекламу» — пиши на каких ключах и с каким бюджетом.
"""

COMPETITOR_PROMPT_TEMPLATE = """Ты — AI-маркетолог, который анализирует карточки конкурентов на Wildberries и Ozon. Твоя задача — найти слабые места в карточке конкурента и показать, как обойти его.

Не текст. Стратегия.

## Наша карточка товара

**Категория:** {my_category}
**Наше описание:**
{my_description}

## Карточка конкурента (текст, скопированный селлером)

{competitor_text}

## Требования к ответу

Найди 3-5 конкретных слабых мест в карточке конкурента и дай точки обхода. Для каждого пункта укажи:
1. Что именно у конкурента слабо (со ссылкой на конкретные данные из его текста)
2. Как мы можем это обойти (конкретное действие)

Формат ответа — нумерованный список. Каждый пункт начинается с цифры и точки. Пример:

1. Конкурент: «...» — наша точка обхода: ...
2. Конкурент: «...» — наша точка обхода: ...
...

Пиши конкретно. Никакой воды. Если в тексте конкурента нет слабых мест — не придумывай, скажи честно, что карточка сильная и обойти сложно.
"""


def build_prompt(
    description: str,
    category_key: str,
    target_audience: str,
    brand: str,
    category_fields: list[str] | None = None,
) -> str:
    if category_fields is None:
        category_fields = get_category_fields(category_key)

    safe_description = description.replace("{", "{{").replace("}", "}}")

    fields_formatted = "\n".join(f"- {f}" for f in category_fields)

    return PROMPT_TEMPLATE.format(
        description=safe_description,
        category_name=get_category_name(category_key),
        target_audience=target_audience,
        brand=brand,
        category_fields=fields_formatted,
        wb_rules=get_wb_rules(),
        ozon_rules=get_ozon_rules(),
    )
