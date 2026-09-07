"""Бот MAX для «Топливного монитора».

Короткий опрос: АЗС -> статусы топлива одним экраном -> одна строка подробностей ->
фото (не обязательно) -> подтверждение.

Переменные окружения BotHost:
  BOT_TOKEN    — токен бота MAX
  BOT_SECRET   — общий ключ приложения (заголовок x-bot-secret)
  APP_API_URL  — адрес приложения, например https://example.workers.dev
"""

import asyncio
import base64
import logging
import os
import re
import time
from typing import Any

import aiohttp
from maxapi import Bot, Dispatcher, F
from maxapi.filters.command import CommandStart
from maxapi.types import BotStarted, CallbackButton, MessageCallback, MessageCreated
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder


def required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Не задана обязательная переменная {name}")
    return value


BOT_TOKEN = required_env("BOT_TOKEN")
BOT_SECRET = required_env("BOT_SECRET")
APP_API_URL = required_env("APP_API_URL").rstrip("/")
REPORT_URL = f"{APP_API_URL}/api/public/bot/report"
AZS_URL = f"{APP_API_URL}/api/public/bot/azs"

MAX_PHOTO_BYTES = 5 * 1024 * 1024
AZS_PAGE_SIZE = 8
AZS_CACHE_TTL = 300          # список АЗС живёт 5 минут
SESSION_TTL = 30 * 60        # диалог живёт 30 минут
TIMEOUT = aiohttp.ClientTimeout(total=30)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

FUEL_TYPES = [
    ("92", "92"),
    ("95", "95"),
    ("98", "98"),
    ("100", "100"),
    ("dt", "ДТ"),
    ("gas", "ГАЗ"),
]
FUEL_NAMES = dict(FUEL_TYPES)
FUEL_ALIASES = {"дт": "dt", "dt": "dt", "газ": "gas", "gas": "gas", "92": "92", "95": "95", "98": "98", "100": "100"}

STATUS_CYCLE = ["available", "unavailable", "refueling"]
STATUS_MARK = {"available": "✅", "unavailable": "⛔", "refueling": "🚚", None: "—"}
STATUS_LABELS = {"available": "есть", "unavailable": "нет", "refueling": "завоз"}

RESTRICTIONS = {
    "none": "нет",
    "special_only": "только спецтранспорт",
    "limit_20l": "не более 20 л",
    "closed": "закрыта",
}

sessions: dict[str, dict[str, Any]] = {}
_azs_cache: dict[str, Any] = {"rows": [], "at": 0.0}


# ---------------------------------------------------------------- состояние


def new_session(user_id: str) -> dict[str, Any]:
    return {
        "touched": time.time(),
        "state": "azs",
        "azs_id": None,
        "azs_name": None,
        "fuel_status": {},
        "prices": {},
        "remaining_pct": {},
        "remaining_l": {},
        "queue_length": None,
        "restriction_type": "none",
        "note": "",
        "photo_base64": None,
        "user_id": user_id,
    }


def get_session(user_id: str) -> dict[str, Any] | None:
    session = sessions.get(user_id)
    if not session:
        return None
    if time.time() - session["touched"] > SESSION_TTL:
        sessions.pop(user_id, None)
        return None
    session["touched"] = time.time()
    return session


# ---------------------------------------------------------------- транспорт


def keyboard(rows: list[list[tuple[str, str]]]):
    builder = InlineKeyboardBuilder()
    for row in rows:
        builder.row(*[CallbackButton(text=text, payload=payload) for text, payload in row])
    return builder.as_markup()


async def say(chat_id: int, text: str, markup=None) -> None:
    try:
        await bot.send_message(chat_id=chat_id, text=text, attachments=[markup] if markup else None)
    except Exception:
        logger.exception("Не удалось отправить сообщение в MAX")


async def read_json(response: aiohttp.ClientResponse) -> dict[str, Any]:
    try:
        payload = await response.json(content_type=None)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


async def fetch_azs(force: bool = False) -> tuple[list[dict[str, Any]], str | None]:
    if not force and _azs_cache["rows"] and time.time() - _azs_cache["at"] < AZS_CACHE_TTL:
        return _azs_cache["rows"], None
    try:
        async with aiohttp.ClientSession(timeout=TIMEOUT) as http:
            async with http.get(AZS_URL, headers={"x-bot-secret": BOT_SECRET}) as response:
                payload = await read_json(response)
                if response.status != 200 or not payload.get("success"):
                    return [], str(payload.get("error") or f"ошибка сервера {response.status}")
                rows = [r for r in payload.get("azs") or [] if isinstance(r, dict) and r.get("id")]
                if not rows:
                    return [], "в приложении пока нет ни одной АЗС"
                _azs_cache.update(rows=rows, at=time.time())
                return rows, None
    except asyncio.TimeoutError:
        return [], "приложение не ответило за 30 секунд"
    except Exception:
        logger.exception("Не удалось получить список АЗС")
        return [], "нет связи с приложением"


