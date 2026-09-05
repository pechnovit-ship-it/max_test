import asyncio
import logging
import aiohttp
import base64
from maxapi import Bot, Dispatcher, F
from maxapi.filters.command import CommandStart
from maxapi.types import BotStarted, MessageCreated

# --- КОНФИГ ---
MAX_BOT_TOKEN = "f9LHodD0cOIv3pssaR8kV9WyEVMdYmHoyXHjxLnQtCSRcENWj-6f9ZhyxsQC6qK8F7qOSqpCgIwTkRN8q9NM"
BOT_SECRET = "F7kL9mN2pQ5rS8tU1vW3xY4zA6bC0dE9"
APP_URL = "https://data-reporting-via-a-bot.pechnovit.workers.dev"
WEBHOOK_URL = f"{APP_URL}/api/public/bot/report"

logging.basicConfig(level=logging.INFO)
bot = Bot(token=MAX_BOT_TOKEN)
dp = Dispatcher()

# --- ДАННЫЕ ---
FUEL_TYPES = ["92", "95", "98", "100", "dt", "gas"]
FUEL_NAMES = {
    "92": "АИ-92", "95": "АИ-95", "98": "АИ-98",
    "100": "АИ-100", "dt": "ДТ", "gas": "ГАЗ"
}
AZS_LIST = [
    {"id": 1, "name": "Лукойл №13202", "address": "ул. Волгоградская, 48"},
    {"id": 2, "name": "Татнефть №16", "address": "ул. Лодыгина, 17Б"},
    {"id": 3, "name": "Башнефть Косарева", "address": "ул. Косарева, 128а"},
]
user_states = {}

async def send_message(chat_id, text):
    """Отправка сообщения в чат"""
    try:
        await bot.send_message(chat_id=chat_id, text=text)
        logging.info(f"✅ Отправлено: {text[:50]}...")
    except Exception as e:
        logging.error(f"❌ Ошибка отправки: {e}")

def get_chat_id(event):
    """Получить chat_id из события"""
    if hasattr(event, 'chat_id'):
        return event.chat_id
    if hasattr(event, 'message') and hasattr(event.message, 'chat_id'):
        return event.message.chat_id
    if hasattr(event, 'sender') and hasattr(event.sender, 'id'):
        return event.sender.id
    return None

# --- ОБРАБОТЧИКИ ---
@dp.bot_started()
async def start(event: BotStarted):
    chat_id = get_chat_id(event)
    if chat_id:
        await send_message(chat_id, "👋 Привет! Напиши /start")

@dp.message_created(CommandStart())
async def cmd_start(event: MessageCreated):
    chat_id = get_chat_id(event)
    if not chat_id:
        return
    # Очищаем состояние пользователя, если оно есть
    if chat_id in user_states:
        del user_states[chat_id]
    user_states[chat_id] = {"step": "azs"}
    msg = "⛽ Выбери АЗС, написав её номер:\n\n"
    for azs in AZS_LIST:
        msg += f"{azs['id']} — {azs['name']} ({azs['address']})\n"
    msg += "\nНапример, напиши 1"
    await send_message(chat_id, msg)

@dp.message_created(F.message.body.text)
async def handle_text(event: MessageCreated):
    chat_id = get_chat_id(event)
    if not chat_id:
        return
    text = event.message.body.text.strip()
    
    # Если пользователь написал /start в любом виде — перезапускаем процесс
    if text.startswith('/start'):
        await cmd_start(event)
        return
    
    state = user_states.get(chat_id)
    if not state:
        await send_message(chat_id, "Напиши /start, чтобы начать")
        return
    
    # --- ВЫБОР АЗС ---
    if state.get("step") == "azs":
        try:
            azs_id = int(text)
            if not any(azs["id"] == azs_id for azs in AZS_LIST):
                await send_message(chat_id, "❌ Такой АЗС нет. Выбери номер из списка.")
                return
            state["azs_id"] = azs_id
            state["step"] = "fuel"
            state["fuel_status"] = {}
            state["prices"] = {}
            state["remaining"] = {}
            state["fuel_index"] = 0
            await ask_fuel(chat_id, state)
        except ValueError:
            await send_message(chat_id, "❌ Введи номер АЗС из списка (например, 1)")
        return
    
    # --- СТАТУС ТОПЛИВА ---
    if state.get("step") == "fuel":
        await handle_fuel_status(chat_id, state, text)
        return
    
    # --- ОСТАТОК ---
    if state.get("step") == "remaining":
        await handle_remaining(chat_id, state, text)
        return
    
    # --- ЦЕНА ---
    if state.get("step") == "price":
        await handle_price(chat_id, state, text)
        return
    
    # --- ОЧЕРЕДЬ ---
    if state.get("step") == "queue":
        await handle_queue(chat_id, state, text)
        return
    
    # --- ПРОПУСК ФОТО ---
    if state.get("step") == "photo" and text.lower() in ["пропустить", "skip", "пропуск", "нет"]:
        await send_report(chat_id, state)
        return
    
    # Если ничего не подошло
    await send_message(chat_id, "❌ Я не понял. Напиши номер или 'пропустить'.")

