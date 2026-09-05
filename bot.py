import asyncio
import logging
import os
import json
import aiohttp
import base64
from io import BytesIO
from maxapi import Bot, Dispatcher, F
from maxapi.filters.command import CommandStart
from maxapi.types import BotStarted, MessageCreated, CallbackQuery

# --- КОНФИГУРАЦИЯ ---
MAX_BOT_TOKEN = "f9LHodD0cOIv3pssaR8kV9WyEVMdYmHoyXHjxLnQtCSRcENWj-6f9ZhyxsQC6qK8F7qOSqpCgIwTkRN8q9NM"
BOT_SECRET = "F7kL9mN2pQ5rS8tU1vW3xY4zA6bC0dE9"
APP_URL = "https://data-reporting-via-a-bot.pechnovit.workers.dev"
WEBHOOK_URL = f"{APP_URL}/api/public/bot/report"
REGISTER_URL = f"{APP_URL}/api/public/bot/register-operator"

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=MAX_BOT_TOKEN)
dp = Dispatcher()

# --- СПИСОК ВИДОВ ТОПЛИВА ---
FUEL_TYPES = ["92", "95", "98", "100", "dt", "gas"]
FUEL_NAMES = {
    "92": "АИ-92",
    "95": "АИ-95",
    "98": "АИ-98",
    "100": "АИ-100",
    "dt": "ДТ",
    "gas": "ГАЗ"
}
FUEL_EMOJIS = {
    "92": "⛽",
    "95": "⛽",
    "98": "⛽",
    "100": "⛽",
    "dt": "🛢️",
    "gas": "🔥"
}

# --- ВРЕМЕННОЕ ХРАНИЛИЩЕ ДАННЫХ ОПЕРАТОРОВ ---
user_states = {}

# --- АЗС (для теста, потом заменишь на запрос к БД) ---
AZS_LIST = [
    {"id": 1, "name": "Лукойл №13202", "address": "ул. Волгоградская, 48"},
    {"id": 2, "name": "Татнефть №16", "address": "ул. Лодыгина, 17Б"},
    {"id": 3, "name": "Башнефть Косарева", "address": "ул. Косарева, 128а"},
]

# --- ФУНКЦИЯ РЕГИСТРАЦИИ ОПЕРАТОРА ---
async def register_operator(user_id: str, name: str):
    """Регистрирует оператора в приложении"""
    try:
        async with aiohttp.ClientSession() as session:
            payload = {"max_user_id": user_id, "name": name}
            async with session.post(REGISTER_URL, json=payload) as resp:
                if resp.status == 200:
                    logger.info(f"Оператор {name} ({user_id}) зарегистрирован")
                    return True
                else:
                    logger.error(f"Ошибка регистрации: {resp.status}")
                    return False
    except Exception as e:
        logger.error(f"Ошибка регистрации: {e}")
        return False

# --- ОБРАБОТЧИК /START ---
@dp.bot_started()
async def start(event: BotStarted):
    await event.answer("👋 Привет! Я бот для сбора отчетов с АЗС.\nНапиши /start, чтобы начать.")

@dp.message_created(CommandStart())
async def cmd_start(event: MessageCreated):
    user_id = str(event.message.from_user.id)
    name = event.message.from_user.first_name or "Оператор"
    
    # Регистрируем оператора
    await register_operator(user_id, name)
    
    # Выбираем АЗС
    keyboard = {
        "inline_keyboard": [
            [{"text": f"{azs['name']} ({azs['address']})", "callback_data": f"azs_{azs['id']}"}]
            for azs in AZS_LIST
        ]
    }
    await event.answer("⛽ Выберите АЗС для отчета:", reply_markup=keyboard)

# --- ОБРАБОТКА ВЫБОРА АЗС ---
@dp.callback_query(F.data.startswith("azs_"))
async def select_azs(callback: CallbackQuery):
    user_id = str(callback.from_user.id)
    azs_id = int(callback.data.split("_")[1])
    
    user_states[user_id] = {
        "azs_id": azs_id,
        "fuel_status": {},
        "prices": {},
        "remaining": {},
        "step": "fuel_status"
    }
    
    # Клавиатура выбора топлива
    fuel_keyboard = {
        "inline_keyboard": [
            [{"text": f"{FUEL_EMOJIS[f]} {FUEL_NAMES[f]}", "callback_data": f"fuel_{f}"}]
            for f in FUEL_TYPES
        ] + [
            [{"text": "✅ Готово", "callback_data": "fuel_done"}]
        ]
    }
    await callback.message.answer("📊 Выберите вид топлива для отметки:", reply_markup=fuel_keyboard)
    await callback.answer()

