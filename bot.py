import asyncio
import logging
import json
import base64
import aiohttp
import os
from maxapi import Bot, Dispatcher, F
from maxapi.filters.command import CommandStart
from maxapi.types import BotStarted, MessageCreated

# ===== КОНФИГ =====
MAX_BOT_TOKEN = "f9LHodD0cOIv3pssaR8kV9WyEVMdYmHoyXHjxLnQtCSRcENWj-6f9ZhyxsQC6qK8F7qOSqpCgIwTkRN8q9NM"
BOT_SECRET = "F7kL9mN2pQ5rS8tU1vW3xY4zA6bC0dE9"
WEBHOOK_URL = "https://data-reporting-via-a-bot.pechnovit.workers.dev/api/public/bot/report"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=MAX_BOT_TOKEN)
dp = Dispatcher()

# ===== ДАННЫЕ =====
AZS_LIST = [
    {"id": 1, "name": "Лукойл №13202"},
    {"id": 2, "name": "Татнефть №16"},
    {"id": 3, "name": "Газпромнефть №45"},
    {"id": 4, "name": "Роснефть №78"},
    {"id": 5, "name": "Башнефть №23"},
]

FUEL_TYPES = [
    {"key": "92", "name": "АИ-92"},
    {"key": "95", "name": "АИ-95"},
    {"key": "98", "name": "АИ-98"},
    {"key": "100", "name": "АИ-100"},
    {"key": "dt", "name": "ДТ"},
    {"key": "gas", "name": "ГАЗ"},
]

# ===== ХРАНИЛИЩЕ =====
user_sessions = {}

def get_session(user_id):
    if user_id not in user_sessions:
        user_sessions[user_id] = {
            "state": "start",
            "report": {
                "max_user_id": user_id,
                "operator_name": "",
                "fuel_status": {},
                "queue_length": 0,
                "prices": {},
                "remaining": {},
                "photo_base64": None,
                "note": "",
            },
            "current_fuel_index": 0,
            "current_fuel": None,
            "azs_name": None,
        }
    return user_sessions[user_id]

def get_chat_id(event):
    """Получить chat_id из любого события"""
    if hasattr(event, "chat_id"):
        return event.chat_id
    if hasattr(event, "message") and hasattr(event.message, "recipient"):
        return event.message.recipient.chat_id
    if hasattr(event, "message") and hasattr(event.message, "chat_id"):
        return event.message.chat_id
    return None

def make_keyboard(buttons):
    """Создает inline-клавиатуру из словаря"""
    return {"inline_keyboard": buttons}

def make_button(text, callback_data):
    return {"text": text, "callback_data": callback_data}

# ===== ОТПРАВКА =====
async def send_message(chat_id, text, keyboard=None):
    try:
        if keyboard:
            await bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard)
        else:
            await bot.send_message(chat_id=chat_id, text=text)
        logger.info(f"✅ Отправлено: {text[:50]}...")
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка отправки: {e}")
        return False

# ===== ОТПРАВКА ОТЧЕТА =====
async def send_report_to_app(user_id):
    session = get_session(user_id)
    report = session["report"]
    headers = {"x-bot-secret": BOT_SECRET}
    try:
        async with aiohttp.ClientSession() as session_http:
            async with session_http.post(WEBHOOK_URL, json=report, headers=headers, timeout=30) as resp:
                if resp.status == 200:
                    logger.info("✅ Отчет отправлен")
                    return True
                else:
                    logger.error(f"❌ Ошибка: {resp.status}")
                    return False
    except Exception as e:
        logger.error(f"❌ Исключение: {e}")
        return False

# ===== ОБРАБОТЧИКИ =====

@dp.bot_started()
async def on_start(event: BotStarted):
    chat_id = get_chat_id(event)
    if chat_id:
        await send_message(chat_id, "👋 Привет! Напиши /start")

@dp.message_created(CommandStart())
async def cmd_start(event: MessageCreated):
    chat_id = get_chat_id(event)
    if not chat_id:
        return
    user_id = str(chat_id)
    session = get_session(user_id)
    session["state"] = "start"
    session["operator_name"] = "Оператор"
    keyboard = make_keyboard([[make_button("🚀 Начать отчет", "start_report")]])
    await send_message(chat_id, "Привет! Нажми кнопку, чтобы начать.", keyboard)

