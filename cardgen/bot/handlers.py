import asyncio
import html
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from cardgen.bot.keyboards import category_keyboard, competitor_keyboard, confirm_keyboard, start_keyboard
from cardgen.config import settings
from cardgen.engine.generator import CardResult, analyze_competitor, format_for_copy, generate_card, split_by_sections
from cardgen.templates.categories import (
    detect_category,
    get_category_emoji,
    get_category_examples,
    get_category_fields,
    get_category_name,
)

router = Router()

logger = logging.getLogger(__name__)

TELEGRAM_MAX_LENGTH = 4096

_copy_cache: dict[str, str] = {}
_copy_counter = 0
_COPY_CACHE_MAX = 1000


def _store_copy(text: str) -> str:
    global _copy_counter
    _copy_counter += 1
    key = str(_copy_counter)
    if len(_copy_cache) >= _COPY_CACHE_MAX:
        oldest = next(iter(_copy_cache))
        del _copy_cache[oldest]
    _copy_cache[key] = text
    return key


def _get_copy(key: str) -> str | None:
    return _copy_cache.pop(key, None)


class CardFlow(StatesGroup):
    category_select = State()
    product_input = State()
    confirm = State()
    competitor_input = State()
    generating = State()


def _escape(text: str) -> str:
    return html.escape(text)


def _safe_send(text: str) -> list[str]:
    if len(text) <= TELEGRAM_MAX_LENGTH:
        return [text]
    chunks: list[str] = []
    while len(text) > TELEGRAM_MAX_LENGTH:
        split_at = text.rfind("\n", 0, TELEGRAM_MAX_LENGTH)
        if split_at == -1:
            split_at = TELEGRAM_MAX_LENGTH
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    if text:
        chunks.append(text)
    return chunks


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "👋 Привет! Я — AI-маркетолог.\n\n"
        "Создаю продающие карточки для <b>Wildberries</b> и <b>Ozon</b>.\n"
        "Не текст. Стратегия.\n\n"
        "Выбери категорию товара:",
        reply_markup=category_keyboard(),
        parse_mode="HTML",
    )
    await state.set_state(CardFlow.category_select)


@router.callback_query(F.data.startswith("category:"), CardFlow.category_select)
async def category_chosen(callback: CallbackQuery, state: FSMContext) -> None:
    category_key = callback.data.split(":", 1)[1]
    await state.update_data(category=category_key)
    await callback.answer()

    emoji = get_category_emoji(category_key)
    name = get_category_name(category_key)
    fields = get_category_fields(category_key)

    fields_text = "\n".join(f"  • {_escape(f)}" for f in fields)
    await callback.message.answer(
        f"{emoji} <b>{_escape(name)}</b>\n\n"
        f"⚠️ Убедись, что твой товар — это действительно <b>{_escape(name)}</b>.\n\n"
        f"Поля характеристик, которые я заполню:\n{fields_text}\n\n"
        "Опиши товар своими словами (до 2000 символов).\n"
        "Чем подробнее — тем точнее результат.",
        reply_markup=start_keyboard(),
        parse_mode="HTML",
    )
    await state.set_state(CardFlow.product_input)


@router.message(F.text == "🔄 Начать заново")
async def restart(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "Выбери категорию товара:",
        reply_markup=category_keyboard(),
    )
    await state.set_state(CardFlow.category_select)


@router.message(CardFlow.generating)
async def busy(message: Message) -> None:
    await message.answer("⏳ Генерация уже идёт. Подожди, пожалуйста.")