# --- ОБРАБОТКА ВЫБОРА ТОПЛИВА ---
@dp.callback_query(F.data.startswith("fuel_"))
async def select_fuel(callback: CallbackQuery):
    user_id = str(callback.from_user.id)
    data = callback.data
    
    if data == "fuel_done":
        # Проверяем, заполнены ли все виды
        state = user_states.get(user_id)
        if not state:
            await callback.message.answer("❌ Ошибка. Начните заново с /start")
            await callback.answer()
            return
            
        # Переходим к очереди
        state["step"] = "queue"
        await callback.message.answer("🚗 Введите количество машин в очереди (число):")
        await callback.answer()
        return
    
    fuel_type = data.split("_")[1]
    state = user_states.get(user_id)
    if not state:
        await callback.message.answer("❌ Ошибка. Начните заново с /start")
        await callback.answer()
        return
    
    # Статус топлива
    status_keyboard = {
        "inline_keyboard": [
            [
                {"text": "✅ Есть", "callback_data": f"status_{fuel_type}_available"},
                {"text": "❌ Нет", "callback_data": f"status_{fuel_type}_unavailable"},
                {"text": "⛔ Слив", "callback_data": f"status_{fuel_type}_refueling"}
            ]
        ]
    }
    await callback.message.answer(
        f"{FUEL_EMOJIS[fuel_type]} Статус для {FUEL_NAMES[fuel_type]}:",
        reply_markup=status_keyboard
    )
    await callback.answer()

# --- ОБРАБОТКА СТАТУСА ТОПЛИВА ---
@dp.callback_query(F.data.startswith("status_"))
async def set_status(callback: CallbackQuery):
    user_id = str(callback.from_user.id)
    parts = callback.data.split("_")
    fuel_type = parts[1]
    status = parts[2]
    
    state = user_states.get(user_id)
    if not state:
        await callback.message.answer("❌ Ошибка. Начните заново с /start")
        await callback.answer()
        return
    
    state["fuel_status"][fuel_type] = status
    
    # Дополнительные параметры для топлива
    extra_keyboard = {
        "inline_keyboard": [
            [
                {"text": "📊 Указать остаток", "callback_data": f"remaining_{fuel_type}"},
                {"text": "💰 Указать цену", "callback_data": f"price_{fuel_type}"}
            ],
            [
                {"text": "⏭️ Пропустить", "callback_data": f"skip_{fuel_type}"}
            ]
        ]
    }
    await callback.message.answer(
        f"✅ {FUEL_NAMES[fuel_type]} = {status}\nЧто хотите добавить?",
        reply_markup=extra_keyboard
    )
    await callback.answer()

# --- ОБРАБОТКА ОСТАТКА ---
@dp.callback_query(F.data.startswith("remaining_"))
async def set_remaining(callback: CallbackQuery):
    user_id = str(callback.from_user.id)
    fuel_type = callback.data.split("_")[1]
    state = user_states.get(user_id)
    if not state:
        await callback.message.answer("❌ Ошибка. Начните заново с /start")
        await callback.answer()
        return
    
    state["step"] = "remaining"
    state["remaining_fuel"] = fuel_type
    await callback.message.answer(f"📊 Введите остаток для {FUEL_NAMES[fuel_type]} в процентах (например, 15):")
    await callback.answer()

# --- ОБРАБОТКА ЦЕНЫ ---
@dp.callback_query(F.data.startswith("price_"))
async def set_price(callback: CallbackQuery):
    user_id = str(callback.from_user.id)
    fuel_type = callback.data.split("_")[1]
    state = user_states.get(user_id)
    if not state:
        await callback.message.answer("❌ Ошибка. Начните заново с /start")
        await callback.answer()
        return
    
    state["step"] = "price"
    state["price_fuel"] = fuel_type
    await callback.message.answer(f"💰 Введите цену для {FUEL_NAMES[fuel_type]} (например, 52.50):")
    await callback.answer()

# --- ОБРАБОТКА ПРОПУСКА ---
@dp.callback_query(F.data.startswith("skip_"))
async def skip(callback: CallbackQuery):
    await callback.message.answer("⏭️ Пропущено. Выберите следующее топливо или нажмите 'Готово'")
    await callback.answer()