@dp.message_created(F.message.body.text)
async def handle_text(event: MessageCreated):
    chat_id = get_chat_id(event)
    if not chat_id:
        return
    user_id = str(chat_id)
    text = event.message.body.text.strip()
    session = get_session(user_id)
    state = session.get("state")

    if text.startswith("/"):
        return

    if state == "remaining":
        try:
            remaining = int(text)
            fuel = session.get("current_fuel")
            if 0 <= remaining <= 100:
                session["report"]["remaining"][fuel["key"]] = remaining
                await send_message(chat_id, f"Остаток {fuel['name']}: {remaining}%")
                await ask_price(chat_id, user_id)
            else:
                await send_message(chat_id, "❌ Введите число от 0 до 100")
        except ValueError:
            await send_message(chat_id, "❌ Введите целое число")
        return

    if state == "price":
        try:
            price = float(text.replace(",", "."))
            fuel = session.get("current_fuel")
            if price > 0:
                session["report"]["prices"][fuel["key"]] = price
                await send_message(chat_id, f"Цена {fuel['name']}: {price} руб.")
                session["current_fuel_index"] += 1
                await ask_fuel_status(chat_id, user_id)
            else:
                await send_message(chat_id, "❌ Введите положительное число")
        except ValueError:
            await send_message(chat_id, "❌ Введите число")
        return

    if state == "queue":
        try:
            queue_length = int(text)
            if queue_length >= 0:
                session["report"]["queue_length"] = queue_length
                await send_message(chat_id, f"Очередь: {queue_length} машин")
                await ask_photo(chat_id, user_id)
            else:
                await send_message(chat_id, "❌ Введите неотрицательное число")
        except ValueError:
            await send_message(chat_id, "❌ Введите целое число")
        return

    if state == "note":
        session["report"]["note"] = text
        await show_summary(chat_id, user_id)
        return

    if state in ["fuel_status", "photo"]:
        await send_message(chat_id, "Пожалуйста, используйте кнопки.")
        return

# ===== ОБРАБОТКА ФОТО =====
@dp.message_created(F.message.body.photo)
async def handle_photo(event: MessageCreated):
    chat_id = get_chat_id(event)
    if not chat_id:
        return
    user_id = str(chat_id)
    session = get_session(user_id)
    state = session.get("state")

    if state != "photo":
        return

    try:
        photo = event.message.body.photo
        file_id = photo.file_id
        file = await bot.get_file(file_id)
        content = await bot.download_file(file.file_path)
        b64 = base64.b64encode(content).decode("utf-8")
        session["report"]["photo_base64"] = f"data:image/jpeg;base64,{b64}"
        await send_message(chat_id, "✅ Фото получено!")
        await ask_note(chat_id, user_id)
    except Exception as e:
        logger.error(f"❌ Ошибка фото: {e}")
        await send_message(chat_id, "❌ Не удалось загрузить фото. Попробуйте ещё раз или пропустите.")

# ===== CALLBACK =====
@dp.message_callback()
async def handle_callback(event):
    chat_id = get_chat_id(event)
    if not chat_id:
        return
    user_id = str(chat_id)
    data = event.callback.payload
    session = get_session(user_id)

    if data == "start_report":
        await show_azs_list(chat_id, user_id)
        return

    if data.startswith("azs_"):
        azs_id = int(data.split("_")[1])
        azs = next((a for a in AZS_LIST if a["id"] == azs_id), None)
        if azs:
            session["report"]["azs_id"] = azs_id
            session["azs_name"] = azs["name"]
            await send_message(chat_id, f"✅ Выбрана АЗС: {azs['name']}")
            await ask_fuel_status(chat_id, user_id)
        return

    if data.startswith("fuel_"):
        status = data.split("_")[1]
        fuel = session.get("current_fuel")
        if fuel:
            session["report"]["fuel_status"][fuel["key"]] = status
            await send_message(chat_id, f"{fuel['name']}: {status}")
            await ask_remaining(chat_id, user_id)
        return

    if data.startswith("skip_"):
        if data == "skip_remaining":
            await ask_price(chat_id, user_id)
        elif data == "skip_price":
            session["current_fuel_index"] += 1
            await ask_fuel_status(chat_id, user_id)
        elif data == "skip_photo":
            await ask_note(chat_id, user_id)
        elif data == "skip_note":
            await show_summary(chat_id, user_id)
        return

    if data.startswith("confirm_"):
        if data == "confirm_send":
            await send_message(chat_id, "⏳ Отправка...")
            success = await send_report_to_app(user_id)
            if success:
                await send_message(chat_id, "✅ Отчет отправлен!")
            else:
                await send_message(chat_id, "❌ Ошибка отправки")
        else:
            await send_message(chat_id, "❌ Отчет отменен")
        if user_id in user_sessions:
            del user_sessions[user_id]
        return

