"""
Файлы данных: accounts.json, queue.json, разбор ссылок на Reels.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from pathlib import Path
from typing import Any

import aiofiles
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = ROOT_DIR / "data"
EMULATOR_SESSIONS_DIR = ROOT_DIR / "emulator_sessions"
ACCOUNTS_PATH = DATA_DIR / "accounts.json"
QUEUE_PATH = DATA_DIR / "queue.json"
LOGS_DIR = ROOT_DIR / "logs"

# Пауза между публикациями в Stories (сек), случайно в диапазоне
POST_INTERVAL_MIN_SEC = 60
POST_INTERVAL_MAX_SEC = 120


def random_post_interval_sec() -> int:
    return random.randint(POST_INTERVAL_MIN_SEC, POST_INTERVAL_MAX_SEC)
ERROR_PAUSE_MIN_SEC = 600
ERROR_PAUSE_MAX_SEC = 900
MAX_LINKS = 100

INSTAGRAM_URL_RE = re.compile(
    r"https?://(?:www\.)?instagram\.com/(?:reel|reels|p|tv)/[\w-]+/?[^\s]*",
    re.IGNORECASE,
)

_queue_lock = asyncio.Lock()
_accounts_lock = asyncio.Lock()
_account_post_locks: dict[int, asyncio.Lock] = {}


def get_account_post_lock(account_id: int) -> asyncio.Lock:
    if account_id not in _account_post_locks:
        _account_post_locks[account_id] = asyncio.Lock()
    return _account_post_locks[account_id]


class AccountRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    name: str
    username: str
    # Android multi-user profile id внутри эмулятора. Если None — используем Owner (0).
    android_user_id: int | None = None


class QueueEntry(BaseModel):
    account_id: int
    telegram_user_id: int
    account_name: str
    username: str
    reel_urls: list[str] = Field(default_factory=list)
    cursor: int = 0
    last_post_ts: float | None = None
    next_attempt_ts: float | None = None
    status: str = "active"


def ensure_directories() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    EMULATOR_SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)


async def read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    async with aiofiles.open(path, "r", encoding="utf-8") as f:
        raw = await f.read()
    if not raw.strip():
        return default
    return json.loads(raw)


async def write_json_atomic(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    text = json.dumps(data, ensure_ascii=False, indent=2)
    async with aiofiles.open(tmp, "w", encoding="utf-8") as f:
        await f.write(text)
    tmp.replace(path)


async def load_accounts() -> list[AccountRecord]:
    async with _accounts_lock:
        raw = await read_json(ACCOUNTS_PATH, [])
    if not isinstance(raw, list):
        return []
    out: list[AccountRecord] = []
    for item in raw:
        try:
            out.append(AccountRecord.model_validate(item))
        except Exception as e:
            logger.warning("Пропуск некорректной записи аккаунта: %s", e)
    return out


async def save_accounts(accounts: list[AccountRecord]) -> None:
    async with _accounts_lock:
        data = [a.model_dump() for a in accounts]
        await write_json_atomic(ACCOUNTS_PATH, data)


async def next_account_id() -> int:
    accounts = await load_accounts()
    if not accounts:
        return 1
    return max(a.id for a in accounts) + 1


async def load_queue_dict() -> dict[str, dict[str, Any]]:
    async with _queue_lock:
        raw = await read_json(QUEUE_PATH, {})
    if not isinstance(raw, dict):
        return {}
    return raw


async def set_queue_entry(entry: QueueEntry) -> None:
    async with _queue_lock:
        q = await read_json(QUEUE_PATH, {})
        if not isinstance(q, dict):
            q = {}
        q[str(entry.account_id)] = entry.model_dump()
        await write_json_atomic(QUEUE_PATH, q)


async def remove_queue_entry(account_id: int) -> None:
    async with _queue_lock:
        q = await read_json(QUEUE_PATH, {})
        if not isinstance(q, dict):
            q = {}
        q.pop(str(account_id), None)
        await write_json_atomic(QUEUE_PATH, q)


async def get_queue_entry(account_id: int) -> QueueEntry | None:
    q = await load_queue_dict()
    raw = q.get(str(account_id))
    if not raw:
        return None
    try:
        return QueueEntry.model_validate(raw)
    except Exception:
        return None


async def list_active_queues_for_user(telegram_user_id: int) -> list[QueueEntry]:
    """Очереди с незавершённым постингом для данного пользователя Telegram."""
    q = await load_queue_dict()
    out: list[QueueEntry] = []
    for raw in q.values():
        try:
            e = QueueEntry.model_validate(raw)
        except Exception:
            continue
        if e.telegram_user_id != telegram_user_id:
            continue
        if e.cursor >= len(e.reel_urls):
            continue
        out.append(e)
    out.sort(key=lambda x: x.account_id)
    return out


def parse_instagram_urls(text: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for m in INSTAGRAM_URL_RE.finditer(text or ""):
        u = m.group(0).rstrip(").,;]")
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def random_error_pause_sec() -> int:
    import random

    return random.randint(ERROR_PAUSE_MIN_SEC, ERROR_PAUSE_MAX_SEC)
