import asyncio
import logging
import os
import re
import csv
import io
import sqlite3
import traceback
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery,
    BufferedInputFile
)

BOT_TOKEN = "8822110606:AAEE0ps4NI5XK4YoGJrxOYSK1BCSk3LxBik"
ADMIN_CHAT_ID = -1003709542377

if not BOT_TOKEN:
    raise ValueError("Не задан BOT_TOKEN!")

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

DB_PATH = "/tmp/applications.db"
ADMINS = set()  # если хочешь ограничить админ-команды — впиши ID через запятую


# ═══════════════════════════════════════════
#                  УТИЛИТЫ
# ═══════════════════════════════════════════
def esc(s) -> str:
    """Экранирование HTML."""
    if s is None:
        return ""
    return (str(s)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;"))


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def divider() -> str:
    return "━━━━━━━━━━━━━━━━━━━━━━━━━━"


# ═══════════════════════════════════════════
#                  БАЗА
# ═══════════════════════════════════════════
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
                bio_sentences INTEGER,
                bio_length INTEGER,
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


def save_application(user, data, verdict, reason):
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO applications "
            "(telegram_id, username, nickname, discord, age, online, "
            "bio_sentences, bio_length, verdict, reason, applied_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user.id, user.username or "",
             data["nickname"], data["discord"],
             data["age"] or 0, str(data["online"] or ""),
             data["bio_sentences"], data["bio_length"],
             verdict, "; ".join(reason)[:500], now_str())
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logging.error(f"save_application error: {e}")


def get_stats() -> dict:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM applications")
    total = cur.fetchone()[0] or 0
    cur.execute("SELECT COUNT(*) FROM applications WHERE verdict='ОТКАЗ'")
    rejected = cur.fetchone()[0] or 0
    cur.execute("SELECT COUNT(*) FROM applications WHERE verdict='ПРИНЯТА'")
    accepted = cur.fetchone()[0] or 0
    today = datetime.now().strftime("%Y-%m-%d")
    cur.execute("SELECT COUNT(*) FROM applications WHERE applied_at LIKE ?", (f"{today}%",))
    today_count = cur.fetchone()[0] or 0
    conn.close()
    return {
        "total": total,
        "rejected": rejected,
        "accepted": accepted,
        "today": today_count
    }