# --- ОБРАБОТКА ТЕКСТОВЫХ СООБЩЕНИЙ ---
@dp.message_created(F.message.body.text)
async def handle_text(event: MessageCreated):
    user_id = str(event.message.from_user.id)
    text = event.message.body.text.strip()
    state = user_states.get(user_id)
    
    if not state:
        await event.answer("Напишите /start для начала")
        return
    
    # Обработка остатка
    if state.get("step") == "remaining":
        try:
            remaining = int(text)
            if remaining < 0 or remaining > 100:
                await event.answer("❌ Остаток должен быть от 0 до 100%")
                return
            fuel_type = state["remaining_fuel"]
            state["remaining"][fuel_type] = remaining
            state["step"] = "fuel_status"
            await event.answer(f"✅ Остаток {remaining}% для {FUEL_NAMES[fuel_type]} сохранен.")
        except ValueError:
            await event.answer("❌ Введите число от 0 до 100")
        return
    
    # Обработка цены
    if state.get("step") == "price":
        try:
            price = float(text)
            if price < 0:
                await event.answer("❌ Цена не может быть отрицательной")
                return
            fuel_type = state["price_fuel"]
            state["prices"][fuel_type] = price
            state["step"] = "fuel_status"
            await event.answer(f"✅ Цена {price} руб для {FUEL_NAMES[fuel_type]} сохранена.")
        except ValueError:
            await event.answer("❌ Введите число (например, 52.50)")
        return
    
    # Обработка очереди
    if state.get("step") == "queue":
        try:
            queue = int(text)
            if queue < 0:
                await event.answer("❌ Количество машин не может быть отрицательным")
                return
            state["queue"] = queue
            state["step"] = "photo"
            await event.answer("📸 Отправьте фото заправки (или нажмите 'Пропустить'):")
        except ValueError:
            await event.answer("❌ Введите число машин в очереди")
        return

# --- ОБРАБОТКА ФОТО ---
@dp.message_created(F.message.body.photo)
async def handle_photo(event: MessageCreated):
    user_id = str(event.message.from_user.id)
    state = user_states.get(user_id)
    
    if not state or state.get("step") != "photo":
        await event.answer("📸 Я жду фото отчета. Если не хотите отправлять фото, напишите 'пропустить'")
        return
    
    try:
        # Получаем фото
        photo = event.message.body.photo
        file_id = photo.file_id
        file = await bot.get_file(file_id)
        file_content = await bot.download_file(file.file_path)
        
        # Конвертируем в base64
        base64_photo = base64.b64encode(file_content).decode('utf-8')
        mime_type = photo.mime_type or "image/jpeg"
        state["photo_base64"] = f"data:{mime_type};base64,{base64_photo}"
        
        # Отправляем отчет
        await send_report(event, state)
        
    except Exception as e:
        logger.error(f"Ошибка загрузки фото: {e}")
        await event.answer("❌ Не удалось загрузить фото. Попробуйте еще раз.")

# --- ОБРАБОТКА ПРОПУСКА ФОТО ---
@dp.message_created(F.message.body.text)
async def handle_skip_photo(event: MessageCreated):
    user_id = str(event.message.from_user.id)
    text = event.message.body.text.lower()
    state = user_states.get(user_id)
    
    if not state or state.get("step") != "photo":
        return
    
    if text in ["пропустить", "skip", "нет"]:
        # Отправляем отчет без фото
        await send_report(event, state)

# --- ОТПРАВКА ОТЧЕТА В ПРИЛОЖЕНИЕ ---
async def send_report(event: MessageCreated, state: dict):
    user_id = str(event.message.from_user.id)
    name = event.message.from_user.first_name or "Оператор"
    
    # Формируем полный отчет
    report = {
        "max_user_id": user_id,
        "azs_id": state["azs_id"],
        "operator_name": name,
        "fuel_status": state["fuel_status"],
        "queue_length": state.get("queue", 0),
        "prices": state.get("prices", {}),
        "remaining": state.get("remaining", {}),
        "note": "",
    }
    
    # Добавляем фото (если есть)
    if "photo_base64" in state:
        report["photo_base64"] = state["photo_base64"]
    
    # Отправляем в бэкенд
    headers = {"x-bot-secret": BOT_SECRET}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(WEBHOOK_URL, json=report, headers=headers) as resp:
                if resp.status == 200:
                    await event.answer("✅ Отчет принят! Спасибо за помощь!")
                else:
                    error_text = await resp.text()
                    await event.answer(f"❌ Ошибка отправки: {resp.status}\n{error_text}")
    except Exception as e:
        logger.error(f"Ошибка отправки: {e}")
        await event.answer("❌ Не удалось отправить отчет. Попробуйте позже.")
    
    # Очищаем состояние
    del user_states[user_id]

# --- ЗАПУСК БОТА ---
async def main():
    logger.info("🚀 Бот запускается...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())