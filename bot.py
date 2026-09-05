import asyncio
import logging
import aiohttp
import base64
from maxapi import Bot, Dispatcher, types

# --- КОНФИГ ---
MAX_BOT_TOKEN = "f9LHodD0cOIv3pssaR8kV9WyEVMdYmHoyXHjxLnQtCSRcENWj-6f9ZhyxsQC6qK8F7qOSqpCgIwTkRN8q9NM"
BOT_SECRET = "F7kL9mN2pQ5rS8tU1vW3xY4zA6bC0dE9"
APP_URL = "https://data-reporting-via-a-bot.pechnovit.workers.dev"
WEBHOOK_URL = f"{APP_URL}/api/public/bot/report"

logging.basicConfig(level=logging.INFO)
bot = Bot(token=MAX_BOT_TOKEN)
dp = Dispatcher(bot)

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

# --- ОБРАБОТЧИКИ ---
@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    user_id = str(message.user_id)
    user_states[user_id] = {"step": "azs"}
    msg = "⛽ Выбери АЗС, написав её номер:\n\n"
    for azs in AZS_LIST:
        msg += f"{azs['id']} — {azs['name']} ({azs['address']})\n"
    msg += "\nНапример, напиши 1"
    await message.reply(msg)

@dp.message_handler(content_types=['text'])
async def handle_text(message: types.Message):
    user_id = str(message.user_id)
    text = message.text.strip()
    state = user_states.get(user_id)
    if not state:
        await message.reply("Напиши /start, чтобы начать")
        return
    if state.get("step") == "azs":
        try:
            azs_id = int(text)
            if not any(azs["id"] == azs_id for azs in AZS_LIST):
                await message.reply("❌ Такой АЗС нет. Выбери номер из списка.")
                return
            state["azs_id"] = azs_id
            state["step"] = "fuel"
            state["fuel_status"] = {}
            state["prices"] = {}
            state["remaining"] = {}
            state["fuel_index"] = 0
            await ask_fuel(message, state)
        except ValueError:
            await message.reply("❌ Введи номер АЗС из списка (например, 1)")
    elif state.get("step") == "fuel":
        await handle_fuel_status(message, state)
    elif state.get("step") == "remaining":
        await handle_remaining(message, state)
    elif state.get("step") == "price":
        await handle_price(message, state)
    elif state.get("step") == "queue":
        await handle_queue(message, state)
    elif state.get("step") == "photo" and text.lower() in ["пропустить", "skip", "пропуск", "нет"]:
        await send_report(message, state)

async def ask_fuel(message: types.Message, state: dict):
    idx = state.get("fuel_index", 0)
    if idx >= len(FUEL_TYPES):
        state["step"] = "queue"
        await message.reply("🚗 Введи количество машин в очереди (число):")
        return
    fuel = FUEL_TYPES[idx]
    msg = f"📊 Статус для {FUEL_NAMES[fuel]}:\n"
    msg += "Напиши:\n✅ 1 — есть\n❌ 2 — нет\n⛔ 3 — слив\n(или 'пропустить')"
    await message.reply(msg)
    state["current_fuel"] = fuel

async def handle_fuel_status(message: types.Message, state: dict):
    text = message.text.strip().lower()
    fuel = state.get("current_fuel")
    if not fuel: return
    if text in ["пропустить", "skip", "пропуск"]:
        state["fuel_index"] += 1
        await ask_fuel(message, state)
        return
    status_map = {"1": "available", "2": "unavailable", "3": "refueling",
                  "✅": "available", "❌": "unavailable", "⛔": "refueling",
                  "есть": "available", "нет": "unavailable", "слив": "refueling"}
    status = status_map.get(text)
    if not status:
        await message.reply("❌ Напиши 1 (есть), 2 (нет), 3 (слив) или 'пропустить'")
        return
    state["fuel_status"][fuel] = status
    await message.reply(f"✅ {FUEL_NAMES[fuel]} = {status}")
    state["step"] = "remaining"
    await message.reply(f"📊 Введи остаток для {FUEL_NAMES[fuel]} в % (0-100), или 'пропустить':")

async def handle_remaining(message: types.Message, state: dict):
    text = message.text.strip().lower()
    fuel = state.get("current_fuel")
    if not fuel: return
    if text in ["пропустить", "skip", "пропуск"]:
        state["step"] = "price"
        await message.reply(f"💰 Введи цену для {FUEL_NAMES[fuel]} (например, 52.50), или 'пропустить':")
        return
    try:
        rem = int(text)
        if rem < 0 or rem > 100:
            await message.reply("❌ Остаток должен быть от 0 до 100%")
            return
        state["remaining"][fuel] = rem
        await message.reply(f"✅ Остаток {rem}% сохранён")
        state["step"] = "price"
        await message.reply(f"💰 Введи цену для {FUEL_NAMES[fuel]} (например, 52.50), или 'пропустить':")
    except ValueError:
        await message.reply("❌ Введи число от 0 до 100")

async def handle_price(message: types.Message, state: dict):
    text = message.text.strip().lower()
    fuel = state.get("current_fuel")
    if not fuel: return
    if text in ["пропустить", "skip", "пропуск"]:
        state["fuel_index"] += 1
        state["step"] = "fuel"
        await ask_fuel(message, state)
        return
    try:
        price = float(text)
        if price < 0:
            await message.reply("❌ Цена не может быть отрицательной")
            return
        state["prices"][fuel] = price
        await message.reply(f"✅ Цена {price} руб сохранена")
        state["fuel_index"] += 1
        state["step"] = "fuel"
        await ask_fuel(message, state)
    except ValueError:
        await message.reply("❌ Введи число (например, 52.50)")

async def handle_queue(message: types.Message, state: dict):
    try:
        q = int(message.text.strip())
        if q < 0:
            await message.reply("❌ Количество машин не может быть отрицательным")
            return
        state["queue"] = q
        state["step"] = "photo"
        await message.reply("📸 Отправь фото заправки (или напиши 'пропустить')")
    except ValueError:
        await message.reply("❌ Введи число машин в очереди")

@dp.message_handler(content_types=['photo'])
async def handle_photo(message: types.Message):
    user_id = str(message.user_id)
    state = user_states.get(user_id)
    if not state or state.get("step") != "photo":
        return
    try:
        photo = message.photo[-1]
        file_id = photo.file_id
        file = await bot.get_file(file_id)
        content = await bot.download_file(file.file_path)
        b64 = base64.b64encode(content).decode('utf-8')
        state["photo_base64"] = f"data:image/jpeg;base64,{b64}"
        await send_report(message, state)
    except Exception as e:
        logging.error(f"Ошибка фото: {e}")
        await message.reply("❌ Не удалось загрузить фото. Попробуй еще раз.")

async def send_report(message: types.Message, state: dict):
    user_id = str(message.user_id)
    name = message.sender.first_name or "Оператор"
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
                    await message.reply("✅ Отчёт принят! Спасибо!")
                else:
                    await message.reply(f"❌ Ошибка: {resp.status}\n{await resp.text()}")
    except Exception as e:
        logging.error(f"Ошибка отправки: {e}")
        await message.reply("❌ Не удалось отправить отчёт.")
    del user_states[user_id]

# --- ЗАПУСК ---
async def main():
    await dp.start_polling()

if __name__ == "__main__":
    asyncio.run(main())
