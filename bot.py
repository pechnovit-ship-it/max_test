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

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
def get_chat_id(event):
    """Получить chat_id из события"""
    if hasattr(event, 'chat_id'):
        return event.chat_id
    if hasattr(event, 'message') and hasattr(event.message, 'chat'):
        return event.message.chat.id
    if hasattr(event, 'message') and hasattr(event.message, 'chat_id'):
        return event.message.chat_id
    if hasattr(event, 'chat') and hasattr(event.chat, 'id'):
        return event.chat.id
    if hasattr(event, 'sender') and hasattr(event.sender, 'id'):
        return event.sender.id
    return None

async def send_message(chat_id, text, keyboard=None):
    """Отправка сообщения с поддержкой клавиатуры"""
    try:
        if keyboard:
            await bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard)
        else:
            await bot.send_message(chat_id=chat_id, text=text)
        logging.info(f"✅ Отправлено: {text[:50]}...")
        return True
    except Exception as e:
        logging.error(f"❌ Ошибка отправки: {e}")
        return False

async def send_report_to_app(chat_id, state):
    """Отправка отчёта в приложение"""
    report = {
        "max_user_id": str(chat_id),
        "azs_id": state["azs_id"],
        "operator_name": "Оператор",
        "fuel_status": state.get("fuel_status", {}),
        "queue_length": state.get("queue", 0),
        "prices": state.get("prices", {}),
        "remaining": state.get("remaining", {}),
        "note": state.get("note", ""),
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
        return True
    except Exception as e:
        logging.error(f"Ошибка отправки: {e}")
        await send_message(chat_id, "❌ Не удалось отправить отчёт.")
        return False

# --- ОСНОВНЫЕ ОБРАБОТЧИКИ ---
@dp.bot_started()
async def start(event: BotStarted):
    chat_id = get_chat_id(event)
    if chat_id:
        await send_message(chat_id, "👋 Привет! Я бот для сбора отчётов с АЗС.\nНапиши /start")

@dp.message_created(CommandStart())
async def cmd_start(event: MessageCreated):
    chat_id = get_chat_id(event)
    if not chat_id:
        logging.error("Не удалось определить chat_id")
        return
    
    # Сбрасываем состояние
    if chat_id in user_states:
        del user_states[chat_id]
    
    user_states[chat_id] = {"step": "azs"}
    
    # Клавиатура с АЗС
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

# --- ВЫБОР АЗС (callback) ---
@dp.callback_query()
async def handle_callback(event):
    chat_id = get_chat_id(event)
    if not chat_id:
        return
    
    data = event.data
    if data.startswith("azs_"):
        azs_id = int(data.split("_")[1])
        state = user_states.get(chat_id)
        if not state:
            await send_message(chat_id, "❌ Ошибка. Начните с /start")
            return
        
        state["azs_id"] = azs_id
        state["step"] = "fuel"
        state["fuel_status"] = {}
        state["prices"] = {}
        state["remaining"] = {}
        state["fuel_index"] = 0
        
        # Начинаем опрос по топливу
        await ask_fuel(chat_id, state)
        await bot.answer_callback_query(event.id, "АЗС выбрана ✅")

# --- ОПРОС ПО ТОПЛИВУ ---
async def ask_fuel(chat_id, state):
    idx = state.get("fuel_index", 0)
    if idx >= len(FUEL_TYPES):
        state["step"] = "queue"
        await send_message(chat_id, "🚗 Введите количество машин в очереди (число):")
        return
    
    fuel = FUEL_TYPES[idx]
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "✅ Есть", "callback_data": f"fuel_{fuel}_available"},
                {"text": "❌ Нет", "callback_data": f"fuel_{fuel}_unavailable"},
                {"text": "⛔ Слив", "callback_data": f"fuel_{fuel}_refueling"}
            ],
            [
                {"text": "⏭️ Пропустить", "callback_data": f"fuel_{fuel}_skip"}
            ]
        ]
    }
    await send_message(
        chat_id,
        f"📊 Статус для {FUEL_NAMES[fuel]}:",
        keyboard
    )
    state["current_fuel"] = fuel

# --- ОБРАБОТКА СТАТУСА ТОПЛИВА (callback) ---
@dp.callback_query()
async def handle_fuel_callback(event):
    chat_id = get_chat_id(event)
    if not chat_id:
        return
    
    data = event.data
    if not data.startswith("fuel_"):
        return
    
    parts = data.split("_")
    fuel = parts[1]
    action = parts[2] if len(parts) > 2 else None
    
    state = user_states.get(chat_id)
    if not state:
        await send_message(chat_id, "❌ Ошибка. Начните с /start")
        return
    
    if action == "skip":
        state["fuel_index"] += 1
        await ask_fuel(chat_id, state)
        await bot.answer_callback_query(event.id, "Пропущено ⏭️")
        return
    
    # Сохраняем статус
    state["fuel_status"][fuel] = action
    await send_message(chat_id, f"✅ {FUEL_NAMES[fuel]} = {action}")
    
    # Спрашиваем остаток и цену
    state["step"] = "remaining"
    state["current_fuel"] = fuel
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "📊 Указать остаток", "callback_data": f"remaining_{fuel}"},
                {"text": "💰 Указать цену", "callback_data": f"price_{fuel}"}
            ],
            [
                {"text": "⏭️ Пропустить оба", "callback_data": f"skip_extra_{fuel}"}
            ]
        ]
    }
    await send_message(
        chat_id,
        f"📊 Хотите добавить остаток или цену для {FUEL_NAMES[fuel]}?",
        keyboard
    )
    await bot.answer_callback_query(event.id, f"{FUEL_NAMES[fuel]} = {action}")

