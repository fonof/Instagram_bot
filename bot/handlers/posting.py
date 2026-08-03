"""
Очередь в queue.json, автопостинг через Appium + APScheduler.
"""

from __future__ import annotations

import asyncio
import html
import logging
import time

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.utils import (
    POST_INTERVAL_MAX_SEC,
    POST_INTERVAL_MIN_SEC,
    AdbError,
    prepare_android_profile_for_appium,
    InstagramAppiumError,
    QueueEntry,
    get_account_post_lock,
    get_queue_entry,
    get_shared_appium_client,
    list_active_queues_for_user,
    load_accounts,
    load_queue_dict,
    random_error_pause_sec,
    random_post_interval_sec,
    remove_queue_entry,
    restart_instagram,
    reset_shared_appium_client,
    set_queue_entry,
    switch_android_user,
)

logger = logging.getLogger(__name__)

# Промежуточный отчёт в Telegram и в лог после каждых N успешных Stories
PROGRESS_REPORT_EVERY = 2

router = Router(name="posting")

_bot_ref: Bot | None = None


def set_bot_instance(bot: Bot) -> None:
    global _bot_ref
    _bot_ref = bot


async def _post_one_reel(reel_url: str) -> None:
    client = await get_shared_appium_client()
    await client.add_reel_to_story(reel_url)


def _queues_reply_markup(entries: list[QueueEntry]) -> InlineKeyboardMarkup | None:
    if not entries:
        return None
    rows: list[list[InlineKeyboardButton]] = []
    for e in entries:
        label = f"⏹ {e.account_name}"
        if len(label) > 64:
            label = label[:61] + "…"
        rows.append(
            [InlineKeyboardButton(text=label, callback_data=f"queue:stop:{e.account_id}")]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_queues_view(entries: list[QueueEntry]) -> tuple[str, InlineKeyboardMarkup | None]:
    """Текст и клавиатура для списка активных очередей."""
    if not entries:
        return (
            "📭 <b>Нет запущенных автопостингов.</b>\n"
            "После подтверждения «✅ Старт» очередь появится здесь.",
            None,
        )
    lines: list[str] = ["📊 <b>Запущенные автопостинги</b>\n"]
    for e in entries:
        n = len(e.reel_urls)
        c = e.cursor
        left = n - c
        nm = html.escape(e.account_name)
        un = html.escape(e.username)
        lines.append(
            f"• <b>{nm}</b> (@{un})\n"
            f"  прогресс: {c}/{n} · осталось: {left}"
        )
    return "\n\n".join(lines), _queues_reply_markup(entries)


@router.message(F.text == "📊 Запущенные автопостинги")
async def msg_active_queues(message: Message) -> None:
    entries = await list_active_queues_for_user(message.from_user.id)
    text, kb = build_queues_view(entries)
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "queue:list")
async def cb_queue_list(query: CallbackQuery) -> None:
    await query.answer()
    entries = await list_active_queues_for_user(query.from_user.id)
    text, kb = build_queues_view(entries)
    await query.message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data.startswith("queue:stop:"))
async def cb_queue_stop(query: CallbackQuery) -> None:
    try:
        aid = int(query.data.split(":")[-1])
    except ValueError:
        await query.answer("Некорректные данные", show_alert=True)
        return

    entry = await get_queue_entry(aid)
    if not entry or entry.telegram_user_id != query.from_user.id:
        await query.answer("Нет доступа к этой очереди", show_alert=True)
        return

    name = entry.account_name
    await remove_queue_entry(aid)
    await query.answer(f"Остановлено: {name[:180]}")

    entries = await list_active_queues_for_user(query.from_user.id)
    text, kb = build_queues_view(entries)
    try:
        await query.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await query.message.answer(
            f"⏹ Автопостинг для «{html.escape(name)}» остановлен, очередь снята.\n\n"
            + text,
            reply_markup=kb,
            parse_mode="HTML",
        )


@router.callback_query(F.data == "post:cancel")
async def cb_post_cancel(query: CallbackQuery, state: FSMContext) -> None:
    await query.answer("Отменено")
    await state.update_data(pending_reel_urls=[])
    await query.message.edit_text("Автопостинг отменён. Можешь загрузить ссылки снова.")


@router.callback_query(F.data == "post:confirm")
async def cb_post_confirm(query: CallbackQuery, state: FSMContext) -> None:
    await query.answer("Очередь создана")
    data = await state.get_data()
    sel = data.get("selected_account_id")
    urls = data.get("pending_reel_urls") or []

    if not sel or not urls:
        await query.message.edit_text("Нет данных для постинга. Начни загрузку ссылок снова.")
        return

    acc = next((a for a in await load_accounts() if a.id == int(sel)), None)
    if not acc:
        await query.message.edit_text("Аккаунт не найден.")
        return

    uid = query.from_user.id
    entry = QueueEntry(
        account_id=acc.id,
        telegram_user_id=uid,
        account_name=acc.name,
        username=acc.username,
        reel_urls=list(urls),
        cursor=0,
        last_post_ts=None,
        next_attempt_ts=None,
        status="active",
    )
    await set_queue_entry(entry)
    await state.update_data(pending_reel_urls=[])
    # Не ждём ближайший плановый тик (до 90 сек): запускаем немедленную проверку очереди.
    asyncio.create_task(posting_tick())

    total = len(entry.reel_urls)
    await query.message.edit_text(
        f"✅ Очередь из <b>{total}</b> рилсов сохранена.\n"
        f"Аккаунт: <b>{acc.name}</b> (@{acc.username})\n"
        f"Пауза между Stories: {POST_INTERVAL_MIN_SEC}–{POST_INTERVAL_MAX_SEC} сек (случайно), "
        f"ориентир ~{3600 // POST_INTERVAL_MAX_SEC}–{3600 // POST_INTERVAL_MIN_SEC} в час. "
        f"Отчёт каждые {PROGRESS_REPORT_EVERY} публикации.\n"
        f"Убедись, что эмулятор с Instagram запущен и Appium слушает на APPIUM_SERVER.",
        parse_mode="HTML",
    )


