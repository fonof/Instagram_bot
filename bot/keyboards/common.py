"""Reply- и Inline-клавиатуры."""

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)


def kb_add_account_only() -> ReplyKeyboardMarkup:
    """Стартовый экран, когда аккаунтов ещё нет."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="➕ Добавить новый Instagram-аккаунт")]],
        resize_keyboard=True,
    )


def kb_main_reply() -> ReplyKeyboardMarkup:
    """После того как в базе есть хотя бы один аккаунт."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Выбрать Instagram-аккаунт")],
            [KeyboardButton(text="📊 Запущенные автопостинги")],
        ],
        resize_keyboard=True,
    )


def kb_account_actions() -> InlineKeyboardMarkup:
    """Главное меню выбранного аккаунта."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📤 Загрузить видео", callback_data="acc:upload")],
            [
                InlineKeyboardButton(
                    text="📊 Запущенные автопостинги",
                    callback_data="queue:list",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📋 Сменить аккаунт",
                    callback_data="acc:switch",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отключить текущий аккаунт",
                    callback_data="acc:disconnect",
                )
            ],
        ]
    )


def kb_upload_confirm() -> InlineKeyboardMarkup:
    """Подтверждение перед постингом в Stories."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Старт", callback_data="post:confirm"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="post:cancel"),
            ]
        ]
    )
