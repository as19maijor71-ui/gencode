import asyncio
import html
import logging
import time

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from cardgen.bot.keyboards import category_keyboard, competitor_keyboard, confirm_keyboard, start_keyboard
from cardgen.bot.storage import SQLiteStorage
from cardgen.config import settings
from cardgen.engine.generator import CardResult, COMPETITOR_MIN_LENGTH, analyze_competitor, format_for_copy, generate_card, split_by_sections
from cardgen.engine.url_fetcher import detect_platform, extract_product_text, fetch_product_page
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

def _parse_whitelist() -> set[int]:
    raw = settings.WHITELIST_USERS.strip()
    if not raw:
        return set()
    return {int(uid.strip()) for uid in raw.split(",") if uid.strip().isdigit()}

_storage_instance: SQLiteStorage | None = None


def set_storage(storage: SQLiteStorage) -> None:
    global _storage_instance
    _storage_instance = storage


def _store_copy(storage: SQLiteStorage, text: str, user_id: int) -> str:
    key = f"{user_id}:{int(time.time_ns())}"
    storage.put_copy(key, text)
    return key


def _get_copy(storage: SQLiteStorage, key: str) -> str | None:
    return storage.get_copy(key)


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
    logger.info("User %d (@%s) started the bot", message.from_user.id, message.from_user.username or "?")

    whitelist = _parse_whitelist()
    if whitelist and message.from_user.id not in whitelist:
        await message.answer("🔒 Доступ ограничен. Бот в закрытом тестировании.")
        return

    current_state = await state.get_state()
    if current_state == CardFlow.generating:
        await message.answer("⏳ Генерация уже идёт. Подожди, пожалуйста.")
        return

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
    current_state = await state.get_state()
    if current_state == CardFlow.generating:
        await message.answer("⏳ Генерация уже идёт. Подожди, пожалуйста.")
        return

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
        "Отправь URL карточки WB или Ozon — или скопируй текст вручную.\n"
        "Или нажми <b>Пропустить</b>.",
        reply_markup=competitor_keyboard(),
        parse_mode="HTML",
    )
    await state.set_state(CardFlow.competitor_input)

    await state.update_data(competitor_text="")


@router.callback_query(F.data == "competitor:skip", CardFlow.competitor_input)
async def competitor_skip(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer()
    await _do_generate(callback.message, state, competitor_input="", user_id=callback.from_user.id)


@router.message(CardFlow.competitor_input)
async def competitor_text_input(message: Message, state: FSMContext) -> None:
    if not message.text:
        await message.answer("Отправь текст карточки конкурента или нажми «Пропустить».")
        return

    text = message.text.strip()

    if text.startswith("http"):
        platform = detect_platform(text)
        if platform is not None:
            await message.answer("🔗 Анализирую карточку конкурента по ссылке...")
            await _do_generate(message, state, competitor_input=text, user_id=message.from_user.id)
            return
        await message.answer(
            "❌ Это не ссылка на товар WB или Ozon. Скопируй URL из адресной строки."
        )
        return

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

    await _do_generate(message, state, competitor_input=text, user_id=message.from_user.id)


async def _do_generate(message: Message, state: FSMContext, competitor_input: str, user_id: int) -> None:
    data = await state.get_data()
    description: str = data.get("description", "")
    category_key: str = data.get("category", "clothing")
    logger.info("User %d generating card — category: %s, competitor: %s",
                user_id, category_key, "yes" if competitor_input else "no")

    if _storage_instance is not None:
        _storage_instance.log_generation(
            user_id,
            message.from_user.username,
            category_key,
            bool(competitor_input),
        )

    if not description:
        await message.answer("⚠️ Описание товара не найдено. Начни заново: /start")
        await state.clear()
        return

    old_key = data.get("active_copy_key")
    if old_key and _storage_instance is not None:
        _storage_instance.get_copy(old_key)
    await state.update_data(active_copy_key=None)

    await state.set_state(CardFlow.generating)
    thinking_msg = await message.answer(
        "🔍 Анализирую товар...\n\nЭто может занять несколько минут.\nНе закрывайте чат."
    )

    animation_task = asyncio.create_task(_animate_thinking(thinking_msg))

    platform = detect_platform(competitor_input) if competitor_input else None
    competitor_analysis: str = ""
    url_failed = False

    try:
        if platform:
            results = await asyncio.gather(
                generate_card(description=description, category_key=category_key),
                _fetch_and_analyze(competitor_input, description, category_key),
                return_exceptions=True,
            )
            result, competitor_analysis = results
            if isinstance(result, Exception):
                raise result
            if isinstance(competitor_analysis, Exception):
                competitor_analysis = ""
                url_failed = True
            elif not competitor_analysis:
                url_failed = True
        elif competitor_input:
            results = await asyncio.gather(
                generate_card(description=description, category_key=category_key),
                analyze_competitor(description, category_key, competitor_input),
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

    if url_failed:
        await message.answer("⚠️ Не удалось загрузить карточку. Генерирую без анализа.")

    copy_text = format_for_copy(result, competitor_analysis)
    if _storage_instance is not None:
        copy_key = _store_copy(_storage_instance, copy_text, user_id)
        await state.update_data(active_copy_key=copy_key)
        copy_kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="📋 Копировать всё", callback_data=f"copy:{copy_key}")
        ]])
        await message.answer("📋 Нажми, чтобы скопировать всю карточку одним сообщением:", reply_markup=copy_kb)

    await message.answer(
        "✅ Готово! Выбери категорию для следующего товара:",
        reply_markup=category_keyboard(),
    )
    await state.set_state(CardFlow.category_select)