async def posting_tick() -> None:
    bot = _bot_ref
    if bot is None:
        return

    now = time.time()
    qraw = await load_queue_dict()

    for _key, raw in list(qraw.items()):
        try:
            entry = QueueEntry.model_validate(raw)
        except Exception as e:
            logger.error("Плохая запись очереди: %s", e)
            continue

        if entry.cursor >= len(entry.reel_urls):
            await remove_queue_entry(entry.account_id)
            continue

        # Старые очереди: после успеха next_attempt_ts не задавался — задаём задержку до следующего рилса
        if (
            entry.last_post_ts is not None
            and entry.next_attempt_ts is None
            and entry.cursor < len(entry.reel_urls)
        ):
            entry.next_attempt_ts = entry.last_post_ts + float(random_post_interval_sec())
            await set_queue_entry(entry)

        if entry.next_attempt_ts and now < entry.next_attempt_ts:
            continue

        acc = next((a for a in await load_accounts() if a.id == entry.account_id), None)
        if not acc:
            logger.warning("Аккаунт %s удалён, очищаю очередь", entry.account_id)
            await remove_queue_entry(entry.account_id)
            continue

        lock = get_account_post_lock(entry.account_id)
        async with lock:
            # Переключаем Android user под нужный Instagram-аккаунт (если задан).
            if acc.android_user_id is not None:
                try:
                    await switch_android_user(int(acc.android_user_id))
                    await prepare_android_profile_for_appium(int(acc.android_user_id))
                    await restart_instagram()
                    await reset_shared_appium_client()
                except AdbError as e:
                    logger.warning("ADB: не удалось переключить профиль (%s): %s", acc.android_user_id, e)
                    pause = random_error_pause_sec()
                    entry.next_attempt_ts = now + pause
                    await set_queue_entry(entry)
                    try:
                        await bot.send_message(
                            entry.telegram_user_id,
                            f"⏸ Не удалось переключить Android-профиль для Stories: {e}\n"
                            f"Пауза ~{pause // 60} мин. ({acc.name})",
                        )
                    except Exception as send_e:
                        logger.warning("Не удалось отправить сообщение в Telegram: %s", send_e)
                    continue

            fresh = await get_queue_entry(entry.account_id)
            if not fresh or fresh.cursor >= len(fresh.reel_urls):
                continue
            entry = fresh
            url = entry.reel_urls[entry.cursor]
            total = len(entry.reel_urls)

            try:
                await _post_one_reel(url)
            except InstagramAppiumError as e:
                logger.warning("Appium Stories: %s", e)
                pause = random_error_pause_sec()
                entry.next_attempt_ts = now + pause
                await set_queue_entry(entry)
                try:
                    await bot.send_message(
                        entry.telegram_user_id,
                        f"⏸ Не удалось опубликовать в Stories (Appium): {e}\n"
                        f"Пауза ~{pause // 60} мин. ({acc.name})",
                    )
                except Exception as send_e:
                    logger.warning("Не удалось отправить сообщение в Telegram: %s", send_e)
                continue
            except Exception as e:
                logger.exception("Ошибка публикации Stories url=%s", url[:60])
                pause = random_error_pause_sec()
                entry.next_attempt_ts = now + pause
                await set_queue_entry(entry)
                try:
                    await bot.send_message(
                        entry.telegram_user_id,
                        f"⚠️ Ошибка: {e}\nПауза ~{pause // 60} мин. ({acc.name})",
                    )
                except Exception as send_e:
                    logger.warning("Не удалось отправить сообщение в Telegram: %s", send_e)
                continue

            entry.cursor += 1
            entry.last_post_ts = time.time()
            if entry.cursor < len(entry.reel_urls):
                entry.next_attempt_ts = entry.last_post_ts + float(random_post_interval_sec())
            else:
                entry.next_attempt_ts = None
            entry.status = "active"

            done = entry.cursor
            if entry.cursor >= len(entry.reel_urls):
                await remove_queue_entry(entry.account_id)
            else:
                await set_queue_entry(entry)

        try:
            if done >= total:
                await bot.send_message(
                    entry.telegram_user_id,
                    f"🎉 Автопостинг завершён: {total}/{total} для «{entry.account_name}».",
                )
            elif done % PROGRESS_REPORT_EVERY == 0:
                logger.info(
                    "Stories: промежуточный отчёт %s/%s (%s), аккаунт id=%s",
                    done,
                    total,
                    entry.account_name,
                    entry.account_id,
                )
                await bot.send_message(
                    entry.telegram_user_id,
                    f"✅ Опубликовано {done}/{total} в Stories ({entry.account_name})",
                )
        except Exception as e:
            logger.warning("Не удалось отправить отчёт в Telegram: %s", e)

        logger.info(
            "Stories: аккаунт %s, прогресс %s/%s",
            entry.account_id,
            done,
            total,
        )
