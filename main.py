import asyncio
import logging
import os
import re
import csv
import io
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
    InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery,
    BufferedInputFile
)

# ═══════════════ КОНФИГ ═══════════════
BOT_TOKEN = "8822110606:AAEE0ps4NI5XK4YoGJrxOYSK1BCSk3LxBik"
ADMIN_CHAT_ID = -1003709542377

if not BOT_TOKEN:
    raise ValueError("Не задан BOT_TOKEN!")

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

DB_PATH = "/tmp/applications.db"

MIN_AGE = 14
MIN_ONLINE = 3
MIN_BIO_SENT = 5
MIN_BIO_LEN = 200


# ═══════════════ УТИЛИТЫ ═══════════════
def esc(s) -> str:
    if s is None:
        return ""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def divider() -> str:
    return "━━━━━━━━━━━━━━━━━━━━━━━━━━"


def count_sentences(text: str) -> int:
    return len(re.findall(r"[.!?]+", text))


# ═══════════════ БАЗА ═══════════════
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
        logging.info("DB ready")
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
            " bio_sentences, bio_length, verdict, reason, applied_at) "
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
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM applications")
        total = cur.fetchone()[0] or 0
        cur.execute("SELECT COUNT(*) FROM applications WHERE verdict='ОТКАЗ'")
        rej = cur.fetchone()[0] or 0
        cur.execute("SELECT COUNT(*) FROM applications WHERE verdict='ПРИНЯТА'")
        acc = cur.fetchone()[0] or 0
        today = datetime.now().strftime("%Y-%m-%d")
        cur.execute("SELECT COUNT(*) FROM applications WHERE applied_at LIKE ?", (f"{today}%",))
        t = cur.fetchone()[0] or 0
        conn.close()
        return {"total": total, "rejected": rej, "accepted": acc, "today": t}
    except Exception as e:
        logging.error(f"get_stats error: {e}")
        return {"total": 0, "rejected": 0, "accepted": 0, "today": 0}


def get_top(limit=10):
    try:
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
    except Exception as e:
        logging.error(f"get_top error: {e}")
        return []


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
    w = csv.writer(buf, delimiter=";")
    w.writerow(["ID", "TG_ID", "Username", "Nickname", "Discord",
                "Age", "Online", "Bio_sentences", "Bio_length", "Verdict", "Applied_at"])
    for r in rows:
        w.writerow(r)
    return buf.getvalue().encode("utf-8")


init_db()


# ═══════════════ СОСТОЯНИЯ ═══════════════
class Check(StatesGroup):
    waiting = State()


# ═══════════════ КЛАВИАТУРЫ ═══════════════
def main_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📝 Проверить заявку")],
            [KeyboardButton(text="📊 Статистика"), KeyboardButton(text="🏆 Топ")],
            [KeyboardButton(text="📖 Инструкция")],
        ],
        resize_keyboard=True
    )


def cancel_kb():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Отмена")]],
        resize_keyboard=True
    )


def admin_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Принять", callback_data="accept"),
         InlineKeyboardButton(text="❌ Отклонить", callback_data="reject")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="stats"),
         InlineKeyboardButton(text="🏆 Топ", callback_data="top")],
    ])


def back_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🏠 В меню", callback_data="menu")
    ]])


