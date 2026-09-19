import asyncio
import logging
import os
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove

# ---------- НАСТРОЙКИ ----------
BOT_TOKEN = "8822110606:AAEE0ps4NI5XK4YoGJrxOYSK1BCSk3LxBik"    # токен бота (из @BotFather)
ADMIN_CHAT_ID = -1003709542377  # ID канала/чата, куда слать заявки

if not BOT_TOKEN:
    raise ValueError("Не задан BOT_TOKEN в переменных окружения!")

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# ---------- СОСТОЯНИЯ АНКЕТЫ ----------
class Form(StatesGroup):
    nickname = State()
    stats = State()
    bio = State()
    real_name = State()
    location = State()
    online = State()
    discord = State()
    rules_known = State()
    confirm = State()

# ---------- КЛАВИАТУРЫ ----------
def yes_no_kb():
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Да"), KeyboardButton(text="Нет")]],
        resize_keyboard=True, one_time_keyboard=True
    )
    return kb

def confirm_kb():
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Отправить")], [KeyboardButton(text="Отменить")]],
        resize_keyboard=True, one_time_keyboard=True
    )
    return kb

# ---------- СТАРТ ----------
@dp.message(CommandStart())
async def start(message: types.Message, state: FSMContext):
    await state.clear()
    text = (
        "👋 Привет! Это бот для подачи заявки на пост <b>Лидера ОПГ</b>.\n\n"
        "📋 <b>Требования:</b>\n"
        "• Адекватность, сдержанность, грамотная речь\n"
        "• Отсутствие грубых нарушений и активных наказаний (BAN/WARN)\n"
        "• Знание правил своей сферы и общих правил проекта\n"
        "• Минимум 10 игровой уровень\n"
        "• Онлайн от 3 часов в сутки\n"
        "• Реальный возраст 14+\n"
        "• Наличие Discord и рабочего микрофона\n\n"
        "Если ты подходишь — начнём! Напиши свой <b>NickName</b>:"
    )
    await message.answer(text, parse_mode="HTML")
    await state.set_state(Form.nickname)

# ---------- 1. НИК ----------
@dp.message(Form.nickname)
async def process_nick(message: types.Message, state: FSMContext):
    await state.update_data(nickname=message.text)
    await message.answer(
        "📸 Отправь <b>скриншот статистики</b> и номер аккаунта (ссылка на imgur/yapix):",
        parse_mode="HTML"
    )
    await state.set_state(Form.stats)

# ---------- 2. СТАТИСТИКА ----------
@dp.message(Form.stats)
async def process_stats(message: types.Message, state: FSMContext):
    await state.update_data(stats=message.text)
    await message.answer(
        "📖 Напиши <b>биографию</b> персонажа (от 5 предложений):",
        parse_mode="HTML"
    )
    await state.set_state(Form.bio)

# ---------- 3. БИО ----------
@dp.message(Form.bio)
async def process_bio(message: types.Message, state: FSMContext):
    if len(message.text) < 80:  # грубая проверка "от 5 предложений"
        await message.answer("❌ Биография слишком короткая. Напиши развёрнуто, от 5 предложений.")
        return
    await state.update_data(bio=message.text)
    await message.answer("🧑 Напиши своё <b>реальное имя и возраст</b>:", parse_mode="HTML")
    await state.set_state(Form.real_name)

# ---------- 4. ИМЯ И ВОЗРАСТ ----------
@dp.message(Form.real_name)
async def process_real_name(message: types.Message, state: FSMContext):
    text = message.text
    # ищем возраст в тексте
    digits = "".join(ch for ch in text if ch.isdigit())
    if not digits:
        await message.answer("❌ Не вижу возраст. Напиши, например: «Иван, 16 лет».")
        return
    age = int(digits[:2]) if len(digits) >= 2 else int(digits)
    if age < 14:
        await message.answer("❌ К сожалению, реальный возраст должен быть 14+. Заявка отклонена.")
        await state.clear()
        return
    await state.update_data(real_name=text, age=age)
    await message.answer(
        "🌍 Напиши <b>страну, город и часовой пояс</b> (от МСК):",
        parse_mode="HTML"
    )
    await state.set_state(Form.location)

