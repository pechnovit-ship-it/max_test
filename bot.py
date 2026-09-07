import asyncio
import base64
import logging
import os
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


MAX_BOT_TOKEN = required_env("BOT_TOKEN")
BOT_SECRET = required_env("BOT_SECRET")
APP_API_URL = required_env("APP_API_URL").rstrip("/")
REPORT_URL = f"{APP_API_URL}/api/public/bot/report"
AZS_URL = f"{APP_API_URL}/api/public/bot/azs"
MAX_PHOTO_BYTES = 5 * 1024 * 1024
AZS_PAGE_SIZE = 8
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=MAX_BOT_TOKEN)
dp = Dispatcher()

FUEL_TYPES = [
    {"key": "92", "name": "АИ-92"},
    {"key": "95", "name": "АИ-95"},
    {"key": "98", "name": "АИ-98"},
    {"key": "100", "name": "АИ-100"},
    {"key": "dt", "name": "ДТ"},
    {"key": "gas", "name": "ГАЗ"},
]

STATUS_LABELS = {
    "available": "Есть",
    "unavailable": "Нет",
    "refueling": "Слив",
}

RESTRICTIONS = {
    "none": "Нет ограничений",
    "special_only": "Только спецтранспорт",
    "limit_20l": "Не более 20 л",
    "closed": "АЗС закрыта",
}

user_sessions: dict[str, dict[str, Any]] = {}
azs_cache: list[dict[str, Any]] = []


def new_session(user_id: str) -> dict[str, Any]:
    return {
        "state": "start",
        "report": {
            "max_user_id": user_id,
            "azs_id": None,
            "operator_name": "Оператор",
            "fuel_status": {},
            "queue_length": 0,
            "restriction_type": "none",
            "prices": {},
            "remaining_pct": {},
            "remaining_l": {},
            "photo_base64": None,
            "note": "",
        },
        "current_fuel_index": 0,
        "current_fuel": None,
        "azs_name": None,
    }


def get_session(user_id: str) -> dict[str, Any]:
    if user_id not in user_sessions:
        user_sessions[user_id] = new_session(user_id)
    return user_sessions[user_id]


def make_keyboard(rows: list[list[tuple[str, str]]]):
    keyboard = InlineKeyboardBuilder()
    for row in rows:
        keyboard.row(*[CallbackButton(text=text, payload=payload) for text, payload in row])
    return keyboard.as_markup()


def api_headers() -> dict[str, str]:
    return {"x-bot-secret": BOT_SECRET}


async def send_message(chat_id: int, text: str, keyboard=None) -> None:
    try:
        attachments = [keyboard] if keyboard else None
        await bot.send_message(chat_id=chat_id, text=text, attachments=attachments)
    except Exception:
        logger.exception("Не удалось отправить сообщение в MAX")


async def read_api_response(response: aiohttp.ClientResponse) -> dict[str, Any]:
    try:
        payload = await response.json(content_type=None)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


async def fetch_azs() -> tuple[list[dict[str, Any]], str | None]:
    global azs_cache
    try:
        async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as http:
            async with http.get(AZS_URL, headers=api_headers()) as response:
                payload = await read_api_response(response)
                if response.status != 200 or not payload.get("success"):
                    return [], str(payload.get("error") or f"Ошибка сервера {response.status}")
                rows = payload.get("azs")
                if not isinstance(rows, list):
                    return [], "Сервер вернул некорректный список АЗС"
                azs_cache = [row for row in rows if isinstance(row, dict)]
                return azs_cache, None
    except asyncio.TimeoutError:
        return [], "Приложение не ответило за 30 секунд"
    except Exception:
        logger.exception("Не удалось получить список АЗС")
        return [], "Нет связи с приложением"


