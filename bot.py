import asyncio
import logging
import aiohttp
import base64
import json
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
FUEL_NAMES = {"92": "АИ-92", "95": "АИ-95", "98": "АИ-98", "100": "АИ-100", "dt": "ДТ", "gas": "ГАЗ"}

AZS_LIST = [
    {"id": 1, "name": "Лукойл №13202", "address": "ул. Волгоградская, 48"},
    {"id": 2, "name": "Татнефть №16", "address": "ул. Лодыгина, 17Б"},
    {"id": 3, "name": "Башнефть Косарева", "address": "ул. Косарева, 128а"},
]

user_states = {}

# --- /START ---
@dp.bot_started()
async def start(event: BotStarted):
    await event.answer("👋 Привет! Напиши /start")

@dp.message_created(CommandStart())
async def cmd_start(event: MessageCreated):
    user_id = str(event.message.from_user.id)
    user_states[user_id] = {"step": "azs"}
    
    # Отправляем список АЗС
    msg = "⛽ Выбери АЗС, написав её номер:\n\n"
    for azs in AZS_LIST:
        msg += f"{azs['id']} — {azs['name']} ({azs['address']})\n"
    msg += "\nНапример, напиши 1"

    await event.answer(msg)

# --- ВЫБОР АЗС ---
@dp.message_created(F.message.body.text)
async def handle_text(event: MessageCreated):
    user_id = str(event.message.from_user.id)
    text = event.message.body.text.strip()
    state = user_states.get(user_id)
    
    if not state:
        await event.answer("Напиши /start, чтобы начать")
        return
    
    if state.get("step") == "azs":
        try:
            azs_id = int(text)
            # Проверяем, есть ли такая АЗС
            azs_exists = any(azs["id"] == azs_id for azs in AZS_LIST)
            if not azs_exists:
                await event.answer("❌ Такой АЗС нет. Выбери номер из списка.")
                return
            
            state["azs_id"] = azs_id
            state["step"] = "fuel"
            state["fuel_status"] = {}
            state["prices"] = {}
            state["remaining"] = {}
            state["fuel_index"] = 0  # начинаем с первого вида топлива
            
            # Запрашиваем первый вид топлива
            await ask_fuel(event, state)
            
        except ValueError:
            await event.answer("❌ Введи номер АЗС из списка (например, 1)")

# --- СПРАШИВАЕМ ТОПЛИВО ---
async def ask_fuel(event: MessageCreated, state: dict):
    fuel_idx = state.get("fuel_index", 0)
    if fuel_idx >= len(FUEL_TYPES):
        # Все виды топлива обработаны
        state["step"] = "queue"
        await event.answer("🚗 Введи количество машин в очереди (число):")
        return
    
    fuel_type = FUEL_TYPES[fuel_idx]
    fuel_name = FUEL_NAMES[fuel_type]
    msg = f"📊 Статус для {fuel_name}:\n"
    msg += "Напиши:\n"
    msg += "✅ 1 — есть\n"
    msg += "❌ 2 — нет\n"
    msg += "⛔ 3 — слив\n"
    msg += f"(или 'пропустить' чтобы пропустить {fuel_name})"
    
    await event.answer(msg)
    state["current_fuel"] = fuel_type

# --- ОБРАБОТКА СТАТУСА ТОПЛИВА ---
@dp.message_created(F.message.body.text)
async def handle_fuel_status(event: MessageCreated):
    user_id = str(event.message.from_user.id)
    text = event.message.body.text.strip().lower()
    state = user_states.get(user_id)
    
    if not state or state.get("step") != "fuel":
        return
    
    fuel_type = state.get("current_fuel")
    if not fuel_type:
        return
    
    # Если пропуск
    if text in ["пропустить", "skip", "пропуск"]:
        state["fuel_index"] += 1
        await ask_fuel(event, state)
        return
    
    # Определяем статус
    status_map = {
        "1": "available",
        "2": "unavailable",
        "3": "refueling",
        "✅": "available",
        "❌": "unavailable",
        "⛔": "refueling",
        "есть": "available",
        "нет": "unavailable",
        "слив": "refueling",
    }
    
    status = status_map.get(text)
    if not status:
        await event.answer("❌ Напиши 1 (есть), 2 (нет), 3 (слив) или 'пропустить'")
        return
    
    # Сохраняем статус
    state["fuel_status"][fuel_type] = status
    await event.answer(f"✅ {FUEL_NAMES[fuel_type]} = {status}")
    
    # Спрашиваем остаток и цену
    state["step"] = "remaining"
    state["current_fuel"] = fuel_type
    await event.answer(f"📊 Введи остаток для {FUEL_NAMES[fuel_type]} в % (0-100), или 'пропустить':")

