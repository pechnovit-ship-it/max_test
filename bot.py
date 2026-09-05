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

MY_CHAT_ID = "512925955"

async def send_message(text):
    """Отправка сообщения через bot.send_message"""
    try:
        await bot.send_message(MY_CHAT_ID, text)
        logging.info(f"✅ Отправлено: {text[:50]}...")
    except Exception as e:
        logging.error(f"❌ Ошибка bot.send_message: {e}")

# --- ОБРАБОТЧИКИ ---
@dp.bot_started()
async def start(event: BotStarted):
    await send_message("👋 Привет! Напиши /start")

@dp.message_created(CommandStart())
async def cmd_start(event: MessageCreated):
    await send_message("⛽ Привет! Я бот для сбора отчетов. Я работаю!")

# --- ЗАПУСК ---
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
