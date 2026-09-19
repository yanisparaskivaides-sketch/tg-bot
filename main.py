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
from collections import Counter
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
BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID")
AI_API_KEY = os.environ.get("AI_API_KEY")
AI_BASE_URL = os.environ.get("AI_BASE_URL", "https://api.groq.com/openai/v1")
AI_MODEL = os.environ.get("AI_MODEL", "llama-3.3-70b-versatile")

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
MIN_GRAMMAR = 50
MIN_AI = 50


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
    if not text:
        return 0
    t = text.replace("...", "…").replace("..", ".")
    parts = re.split(r"[.!?…\n]+", t)
    return len([p for p in parts if len(p.strip()) >= 3])


# ═══════════════ СЛОВАРЬ ОШИБОК ═══════════════
COMMON_TYPOS = {
    "жызнь": "жизнь", "жывот": "живот", "жызни": "жизни",
    "шына": "шина", "машына": "машина", "жыть": "жить",
    "чясть": "часть", "щястье": "счастье", "чяй": "чай",
    "чясто": "часто",
    "ихний": "их", "евонный": "его", "ейный": "её",
    "ложить": "класть", "ложу": "кладу",
    "одел": "надел", "одела": "надела",
    "извени": "извини", "извените": "извините",
    "здарова": "здорово", "здраствуй": "здравствуй",
    "приветсвую": "приветствую",
    "сдесь": "здесь", "зделать": "сделать", "зделал": "сделал",
    "здать": "сдать", "здал": "сдал",
    "вообщем": "в общем", "вобщем": "в общем",
    "корче": "короче",
    "щас": "сейчас", "ща": "сейчас",
    "че": "что", "шо": "что", "чё": "что",
    "норм": "нормально",
    "плиз": "пожалуйста", "пж": "пожалуйста",
    "спс": "спасибо",
    "тян": "тянется", "збс": "отлично",
    "кст": "кстати",
}

CAPS_WORDS = ["ору", "ахахах", "ахаха", "хахаха", "лол", "кек", "ржу", "кринж"]


# ═══════════════ ПОИСК НЕГРАМОТНЫХ СЛОВ ═══════════════
def find_typos(text: str) -> list:
    issues = []
    lower = text.lower()
    words = re.findall(r"[а-яёa-z]+", lower)
    seen = set()

    for w in words:
        if w in COMMON_TYPOS and w not in seen:
            issues.append({"word": w, "reason": "типичная ошибка",
                           "suggest": COMMON_TYPOS[w]})
            seen.add(w)

    for w in words:
        if w in CAPS_WORDS and w not in seen:
            issues.append({"word": w, "reason": "сленг",
                           "suggest": "убери сленг в заявке"})
            seen.add(w)

    for m in re.finditer(r"\b([а-яёa-z])\1{2,}\w*", lower):
        w = m.group(0)
        if w not in seen:
            issues.append({"word": w, "reason": "растягивание букв",
                           "suggest": w[0] + w[0]})
            seen.add(w)

    for i in range(len(words) - 1):
        if words[i] == words[i + 1] and len(words[i]) > 2 and words[i] not in seen:
            issues.append({"word": f"{words[i]} {words[i]}",
                           "reason": "повтор слова", "suggest": words[i]})
            seen.add(words[i])

    for m in re.finditer(r"\b\w*(?:жы|шы|чя|щя|щю|чю)\w*\b", lower):
        w = m.group(0)
        if w not in seen and w not in COMMON_TYPOS:
            fixed = (w.replace("жы", "жи").replace("шы", "ши")
                     .replace("чя", "ча").replace("щя", "ща")
                     .replace("щю", "щу").replace("чю", "чу"))
            issues.append({"word": w, "reason": "ошибка жи/ши, ча/ща",
                           "suggest": fixed})
            seen.add(w)

    return issues[:15]