async def post_report(session: dict[str, Any]) -> tuple[bool, str]:
    body = {
        "max_user_id": session["user_id"],
        "operator_name": session.get("operator_name") or "Оператор",
        "azs_id": session["azs_id"],
        "fuel_status": session["fuel_status"],
        "queue_length": session["queue_length"],
        "restriction_type": session["restriction_type"],
        "note": session["note"] or None,
        "photo_base64": session["photo_base64"],
        "prices": session["prices"],
        "remaining_pct": session["remaining_pct"],
        "remaining_l": session["remaining_l"],
    }
    try:
        async with aiohttp.ClientSession(timeout=TIMEOUT) as http:
            async with http.post(REPORT_URL, json=body, headers={"x-bot-secret": BOT_SECRET}) as response:
                payload = await read_json(response)
                if 200 <= response.status < 300 and payload.get("success"):
                    return True, f"отчёт №{payload.get('report_id')} принят"
                if payload.get("details"):
                    logger.error("Отчёт отклонён: %s", payload["details"])
                return False, str(payload.get("error") or f"ошибка сервера {response.status}")
    except asyncio.TimeoutError:
        return False, "приложение не ответило за 30 секунд"
    except Exception:
        logger.exception("Не удалось отправить отчёт")
        return False, "нет связи с приложением"


# ---------------------------------------------------------------- экраны