def get_top(limit=10) -> list:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT nickname, discord, applied_at FROM applications "
        "WHERE verdict='ПРИНЯТА' ORDER BY id DESC LIMIT ?",
        (limit,)
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def export_csv() -> bytes:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT id, telegram_id, username, nickname, discord,
               age, online, bio_sentences, bio_length, verdict, applied_at
        FROM applications ORDER BY id DESC
    """)
    rows = cur.fetchall()
    conn.close()

    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow([
        "ID", "TG_ID", "Username", "Nickname", "Discord",
        "Age", "Online", "Bio_sentences", "Bio_length", "Verdict", "Applied_at"
    ])
    for r in rows:
        writer.writerow(r)
    return buf.getvalue().encode("utf-8")


init_db()


# ═══════════════════════════════════════════
#                СОСТОЯНИЯ
# ═══════════════════════════════════════════
class Check(StatesGroup):
    waiting_application = State()


# ═══════════════════════════════════════════
#               КЛАВИАТУРЫ
# ═══════════════════════════════════════════
def main_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📝 Проверить заявку")],
            [KeyboardButton(text="📊 Статистика"), KeyboardButton(text="🏆 Топ")],
            [KeyboardButton(text="📖 Инструкция")]
        ],
        resize_keyboard=True
    )


def cancel_kb():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Отмена")]],
        resize_keyboard=True
    )


def admin_kb():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Принять", callback_data="accept"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data="reject"),
    ], [
        InlineKeyboardButton(text="📊 Статистика", callback_data="stats"),
        InlineKeyboardButton(text="🏆 Топ", callback_data="top"),
    ]])


def back_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🏠 В меню", callback_data="menu")
    ]])


# ═══════════════════════════════════════════
#               ПАРСЕР
# ═══════════════════════════════════════════
def parse_application(text: str) -> dict:
    lower = text.lower()
    data = {}

    # Ник
    m = re.search(r"(?:ник\s*name|nickname|ник)[:\s]*([^\n]+)", text, re.IGNORECASE)
    data["nickname"] = m.group(1).strip() if m else ""

    # Discord
    m = re.search(r"(?:discord|дискорд)[:\s]*([^\n]+)", text, re.IGNORECASE)
    data["discord"] = m.group(1).strip() if m else ""

    # Реальный возраст — только из строки со словом "возраст"
    age = None
    for line in text.splitlines():
        if re.search(r"возраст", line, re.IGNORECASE):
            m = re.search(r"(\d{1,2})", line)
            if m:
                age = int(m.group(1))
                break
    data["age"] = age

    # Онлайн — из строки со словом "онлайн"
    online = None
    for line in text.splitlines():
        if re.search(r"онлайн", line, re.IGNORECASE):
            m = re.search(r"(\d{1,2})\s*(?:час|ч\.|hours?)", line, re.IGNORECASE)
            if not m:
                m = re.search(r"(\d{1,2})", line)
            if m:
                online = int(m.group(1))
                break
    data["online"] = online

    # Био
    bio_match = re.search(
        r"биограф[^\n]*[:\n]+(.*?)(?=\n\s*\n|\n[А-ЯЁA-Z][^\n]{0,40}:|$)",
        text, re.DOTALL | re.IGNORECASE
    )
    bio = bio_match.group(1).strip() if bio_match else ""
    data["bio"] = bio
    data["bio_sentences"] = len(re.findall(r"[.!?]+", bio))
    data["bio_length"] = len(bio)

    # Обязательные пункты
    required = {
        "NickName": r"ник\s*name|nickname|ник",
        "Скриншот статистики": r"скриншот|статистик|imgur|yapix|номер аккаунта",
        "Биография": r"биограф",
        "Реальный возраст": r"реальн\w*\s*(?:имя|возраст)|возраст",
        "Локация": r"стран|город|часов\w*\s*пояс",
        "Онлайн": r"онлайн",
        "Discord": r"discord|дискорд",
        "Правила": r"правил",
    }
    data["missing"] = [n for n, p in required.items() if not re.search(p, lower)]
    return data


# ═══════════════════════════════════════════
#                ВЕРДИКТ
# ═══════════════════════════════════════════
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
        reasons.append("🎂 <b>Реальный возраст не указан</b>")
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
    return "🟢", ["Все критерии пройдены ✅"], "ПРИНЯТА"


# ═══════════════════════════════════════════
#            ФОРМИРОВАНИЕ КАРТОЧКИ
# ═══════════════════════════════════════════
def build_result_card(data, reasons, status, emoji):
    header = (
        "╔════════════════════════════╗\n"
        f"     {emoji} <b>{status}</b>\n"
        "╚════════════════════════════╝\n"
    )
    info_block = (
        "\n📊 <b>РАСПОЗНАНО:</b>\n"
        f"  ├ Ник: <b>{esc(data['nickname']) or '—'}</b>\n"
        f"  ├ Discord: <b>{esc(data['discord']) or '—'}</b>\n"
        f"  ├ Возраст: <b>{data['age'] if data['age'] else '—'}</b>\n"
        f"  ├ Онлайн: <b>{data['online'] if data['online'] else '—'}</b> ч\n"
        f"  └ Био: <b>{data['bio_sentences']}</b> предл. / <b>{data['bio_length']}</b> симв.\n\n"
    )
    if status == "ОТКАЗ":
        reasons_block = "❌ <b>ПРИЧИНЫ ОТКАЗА:</b>\n" + "\n".join(f"  • {r}" for r in reasons) + "\n"
    else:
        reasons_block = "✅ <b>ВСЁ ПРОШЛО:</b>\n" + "\n".join(f"  • {r}" for r in reasons) + "\n"

    footer = f"\n{divider()}\n🤖 <i>Решение вынесено автоматически по формальным критериям.</i>"
    return header + info_block + reasons_block + footer


# ═══════════════════════════════════════════
#                СТАРТ
# ═══════════════════════════════════════════
@dp.message(CommandStart())
async def start(message: types.Message, state: FSMContext):
    await state.clear()
    text = (
        "╔════════════════════════════╗\n"
        "     🎯 <b>ПРОВЕРКА ЗАЯВОК ОПГ</b> 🎯\n"
        "╚════════════════════════════╝\n\n"
        "👋 Привет! Я автоматически проверяю заявки на пост <b>Лидера ОПГ</b>.\n\n"
        "🔍 <b>Автопроверка:</b>\n"
        "  • 📝 Био: 5+ предложений и 200+ символов\n"
        "  • 🎂 Реальный возраст: 14+\n"
        "  • ⏰ Онлайн: 3+ часов\n"
        "  • 📋 Наличие всех пунктов\n"
        "  • 🔁 Дубликаты заявок\n\n"
        "📥 <b>Как пользоваться:</b>\n"
        "Нажми «📝 Проверить заявку» и скинь текст заявки из Discord.\n\n"
        f"{divider()}\n"
        "💡 Выбери действие в меню ниже 👇"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=main_kb())


# ═══════════════════════════════════════════
#             КНОПКИ МЕНЮ
# ═══════════════════════════════════════════
@dp.message(F.text == "📝 Проверить заявку")
async def ask_application(message: types.Message, state: FSMContext):
    await state.set_state(Check.waiting_application)
    await message.answer(
        "📥 <b>Кидай текст заявки</b> одним сообщением.\n\n"
        "💡 Просто скопируй всю заявку из Discord и отправь сюда.",
        parse_mode="HTML",
        reply_markup=cancel_kb()
    )


@dp.message(F.text == "📊 Статистика")
async def stats_button(message: types.Message):
    s = get_stats()
    text = (
        "╔════════════════════════════╗\n"
        "     📊 <b>СТАТИСТИКА ЗАЯВОК</b>\n"
        "╚════════════════════════════╝\n\n"
        f"  ├ Всего: <b>{s['total']}</b>\n"
        f"  ├ Принято: <b>{s['accepted']}</b>\n"
        f"  ├ Отказов: <b>{s['rejected']}</b>\n"
        f"  └ Сегодня: <b>{s['today']}</b>"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=back_menu_kb())


@dp.message(F.text == "🏆 Топ")
async def top_button(message: types.Message):
    rows = get_top(10)
    if not rows:
        await message.answer("🏆 Пока нет принятых заявок.", reply_markup=back_menu_kb())
        return
    text = (
        "╔════════════════════════════╗\n"
        "     🏆 <b>ТОП ПРИНЯТЫХ ЗАЯВОК</b>\n"
        "╚════════════════════════════╝\n\n"
    )
    for i, (nick, disc, dt) in enumerate(rows, 1):
        text += f"{i}. <b>{esc(nick)}</b> — {esc(dt)}\n"
    await message.answer(text, parse_mode="HTML", reply_markup=back_menu_kb())


@dp.message(F.text == "📖 Инструкция")
async def instruction(message: types.Message):
    text = (
        "📖 <b>КАК ПОЛЬЗОВАТЬСЯ</b>\n\n"
        f"{divider()}\n"
        "1️⃣ Нажми <b>«📝 Проверить заявку»</b>.\n"
        "2️⃣ Скопируй заявку из Discord.\n"
        "3️⃣ Отправь её боту одним сообщением.\n"
        "4️⃣ Получи вердикт: <b>ПРИНЯТА</b> или <b>ОТКАЗ</b>.\n\n"
        f"{divider()}\n"
        "<b>Что проверяется автоматически:</b>\n"
        "  • Наличие всех обязательных пунктов\n"
        "  • Био: количество предложений и длина\n"
        "  • Возраст из пункта «Реальный возраст»\n"
        "  • Онлайн из пункта «Онлайн»\n"
        "  • Дубликаты заявок\n\n"
        f"{divider()}\n"
        "<b>Что проверяется вручную админом:</b>\n"
        "  • Скриншот ↔ ник\n"
        "  • Уровень 10+\n"
        "  • Наказания (BAN/WARN)\n"
        "  • Адекватность и грамотность"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=back_menu_kb())


@dp.message(F.text == "❌ Отмена")
async def cancel(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Отменено. Возвращаю в меню.", reply_markup=main_kb())


# ═══════════════════════════════════════════
#              ПРОВЕРКА
# ═══════════════════════════════════════════
@dp.message(Check.waiting_application)
async def check_application(message: types.Message, state: FSMContext):
    try:
        text = message.text or message.caption or ""
        if len(text.strip()) < 30:
            await message.answer("❌ Сообщение слишком короткое. Пришли полный текст заявки.")
            return

        data = parse_application(text)
        duplicate = find_duplicate(data["nickname"], data["discord"])
        emoji, reasons, status = make_verdict(data, duplicate)

        result = build_result_card(data, reasons, status, emoji)
        await message.answer(result, parse_mode="HTML", reply_markup=main_kb())

        save_application(message.from_user, data, status, reasons)

        # Отправка в админ-группу
        if ADMIN_CHAT_ID:
            safe_text = esc(text[:3000])
            user = message.from_user
            admin_text = (
                f"{build_result_card(data, reasons, status, emoji)}\n\n"
                f"{divider()}\n"
                f"👤 От: @{esc(user.username) or 'без_юз'} "
                f"(<code>{user.id}</code>)\n\n"
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

        await state.clear()

    except Exception as e:
        err = traceback.format_exc()
        logging.error(f"check_application error:\n{err}")
        await message.answer(
            f"⚠️ <b>Ошибка:</b>\n<code>{esc(str(e))[:300]}</code>",
            parse_mode="HTML"
        )


# ═══════════════════════════════════════════
#              КНОПКИ АДМИНА
# ═══════════════════════════════════════════
@dp.callback_query(F.data == "accept")
async def cb_accept(call: CallbackQuery):
    await call.answer("✅ Принято")
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await call.message.reply(
        f"✅ <b>Заявка одобрена</b>\n"
        f"Админ: @{esc(call.from_user.username) or call.from_user.id}",
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
        f"❌ <b>Заявка отклонена</b>\n"
        f"Админ: @{esc(call.from_user.username) or call.from_user.id}",
        parse_mode="HTML"
    )


@dp.callback_query(F.data == "stats")
async def cb_stats(call: CallbackQuery):
    await call.answer()
    s = get_stats()
    text = (
        "📊 <b>Статистика</b>\n\n"
        f"  ├ Всего: <b>{s['total']}</b>\n"
        f"  ├ Принято: <b>{s['accepted']}</b>\n"
        f"  ├ Отказов: <b>{s['rejected']}</b>\n"
        f"  └ Сегодня: <b>{s['today']}</b>"
    )
    await call.message.reply(text, parse_mode="HTML")


@dp.callback_query(F.data == "top")
async def cb_top(call: CallbackQuery):
    await call.answer()
    rows = get_top(10)
    if not rows:
        await call.message.reply("🏆 Пока нет принятых заявок.")
        return
    text = "🏆 <b>Топ принятых:</b>\n\n"
    for i, (nick, disc, dt) in enumerate(rows, 1):
        text += f"{i}. <b>{esc(nick)}</b> — {esc(dt)}\n"
    await call.message.reply(text, parse_mode="HTML")


@dp.callback_query(F.data == "menu")
async def cb_menu(call: CallbackQuery):
    await call.answer()
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await call.message.answer("🏠 В меню.", reply_markup=main_kb())


# ═══════════════════════════════════════════
#             КОМАНДЫ АДМИНА
# ═══════════════════════════════════════════
@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    s = get_stats()
    await message.answer(
        "📊 <b>Статистика</b>\n\n"
        f"  ├ Всего: <b>{s['total']}</b>\n"
        f"  ├ Принято: <b>{s['accepted']}</b>\n"
        f"  ├ Отказов: <b>{s['rejected']}</b>\n"
        f"  └ Сегодня: <b>{s['today']}</b>",
        parse_mode="HTML"
    )


@dp.message(Command("top"))
async def cmd_top(message: types.Message):
    rows = get_top(10)
    if not rows:
        await message.answer("🏆 Пока нет принятых заявок.")
        return
    text = "🏆 <b>Топ-10 принятых:</b>\n\n"
    for i, (nick, disc, dt) in enumerate(rows, 1):
        text += f"{i}. <b>{esc(nick)}</b> — {esc(dt)}\n"
    await message.answer(text, parse_mode="HTML")


@dp.message(Command("export"))
async def cmd_export(message: types.Message):
    try:
        data = export_csv()
        file = BufferedInputFile(data, filename=f"applications_{datetime.now():%Y%m%d_%H%M}.csv")
        await message.answer_document(file, caption="📁 Экспорт заявок")
    except Exception as e:
        await message.answer(f"⚠️ Ошибка экспорта: {esc(e)}")


@dp.message(Command("menu"))
async def cmd_menu(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("🏠 Главное меню:", reply_markup=main_kb())


# ═══════════════════════════════════════════
#              FALLBACK
# ═══════════════════════════════════════════
@dp.message()
async def fallback(message: types.Message, state: FSMContext):
    await message.answer(
        "💡 Нажми /menu или выбери действие в меню.",
        reply_markup=main_kb()
    )


# ═══════════════════════════════════════════
#                ЗАПУСК
# ═══════════════════════════════════════════
async def main():
    logging.info("Bot started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
