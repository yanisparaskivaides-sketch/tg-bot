import asyncio
import logging
import os
import re
import csv
import io
import json
import sqlite3
import traceback
import aiohttp
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

# AI (необязательно). Любой OpenAI-совместимый API.
AI_API_KEY = os.environ.get("AI_API_KEY")           # ключ
AI_BASE_URL = os.environ.get("AI_BASE_URL", "https://api.openai.com/v1")
AI_MODEL = os.environ.get("AI_MODEL", "gpt-4o-mini")

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
                telegram_id INTEGER, username TEXT,
                nickname TEXT, discord TEXT,
                age INTEGER, online TEXT,
                bio_sentences INTEGER, bio_length INTEGER,
                ai_score INTEGER, ai_comment TEXT,
                verdict TEXT, reason TEXT, applied_at TEXT
            )
        """)
        conn.commit(); conn.close()
        logging.info("DB ready")
    except Exception as e:
        logging.error(f"DB init error: {e}")

def find_duplicate(nickname, discord):
    try:
        if not nickname and not discord: return None
        conn = sqlite3.connect(DB_PATH); cur = conn.cursor()
        cur.execute(
            "SELECT nickname, discord, applied_at, verdict FROM applications "
            "WHERE LOWER(nickname)=LOWER(?) OR LOWER(discord)=LOWER(?) "
            "ORDER BY id DESC LIMIT 1",
            (nickname or "", discord or "")
        )
        row = cur.fetchone(); conn.close(); return row
    except Exception as e:
        logging.error(f"find_duplicate error: {e}"); return None

def save_application(user, data, verdict, reason, ai_score, ai_comment):
    try:
        conn = sqlite3.connect(DB_PATH); cur = conn.cursor()
        cur.execute(
            "INSERT INTO applications "
            "(telegram_id, username, nickname, discord, age, online, "
            " bio_sentences, bio_length, ai_score, ai_comment, verdict, reason, applied_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (user.id, user.username or "",
             data["nickname"], data["discord"],
             data["age"] or 0, str(data["online"] or ""),
             data["bio_sentences"], data["bio_length"],
             ai_score or 0, ai_comment or "",
             verdict, "; ".join(reason)[:500], now_str())
        )
        conn.commit(); conn.close()
    except Exception as e:
        logging.error(f"save error: {e}")

def get_stats():
    try:
        conn = sqlite3.connect(DB_PATH); cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM applications"); total = cur.fetchone()[0] or 0
        cur.execute("SELECT COUNT(*) FROM applications WHERE verdict='ОТКАЗ'"); rej = cur.fetchone()[0] or 0
        cur.execute("SELECT COUNT(*) FROM applications WHERE verdict='ПРИНЯТА'"); acc = cur.fetchone()[0] or 0
        today = datetime.now().strftime("%Y-%m-%d")
        cur.execute("SELECT COUNT(*) FROM applications WHERE applied_at LIKE ?", (f"{today}%",))
        t = cur.fetchone()[0] or 0
        conn.close()
        return {"total": total, "rejected": rej, "accepted": acc, "today": t}
    except Exception as e:
        logging.error(f"stats error: {e}"); return {"total":0,"rejected":0,"accepted":0,"today":0}

def get_top(limit=10):
    try:
        conn = sqlite3.connect(DB_PATH); cur = conn.cursor()
        cur.execute("SELECT nickname, discord, applied_at FROM applications "
                    "WHERE verdict='ПРИНЯТА' ORDER BY id DESC LIMIT ?", (limit,))
        rows = cur.fetchall(); conn.close(); return rows
    except Exception: return []

def export_csv() -> bytes:
    conn = sqlite3.connect(DB_PATH); cur = conn.cursor()
    cur.execute("""SELECT id, telegram_id, username, nickname, discord,
                          age, online, bio_sentences, bio_length,
                          ai_score, ai_comment, verdict, applied_at
                   FROM applications ORDER BY id DESC""")
    rows = cur.fetchall(); conn.close()
    buf = io.StringIO(); w = csv.writer(buf, delimiter=";")
    w.writerow(["ID","TG_ID","Username","Nickname","Discord","Age","Online",
                "Bio_sentences","Bio_length","AI_score","AI_comment","Verdict","Applied_at"])
    for r in rows: w.writerow(r)
    return buf.getvalue().encode("utf-8")

init_db()


# ═══════════════ СОСТОЯНИЯ ═══════════════
class Check(StatesGroup):
    waiting = State()


# ═══════════════ КЛАВИАТУРЫ ═══════════════
def main_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Проверить заявку", callback_data="check")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="stats"),
         InlineKeyboardButton(text="🏆 Топ", callback_data="top")],
        [InlineKeyboardButton(text="📖 Инструкция", callback_data="help")],
    ])

def cancel_kb():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="❌ Отмена")]], resize_keyboard=True)

def admin_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Принять", callback_data="accept"),
         InlineKeyboardButton(text="❌ Отклонить", callback_data="reject")],
        [InlineKeyboardButton(text="📊 Стата", callback_data="stats"),
         InlineKeyboardButton(text="🏆 Топ", callback_data="top")],
    ])

def back_kb():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🏠 В меню", callback_data="menu")
    ]])


# ═══════════════ AI-ПРОВЕРКА ═══════════════
AI_PROMPT = """Ты — строгий, но справедливый проверяющий заявок на пост Лидера ОПГ в GTA-ролевом проекте.

