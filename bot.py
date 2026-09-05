import asyncio
import logging
import json
import aiohttp
from maxapi import Bot, Dispatcher
from maxapi.filters.command import CommandStart
from maxapi.types import MessageCreated

# --- КОНФИГ ---
MAX_BOT_TOKEN = "f9LHodD0cOIv3pssaR8kV9WyEVMdYmHoyXHjxLnQtCSRcENWj-6f9ZhyxsQC6qK8F7qOSqpCgIwTkRN8q9NM"
BOT_SECRET = "F7kL9mN2pQ5rS8tU1vW3xY4zA6bC0dE9"
APP_URL = "https://data-reporting-via-a-bot.pechnovit.workers.dev"
WEBHOOK_URL = f"{APP_URL}/api/public/bot/report"

logging.basicConfig(level=logging.INFO)
bot = Bot(token=MAX_BOT_TOKEN)
dp = Dispatcher()

# --- ДАННЫЕ ---
AZS_LIST = [
    {"id": 1, "name": "Лукойл №13202", "address": "ул. Волгоградская, 48"},
    {"id": 2, "name": "Татнефть №16", "address": "ул. Лодыгина, 17Б"},
    {"id": 3, "name": "Башнефть Косарева", "address": "ул. Косарева, 128а"},
]

user_states = {}

async def send_message(chat_id, text, keyboard=None):
    """Отправка сообщения через HTTP API MAX с поддержкой клавиатуры"""
    url = "https://api.max.ru/bot/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    if keyboard:
        payload["reply_markup"] = keyboard
    headers = {"Authorization": f"Bearer {MAX_BOT_TOKEN}"}
    
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=headers) as resp:
            if resp.status == 200:
                logging.info(f"✅ Отправлено: {text[:50]}...")
                return True
            else:
                error_text = await resp.text()
                logging.error(f"❌ Ошибка HTTP: {resp.status} {error_text}")
                return False

@dp.message_created(CommandStart())
async def cmd_start(event: MessageCreated):
    chat_id = event.chat_id
    user_states[chat_id] = {"step": "azs"}
    
    # Создаём кнопки для выбора АЗС
    keyboard = {
        "inline_keyboard": [
            [{"text": f"{azs['name']}", "callback_data": f"azs_{azs['id']}"}]
            for azs in AZS_LIST
        ]
    }
    await send_message(
        chat_id,
        "⛽ Выберите АЗС для отчёта:",
        keyboard
    )

@dp.callback_query()
async def handle_callback(event):
    chat_id = event.chat_id
    data = event.data
    
    if data.startswith("azs_"):
        azs_id = int(data.split("_")[1])
        state = user_states.get(chat_id)
        if state:
            state["azs_id"] = azs_id
            state["step"] = "fuel_status"
            state["fuel_status"] = {}
            state["fuel_index"] = 0
            await send_message(
                chat_id, 
                f"✅ Выбрана АЗС: {AZS_LIST[azs_id-1]['name']}\n"
                "Теперь введи статус для АИ-92:\n"
                "1 — есть\n2 — нет\n3 — слив"
            )
        await bot.answer_callback_query(event.id, "АЗС выбрана ✅")

@dp.message_created()
async def handle_text(event: MessageCreated):
    chat_id = event.chat_id
    text = event.text.strip()
    state = user_states.get(chat_id)
    
    if not state:
        await send_message(chat_id, "Напиши /start, чтобы начать")
        return
    
    if state.get("step") == "fuel_status":
        fuel_types = ["92", "95", "98", "100", "dt", "gas"]
        fuel_names = {"92": "АИ-92", "95": "АИ-95", "98": "АИ-98", "100": "АИ-100", "dt": "ДТ", "gas": "ГАЗ"}
        idx = state.get("fuel_index", 0)
        
        if idx < len(fuel_types):
            fuel = fuel_types[idx]
            status_map = {"1": "available", "2": "unavailable", "3": "refueling"}
            
            if text in ["1", "2", "3"]:
                state["fuel_status"][fuel] = status_map[text]
                state["fuel_index"] = idx + 1
                if state["fuel_index"] < len(fuel_types):
                    next_fuel = fuel_types[state["fuel_index"]]
                    await send_message(
                        chat_id,
                        f"✅ {fuel_names[fuel]} = {status_map[text]}\n"
                        f"Теперь статус для {fuel_names[next_fuel]}:"
                    )
                else:
                    # Все виды топлива обработаны
                    state["step"] = "queue"
                    await send_message(
                        chat_id,
                        "🚗 Введи количество машин в очереди (число):"
                    )
            else:
                await send_message(chat_id, "❌ Напиши 1 (есть), 2 (нет) или 3 (слив)")
        return
    
    if state.get("step") == "queue":
        try:
            queue = int(text)
            state["queue"] = queue
            state["step"] = "photo"
            await send_message(
                chat_id,
                "📸 Отправь фото заправки (или напиши 'пропустить')"
            )
        except ValueError:
            await send_message(chat_id, "❌ Введи число")
        return
    
    if state.get("step") == "photo":
        if text.lower() in ["пропустить", "skip", "пропуск", "нет"]:
            await send_report(chat_id, state)
        else:
            await send_message(chat_id, "📸 Отправь фото или напиши 'пропустить'")
        return

@dp.message_created(content_types=['photo'])
async def handle_photo(event):
    chat_id = event.chat_id
    state = user_states.get(chat_id)
    if not state or state.get("step") != "photo":
        return
    
    try:
        # Получаем фото
        photo = event.photo[-1]
        file = await bot.get_file(photo.file_id)
        content = await bot.download_file(file.file_path)
        import base64
        b64 = base64.b64encode(content).decode('utf-8')
        state["photo_base64"] = f"data:image/jpeg;base64,{b64}"
        await send_report(chat_id, state)
    except Exception as e:
        logging.error(f"Ошибка фото: {e}")
        await send_message(chat_id, "❌ Не удалось загрузить фото. Попробуй еще раз.")

async def send_report(chat_id, state):
    report = {
        "max_user_id": str(chat_id),
        "azs_id": state["azs_id"],
        "operator_name": "Оператор",
        "fuel_status": state.get("fuel_status", {}),
        "queue_length": state.get("queue", 0),
        "prices": {},
        "remaining": {},
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

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
