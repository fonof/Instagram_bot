# Instagram Stories Bot

Telegram-бот для автоматической публикации Instagram Reels в Stories через Android-эмулятор и Appium.

Вы загружаете список ссылок на Reels, выбираете аккаунт — бот по очереди открывает каждый Reel в приложении Instagram и добавляет его в Stories с паузами между публикациями.

---

## Что умеет

- Добавление и выбор нескольких Instagram-аккаунтов через Telegram
- Загрузка списка ссылок на Reels (до 100 за раз)
- Очередь автопостинга с сохранением прогресса
- Пауза между Stories (случайный интервал, по умолчанию 60–120 секунд)
- Промежуточные отчёты в Telegram (каждые 2 успешные публикации)
- Несколько аккаунтов на одном эмуляторе через Android multi-user profiles
- Скрипты `start_all.bat` / `stop_all.bat` для быстрого запуска и остановки на Windows

---

## Как это работает

1. **Telegram (aiogram)** — интерфейс: аккаунты, загрузка ссылок, старт/стоп очереди.
2. **Очередь** хранится в `data/queue.json` (локально, в git не попадает).
3. **Планировщик (APScheduler)** периодически проверяет очередь и публикует следующий Reel.
4. **Appium + UiAutomator2** управляет приложением Instagram на Android-эмуляторе: Share → Add to story → публикация.
5. Перед постингом в нужный аккаунт бот переключает Android-профиль (`am switch-user`), перезапускает Instagram и пересоздаёт сессию Appium.

Один эмулятор обрабатывает аккаунты **по очереди**, не параллельно: одновременно активен только один Android user.

Подробнее про профили: [docs/android_profiles.md](docs/android_profiles.md).

---

## Требования

### На компьютере

| Компонент | Рекомендация |
|-----------|--------------|
| ОС | Windows 10/11 (64-bit) |
| CPU | 4+ ядра (для комфортной работы эмулятора) |
| RAM | от 16 GB (минимум 8 GB, но будет тесно) |
| Диск | SSD, свободно от 20–30 GB под SDK и AVD |
| Python | 3.11+ |
| Node.js | LTS (для Appium CLI) |
| Android Studio | последняя стабильная версия |
| Appium | 2.x / 3.x + драйвер `uiautomator2` |

### Системные требования для Android Studio / AVD

Официальные ориентиры Google для Android Studio:

- **Windows 10/11 64-bit**
- **8 GB RAM** — минимум для IDE; для эмулятора Instagram комфортнее **16 GB**
- **SSD**
- Включённая аппаратная виртуализация (VT-x / AMD-V) в BIOS
- В Windows: компонент **Windows Hypervisor Platform** (или подходящий hypervisor для эмулятора)

Для AVD с Instagram на практике:

- образ **x86_64**, API 33–34
- **2 CPU cores**, **2 GB RAM** у виртуального устройства
- Graphics: **Software** / **SwiftShader**, если Hardware глючит на встроенной видеокарте
- разрешение около **720p**

На ноутбуках со встроенной графикой эмулятор может грузиться долго — это нормально; в `.env` можно увеличить таймауты (см. `.env.example`).

---

## Установка

### 1. Клонирование

```bash
git clone https://github.com/fonof/Instagram_bot.git
cd Instagram_bot
```

### 2. Python-окружение

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

Если PowerShell ругается на скрипты:

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

### 3. Node.js и Appium

```powershell
npm i -g appium
appium driver install uiautomator2
```

Проверка:

```powershell
appium -v
appium driver list --installed
```

### 4. Android Studio