# ═══════════════ ГРАМОТНОСТЬ (свой алгоритм) ═══════════════
def check_grammar(text: str) -> dict:
    if not text or len(text.strip()) < 20:
        return {"score": 0, "issues": ["Текст слишком короткий"], "typos": []}

    issues = []
    typos = find_typos(text)
    score = 100
    score -= min(len(typos) * 4, 40)

    letters = [c for c in text if c.isalpha()]
    if letters:
        upper_ratio = sum(1 for c in letters if c.isupper()) / len(letters)
        if upper_ratio > 0.6:
            issues.append("Много заглавных букв (капс)")
            score -= 20

    sentences_raw = re.split(r"[.!?…\n]+", text)
    long_s = [s.strip() for s in sentences_raw if len(s.strip()) > 150]
    if long_s:
        issues.append(f"Слишком длинные предложения ({len(long_s)} шт.) без точек")
        score -= 5 * min(len(long_s), 3)

    if not re.search(r"[.!?…]", text):
        issues.append("Нет знаков завершения предложений (. ! ?)")
        score -= 20

    punct = len(re.findall(r"[,.!?;:…]", text))
    if len(text) > 200 and punct < 3:
        issues.append("Мало знаков препинания")
        score -= 15

    for s in sentences_raw:
        s = s.strip()
        if len(s) > 10 and s[0].islower():
            issues.append("Есть предложения с маленькой буквы")
            score -= 5
            break

    score = max(0, min(100, score))
    if not issues:
        issues = ["Основные правила соблюдены ✅"]
    return {"score": score, "issues": issues, "typos": typos}


# ═══════════════ AI-ПРОМТ ═══════════════
AI_SYSTEM_PROMPT = """Ты — опытный куратор GTA-ролевого проекта «GRAND» (Курганская ОПГ).
Твоя задача — оценивать заявки на пост Лидера ОПГ строго, но справедливо, как это делал бы живой администратор.

ТРЕБОВАНИЯ К ЗАЯВКЕ:
1. Возраст игрока 14+.
2. Онлайн от 3 часов в сутки.
3. Наличие Discord и микрофона.
4. Биография должна быть развёрнутой (от 5 предложений), осмысленной и связанной с РП-миром проекта.
5. Грамотная письменная речь, без капса, сленга и ошибок.
6. Адекватность, сдержанность.
7. Знание правил проекта и своей сферы.

ТВОЯ ЗАДАЧА:
Оценить ТОЛЬКО текст заявки по 6 критериям (0-100 каждый):
- grammar — грамотность (орфография, пунктуация, капс, сленг)
- adequacy — адекватность тона (нет агрессии, оскорблений, бессмыслицы)
- bio_meaning — осмысленность биографии (связность, логика, РП)
- rp_match — соответствие РП-миру GTA (реалистично ли для лидера ОПГ)
- completeness — заполненность заявки (все ли пункты)
- overall — общая оценка

Также:
- Перечисли КОНКРЕТНЫЕ ошибки в тексте (5-15 штук): опечатки, неграмотные слова, повторы, капс, сленг. Указывай слово, как правильно, и причину.
- Дай короткий вердикт (1-2 предложения).

ВЕРНИ СТРОГО ВАЛИДНЫЙ JSON без комментариев и markdown:
{
  "grammar": 0-100,
  "adequacy": 0-100,
  "bio_meaning": 0-100,
  "rp_match": 0-100,
  "completeness": 0-100,
  "overall": 0-100,
  "errors": [
    {"word": "неграмотное слово", "fix": "как правильно", "reason": "почему"}
  ],
  "verdict": "короткий вердикт на русском"
}

ВАЖНО:
- Оценивай строго. Если био — набор слов или отписка, ставь bio_meaning ниже 40.
- Если в тексте капс, сленг, куча ошибок — grammar ниже 50.
- Если заявка неполная — completeness ниже 50.
- Общая оценка overall — это средневзвешенное, но если есть грубые косяки (нет возраста, нет онлайна, био <5 предложений) — overall ниже 40.
- Текст заявки может быть на русском. Отвечай тоже на русском, но JSON-ключи оставь английскими."""


