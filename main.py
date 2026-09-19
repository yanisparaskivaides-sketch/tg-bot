import asyncio
import logging
import os
import re
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove

BOT_TOKEN = "8822110606:AAEE0ps4NI5XK4YoGJrxOYSK1BCSk3LxBik"
ADMIN_CHAT_ID = -1003709542377

if not BOT_TOKEN:
    raise ValueError("Не задан BOT_TOKEN!")

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())


class Check(StatesGroup):
    waiting_application = State()


def cancel_kb():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Отмена")]],
        resize_keyboard=True
    )


# ---------- СТАРТ ----------
@dp.message(CommandStart())
async def start(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "👋 Привет! Пришли мне <b>заявку игрока</b> текстом.\n\n"
        "Я проверю её по требованиям и скажу — прошла она или нет.\n\n"
        "📋 <b>Что я проверю автоматически:</b>\n"
        "• Биография от 5 предложений\n"
        "• Реальный возраст 14+\n"
        "• Онлайн от 3 часов в сутки\n"
        "• Наличие всех обязательных пунктов\n\n"
        "⚠️ Остальное (адекватность, наказания, уровень, знание правил) — проверяешь ты сам.",
        parse_mode="HTML",
        reply_markup=cancel_kb()
    )
    await state.set_state(Check.waiting_application)


# ---------- ОТМЕНА ----------
@dp.message(F.text == "❌ Отмена")
async def cancel(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Отменено. Начать заново: /start")


# ---------- ПРОВЕРКА ЗАЯВКИ ----------
@dp.message(Check.waiting_application)
async def check_application(message: types.Message, state: FSMContext):
    text = message.text or message.caption or ""
    if not text.strip():
        await message.answer("❌ Пришли заявку текстом, а не пустое сообщение.")
        return

    errors = []
    warnings = []
    passed = []

    # --- 1. Обязательные пункты ---
    required_fields = {
        "NickName": r"ник\s*name|nickname|ник",
        "Статистика": r"скриншот|статистик|номер аккаунта|imgur|yapix",
        "Биография": r"биограф",
        "Реальное имя и возраст": r"реальн\w*\s*имя|имя\s*и\s*возраст",
        "Локация": r"стран|город|часов\w*\s*пояс",
        "Онлайн": r"онлайн",
        "Discord": r"discord|дискорд",
        "Правила": r"правил",
    }
    lower = text.lower()
    for name, pattern in required_fields.items():
        if re.search(pattern, lower):
            passed.append(name)
        else:
            errors.append(f"❌ Не найден пункт: <b>{name}</b>")

    # --- 2. Биография: от 5 предложений ---
    bio_match = re.search(r"биограф[^\n]*\n?(.*?)(?=\n\s*\n|\n[A-ZА-ЯЁ][^\n]*:|$)", text, re.DOTALL | re.IGNORECASE)
    bio_text = bio_match.group(1) if bio_match else ""
    sentence_count = len(re.findall(r"[.!?]+", bio_text))
    if sentence_count < 5:
        errors.append(f"❌ Биография: только <b>{sentence_count}</b> предложений, нужно минимум 5")

    # --- 3. Возраст 14+ ---
    age_match = re.search(r"(\d{1,2})\s*(?:лет|года|год|y\.?o\.?|years?)", lower)
    if not age_match:
        age_match = re.search(r"возраст[:\s]*(\d{1,2})", lower)
    if age_match:
        age = int(age_match.group(1))
        if age < 14:
            errors.append(f"❌ Возраст: <b>{age}</b> — меньше 14")
        else:
            passed.append(f"Возраст {age}")
    else:
        warnings.append("⚠️ Возраст не найден в тексте — проверь вручную")

    # --- 4. Онлайн от 3 часов ---
    online_match = re.search(r"(\d{1,2})\s*(?:час|ч\.|hours?)", lower)
    if online_match:
        hours = int(online_match.group(1))
        if hours < 3:
            errors.append(f"❌ Онлайн: <b>{hours} ч</b> — меньше 3")
        else:
            passed.append(f"Онлайн {hours} ч")
    else:
        warnings.append("⚠️ Онлайн не найден — проверь вручную")

    # --- 5. Проверка ника (грубо) ---
    nick_match = re.search(r"ник\s*name[:\s]*([^\n]+)", lower)
    if nick_match:
        nick = nick_match.group(1).strip()
        passed.append(f"Ник: {nick}")

    # --- Формируем ответ ---
    if errors:
        verdict = "🔴 <b>ЗАЯВКА НЕ ПРОШЛА</b>"
    elif warnings:
        verdict = "🟡 <b>ЗАЯВКА ТРЕБУЕТ РУЧНОЙ ПРОВЕРКИ</b>"
    else:
        verdict = "🟢 <b>ЗАЯВКА ПРОШЛА АВТОПРОВЕРКУ</b>"

    result = verdict + "\n\n"
    if passed:
        result += "✅ <b>Пройдено:</b>\n" + "\n".join(f"• {p}" for p in passed) + "\n\n"
    if errors:
        result += "❌ <b>Ошибки:</b>\n" + "\n".join(errors) + "\n\n"
    if warnings:
        result += "⚠️ <b>Проверить вручную:</b>\n" + "\n".join(warnings) + "\n\n"

    await message.answer(result, parse_mode="HTML")

    # --- Отправка в админ-группу ---
    user = message.from_user
    admin_msg = (
        f"📥 <b>Проверка заявки</b>\n"
        f"От: @{user.username or 'без_юзернейма'} (ID: <code>{user.id}</code>)\n\n"
        f"{result}\n\n"
        f"📄 <b>Исходная заявка:</b>\n<code>{text[:3500]}</code>"
    )
    if ADMIN_CHAT_ID:
        try:
            await bot.send_message(chat_id=ADMIN_CHAT_ID, text=admin_msg, parse_mode="HTML")
        except Exception as e:
            logging.error(f"Ошибка отправки в админ-чат: {e}")

    await message.answer("Можешь прислать следующую заявку. Или /start для новых инструкций.",
                         reply_markup=cancel_kb())


@dp.message()
async def fallback(message: types.Message, state: FSMContext):
    await message.answer("Нажми /start, чтобы начать проверку заявки.")


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