async def send_report_to_app(user_id: str) -> tuple[bool, str]:
    report = get_session(user_id)["report"]
    try:
        async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as http:
            async with http.post(REPORT_URL, json=report, headers=api_headers()) as response:
                payload = await read_api_response(response)
                if response.status == 200 and payload.get("success") is True:
                    report_id = payload.get("report_id")
                    logger.info("Отчёт принят, id=%s", report_id)
                    return True, "Отчёт принят приложением"
                error = str(payload.get("error") or f"Ошибка сервера {response.status}")
                details = payload.get("details")
                if details:
                    logger.error("Ошибка валидации отчёта: %s", details)
                return False, error
    except asyncio.TimeoutError:
        return False, "Приложение не ответило за 30 секунд"
    except Exception:
        logger.exception("Не удалось отправить отчёт")
        return False, "Нет связи с приложением"


def detect_image_mime(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


@dp.bot_started()
async def on_start(event: BotStarted):
    await send_message(event.chat_id, "Привет! Напишите /start, чтобы отправить отчёт.")


@dp.message_created(CommandStart())
async def cmd_start(event: MessageCreated):
    chat_id = event.message.recipient.chat_id
    user_id = str(chat_id)
    user_sessions[user_id] = new_session(user_id)
    keyboard = make_keyboard([[("Начать отчёт", "start_report")]])
    await send_message(chat_id, "Нажмите кнопку, чтобы начать новый отчёт.", keyboard)


@dp.message_created(F.message.body.attachments)
async def handle_attachments(event: MessageCreated):
    chat_id = event.message.recipient.chat_id
    user_id = str(chat_id)
    session = get_session(user_id)
    if session.get("state") != "photo":
        return

    for attachment in event.message.body.attachments:
        if attachment.type != "image":
            continue
        try:
            image_url = attachment.payload.url
            if not image_url:
                raise ValueError("MAX не вернул адрес изображения")
            async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as http:
                async with http.get(image_url) as response:
                    if response.status != 200:
                        raise ValueError(f"MAX вернул статус {response.status}")
                    image = await response.content.read(MAX_PHOTO_BYTES + 1)
            if len(image) > MAX_PHOTO_BYTES:
                await send_message(chat_id, "Фото больше 5 МБ. Отправьте изображение меньшего размера.")
                return
            mime = detect_image_mime(image)
            if not mime:
                await send_message(chat_id, "Допустимы только фотографии JPEG, PNG или WEBP.")
                return
            encoded = base64.b64encode(image).decode("ascii")
            session["report"]["photo_base64"] = f"data:{mime};base64,{encoded}"
            await send_message(chat_id, "Фото добавлено.")
            await ask_note(chat_id, user_id)
            return
        except Exception:
            logger.exception("Ошибка обработки фото")
            await send_message(chat_id, "Не удалось загрузить фото. Попробуйте ещё раз или пропустите.")
            return

    await send_message(chat_id, "Отправьте изображение JPEG, PNG или WEBP либо нажмите «Пропустить».")


@dp.message_created(F.message.body.text)
async def handle_text(event: MessageCreated):
    chat_id = event.message.recipient.chat_id
    user_id = str(chat_id)
    text = event.message.body.text.strip()
    session = get_session(user_id)
    state = session.get("state")

    if text.startswith("/"):
        return
    if state == "fuel_status":
        await send_message(chat_id, "Выберите статус кнопкой.")
        return
    if state == "photo":
        await send_message(chat_id, "Отправьте фото или нажмите «Пропустить».")
        return
    if state == "remaining_pct":
        try:
            value = int(text)
            fuel = session.get("current_fuel")
            if not fuel or not 0 <= value <= 100:
                raise ValueError
            session["report"]["remaining_pct"][fuel["key"]] = value
            await ask_remaining_l(chat_id, user_id)
        except ValueError:
            await send_message(chat_id, "Введите целое число от 0 до 100.")
        return
    if state == "remaining_l":
        try:
            value = int(text)
            fuel = session.get("current_fuel")
            if not fuel or not 0 <= value <= 1_000_000:
                raise ValueError
            session["report"]["remaining_l"][fuel["key"]] = value
            await ask_price(chat_id, user_id)
        except ValueError:
            await send_message(chat_id, "Введите целое число от 0 до 1 000 000.")
        return
    if state == "price":
        try:
            value = float(text.replace(",", "."))
            fuel = session.get("current_fuel")
            if not fuel or not 0 <= value <= 500:
                raise ValueError
            session["report"]["prices"][fuel["key"]] = value
            session["current_fuel_index"] += 1
            await ask_fuel_status(chat_id, user_id)
        except ValueError:
            await send_message(chat_id, "Введите цену от 0 до 500 рублей.")
        return
    if state == "queue":
        try:
            value = int(text)
            if not 0 <= value <= 1000:
                raise ValueError
            session["report"]["queue_length"] = value
            await ask_restriction(chat_id, user_id)
        except ValueError:
            await send_message(chat_id, "Введите количество машин от 0 до 1000.")
        return
    if state == "note":
        if len(text) > 200:
            await send_message(chat_id, "Примечание должно быть не длиннее 200 символов.")
            return
        session["report"]["note"] = text
        await show_summary(chat_id, user_id)


@dp.message_callback()
async def handle_callback(event: MessageCallback):
    chat_id = event.message.recipient.chat_id
    user_id = str(chat_id)
    data = event.callback.payload
    session = get_session(user_id)

    if data == "start_report":
        await show_azs_list(chat_id, user_id, 0)
        return
    if data.startswith("azspage_"):
        await show_azs_list(chat_id, user_id, int(data.split("_", 1)[1]))
        return
    if data.startswith("azs_"):
        azs_id = int(data.split("_", 1)[1])
        azs = next((item for item in azs_cache if item.get("id") == azs_id), None)
        if not azs:
            await send_message(chat_id, "Список АЗС изменился. Откройте его заново.")
            return
        session["report"]["azs_id"] = azs_id
        session["azs_name"] = azs.get("name") or f"АЗС {azs_id}"
        await ask_fuel_status(chat_id, user_id)
        return
    if data.startswith("fuel_"):
        status = data.split("_", 1)[1]
        fuel = session.get("current_fuel")
        if fuel and status in STATUS_LABELS:
            session["report"]["fuel_status"][fuel["key"]] = status
            await ask_remaining_pct(chat_id, user_id)
        return
    if data.startswith("restriction_"):
        restriction = data.split("_", 1)[1]
        if restriction in RESTRICTIONS:
            session["report"]["restriction_type"] = restriction
            await ask_photo(chat_id, user_id)
        return
    if data == "skip_remaining_pct":
        await ask_remaining_l(chat_id, user_id)
        return
    if data == "skip_remaining_l":
        await ask_price(chat_id, user_id)
        return
    if data == "skip_price":
        session["current_fuel_index"] += 1
        await ask_fuel_status(chat_id, user_id)
        return
    if data == "skip_photo":
        await ask_note(chat_id, user_id)
        return
    if data == "skip_note":
        session["report"]["note"] = ""
        await show_summary(chat_id, user_id)
        return
    if data == "confirm_send":
        await send_message(chat_id, "Отправляю отчёт…")
        success, message = await send_report_to_app(user_id)
        if success:
            await send_message(chat_id, f"Готово. {message}.")
            user_sessions.pop(user_id, None)
        else:
            keyboard = make_keyboard([[("Повторить", "confirm_send"), ("Отменить", "confirm_cancel")]])
            await send_message(chat_id, f"Не удалось отправить: {message}", keyboard)
        return
    if data == "confirm_cancel":
        user_sessions.pop(user_id, None)
        await send_message(chat_id, "Отчёт отменён.")


async def show_azs_list(chat_id: int, user_id: str, page: int):
    azs, error = await fetch_azs()
    if error:
        await send_message(chat_id, f"Не удалось получить список АЗС: {error}")
        return
    if not azs:
        await send_message(chat_id, "В приложении пока нет доступных АЗС.")
        return
    page_count = max(1, (len(azs) + AZS_PAGE_SIZE - 1) // AZS_PAGE_SIZE)
    page = min(max(page, 0), page_count - 1)
    start = page * AZS_PAGE_SIZE
    rows = [[(str(item.get("name") or f"АЗС {item['id']}"), f"azs_{item['id']}")] for item in azs[start:start + AZS_PAGE_SIZE]]
    navigation = []
    if page > 0:
        navigation.append(("Назад", f"azspage_{page - 1}"))
    if page + 1 < page_count:
        navigation.append(("Далее", f"azspage_{page + 1}"))
    if navigation:
        rows.append(navigation)
    await send_message(chat_id, f"Выберите АЗС (страница {page + 1}/{page_count}):", make_keyboard(rows))


async def ask_fuel_status(chat_id: int, user_id: str):
    session = get_session(user_id)
    index = session.get("current_fuel_index", 0)
    if index >= len(FUEL_TYPES):
        await ask_queue(chat_id, user_id)
        return
    fuel = FUEL_TYPES[index]
    session["current_fuel"] = fuel
    session["state"] = "fuel_status"
    keyboard = make_keyboard([[("Есть", "fuel_available"), ("Нет", "fuel_unavailable"), ("Слив", "fuel_refueling")]])
    await send_message(chat_id, f"Статус для {fuel['name']}:", keyboard)


async def ask_remaining_pct(chat_id: int, user_id: str):
    session = get_session(user_id)
    session["state"] = "remaining_pct"
    fuel = session["current_fuel"]
    await send_message(chat_id, f"Остаток {fuel['name']} в процентах (0–100):", make_keyboard([[("Пропустить", "skip_remaining_pct")]]))


async def ask_remaining_l(chat_id: int, user_id: str):
    session = get_session(user_id)
    session["state"] = "remaining_l"
    fuel = session["current_fuel"]
    await send_message(chat_id, f"Остаток {fuel['name']} в литрах:", make_keyboard([[("Пропустить", "skip_remaining_l")]]))


async def ask_price(chat_id: int, user_id: str):
    session = get_session(user_id)
    session["state"] = "price"
    fuel = session["current_fuel"]
    await send_message(chat_id, f"Цена {fuel['name']}:", make_keyboard([[("Пропустить", "skip_price")]]))


async def ask_queue(chat_id: int, user_id: str):
    get_session(user_id)["state"] = "queue"
    await send_message(chat_id, "Количество машин в очереди:")


async def ask_restriction(chat_id: int, user_id: str):
    get_session(user_id)["state"] = "restriction"
    keyboard = make_keyboard([
        [("Нет", "restriction_none"), ("До 20 л", "restriction_limit_20l")],
        [("Только спецтранспорт", "restriction_special_only"), ("Закрыта", "restriction_closed")],
    ])
    await send_message(chat_id, "Есть ограничения?", keyboard)


async def ask_photo(chat_id: int, user_id: str):
    get_session(user_id)["state"] = "photo"
    await send_message(chat_id, "Отправьте фото до 5 МБ:", make_keyboard([[("Пропустить", "skip_photo")]]))


async def ask_note(chat_id: int, user_id: str):
    get_session(user_id)["state"] = "note"
    await send_message(chat_id, "Добавьте примечание до 200 символов:", make_keyboard([[("Пропустить", "skip_note")]]))


async def show_summary(chat_id: int, user_id: str):
    session = get_session(user_id)
    report = session["report"]
    lines = ["Итоговый отчёт", "", f"АЗС: {session.get('azs_name') or 'не выбрана'}"]
    for fuel in FUEL_TYPES:
        key = fuel["key"]
        status = STATUS_LABELS.get(report["fuel_status"].get(key), "не указан")
        details = [status]
        if key in report["remaining_pct"]:
            details.append(f"{report['remaining_pct'][key]}%")
        if key in report["remaining_l"]:
            details.append(f"{report['remaining_l'][key]} л")
        if key in report["prices"]:
            details.append(f"{report['prices'][key]} ₽")
        lines.append(f"{fuel['name']}: {', '.join(details)}")
    lines.extend([
        "",
        f"Очередь: {report['queue_length']} машин",
        f"Ограничения: {RESTRICTIONS[report['restriction_type']]}",
        f"Фото: {'добавлено' if report['photo_base64'] else 'нет'}",
    ])
    if report["note"]:
        lines.append(f"Примечание: {report['note']}")
    keyboard = make_keyboard([[("Отправить", "confirm_send"), ("Отменить", "confirm_cancel")]])
    await send_message(chat_id, "\n".join(lines), keyboard)


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
