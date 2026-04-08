"""
Telegram-бот для сервиса выполнения студенческих заданий.
Технологии: aiogram 3.x, aiosqlite, Python 3.11
"""

import asyncio
import logging
import os
from datetime import datetime

import aiosqlite
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from dotenv import load_dotenv

# ─── Конфигурация ──────────────────────────────────────────────────────────────

load_dotenv()

BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
# Несколько ID администраторов через запятую
ADMIN_CHAT_IDS: list[int] = [
    int(x.strip())
    for x in os.getenv("ADMIN_CHAT_IDS", "").split(",")
    if x.strip()
]

DB_PATH = "orders.db"

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не задан в переменных окружения!")
if not ADMIN_CHAT_IDS:
    raise ValueError("ADMIN_CHAT_IDS не заданы в переменных окружения!")

# ─── Логирование ───────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ─── База данных ───────────────────────────────────────────────────────────────

async def init_db() -> None:
    """Создаёт таблицы, если они ещё не существуют."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_user_id  INTEGER NOT NULL,
                username    TEXT,
                subject     TEXT,
                description TEXT,
                deadline    TEXT,
                contact     TEXT,
                file_id     TEXT,
                file_type   TEXT,
                status      TEXT    DEFAULT 'new',
                taken_by    TEXT,
                created_at  TEXT
            )
        """)
        # Таблица для хранения message_id уведомлений у каждого админа
        await db.execute("""
            CREATE TABLE IF NOT EXISTS order_messages (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id   INTEGER NOT NULL,
                chat_id    INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                has_caption INTEGER DEFAULT 0  -- 1 если сообщение с фото/документом
            )
        """)
        await db.commit()
    logger.info("База данных инициализирована.")


async def save_order(data: dict, user_id: int, username: str | None) -> int:
    """Сохраняет заявку в БД и возвращает её id."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            INSERT INTO orders
                (tg_user_id, username, subject, description,
                 deadline, contact, file_id, file_type, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'new', ?)
            """,
            (
                user_id,
                username,
                data.get("subject"),
                data.get("description"),
                data.get("deadline"),
                data.get("contact"),
                data.get("file_id"),
                data.get("file_type"),
                now,
            ),
        )
        order_id = cursor.lastrowid
        await db.commit()
    return order_id


async def update_order_status(order_id: int, status: str, taken_by: str | None = None) -> None:
    """Обновляет статус заявки."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE orders SET status = ?, taken_by = ? WHERE id = ?",
            (status, taken_by, order_id),
        )
        await db.commit()


async def save_order_message(order_id: int, chat_id: int, message_id: int, has_caption: bool) -> None:
    """Сохраняет message_id уведомления у конкретного админа."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO order_messages (order_id, chat_id, message_id, has_caption) VALUES (?, ?, ?, ?)",
            (order_id, chat_id, message_id, 1 if has_caption else 0),
        )
        await db.commit()


async def get_order_messages(order_id: int) -> list[tuple[int, int, bool]]:
    """Возвращает список (chat_id, message_id, has_caption) для всех уведомлений заявки."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT chat_id, message_id, has_caption FROM order_messages WHERE order_id = ?",
            (order_id,),
        )
        rows = await cursor.fetchall()
    return [(r[0], r[1], bool(r[2])) for r in rows]

# ─── FSM: шаги анкеты ──────────────────────────────────────────────────────────

class OrderForm(StatesGroup):
    subject     = State()
    description = State()
    deadline    = State()
    file        = State()
    contact     = State()

# ─── Клавиатуры ────────────────────────────────────────────────────────────────

def kb_main() -> ReplyKeyboardMarkup:
    """Главное меню клиента."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📝 Оставить заявку")]],
        resize_keyboard=True,
    )


def kb_skip() -> ReplyKeyboardMarkup:
    """Кнопка «Пропустить» для необязательных шагов."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="⏭️ Пропустить")]],
        resize_keyboard=True,
    )


def kb_admin(order_id: int) -> InlineKeyboardMarkup:
    """Inline-кнопки для администраторов под уведомлением о заявке."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Взять в работу",
                    callback_data=f"take_{order_id}",
                ),
                InlineKeyboardButton(
                    text="➡️ Передать партнёру",
                    callback_data=f"partner_{order_id}",
                ),
            ]
        ]
    )

# ─── Хелпер: отправка уведомления администраторам ─────────────────────────────