Тебе дают текст заявки. Оцени её по критериям:
1. ГРАМОТНОСТЬ — есть ли ошибки, опечатки, капс, сленг.
2. АДЕКВАТНОСТЬ — адекватный ли тон, нет ли агрессии, оскорблений, бреда.
3. ОСМЫСЛЕННОСТЬ БИО — связано ли био с персонажем, есть ли логика, не набор ли это случайных слов.
4. РП-СООТВЕТСТВИЕ — подходит ли персонаж под роль лидера ОПГ.

Верни СТРОГО JSON без пояснений, в таком формате:
{
  "score": 0-100,
  "grammar_ok": true/false,
  "adequate": true/false,
  "bio_meaningful": true/false,
  "rp_match": true/false,
  "comment": "короткий комментарий на русском до 200 символов"
}

Если что-то явно плохо — score низкий. Если всё ок — 80+.

Текст заявки:
---
{application}
---"""

async def ai_check(application_text: str):
    """Возвращает (score, comment, details) или (None, None, None) если AI недоступен."""
    if not AI_API_KEY:
        return None, None, None
    try:
        payload = {
            "model": AI_MODEL,
            "messages": [
                {"role": "system", "content": "Ты возвращаешь только валидный JSON."},
                {"role": "user", "content": AI_PROMPT.format(application=application_text[:4000])}
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {AI_API_KEY}",
            "Content-Type": "application/json",
        }
        async with aiohttp.ClientSession() as s:
            async with s.post(f"{AI_BASE_URL}/chat/completions",
                              headers=headers, json=payload, timeout=30) as r:
                if r.status != 200:
                    logging.error(f"AI HTTP {r.status}: {await r.text()}")
                    return None, None, None
                data = await r.json()
        content = data["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        score = int(parsed.get("score", 0))
        comment = parsed.get("comment", "")
        return score, comment, parsed
    except Exception as e:
        logging.error(f"AI error: {e}")
        return None, None, None


# ═══════════════ ПАРСЕР ═══════════════
def parse_application(text: str) -> dict:
    lower = text.lower()
    lines = text.splitlines()
    data = {}

    m = re.search(r"(?:ник\s*name|nickname|ник)[:\s]*([^\n]+)", text, re.IGNORECASE)
    data["nickname"] = m.group(1).strip() if m else ""

    m = re.search(r"(?:discord|дискорд)[:\s]*([^\n]+)", text, re.IGNORECASE)
    data["discord"] = m.group(1).strip() if m else ""

    # Возраст — ТОЛЬКО из строки с "реальное имя и возраст" или "возраст:"
    age = None
    for line in lines:
        low = line.lower()
        if ("реальн" in low and "возраст" in low) or re.search(r"имя\s*и\s*возраст", low):
            nums = re.findall(r"\b(\d{1,2})\b", line)
            for n in nums:
                if 10 <= int(n) <= 99:
                    age = int(n); break
            if age: break
    if age is None:
        for line in lines:
            low = line.lower()
            if re.match(r"\s*(?:ваш[а-я]*\s*)?(?:реальн[а-я]*\s*)?возраст\s*[:\-]", low):
                nums = re.findall(r"\b(\d{1,2})\b", line)
                for n in nums:
                    if 10 <= int(n) <= 99:
                        age = int(n); break
                if age: break
    data["age"] = age

    # Онлайн
    online = None
    for line in lines:
        if re.search(r"онлайн", line, re.IGNORECASE):
            nums = re.findall(r"\b(\d{1,2})\b", line)
            if nums:
                online = max(int(n) for n in nums); break
    data["online"] = online

    # Био
    bio = ""
    m = re.search(
        r"биограф[^\n]*[:\n]+(.*?)(?=\n\s*\n|\n[А-ЯЁA-Z][^\n]{0,60}:|\Z)",
        text, re.DOTALL | re.IGNORECASE
    )
    if m: bio = m.group(1).strip()
    data["bio"] = bio
    data["bio_sentences"] = count_sentences(bio)
    data["bio_length"] = len(bio)

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
def make_verdict(data, duplicate, ai_score, ai_details):
    reasons = []
    hard_fail = False  # жёсткий провал — бот сам говорит НЕ ПОДХОДИТ

    if duplicate and len(duplicate) >= 4:
        reasons.append(
            f"🔁 <b>Дубликат</b> — уже подавал: <b>{esc(duplicate[0])}</b> "
            f"(Discord: {esc(duplicate[1])}), {esc(duplicate[2])}, статус: {esc(duplicate[3])}"
        )
        hard_fail = True

    if data["bio_sentences"] < MIN_BIO_SENT:
        reasons.append(f"📝 <b>НРП био</b> — только <b>{data['bio_sentences']}</b> предложений (нужно {MIN_BIO_SENT}+)")
        hard_fail = True
    elif data["bio_length"] < MIN_BIO_LEN:
        reasons.append(f"📝 <b>НРП био</b> — слишком короткая (<b>{data['bio_length']}</b> символов, нужно {MIN_BIO_LEN}+)")
        hard_fail = True

    if data["age"] is None:
        reasons.append("🎂 <b>Возраст не указан</b> в пункте «Ваше реальное имя и возраст»")
        hard_fail = True
    elif data["age"] < MIN_AGE:
        reasons.append(f"🎂 <b>Возраст {data['age']}</b> — меньше {MIN_AGE}")
        hard_fail = True

    if data["online"] is None:
        reasons.append("⏰ <b>Онлайн не указан</b>")
        hard_fail = True
    elif data["online"] < MIN_ONLINE:
        reasons.append(f"⏰ <b>Онлайн {data['online']} ч</b> — меньше {MIN_ONLINE}")
        hard_fail = True

    if data["missing"]:
        reasons.append("📋 <b>Отсутствуют пункты:</b> " + ", ".join(data["missing"]))
        hard_fail = True

    # AI-вердикт
    if ai_score is not None:
        if ai_score < 50:
            reasons.append(f"🧠 <b>AI-оценка: {ai_score}/100</b> — низкая (грамотность/адекватность/смысл био)")
            hard_fail = True
        elif ai_score < 70:
            reasons.append(f"🧠 <b>AI-оценка: {ai_score}/100</b> — средняя, требует внимания")

    if hard_fail:
        return "🔴", reasons, "НЕ ПОДХОДИТ"
    return "🟢", reasons or ["Все критерии пройдены ✅"], "ПОДХОДИТ — МОЖНО ПРИНИМАТЬ"


# ═══════════════ КАРТОЧКА ═══════════════
def build_card(data, reasons, status, emoji, ai_score, ai_comment):
    if status.startswith("ПОДХОДИТ"):
        header = (
            "╔════════════════════════════╗\n"
            "  🟢 <b>ЗАЯВКА ПОДХОДИТ</b> 🟢\n"
            "  <i>можно принимать</i>\n"
            "╚════════════════════════════╝\n"
        )
    elif status.startswith("НЕ ПОДХОДИТ"):
        header = (
            "╔════════════════════════════╗\n"
            "  🔴 <b>ЗАЯВКА НЕ ПОДХОДИТ</b> 🔴\n"
            "  <i>есть нарушения критериев</i>\n"
            "╚════════════════════════════╝\n"
        )
    else:
        header = (
            "╔════════════════════════════╗\n"
            f"  {emoji} <b>{status}</b>\n"
            "╚════════════════════════════╝\n"
        )

    info = (
        "\n📊 <b>РАСПОЗНАНО:</b>\n"
        f"  ├ Ник: <b>{esc(data['nickname']) or '—'}</b>\n"
        f"  ├ Discord: <b>{esc(data['discord']) or '—'}</b>\n"
        f"  ├ Возраст: <b>{data['age'] if data['age'] else '—'}</b>\n"
        f"  ├ Онлайн: <b>{data['online'] if data['online'] else '—'}</b> ч\n"
        f"  └ Био: <b>{data['bio_sentences']}</b> предл. / <b>{data['bio_length']}</b> симв.\n\n"
    )

    if status.startswith("НЕ ПОДХОДИТ"):
        rb = "❌ <b>ПРИЧИНЫ:</b>\n" + "\n".join(f"  • {r}" for r in reasons) + "\n"
    else:
        rb = "✅ <b>ЧТО ПРОВЕРЕНО:</b>\n" + "\n".join(f"  • {r}" for r in reasons) + "\n"

    ai_block = ""
    if ai_score is not None:
        ai_block = (
            f"\n🧠 <b>AI-ОЦЕНКА:</b> <b>{ai_score}/100</b>\n"
            f"<i>{esc(ai_comment)[:300] if ai_comment else ''}</i>\n"
        )

    footer = f"\n{divider()}\n🤖 <i>Решение вынесено автоматически.</i>"
    return header + info + rb + ai_block + footer


# ═══════════════ СТАРТ ═══════════════
@dp.message(CommandStart())
async def start(message: types.Message, state: FSMContext):
    await state.clear()
    ai_status = "🧠 AI-проверка: <b>включена</b>" if AI_API_KEY else "🧠 AI-проверка: <b>выключена</b>"
    text = (
        "╔════════════════════════════╗\n"
        "     🎯 <b>ПРОВЕРКА ЗАЯВОК ОПГ</b> 🎯\n"
        "╚════════════════════════════╝\n\n"
        "👋 Привет! Я автоматически проверяю заявки на пост <b>Лидера ОПГ</b>.\n\n"
        "🔍 <b>Проверяю:</b>\n"
        f"  • 📝 Био: {MIN_BIO_SENT}+ предложений и {MIN_BIO_LEN}+ символов\n"
        f"  • 🎂 Возраст {MIN_AGE}+ (из «Ваше реальное имя и возраст»)\n"
        f"  • ⏰ Онлайн {MIN_ONLINE}+ часов\n"
        "  • 📋 Все обязательные пункты\n"
        "  • 🔁 Дубликаты заявок\n"
        "  • 🧠 Грамотность, адекватность, смысл био (AI)\n\n"
        f"{ai_status}\n\n"
        f"{divider()}\n"
        "💡 Выбери действие 👇"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=main_kb())


# ═══════════════ КНОПКИ ═══════════════
@dp.callback_query(F.data == "menu")
async def cb_menu(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.answer()
    try: await call.message.edit_reply_markup(reply_markup=None)
    except: pass
    await call.message.answer("🏠 Главное меню:", reply_markup=main_kb())


@dp.callback_query(F.data == "check")
async def cb_check(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(Check.waiting)
    await call.message.answer(
        "📥 <b>Кидай текст заявки</b> одним сообщением.\n\n"
        "💡 Скопируй всю заявку из Discord и отправь сюда.\n"
        "Я проверю её и вынесу вердикт.",
        parse_mode="HTML",
        reply_markup=cancel_kb()
    )


@dp.callback_query(F.data == "stats")
async def cb_stats(call: CallbackQuery):
    await call.answer()
    s = get_stats()
    await call.message.answer(
        "╔════════════════════════════╗\n"
        "     📊 <b>СТАТИСТИКА ЗАЯВОК</b>\n"
        "╚════════════════════════════╝\n\n"
        f"  ├ Всего: <b>{s['total']}</b>\n"
        f"  ├ Принято: <b>{s['accepted']}</b>\n"
        f"  ├ Отказов: <b>{s['rejected']}</b>\n"
        f"  └ Сегодня: <b>{s['today']}</b>",
        parse_mode="HTML", reply_markup=back_kb()
    )


@dp.callback_query(F.data == "top")
async def cb_top(call: CallbackQuery):
    await call.answer()
    rows = get_top(10)
    if not rows:
        await call.message.answer("🏆 Пока нет принятых заявок.", reply_markup=back_kb()); return
    text = "╔════════════════════════════╗\n     🏆 <b>ТОП-10 ПРИНЯТЫХ</b>\n╚════════════════════════════╝\n\n"
    for i, (nick, disc, dt) in enumerate(rows, 1):
        text += f"{i}. <b>{esc(nick)}</b> — {esc(dt)}\n"
    await call.message.answer(text, parse_mode="HTML", reply_markup=back_kb())


@dp.callback_query(F.data == "help")
async def cb_help(call: CallbackQuery):
    await call.answer()
    text = (
        "📖 <b>ИНСТРУКЦИЯ</b>\n\n"
        f"{divider()}\n"
        "1️⃣ Нажми <b>«📝 Проверить заявку»</b>\n"
        "2️⃣ Скопируй заявку из Discord\n"
        "3️⃣ Отправь боту одним сообщением\n"
        "4️⃣ Бот сам скажет: <b>ПОДХОДИТ</b> или <b>НЕ ПОДХОДИТ</b>\n\n"
        f"{divider()}\n"
        "<b>Автопроверка:</b>\n"
        "  • Био: 5+ предложений и 200+ символов\n"
        "  • Возраст 14+ (из «Ваше реальное имя и возраст»)\n"
        "  • Онлайн 3+ часов\n"
        "  • Все обязательные пункты\n"
        "  • Дубликаты\n"
        "  • AI: грамотность, адекватность, смысл био\n\n"
        f"{divider()}\n"
        "<b>Вручную админ:</b> скриншот ↔ ник, уровень 10+, наказания."
    )
    await call.message.answer(text, parse_mode="HTML", reply_markup=back_kb())


# ═══════════════ ПРОВЕРКА ═══════════════
@dp.message(Check.waiting)
async def check_app(message: types.Message, state: FSMContext):
    try:
        text = message.text or message.caption or ""
        if len(text.strip()) < 30:
            await message.answer("❌ Слишком коротко. Пришли полный текст заявки.")
            return

        wait_msg = await message.answer("⏳ <b>Проверяю заявку...</b>", parse_mode="HTML")

        data = parse_application(text)
        duplicate = find_duplicate(data["nickname"], data["discord"])

        ai_score, ai_comment, ai_details = await ai_check(text) if AI_API_KEY else (None, None, None)

        emoji, reasons, status = make_verdict(data, duplicate, ai_score, ai_details)
        card = build_card(data, reasons, status, emoji, ai_score, ai_comment)

        try: await wait_msg.delete()
        except: pass
        await message.answer(card, parse_mode="HTML", reply_markup=main_kb())

        save_application(message.from_user, data, status, reasons, ai_score, ai_comment)

        # В админ-группу
        if ADMIN_CHAT_ID:
            safe = esc(text[:3500])
            u = message.from_user
            admin_text = (
                f"{card}\n\n"
                f"{divider()}\n"
                f"👤 От: @{esc(u.username) or 'без_юз'} (<code>{u.id}</code>)\n\n"
                f"📄 <b>Оригинал:</b>\n<blockquote>{safe}</blockquote>"
            )
            try:
                await bot.send_message(
                    chat_id=ADMIN_CHAT_ID, text=admin_text,
                    parse_mode="HTML", reply_markup=admin_kb()
                )
            except Exception as e:
                logging.error(f"admin send error: {e}")

        await state.clear()

    except Exception as e:
        err = traceback.format_exc()
        logging.error(f"check_app error:\n{err}")
        await message.answer(f"⚠️ <b>Ошибка:</b>\n<code>{esc(str(e))[:300]}</code>", parse_mode="HTML")


# ═══════════════ АДМИН-КНОПКИ ═══════════════
@dp.callback_query(F.data == "accept")
async def cb_accept(call: CallbackQuery):
    await call.answer("✅ Принято")
    try: await call.message.edit_reply_markup(reply_markup=None)
    except: pass
    await call.message.reply(
        f"✅ <b>Заявка одобрена</b>\nАдмин: @{esc(call.from_user.username) or call.from_user.id}",
        parse_mode="HTML"
    )

@dp.callback_query(F.data == "reject")
async def cb_reject(call: CallbackQuery):
    await call.answer("❌ Отклонено")
    try: await call.message.edit_reply_markup(reply_markup=None)
    except: pass
    await call.message.reply(
        f"❌ <b>Заявка отклонена</b>\nАдмин: @{esc(call.from_user.username) or call.from_user.id}",
        parse_mode="HTML"
    )


# ═══════════════ КОМАНДЫ ═══════════════
@dp.message(Command("stats"))
async def cmd_stats(m: types.Message):
    s = get_stats()
    await m.answer(
        f"📊 <b>Статистика</b>\n\n  ├ Всего: <b>{s['total']}</b>\n"
        f"  ├ Принято: <b>{s['accepted']}</b>\n  ├ Отказов: <b>{s['rejected']}</b>\n"
        f"  └ Сегодня: <b>{s['today']}</b>", parse_mode="HTML"
    )

@dp.message(Command("top"))
async def cmd_top(m: types.Message):
    rows = get_top(10)
    if not rows: await m.answer("🏆 Пусто."); return
    text = "🏆 <b>Топ-10:</b>\n\n"
    for i, (nick, disc, dt) in enumerate(rows, 1):
        text += f"{i}. <b>{esc(nick)}</b> — {esc(dt)}\n"
    await m.answer(text, parse_mode="HTML")

@dp.message(Command("export"))
async def cmd_export(m: types.Message):
    try:
        data = export_csv()
        f = BufferedInputFile(data, filename=f"applications_{datetime.now():%Y%m%d_%H%M}.csv")
        await m.answer_document(f, caption="📁 Экспорт")
    except Exception as e:
        await m.answer(f"⚠️ {esc(e)}")

@dp.message(Command("menu"))
async def cmd_menu(m: types.Message, state: FSMContext):
    await state.clear()
    await m.answer("🏠 Меню:", reply_markup=main_kb())

@dp.message(F.text == "❌ Отмена")
async def cancel(m: types.Message, state: FSMContext):
    await state.clear()
    await m.answer("❌ Отменено.", reply_markup=main_kb())


# ═══════════════ FALLBACK ═══════════════
@dp.message()
async def fallback(m: types.Message, state: FSMContext):
    await m.answer("💡 /menu — открыть меню.", reply_markup=main_kb())


# ═══════════════ ЗАПУСК ═══════════════
async def main():
    logging.info("Bot started")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