async def ai_check(application_text: str):
    if not AI_API_KEY:
        return None
    try:
        payload = {
            "model": AI_MODEL,
            "messages": [
                {"role": "system", "content": AI_SYSTEM_PROMPT},
                {"role": "user", "content": f"Заявка:\n---\n{application_text[:5000]}\n---"},
            ],
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": f"Bearer {AI_API_KEY}", "Content-Type": "application/json"}
        async with aiohttp.ClientSession() as s:
            async with s.post(f"{AI_BASE_URL}/chat/completions", headers=headers,
                              json=payload, timeout=45) as r:
                if r.status != 200:
                    logging.error(f"AI {r.status}: {await r.text()}")
                    return None
                data = await r.json()
        return json.loads(data["choices"][0]["message"]["content"])
    except Exception as e:
        logging.error(f"AI error: {e}")
        return None


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
                grammar_score INTEGER, grammar_issues TEXT, grammar_typos TEXT,
                ai_score INTEGER, ai_json TEXT,
                verdict TEXT, reason TEXT, applied_at TEXT
            )""")
        conn.commit()
        conn.close()
        logging.info("DB ready")
    except Exception as e:
        logging.error(f"DB: {e}")


def find_duplicate(nickname, discord):
    try:
        if not nickname and not discord:
            return None
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT nickname, discord, applied_at, verdict FROM applications "
                    "WHERE LOWER(nickname)=LOWER(?) OR LOWER(discord)=LOWER(?) "
                    "ORDER BY id DESC LIMIT 1", (nickname or "", discord or ""))
        row = cur.fetchone()
        conn.close()
        return row
    except:
        return None


def save_application(user, data, verdict, reason, grammar, ai_json):
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        typos_str = "; ".join(f"{t['word']}→{t['suggest']}" for t in grammar.get("typos", []))[:500]
        ai_score = ai_json.get("overall", 0) if ai_json else 0
        cur.execute(
            "INSERT INTO applications (telegram_id, username, nickname, discord, age, online, "
            "bio_sentences, bio_length, grammar_score, grammar_issues, grammar_typos, "
            "ai_score, ai_json, verdict, reason, applied_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (user.id, user.username or "", data["nickname"], data["discord"],
             data["age"] or 0, str(data["online"] or ""),
             data["bio_sentences"], data["bio_length"],
             grammar["score"], "; ".join(grammar["issues"])[:300], typos_str,
             ai_score, json.dumps(ai_json, ensure_ascii=False)[:1000] if ai_json else "",
             verdict, "; ".join(reason)[:500], now_str()))
        conn.commit()
        conn.close()
    except Exception as e:
        logging.error(f"save: {e}")


def get_stats():
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM applications")
        t = cur.fetchone()[0] or 0
        cur.execute("SELECT COUNT(*) FROM applications WHERE verdict='ОТКАЗ'")
        r = cur.fetchone()[0] or 0
        cur.execute("SELECT COUNT(*) FROM applications WHERE verdict LIKE 'ПОДХОДИТ%'")
        a = cur.fetchone()[0] or 0
        today = datetime.now().strftime("%Y-%m-%d")
        cur.execute("SELECT COUNT(*) FROM applications WHERE applied_at LIKE ?", (f"{today}%",))
        td = cur.fetchone()[0] or 0
        conn.close()
        return {"total": t, "rejected": r, "accepted": a, "today": td}
    except:
        return {"total": 0, "rejected": 0, "accepted": 0, "today": 0}


def get_top(limit=10):
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT nickname, discord, applied_at FROM applications "
                    "WHERE verdict LIKE 'ПОДХОДИТ%' ORDER BY id DESC LIMIT ?", (limit,))
        rows = cur.fetchall()
        conn.close()
        return rows
    except:
        return []


def export_csv():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""SELECT id, telegram_id, username, nickname, discord, age, online,
                          bio_sentences, bio_length, grammar_score, ai_score, verdict, applied_at
                   FROM applications ORDER BY id DESC""")
    rows = cur.fetchall()
    conn.close()
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["ID", "TG", "Username", "Nick", "Discord", "Age", "Online", "Bio_sent",
                "Bio_len", "Grammar", "AI", "Verdict", "Applied"])
    for r in rows:
        w.writerow(r)
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


def back_kb():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🏠 В меню", callback_data="menu")]])


