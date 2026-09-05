import os
import time
import json
import logging
from collections import deque

import requests

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("max-bot")

# ===== КОНФИГ =====
API = "https://platform-api.max.ru"
TOKEN = "f9LHodD0cOIv3pssaR8kV9WyEVMdYmHoyXHjxLnQtCSRcENWj-6f9ZhyxsQC6qK8F7qOSqpCgIwTkRN8q9NM"
HEADERS = {"Authorization": TOKEN, "Content-Type": "application/json"}

_SEEN = deque(maxlen=200)

# ===== ФУНКЦИЯ ОТПРАВКИ =====
def send_message(chat_id: int, text: str, keyboard=None):
    """Отправка сообщения по схеме из документации MAX"""
    url = f"{API}/messages"
    
    # Правильная структура для MAX
    payload = {
        "recipient": {
            "chat_id": chat_id
        },
        "message": {
            "text": text
        }
    }
    
    if keyboard:
        payload["message"]["inline_keyboard"] = keyboard
    
    try:
        resp = requests.post(url, headers=HEADERS, json=payload, timeout=10)
        
        if resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After", 5))
            log.warning(f"429, ждём {wait} сек")
            time.sleep(wait)
            return send_message(chat_id, text, keyboard)
        
        if resp.ok:
            log.info(f"✅ Отправлено: {text[:50]}...")
        else:
            log.error(f"❌ Ошибка: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        log.error(f"❌ Ошибка отправки: {e}")

def answer_callback(callback_id: str, notification: str = "Готово"):
    if not callback_id:
        return
    try:
        resp = requests.post(
            f"{API}/answers",
            headers=HEADERS,
            params={"callback_id": callback_id},
            json={"notification": notification},
            timeout=10,
        )
        if not resp.ok:
            log.error(f"❌ Ошибка callback: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        log.error(f"❌ Ошибка callback: {e}")

# ===== ОБРАБОТКА СОБЫТИЙ =====
def handle_update(update):
    ut = update.get("update_type")
    
    if ut == "message_created":
        msg = update.get("message", {})
        recipient = msg.get("recipient", {})
        chat_id = recipient.get("chat_id")
        body = msg.get("body", {})
        text = (body.get("text") or "").strip()
        
        if chat_id is None:
            return
        
        if text.startswith("/start"):
            keyboard = {
                "inline_keyboard": [
                    [{"text": "Лукойл №13202", "callback_data": "azs_1"}],
                    [{"text": "Татнефть №16", "callback_data": "azs_2"}],
                    [{"text": "Башнефть Косарева", "callback_data": "azs_3"}]
                ]
            }
            send_message(chat_id, "⛽ Выберите АЗС:", keyboard)
        
        elif text == "/help":
            send_message(chat_id, "Доступно:\n/start - начать\n/help - помощь")
        
        else:
            send_message(chat_id, f"Вы написали: {text}")
    
    elif ut == "message_callback":
        cb = update.get("callback", {})
        callback_id = cb.get("callback_id")
        payload = cb.get("payload", "")
        msg = cb.get("message", {})
        recipient = msg.get("recipient", {})
        chat_id = recipient.get("chat_id")
        
        answer_callback(callback_id, "Принято")
        
        if chat_id is not None:
            send_message(chat_id, f"Вы выбрали: {payload}")
    
    elif ut == "bot_added":
        log.info(f"Бот добавлен в чат: {update.get('chat_id')}")

# ===== ПОЛЛИНГ =====
def poll_updates(marker=None, timeout=30):
    params = {"timeout": timeout, "limit": 100}
    if marker:
        params["marker"] = marker
    resp = requests.get(f"{API}/updates", headers=HEADERS, params=params, timeout=timeout + 10)
    resp.raise_for_status()
    return resp.json()

def main():
    marker = None
    log.info("🚀 Бот запущен (long polling)")
    while True:
        try:
            data = poll_updates(marker)
            for update in data.get("updates", []):
                handle_update(update)
            if data.get("marker"):
                marker = data["marker"]
        except requests.RequestException as e:
            log.warning(f"Сеть/API: {e} — пауза 5 сек")
            time.sleep(5)
        except Exception as e:
            log.error(f"Ошибка: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
