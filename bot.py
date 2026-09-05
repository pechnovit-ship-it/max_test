import asyncio
import aiohttp
import logging

MAX_BOT_TOKEN = "f9LHodD0cOIv3pssaR8kV9WyEVMdYmHoyXHjxLnQtCSRcENWj-6f9ZhyxsQC6qK8F7qOSqpCgIwTkRN8q9NM"
API_URL = "https://api.max.ru/bot"

logging.basicConfig(level=logging.INFO)

async def send_message(chat_id, text):
    url = f"{API_URL}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text
    }
    headers = {"Authorization": f"Bearer {MAX_BOT_TOKEN}"}
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=headers) as resp:
            if resp.status == 200:
                logging.info(f"Сообщение отправлено в chat_id={chat_id}")
            else:
                logging.error(f"Ошибка: {resp.status} {await resp.text()}")

async def main():
    # Здесь мы не можем получить chat_id из событий, потому что это чистый HTTP
    # Для теста отправляем сообщение в твой чат (ID из логов)
    await send_message("512925955", "✅ Бот работает через HTTP API!")

if __name__ == "__main__":
    asyncio.run(main())