# ═══════════════ ПАРСЕР ═══════════════
def parse_application(text: str) -> dict:
    lower = text.lower()
    lines = text.splitlines()
    data = {}

    m = re.search(r"(?:ник\s*name|nickname|ник)[:\s]*([^\n]+)", text, re.IGNORECASE)
    data["nickname"] = m.group(1).strip() if m else ""

    m = re.search(r"(?:discord|дискорд)[:\s]*([^\n]+)", text, re.IGNORECASE)
    data["discord"] = m.group(1).strip() if m else ""

    age = None
    for line in lines:
        low = line.lower()
        if ("реальн" in low and "возраст" in low) or re.search(r"имя\s*и\s*возраст", low):
            nums = re.findall(r"\b(\d{1,2})\b", line)
            for n in nums:
                if 10 <= int(n) <= 99:
                    age = int(n)
                    break
            if age:
                break
    if age is None:
        for line in lines:
            if re.match(r"\s*(?:ваш[а-я]*\s*)?(?:реальн[а-я]*\s*)?возраст\s*[:\-]", line.lower()):
                nums = re.findall(r"\b(\d{1,2})\b", line)
                for n in nums:
                    if 10 <= int(n) <= 99:
                        age = int(n)
                        break
                if age:
                    break
    data["age"] = age

    online = None
    for line in lines:
        if re.search(r"онлайн", line, re.IGNORECASE):
            nums = re.findall(r"\b(\d{1,2})\b", line)
            if nums:
                online = max(int(n) for n in nums)
                break
    data["online"] = online

    bio = ""
    m = re.search(r"биограф[^\n]*[:\n]+(.*?)(?=\n\s*\n|\n[А-ЯЁA-Z][^\n]{0,60}:|\Z)",
                  text, re.DOTALL | re.IGNORECASE)
    if m:
        bio = m.group(1).strip()
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
def make_verdict(data, duplicate, grammar, ai_json):
    reasons = []
    hard_fail = False

    if duplicate and len(duplicate) >= 4:
        reasons.append(f"🔁 <b>Дубликат</b> — уже подавал: <b>{esc(duplicate[0])}</b> "
                       f"(Discord: {esc(duplicate[1])}), {esc(duplicate[2])}, статус: {esc(duplicate[3])}")
        hard_fail = True

    if data["bio_sentences"] < MIN_BIO_SENT:
        reasons.append(f"📝 <b>Мало предложений в био</b> — <b>{data['bio_sentences']}</b>, нужно {MIN_BIO_SENT}+")
        hard_fail = True
    elif data["bio_length"] < MIN_BIO_LEN:
        reasons.append(f"📝 <b>Био короткое</b> — <b>{data['bio_length']}</b> символов, нужно {MIN_BIO_LEN}+")
        hard_fail = True

    if data["age"] is None:
        reasons.append("🎂 <b>Возраст не указан</b> в «Ваше реальное имя и возраст»")
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

    if grammar["score"] < MIN_GRAMMAR:
        reasons.append(f"📚 <b>Грамотность: {grammar['score']}/100</b> — низкая")
        hard_fail = True

    if ai_json:
        overall = ai_json.get("overall", 0)
        if overall < MIN_AI:
            reasons.append(f"🧠 <b>AI: {overall}/100</b> — низкая оценка")
            hard_fail = True

    if hard_fail:
        return "🔴", reasons, "НЕ ПОДХОДИТ"
    return "🟢", reasons or ["Все критерии пройдены ✅"], "ПОДХОДИТ — МОЖНО ПРИНИМАТЬ"


