import asyncio
import logging
from maxapi import Bot, Dispatcher
from maxapi.filters.command import CommandStart
from maxapi.types import MessageCreated

MAX_BOT_TOKEN = "f9LHodD0cOIv3pssaR8kV9WyEVMdYmHoyXHjxLnQtCSRcENWj-6f9ZhyxsQC6qK8F7qOSqpCgIwTkRN8q9NM"
logging.basicConfig(level=logging.INFO)
bot = Bot(token=MAX_BOT_TOKEN)
dp = Dispatcher()

# Жёстко задаём chat_id из логов
MY_CHAT_ID = "512925955"

@dp.message_created(CommandStart())
async def cmd_start(event: MessageCreated):
    # Отправляем сообщение в жёстко заданный chat_id
    try:
        await bot.send_message(chat_id=MY_CHAT_ID, text="✅ Бот работает! Твой chat_id: 512925955")
        logging.info("Сообщение отправлено в MY_CHAT_ID")
    except Exception as e:
        logging.error(f"Ошибка: {e}")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
