import os
import base64
import aiohttp
import logging
from maxapi import Dispatcher, Bot, F
from maxapi.types import MessageCreated

# --- КОНФИГУРАЦИЯ ---
# Ключ теперь подтягивается из переменных окружения (Cloudflare Worker: MAX_BOT_SECRET)
BOT_SECRET = os.environ.get("MAX_BOT_SECRET")
if not BOT_SECRET:
    raise ValueError("❌ Ошибка: переменная окружения MAX_BOT_SECRET не найдена!")

# URL вашего Cloudflare Worker (куда бот будет слать отчёты)
REPORT_WORKER_URL = "https://data-reporting-via-a-bot.pechnovit.workers.dev"

# Инициализация
bot = Bot(secret=BOT_SECRET)
dp = Dispatcher(bot)

# Простой словарь для хранения состояний пользователей (в продакшене лучше использовать Redis/DB)
user_sessions = {}

def get_session(user_id: str):
    if user_id not in user_sessions:
        user_sessions[user_id] = {
            "state": "start",
            "report": {
                "fuel_98": None,
                "fuel_100": None,
                "dt": None,
                "gas": None,
                "queue": None,
                "photo_base64": None,
                "note": None
            }
        }
    return user_sessions[user_id]

async def send_message(chat_id: int, text: str):
    """Вспомогательная функция для отправки сообщений"""
    await bot.send_message(chat_id=chat_id, text=text)

async def ask_note(chat_id: int, user_id: str):
    """Запрос примечания после фото"""
    await send_message(chat_id, "📝 Добавь примечание (или напиши 'пропустить'):")
    session = get_session(user_id)
    session["state"] = "awaiting_note"

# --- ХЕНДЛЕРЫ ---

@dp.message_created(F.message.text == "/start")
async def cmd_start(event: MessageCreated):
    chat_id = event.message.recipient.chat_id
    user_id = str(chat_id)
    
    session = get_session(user_id)
    session["state"] = "awaiting_fuel_98"
    
    await send_message(chat_id, "⛽ Привет! Начнём с отчёта. Статус для АИ-98: available. Введите остаток в % (число):")

@dp.message_created(F.message.text.regexp(r"^\d+\$"))
async def handle_numbers(event: MessageCreated):
    chat_id = event.message.recipient.chat_id
    user_id = str(chat_id)
    text = event.message.text
    value = int(text)
    session = get_session(user_id)
    state = session["state"]

    # Логика заполнения данных по шагам
    if state == "awaiting_fuel_98":
        session["report"]["fuel_98"] = value
        session["state"] = "awaiting_fuel_100"
        await send_message(chat_id, "⛽ Статус для АИ-100: available. Введите остаток в % (число):")
    
    elif state == "awaiting_fuel_100":
        session["report"]["fuel_100"] = value
        session["state"] = "awaiting_dt"
        await send_message(chat_id, "⛽ Статус для ДТ: available. Введите остаток в % (число):")

    elif state == "awaiting_dt":
        session["report"]["dt"] = value
        session["state"] = "awaiting_gas"
        await send_message(chat_id, "⛽ Статус для ГАЗ: available. Введите остаток в % (число):")

    elif state == "awaiting_gas":
        session["report"]["gas"] = value
        session["state"] = "awaiting_queue"
        await send_message(chat_id, "🚗 Количество машин в очереди (число):")

    elif state == "awaiting_queue":
        session["report"]["queue"] = value
        session["state"] = "awaiting_photo"
        await send_message(chat_id, "📸 Теперь отправь фото АЗС (или нажми «Пропустить»):")

    elif state == "awaiting_note":
        session["report"]["note"] = text
        await finalize_report(chat_id, user_id)

@dp.message_created(F.message.text.regexp(r"пропустить|skip", flags=re.IGNORECASE))
async def handle_skip(event: MessageCreated):
    chat_id = event.message.recipient.chat_id
    user_id = str(chat_id)
    session = get_session(user_id)
    
    if session["state"] == "awaiting_photo":
        session["report"]["photo_base64"] = None
        await ask_note(chat_id, user_id)
    elif session["state"] == "awaiting_note":
        session["report"]["note"] = "Пропущено"
        await finalize_report(chat_id, user_id)

# --- ИСПРАВЛЕННЫЙ ХЕНДЛЕР ДЛЯ ФОТО ---
# Ключевое изменение: F.message.body.attachments вместо несуществующего F.message.attachments
@dp.message_created(F.message.body.attachments)
async def handle_attachments(event: MessageCreated):
    chat_id = event.message.recipient.chat_id
    user_id = str(chat_id)
    session = get_session(user_id)
    
    # Обрабатываем фото только если бот ждёт именно фото
    if session.get("state") != "awaiting_photo":
        return

    attachments = event.message.body.attachments
    
    for attachment in attachments:
        if attachment.type == "image":
            try:
                image_url = attachment.payload.url
                if image_url:
                    async with aiohttp.ClientSession() as http_session:
                        async with http_session.get(image_url) as resp:
                            if resp.status == 200:
                                img_data = await resp.read()
                                img_b64 = base64.b64encode(img_data).decode("utf-8")
                                session["report"]["photo_base64"] = f"data:image/jpeg;base64,{img_b64}"
                                await send_message(chat_id, "✅ Фото получено!")
                                await ask_note(chat_id, user_id)
                                return
                    await send_message(chat_id, "❌ Не удалось загрузить фото. Попробуйте ещё раз или напишите 'пропустить'.")
            except Exception as e:
                logging.error(f"❌ Ошибка загрузки фото: {e}")
                await send_message(chat_id, "❌ Ошибка обработки фото. Попробуйте ещё раз или напишите 'пропустить'.")
            return

    await send_message(chat_id, "❌ Это не похоже на фото. Отправьте изображение или напишите 'пропустить'.")

async def finalize_report(chat_id: int, user_id: str):
    """Отправка данных на Cloudflare Worker"""
    session = get_session(user_id)
    report_data = session["report"]
    
    # Формируем payload для отправки на ваш воркер
    payload = {
        "chat_id": chat_id,
        "user_id": user_id,
        "data": report_data
    }
    
    try:
        async with aiohttp.ClientSession() as session_http:
            async with session_http.post(REPORT_WORKER_URL, json=payload) as resp:
                if resp.status == 200:
                    await send_message(chat_id, "💾 Отчёт успешно отправлен на сервер!")
                else:
                    await send_message(chat_id, f"⚠️ Ошибка сервера: {resp.status}")
    except Exception as e:
        logging.error(f"❌ Ошибка отправки отчёта: {e}")
        await send_message(chat_id, "❌ Не удалось отправить отчёт. Проверьте соединение.")
    
    # Сброс состояния
    session["state"] = "start"

# --- ЗАПУСК ---
if __name__ == "__main__":
    import re
    logging.basicConfig(level=logging.INFO)
    dp.run_polling()