# ═══════════════ КАРТОЧКА ═══════════════
def build_card(data, reasons, status, grammar, ai_json):
    if status.startswith("ПОДХОДИТ"):
        header = ("╔════════════════════════════╗\n"
                  "  🟢 <b>ЗАЯВКА ПОДХОДИТ</b> 🟢\n"
                  "  <i>можно принимать</i>\n"
                  "╚════════════════════════════╝\n")
    else:
        header = ("╔════════════════════════════╗\n"
                  "  🔴 <b>ЗАЯВКА НЕ ПОДХОДИТ</b> 🔴\n"
                  "  <i>есть нарушения критериев</i>\n"
                  "╚════════════════════════════╝\n")

    info = ("\n📊 <b>РАСПОЗНАНО:</b>\n"
            f"  ├ Ник: <b>{esc(data['nickname']) or '—'}</b>\n"
            f"  ├ Discord: <b>{esc(data['discord']) or '—'}</b>\n"
            f"  ├ Возраст: <b>{data['age'] if data['age'] else '—'}</b>\n"
            f"  ├ Онлайн: <b>{data['online'] if data['online'] else '—'}</b> ч\n"
            f"  ├ Био: <b>{data['bio_sentences']}</b> предл. / <b>{data['bio_length']}</b> симв.\n"
            f"  └ Грамматика (свой): <b>{grammar['score']}/100</b>\n\n")

    if status.startswith("НЕ ПОДХОДИТ"):
        rb = "❌ <b>ПРИЧИНЫ:</b>\n" + "\n".join(f"  • {r}" for r in reasons) + "\n"
    else:
        rb = "✅ <b>ПРОШЛО:</b>\n" + "\n".join(f"  • {r}" for r in reasons) + "\n"

    typo_block = ""
    typos = grammar.get("typos", [])
    if typos:
        lines = []
        for t in typos[:10]:
            w = esc(t["word"])
            sug = esc(t["suggest"])
            reason = esc(t["reason"])
            lines.append(f"  ⚠️ <code>{w}</code> → <b>{sug}</b> <i>({reason})</i>")
        typo_block = "\n📚 <b>НЕГРАМОТНЫЕ СЛОВА (свой алгоритм):</b>\n" + "\n".join(lines) + "\n"

    ai_block = ""
    if ai_json:
        errs = ai_json.get("errors", [])
        errs_lines = ""
        if errs:
            errs_lines = "\n🧠 <b>ОШИБКИ ОТ AI:</b>\n"
            for e in errs[:15]:
                w = esc(e.get("word", ""))
                f = esc(e.get("fix", ""))
                r = esc(e.get("reason", ""))
                errs_lines += f"  ⚠️ <code>{w}</code> → <b>{f}</b>\n      <i>{r}</i>\n"
        else:
            errs_lines = "\n🧠 <b>AI не нашёл явных ошибок</b> ✅\n"

        verdict = esc(ai_json.get("verdict", ""))
        ai_block = (
            "\n🧠 <b>AI-ОЦЕНКА:</b>\n"
            f"  ├ Общая: <b>{ai_json.get('overall', 0)}/100</b>\n"
            f"  ├ Грамотность: <b>{ai_json.get('grammar', 0)}/100</b>\n"
            f"  ├ Адекватность: <b>{ai_json.get('adequacy', 0)}/100</b>\n"
            f"  ├ Осмысленность био: <b>{ai_json.get('bio_meaning', 0)}/100</b>\n"
            f"  ├ РП-соответствие: <b>{ai_json.get('rp_match', 0)}/100</b>\n"
            f"  └ Полнота: <b>{ai_json.get('completeness', 0)}/100</b>\n"
            f"{errs_lines}"
            f"\n💬 <b>Вердикт AI:</b> <i>{verdict}</i>\n"
        )

    footer = f"\n{divider()}\n🤖 <i>Решение вынесено автоматически.</i>"
    return header + info + rb + typo_block + ai_block + footer


# ═══════════════ ХЕНДЛЕРЫ ═══════════════
@dp.message(CommandStart())
async def start(m: types.Message, state: FSMContext):
    await state.clear()
    ai = "🧠 AI: <b>включён</b>" if AI_API_KEY else "🧠 AI: <b>выключен</b>"
    text = (
        "╔════════════════════════════╗\n"
        "     🎯 <b>ПРОВЕРКА ЗАЯВОК ОПГ</b> 🎯\n"
        "╚════════════════════════════╝\n\n"
        "👋 Привет! Я автоматически проверяю заявки.\n\n"
        "🔍 <b>Проверяю:</b>\n"
        f"  • 📝 Био: {MIN_BIO_SENT}+ предложений и {MIN_BIO_LEN}+ символов\n"
        f"  • 🎂 Возраст {MIN_AGE}+ (из «Ваше реальное имя и возраст»)\n"
        f"  • ⏰ Онлайн {MIN_ONLINE}+ часов\n"
        "  • 📋 Все обязательные пункты\n"
        "  • 🔁 Дубликаты\n"
        f"  • 📚 Грамотность (мин. {MIN_GRAMMAR}/100)\n"
        "  • ⚠️ Показываю неграмотные слова\n"
        "  • 🧠 AI-анализ (Groq)\n\n"
        f"{ai}\n\n{divider()}\n💡 Выбери действие 👇")
    await m.answer(text, parse_mode="HTML", reply_markup=main_kb())