async def notify_admins(bot: Bot, order_id: int, data: dict, message: Message) -> None:
    """Отправляет уведомление о новой заявке всем администраторам."""
    username_str = f"@{message.from_user.username}" if message.from_user.username else "нет username"
    text = (
        f"🆕 <b>Новая заявка #{order_id}</b>\n\n"
        f"👤 <b>Клиент:</b> {message.from_user.full_name} ({username_str})\n"
        f"📖 <b>Предмет:</b> {data.get('subject')}\n"
        f"📝 <b>Описание:</b> {data.get('description')}\n"
        f"⏰ <b>Дедлайн:</b> {data.get('deadline')}\n"
        f"📞 <b>Контакт:</b> {data.get('contact')}\n"
        f"🕐 <b>Время заявки:</b> {datetime.now().strftime('%d.%m.%Y %H:%M')}"
    )

    keyboard = kb_admin(order_id)
    file_id   = data.get("file_id")
    file_type = data.get("file_type")

    for admin_id in ADMIN_CHAT_IDS:
        try:
            if file_id and file_type == "photo":
                sent = await bot.send_photo(
                    admin_id, photo=file_id,
                    caption=text, parse_mode="HTML",
                    reply_markup=keyboard,
                )
                await save_order_message(order_id, admin_id, sent.message_id, has_caption=True)
            elif file_id and file_type == "document":
                sent = await bot.send_document(
                    admin_id, document=file_id,
                    caption=text, parse_mode="HTML",
                    reply_markup=keyboard,
                )
                await save_order_message(order_id, admin_id, sent.message_id, has_caption=True)
            else:
                sent = await bot.send_message(
                    admin_id, text,
                    parse_mode="HTML",
                    reply_markup=keyboard,
                )
                await save_order_message(order_id, admin_id, sent.message_id, has_caption=False)
        except Exception as e:
            logger.error("Ошибка отправки уведомления администратору %s: %s", admin_id, e)

# ─── Роутер и хендлеры ─────────────────────────────────────────────────────────

router = Router()


WELCOME_TEXT = (
    "👋 Салем! Ты попал куда надо 😎\n\n"
    "Помогаем со студенческими заданиями уже не первый семестр.\n"
    "Лабы, курсовые, семинары — берёмся за всё.\n\n"
    "⚡️ Оставь заявку — ответим быстро, сделаем чётко.\n\n"
    "👇 Жми кнопку ниже чтобы начать"
)

WELCOME_PHOTO = "welcome.png"  # файл лежит рядом с bot.py


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    """Приветственное сообщение с фото при /start."""
    await state.clear()
    try:
        photo = FSInputFile(WELCOME_PHOTO)
        await message.answer_photo(
            photo=photo,
            caption=WELCOME_TEXT,
            reply_markup=kb_main(),
        )
    except Exception:
        # Если фото не найдено — отправляем просто текст
        await message.answer(WELCOME_TEXT, reply_markup=kb_main())


@router.message(F.text == "📝 Оставить заявку")
async def start_order(message: Message, state: FSMContext) -> None:
    """Начало анкеты — сразу предмет."""
    await state.set_state(OrderForm.subject)
    await message.answer(
        "📖 Укажите предмет (например: Математика, Java, Экономика):",
        reply_markup=ReplyKeyboardRemove(),
    )


# ── Шаг 1: Предмет ────────────────────────────────────────────────────────────

@router.message(OrderForm.subject)
async def step_subject(message: Message, state: FSMContext) -> None:
    await state.update_data(subject=message.text)
    await state.set_state(OrderForm.description)
    await message.answer("📝 Опишите задание:")


# ── Шаг 3: Описание ───────────────────────────────────────────────────────────

@router.message(OrderForm.description)
async def step_description(message: Message, state: FSMContext) -> None:
    await state.update_data(description=message.text)
    await state.set_state(OrderForm.deadline)
    await message.answer("⏰ Укажите дедлайн (например: 15 апреля, 20:00):")


# ── Шаг 4: Дедлайн ────────────────────────────────────────────────────────────

@router.message(OrderForm.deadline)
async def step_deadline(message: Message, state: FSMContext) -> None:
    await state.update_data(deadline=message.text)
    await state.set_state(OrderForm.file)
    await message.answer(
        "📎 Прикрепите файл задания (фото или документ).\n"
        "Если файла нет — нажмите <b>«Пропустить»</b>.",
        parse_mode="HTML",
        reply_markup=kb_skip(),
    )


# ── Шаг 5а: Пропуск файла ─────────────────────────────────────────────────────

@router.message(OrderForm.file, F.text == "⏭️ Пропустить")
async def step_file_skip(message: Message, state: FSMContext) -> None:
    await state.update_data(file_id=None, file_type=None)
    await state.set_state(OrderForm.contact)
    await message.answer(
        "📞 Укажите ваш номер телефона или @username для связи:",
        reply_markup=ReplyKeyboardRemove(),
    )


# ── Шаг 5б: Загрузка файла (фото) ─────────────────────────────────────────────

@router.message(OrderForm.file, F.photo)
async def step_file_photo(message: Message, state: FSMContext) -> None:
    # Берём наибольшее по качеству фото
    file_id = message.photo[-1].file_id
    await state.update_data(file_id=file_id, file_type="photo")
    await state.set_state(OrderForm.contact)
    await message.answer(
        "✅ Фото получено!\n\n📞 Укажите ваш номер телефона или @username для связи:",
        reply_markup=ReplyKeyboardRemove(),
    )


# ── Шаг 5в: Загрузка файла (документ) ────────────────────────────────────────

