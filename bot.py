import os
import time
import logging
import requests

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("max-bot")

API = "https://platform-api.max.ru"
TOKEN = "f9LHodD0cOIv3pssaR8kV9WyEVMdYmHoyXHjxLnQtCSRcENWj-6f9ZhyxsQC6qK8F7qOSqpCgIwTkRN8q9NM"
HEADERS = {"Authorization": TOKEN, "Content-Type": "application/json"}

def poll_updates(marker=None, timeout=30):
    params = {"timeout": timeout, "limit": 100}
    if marker:
        params["marker"] = marker
    r = requests.get(f"{API}/updates", headers=HEADERS, params=params, timeout=timeout + 10)
    r.raise_for_status()
    return r.json()

def send_message(chat_id, text):
    """Отправка сообщения в MAX"""
    url = f"{API}/messages"
    payload = {
        "recipient": {
            "chat_id": str(chat_id)
        },
        "message": {
            "text": text
        }
    }
    try:
        resp = requests.post(url, headers=HEADERS, json=payload, timeout=10)
        if resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After", 5))
            log.warning(f"429, ждём {wait} сек")
            time.sleep(wait)
            return send_message(chat_id, text)
        if resp.ok:
            log.info(f"✅ Отправлено: {text[:50]}...")
        else:
            log.error(f"❌ Ошибка: {resp.status_code} {resp.text}")
    except Exception as e:
        log.error(f"❌ Ошибка: {e}")

def handle_update(update):
    ut = update.get("update_type")
    if ut == "message_created":
        msg = update.get("message", {})
        recipient = msg.get("recipient", {})
        chat_id = recipient.get("chat_id")
        body = msg.get("body", {})
        text = body.get("text", "").strip()
        
        if chat_id is None:
            return
        
        log.info(f"Сообщение от {chat_id}: {text}")
        
        if text.startswith("/start"):
            send_message(chat_id, "Привет! Напиши /help")
        elif text == "/help":
            send_message(chat_id, "Доступны команды:\n/start - приветствие\n/help - помощь")
        else:
            send_message(chat_id, f"Вы написали: {text}")

def main():
    marker = None
    log.info("🚀 Бот запущен")
    while True:
        try:
            data = poll_updates(marker)
            for u in data.get("updates", []):
                handle_update(u)
            if data.get("marker"):
                marker = data["marker"]
        except Exception as e:
            log.error(f"Ошибка: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