# ===== ШАГИ =====
async def show_azs_list(chat_id, user_id):
    buttons = [[make_button(azs["name"], f"azs_{azs['id']}")] for azs in AZS_LIST]
    keyboard = make_keyboard(buttons)
    await send_message(chat_id, "📍 Выберите АЗС:", keyboard)

async def ask_fuel_status(chat_id, user_id):
    session = get_session(user_id)
    idx = session.get("current_fuel_index", 0)
    if idx >= len(FUEL_TYPES):
        await ask_queue(chat_id, user_id)
        return
    fuel = FUEL_TYPES[idx]
    session["current_fuel"] = fuel
    session["state"] = "fuel_status"
    keyboard = make_keyboard([[
        make_button("✅ Есть", "fuel_available"),
        make_button("❌ Нет", "fuel_unavailable"),
        make_button("🔄 Слив", "fuel_refueling"),
    ]])
    await send_message(chat_id, f"⛽ Статус для {fuel['name']}:", keyboard)

async def ask_remaining(chat_id, user_id):
    session = get_session(user_id)
    fuel = session.get("current_fuel")
    session["state"] = "remaining"
    keyboard = make_keyboard([[make_button("Пропустить", "skip_remaining")]])
    await send_message(chat_id, f"📊 Остаток для {fuel['name']} в % (0-100):", keyboard)

async def ask_price(chat_id, user_id):
    session = get_session(user_id)
    fuel = session.get("current_fuel")
    session["state"] = "price"
    keyboard = make_keyboard([[make_button("Пропустить", "skip_price")]])
    await send_message(chat_id, f"💰 Цена для {fuel['name']}:", keyboard)

async def ask_queue(chat_id, user_id):
    session = get_session(user_id)
    session["state"] = "queue"
    await send_message(chat_id, "🚗 Количество машин в очереди (число):")

async def ask_photo(chat_id, user_id):
    session = get_session(user_id)
    session["state"] = "photo"
    keyboard = make_keyboard([[make_button("Пропустить", "skip_photo")]])
    await send_message(chat_id, "📸 Отправь фото или нажми «Пропустить»:", keyboard)

async def ask_note(chat_id, user_id):
    session = get_session(user_id)
    session["state"] = "note"
    keyboard = make_keyboard([[make_button("Пропустить", "skip_note")]])
    await send_message(chat_id, "📝 Добавь примечание (или пропусти):", keyboard)

async def show_summary(chat_id, user_id):
    session = get_session(user_id)
    report = session["report"]
    text = "📋 Итоговый отчет:\n\n"
    text += f"🏢 АЗС: {session.get('azs_name', 'Не выбрана')}\n"
    text += f"👤 Оператор: {report['operator_name']}\n\n"
    text += "⛽ Статусы топлива:\n"
    for fuel in FUEL_TYPES:
        status = report["fuel_status"].get(fuel["key"], "не указан")
        text += f"  • {fuel['name']}: {status}\n"
    text += f"\n🚗 Очередь: {report['queue_length']} машин\n"
    if report["prices"]:
        text += "\n💰 Цены:\n"
        for k, v in report["prices"].items():
            name = next((f["name"] for f in FUEL_TYPES if f["key"] == k), k)
            text += f"  • {name}: {v} руб.\n"
    if report["note"]:
        text += f"\n📝 Примечание: {report['note']}\n"
    keyboard = make_keyboard([
        [make_button("✅ Отправить", "confirm_send"), make_button("❌ Отменить", "confirm_cancel")]
    ])
    await send_message(chat_id, text, keyboard)

# ===== ЗАПУСК =====
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