@router.message(CardFlow.product_input)
async def product_description_input(message: Message, state: FSMContext) -> None:
    if not message.text:
        await message.answer("Отправь текстовое описание товара.")
        return

    if len(message.text) > settings.MAX_INPUT_LENGTH:
        await message.answer(
            f"Описание слишком длинное ({len(message.text)} символов). "
            f"Сократи до {settings.MAX_INPUT_LENGTH} символов."
        )
        return

    data = await state.get_data()
    category_key = data.get("category", "clothing")

    await state.update_data(description=message.text)
    await state.set_state(CardFlow.confirm)

    emoji = get_category_emoji(category_key)
    name = get_category_name(category_key)
    examples = get_category_examples(category_key)
    await message.answer(
        f"{emoji} <b>{_escape(name)}</b>\n"
        f"Примеры: <i>{_escape(examples)}</i>\n\n"
        f"Твой товар: <i>{_escape(message.text[:200])}</i>\n\n"
        "⚠️ Это точно правильная категория?",
        reply_markup=confirm_keyboard(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "confirm:yes", CardFlow.confirm)
async def confirm_yes(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    description: str = data.get("description", "")
    category_key: str = data.get("category", "clothing")

    detected = detect_category(description)
    category_name = get_category_name(category_key)

    if detected and detected != category_key:
        detected_name = get_category_name(detected)
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.answer()
        await callback.message.answer(
            f"⚠️ <b>Внимание!</b>\n\n"
            f"Твой товар похож на категорию <b>{_escape(detected_name)}</b>, "
            f"а выбрана <b>{_escape(category_name)}</b>.\n\n"
            "Выбери правильную категорию:",
            reply_markup=category_keyboard(),
            parse_mode="HTML",
        )
        await state.set_state(CardFlow.category_select)
        return

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer()

    name = get_category_name(category_key)
    await callback.message.answer(
        f"🎯 <b>Конкурентный анализ</b>\n\n"
        f"Хочешь обойти конкурента в категории <b>{_escape(name)}</b>?\n\n"
        "Скопируй текст его карточки (заголовок + описание + характеристики) "
        "или нажми <b>Пропустить</b>.\n\n"
        "⚠️ Убедись, что конкурент из той же категории.",
        reply_markup=competitor_keyboard(),
        parse_mode="HTML",
    )
    await state.set_state(CardFlow.competitor_input)

    await state.update_data(competitor_text="")


@router.callback_query(F.data == "competitor:skip", CardFlow.competitor_input)
async def competitor_skip(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer()
    await _do_generate(callback.message, state, competitor_text="")


@router.message(CardFlow.competitor_input)
async def competitor_text_input(message: Message, state: FSMContext) -> None:
    if not message.text:
        await message.answer("Отправь текст карточки конкурента или нажми «Пропустить».")
        return

    text = message.text.strip()

    if len(text) < 50:
        await message.answer(
            "Слишком мало текста. Скопируй заголовок, описание и характеристики конкурента "
            "(минимум 50 символов)."
        )
        return

    if len(text) > settings.COMPETITOR_MAX_LENGTH:
        await message.answer(
            f"Текст слишком длинный ({len(text)} символов). Максимум {settings.COMPETITOR_MAX_LENGTH} символов. "
            "Сократи и отправь снова."
        )
        return

    await _do_generate(message, state, competitor_text=text)


async def _do_generate(message: Message, state: FSMContext, competitor_text: str) -> None:
    data = await state.get_data()
    description: str = data.get("description", "")
    category_key: str = data.get("category", "clothing")

    await state.set_state(CardFlow.generating)
    thinking_msg = await message.answer(
        "🔍 Анализирую товар...\n\nЭто может занять несколько минут.\nНе закрывайте чат."
    )

    animation_task = asyncio.create_task(_animate_thinking(thinking_msg))

    try:
        if competitor_text:
            results = await asyncio.gather(
                generate_card(description=description, category_key=category_key),
                analyze_competitor(description, category_key, competitor_text),
                return_exceptions=True,
            )
            result, competitor_analysis = results
            if isinstance(result, Exception):
                raise result
            if isinstance(competitor_analysis, Exception):
                competitor_analysis = ""
        else:
            result = await generate_card(
                description=description,
                category_key=category_key,
            )
            competitor_analysis = ""
    except Exception as e:
        animation_task.cancel()
        await thinking_msg.edit_text(f"❌ Ошибка при генерации: {_escape(str(e))}")
        await state.set_state(CardFlow.product_input)
        return

    animation_task.cancel()
    try:
        await thinking_msg.delete()
    except Exception:
        pass

    await send_results(message, result, competitor_analysis=competitor_analysis)

    copy_text = format_for_copy(result, competitor_analysis)
    copy_key = _store_copy(copy_text)
    copy_kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📋 Копировать всё", callback_data=f"copy:{copy_key}")
    ]])
    await message.answer("📋 Нажми, чтобы скопировать всю карточку одним сообщением:", reply_markup=copy_kb)

    await message.answer(
        "✅ Готово! Выбери категорию для следующего товара:",
        reply_markup=category_keyboard(),
    )
    await state.set_state(CardFlow.category_select)