@dp.callback_query(F.data == "menu")
async def cb_menu(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await c.answer()
    try:
        await c.message.edit_reply_markup(reply_markup=None)
    except:
        pass
    await c.message.answer("🏠 Меню:", reply_markup=main_kb())


@dp.callback_query(F.data == "check")
async def cb_check(c: CallbackQuery, state: FSMContext):
    await c.answer()
    await state.set_state(Check.waiting)
    await c.message.answer("📥 <b>Кидай текст заявки</b> одним сообщением.",
                           parse_mode="HTML", reply_markup=cancel_kb())


@dp.callback_query(F.data == "stats")
async def cb_stats(c: CallbackQuery):
    await c.answer()
    s = get_stats()
    await c.message.answer(
        f"📊 <b>Статистика</b>\n\n  ├ Всего: <b>{s['total']}</b>\n"
        f"  ├ Принято: <b>{s['accepted']}</b>\n  ├ Отказов: <b>{s['rejected']}</b>\n"
        f"  └ Сегодня: <b>{s['today']}</b>", parse_mode="HTML", reply_markup=back_kb())


@dp.callback_query(F.data == "top")
async def cb_top(c: CallbackQuery):
    await c.answer()
    rows = get_top(10)
    if not rows:
        await c.message.answer("🏆 Пусто.", reply_markup=back_kb())
        return
    text = "🏆 <b>Топ-10:</b>\n\n"
    for i, (n, d, dt) in enumerate(rows, 1):
        text += f"{i}. <b>{esc(n)}</b> — {esc(dt)}\n"
    await c.message.answer(text, parse_mode="HTML", reply_markup=back_kb())


@dp.callback_query(F.data == "help")
async def cb_help(c: CallbackQuery):
    await c.answer()
    text = ("📖 <b>ИНСТРУКЦИЯ</b>\n\n"
            "1️⃣ Нажми «📝 Проверить заявку»\n"
            "2️⃣ Скопируй заявку из Discord\n"
            "3️⃣ Отправь боту одним сообщением\n"
            "4️⃣ Бот + нейронка вынесут вердикт\n\n"
            "📚 <b>Проверяется:</b> био, возраст, онлайн, дубликаты, "
            "грамотность и AI-анализ.")
    await c.message.answer(text, parse_mode="HTML", reply_markup=back_kb())


@dp.message(Check.waiting)
async def check_app(m: types.Message, state: FSMContext):
    try:
        text = m.text or m.caption or ""
        if len(text.strip()) < 30:
            await m.answer("❌ Слишком коротко.")
            return

        wait = await m.answer(
            "⏳ <b>Проверяю заявку...</b>\n<i>Нейронка анализирует текст, подожди ~5 сек.</i>",
            parse_mode="HTML"
        )

        data = parse_application(text)
        duplicate = find_duplicate(data["nickname"], data["discord"])
        grammar = check_grammar(text)
        ai_json = await ai_check(text) if AI_API_KEY else None
        emoji, reasons, status = make_verdict(data, duplicate, grammar, ai_json)
        card = build_card(data, reasons, status, grammar, ai_json)

        try:
            await wait.delete()
        except:
            pass
        await m.answer(card, parse_mode="HTML", reply_markup=main_kb())

        save_application(m.from_user, data, status, reasons, grammar, ai_json)

        if ADMIN_CHAT_ID:
            safe = esc(text[:3500])
            u = m.from_user
            admin_text = (f"{card}\n\n{divider()}\n"
                          f"👤 @{esc(u.username) or 'без_юз'} (<code>{u.id}</code>)\n\n"
                          f"📄 <b>Оригинал:</b>\n<blockquote>{safe}</blockquote>")
            try:
                await bot.send_message(chat_id=ADMIN_CHAT_ID, text=admin_text,
                                       parse_mode="HTML")
            except Exception as e:
                logging.error(f"admin: {e}")

        await state.clear()
    except Exception as e:
        logging.error(f"err: {traceback.format_exc()}")
        await m.answer(f"⚠️ <code>{esc(str(e))[:300]}</code>", parse_mode="HTML")


@dp.message(F.text == "❌ Отмена")
async def cancel(m: types.Message, state: FSMContext):
    await state.clear()
    await m.answer("❌ Отменено.", reply_markup=main_kb())


@dp.message(Command("stats"))
async def cmd_stats(m: types.Message):
    s = get_stats()
    await m.answer(
        f"📊 <b>Статистика</b>\n\n  ├ Всего: <b>{s['total']}</b>\n"
        f"  ├ Принято: <b>{s['accepted']}</b>\n  ├ Отказов: <b>{s['rejected']}</b>\n"
        f"  └ Сегодня: <b>{s['today']}</b>", parse_mode="HTML")


@dp.message(Command("top"))
async def cmd_top(m: types.Message):
    rows = get_top(10)
    if not rows:
        await m.answer("🏆 Пусто.")
        return
    text = "🏆 <b>Топ-10:</b>\n\n"
    for i, (n, d, dt) in enumerate(rows, 1):
        text += f"{i}. <b>{esc(n)}</b> — {esc(dt)}\n"
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


@dp.message()
async def fallback(m: types.Message, state: FSMContext):
    await m.answer("💡 /menu — открыть меню.", reply_markup=main_kb())


# ═══════════════ ЗАПУСК ═══════════════
async def main():
    logging.info("Bot started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
