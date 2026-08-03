from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AndroidUser:
    user_id: int
    name: str
    flags: str
    running: bool


class AdbError(RuntimeError):
    pass


def _adb_exe() -> str:
    return (os.getenv("ADB_PATH") or "adb").strip()


def _device_serial() -> str:
    return (os.getenv("EMULATOR_NAME") or "emulator-5554").strip()


def _instagram_pkg() -> str:
    return (os.getenv("INSTAGRAM_APP_PACKAGE") or "com.instagram.android").strip()


def _instagram_activity() -> str:
    return (os.getenv("INSTAGRAM_APP_ACTIVITY") or "com.instagram.mainactivity.LauncherActivity").strip()


async def adb_shell(*args: str, timeout_sec: float = 25.0) -> str:
    exe = _adb_exe()
    serial = _device_serial()
    cmd = [exe, "-s", serial, "shell", *args]

    def _run() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )

    cp = await asyncio.to_thread(_run)
    out = (cp.stdout or "").strip()
    err = (cp.stderr or "").strip()
    if cp.returncode != 0:
        raise AdbError(f"ADB ошибка (code={cp.returncode}): {err or out or 'no output'}")
    return out


_USERINFO_RE = re.compile(r"UserInfo\{(\d+):([^:}]+):([^}]+)\}")


async def list_android_users() -> list[AndroidUser]:
    raw = await adb_shell("pm", "list", "users")
    users: list[AndroidUser] = []
    for line in raw.splitlines():
        line = line.strip()
        m = _USERINFO_RE.search(line)
        if not m:
            continue
        uid = int(m.group(1))
        name = m.group(2)
        flags = m.group(3)
        running = "running" in line
        users.append(AndroidUser(user_id=uid, name=name, flags=flags, running=running))
    return users


async def ensure_android_user(label: str) -> int:
    """
    Возвращает user_id для профиля с name=label. Если нет — создаёт.
    """
    for u in await list_android_users():
        if u.name == label:
            return u.user_id

    out = await adb_shell("pm", "create-user", label)
    m = re.search(r"created user id (\d+)", out, re.IGNORECASE)
    if not m:
        raise AdbError(f"Не удалось распарсить user id из ответа: {out!r}")
    return int(m.group(1))


async def switch_android_user(user_id: int) -> None:
    # На AVD обычно работает am switch-user, но оставляем fallback.
    try:
        await adb_shell("am", "switch-user", str(user_id), timeout_sec=20.0)
    except AdbError:
        await adb_shell("cmd", "activity", "switch-user", str(user_id), timeout_sec=20.0)
    await wait_active_user(user_id, timeout_sec=20.0)


async def get_current_user_id() -> int | None:
    # "am get-current-user" обычно возвращает просто число.
    try:
        out = await adb_shell("am", "get-current-user", timeout_sec=10.0)
    except AdbError:
        return None
    m = re.search(r"(\d+)", out)
    return int(m.group(1)) if m else None


def _after_switch_stabilize_sec() -> float:
    try:
        return float(os.getenv("APPIUM_AFTER_SWITCH_USER_SEC", "5"))
    except ValueError:
        return 5.0


async def wait_active_user(user_id: int, timeout_sec: float = 20.0) -> None:
    end = asyncio.get_running_loop().time() + timeout_sec
    while asyncio.get_running_loop().time() < end:
        cur = await get_current_user_id()
        if cur == user_id:
            # Буфер после switch-user (медленные AVD / secondary user).
            await asyncio.sleep(_after_switch_stabilize_sec())
            return
        await asyncio.sleep(0.6)
    raise AdbError(f"Не удалось дождаться активации user #{user_id}")


async def wait_boot_completed(timeout_sec: float | None = None) -> None:
    """Ждём sys.boot_completed=1 (после switch-user на слабых эмуляторах может задерживаться)."""
    if timeout_sec is None:
        try:
            timeout_sec = float(os.getenv("APPIUM_BOOT_WAIT_SEC", "120"))
        except ValueError:
            timeout_sec = 120.0
    end = asyncio.get_running_loop().time() + timeout_sec
    while asyncio.get_running_loop().time() < end:
        try:
            out = await adb_shell("getprop", "sys.boot_completed", timeout_sec=12.0)
            if out.strip() == "1":
                await asyncio.sleep(0.6)
                return
        except AdbError:
            pass
        await asyncio.sleep(1.2)
    raise AdbError(f"Таймаут ожидания sys.boot_completed=1 ({timeout_sec:.0f} c)")