# --- ОСТАТОК (callback) ---
@dp.callback_query()
async def handle_remaining_callback(event):
    chat_id = get_chat_id(event)
    if not chat_id:
        return
    
    data = event.data
    if data.startswith("remaining_"):
        fuel = data.split("_")[1]
        state = user_states.get(chat_id)
        if not state:
            return
        state["step"] = "remaining_input"
        state["current_fuel"] = fuel
        await send_message(chat_id, f"📊 Введите остаток для {FUEL_NAMES[fuel]} в % (0-100):")
        await bot.answer_callback_query(event.id, "Введите число")

# --- ЦЕНА (callback) ---
@dp.callback_query()
async def handle_price_callback(event):
    chat_id = get_chat_id(event)
    if not chat_id:
        return
    
    data = event.data
    if data.startswith("price_"):
        fuel = data.split("_")[1]
        state = user_states.get(chat_id)
        if not state:
            return
        state["step"] = "price_input"
        state["current_fuel"] = fuel
        await send_message(chat_id, f"💰 Введите цену для {FUEL_NAMES[fuel]} (например, 52.50):")
        await bot.answer_callback_query(event.id, "Введите число")

# --- ПРОПУСК ОСТАТКА/ЦЕНЫ ---
@dp.callback_query()
async def handle_skip_extra(event):
    chat_id = get_chat_id(event)
    if not chat_id:
        return
    
    data = event.data
    if data.startswith("skip_extra_"):
        state = user_states.get(chat_id)
        if not state:
            return
        state["fuel_index"] += 1
        state["step"] = "fuel"
        await ask_fuel(chat_id, state)
        await bot.answer_callback_query(event.id, "Пропущено ⏭️")

# --- ОБРАБОТКА ТЕКСТОВЫХ ВВОДОВ (остаток, цена, очередь) ---
@dp.message_created(F.message.body.text)
async def handle_text_input(event: MessageCreated):
    chat_id = get_chat_id(event)
    if not chat_id:
        return
    
    text = event.message.body.text.strip()
    state = user_states.get(chat_id)
    if not state:
        await send_message(chat_id, "Напишите /start, чтобы начать")
        return
    
    # --- ОСТАТОК (текстовый ввод) ---
    if state.get("step") == "remaining_input":
        fuel = state.get("current_fuel")
        try:
            rem = int(text)
            if rem < 0 or rem > 100:
                await send_message(chat_id, "❌ Остаток должен быть от 0 до 100%")
                return
            state["remaining"][fuel] = rem
            await send_message(chat_id, f"✅ Остаток {rem}% сохранён")
            state["fuel_index"] += 1
            state["step"] = "fuel"
            await ask_fuel(chat_id, state)
        except ValueError:
            await send_message(chat_id, "❌ Введите число от 0 до 100")
        return
    
    # --- ЦЕНА (текстовый ввод) ---
    if state.get("step") == "price_input":
        fuel = state.get("current_fuel")
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
            await send_message(chat_id, "❌ Введите число (например, 52.50)")
        return
    
    # --- ОЧЕРЕДЬ (текстовый ввод) ---
    if state.get("step") == "queue":
        try:
            q = int(text)
            if q < 0:
                await send_message(chat_id, "❌ Количество машин не может быть отрицательным")
                return
            state["queue"] = q
            state["step"] = "photo"
            await send_message(chat_id, "📸 Отправьте фото заправки (или напишите 'пропустить')")
        except ValueError:
            await send_message(chat_id, "❌ Введите число машин в очереди")
        return
    
    # --- ПРОПУСК ФОТО ---
    if state.get("step") == "photo" and text.lower() in ["пропустить", "skip", "пропуск", "нет"]:
        await send_report_to_app(chat_id, state)
        if chat_id in user_states:
            del user_states[chat_id]
        return

# --- ОБРАБОТКА ФОТО ---
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
        file = await bot.get_file(photo.file_id)
        content = await bot.download_file(file.file_path)
        b64 = base64.b64encode(content).decode('utf-8')
        state["photo_base64"] = f"data:image/jpeg;base64,{b64}"
        await send_report_to_app(chat_id, state)
        if chat_id in user_states:
            del user_states[chat_id]
    except Exception as e:
        logging.error(f"Ошибка фото: {e}")
        await send_message(chat_id, "❌ Не удалось загрузить фото. Попробуйте еще раз.")

# --- ЗАПУСК ---
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
