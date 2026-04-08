# EduAssist — Telegram-бот для приёма студенческих заявок

## Структура проекта

```
bot/
├── bot.py           # Основной код бота
├── requirements.txt # Python-зависимости
├── Dockerfile       # Docker-образ
├── .env.example     # Пример переменных окружения
└── README.md
```

---

## Получение BOT_TOKEN

1. Откройте Telegram и найдите **@BotFather**
2. Напишите `/newbot`
3. Придумайте имя (например: `EduAssist Bot`) и username (например: `eduassist_orders_bot`)
4. Скопируйте токен вида `1234567890:AAH...`

---

## Получение ADMIN_CHAT_ID

1. Напишите боту **@userinfobot** в Telegram
2. Он вернёт ваш Telegram ID (число)
3. Если нужно несколько администраторов — соберите все ID через запятую

---

## Запуск локально

### Вариант 1 — Python напрямую

```bash
# 1. Перейдите в папку бота
cd bot

# 2. Создайте виртуальное окружение
python -m venv venv
source venv/bin/activate        # Linux / macOS
venv\Scripts\activate           # Windows

# 3. Установите зависимости
pip install -r requirements.txt

# 4. Создайте файл .env из примера
cp .env.example .env
# Отредактируйте .env — вставьте BOT_TOKEN и ADMIN_CHAT_IDS

# 5. Запустите
python bot.py
```

### Вариант 2 — Docker

```bash
cd bot

# Создайте .env файл
cp .env.example .env
# Вставьте BOT_TOKEN и ADMIN_CHAT_IDS в .env

# Соберите и запустите образ
docker build -t eduassist-bot .
docker run --env-file .env -v $(pwd)/data:/app eduassist-bot
```

---

## Деплой на Railway

1. Зарегистрируйтесь на [railway.app](https://railway.app)
2. Нажмите **New Project → Deploy from GitHub repo**
3. Подключите репозиторий
4. В разделе **Settings → Root Directory** укажите `bot`
5. Перейдите в **Variables** и добавьте:
   - `BOT_TOKEN` = ваш токен от BotFather
   - `ADMIN_CHAT_IDS` = `5200591878,1555289142,1057138144`
6. Railway автоматически обнаружит `Dockerfile` и задеплоит бот

> База данных `orders.db` хранится внутри контейнера. Для постоянного хранения на Railway добавьте Volume или используйте внешнюю БД.

---

## Деплой на Render

1. Зарегистрируйтесь на [render.com](https://render.com)
2. Нажмите **New → Web Service**
3. Подключите GitHub-репозиторий
4. Настройки:
   - **Root Directory:** `bot`
   - **Environment:** `Docker`
   - **Instance Type:** Free
5. В разделе **Environment Variables** добавьте:
   - `BOT_TOKEN`
   - `ADMIN_CHAT_IDS`
6. Нажмите **Create Web Service**

---

## Переменные окружения

| Переменная      | Описание                                          | Пример                              |
|-----------------|---------------------------------------------------|-------------------------------------|
| `BOT_TOKEN`     | Токен бота от @BotFather                          | `1234567890:AAH...`                 |
| `ADMIN_CHAT_IDS`| ID чатов администраторов через запятую            | `5200591878,1555289492,1057138144`  |

---

## Статусы заявок в БД

| Статус    | Описание                        |
|-----------|---------------------------------|
| `new`     | Новая заявка                    |
| `taken`   | Взята администратором в работу  |
| `partner` | Передана партнёру               |
| `done`    | Выполнена                       |
