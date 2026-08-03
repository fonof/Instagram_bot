"""Команда /start и первичная клавиатура."""

import logging

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from bot.keyboards import kb_add_account_only, kb_main_reply
from bot.utils import load_accounts

logger = logging.getLogger(__name__)

router = Router(name="start")


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    """Старт: разная Reply-клавиатура в зависимости от наличия аккаунтов."""
    accounts = await load_accounts()
    if not accounts:
        await message.answer(
            "Привет! У тебя ещё нет подключённых Instagram-аккаунтов.\n"
            "Нажми кнопку ниже, чтобы добавить первый аккаунт.",
            reply_markup=kb_add_account_only(),
        )
    else:
        await message.answer(
            "Выбери действие: «Выбрать Instagram-аккаунт» — список и добавление; "
            "«📊 Запущенные автопостинги» — очередь и остановка.",
            reply_markup=kb_main_reply(),
        )
    logger.info("Пользователь %s вызвал /start", message.from_user.id if message.from_user else "?")
