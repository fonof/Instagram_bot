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
2. **Очередь** хранится в `data/queue.json`.
3. **Планировщик (APScheduler)** периодически проверяет очередь и публикует следующий Reel.
4. **Appium + UiAutomator2** управляет приложением Instagram на Android-эмуляторе: Share → Add to story → публикация.
5. Перед постингом в нужный аккаунт бот переключает Android-профиль (`am switch-user`), перезапускает Instagram и пересоздаёт сессию Appium.

Один эмулятор обрабатывает аккаунты по очереди: одновременно активен только один Android user.

Подробнее про профили: [docs/android_profiles.md](docs/android_profiles.md).

---

## Требования

### На компьютере

| Компонент | Рекомендация |
|-----------|--------------|
| ОС | Windows 10/11 (64-bit) |
| CPU | 4+ ядра |
| RAM | от 16 GB (минимум 8 GB) |
| Диск | SSD, свободно от 20–30 GB под SDK и AVD |
| Python | 3.11+ |
| Node.js | LTS (для Appium CLI) |
| Android Studio | последняя стабильная версия |
| Appium | 2.x / 3.x + драйвер `uiautomator2` |

### Android Studio / AVD

- Windows 10/11 64-bit
- 8 GB RAM минимум для IDE; для эмулятора с Instagram удобнее 16 GB
- SSD
- Аппаратная виртуализация (VT-x / AMD-V) в BIOS
- В Windows: **Windows Hypervisor Platform** (или другой поддерживаемый hypervisor)

Параметры AVD, которые обычно ставят под этот проект:

- образ **x86_64**, API 33–34
- **2 CPU cores**, **2 GB RAM**
- Graphics: **Software** или **SwiftShader**, если Hardware нестабилен
- разрешение около **720p**

При долгом старте эмулятора или смене Android-профиля таймауты можно увеличить в `.env` (см. `.env.example`).

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

Если PowerShell блокирует скрипты:

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

### 3. Node.js и Appium

```powershell
npm i -g appium
appium driver install uiautomator2
```

```powershell
appium -v
appium driver list --installed
```

### 4. Android Studio

1. Установите [Android Studio](https://developer.android.com/studio).
2. Через SDK Manager поставьте **Platform-Tools**, **Emulator**, системный образ **x86_64**.
3. Создайте AVD (например Pixel 7, API 34).
4. Добавьте в PATH папку `platform-tools`.
5. Задайте переменные окружения пользователя:

```text
ANDROID_HOME = C:\Users\<user>\AppData\Local\Android\Sdk
ANDROID_SDK_ROOT = то же значение
```

После изменения PATH / переменных перезапустите терминалы.

### 5. Конфиг проекта

```powershell
copy .env.example .env
copy data\accounts.example.json data\accounts.json
copy data\queue.example.json data\queue.json
```

В `.env` укажите:

- `BOT_TOKEN` — токен от [@BotFather](https://t.me/BotFather)
- `EMULATOR_NAME` — serial из `adb devices` (часто `emulator-5554`)
- `ADB_PATH` — путь к `adb.exe`
- `APPIUM_SERVER` — обычно `http://127.0.0.1:4723`

Локальные файлы `.env`, `data/accounts.json` и `data/queue.json` задаются на машине и не входят в репозиторий.

### 6. Instagram на эмуляторе

Установите Instagram на AVD (Play Store или APK).  
Для нескольких аккаунтов используйте Android multi-user ([docs/android_profiles.md](docs/android_profiles.md)) или добавление аккаунта через бота.

---

## Запуск

### Вручную

1. Запустите эмулятор (Device Manager → Play).
2. Запустите Appium:

```powershell
appium -p 4723
```

3. Запустите бота:

```powershell
cd путь\к\Instagram_bot
.\.venv\Scripts\activate
python main.py
```

### Через bat (Windows)

- `start_all.bat` — Appium → эмулятор → бот
- `stop_all.bat` — остановка процессов
- `lighten_pc_for_emulator.bat` — закрытие выбранных фоновых программ перед стартом

В `start_all.bat` можно задать `AVD_NAME` или оставить автовыбор первого AVD из `emulator -list-avds`.

Если порт `4723` занят (`EADDRINUSE`), выполните `stop_all.bat` или `taskkill /F /IM node.exe`.

---

## Использование в Telegram

1. `/start`
2. Добавить или выбрать Instagram-аккаунт
3. Загрузить ссылки на Reels
4. Подтвердить старт очереди
5. Следить за прогрессом; остановить очередь можно через «Запущенные автопостинги»

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
│       ├── emulator.py     # ADB, multi-user, Appium Settings
│       └── storage.py      # accounts / queue JSON
├── data/                   # шаблоны *.example.json
├── docs/
└── logs/
```

---

## Данные и конфиг

- `BOT_TOKEN` и пути к SDK хранятся в `.env`
- В `accounts.json` сохраняются id, имя в боте, username и `android_user_id`
- Пароли Instagram в файлы проекта не записываются

---

## Частые проблемы

| Симптом | Что проверить |
|---------|----------------|
| `Neither ANDROID_HOME nor ANDROID_SDK_ROOT` | Переменные окружения + перезапуск Appium |
| `No drivers have been installed` | `appium driver install uiautomator2` |
| `EADDRINUSE :4723` | Уже запущен Appium → `stop_all.bat` |
| `Appium Settings app is not running` | Дождаться загрузки эмулятора / профиля; увеличить таймауты в `.env` |
| `device offline` после switch-user | Подождать; Cold Boot AVD |
| `adb` не находится | PATH → `...\Android\Sdk\platform-tools` |
| PowerShell блокирует `activate` / `npm` | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |

---

## Лицензия

Используйте на свой страх и риск. Соблюдайте правила Instagram и законодательство вашей страны. Автоматизация аккаунтов может привести к ограничениям со стороны сервиса.
