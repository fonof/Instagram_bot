"""
Выбор аккаунта, добавление (Appium: логин / 2FA / имя), смена и отключение.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil

from aiogram import F, Router
from aiogram.exceptions import TelegramNetworkError
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.keyboards import kb_account_actions, kb_add_account_only, kb_main_reply
from bot.states import AccountPickStates, AddAccountStates
from bot.utils import (
    EMULATOR_SESSIONS_DIR,
    ROOT_DIR,
    AdbError,
    AccountRecord,
    Instagram2FARequired,
    InstagramAppiumError,
    InstagramLoginError,
    prepare_android_profile_for_appium,
    ensure_android_user,
    ensure_directories,
    get_shared_appium_client,
    load_accounts,
    next_account_id,
    remove_queue_entry,
    restart_instagram,
    reset_shared_appium_client,
    save_accounts,
    switch_android_user,
)

logger = logging.getLogger(__name__)

router = Router(name="accounts")


async def answer_with_retry(
    message: Message,
    text: str,
    *,
    parse_mode: str | None = None,
    reply_markup=None,
    attempts: int = 6,
) -> None:
    delay = 1.5
    last: Exception | None = None
    for i in range(attempts):
        try:
            await message.answer(text, parse_mode=parse_mode, reply_markup=reply_markup)
            return
        except TelegramNetworkError as e:
            last = e
            logger.warning(
                "TelegramNetworkError при answer (попытка %s/%s): %s",
                i + 1,
                attempts,
                e,
            )
            if i < attempts - 1:
                await asyncio.sleep(delay)
                delay = min(delay * 1.8, 30.0)
    assert last is not None
    raise last


def _accounts_inline_kb(accounts: list[AccountRecord]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for a in accounts:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{a.name} (@{a.username})",
                    callback_data=f"acc:sel:{a.id}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="➕ Добавить новый Instagram-аккаунт",
                callback_data="acc:add",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(F.text == "Выбрать Instagram-аккаунт")
async def choose_account_entry(message: Message, state: FSMContext) -> None:
    accounts = await load_accounts()
    if not accounts:
        await message.answer(
            "Аккаунтов пока нет. Добавь первый:",
            reply_markup=kb_add_account_only(),
        )
        return
    await state.set_state(AccountPickStates.selecting)
    await message.answer(
        "Выбери Instagram-аккаунт из списка или добавь новый:",
        reply_markup=_accounts_inline_kb(accounts),
    )


@router.callback_query(F.data == "acc:switch")
async def cb_switch_account(query: CallbackQuery, state: FSMContext) -> None:
    await query.answer()
    accounts = await load_accounts()
    if not accounts:
        await query.message.answer("Список аккаунтов пуст.")
        return
    await state.set_state(AccountPickStates.selecting)
    await query.message.answer(
        "Выбери другой аккаунт:",
        reply_markup=_accounts_inline_kb(accounts),
    )


@router.callback_query(F.data == "acc:add")
async def cb_add_account(query: CallbackQuery, state: FSMContext) -> None:
    await query.answer()
    await state.set_state(AddAccountStates.login)
    await query.message.answer(
        "Введи <b>логин</b> Instagram (имя пользователя):",
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("acc:sel:"))
async def cb_select_account(query: CallbackQuery, state: FSMContext) -> None:
    await query.answer()
    aid = int(query.data.split(":")[-1])
    accounts = await load_accounts()
    acc = next((a for a in accounts if a.id == aid), None)
    if not acc:
        await query.message.answer("Аккаунт не найден. Обнови список через /start.")
        await state.clear()
        return
    await state.update_data(selected_account_id=aid)
    await state.set_state()
    await query.message.answer(
        f"Выбран аккаунт: <b>{acc.name}</b> (@{acc.username})\n"
        f"Главное меню:",
        parse_mode="HTML",
        reply_markup=kb_account_actions(),
    )


@router.message(StateFilter(AddAccountStates.login))
async def add_account_login(message: Message, state: FSMContext) -> None:
    login = (message.text or "").strip()
    if not login:
        await message.answer("Логин не может быть пустым. Попробуй ещё раз.")
        return
    await state.update_data(ig_username=login)
    await state.set_state(AddAccountStates.password)
    await message.answer(
        "Теперь отправь <b>пароль</b> (при желании удали сообщение из чата вручную).",
        parse_mode="HTML",
    )


@router.message(StateFilter(AddAccountStates.password))
async def add_account_password(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    username = data.get("ig_username")
    password = message.text or ""
    uid = message.from_user.id

    if not username or not password:
        await message.answer("Нужны логин и пароль. Начни снова через «Добавить аккаунт».")
        await state.clear()
        return

    ensure_directories()
    new_id = await next_account_id()
    android_label = f"ig_account_{new_id}"

    await message.answer(
        "Создаю отдельный Android-профиль для аккаунта и подключаюсь к эмулятору через Appium…"
    )

    try:
        android_user_id = await ensure_android_user(android_label)
        await switch_android_user(android_user_id)
        await prepare_android_profile_for_appium(android_user_id)
        await restart_instagram()
        await reset_shared_appium_client()
        client = await get_shared_appium_client()
        await client.login(username, password)
    except Instagram2FARequired:
        await state.update_data(
            ig_password=password,
            pending_new_account_id=new_id,
            android_user_id=android_user_id,
        )
        await state.set_state(AddAccountStates.twofa)
        await message.answer(
            "Нужна двухфакторная аутентификация. Отправь <b>код из приложения</b> (TOTP).",
            parse_mode="HTML",
        )
        return
    except AdbError as e:
        await message.answer(f"ADB ошибка при создании/переключении профиля: {e}")
        await state.clear()
        return
    except InstagramAppiumError as e:
        logger.exception("Appium при входе")
        await message.answer(f"Ошибка Appium / Instagram: {e}")
        await state.clear()
        return
    except InstagramLoginError as e:
        await state.clear()
        await state.set_state(AddAccountStates.login)
        await message.answer(
            f"❌ {e}\n"
            "Отправь <b>логин</b> Instagram снова, затем бот попросит пароль.",
            parse_mode="HTML",
        )
        return
    except Exception as e:
        logger.exception("Ошибка логина Appium")
        await message.answer(f"Ошибка входа: {e}")
        await state.clear()
        return

    await state.update_data(
        ig_password=password,
        pending_new_account_id=new_id,
        android_user_id=android_user_id,
    )
    await state.set_state(AddAccountStates.name)
    await answer_with_retry(
        message,
        "Вход выполнен. Введи <b>понятное имя</b> этого аккаунта в боте "
        "(например: «Аккаунт 1», «Основной»):",
        parse_mode="HTML",
    )


@router.message(StateFilter(AddAccountStates.twofa))
async def add_account_twofa(message: Message, state: FSMContext) -> None:
    code = (message.text or "").strip().replace(" ", "")
    data = await state.get_data()
    username = data.get("ig_username")
    if not username:
        await message.answer("Сессия добавления сброшена. Начни заново.")
        await state.clear()
        return

    await message.answer("Проверяю код 2FA…")

    try:
        android_user_id = data.get("android_user_id")
        if isinstance(android_user_id, int):
            await switch_android_user(android_user_id)
            await prepare_android_profile_for_appium(android_user_id)
            await restart_instagram()
            await reset_shared_appium_client()
        client = await get_shared_appium_client()
        await client.login(username, "", twofa_code=code)
    except AdbError as e:
        await message.answer(f"ADB ошибка при переключении профиля: {e}")
        await state.clear()
        return
    except InstagramAppiumError as e:
        await message.answer(f"Проверка Instagram: {e}")
        await state.clear()
        return
    except InstagramLoginError as e:
        await message.answer(f"Код не подошёл: {e}")
        return
    except Exception as e:
        logger.exception("2FA не принят")
        await message.answer(f"Ошибка: {e}")
        await state.clear()
        return

    await state.set_state(AddAccountStates.name)
    await answer_with_retry(
        message,
        "Готово. Введи <b>имя аккаунта</b> для отображения в боте:",
        parse_mode="HTML",
    )


@router.message(StateFilter(AddAccountStates.name))
async def add_account_name(message: Message, state: FSMContext) -> None:
    uid = message.from_user.id
    name = (message.text or "").strip()
    if not name:
        await message.answer("Имя не может быть пустым.")
        return

    data = await state.get_data()
    new_id = data.get("pending_new_account_id")
    username = data.get("ig_username")
    android_user_id = data.get("android_user_id")

    if new_id is None or not username:
        await message.answer("Сессия истекла. Добавь аккаунт снова.")
        await state.clear()
        return

    record = AccountRecord(
        id=int(new_id),
        name=name,
        username=str(username),
        android_user_id=int(android_user_id) if isinstance(android_user_id, int) else None,
    )
    accounts = await load_accounts()
    accounts.append(record)
    await save_accounts(accounts)

    await state.update_data(selected_account_id=new_id)
    await state.set_state()
    await answer_with_retry(
        message,
        f"Аккаунт сохранён: <b>{name}</b> (@{record.username}).",
        parse_mode="HTML",
        reply_markup=kb_main_reply(),
    )
    await answer_with_retry(
        message,
        "Главное меню:",
        reply_markup=kb_account_actions(),
    )
    logger.info("Добавлен аккаунт id=%s user=%s", new_id, uid)


@router.message(F.text == "➕ Добавить новый Instagram-аккаунт")
async def add_account_from_button(message: Message, state: FSMContext) -> None:
    await state.set_state(AddAccountStates.login)
    await message.answer(
        "Введи <b>логин</b> Instagram:",
        parse_mode="HTML",
    )


@router.callback_query(F.data == "acc:disconnect")
async def cb_disconnect(query: CallbackQuery, state: FSMContext) -> None:
    await query.answer()
    data = await state.get_data()
    sel = data.get("selected_account_id")
    if not sel:
        await query.message.answer("Нет выбранного аккаунта.")
        return
    accounts = await load_accounts()
    acc = next((a for a in accounts if a.id == sel), None)
    if not acc:
        await query.message.answer("Аккаунт уже удалён.")
        await state.clear()
        return

    tag = f"account_{acc.id}"
    folder = EMULATOR_SESSIONS_DIR / tag
    if folder.is_dir():
        try:
            shutil.rmtree(folder)
        except OSError as e:
            logger.warning("Не удалось удалить %s: %s", folder, e)

    accounts = [a for a in accounts if a.id != sel]
    await save_accounts(accounts)
    await remove_queue_entry(int(sel))
    await state.clear()

    await reset_shared_appium_client()

    await query.message.answer(
        f"Аккаунт «{acc.name}» (@{acc.username}) отключён и удалён из списка.",
    )
    rest = await load_accounts()
    if not rest:
        await query.message.answer(
            "Аккаунтов больше нет.",
            reply_markup=kb_add_account_only(),
        )
    else:
        await query.message.answer(
            "Можешь выбрать другой аккаунт:",
            reply_markup=kb_main_reply(),
        )