# ═══════════════ ПАРСЕР ═══════════════
def parse_application(text: str) -> dict:
    """Извлекает поля из текста заявки."""
    lower = text.lower()
    lines = text.splitlines()
    data = {}

    # --- Ник ---
    m = re.search(r"(?:ник\s*name|nickname|ник)[:\s]*([^\n]+)", text, re.IGNORECASE)
    data["nickname"] = m.group(1).strip() if m else ""

    # --- Discord ---
    m = re.search(r"(?:discord|дискорд)[:\s]*([^\n]+)", text, re.IGNORECASE)
    data["discord"] = m.group(1).strip() if m else ""

    # --- ВОЗРАСТ: ТОЛЬКО из строки "Ваше реальное имя и возраст" ---
    age = None
    for line in lines:
        low = line.lower()
        # ищем строку, где есть "реальное имя" И "возраст" — это пункт "Ваше реальное имя и возраст:"
        if ("реальн" in low and "возраст" in low) or re.search(r"имя\s*и\s*возраст", low):
            # берём все числа из строки и выбираем то, что может быть возрастом (10-99)
            nums = re.findall(r"\b(\d{1,2})\b", line)
            for n in nums:
                if 10 <= int(n) <= 99:
                    age = int(n)
                    break
            if age:
                break
    # fallback: если не нашли "реальное имя и возраст", ищем отдельный пункт "возраст:"
    if age is None:
        for line in lines:
            low = line.lower()
            if re.match(r"\s*(?:ваш[а-я]*\s*)?(?:реальн[а-я]*\s*)?возраст\s*[:\-]", low):
                nums = re.findall(r"\b(\d{1,2})\b", line)
                for n in nums:
                    if 10 <= int(n) <= 99:
                        age = int(n)
                        break
                if age:
                    break
    data["age"] = age

    # --- ОНЛАЙН: только из строки со словом "онлайн" ---
    online = None
    for line in lines:
        if re.search(r"онлайн", line, re.IGNORECASE):
            nums = re.findall(r"\b(\d{1,2})\b", line)
            if nums:
                # берём самое большое число из строки (обычно "3-8" → 8, "5 часов" → 5)
                online = max(int(n) for n in nums)
                break
    data["online"] = online

    # --- БИО ---
    bio = ""
    m = re.search(
        r"биограф[^\n]*[:\n]+(.*?)(?=\n\s*\n|\n[А-ЯЁA-Z][^\n]{0,60}:|\Z)",
        text, re.DOTALL | re.IGNORECASE
    )
    if m:
        bio = m.group(1).strip()
    data["bio"] = bio
    data["bio_sentences"] = count_sentences(bio)
    data["bio_length"] = len(bio)

    # --- Обязательные пункты ---
    required = {
        "NickName": r"ник\s*name|nickname|ник",
        "Скриншот статистики": r"скриншот|статистик|imgur|yapix|номер аккаунта",
        "Биография": r"биограф",
        "Реальное имя и возраст": r"реальн\w*\s*имя|имя\s*и\s*возраст",
        "Локация": r"стран|город|часов\w*\s*пояс",
        "Онлайн": r"онлайн",
        "Discord": r"discord|дискорд",
        "Правила": r"правил",
    }
    data["missing"] = [n for n, p in required.items() if not re.search(p, lower)]
    return data


# ═══════════════ ВЕРДИКТ ═══════════════
def make_verdict(data, duplicate) -> tuple:
    reasons = []

    if duplicate and len(duplicate) >= 4:
        reasons.append(
            f"🔁 <b>Дубликат</b> — уже подавал: "
            f"<b>{esc(duplicate[0])}</b> (Discord: {esc(duplicate[1])}), "
            f"{esc(duplicate[2])}, статус: {esc(duplicate[3])}"
        )

    if data["bio_sentences"] < MIN_BIO_SENT:
        reasons.append(
            f"📝 <b>НРП био</b> — только <b>{data['bio_sentences']}</b> предложений "
            f"(нужно {MIN_BIO_SENT}+)"
        )
    elif data["bio_length"] < MIN_BIO_LEN:
        reasons.append(
            f"📝 <b>НРП био</b> — слишком короткая "
            f"(<b>{data['bio_length']}</b> символов, нужно {MIN_BIO_LEN}+)"
        )

    if data["age"] is None:
        reasons.append("🎂 <b>Возраст не указан</b> в пункте «Ваше реальное имя и возраст»")
    elif data["age"] < MIN_AGE:
        reasons.append(f"🎂 <b>Возраст {data['age']}</b> — меньше {MIN_AGE}")

    if data["online"] is None:
        reasons.append("⏰ <b>Онлайн не указан</b>")
    elif data["online"] < MIN_ONLINE:
        reasons.append(f"⏰ <b>Онлайн {data['online']} ч</b> — меньше {MIN_ONLINE}")

    if data["missing"]:
        reasons.append("📋 <b>Отсутствуют пункты:</b> " + ", ".join(data["missing"]))

    if reasons:
        return "🔴", reasons, "ОТКАЗ"
    return "🟢", ["Все критерии пройдены ✅"], "ПРИНЯТА"


# ═══════════════ КАРТОЧКА ═══════════════
def build_card(data, reasons, status, emoji):
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
        rb = "❌ <b>ПРИЧИНЫ ОТКАЗА:</b>\n" + "\n".join(f"  • {r}" for r in reasons) + "\n"
    else:
        rb = "✅ <b>ВСЁ ПРОШЛО:</b>\n" + "\n".join(f"  • {r}" for r in reasons) + "\n"
    footer = f"\n{divider()}\n🤖 <i>Решение вынесено автоматически по формальным критериям.</i>"
    return header + info_block + rb + footer