async def warm_appium_settings_app() -> None:
    """Поднять io.appium.settings до запроса сессии Appium (иначе 'not running after 30000ms')."""
    attempts: list[tuple[str, ...]] = [
        ("am", "start", "-W", "-n", "io.appium.settings/io.appium.settings.Settings"),
        ("am", "start", "-W", "-n", "io.appium.settings/.Settings"),
        ("monkey", "-p", "io.appium.settings", "-c", "android.intent.category.LAUNCHER", "1"),
    ]
    for args in attempts:
        try:
            await adb_shell(*args, timeout_sec=35.0)
            logger.info("Appium helper: io.appium.settings запущен (%s)", " ".join(args[:4]))
            return
        except AdbError as e:
            logger.debug("warm_appium_settings: %s: %s", args, e)
            continue
    logger.warning("Не удалось явно запустить io.appium.settings (Appium поднимет сам)")


async def wait_for_appium_settings_pid(timeout_sec: float | None = None) -> bool:
    """
    Ждём, пока в системе появится процесс io.appium.settings (pidof).
    Возвращает True, если процесс найден; False по таймауту (не падаем — Appium сам добьёт).
    """
    if timeout_sec is None:
        try:
            timeout_sec = float(os.getenv("APPIUM_SETTINGS_PID_WAIT_SEC", "90"))
        except ValueError:
            timeout_sec = 90.0
    pkg = "io.appium.settings"
    end = asyncio.get_running_loop().time() + timeout_sec
    while asyncio.get_running_loop().time() < end:
        try:
            out = await adb_shell("pidof", pkg, timeout_sec=12.0)
            if out.strip():
                logger.info("Appium helper: pidof %s OK (%s)", pkg, out.strip()[:80])
                return True
        except AdbError:
            pass
        await asyncio.sleep(2.0)
    logger.warning("Таймаут pidof %s за %.0f c", pkg, timeout_sec)
    return False


async def prepare_android_profile_for_appium(user_id: int) -> None:
    """
    После switch-user: пакеты Appium в профиле, дождаться boot, прогреть Settings app.
    """
    await ensure_appium_helpers_for_user(user_id)
    await wait_boot_completed()
    # Несколько попыток прогрева — на медленном AVD первый старт может не поднять процесс.
    for _ in range(3):
        await warm_appium_settings_app()
        if await wait_for_appium_settings_pid(timeout_sec=35.0):
            break
        await asyncio.sleep(5.0)
    extra = _after_switch_stabilize_sec()
    if extra > 0:
        await asyncio.sleep(min(extra, 15.0))


async def restart_instagram() -> None:
    pkg = _instagram_pkg()
    act = _instagram_activity()
    # force-stop может вернуть stderr даже при успехе на некоторых образах — не критично.
    try:
        await adb_shell("am", "force-stop", pkg, timeout_sec=20.0)
    except AdbError:
        pass
    # На некоторых образах monkey в secondary user может падать с code=127.
    # Сначала пробуем явный старт activity, затем fallback на monkey.
    try:
        await adb_shell("am", "start", "-n", f"{pkg}/{act}", timeout_sec=25.0)
        return
    except AdbError:
        pass
    await adb_shell("monkey", "-p", pkg, "-c", "android.intent.category.LAUNCHER", "1", timeout_sec=25.0)


async def install_existing_for_user(package_name: str, user_id: int) -> None:
    # На части образов доступен cmd package, на части pm.
    try:
        await adb_shell("cmd", "package", "install-existing", "--user", str(user_id), package_name, timeout_sec=20.0)
        return
    except AdbError:
        pass
    await adb_shell("pm", "install-existing", "--user", str(user_id), package_name, timeout_sec=20.0)


async def ensure_appium_helpers_for_user(user_id: int) -> None:
    """
    Убеждаемся, что пакеты Appium доступны в выбранном Android user.
    Иначе UiAutomator2 может падать с "instrumentation process cannot be initialized".
    """
    for pkg in (
        "io.appium.settings",
        "io.appium.uiautomator2.server",
        "io.appium.uiautomator2.server.test",
    ):
        try:
            await install_existing_for_user(pkg, user_id)
        except AdbError:
            # Не валим процесс, если пакет уже есть/недоступен на данном образе.
            continue