async def ask_fuel(chat_id: str, state: dict):
    idx = state.get("fuel_index", 0)
    if idx >= len(FUEL_TYPES):
        state["step"] = "queue"
        await send_message(chat_id, "🚗 Введи количество машин в очереди (число):")
        return
    fuel = FUEL_TYPES[idx]
    msg = f"📊 Статус для {FUEL_NAMES[fuel]}:\n"
    msg += "Напиши:\n✅ 1 — есть\n❌ 2 — нет\n⛔ 3 — слив\n(или 'пропустить')"
    await send_message(chat_id, msg)
    state["current_fuel"] = fuel

async def handle_fuel_status(chat_id: str, state: dict, text: str):
    text = text.strip().lower()
    fuel = state.get("current_fuel")
    if not fuel: 
        return
    if text in ["пропустить", "skip", "пропуск"]:
        state["fuel_index"] += 1
        await ask_fuel(chat_id, state)
        return
    status_map = {
        "1": "available", "2": "unavailable", "3": "refueling",
        "✅": "available", "❌": "unavailable", "⛔": "refueling",
        "есть": "available", "нет": "unavailable", "слив": "refueling"
    }
    status = status_map.get(text)
    if not status:
        await send_message(chat_id, "❌ Напиши 1 (есть), 2 (нет), 3 (слив) или 'пропустить'")
        return
    state["fuel_status"][fuel] = status
    await send_message(chat_id, f"✅ {FUEL_NAMES[fuel]} = {status}")
    state["step"] = "remaining"
    await send_message(chat_id, f"📊 Введи остаток для {FUEL_NAMES[fuel]} в % (0-100), или 'пропустить':")

async def handle_remaining(chat_id: str, state: dict, text: str):
    text = text.strip().lower()
    fuel = state.get("current_fuel")
    if not fuel: 
        return
    if text in ["пропустить", "skip", "пропуск"]:
        state["step"] = "price"
        await send_message(chat_id, f"💰 Введи цену для {FUEL_NAMES[fuel]} (например, 52.50), или 'пропустить':")
        return
    try:
        rem = int(text)
        if rem < 0 or rem > 100:
            await send_message(chat_id, "❌ Остаток должен быть от 0 до 100%")
            return
        state["remaining"][fuel] = rem
        await send_message(chat_id, f"✅ Остаток {rem}% сохранён")
        state["step"] = "price"
        await send_message(chat_id, f"💰 Введи цену для {FUEL_NAMES[fuel]} (например, 52.50), или 'пропустить':")
    except ValueError:
        await send_message(chat_id, "❌ Введи число от 0 до 100")

async def handle_price(chat_id: str, state: dict, text: str):
    text = text.strip().lower()
    fuel = state.get("current_fuel")
    if not fuel: 
        return
    if text in ["пропустить", "skip", "пропуск"]:
        state["fuel_index"] += 1
        state["step"] = "fuel"
        await ask_fuel(chat_id, state)
        return
    try:
        price = float(text)
        if price < 0:
            await send_message(chat_id, "❌ Цена не может быть отрицательной")
            return
        state["prices"][fuel] = price
        await send_message(chat_id, f"✅ Цена {price} руб сохранена")
        state["fuel_index"] += 1
        state["step"] = "fuel"
        await ask_fuel(chat_id, state)
    except ValueError:
        await send_message(chat_id, "❌ Введи число (например, 52.50)")

async def handle_queue(chat_id: str, state: dict, text: str):
    try:
        q = int(text.strip())
        if q < 0:
            await send_message(chat_id, "❌ Количество машин не может быть отрицательным")
            return
        state["queue"] = q
        state["step"] = "photo"
        await send_message(chat_id, "📸 Отправь фото заправки (или напиши 'пропустить')")
    except ValueError:
        await send_message(chat_id, "❌ Введи число машин в очереди")

@dp.message_created(F.message.body.photo)
async def handle_photo(event: MessageCreated):
    chat_id = get_chat_id(event)
    if not chat_id:
        return
    state = user_states.get(chat_id)
    if not state or state.get("step") != "photo":
        return
    try:
        photo = event.message.body.photo
        file_id = photo.file_id
        file = await bot.get_file(file_id)
        content = await bot.download_file(file.file_path)
        b64 = base64.b64encode(content).decode('utf-8')
        state["photo_base64"] = f"data:image/jpeg;base64,{b64}"
        await send_report(chat_id, state)
    except Exception as e:
        logging.error(f"Ошибка фото: {e}")
        await send_message(chat_id, "❌ Не удалось загрузить фото. Попробуй еще раз.")

async def send_report(chat_id: str, state: dict):
    name = "Оператор"
    report = {
        "max_user_id": chat_id,
        "azs_id": state["azs_id"],
        "operator_name": name,
        "fuel_status": state.get("fuel_status", {}),
        "queue_length": state.get("queue", 0),
        "prices": state.get("prices", {}),
        "remaining": state.get("remaining", {}),
        "note": "",
    }
    if "photo_base64" in state:
        report["photo_base64"] = state["photo_base64"]
    headers = {"x-bot-secret": BOT_SECRET}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(WEBHOOK_URL, json=report, headers=headers) as resp:
                if resp.status == 200:
                    await send_message(chat_id, "✅ Отчёт принят! Спасибо!")
                else:
                    error_text = await resp.text()
                    await send_message(chat_id, f"❌ Ошибка: {resp.status}\n{error_text}")
    except Exception as e:
        logging.error(f"Ошибка отправки: {e}")
        await send_message(chat_id, "❌ Не удалось отправить отчёт.")
    if chat_id in user_states:
        del user_states[chat_id]

# --- ЗАПУСК ---
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