# ---------- 5. ЛОКАЦИЯ ----------
@dp.message(Form.location)
async def process_location(message: types.Message, state: FSMContext):
    await state.update_data(location=message.text)
    await message.answer("⏰ Какой у тебя <b>средний онлайн в сутки</b>?", parse_mode="HTML")
    await state.set_state(Form.online)

# ---------- 6. ОНЛАЙН ----------
@dp.message(Form.online)
async def process_online(message: types.Message, state: FSMContext):
    await state.update_data(online=message.text)
    await message.answer("💬 Напиши свой <b>логин Discord</b>:", parse_mode="HTML")
    await state.set_state(Form.discord)

# ---------- 7. DISCORD ----------
@dp.message(Form.discord)
async def process_discord(message: types.Message, state: FSMContext):
    await state.update_data(discord=message.text)
    await message.answer(
        "📜 Ознакомлен(а) ли ты с правилами проекта и сервера?",
        reply_markup=yes_no_kb()
    )
    await state.set_state(Form.rules_known)

# ---------- 8. ПРАВИЛА ----------
@dp.message(Form.rules_known, F.text.in_(["Да", "Нет"]))
async def process_rules(message: types.Message, state: FSMContext):
    if message.text == "Нет":
        await message.answer("❌ Без ознакомления с правилами заявку подать нельзя. Начни заново: /start")
        await state.clear()
        return
    await state.update_data(rules_known=message.text)

    data = await state.get_data()
    preview = (
        "📋 <b>Проверь свою заявку:</b>\n\n"
        f"<b>NickName:</b> {data['nickname']}\n"
        f"<b>Статистика:</b> {data['stats']}\n"
        f"<b>Биография:</b> {data['bio']}\n"
        f"<b>Имя и возраст:</b> {data['real_name']}\n"
        f"<b>Локация:</b> {data['location']}\n"
        f"<b>Онлайн:</b> {data['online']}\n"
        f"<b>Discord:</b> {data['discord']}\n"
        f"<b>С правилами ознакомлен:</b> {data['rules_known']}\n\n"
        "Если всё верно — жми «Отправить»."
    )
    await message.answer(preview, parse_mode="HTML", reply_markup=confirm_kb())
    await state.set_state(Form.confirm)

# ---------- 9. ПОДТВЕРЖДЕНИЕ ----------
@dp.message(Form.confirm, F.text == "Отправить")
async def process_confirm(message: types.Message, state: FSMContext):
    data = await state.get_data()
    user = message.from_user

    admin_text = (
        "🚨 <b>НОВАЯ ЗАЯВКА НА ЛИДЕРА ОПГ</b>\n\n"
        f"👤 <b>От:</b> @{user.username or 'без_юзернейма'} (ID: <code>{user.id}</code>)\n\n"
        f"<b>NickName:</b> {data['nickname']}\n"
        f"<b>Статистика:</b> {data['stats']}\n"
        f"<b>Биография:</b> {data['bio']}\n"
        f"<b>Имя и возраст:</b> {data['real_name']}\n"
        f"<b>Локация:</b> {data['location']}\n"
        f"<b>Онлайн:</b> {data['online']}\n"
        f"<b>Discord:</b> {data['discord']}\n"
        f"<b>Правила:</b> {data['rules_known']}"
    )

    if ADMIN_CHAT_ID:
        try:
            await bot.send_message(chat_id=ADMIN_CHAT_ID, text=admin_text, parse_mode="HTML")
        except Exception as e:
            logging.error(f"Не удалось отправить заявку в админ-чат: {e}")

    await message.answer(
        "✅ Заявка отправлена на рассмотрение администрации! Ожидай ответа.",
        reply_markup=ReplyKeyboardRemove()
    )
    await state.clear()

@dp.message(Form.confirm, F.text == "Отменить")
async def process_cancel(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Заявка отменена. Начать заново: /start", reply_markup=ReplyKeyboardRemove())

# ---------- ЛЮБОЙ ДРУГОЙ ТЕКСТ ----------
@dp.message()
async def fallback(message: types.Message):
    await message.answer("Пожалуйста, следуй инструкциям бота или начни заново: /start")

# ---------- ЗАПУСК ----------
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
