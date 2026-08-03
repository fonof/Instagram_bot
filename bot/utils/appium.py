"""
Appium + Instagram (Android): подключение, логин, репост Reels в Stories.
Синхронный Appium оборачивается в asyncio.to_thread.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import subprocess
import time
from typing import Optional

from appium import webdriver
from appium.options.android import UiAutomator2Options
from appium.webdriver.common.appiumby import AppiumBy
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

logger = logging.getLogger(__name__)

STORY_RETRIES = 5
# Паузы «как человек» (сек). Можно переопределить: APPIUM_UI_DELAY_MIN / APPIUM_UI_DELAY_MAX
_DEFAULT_DELAY_MIN = 1.5
_DEFAULT_DELAY_MAX = 4.0
# Таймаут без команд к Appium; если истёк — сервер гасит сессию и может убить UiAutomator2 для всех клиентов на устройстве
_DEFAULT_NEW_CMD_TIMEOUT = 3600
_DEFAULT_LOGIN_RESULT_TIMEOUT = 45.0


def _new_command_timeout_sec() -> int:
    try:
        return int(os.getenv("APPIUM_NEW_COMMAND_TIMEOUT", str(_DEFAULT_NEW_CMD_TIMEOUT)))
    except ValueError:
        return _DEFAULT_NEW_CMD_TIMEOUT


COMMAND_TIMEOUT_SEC = _new_command_timeout_sec()


def _login_result_timeout_sec() -> float:
    try:
        return float(os.getenv("APPIUM_LOGIN_RESULT_TIMEOUT_SEC", str(_DEFAULT_LOGIN_RESULT_TIMEOUT)))
    except ValueError:
        return _DEFAULT_LOGIN_RESULT_TIMEOUT


def _delay_bounds() -> tuple[float, float]:
    try:
        lo = float(os.getenv("APPIUM_UI_DELAY_MIN", str(_DEFAULT_DELAY_MIN)))
        hi = float(os.getenv("APPIUM_UI_DELAY_MAX", str(_DEFAULT_DELAY_MAX)))
    except ValueError:
        lo, hi = _DEFAULT_DELAY_MIN, _DEFAULT_DELAY_MAX
    if lo > hi:
        lo, hi = hi, lo
    return lo, hi


class InstagramAppiumError(Exception):
    """Общая ошибка сценария Appium / Instagram."""


class Instagram2FARequired(Exception):
    """Нужен код 2FA (введи в боте)."""


class InstagramLoginError(Exception):
    """Не удалось войти (неверные данные или разметка экрана)."""


def _human_delay() -> None:
    lo, hi = _delay_bounds()
    time.sleep(random.uniform(lo, hi))


def _short_pause() -> None:
    """Короткая пауза между микрошагами UI (без длинного «раздумья»)."""
    time.sleep(random.uniform(0.4, 1.2))


def _retry_pause() -> None:
    """Между неудачными попытками add_reel_to_story."""
    time.sleep(random.uniform(2.0, 5.0))


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _build_options(
    server_url: str,
    udid: str,
    app_package: str,
    app_activity: str,
) -> UiAutomator2Options:
    # На secondary Android users (user 10, 11, ...) UnicodeIME часто недоступен:
    # "io.appium.settings/.UnicodeIME cannot be enabled for user #10".
    # Поэтому по умолчанию НЕ включаем unicodeKeyboard/resetKeyboard.
    use_unicode_ime = (os.getenv("APPIUM_USE_UNICODE_IME", "0").strip().lower() in {"1", "true", "yes"})
    # Таймауты ADB / UiAutomator2 / io.appium.settings — на слабых AVD после switch-user 30 c мало.
    adb_ms = _int_env("APPIUM_ADB_EXEC_TIMEOUT_MS", 180000)
    uia2_launch_ms = _int_env("APPIUM_UIA2_SERVER_LAUNCH_TIMEOUT_MS", 180000)
    settings_wait_ms = _int_env("APPIUM_SETTINGS_APP_WAIT_MS", 120000)

    caps = {
        "platformName": "Android",
        "appium:automationName": "UiAutomator2",
        "appium:udid": udid,
        "appium:appPackage": app_package,
        "appium:appActivity": app_activity,
        "appium:noReset": True,
        "appium:newCommandTimeout": _new_command_timeout_sec(),
        "appium:adbExecTimeout": adb_ms,
        "appium:uiautomator2ServerLaunchTimeout": uia2_launch_ms,
        "appium:settingsAppWaitTimeout": settings_wait_ms,
    }
    if use_unicode_ime:
        caps["appium:unicodeKeyboard"] = True
        caps["appium:resetKeyboard"] = True
    o = UiAutomator2Options()
    o.load_capabilities(caps)
    return o


_shared: Optional["InstagramAppiumClient"] = None


async def get_shared_appium_client() -> "InstagramAppiumClient":
    """Один клиент на процесс (один эмулятор)."""
    global _shared
    if _shared is None:
        _shared = InstagramAppiumClient()
    await _shared.connect()
    return _shared


async def reset_shared_appium_client() -> None:
    global _shared
    if _shared is not None:
        await _shared.disconnect()
        _shared = None


class InstagramAppiumClient:
    def __init__(self) -> None:
        self.driver = None
        self.server_url = (os.getenv("APPIUM_SERVER") or "http://127.0.0.1:4723").strip()
        self.udid = (os.getenv("EMULATOR_NAME") or "emulator-5554").strip()
        self.app_package = (os.getenv("INSTAGRAM_APP_PACKAGE") or "com.instagram.android").strip()
        self.app_activity = (
            os.getenv("INSTAGRAM_APP_ACTIVITY") or "com.instagram.mainactivity.LauncherActivity"
        ).strip()
        self.adb = (os.getenv("ADB_PATH") or "adb").strip()

    async def _recover_adb_offline(self) -> None:
        """
        Кратковременные offline состояния adb после switch-user лечим мягким reconnect.
        """
        def _run(cmd: list[str], timeout: float = 20.0) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )

        await asyncio.to_thread(_run, [self.adb, "reconnect"], 15.0)
        await asyncio.to_thread(_run, [self.adb, "-s", self.udid, "wait-for-device"], 30.0)

        # Подождать, пока state станет device (а не offline).
        deadline = time.time() + 20.0
        while time.time() < deadline:
            cp = await asyncio.to_thread(_run, [self.adb, "-s", self.udid, "get-state"], 8.0)
            state = (cp.stdout or "").strip().lower()
            if cp.returncode == 0 and state == "device":
                logger.info("ADB: устройство %s снова online", self.udid)
                return
            await asyncio.sleep(0.8)
        logger.warning("ADB: не удалось подтвердить online state для %s", self.udid)

    def _apply_driver_tuning_sync(self) -> None:
        try:
            self.driver.implicitly_wait(0)
        except Exception as e:
            logger.debug("Appium: implicitly_wait(0): %s", e)
        try:
            self.driver.update_settings(
                {
                    "waitForIdleTimeout": 100,
                    "actionAcknowledgmentTimeout": 500,
                }
            )
        except Exception as e:
            logger.debug("Appium: update_settings: %s", e)

    def _uia2_alive(self) -> bool:
        """
        current_package идёт через adb — не проверяет UiAutomator2.
        Если другая сессия Appium таймаутится, она может убить io.appium.uiautomator2.server,
        и find_elements начнут отдавать «instrumentation process is not running».
        """
        if not self.driver:
            return False
        try:
            self.driver.implicitly_wait(0)
            self.driver.find_elements(
                AppiumBy.ANDROID_UIAUTOMATOR,
                'new UiSelector().resourceId("org.appium.healthcheck.nonexistent")',
            )
            return True
        except WebDriverException as e:
            logger.warning("Appium: UiAutomator2 не отвечает (нужен перезапуск сессии): %s", e)
            return False
        except Exception as e:
            logger.warning("Appium: проверка UiAutomator2: %s", e)
            return False

    async def _ensure_live_session(self) -> None:
        """Перед шагами с find_element убеждаемся, что UiAutomator2 на устройстве жив."""
        if self.driver is None:
            await self.connect()
            return
        ok = await asyncio.to_thread(self._uia2_alive)
        if ok:
            return
        logger.warning("Appium: пересоздаю драйвер — UiAutomator2 был недоступен")
        await self.disconnect()
        await self.connect()

    async def connect(self) -> None:
        if self.driver is not None:
            ok = await asyncio.to_thread(self._uia2_alive)
            if ok:
                await asyncio.to_thread(self._apply_driver_tuning_sync)
                logger.info("Appium: сессия уже активна (udid=%s)", self.udid)
                return
            logger.warning("Appium: сессия без UiAutomator2 — переподключаемся")
            await self.disconnect()

        def _open():
            logger.info(
                "Appium: подключение к %s, устройство %s, пакет %s",
                self.server_url,
                self.udid,
                self.app_package,
            )
            opts = _build_options(self.server_url, self.udid, self.app_package, self.app_activity)
            return webdriver.Remote(self.server_url, options=opts)

        try:
            self.driver = await asyncio.to_thread(_open)
        except Exception as e:
            msg = str(e).lower()
            if "device offline" in msg:
                logger.warning("ADB offline при подключении Appium, пробую восстановить и повторить")
                await self._recover_adb_offline()
                try:
                    self.driver = await asyncio.to_thread(_open)
                except Exception as e2:
                    raise InstagramAppiumError(
                        f"Не удалось подключиться к Appium ({self.server_url}). "
                        f"Запущен ли сервер (appium), эмулятор и Instagram? Ошибка: {e2}"
                    ) from e2
            elif "appium settings" in msg and "not running" in msg:
                from bot.utils import emulator as _emu

                last = e
                for attempt in range(1, 5):
                    logger.warning(
                        "Appium Settings не успел за таймаут сервера, попытка %s/4 (прогрев + пауза)",
                        attempt,
                    )
                    await _emu.warm_appium_settings_app()
                    await _emu.wait_for_appium_settings_pid(timeout_sec=25.0)
                    await asyncio.sleep(12.0 + attempt * 5.0)
                    try:
                        self.driver = await asyncio.to_thread(_open)
                        last = None
                        break
                    except Exception as e2:
                        last = e2
                        msg2 = str(e2).lower()
                        if "appium settings" not in msg2 or attempt >= 4:
                            raise InstagramAppiumError(
                                f"Не удалось подключиться к Appium ({self.server_url}). "
                                f"Увеличь APPIUM_SETTINGS_APP_WAIT_MS / APPIUM_AFTER_SWITCH_USER_SEC в .env "
                                f"или дождись полной загрузки эмулятора. Ошибка: {e2}"
                            ) from e2
            else:
                raise InstagramAppiumError(
                    f"Не удалось подключиться к Appium ({self.server_url}). "
                    f"Запущен ли сервер (appium), эмулятор и Instagram? Ошибка: {e}"
                ) from e

        await asyncio.to_thread(self._apply_driver_tuning_sync)
        logger.info("Appium: драйвер создан.")

    async def disconnect(self) -> None:
        if not self.driver:
            return

        def _q():
            try:
                self.driver.quit()
            except Exception as e:
                logger.warning("Appium: quit: %s", e)

        await asyncio.to_thread(_q)
        self.driver = None
        logger.info("Appium: драйвер закрыт.")

    async def login(self, username: str, password: str, twofa_code: Optional[str] = None) -> None:
        await self.connect()
        if twofa_code:
            await self._run_sync(self._submit_2fa_sync, twofa_code)
            logger.info("Appium: вход с 2FA завершён")
            return
        await self._run_sync(self._login_sync, username, password)
        logger.info("Appium: вход выполнен для %s", username)

    def _submit_2fa_sync(self, code: str) -> None:
        d = self.driver
        wait = WebDriverWait(d, 30)
        box = wait.until(
            EC.presence_of_element_located((AppiumBy.XPATH, "//android.widget.EditText"))
        )
        box.clear()
        box.send_keys(code.replace(" ", ""))
        _human_delay()
        for xp in (
            "//*[@text='Confirm']",
            "//*[@text='Подтвердить']",
            "//*[@text='Continue']",
            "//android.widget.Button",
        ):
            try:
                d.find_element(AppiumBy.XPATH, xp).click()
                logger.info("Appium: нажато подтверждение 2FA (%s)", xp[:40])
                break
            except Exception:
                continue
        time.sleep(5)
        self._dismiss_save_login_info_prompt(timeout_sec=10.0)

    def _find_login_edittexts(self):
        """Все видимые поля ввода на экране логина (Instagram может использовать не только EditText)."""
        d = self.driver
        seen: set[str] = set()
        merged = []
        queries: list[tuple] = [
            (AppiumBy.CLASS_NAME, "android.widget.EditText"),
            (AppiumBy.XPATH, "//android.widget.EditText"),
            (AppiumBy.XPATH, "//android.widget.AutoCompleteTextView"),
            (AppiumBy.XPATH, "//*[contains(@class,'EditText')]"),
        ]
        for by, sel in queries:
            try:
                for el in d.find_elements(by, sel):
                    try:
                        eid = getattr(el, "id", None) or str(id(el))
                        if eid in seen:
                            continue
                        if el.is_displayed():
                            seen.add(eid)
                            merged.append(el)
                    except Exception:
                        continue
            except Exception:
                continue
        merged.sort(key=lambda e: e.location["y"])
        return merged

    def _dismiss_empty_login_dialog(self) -> bool:
        """
        Если нажали синюю «Log in» с пустыми полями — диалог
        «Enter your username, email or mobile number…» с кнопкой OK.
        Без закрытия полей в дереве не видны.
        """
        d = self.driver
        for xp in (
            "//*[@text='OK']",
            "//android.widget.Button[@text='OK']",
            "//*[@text='Ok']",
        ):
            try:
                for el in d.find_elements(AppiumBy.XPATH, xp):
                    try:
                        if el.is_displayed():
                            el.click()
                            logger.info("Appium: закрыт диалог «введите логин» (OK)")
                            time.sleep(0.55)
                            return True
                    except Exception:
                        continue
            except Exception:
                continue
        return False

    def _wait_for_two_login_fields(self, timeout_sec: float = 28.0):
        end = time.time() + timeout_sec
        while time.time() < end:
            self._dismiss_empty_login_dialog()
            eds = self._find_login_edittexts()
            if len(eds) >= 2:
                return eds
            time.sleep(0.4)
        return self._find_login_edittexts()

    def _pick_user_pass_elements(self, edits: list):
        non_pwd = []
        pwd_only = []
        for el in edits:
            try:
                if el.get_attribute("password") == "true":
                    pwd_only.append(el)
                else:
                    non_pwd.append(el)
            except Exception:
                non_pwd.append(el)
        non_pwd.sort(key=lambda e: e.location["y"])
        pwd_only.sort(key=lambda e: e.location["y"])
        if non_pwd and pwd_only:
            return non_pwd[0], pwd_only[0]
        if len(edits) >= 2:
            edits = sorted(edits, key=lambda e: e.location["y"])
            return edits[0], edits[1]
        raise InstagramLoginError("Не удалось сопоставить поля логина и пароля.")

    def _login_sync(self, username: str, password: str) -> None:
        """
        username/password — это те же строки, что пользователь ввёл в Telegram
        (логин Instagram → поле username, пароль → поле password).
        """
        username = (username or "").strip()
        password = password or ""
        if not username or not password:
            raise InstagramLoginError(
                "Логин или пароль пустые. Введи их снова в боте (сначала логин, затем пароль)."
            )

        d = self.driver
        _short_pause()
        self._dismiss_empty_login_dialog()
        edits = self._find_login_edittexts()

        # Важно: не жать «Log in», если поля уже есть — иначе попадём в синюю кнопку отправки с пустыми полями.
        if len(edits) < 2:
            logger.info("Appium: полей нет — открываю форму входа (кнопка Log in на приветствии)")
            self._tap_if_visible(
                [
                    "//*[@text='Log in']",
                    "//*[@text='Войти']",
                    "//*[contains(@text,'Log in')]",
                ],
                timeout=10,
            )
            time.sleep(1.0)
            self._dismiss_empty_login_dialog()
            edits = self._wait_for_two_login_fields(28.0)

        self._dismiss_empty_login_dialog()
        edits = self._find_login_edittexts()
        if len(edits) < 2:
            raise InstagramLoginError(
                "Не найдены два поля ввода (логин/пароль). Открой экран входа Instagram вручную и повтори."
            )

        user_el, pass_el = self._pick_user_pass_elements(edits)
        logger.info(
            "Appium: ввожу в поля Instagram данные из бота (логин: %s, длина пароля: %s симв.)",
            username,
            len(password),
        )
        user_el.clear()
        user_el.send_keys(username)
        _short_pause()
        pass_el.clear()
        pass_el.send_keys(password)
        _short_pause()
        logger.info("Appium: логин и пароль отправлены в поля, нажимаю вход")

        # Отправка: сначала именно кнопка (не совпасть с другими «Log in» на экране)
        if not self._tap_if_visible(
            [
                "//android.widget.Button[@text='Log in']",
                "//android.widget.Button[@text='Войти']",
                "//*[@text='Log in']",
                "//*[@text='Войти']",
            ],
            timeout=18,
        ):
            raise InstagramLoginError("Не найдена кнопка входа после ввода логина и пароля.")

        self._wait_login_result(timeout_sec=_login_result_timeout_sec())

    def _wait_login_result(self, timeout_sec: float = 18.0) -> None:
        d = self.driver
        end = time.time() + timeout_sec
        while time.time() < end:
            bad_creds_reason = self._detect_invalid_credentials_reason()
            if bad_creds_reason:
                self._dismiss_generic_ok_dialog()
                raise InstagramLoginError(bad_creds_reason)
            if self._has_2fa_screen():
                raise Instagram2FARequired()
            page = (d.page_source or "").lower()
            if "challenge" in page or "checkpoint" in page:
                raise InstagramAppiumError("Instagram показал проверку безопасности (challenge).")
            if self._dismiss_save_login_info_prompt(timeout_sec=0.8):
                return
            # Успех только при явных признаках домашнего экрана после логина.
            if self._looks_like_logged_in_home():
                return
            # Пока крутится индикатор входа, продолжаем ждать.
            if self._login_submit_in_progress():
                time.sleep(0.7)
                continue
            time.sleep(0.5)
        raise InstagramLoginError("Не удалось подтвердить вход. Проверь логин/пароль и попробуй снова.")

    def _detect_invalid_credentials_reason(self) -> str | None:
        src = ""
        try:
            src = (self.driver.page_source or "").lower()
        except Exception:
            return None

        bad_patterns = (
            "incorrect password",
            "password you entered is incorrect",
            "password was incorrect",
            "wrong password",
            "please try again",
            "username you entered",
            "couldn't find your account",
            "invalid username",
            "неверный пароль",
            "неправильный пароль",
            "неверное имя пользователя",
            "не удалось найти аккаунт",
        )
        for p in bad_patterns:
            if p in src:
                return "Неверный логин или пароль. Введи данные ещё раз."
        return None

    def _looks_like_login_screen(self) -> bool:
        # Быстрая эвристика, что мы всё ещё на форме входа.
        if self._find_login_edittexts():
            return True
        d = self.driver
        for xp in (
            "//*[@text='Log in']",
            "//*[@text='Войти']",
            "//*[contains(@text,'Log in')]",
            "//*[contains(@text,'Войти')]",
        ):
            try:
                el = d.find_element(AppiumBy.XPATH, xp)
                if el.is_displayed():
                    return True
            except Exception:
                continue
        return False

    def _looks_like_logged_in_home(self) -> bool:
        d = self.driver
        # Позитивные индикаторы нижнего меню/вкладок после авторизации.
        checks = (
            "//*[@content-desc='Home']",
            "//*[@content-desc='Домой']",
            "//*[@content-desc='Search']",
            "//*[@content-desc='Поиск']",
            "//*[@content-desc='Reels']",
            "//*[@content-desc='Профиль']",
            "//*[@content-desc='Profile']",
            "//*[contains(@text,'Threads')]",
        )
        for xp in checks:
            try:
                el = d.find_element(AppiumBy.XPATH, xp)
                if el.is_displayed():
                    return True
            except Exception:
                continue
        return False

    def _dismiss_generic_ok_dialog(self) -> bool:
        return self._tap_if_visible(
            [
                "//*[@text='OK']",
                "//android.widget.Button[@text='OK']",
                "//*[@text='Ok']",
                "//*[@text='ОК']",
            ],
            timeout=0.8,
        )

    def _login_submit_in_progress(self) -> bool:
        d = self.driver
        for xp in (
            "//android.widget.ProgressBar",
            "//*[contains(@content-desc,'loading')]",
            "//*[contains(@content-desc,'Loading')]",
        ):
            try:
                el = d.find_element(AppiumBy.XPATH, xp)
                if el.is_displayed():
                    return True
            except Exception:
                continue
        return False

    def _dismiss_save_login_info_prompt(self, timeout_sec: float = 8.0) -> bool:
        """
        После входа Instagram может показать экран "Save your login info?".
        Для наших сессий жмём "Not now" / "Не сейчас", чтобы не зависать на этом шаге.
        """
        d = self.driver
        end = time.time() + timeout_sec
        xpaths = [
            "//*[@text='Not now']",
            "//android.widget.Button[@text='Not now']",
            "//*[@text='Не сейчас']",
            "//android.widget.Button[@text='Не сейчас']",
            "//*[contains(@text,'Not now')]",
            "//*[contains(@text,'Не сейчас')]",
        ]
        ui_selectors = [
            'new UiSelector().text("Not now")',
            'new UiSelector().text("Не сейчас")',
            'new UiSelector().textContains("Not now")',
            'new UiSelector().textContains("Не сейчас")',
        ]

        while time.time() < end:
            src = ""
            try:
                src = (d.page_source or "").lower()
            except Exception:
                pass
            if "save your login info" not in src and "сохран" not in src and "login info" not in src:
                # Если явного экрана нет, продолжаем попытки коротко — селекторы ниже всё равно проверим.
                pass

            for sel in ui_selectors:
                try:
                    el = d.find_element(AppiumBy.ANDROID_UIAUTOMATOR, sel)
                    if el.is_displayed():
                        el.click()
                        logger.info("Appium: закрыт экран сохранения логина (Not now)")
                        time.sleep(0.6)
                        return True
                except Exception:
                    continue

            for xp in xpaths:
                try:
                    el = d.find_element(AppiumBy.XPATH, xp)
                    if el.is_displayed():
                        el.click()
                        logger.info("Appium: закрыт экран сохранения логина (XPath Not now)")
                        time.sleep(0.6)
                        return True
                except Exception:
                    continue

            time.sleep(0.4)
        return False

    def _has_2fa_screen(self) -> bool:
        src = self.driver.page_source.lower()
        return "verification" in src or "two-factor" in src or "код" in src

    def _tap_if_visible(self, xpaths: list[str], timeout: float = 10) -> bool:
        d = self.driver
        end = time.time() + timeout
        while time.time() < end:
            for xp in xpaths:
                try:
                    el = d.find_element(AppiumBy.XPATH, xp)
                    if el.is_displayed():
                        el.click()
                        logger.info("Appium: клик по %s", xp[:60])
                        return True
                except Exception:
                    continue
            time.sleep(0.5)
        return False

    def _dismiss_follow_reel_modal(self) -> bool:
        """
        Модалка «… shared this reel» / Suggested for you с кнопками Follow / Not now.
        Закрываем через «Not now» (и языковые аналоги), иначе Share не виден / не кликается.
        При implicit wait > 0 каждый промах по селектору давал ~20 с — держим поиск коротким.
        """
        d = self.driver
        try:
            d.implicitly_wait(0)
        except Exception:
            pass

        ui_selectors = (
            'new UiSelector().text("Not now")',
            'new UiSelector().textContains("Not now")',
            'new UiSelector().text("Не сейчас")',
            'new UiSelector().text("Позже")',
            'new UiSelector().descriptionContains("Not now")',
        )
        for sel in ui_selectors:
            try:
                els = d.find_elements(AppiumBy.ANDROID_UIAUTOMATOR, sel)
                for el in els:
                    try:
                        if el.is_displayed():
                            el.click()
                            logger.info(
                                "Appium: закрыта модалка подписки на авторе рилса (UiAutomator)"
                            )
                            return True
                    except Exception:
                        continue
            except Exception:
                continue

        xpaths = [
            "//*[@text='Not now']",
            "//android.widget.Button[@text='Not now']",
            "//*[@text='Не сейчас']",
            "//*[@text='Позже']",
        ]
        if self._tap_if_visible(xpaths, timeout=1.5):
            logger.info("Appium: закрыта модалка подписки (XPath «Not now» / аналог)")
            return True
        return False

    def _clear_follow_reel_modals(self) -> None:
        """1–2 быстрых прохода; если модалки нет, не крутим десятки селекторов по 20 с."""
        if self._dismiss_follow_reel_modal():
            time.sleep(0.35)
            self._dismiss_follow_reel_modal()
        else:
            time.sleep(0.25)

    async def add_reel_to_story(self, reel_url: str) -> None:
        last: Exception | None = None
        for attempt in range(1, STORY_RETRIES + 1):
            try:
                await self._ensure_live_session()
                logger.info(
                    "Appium: add_reel_to_story попытка %s/%s url=%s",
                    attempt,
                    STORY_RETRIES,
                    reel_url[:80],
                )
                await self._run_sync(self._add_reel_to_story_once, reel_url)
                logger.info("Appium: рилс отправлен в Stories")
                return
            except Exception as e:
                last = e
                logger.warning("Appium: попытка %s не удалась: %s", attempt, e)
                await asyncio.to_thread(_retry_pause)
        raise InstagramAppiumError(str(last) if last else "unknown")

    def _add_reel_to_story_once(self, reel_url: str) -> None:
        self._open_reel_url(reel_url)
        self._clear_follow_reel_modals()
        # Одна пауза после deep link; второй полный проход модалки убран — иначе долгий «поиск» при уже закрытом окне
        _human_delay()
        self._tap_share()
        time.sleep(random.uniform(0.9, 1.4))
        self._tap_add_to_story()
        # Дождаться экрана предпросмотра/композитора — иначе второй клик может уйти в «дубликат» Stories
        time.sleep(random.uniform(1.6, 2.4))
        self._tap_confirm_story()

    def _open_reel_url(self, url: str) -> None:
        d = self.driver
        logger.info("Appium: открываю ссылку рилса…")
        try:
            d.execute_script("mobile: deepLink", {"url": url, "package": self.app_package})
            logger.info("Appium: deepLink выполнен")
            return
        except Exception as e:
            logger.warning("Appium: deepLink не сработал (%s), пробую adb intent", e)
        cmd = [
            self.adb,
            "-s",
            self.udid,
            "shell",
            "am",
            "start",
            "-a",
            "android.intent.action.VIEW",
            "-d",
            url,
            self.app_package,
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=60)
            logger.info("Appium: открыто через adb am start")
        except FileNotFoundError:
            raise InstagramAppiumError(
                "adb не найден в PATH. Укажи ADB_PATH в .env или установи Android SDK platform-tools."
            ) from None
        except subprocess.CalledProcessError as e:
            raise InstagramAppiumError(f"adb intent: {e.stderr or e}") from e

    def _tap_share(self) -> None:
        d = self.driver
        wait = WebDriverWait(d, 22)
        xpaths = [
            "//android.widget.ImageView[contains(@content-desc,'Share')]",
            "//*[contains(@content-desc,'Share')]",
            "//*[contains(@content-desc,'Поделиться')]",
            "//android.widget.Button[contains(@content-desc,'Share')]",
        ]
        for xp in xpaths:
            try:
                el = wait.until(EC.element_to_be_clickable((AppiumBy.XPATH, xp)))
                el.click()
                logger.info("Appium: нажата Share (%s)", xp[:50])
                time.sleep(random.uniform(0.45, 0.85))
                return
            except (TimeoutException, Exception):
                continue
        raise InstagramAppiumError("Не найдена кнопка Share (проверь язык/версию Instagram).")

    def _tap_add_to_story(self) -> None:
        d = self.driver
        # Не использовать здесь «Your story» / «Story» — совпадают с финальным шагом и дают двойную публикацию
        primary_texts = [
            "Add to story",
            "Add to your story",
            "Добавить в историю",
        ]
        fallback_texts = [
            "Your story",
            "Story",
        ]
        per_locator_timeout = 4
        for scroll_try in range(5):
            texts = primary_texts if scroll_try == 0 else primary_texts + fallback_texts
            wait = WebDriverWait(d, per_locator_timeout)
            for t in texts:
                xps = [
                    f"//*[@text='{t}']",
                    f"//*[contains(@text,'{t}')]",
                ]
                for xp in xps:
                    try:
                        el = wait.until(EC.element_to_be_clickable((AppiumBy.XPATH, xp)))
                        el.click()
                        logger.info("Appium: выбран пункт «%s» (попытка прокрутки %s)", t, scroll_try)
                        time.sleep(random.uniform(0.35, 0.65))
                        return
                    except (TimeoutException, Exception):
                        continue
            if scroll_try < 4:
                self._scroll_share_row()
                time.sleep(0.7)
        raise InstagramAppiumError("Не найден пункт «Добавить в историю».")

    def _scroll_share_row(self) -> bool:
        d = self.driver
        try:
            w, h = d.get_window_size()["width"], d.get_window_size()["height"]
            d.swipe(w * 0.85, h * 0.45, w * 0.15, h * 0.45, 600)
            logger.info("Appium: свайп влево по полосе шаринга")
            return True
        except Exception as e:
            logger.debug("Appium: свайп шаринга: %s", e)
            return False

    def _tap_confirm_story(self) -> None:
        d = self.driver
        time.sleep(random.uniform(0.5, 0.9))
        # Сначала явные «в историю» — «Share» на некоторых экранах совпадает с шарингом и даёт лишний клик
        labels = (
            "Your story",
            "Ваша история",
            "Done",
            "Готово",
            "Share",
            "Поделиться",
        )
        wait = WebDriverWait(d, 14)
        for t in labels:
            try:
                el = wait.until(EC.element_to_be_clickable((AppiumBy.XPATH, f"//*[@text='{t}']")))
                el.click()
                logger.info("Appium: финальное подтверждение «%s» (один клик)", t)
                time.sleep(random.uniform(2.2, 3.0))
                return
            except (TimeoutException, Exception):
                continue
        raise InstagramAppiumError("Не найдена кнопка публикации в Stories.")

    async def _run_sync(self, fn, *args, **kwargs):
        return await asyncio.to_thread(fn, *args, **kwargs)
