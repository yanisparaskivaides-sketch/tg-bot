import asyncio
import logging
import os
import re
import sqlite3
import traceback
from datetime import datetime
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
)

BOT_TOKEN = "8822110606:AAEE0ps4NI5XK4YoGJrxOYSK1BCSk3LxBik"
ADMIN_CHAT_ID = -1003709542377

if not BOT_TOKEN:
    raise ValueError("Не задан BOT_TOKEN!")

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

DB_PATH = "/tmp/applications.db"


# ---------- HTML ЭКРАНИРОВАНИЕ ----------
def esc(s) -> str:
    """Безопасный текст для HTML-разметки Telegram."""
    if s is None:
        return ""
    return (str(s)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;"))


# ---------- БАЗА ----------
def init_db():
    try:
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
        logging.info("DB initialized OK")
    except Exception as e:
        logging.error(f"DB init error: {e}")


def find_duplicate(nickname, discord):
    try:
        if not nickname and not discord:
            return None
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute(
            "SELECT nickname, discord, applied_at, verdict FROM applications "
            "WHERE LOWER(nickname) = LOWER(?) OR LOWER(discord) = LOWER(?) "
            "ORDER BY id DESC LIMIT 1",
            (nickname or "", discord or "")
        )
        row = cur.fetchone()
        conn.close()
        return row
    except Exception as e:
        logging.error(f"find_duplicate error: {e}")
        return None


def save_application(tg_id, username, nickname, discord, age, online, verdict, reason):
    try:
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
    except Exception as e:
        logging.error(f"save_application error: {e}")


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


def admin_kb():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Принять", callback_data="accept"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data="reject"),
    ]])


# ---------- ПАРСЕР ----------
def parse_application(text: str) -> dict:
    lower = text.lower()
    data = {}

    m = re.search(r"(?:ник\s*name|nickname|ник)[:\s]*([^\n]+)", text, re.IGNORECASE)
    data["nickname"] = m.group(1).strip() if m else ""

    m = re.search(r"(?:discord|дискорд)[:\s]*([^\n]+)", text, re.IGNORECASE)
    data["discord"] = m.group(1).strip() if m else ""

    age = None
    m = re.search(r"возраст[^\d]{0,10}(\d{1,2})", lower)
    if not m:
        m = re.search(r"(\d{1,2})\s*(?:лет|года|год|y\.?o\.?)", lower)
    if m:
        age = int(m.group(1))
    data["age"] = age

    online = None
    m = re.search(r"(\d{1,2})\s*(?:час|ч\.|hours?)", lower)
    if m:
        online = int(m.group(1))
    data["online"] = online

    bio_match = re.search(
        r"биограф[^\n]*[:\n]+(.*?)(?=\n\s*\n|\n[А-ЯЁA-Z][^\n]{0,40}:|$)",
        text, re.DOTALL | re.IGNORECASE
    )
    bio = bio_match.group(1).strip() if bio_match else ""
    data["bio"] = bio
    data["bio_sentences"] = len(re.findall(r"[.!?]+", bio))
    data["bio_length"] = len(bio)

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
    data["missing"] = [n for n, p in required.items() if not re.search(p, lower)]
    return data


# ---------- ВЕРДИКТ ----------
def make_verdict(data: dict, duplicate) -> tuple:
    reasons = []

    if duplicate and len(duplicate) >= 4:
        reasons.append(
            f"🔁 <b>Дубликат</b> — уже подавал: "
            f"<b>{esc(duplicate[0])}</b> (Discord: {esc(duplicate[1])}), "
            f"{esc(duplicate[2])}, статус: {esc(duplicate[3])}"
        )

    if data["bio_sentences"] < 5:
        reasons.append(
            f"📝 <b>НРП био</b> — только <b>{data['bio_sentences']}</b> предложений (нужно 5+)"
        )
    elif data["bio_length"] < 200:
        reasons.append(
            f"📝 <b>НРП био</b> — слишком короткая "
            f"(<b>{data['bio_length']}</b> символов, нужно 200+)"
        )

    if data["age"] is None:
        reasons.append("🎂 <b>Возраст не указан</b>")
    elif data["age"] < 14:
        reasons.append(f"🎂 <b>Возраст {data['age']}</b> — меньше 14")

    if data["online"] is None:
        reasons.append("⏰ <b>Онлайн не указан</b>")
    elif data["online"] < 3:
        reasons.append(f"⏰ <b>Онлайн {data['online']} ч</b> — меньше 3")

    if data["missing"]:
        reasons.append("📋 <b>Отсутствуют пункты:</b> " + ", ".join(data["missing"]))

    if reasons:
        return "🔴", reasons, "ОТКАЗ"
    return "🟢", ["Все критерии пройдены ✅"], "ПРОШЛА АВТОПРОВЕРКУ"


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
        "Скинь мне <b>текст заявки</b> из Discord — я проверю её по всем критериям.\n\n"
        "🔍 <b>Автопроверка:</b>\n"
        "  • 📝 Био от 5 предложений и 200+ символов\n"
        "  • 🎂 Возраст 14+\n"
        "  • ⏰ Онлайн 3+ часов\n"
        "  • 📋 Наличие всех пунктов\n"
        "  • 🔁 Дубликаты заявок\n\n"
        "⚠️ <b>Проверяешь сам:</b> адекватность, грамотность, ник со скрина = ник в заявке, уровень 10+, наказания.\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "✍️ <b>Кидай заявку!</b>"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=main_kb())
    await state.set_state(Check.waiting_application)