@router.message(OrderForm.file, F.document)
async def step_file_document(message: Message, state: FSMContext) -> None:
    file_id = message.document.file_id
    await state.update_data(file_id=file_id, file_type="document")
    await state.set_state(OrderForm.contact)
    await message.answer(
        "✅ Файл получен!\n\n📞 Укажите ваш номер телефона или @username для связи:",
        reply_markup=ReplyKeyboardRemove(),
    )


# ── Шаг 5г: Невалидный ввод на шаге файла ────────────────────────────────────

@router.message(OrderForm.file)
async def step_file_invalid(message: Message) -> None:
    await message.answer(
        "❗ Пожалуйста, отправьте файл (фото или документ) или нажмите «Пропустить».",
        reply_markup=kb_skip(),
    )


# ── Шаг 6: Контакт → финал ────────────────────────────────────────────────────

@router.message(OrderForm.contact)
async def step_contact(message: Message, state: FSMContext, bot: Bot) -> None:
    await state.update_data(contact=message.text)
    data = await state.get_data()
    await state.clear()

    # Сохраняем в БД
    try:
        order_id = await save_order(data, message.from_user.id, message.from_user.username)
    except Exception as e:
        logger.error("Ошибка сохранения заявки: %s", e)
        await message.answer("⚠️ Произошла ошибка при сохранении заявки. Попробуйте снова.")
        return

    # Подтверждение клиенту
    await message.answer(
        "✅ <b>Спасибо! Ваша заявка принята.</b>\n\n"
        "Менеджер свяжется с вами в течение <b>30 минут</b>.",
        parse_mode="HTML",
        reply_markup=kb_main(),
    )

    # Уведомление администраторов
    await notify_admins(bot, order_id, data, message)

# ─── Callback-хендлеры для администраторов ────────────────────────────────────

async def edit_all_admin_messages(bot: Bot, order_id: int, suffix: str) -> None:
    """Редактирует уведомление о заявке у всех админов сразу."""
    messages = await get_order_messages(order_id)
    empty_kb = InlineKeyboardMarkup(inline_keyboard=[])
    for chat_id, message_id, has_caption in messages:
        try:
            if has_caption:
                await bot.edit_message_caption(
                    chat_id=chat_id,
                    message_id=message_id,
                    caption=suffix,
                    parse_mode="HTML",
                    reply_markup=empty_kb,
                )
            else:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=suffix,
                    parse_mode="HTML",
                    reply_markup=empty_kb,
                )
        except Exception as e:
            logger.error("Ошибка редактирования у админа %s: %s", chat_id, e)


@router.callback_query(F.data.startswith("take_"))
async def cb_take_order(callback: CallbackQuery, bot: Bot) -> None:
    """Администратор берёт заявку в работу — обновляется у всех."""
    order_id  = int(callback.data.split("_", 1)[1])
    admin_tag = f"@{callback.from_user.username}" if callback.from_user.username else callback.from_user.full_name

    # Проверяем — не взят ли уже заказ другим менеджером
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT status, taken_by FROM orders WHERE id = ?", (order_id,))
        row = await cursor.fetchone()
    if row and row[0] != "new":
        taken_by_str = row[1] or "партнёр"
        await callback.answer(f"❌ Заказ уже взят: {taken_by_str}", show_alert=True)
        return

    await update_order_status(order_id, status="taken", taken_by=admin_tag)

    # Берём оригинальный текст из сообщения того, кто нажал, и добавляем пометку
    orig = callback.message.caption or callback.message.text or ""
    new_text = orig + f"\n\n✅ <b>Взял в работу:</b> {admin_tag}"

    await edit_all_admin_messages(bot, order_id, new_text)
    await callback.answer("✅ Заявка взята в работу!")


@router.callback_query(F.data.startswith("partner_"))
async def cb_partner_order(callback: CallbackQuery, bot: Bot) -> None:
    """Администратор передаёт заявку партнёру — обновляется у всех."""
    order_id = int(callback.data.split("_", 1)[1])

    # Проверяем — не взят ли уже заказ
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT status, taken_by FROM orders WHERE id = ?", (order_id,))
        row = await cursor.fetchone()
    if row and row[0] != "new":
        taken_by_str = row[1] or "партнёр"
        await callback.answer(f"❌ Заказ уже взят: {taken_by_str}", show_alert=True)
        return

    admin_tag = f"@{callback.from_user.username}" if callback.from_user.username else callback.from_user.full_name

    await update_order_status(order_id, status="partner")

    orig = callback.message.caption or callback.message.text or ""
    new_text = orig + f"\n\n➡️ <b>Передал партнёру:</b> {admin_tag}"

    await edit_all_admin_messages(bot, order_id, new_text)
    await callback.answer("➡️ Заявка передана партнёру")

# ─── Запуск бота ───────────────────────────────────────────────────────────────

async def main() -> None:
    bot = Bot(token=BOT_TOKEN)
    dp  = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    await init_db()

    logger.info("Бот запущен. Ожидание сообщений...")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
