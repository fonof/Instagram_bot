## Android multi-user профили для Instagram (AVD)

Этот проект поддерживает несколько Instagram-аккаунтов в **одном** эмуляторе за счёт
Android multi-user profiles. Идея простая:

- **один Android user profile = один залогиненный Instagram**
- бот перед постингом делает `adb shell am switch-user <id>` и перезапускает Instagram
- после переключения пересоздаётся Appium driver, чтобы он работал с текущим профилем

### Проверка / ручные команды (PowerShell)

1) Список устройств:

```powershell
adb devices
```

2) Список профилей Android:

```powershell
adb -s emulator-5554 shell pm list users
```

3) Создать новый профиль:

```powershell
adb -s emulator-5554 shell pm create-user "ig_account_2"
```

4) Переключиться:

```powershell
adb -s emulator-5554 shell am switch-user 10
```

Если не сработало:

```powershell
adb -s emulator-5554 shell cmd activity switch-user 10
```

### Добавление аккаунта через бота

- В Telegram: **«Добавить новый Instagram-аккаунт»**
- бот создаёт Android-профиль `ig_account_<id>` и переключается на него
- выполняется вход в Instagram через Appium
- в `data/accounts.json` сохраняется `android_user_id`

### Как работает автопостинг

Перед каждой публикацией Stories бот:
- переключает Android user на `accounts[].android_user_id`
- перезапускает Instagram
- пересоздаёт Appium driver
- публикует один Story

