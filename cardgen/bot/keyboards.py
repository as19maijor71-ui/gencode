from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from cardgen.templates.categories import CATEGORIES


def category_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for key, cat in CATEGORIES.items():
        builder.add(InlineKeyboardButton(
            text=f"{cat['emoji']} {cat['name']}",
            callback_data=f"category:{key}",
        ))
    builder.adjust(2)
    return builder.as_markup()


def start_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🔄 Начать заново")]],
        resize_keyboard=True,
    )


def confirm_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(text="✅ Да, всё верно", callback_data="confirm:yes"))
    builder.add(InlineKeyboardButton(text="🔄 Выбрать другую категорию", callback_data="confirm:no"))
    builder.adjust(1)
    return builder.as_markup()


def competitor_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(text="⏭ Пропустить", callback_data="competitor:skip"))
    builder.adjust(1)
    return builder.as_markup()
