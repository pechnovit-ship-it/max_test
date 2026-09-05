import asyncio
import logging
from maxapi import Bot, Dispatcher
from maxapi.filters.command import CommandStart
from maxapi.types import MessageCreated

MAX_BOT_TOKEN = "f9LHodD0cOIv3pssaR8kV9WyEVMdYmHoyXHjxLnQtCSRcENWj-6f9ZhyxsQC6qK8F7qOSqpCgIwTkRN8q9NM"
logging.basicConfig(level=logging.INFO)
bot = Bot(token=MAX_BOT_TOKEN)
dp = Dispatcher()

@dp.message_created(CommandStart())
async def cmd_start(event: MessageCreated):
    # Пробуем отправить через bot.send_message
    try:
        chat_id = event.chat_id  # или event.message.chat_id
        await bot.send_message(chat_id=chat_id, text="✅ Бот работает!")
        logging.info("Сообщение отправлено через bot.send_message")
    except Exception as e:
        logging.error(f"Ошибка bot.send_message: {e}")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
