<div align="center">
  <img src="assets/app_icon.png" width="128" alt="Telegram Exporter Logo">

  <h1>Telegram Exporter</h1>

  <p><b>Десктопное приложение для экспорта чатов и каналов Telegram в JSON и Markdown.</b><br>
  С транскрипцией голосовых, скачиванием медиа и поддержкой нескольких аккаунтов.</p>

  <p>
    <a href="https://t.me/+cK5SwFPffNViOWUy">
      <img src="https://img.shields.io/badge/TELEGRAM-%D0%9A%D0%90%D0%9D%D0%90%D0%9B_%D0%90%D0%92%D0%A2%D0%9E%D0%A0%D0%90-2CA5E0?style=for-the-badge&logo=telegram&logoColor=white&labelColor=555555" alt="Telegram Канал Автора">
    </a>
    <a href="https://www.youtube.com/channel/UCLk7uewdd5s7kszfy736ScA">
      <img src="https://img.shields.io/badge/YOUTUBE-%D0%9A%D0%90%D0%9D%D0%90%D0%9B_%D0%90%D0%92%D0%A2%D0%9E%D0%A0%D0%90-FF0000?style=for-the-badge&logo=youtube&logoColor=white&labelColor=555555" alt="YouTube Канал Автора">
    </a>
  </p>

  <p>
    <a href="https://github.com/morf3uzzz/telegram-exporter/releases">
      <img src="https://img.shields.io/github/v/release/morf3uzzz/telegram-exporter?style=flat-square" alt="GitHub Release">
    </a>
    <a href="https://opensource.org/licenses/MIT">
      <img src="https://img.shields.io/badge/License-MIT-yellow.svg?style=flat-square" alt="License: MIT">
    </a>
  </p>
</div>


## Что умеет

- **Экспорт в JSON или Markdown** — вся история сообщений с метаданными или в удобном для чтения виде (подходит для Obsidian).
- **Несколько аккаунтов** — добавил все свои номера один раз, переключаешься между ними в один клик.
- **Транскрипция голосовых и видео-кружков**:
  - локально через Faster-Whisper (модели от tiny до large-v3);
  - облачно через Deepgram (nova-3, быстро и точно).
- **Скачивание медиа** — фото, видео, голосовые, документы раскладываются по папкам. Каждый файл назван по дате и номеру сообщения и указан прямо под своим сообщением в Markdown (и в `media_path` в JSON); альбомы выводятся одним блоком — видно, какие фото к какому посту относятся.
- **Фильтры**: период (неделя / месяц / свой диапазон), папки Telegram, авторы.
- **Инкрементальный экспорт** — дозабирает только новые сообщения с прошлого раза.
- **Аналитика**: топ авторов и активность по датам.
- **Безопасность**: `api_hash` и сессии хранятся в системном Keyring, не в открытых файлах.

## Установка

### Готовые сборки

Открой [Releases](https://github.com/morf3uzzz/telegram-exporter/releases) и скачай файл под свою ОС:

- **macOS Apple Silicon (M1/M2/M3/M4)** — `TelegramExporter-mac-arm64.dmg`
- **macOS Intel** — `TelegramExporter-mac-intel.dmg`
- **Windows** — `TelegramExporterSetup.exe`
- **Linux (x86_64)** — `TelegramExporter-linux-x86_64.tar.gz` (распаковать и запустить бинарь внутри)

### Из исходников

```bash
git clone https://github.com/morf3uzzz/telegram-exporter.git
cd telegram-exporter
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

Нужен Python 3.11+.

## Первый запуск

1. Получи `api_id` и `api_hash` на [my.telegram.org](https://my.telegram.org) → API development tools.
2. Введи их в окне логина приложения.
3. Введи номер телефона → код из Telegram → (если есть) пароль 2FA.
4. После входа можно добавить ещё аккаунты через кнопку **«Аккаунт ▾»** в шапке.

> **Если из РФ и не приходит код / «Ошибка соединения, проверьте интернет»** — включи VPN. Приложение ходит напрямую к серверам Telegram, а их IP в России заблокированы. Обычные «VPN для сайтов» не всегда помогают — нужен такой, который прогоняет весь трафик (например, AmneziaVPN, Outline, WireGuard). Если один VPN не сработал — попробуй другой.
>
> **Прокси вместо VPN.** Можно не держать VPN на весь компьютер, а указать прокси — тогда через него пойдёт только трафик Telegram. На экране входа нажми **«🌐 Прокси (если нет VPN)»** и введи данные (SOCKS5/HTTP/MTProto). У каждого аккаунта может быть свой прокси — после входа он настраивается в меню **«Аккаунт ▾» → «🌐 Прокси…»**. Прокси нужно где-то взять самому (платный SOCKS5 ~$1–3/мес, бесплатный MTProto или свой сервер). Кнопка **«Проверить связь»** покажет, работает ли он.

## Транскрипция

- **Локальная (Whisper)** — работает офлайн, первый запуск модели скачает её с HuggingFace (от ~75 МБ для `tiny` до ~3 ГБ для `large-v3`). Для `large-v3` желательно 8 ГБ RAM.
- **Deepgram** — нужен API-ключ ([deepgram.com](https://deepgram.com)), ключ вводится в настройках приложения и хранится в Keyring.

Ограничение: одно голосовое/кружок не длиннее 15 минут.

## Где приложение хранит файлы

```
~/.tg_exporter/
├── config.json              # api_id и настройки (без секретов)
├── profiles.json            # список аккаунтов (без сессий)
├── export_history.json      # для инкрементального экспорта
└── app.log                  # лог приложения
```

Секреты (`api_hash`, сессии, Deepgram key) — в системном Keyring (`tg_exporter`).

## Лицензия

MIT — см. [LICENSE](LICENSE).
