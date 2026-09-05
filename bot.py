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

# Жёстко задаём chat_id из логов
MY_CHAT_ID = "512925955"

async def send_message(event, text):
    """Отправка сообщения в известный chat_id"""
    try:
        # Пробуем отправить через bot.send_message
        await bot.send_message(MY_CHAT_ID, text)
        logging.info(f"Отправлено сообщение в chat_id={MY_CHAT_ID}")
    except Exception as e:
        logging.error(f"Ошибка отправки через bot.send_message: {e}")
        # Если не работает — пробуем через прямой API
        try:
            url = "https://api.max.ru/bot/sendMessage"
            payload = {"chat_id": MY_CHAT_ID, "text": text}
            headers = {"Authorization": f"Bearer {MAX_BOT_TOKEN}"}
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, headers=headers) as resp:
                    if resp.status == 200:
                        logging.info(f"Отправлено через прямой API в chat_id={MY_CHAT_ID}")
                    else:
                        logging.error(f"Ошибка прямого API: {resp.status} {await resp.text()}")
        except Exception as e2:
            logging.error(f"Ошибка прямого API: {e2}")

# --- ОБРАБОТЧИКИ ---
@dp.bot_started()
async def start(event: BotStarted):
    await send_message(event, "👋 Привет! Напиши /start")

@dp.message_created(CommandStart())
async def cmd_start(event: MessageCreated):
    await send_message(event, "⛽ Привет! Я бот для сбора отчетов. Сейчас я работаю в тестовом режиме.")
    # Далее можно добавить полную логику, но сначала проверим, что ответ доходит.

# --- ЗАПУСК ---
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
