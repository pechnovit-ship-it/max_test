import os
import time
import logging
from collections import deque

import requests

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("max-bot")

API = "https://platform-api.max.ru"
TOKEN = "f9LHodD0cOIv3pssaR8kV9WyEVMdYmHoyXHjxLnQtCSRcENWj-6f9ZhyxsQC6qK8F7qOSqpCgIwTkRN8q9NM"
HEADERS = {"Authorization": TOKEN, "Content-Type": "application/json"}

_SEEN = deque(maxlen=200)

def dedup_key(update: dict) -> tuple:
    msg = update.get("message") or {}
    body = msg.get("body") or {}
    mid = body.get("mid")
    cb = update.get("callback") or {}
    return (
        update.get("update_type"),
        update.get("timestamp"),
        mid,
        cb.get("callback_id"),
    )

def poll_updates(marker=None, timeout=30):
    params = {"timeout": timeout, "limit": 100}
    if marker is not None:
        params["marker"] = marker
    r = requests.get(f"{API}/updates", headers=HEADERS, params=params, timeout=timeout + 10)
    r.raise_for_status()
    return r.json()

def send_message_safe(chat_id: int, text: str, retries: int = 3) -> None:
    url = f"{API}/messages"
    body = {"chat_id": chat_id, "text": text}
    for attempt in range(retries):
        resp = requests.post(url, headers=HEADERS, json=body, timeout=20)
        if resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After", 5))
            log.warning("429 messages, ждём %s с (попытка %s)", wait, attempt + 1)
            time.sleep(wait)
            continue
        if not resp.ok:
            log.error("messages: %s %s", resp.status_code, resp.text[:400])
        else:
            break

def answer_callback(callback_id: str, notification: str = "Готово") -> None:
    if not callback_id:
        return
    r = requests.post(
        f"{API}/answers",
        headers=HEADERS,
        params={"callback_id": callback_id},
        json={"notification": notification},
        timeout=15,
    )
    if not r.ok:
        log.error("answers: %s %s", r.status_code, r.text[:300])

def handle_update(update: dict) -> None:
    key = dedup_key(update)
    if key in _SEEN:
        return
    _SEEN.append(key)

    ut = update.get("update_type")

    if ut == "message_created":
        msg = update.get("message") or {}
        recipient = msg.get("recipient") or {}
        chat_id = recipient.get("chat_id")
        body = msg.get("body") or {}
        text = (body.get("text") or "").strip()
        if chat_id is None:
            return

        if text.startswith("/start"):
            parts = text.split(maxsplit=1)
            payload = parts[1] if len(parts) > 1 else ""
            if payload:
                send_message_safe(int(chat_id), f"Старт с параметром: {payload}")
            else:
                send_message_safe(int(chat_id), "Привет! Напишите /help.")
        elif text == "/help":
            send_message_safe(int(chat_id), "Доступно:\n/start [код]\n/help")
        else:
            send_message_safe(int(chat_id), f"Вы написали: {text}")

    elif ut == "message_callback":
        cb = update.get("callback") or {}
        callback_id = cb.get("callback_id")
        payload = cb.get("payload", "")
        msg = cb.get("message") or {}
        recipient = msg.get("recipient") or {}
        chat_id = recipient.get("chat_id")

        answer_callback(callback_id, "Принято")
        if chat_id is not None:
            send_message_safe(int(chat_id), f"Нажата кнопка, payload: {payload}")

    elif ut == "bot_added":
        log.info("Бот добавлен в чат: %s", update.get("chat_id"))

def main():
    marker = None
    log.info("Бот запущен (long polling). Остановка: Ctrl+C")
    while True:
        try:
            data = poll_updates(marker)
            for u in data.get("updates") or []:
                handle_update(u)
            if data.get("marker") is not None:
                marker = data["marker"]
        except requests.RequestException as e:
            log.warning("Сеть/API: %s — пауза 5 с", e)
            time.sleep(5)

if __name__ == "__main__":
    main()