async def screen_azs(chat_id: int, user_id: str, page: int = 0, query: str = "") -> None:
    rows, error = await fetch_azs()
    if error:
        await say(chat_id, f"Не удалось получить список АЗС: {error}.", keyboard([[("Повторить", "azs_reload")]]))
        return
    session = get_session(user_id) or sessions.setdefault(user_id, new_session(user_id))
    session["state"] = "azs"
    if query:
        rows = [r for r in rows if query.lower() in str(r.get("name", "")).lower()
                or query.lower() in str(r.get("address", "")).lower()]
        if not rows:
            await say(chat_id, "Ничего не нашлось. Введите другую часть названия.")
            return
    session["found"] = [r["id"] for r in rows]
    pages = max(1, (len(rows) + AZS_PAGE_SIZE - 1) // AZS_PAGE_SIZE)
    page = min(max(page, 0), pages - 1)
    session["page"] = page
    session["query"] = query
    buttons = [[(str(r.get("name") or f"АЗС {r['id']}"), f"azs_{r['id']}")]
               for r in rows[page * AZS_PAGE_SIZE:(page + 1) * AZS_PAGE_SIZE]]
    nav = []
    if page > 0:
        nav.append(("‹ Назад", f"azspage_{page - 1}"))
    if page + 1 < pages:
        nav.append(("Далее ›", f"azspage_{page + 1}"))
    if nav:
        buttons.append(nav)
    await say(chat_id, f"Выберите АЗС ({page + 1}/{pages}) или напишите часть названия:", keyboard(buttons))


def fuel_text(session: dict[str, Any]) -> str:
    marks = "  ".join(f"{name} {STATUS_MARK[session['fuel_status'].get(key)]}" for key, name in FUEL_TYPES)
    return (f"{session['azs_name']}\n{marks}\n\n"
            "Нажимайте на топливо: ✅ есть → ⛔ нет → 🚚 завоз. Затем «Готово».")


async def screen_fuel(chat_id: int, user_id: str) -> None:
    session = get_session(user_id)
    if not session:
        await lost(chat_id, user_id)
        return
    session["state"] = "fuel"
    buttons = [
        [(f"{FUEL_NAMES[k]} {STATUS_MARK[session['fuel_status'].get(k)]}", f"f_{k}") for k in ("92", "95", "98")],
        [(f"{FUEL_NAMES[k]} {STATUS_MARK[session['fuel_status'].get(k)]}", f"f_{k}") for k in ("100", "dt", "gas")],
        [("Все есть", "f_all_ok"), ("Готово", "f_done")],
    ]
    await say(chat_id, fuel_text(session), keyboard(buttons))


async def screen_details(chat_id: int, user_id: str) -> None:
    session = get_session(user_id)
    if not session:
        await lost(chat_id, user_id)
        return
    session["state"] = "details"
    await say(
        chat_id,
        "Подробности одной строкой (можно пропустить):\n"
        "например: 95 80% 59.9  дт 40% 1000л  очередь 7  лимит20\n\n"
        "Слова: очередь N, лимит20, спец, закрыта. Остальной текст станет примечанием.",
        keyboard([[("Пропустить", "d_skip")]]),
    )


async def screen_photo(chat_id: int, user_id: str) -> None:
    session = get_session(user_id)
    if not session:
        await lost(chat_id, user_id)
        return
    session["state"] = "photo"
    await say(chat_id, "Пришлите фото (до 5 МБ) или нажмите «Без фото».", keyboard([[("Без фото", "p_skip")]]))


async def screen_summary(chat_id: int, user_id: str) -> None:
    session = get_session(user_id)
    if not session:
        await lost(chat_id, user_id)
        return
    session["state"] = "summary"
    lines = [session["azs_name"] or "АЗС не выбрана"]
    for key, name in FUEL_TYPES:
        status = session["fuel_status"].get(key)
        if not status:
            continue
        extra = []
        if key in session["remaining_pct"]:
            extra.append(f"{session['remaining_pct'][key]}%")
        if key in session["remaining_l"]:
            extra.append(f"{session['remaining_l'][key]} л")
        if key in session["prices"]:
            extra.append(f"{session['prices'][key]} ₽")
        lines.append(f"{name}: {STATUS_LABELS[status]}" + (f" ({', '.join(extra)})" if extra else ""))
    if session["queue_length"] is not None:
        lines.append(f"Очередь: {session['queue_length']}")
    if session["restriction_type"] != "none":
        lines.append(f"Ограничение: {RESTRICTIONS[session['restriction_type']]}")
    if session["note"]:
        lines.append(f"Примечание: {session['note']}")
    lines.append(f"Фото: {'есть' if session['photo_base64'] else 'нет'}")
    await say(chat_id, "\n".join(lines),
              keyboard([[("Отправить", "send"), ("Исправить", "again")], [("Отменить", "cancel")]]))


async def lost(chat_id: int, user_id: str) -> None:
    sessions.pop(user_id, None)
    await say(chat_id, "Сессия сброшена — начнём заново.")
    await screen_azs(chat_id, user_id, 0)


# ---------------------------------------------------------------- разбор строки подробностей


def parse_details(session: dict[str, Any], text: str) -> None:
    rest: list[str] = []
    current: str | None = None
    for token in text.replace(",", " ").split():
        low = token.lower().strip(".:;")
        if low in FUEL_ALIASES:
            current = FUEL_ALIASES[low]
            continue
        if low in ("лимит20", "20л", "лимит"):
            session["restriction_type"] = "limit_20l"
            continue
        if low.startswith("спец"):
            session["restriction_type"] = "special_only"
            continue
        if low.startswith("закрыт"):
            session["restriction_type"] = "closed"
            continue
        if low.startswith("очеред") or low in ("оч", "очередь"):
            session["_await_queue"] = True
            continue
        number = re.fullmatch(r"(\d+(?:[.,]\d+)?)(%|л|l|₽|р|руб)?", low)
        if number:
            value = float(number.group(1).replace(",", "."))
            suffix = number.group(2)
            if session.pop("_await_queue", False):
                session["queue_length"] = max(0, min(1000, int(value)))
                continue
            if current and suffix == "%":
                session["remaining_pct"][current] = max(0, min(100, int(value)))
                continue
            if current and suffix in ("л", "l"):
                session["remaining_l"][current] = max(0, min(1_000_000, int(value)))
                continue
            if current:
                session["prices"][current] = max(0.0, min(500.0, round(value, 2)))
                continue
        rest.append(token)
    session.pop("_await_queue", None)
    note = " ".join(rest).strip()
    if note:
        session["note"] = note[:200]


# ---------------------------------------------------------------- обработчики


@dp.bot_started()
async def on_start(event: BotStarted):
    await say(event.chat_id, "Здравствуйте! Напишите /start, чтобы отправить отчёт.")


@dp.message_created(CommandStart())
async def cmd_start(event: MessageCreated):
    chat_id = event.message.recipient.chat_id
    user_id = str(chat_id)
    sessions[user_id] = new_session(user_id)
    await screen_azs(chat_id, user_id, 0)


@dp.message_created(F.message.body.attachments)
async def on_attachment(event: MessageCreated):
    chat_id = event.message.recipient.chat_id
    user_id = str(chat_id)
    session = get_session(user_id)
    if not session or session["state"] != "photo":
        return
    for attachment in event.message.body.attachments:
        if attachment.type != "image":
            continue
        try:
            url = attachment.payload.url
            if not url:
                raise ValueError("MAX не вернул адрес изображения")
            async with aiohttp.ClientSession(timeout=TIMEOUT) as http:
                async with http.get(url) as response:
                    if response.status != 200:
                        raise ValueError(f"MAX вернул статус {response.status}")
                    image = await response.content.read(MAX_PHOTO_BYTES + 1)
            if len(image) > MAX_PHOTO_BYTES:
                await say(chat_id, "Фото больше 5 МБ — пришлите поменьше.")
                return
            if image.startswith(b"\xff\xd8\xff"):
                mime = "image/jpeg"
            elif image.startswith(b"\x89PNG\r\n\x1a\n"):
                mime = "image/png"
            elif len(image) >= 12 and image[:4] == b"RIFF" and image[8:12] == b"WEBP":
                mime = "image/webp"
            else:
                await say(chat_id, "Подходят только JPEG, PNG или WEBP.")
                return
            session["photo_base64"] = f"data:{mime};base64,{base64.b64encode(image).decode('ascii')}"
            await screen_summary(chat_id, user_id)
            return
        except Exception:
            logger.exception("Ошибка обработки фото")
            await say(chat_id, "Не удалось загрузить фото. Попробуйте ещё раз или нажмите «Без фото».")
            return


@dp.message_created(F.message.body.text)
async def on_text(event: MessageCreated):
    chat_id = event.message.recipient.chat_id
    user_id = str(chat_id)
    text = (event.message.body.text or "").strip()
    if text.startswith("/"):
        return
    session = get_session(user_id)
    if not session:
        await lost(chat_id, user_id)
        return
    state = session["state"]
    if state == "azs":
        await screen_azs(chat_id, user_id, 0, query=text)
    elif state == "details":
        parse_details(session, text)
        await screen_photo(chat_id, user_id)
    elif state == "fuel":
        await say(chat_id, "Отметьте топливо кнопками и нажмите «Готово».")
    elif state == "photo":
        session["note"] = (session["note"] + " " + text).strip()[:200]
        await say(chat_id, "Записал в примечание. Пришлите фото или нажмите «Без фото».")


@dp.message_callback()
async def on_callback(event: MessageCallback):
    chat_id = event.message.recipient.chat_id
    user_id = str(chat_id)
    data = event.callback.payload or ""
    session = get_session(user_id)

    if data == "azs_reload":
        _azs_cache.update(rows=[], at=0.0)
        await screen_azs(chat_id, user_id, 0)
        return
    if data.startswith("azspage_"):
        await screen_azs(chat_id, user_id, int(data.split("_", 1)[1]), query=(session or {}).get("query", ""))
        return
    if data.startswith("azs_"):
        rows, error = await fetch_azs()
        if error:
            await say(chat_id, f"Не удалось получить список АЗС: {error}.")
            return
        azs_id = int(data.split("_", 1)[1])
        azs = next((r for r in rows if r.get("id") == azs_id), None)
        if not azs:
            await say(chat_id, "Эта АЗС больше не доступна — выберите из свежего списка.")
            await screen_azs(chat_id, user_id, 0)
            return
        session = session or sessions.setdefault(user_id, new_session(user_id))
        session["azs_id"] = azs_id
        session["azs_name"] = str(azs.get("name") or f"АЗС {azs_id}")
        await screen_fuel(chat_id, user_id)
        return

    if not session:
        await lost(chat_id, user_id)
        return

    if data.startswith("f_") and data not in ("f_done", "f_all_ok"):
        key = data.split("_", 1)[1]
        current = session["fuel_status"].get(key)
        nxt = STATUS_CYCLE[(STATUS_CYCLE.index(current) + 1) % len(STATUS_CYCLE)] if current else STATUS_CYCLE[0]
        session["fuel_status"][key] = nxt
        await screen_fuel(chat_id, user_id)
        return
    if data == "f_all_ok":
        session["fuel_status"] = {key: "available" for key, _ in FUEL_TYPES}
        await screen_fuel(chat_id, user_id)
        return
    if data == "f_done":
        if not session["fuel_status"]:
            await say(chat_id, "Отметьте хотя бы один вид топлива.")
            return
        await screen_details(chat_id, user_id)
        return
    if data == "d_skip":
        await screen_photo(chat_id, user_id)
        return
    if data == "p_skip":
        await screen_summary(chat_id, user_id)
        return
    if data == "again":
        await screen_fuel(chat_id, user_id)
        return
    if data == "cancel":
        sessions.pop(user_id, None)
        await say(chat_id, "Отчёт отменён. Напишите /start, чтобы начать заново.")
        return
    if data == "send":
        if not session.get("azs_id"):
            await say(chat_id, "АЗС не выбрана — начнём заново.")
            await lost(chat_id, user_id)
            return
        if not session["fuel_status"]:
            await say(chat_id, "Нет данных по топливу.")
            await screen_fuel(chat_id, user_id)
            return
        await say(chat_id, "Отправляю…")
        ok, message = await post_report(session)
        if ok:
            sessions.pop(user_id, None)
            await say(chat_id, f"Готово: {message}. Напишите /start для следующего отчёта.")
        else:
            await say(chat_id, f"Не удалось отправить: {message}",
                      keyboard([[("Повторить", "send"), ("Отменить", "cancel")]]))


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