@router.callback_query(F.data == "confirm:no", CardFlow.confirm)
async def confirm_no(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer()
    await callback.message.answer(
        "Выбери правильную категорию:",
        reply_markup=category_keyboard(),
    )
    await state.set_state(CardFlow.category_select)


@router.callback_query(F.data.startswith("copy:"))
async def copy_all(callback: CallbackQuery) -> None:
    key = callback.data.split(":", 1)[1]
    text = _get_copy(key)

    if text is None:
        await callback.answer("⚠️ Данные устарели. Сгенерируй заново.", show_alert=True)
        return

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer()

    for chunk in split_by_sections(text):
        try:
            await callback.message.answer(chunk)
        except Exception as e:
            logger.warning(f"Failed to send copy chunk: {e}")
            break


async def _animate_thinking(msg: Message) -> None:
    stages = [
        "🔍 Анализирую товар...",
        "📋 Собираю правила Wildberries...",
        "📋 Собираю правила Ozon...",
        "✍️ Генерирую заголовки...",
        "🔑 Подбираю SEO-ключи...",
        "📸 Готовлю фото-рекомендации...",
        "🎬 Пишу сценарий видео Ozon...",
        "📦 Упаковываю результат...",
    ]
    footer = "\n\nЭто может занять несколько минут.\nНе закрывайте чат."
    try:
        while True:
            for stage in stages:
                try:
                    await msg.edit_text(f"{stage}{footer}")
                except TelegramBadRequest:
                    pass
                await asyncio.sleep(4)
    except asyncio.CancelledError:
        pass


async def send_results(message: Message, result: CardResult, competitor_analysis: str = "") -> None:
    # Message 1: WB
    wb_text = (
        "🟣 <b>Wildberries</b>\n\n"
        f"<b>Заголовок:</b>\n{_escape(result.wb_title) or '—'}\n\n"
        f"<b>Описание:</b>\n{_escape(result.wb_description) or '—'}\n\n"
        f"<b>Ключевые слова:</b>\n{_escape(', '.join(result.wb_keywords)) if result.wb_keywords else '—'}"
    )
    for chunk in _safe_send(wb_text):
        await message.answer(chunk, parse_mode="HTML")

    # Message 2: Ozon
    ozon_text = (
        "🔵 <b>Ozon</b>\n\n"
        f"<b>Заголовок:</b>\n{_escape(result.ozon_title) or '—'}\n\n"
        f"<b>Описание:</b>\n{_escape(result.ozon_description) or '—'}\n\n"
        f"<b>Ключевые слова:</b>\n{_escape(', '.join(result.ozon_keywords)) if result.ozon_keywords else '—'}"
    )
    for chunk in _safe_send(ozon_text):
        await message.answer(chunk, parse_mode="HTML")

    # Message 3: Video script for Ozon
    video_text = (
        "🎬 <b>Сценарий видео для Ozon</b>\n\n"
        f"{_escape(result.ozon_video_script) or '—'}"
    )
    for chunk in _safe_send(video_text):
        await message.answer(chunk, parse_mode="HTML")

    # Message 4: Characteristics + Photo recommendations
    chars = (
        "\n".join(f"• <b>{_escape(k)}:</b> {_escape(v)}" for k, v in result.characteristics.items())
        if result.characteristics
        else "—"
    )
    photos_text = (
        "📋 <b>Характеристики</b>\n\n"
        f"{chars}\n\n"
        "📸 <b>Рекомендации по фото</b>\n\n"
        f"🟣 <b>WB:</b>\n{_escape(result.wb_photo_recommendations) or '—'}\n\n"
        f"🔵 <b>Ozon:</b>\n{_escape(result.ozon_photo_recommendations) or '—'}"
    )
    for chunk in _safe_send(photos_text):
        await message.answer(chunk, parse_mode="HTML")

    # Message 5: Strategy notes
    strategy_text = (
        "🎯 <b>Стратегия: что делать прямо сейчас</b>\n\n"
        f"{_escape(result.strategy_notes) or '—'}"
    )
    for chunk in _safe_send(strategy_text):
        await message.answer(chunk, parse_mode="HTML")

    # Message 6: Competitor analysis (optional)
    if competitor_analysis:
        comp_text = (
            "🕵️ <b>Как обойти конкурента</b>\n\n"
            f"{_escape(competitor_analysis)}"
        )
        for chunk in _safe_send(comp_text):
            await message.answer(chunk, parse_mode="HTML")