# ═══════════════ СТАРТ ═══════════════
@dp.message(CommandStart())
async def start(message: types.Message, state: FSMContext):
    await state.clear()
    text = (
        "╔════════════════════════════╗\n"
        "     🎯 <b>ПРОВЕРКА ЗАЯВОК ОПГ</b> 🎯\n"
        "╚════════════════════════════╝\n\n"
        "👋 Привет! Я автоматически проверяю заявки на пост <b>Лидера ОПГ</b>.\n\n"
        "🔍 <b>Автопроверка:</b>\n"
        f"  • 📝 Био: {MIN_BIO_SENT}+ предложений и {MIN_BIO_LEN}+ символов\n"
        f"  • 🎂 Возраст: {MIN_AGE}+ (из пункта «Ваше реальное имя и возраст»)\n"
        f"  • ⏰ Онлайн: {MIN_ONLINE}+ часов\n"
        "  • 📋 Наличие всех обязательных пунктов\n"
        "  • 🔁 Дубликаты заявок\n\n"
        "📥 <b>Как пользоваться:</b>\n"
        "Нажми «📝 Проверить заявку» и скинь текст заявки из Discord.\n\n"
        f"{divider()}\n"
        "💡 Выбери действие в меню 👇"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=main_kb())


# ═══════════════ КНОПКИ МЕНЮ ═══════════════
@dp.message(F.text == "📝 Проверить заявку")
async def ask_app(message: types.Message, state: FSMContext):
    await state.set_state(Check.waiting)
    await message.answer(
        "📥 <b>Кидай текст заявки</b> одним сообщением.\n\n"
        "💡 Скопируй всю заявку из Discord и отправь сюда.\n"
        "Я проверю её по всем критериям и вынесу вердикт.",
        parse_mode="HTML",
        reply_markup=cancel_kb()
    )


@dp.message(F.text == "📊 Статистика")
async def stat_btn(message: types.Message):
    s = get_stats()
    await message.answer(
        "╔════════════════════════════╗\n"
        "     📊 <b>СТАТИСТИКА ЗАЯВОК</b>\n"
        "╚════════════════════════════╝\n\n"
        f"  ├ Всего: <b>{s['total']}</b>\n"
        f"  ├ Принято: <b>{s['accepted']}</b>\n"
        f"  ├ Отказов: <b>{s['rejected']}</b>\n"
        f"  └ Сегодня: <b>{s['today']}</b>",
        parse_mode="HTML",
        reply_markup=back_menu_kb()
    )


@dp.message(F.text == "🏆 Топ")
async def top_btn(message: types.Message):
    rows = get_top(10)
    if not rows:
        await message.answer("🏆 Пока нет принятых заявок.", reply_markup=back_menu_kb())
        return
    text = "╔════════════════════════════╗\n     🏆 <b>ТОП-10 ПРИНЯТЫХ</b>\n╚════════════════════════════╝\n\n"
    for i, (nick, disc, dt) in enumerate(rows, 1):
        text += f"{i}. <b>{esc(nick)}</b> — {esc(dt)}\n"
    await message.answer(text, parse_mode="HTML", reply_markup=back_menu_kb())


@dp.message(F.text == "📖 Инструкция")
async def instr(message: types.Message):
    text = (
        "📖 <b>КАК ПОЛЬЗОВАТЬСЯ БОТОМ</b>\n\n"
        f"{divider()}\n"
        "1️⃣ Нажми <b>«📝 Проверить заявку»</b>\n"
        "2️⃣ Скопируй заявку из Discord\n"
        "3️⃣ Отправь боту одним сообщением\n"
        "4️⃣ Получи вердикт: <b>ПРИНЯТА</b> или <b>ОТКАЗ</b>\n\n"
        f"{divider()}\n"
        "<b>Что проверяется автоматически:</b>\n"
        "  • Наличие всех обязательных пунктов\n"
        "  • Био: 5+ предложений и 200+ символов\n"
        "  • Возраст 14+ (из пункта «Ваше реальное имя и возраст»)\n"
        "  • Онлайн 3+ часов\n"
        "  • Дубликаты заявок\n\n"
        f"{divider()}\n"
        "<b>Что проверяет админ вручную:</b>\n"
        "  • Скриншот ↔ ник\n"
        "  • Уровень 10+\n"
        "  • Наказания (BAN/WARN)\n"
        "  • Адекватность и грамотность"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=back_menu_kb())


