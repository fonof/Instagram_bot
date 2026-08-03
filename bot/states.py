"""
FSM-состояния бота.
Все пользовательские сценарии завязаны на StatesGroup из aiogram 3.x.
"""

from aiogram.fsm.state import State, StatesGroup


class AccountPickStates(StatesGroup):
    """Выбор Instagram-аккаунта из списка (inline-меню)."""

    selecting = State()


class AddAccountStates(StatesGroup):
    """Пошаговое добавление нового аккаунта."""

    login = State()
    password = State()
    twofa = State()
    name = State()


class UploadStates(StatesGroup):
    """Ожидание списка ссылок на Reels."""

    waiting_links = State()
