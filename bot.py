import asyncio
import logging
from maxapi import Bot, Dispatcher, F
from maxapi.filters.command import CommandStart
from maxapi.types import MessageCreated

MAX_BOT_TOKEN = "f9LHodD0cOIv3pssaR8kV9WyEVMdYmHoyXHjxLnQtCSRcENWj-6f9ZhyxsQC6qK8F7qOSqpCgIwTkRN8q9NM"
logging.basicConfig(level=logging.INFO)
bot = Bot(token=MAX_BOT_TOKEN)
dp = Dispatcher()

@dp.message_created(CommandStart())
async def cmd_start(event: MessageCreated):
    # Попробуем разные варианты
    try:
        user_id = event.sender.id
        await event.answer(f"✅ ID через sender.id: {user_id}")
    except:
        pass
    try:
        user_id = event.message.sender.id
        await event.answer(f"✅ ID через message.sender.id: {user_id}")
    except:
        pass
    try:
        user_id = event.user_id
        await event.answer(f"✅ ID через user_id: {user_id}")
    except:
        pass
    try:
        user_id = event.message.user_id
        await event.answer(f"✅ ID через message.user_id: {user_id}")
    except:
        pass

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