@dp.message(F.text == "❌ Отмена")
async def cancel(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Отменено.", reply_markup=main_kb())


# ═══════════════ ПРОВЕРКА ЗАЯВКИ ═══════════════
@dp.message(Check.waiting)
async def check_app(message: types.Message, state: FSMContext):
    try:
        text = message.text or message.caption or ""
        if len(text.strip()) < 30:
            await message.answer("❌ Сообщение слишком короткое. Пришли полный текст заявки.")
            return

        data = parse_application(text)
        duplicate = find_duplicate(data["nickname"], data["discord"])
        emoji, reasons, status = make_verdict(data, duplicate)

        card = build_card(data, reasons, status, emoji)
        await message.answer(card, parse_mode="HTML", reply_markup=main_kb())

        save_application(message.from_user, data, status, reasons)

        if ADMIN_CHAT_ID:
            safe = esc(text[:3500])
            u = message.from_user
            admin_text = (
                f"{card}\n\n"
                f"{divider()}\n"
                f"👤 От: @{esc(u.username) or 'без_юз'} (<code>{u.id}</code>)\n\n"
                f"📄 <b>Оригинал заявки:</b>\n"
                f"<blockquote>{safe}</blockquote>"
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
        logging.error(f"check_app error:\n{err}")
        await message.answer(
            f"⚠️ <b>Ошибка:</b>\n<code>{esc(str(e))[:300]}</code>",
            parse_mode="HTML"
        )


# ═══════════════ ИНЛАЙН-КНОПКИ ═══════════════
@dp.callback_query(F.data == "accept")
async def cb_accept(call: CallbackQuery):
    await call.answer("✅ Принято")
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await call.message.reply(
        f"✅ <b>Заявка одобрена</b>\nАдмин: @{esc(call.from_user.username) or call.from_user.id}",
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
        f"❌ <b>Заявка отклонена</b>\nАдмин: @{esc(call.from_user.username) or call.from_user.id}",
        parse_mode="HTML"
    )


@dp.callback_query(F.data == "stats")
async def cb_stats(call: CallbackQuery):
    await call.answer()
    s = get_stats()
    await call.message.reply(
        f"📊 <b>Статистика</b>\n\n"
        f"  ├ Всего: <b>{s['total']}</b>\n"
        f"  ├ Принято: <b>{s['accepted']}</b>\n"
        f"  ├ Отказов: <b>{s['rejected']}</b>\n"
        f"  └ Сегодня: <b>{s['today']}</b>",
        parse_mode="HTML"
    )


@dp.callback_query(F.data == "top")
async def cb_top(call: CallbackQuery):
    await call.answer()
    rows = get_top(10)
    if not rows:
        await call.message.reply("🏆 Пока нет принятых заявок.")
        return
    text = "🏆 <b>Топ-10:</b>\n\n"
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


# ═══════════════ КОМАНДЫ ═══════════════
@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    s = get_stats()
    await message.answer(
        f"📊 <b>Статистика</b>\n\n"
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
    text = "🏆 <b>Топ-10:</b>\n\n"
    for i, (nick, disc, dt) in enumerate(rows, 1):
        text += f"{i}. <b>{esc(nick)}</b> — {esc(dt)}\n"
    await message.answer(text, parse_mode="HTML")


@dp.message(Command("export"))
async def cmd_export(message: types.Message):
    try:
        data = export_csv()
        f = BufferedInputFile(data, filename=f"applications_{datetime.now():%Y%m%d_%H%M}.csv")
        await message.answer_document(f, caption="📁 Экспорт заявок")
    except Exception as e:
        await message.answer(f"⚠️ Ошибка: {esc(e)}")


@dp.message(Command("menu"))
async def cmd_menu(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("🏠 Меню:", reply_markup=main_kb())


# ═══════════════ FALLBACK ═══════════════
@dp.message()
async def fallback(message: types.Message, state: FSMContext):
    await message.answer(
        "💡 Нажми /menu или выбери действие в меню.",
        reply_markup=main_kb()
    )


# ═══════════════ ЗАПУСК ═══════════════
async def main():
    logging.info("Bot started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
