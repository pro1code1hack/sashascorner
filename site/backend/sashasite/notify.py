"""Owner notifications via the ops Telegram bot, in Russian like the bot itself.

Always called from a background task: a Telegram outage must never fail or slow
a guest's booking. Failures are logged; an unset token is logged at INFO.
"""

from __future__ import annotations

import datetime as dt
import logging

import httpx

from sashasite.config import get_settings

log = logging.getLogger("sashasite.notify")
# httpx logs every request URL at INFO -- and the Bot API URL contains the token.
logging.getLogger("httpx").setLevel(logging.WARNING)

_WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
_MONTHS = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]
_TOPICS = {
    "general": "общий вопрос",
    "order": "заказ торта / предзаказ",
    "events": "мероприятия",
    "feedback": "отзыв",
    "press": "пресса",
    "jobs": "работа",
}


def ru_date(d: dt.date) -> str:
    return f"{_WEEKDAYS[d.weekday()]} {d.day} {_MONTHS[d.month - 1]}"


def ru_guests(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        word = "гость"
    elif n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        word = "гостя"
    else:
        word = "гостей"
    return f"{n} {word}"


def booking_text(
    *,
    kind: str,
    reference: str,
    day: dt.date,
    time: str,
    party: int,
    name: str,
    phone: str | None,
    notes: str | None,
) -> str:
    head = "Новая бронь" if kind == "created" else "Бронь отменена"
    parts = [f"{head} {reference}: {ru_date(day)}, {time}, {ru_guests(party)} — {name}"]
    if phone:
        parts.append(phone)
    text = ", ".join(parts)
    if notes:
        text += f", заметки: {notes}"
    return text


def contact_text(*, name: str, email: str, topic: str, message: str) -> str:
    return f"Сообщение с сайта ({_TOPICS.get(topic, topic)}) от {name} <{email}>:\n\n{message}"


def send_owner(text: str) -> None:
    s = get_settings()
    if not s.telegram_configured:
        log.info("telegram not configured; skipped notification: %s", text.splitlines()[0])
        return
    url = f"https://api.telegram.org/bot{s.telegram_bot_token}/sendMessage"
    try:
        resp = httpx.post(
            url,
            json={
                "chat_id": s.telegram_owner_chat_id,
                "text": text,
                "disable_web_page_preview": True,
            },
            timeout=5.0,
        )
        if resp.status_code != 200:
            log.error("telegram notify failed: HTTP %s %s", resp.status_code, resp.text[:200])
    except httpx.HTTPError as exc:
        # Never log the URL: it carries the bot token.
        log.error("telegram notify failed: %s", type(exc).__name__)