1. Установите [Android Studio](https://developer.android.com/studio).
2. Через SDK Manager поставьте **Platform-Tools**, **Emulator**, системный образ **x86_64**.
3. Создайте AVD (например Pixel 7, API 34).
4. Добавьте в PATH папку `platform-tools` (чтобы работал `adb`).
5. Задайте переменные окружения пользователя:

```text
ANDROID_HOME = C:\Users\<user>\AppData\Local\Android\Sdk
ANDROID_SDK_ROOT = то же значение
```

Перезапустите терминалы после изменения PATH / переменных.

### 5. Конфиг проекта

```powershell
copy .env.example .env
copy data\accounts.example.json data\accounts.json
copy data\queue.example.json data\queue.json
```

Отредактируйте `.env`:

- `BOT_TOKEN` — токен от [@BotFather](https://t.me/BotFather)
- `EMULATOR_NAME` — serial из `adb devices` (часто `emulator-5554`)
- `ADB_PATH` — полный путь к `adb.exe` на вашей машине
- `APPIUM_SERVER` — обычно `http://127.0.0.1:4723`

**Не коммитьте `.env` и `data/accounts.json` — они в `.gitignore`.**

### 6. Instagram на эмуляторе

Установите Instagram на AVD (Play Store или APK).  
Для второго/третьего аккаунта используйте Android multi-user (см. [docs/android_profiles.md](docs/android_profiles.md)) или кнопку «Добавить аккаунт» в боте.

---

## Запуск

### Вручную (три окна)

1. Эмулятор (Device Manager → Play).
2. Appium:

```powershell
appium -p 4723
```

3. Бот:

```powershell
cd путь\к\Instagram_bot
.\.venv\Scripts\activate
python main.py
```

### Через bat (Windows)

В корне проекта есть:

- `start_all.bat` — Appium → эмулятор → бот  
- `stop_all.bat` — остановить процессы  
- `lighten_pc_for_emulator.bat` — опционально закрыть лишние программы перед стартом  

Перед первым запуском откройте `start_all.bat` и при необходимости укажите имя AVD (`AVD_NAME`) или оставьте автовыбор первого AVD из `emulator -list-avds`.

Если видите `EADDRINUSE ... 4723` — порт занят: сначала `stop_all.bat` или `taskkill /F /IM node.exe`.

---

## Использование в Telegram

1. Напишите боту `/start`.
2. Добавьте или выберите Instagram-аккаунт.
3. Загрузите ссылки на Reels.
4. Подтвердите старт очереди.
5. Смотрите прогресс и отчёты; при необходимости остановите очередь через «Запущенные автопостинги».

---

## Структура проекта

```text
Instagram_bot/
├── main.py                 # точка входа: Telegram + планировщик
├── requirements.txt
├── .env.example
├── start_all.bat / stop_all.bat
├── bot/
│   ├── handlers/           # команды и колбэки Telegram
│   ├── keyboards/
│   ├── states.py
│   └── utils/
│       ├── appium.py       # сценарии Instagram в Appium
│       ├── emulator.py     # ADB, multi-user, прогрев Appium Settings
│       └── storage.py      # accounts / queue JSON
├── data/                   # локальные JSON (шаблоны *.example.json)
├── docs/
└── logs/                   # локальные логи (в git не попадают)
```

---

## Безопасность

- Токен бота и пути к SDK храните только в `.env`.
- Пароли Instagram бот **не сохраняет** в `accounts.json` — только id, отображаемое имя, username и `android_user_id`.
- Не публикуйте скриншоты с `.env`, логами с токенами и живыми сессиями.

Если токен уже светился в чате или на скрине — перевыпустите его у BotFather (`/revoke` / новый токен).

---

## Частые проблемы

| Симптом | Что проверить |
|---------|----------------|
| `Neither ANDROID_HOME nor ANDROID_SDK_ROOT` | Переменные окружения + перезапуск Appium |
| `No drivers have been installed` | `appium driver install uiautomator2` |
| `EADDRINUSE :4723` | Уже запущен Appium → `stop_all.bat` |
| `Appium Settings app is not running` | Дождаться загрузки эмулятора / профиля; увеличить таймауты в `.env` |
| `device offline` после switch-user | Подождать; бот пробует reconnect; Cold Boot AVD |
| `adb` не находится | PATH → `...\Android\Sdk\platform-tools` |
| PowerShell блокирует `activate` / `npm` | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |

---

## Лицензия

Репозиторий публичный. Используйте на свой страх и риск и соблюдайте правила Instagram и законодательство вашей страны. Автоматизация аккаунтов может привести к ограничениям со стороны сервиса.