# --- ОСТАТОК ---
@dp.message_created(F.message.body.text)
async def handle_remaining(event: MessageCreated):
    user_id = str(event.message.from_user.id)
    text = event.message.body.text.strip().lower()
    state = user_states.get(user_id)
    
    if not state or state.get("step") != "remaining":
        return
    
    fuel_type = state.get("current_fuel")
    if not fuel_type:
        return
    
    if text in ["пропустить", "skip", "пропуск"]:
        state["step"] = "price"
        await event.answer(f"💰 Введи цену для {FUEL_NAMES[fuel_type]} (например, 52.50), или 'пропустить':")
        return
    
    try:
        remaining = int(text)
        if remaining < 0 or remaining > 100:
            await event.answer("❌ Остаток должен быть от 0 до 100%")
            return
        state["remaining"][fuel_type] = remaining
        await event.answer(f"✅ Остаток {remaining}% сохранён")
        state["step"] = "price"
        await event.answer(f"💰 Введи цену для {FUEL_NAMES[fuel_type]} (например, 52.50), или 'пропустить':")
    except ValueError:
        await event.answer("❌ Введи число от 0 до 100")

# --- ЦЕНА ---
@dp.message_created(F.message.body.text)
async def handle_price(event: MessageCreated):
    user_id = str(event.message.from_user.id)
    text = event.message.body.text.strip().lower()
    state = user_states.get(user_id)
    
    if not state or state.get("step") != "price":
        return
    
    fuel_type = state.get("current_fuel")
    if not fuel_type:
        return
    
    if text in ["пропустить", "skip", "пропуск"]:
        # Переходим к следующему топливу
        state["fuel_index"] += 1
        state["step"] = "fuel"
        await ask_fuel(event, state)
        return
    
    try:
        price = float(text)
        if price < 0:
            await event.answer("❌ Цена не может быть отрицательной")
            return
        state["prices"][fuel_type] = price
        await event.answer(f"✅ Цена {price} руб сохранена")
        
        # Переходим к следующему топливу
        state["fuel_index"] += 1
        state["step"] = "fuel"
        await ask_fuel(event, state)
    except ValueError:
        await event.answer("❌ Введи число (например, 52.50)")

# --- ОЧЕРЕДЬ ---
@dp.message_created(F.message.body.text)
async def handle_queue(event: MessageCreated):
    user_id = str(event.message.from_user.id)
    text = event.message.body.text.strip()
    state = user_states.get(user_id)
    
    if not state or state.get("step") != "queue":
        return
    
    try:
        queue = int(text)
        if queue < 0:
            await event.answer("❌ Количество машин не может быть отрицательным")
            return
        state["queue"] = queue
        state["step"] = "photo"
        await event.answer("📸 Отправь фото заправки (или напиши 'пропустить')")
    except ValueError:
        await event.answer("❌ Введи число машин в очереди")

# --- ФОТО ---
@dp.message_created(F.message.body.photo)
async def handle_photo(event: MessageCreated):
    user_id = str(event.message.from_user.id)
    state = user_states.get(user_id)
    
    if not state or state.get("step") != "photo":
        return
    
    try:
        photo = event.message.body.photo
        file_id = photo.file_id
        file = await bot.get_file(file_id)
        file_content = await bot.download_file(file.file_path)
        base64_photo = base64.b64encode(file_content).decode('utf-8')
        mime_type = photo.mime_type or "image/jpeg"
        state["photo_base64"] = f"data:{mime_type};base64,{base64_photo}"
        await send_report(event, state)
    except Exception as e:
        logging.error(f"Ошибка фото: {e}")
        await event.answer("❌ Не удалось загрузить фото. Попробуй еще раз.")

# --- ПРОПУСК ФОТО ---
@dp.message_created(F.message.body.text)
async def handle_skip_photo(event: MessageCreated):
    user_id = str(event.message.from_user.id)
    text = event.message.body.text.strip().lower()
    state = user_states.get(user_id)
    
    if not state or state.get("step") != "photo":
        return
    
    if text in ["пропустить", "skip", "пропуск", "нет"]:
        await send_report(event, state)

# --- ОТПРАВКА ОТЧЕТА ---
async def send_report(event: MessageCreated, state: dict):
    user_id = str(event.message.from_user.id)
    name = event.message.from_user.first_name or "Оператор"
    
    report = {
        "max_user_id": user_id,
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
                    await event.answer("✅ Отчёт принят! Спасибо!")
                else:
                    error_text = await resp.text()
                    await event.answer(f"❌ Ошибка: {resp.status}\n{error_text}")
    except Exception as e:
        logging.error(f"Ошибка отправки: {e}")
        await event.answer("❌ Не удалось отправить отчёт.")
    
    # Очищаем состояние
    del user_states[user_id]

# --- ЗАПУСК ---
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