async def _fetch_and_analyze(url: str, description: str, category_key: str) -> str:
    try:
        platform = detect_platform(url)
        if not platform:
            return ""
        html = await fetch_product_page(url)
        text = extract_product_text(html, platform)
        if not text or len(text) < COMPETITOR_MIN_LENGTH:
            return ""
        return await analyze_competitor(description, category_key, text)
    except Exception as e:
        logger.warning(f"URL fetch/analyze failed: {e}")
        return ""


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
    if _storage_instance is None:
        await callback.answer("⚠️ Данные устарели. Сгенерируй заново.", show_alert=True)
        return
    text = _get_copy(_storage_instance, key)

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

    await callback.message.answer(
        "📋 <b>Выдели текст выше и скопируй.</b>\n"
        "На десктопе: зажми левую кнопку мыши и протяни по тексту → Ctrl+C.\n"
        "На телефоне: нажми на сообщение и удерживай → «Копировать».\n\n"
        "Затем вставь в карточку товара на WB или Ozon.\n\n"
        "Готово! Выбери категорию для следующего товара:",
        reply_markup=category_keyboard(),
        parse_mode="HTML",
    )


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
        try:
            await message.answer(chunk, parse_mode="HTML")
        except Exception as e:
            logger.warning(f"Failed to send WB chunk with HTML: {e}")
            await message.answer(_escape(chunk))

    # Message 2: Ozon
    ozon_text = (
        "🔵 <b>Ozon</b>\n\n"
        f"<b>Заголовок:</b>\n{_escape(result.ozon_title) or '—'}\n\n"
        f"<b>Описание:</b>\n{_escape(result.ozon_description) or '—'}\n\n"
        f"<b>Ключевые слова:</b>\n{_escape(', '.join(result.ozon_keywords)) if result.ozon_keywords else '—'}"
    )
    for chunk in _safe_send(ozon_text):
        try:
            await message.answer(chunk, parse_mode="HTML")
        except Exception as e:
            logger.warning(f"Failed to send Ozon chunk with HTML: {e}")
            await message.answer(_escape(chunk))

    # Message 3: Video script for Ozon
    video_text = (
        "🎬 <b>Сценарий видео для Ozon</b>\n\n"
        f"{_escape(result.ozon_video_script) or '—'}"
    )
    for chunk in _safe_send(video_text):
        try:
            await message.answer(chunk, parse_mode="HTML")
        except Exception as e:
            logger.warning(f"Failed to send video chunk with HTML: {e}")
            await message.answer(_escape(chunk))

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
        try:
            await message.answer(chunk, parse_mode="HTML")
        except Exception as e:
            logger.warning(f"Failed to send photos chunk with HTML: {e}")
            await message.answer(_escape(chunk))

    # Message 5: Strategy notes
    strategy_text = (
        "🎯 <b>Стратегия: что делать прямо сейчас</b>\n\n"
        f"{_escape(result.strategy_notes) or '—'}"
    )
    for chunk in _safe_send(strategy_text):
        try:
            await message.answer(chunk, parse_mode="HTML")
        except Exception as e:
            logger.warning(f"Failed to send strategy chunk with HTML: {e}")
            await message.answer(_escape(chunk))

    # Message 6: Competitor analysis (optional)
    if competitor_analysis:
        comp_text = (
            "🕵️ <b>Как обойти конкурента</b>\n\n"
            f"{_escape(competitor_analysis)}"
        )
        for chunk in _safe_send(comp_text):
            try:
                await message.answer(chunk, parse_mode="HTML")
            except Exception as e:
                logger.warning(f"Failed to send competitor chunk with HTML: {e}")
                await message.answer(_escape(chunk))


@router.message(Command("myid"))
async def cmd_myid(message: Message) -> None:
    await message.answer(f"Твой Telegram ID: <code>{message.from_user.id}</code>", parse_mode="HTML")


@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    if settings.ADMIN_USER_ID and message.from_user.id != settings.ADMIN_USER_ID:
        await message.answer("⛔ Эта команда только для администратора.")
        return

    if _storage_instance is None:
        await message.answer("⚠️ Хранилище недоступно.")
        return

    rows = _storage_instance.get_recent_activity(limit=30)
    if not rows:
        await message.answer("📊 Пока нет данных о генерациях.")
        return

    users: dict[int, list] = {}
    total = 0
    with_competitor = 0
    for r in rows:
        uid = r["user_id"]
        if uid not in users:
            users[uid] = []
        users[uid].append(r)
        total += 1
        if r["has_competitor"]:
            with_competitor += 1

    lines = [f"📊 <b>Активность за 7 дней</b>\n"]
    lines.append(f"Всего генераций: {total} (с конкурентом: {with_competitor})")
    lines.append(f"Уникальных пользователей: {len(users)}\n")

    for uid, recs in users.items():
        username = recs[0].get("username") or f"ID:{uid}"
        cats = [r["category"] for r in recs]
        last = recs[0]["created_at"]
        lines.append(f"👤 @{username} — {len(recs)} генераций, последняя: {last}")
        lines.append(f"   Категории: {', '.join(cats[:5])}")

    await message.answer("\n".join(lines), parse_mode="HTML")
