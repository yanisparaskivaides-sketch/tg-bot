import asyncio
import logging
import os
import re
import sqlite3
from datetime import datetime
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
    InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
)

BOT_TOKEN = "8822110606:AAEE0ps4NI5XK4YoGJrxOYSK1BCSk3LxBik"
ADMIN_CHAT_ID = -1003709542377

if not BOT_TOKEN:
    raise ValueError("Не задан BOT_TOKEN!")

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

DB_PATH = "applications.db"


# ---------- БАЗА ДАННЫХ ----------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER,
            username TEXT,
            nickname TEXT,
            discord TEXT,
            age INTEGER,
            online TEXT,
            verdict TEXT,
            reason TEXT,
            applied_at TEXT
        )
    """)
    conn.commit()
    conn.close()


def find_duplicate(nickname, discord):
    """Ищет дубликат по нику ИЛИ discord."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT nickname, discord, applied_at, verdict FROM applications "
        "WHERE LOWER(nickname) = LOWER(?) OR LOWER(discord) = LOWER(?) "
        "ORDER BY id DESC LIMIT 1",
        (nickname, discord)
    )
    row = cur.fetchone()
    conn.close()
    return row


def save_application(tg_id, username, nickname, discord, age, online, verdict, reason):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO applications "
        "(telegram_id, username, nickname, discord, age, online, verdict, reason, applied_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (tg_id, username, nickname, discord, age, online, verdict, reason,
         datetime.now().strftime("%Y-%m-%d %H:%M"))
    )
    conn.commit()
    conn.close()


init_db()


# ---------- СОСТОЯНИЯ ----------
class Check(StatesGroup):
    waiting_application = State()


# ---------- КЛАВИАТУРЫ ----------
def main_kb():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Отмена")]],
        resize_keyboard=True
    )


def admin_kb(app_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Принять", callback_data=f"accept:{app_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject:{app_id}"),
        ]
    ])


# ---------- ПАРСЕР ЗАЯВКИ ----------
def parse_application(text: str) -> dict:
    """Извлекает поля из текста заявки."""
    lower = text.lower()
    data = {}

    # Ник
    m = re.search(r"(?:ник\s*name|nickname|ник)[:\s]*([^\n]+)", text, re.IGNORECASE)
    data["nickname"] = m.group(1).strip() if m else ""

    # Discord
    m = re.search(r"(?:discord|дискорд)[:\s]*([^\n]+)", text, re.IGNORECASE)
    data["discord"] = m.group(1).strip() if m else ""

    # Возраст
    age = None
    m = re.search(r"возраст[^\d]{0,10}(\d{1,2})", lower)
    if not m:
        m = re.search(r"(\d{1,2})\s*(?:лет|года|год|y\.?o\.?)", lower)
    if m:
        age = int(m.group(1))
    data["age"] = age

    # Онлайн
    online = None
    m = re.search(r"(\d{1,2})\s*(?:час|ч\.|hours?)", lower)
    if m:
        online = int(m.group(1))
    data["online"] = online

    # Биография
    bio_match = re.search(
        r"биограф[^\n]*[:\n]+(.*?)(?=\n\s*\n|\n[А-ЯЁA-Z][^\n]{0,40}:|$)",
        text, re.DOTALL | re.IGNORECASE
    )
    bio = bio_match.group(1).strip() if bio_match else ""
    data["bio"] = bio
    data["bio_sentences"] = len(re.findall(r"[.!?]+", bio))
    data["bio_length"] = len(bio)

    # Обязательные поля
    required = {
        "NickName": r"ник\s*name|nickname|ник",
        "Скриншот статистики": r"скриншот|статистик|imgur|yapix|номер аккаунта",
        "Биография": r"биограф",
        "Имя и возраст": r"реальн\w*\s*имя|имя\s*и\s*возраст|возраст",
        "Локация": r"стран|город|часов\w*\s*пояс",
        "Онлайн": r"онлайн",
        "Discord": r"discord|дискорд",
        "Правила": r"правил",
    }
    missing = [name for name, pat in required.items() if not re.search(pat, lower)]
    data["missing"] = missing

    return data


