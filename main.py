"""
Точка входа: Telegram-бот + планировщик очереди Instagram Stories (Appium + Android).
Запуск: из каталога instagram_story_bot — python main.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramNetworkError
from aiogram.fsm.storage.memory import MemoryStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot.handlers import setup_routers  # noqa: E402
from bot.handlers.posting import posting_tick, set_bot_instance  # noqa: E402
from bot.utils import (  # noqa: E402
    POST_INTERVAL_MAX_SEC,
    POST_INTERVAL_MIN_SEC,
    ensure_directories,
)

LOG_DIR = ROOT / "logs"
LOG_FILE = LOG_DIR / "bot.log"


def configure_logging() -> None:
    ensure_directories()
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)

    fh = RotatingFileHandler(
        LOG_FILE,
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    fh.setLevel(logging.INFO)
    fh.setFormatter(fmt)

    root.handlers.clear()
    root.addHandler(ch)
    root.addHandler(fh)

    logging.getLogger("aiogram").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)


async def wait_until_telegram_ready(bot: Bot, log: logging.Logger) -> None:
    """
    Первый запрос к api.telegram.org (get_me) при старте polling часто падает
    при кратковременных сбоях сети / VPN / DNS на Windows.
    """
    try:
        retries = max(1, int(os.getenv("TELEGRAM_STARTUP_RETRIES", "40")))
    except ValueError:
        retries = 40
    try:
        base_delay = float(os.getenv("TELEGRAM_STARTUP_RETRY_DELAY_SEC", "3"))
    except ValueError:
        base_delay = 3.0
    delay = base_delay

    for attempt in range(1, retries + 1):
        try:
            me = await bot.get_me()
            uname = f"@{me.username}" if me.username else str(me.id)
            log.info("Связь с Telegram API есть, бот %s", uname)
            return
        except TelegramNetworkError as e:
            log.warning(
                "Нет доступа к api.telegram.org (%s/%s). Проверь интернет, VPN, DNS, файрвол. %s",
                attempt,
                retries,
                e,
            )
            if attempt >= retries:
                log.error(
                    "Исчерпаны попытки подключения к Telegram. "
                    "Коды WinError 121/1231 часто означают обрыв сети или блокировку хоста. "
                    "Попробуй: другой Wi‑Fi, отключить VPN, DNS 8.8.8.8, запуск от имени администратора не нужен."
                )
                raise
            await asyncio.sleep(delay)
            delay = min(delay * 1.2, 60.0)


async def main() -> None:
    load_dotenv(ROOT / ".env")
    configure_logging()
    log = logging.getLogger("main")

    token = (os.getenv("BOT_TOKEN") or "").strip()
    if not token:
        log.error("В .env не задан BOT_TOKEN")
        raise SystemExit(1)

    admin_raw = os.getenv("ADMIN_ID", "0").strip()
    try:
        admin_id = int(admin_raw)
    except ValueError:
        admin_id = 0
    if admin_id:
        log.info("ADMIN_ID=%s (для справки в логах)", admin_id)

    appium_url = (os.getenv("APPIUM_SERVER") or "http://127.0.0.1:4723").strip()
    emu = (os.getenv("EMULATOR_NAME") or "emulator-5554").strip()
    log.info("APPIUM_SERVER=%s EMULATOR_NAME=%s", appium_url, emu)

    bot = Bot(token=token)

    await wait_until_telegram_ready(bot, log)

    set_bot_instance(bot)

    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(setup_routers())

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        posting_tick,
        "interval",
        seconds=90,
        id="instagram_story_tick",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
    )
    scheduler.start()
    log.info(
        "Бот запущен. Пауза между Stories: %s–%s сек (случайно), ориентир ~%s–%s/час. Проверка очереди: 90 сек.",
        POST_INTERVAL_MIN_SEC,
        POST_INTERVAL_MAX_SEC,
        3600 // POST_INTERVAL_MAX_SEC,
        3600 // POST_INTERVAL_MIN_SEC,
    )

    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
