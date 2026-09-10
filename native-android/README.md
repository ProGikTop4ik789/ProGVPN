# Нативная версия ProGVPN для Android (Java)

Приложение с **встроенной библиотекой WireGuard** — официальное приложение
WireGuard не нужно, конфиг вручную вводить не надо.

## Что умеет
- Загрузить `.conf` **из файла** (системный выбор файлов) **или по прямой ссылке**.
- **Одна кнопка «Подключить/Отключить»** — туннель поднимается/опускается программно.
- Конфиг сохраняется внутри приложения и подставляется при следующем запуске.

## Зависимости
- `com.wireguard.android:tunnel:1.0.20260102` (Maven Central, Apache-2.0)
- Java 17 + core library desugaring (уже настроено в `app/build.gradle`)
- `minSdk 24`, `targetSdk 34`

## Сборка через Android Studio (рекомендуется)
1. Скачай и установи **Android Studio**: https://developer.android.com/studio
2. `File → Open` → выбери папку **`native-android`**.
3. Дождись `Gradle Sync` (Studio сама скачает Gradle, Android SDK и библиотеку WireGuard — нужен интернет).
4. Запусти на телефоне (**Run ▶**) или собери APK:
   `Build → Build Bundle(s) / APK(s) → Build APK(s)`.
5. APK появится в `app/build/outputs/apk/debug/app-debug.apk`.

## Сборка без Android Studio (Gradle CLI, WSL/Linux)
Нужны: JDK 17, Android SDK (platform 34 + build-tools 34), Gradle 8.9+.

```bash
# переменные окружения
export ANDROID_HOME=$HOME/Android/Sdk
export PATH=$PATH:$ANDROID_HOME/platform-tools

# принять лицензии и поставить нужные компоненты
sdkmanager --licenses
sdkmanager "platforms;android-34" "build-tools;34.0.0"

# собрать debug-APK
gradle assembleDebug
# результат: app/build/outputs/apk/debug/app-debug.apk
```

## Как пользоваться
1. Открой приложение.
2. Вставь **ссылку на .conf** и нажми «Взять» — либо «Выбрать .conf из файла».
3. Нажми **«Подключить»**. Первый раз Android покажет системный диалог
   «Запрос на подключение VPN» → разреши.
4. Готово — статус «Подключено ✅». Отключение — той же кнопкой.

> Совет: в настройках Android включи **«Постоянный VPN»** для ProGVPN —
> тогда туннель будет подниматься автоматически (в т.ч. после перезагрузки).

## Замечания по безопасности
- Приватный ключ клиента хранится внутри приложения (для личного VPN это нормально).
- Системный диалог разрешения VPN обойти нельзя — это защита Android.