# ---------- ВЕРДИКТ ----------
def make_verdict(data: dict, duplicate: tuple | None) -> tuple[str, list[str], str]:
    """Возвращает (вердикт, список причин, короткий статус)."""
    reasons = []

    # Дубликат
    if duplicate:
        reasons.append(
            f"🔁 <b>Дубликат заявки</b>\n"
            f"Ранее подавал: <b>{duplicate[0]}</b> (Discord: {duplicate[1]})\n"
            f"Дата: {duplicate[2]} — статус: {duplicate[3]}"
        )

    # Био
    if data["bio_sentences"] < 5:
        reasons.append(
            f"📝 <b>НРП био</b> — только {data['bio_sentences']} предложений (нужно 5+)"
        )
    elif data["bio_length"] < 200:
        reasons.append(
            f"📝 <b>НРП био</b> — слишком короткая ({data['bio_length']} символов, нужно 200+)"
        )

    # Возраст
    if data["age"] is None:
        reasons.append("🎂 <b>Возраст не указан</b> — проверь вручную")
    elif data["age"] < 14:
        reasons.append(f"🎂 <b>Возраст {data['age']}</b> — меньше 14 лет")

    # Онлайн
    if data["online"] is None:
        reasons.append("⏰ <b>Онлайн не указан</b> — проверь вручную")
    elif data["online"] < 3:
        reasons.append(f"⏰ <b>Онлайн {data['online']} ч</b> — меньше 3 часов")

    # Пропущенные поля
    if data["missing"]:
        reasons.append(
            "📋 <b>Отсутствуют пункты:</b> " + ", ".join(data["missing"])
        )

    if reasons:
        return "🔴", reasons, "ОТКАЗ"
    return "🟢", ["Все критерии пройдены ✅"], "ПРИНЯТА НА РУЧНУЮ ПРОВЕРКУ"