@dp.message(F.text == "❌ Отмена")
async def cancel(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Отменено.\nНачать заново: /start")


# ---------- ПРОВЕРКА ----------
@dp.message(Check.waiting_application)
async def check_application(message: types.Message, state: FSMContext):
    try:
        text = message.text or message.caption or ""
        if len(text.strip()) < 30:
            await message.answer("❌ Сообщение слишком короткое. Пришли полный текст заявки.")
            return

        data = parse_application(text)
        duplicate = find_duplicate(data["nickname"], data["discord"])
        verdict_emoji, reasons, status = make_verdict(data, duplicate)

        header = (
            "╔══════════════════════════╗\n"
            f"   {verdict_emoji} <b>{status}</b>\n"
            "╚══════════════════════════╝\n\n"
        )

        info_block = (
            "📊 <b>РАСПОЗНАНО:</b>\n"
            f"  ├ Ник: <b>{esc(data['nickname']) or '—'}</b>\n"
            f"  ├ Discord: <b>{esc(data['discord']) or '—'}</b>\n"
            f"  ├ Возраст: <b>{data['age'] if data['age'] else '—'}</b>\n"
            f"  ├ Онлайн: <b>{data['online'] if data['online'] else '—'}</b> ч\n"
            f"  └ Био: <b>{data['bio_sentences']}</b> предл. / <b>{data['bio_length']}</b> симв.\n\n"
        )

        if status == "ОТКАЗ":
            reasons_block = "❌ <b>ПРИЧИНЫ ОТКАЗА:</b>\n" + "\n".join(f"  • {r}" for r in reasons) + "\n\n"
        else:
            reasons_block = "✅ <b>ВСЁ ПРОШЛО:</b>\n" + "\n".join(f"  • {r}" for r in reasons) + "\n\n"

        footer = (
            "━━━━━━━━━━━━━━━━━━━━━━━\n"
            "👤 <b>Проверь вручную:</b>\n"
            "  • Скриншот ↔ ник\n"
            "  • Уровень 10+\n"
            "  • Наказания (BAN/WARN)\n"
            "  • Адекватность и грамотность"
        )

        result = header + info_block + reasons_block + footer
        await message.answer(result, parse_mode="HTML")

        user = message.from_user
        save_application(
            user.id, user.username or "",
            data["nickname"], data["discord"],
            data["age"] or 0, str(data["online"] or ""),
            status, "; ".join(reasons)[:500]
        )

        if ADMIN_CHAT_ID:
            safe_text = esc(text[:3000])
            admin_text = (
                f"{header}"
                f"👤 От: @{esc(user.username) or 'без_юз'} (<code>{user.id}</code>)\n\n"
                f"{info_block}"
                f"{reasons_block}"
                f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📄 <b>Оригинал:</b>\n"
                f"<blockquote>{safe_text}</blockquote>"
            )
            try:
                await bot.send_message(
                    chat_id=ADMIN_CHAT_ID,
                    text=admin_text,
                    parse_mode="HTML",
                    reply_markup=admin_kb()
                )
            except Exception as e:
                logging.error(f"send to admin error: {e}")

        await message.answer("💬 Кидай следующую заявку или /start.", reply_markup=main_kb())

    except Exception as e:
        err = traceback.format_exc()
        logging.error(f"check_application error:\n{err}")
        await message.answer(
            f"⚠️ <b>Ошибка при проверке:</b>\n<code>{esc(str(e))[:300]}</code>\n\n"
            f"Скинь этот текст разработчику.",
            parse_mode="HTML"
        )


# ---------- КНОПКИ АДМИНА ----------
@dp.callback_query(F.data == "accept")
async def cb_accept(call: CallbackQuery):
    await call.answer("✅ Принято")
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await call.message.reply(
        f"✅ <b>Заявка одобрена</b> — @{esc(call.from_user.username) or call.from_user.id}",
        parse_mode="HTML"
    )


@dp.callback_query(F.data == "reject")
async def cb_reject(call: CallbackQuery):
    await call.answer("❌ Отклонено")
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await call.message.reply(
        f"❌ <b>Заявка отклонена</b> — @{esc(call.from_user.username) or call.from_user.id}",
        parse_mode="HTML"
    )


# ---------- /stats ----------
@dp.message(Command("stats"))
async def stats(message: types.Message):
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM applications")
        total = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM applications WHERE verdict='ОТКАЗ'")
        rejected = cur.fetchone()[0]
        conn.close()
        await message.answer(
            f"📊 <b>Статистика</b>\n\n"
            f"  ├ Всего: <b>{total}</b>\n"
            f"  ├ Отказов: <b>{rejected}</b>\n"
            f"  └ Прошли: <b>{total - rejected}</b>",
            parse_mode="HTML"
        )
    except Exception as e:
        await message.answer(f"⚠️ Ошибка: {esc(e)}")


@dp.message()
async def fallback(message: types.Message, state: FSMContext):
    await message.answer("💡 /start — начать проверку.")


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
