"""
Приём ссылок на Reels, сводка перед стартом.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.keyboards import kb_upload_confirm
from bot.states import UploadStates
from bot.utils import (
    MAX_LINKS,
    AccountRecord,
    get_queue_entry,
    load_accounts,
    parse_instagram_urls,
)

logger = logging.getLogger(__name__)

router = Router(name="upload")


async def _account_by_id(aid: int) -> AccountRecord | None:
    accounts = await load_accounts()
    return next((a for a in accounts if a.id == aid), None)


@router.callback_query(F.data == "acc:upload")
async def cb_upload_start(query: CallbackQuery, state: FSMContext) -> None:
    await query.answer()
    data = await state.get_data()
    sel = data.get("selected_account_id")
    if not sel:
        await query.message.answer("Сначала выбери Instagram-аккаунт.")
        return

    existing = await get_queue_entry(int(sel))
    if existing and existing.cursor < len(existing.reel_urls):
        await query.message.answer(
            "⏳ Для этого аккаунта уже идёт автопостинг.\n\n"
            "Нажми «📊 Запущенные автопостинги» (внизу или в меню аккаунта), "
            "чтобы посмотреть прогресс или остановить очередь."
        )
        return

    await state.set_state(UploadStates.waiting_links)
    await query.message.answer(
        "Пришлите до <b>100</b> ссылок на <b>рилсы</b> "
        "(каждая ссылка с новой строки, через пробел или запятую):",
        parse_mode="HTML",
    )


@router.message(StateFilter(UploadStates.waiting_links), F.text)
async def process_links(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    sel = data.get("selected_account_id")
    if not sel:
        await message.answer("Аккаунт не выбран. Используй /start.")
        await state.clear()
        return

    acc = await _account_by_id(int(sel))
    if not acc:
        await message.answer("Аккаунт не найден.")
        await state.clear()
        return

    urls = parse_instagram_urls(message.text or "")
    if not urls:
        await message.answer(
            "Не найдено ни одной ссылки Instagram. Попробуй ещё раз "
            "(формат: https://www.instagram.com/reel/... )."
        )
        return

    if len(urls) > MAX_LINKS:
        await message.answer(f"Слишком много ссылок. Максимум {MAX_LINKS}.")
        return

    await state.update_data(pending_reel_urls=urls)
    await state.set_state()

    n = len(urls)
    await message.answer(
        f"Загружено <b>{n}</b> ссылок.\n"
        f"Аккаунт: <b>{acc.name}</b> (@{acc.username})\n"
        f"Готовы к автопостингу в Stories?",
        parse_mode="HTML",
        reply_markup=kb_upload_confirm(),
    )