# ---------- СТАРТ ----------
@dp.message(CommandStart())
async def start(message: types.Message, state: FSMContext):
    await state.clear()
    text = (
        "╔══════════════════════════╗\n"
        "   🎯 <b>ПРОВЕРКА ЗАЯВОК ОПГ</b> 🎯\n"
        "╚══════════════════════════╝\n\n"
        "👋 Привет! Я проверяю заявки на пост <b>Лидера ОПГ</b>.\n\n"
        "📥 <b>Как пользоваться:</b>\n"
        "Просто скинь мне <b>текст заявки</b> из Discord — я проверю её по всем критериям.\n\n"
        "🔍 <b>Что я проверяю автоматически:</b>\n"
        "  • 📝 Биография от 5 предложений (и 200+ символов)\n"
        "  • 🎂 Реальный возраст 14+\n"
        "  • ⏰ Онлайн от 3 часов\n"
        "  • 📋 Наличие всех обязательных пунктов\n"
        "  • 🔁 Дубликаты заявок\n\n"
        "⚠️ <b>Что проверяешь ты:</b>\n"
        "  • Адекватность и грамотность\n"
        "  • Ник со скриншота = ник в заявке\n"
        "  • Уровень 10+, наказания, знание правил\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "✍️ <b>Кидай заявку — начинаю проверку!</b>"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=main_kb())
    await state.set_state(Check.waiting_application)


# ---------- ОТМЕНА ----------
@dp.message(F.text == "❌ Отмена")
async def cancel(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Отменено.\nНачать заново: /start")


# ---------- ПРОВЕРКА ЗАЯВКИ ----------
@dp.message(Check.waiting_application)
async def check_application(message: types.Message, state: FSMContext):
    text = message.text or message.caption or ""
    if len(text.strip()) < 30:
        await message.answer(
            "❌ Сообщение слишком короткое.\n"
            "Пришли полный текст заявки."
        )
        return

    await message.answer("⏳ <b>Проверяю заявку...</b>", parse_mode="HTML")

    data = parse_application(text)
    duplicate = find_duplicate(data["nickname"], data["discord"]) if (data["nickname"] or data["discord"]) else None
    verdict_emoji, reasons, status = make_verdict(data, duplicate)

    # Формируем красивый ответ
    header = (
        "╔══════════════════════════╗\n"
        f"   {verdict_emoji} <b>{status}</b>\n"
        "╚══════════════════════════╝\n\n"
    )

    # Блок «распознано»
    info_block = (
        "📊 <b>РАСПОЗНАНО:</b>\n"
        f"  ├ Ник: <b>{data['nickname'] or '—'}</b>\n"
        f"  ├ Discord: <b>{data['discord'] or '—'}</b>\n"
        f"  ├ Возраст: <b>{data['age'] or '—'}</b>\n"
        f"  ├ Онлайн: <b>{data['online'] or '—'}</b> ч\n"
        f"  └ Био: <b>{data['bio_sentences']}</b> предложений / <b>{data['bio_length']}</b> символов\n\n"
    )

    # Блок «причины»
    if status == "ОТКАЗ":
        reasons_block = "❌ <b>ПРИЧИНЫ ОТКАЗА:</b>\n" + "\n".join(f"  • {r}" for r in reasons) + "\n\n"
    else:
        reasons_block = "✅ <b>ВСЕ КРИТЕРИИ ПРОЙДЕНЫ:</b>\n" + "\n".join(f"  • {r}" for r in reasons) + "\n\n"

    footer = (
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "👤 <b>Проверь вручную:</b>\n"
        "  • Скриншот ↔ ник\n"
        "  • Уровень 10+\n"
        "  • Наказания (BAN/WARN)\n"
        "  • Адекватность и грамотность\n"
    )

    result = header + info_block + reasons_block + footer
    await message.answer(result, parse_mode="HTML")

    # Сохраняем в БД
    user = message.from_user
    save_application(
        user.id, user.username or "",
        data["nickname"], data["discord"],
        data["age"] or 0, str(data["online"] or ""),
        status, "; ".join(reasons)[:500]
    )

    # Отправка в админ-группу
    if ADMIN_CHAT_ID:
        admin_text = (
            f"{header}"
            f"👤 От: @{user.username or 'без_юзернейма'} (<code>{user.id}</code>)\n\n"
            f"{info_block}"
            f"{reasons_block}"
            f"━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📄 <b>Оригинал заявки:</b>\n"
            f"<blockquote>{text[:3500]}</blockquote>"
        )
        try:
            await bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=admin_text,
                parse_mode="HTML",
                reply_markup=admin_kb(user.id)
            )
        except Exception as e:
            logging.error(f"Ошибка отправки в админ-чат: {e}")

    await message.answer(
        "💬 Можешь прислать следующую заявку.\n"
        "Или напиши /start для новых инструкций.",
        reply_markup=main_kb()
    )


# ---------- КНОПКИ В АДМИН-ГРУППЕ ----------
@dp.callback_query(F.data.startswith("accept:"))
async def cb_accept(call: CallbackQuery):
    await call.answer("✅ Принято")
    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.reply(
        f"✅ <b>Заявка одобрена</b>\nАдмин: @{call.from_user.username or call.from_user.id}",
        parse_mode="HTML"
    )


@dp.callback_query(F.data.startswith("reject:"))
async def cb_reject(call: CallbackQuery):
    await call.answer("❌ Отклонено")
    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.reply(
        f"❌ <b>Заявка отклонена</b>\nАдмин: @{call.from_user.username or call.from_user.id}",
        parse_mode="HTML"
    )


# ---------- КОМАНДА /stats ----------
@dp.message(Command("stats"))
async def stats(message: types.Message):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM applications")
    total = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM applications WHERE verdict='ОТКАЗ'")
    rejected = cur.fetchone()[0]
    conn.close()
    await message.answer(
        f"📊 <b>Статистика заявок</b>\n\n"
        f"  ├ Всего: <b>{total}</b>\n"
        f"  ├ Отказов: <b>{rejected}</b>\n"
        f"  └ Прошли: <b>{total - rejected}</b>",
        parse_mode="HTML"
    )


@dp.message()
async def fallback(message: types.Message, state: FSMContext):
    await message.answer("💡 Нажми /start, чтобы начать проверку заявки.")


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
