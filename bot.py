import asyncio
import logging
import os
from collections import defaultdict, deque
import aiohttp
from aiohttp import web
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import BusinessConnection, Message

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("Не задан BOT_TOKEN в переменных окружения")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite").strip()
SYSTEM_PROMPT = os.getenv(
    "SYSTEM_PROMPT",
    (
        "Ты вежливый и лаконичный личный ассистент в Telegram. "
        "Отвечай по делу, дружелюбно, на языке собеседника. "
        "Если вопрос требует личного участия владельца аккаунта, пообещай передать ему сообщение."
    ),
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ID владельцев бизнес-аккаунтов, чтобы бот не отвечал на ваши собственные сообщения
connected_owners: dict[str, int] = {}

# Храним последние 10 реплик для каждого чата, чтобы ИИ помнил контекст
chat_histories: dict[int, deque[dict]] = defaultdict(lambda: deque(maxlen=10))


async def ask_gemini(chat_id: int, user_text: str) -> str | None:
    """Отправляет сообщение и историю диалога в Gemini API и возвращает ответ."""
    if not GEMINI_API_KEY:
        return None

    history = chat_histories[chat_id]
    history.append({"role": "user", "parts": [{"text": user_text}]})

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent"
    )
    payload = {
        "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": list(history),
    }
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": GEMINI_API_KEY,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=25)
            ) as resp:
                data = await resp.json()
                if resp.status != 200:
                    logger.error("Ошибка Gemini API (%s): %s", resp.status, data)
                    history.pop()
                    return None

                candidates = data.get("candidates") or []
                if not candidates:
                    history.pop()
                    return None

                parts = candidates[0].get("content", {}).get("parts", [])
                answer = "".join(p.get("text", "") for p in parts).strip()
                if not answer:
                    history.pop()
                    return None

                history.append({"role": "model", "parts": [{"text": answer}]})
                return answer
    except Exception as exc:
        logger.exception("Сбой при запросе к Gemini API: %s", exc)
        if history and history[-1]["role"] == "user":
            history.pop()
        return None


@dp.message(CommandStart())
async def cmd_start(message: Message) -> None:
    """Ответ на /start в обычном личном чате с самим ботом."""
    await message.answer(
        "Привет! Я ИИ-бизнес-бот для автоматизации чатов.\n"
        "Подключите меня в Telegram: Настройки → Telegram для бизнеса → Автоматизация чатов."
    )


@dp.business_connection()
async def on_business_connection(connection: BusinessConnection) -> None:
    """Срабатывает, когда вы подключаете или отключаете бота в 'Автоматизация чатов'."""
    if connection.is_enabled:
        connected_owners[connection.id] = connection.user.id
        logger.info(
            "Бизнес-подключение активно: connection_id=%s, owner_id=%s (@%s)",
            connection.id,
            connection.user.id,
            connection.user.username,
        )
    else:
        connected_owners.pop(connection.id, None)
        logger.info("Бизнес-подключение отключено: connection_id=%s", connection.id)


@dp.business_message(F.text)
async def on_business_message(message: Message) -> None:
    """Обрабатывает входящие текстовые сообщения в подключённых личных чатах."""
    conn_id = message.business_connection_id
    if not conn_id or not message.from_user:
        return

    # Игнорируем ваши собственные исходящие сообщения, чтобы избежать зацикливания
    owner_id = connected_owners.get(conn_id)
    if owner_id and message.from_user.id == owner_id:
        return
    if message.from_user.id != message.chat.id:
        return

    user_text = message.text.strip()
    logger.info("Входящее бизнес-сообщение от %s: %s", message.from_user.id, user_text)

    reply_text = await ask_gemini(message.chat.id, user_text)
    if not reply_text:
        reply_text = (
            "Сообщение получено! Я передал его владельцу аккаунта, он ответит вам лично чуть позже."
        )

    await bot.send_message(
        chat_id=message.chat.id,
        text=reply_text,
        business_connection_id=conn_id,
    )


async def healthcheck(_: web.Request) -> web.Response:
    """Простой HTTP-ответ, чтобы бесплатный Web Service на Render оставался активным."""
    return web.Response(text="OK")


async def start_web_server() -> None:
    app = web.Application()
    app.router.add_get("/", healthcheck)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", "10000"))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info("Веб-сервер запущен на порту %s", port)


async def main() -> None:
    logger.info("Запуск ИИ-бизнес-бота (модель: %s)...", GEMINI_MODEL)
    await asyncio.gather(
        start_web_server(),
        dp.start_polling(bot),
    )


if __name__ == "__main__":
    asyncio.run(main())
