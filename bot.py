import asyncio
import logging
import os
from aiohttp import web
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import BusinessConnection, Message

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("Не задан BOT_TOKEN в переменных окружения")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Храним ID владельцев бизнес-аккаунтов, чтобы бот не отвечал на ваши собственные сообщения
connected_owners: dict[str, int] = {}


@dp.message(CommandStart())
async def cmd_start(message: Message) -> None:
    """Ответ на /start в обычном личном чате с самим ботом."""
    await message.answer(
        "Привет! Я бизнес-бот для автоматизации чатов.\n"
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

    text_lower = message.text.strip().lower()
    logger.info("Входящее бизнес-сообщение от %s: %s", message.from_user.id, message.text)

    if any(word in text_lower for word in ("привет", "здравствуйте", "добрый день")):
        reply_text = (
            "Здравствуйте! Сейчас я не у экрана, но ваше сообщение получено. "
            "Напишите, пожалуйста, по какому вопросу вы обращаетесь — отвечу в ближайшее время."
        )
    elif any(word in text_lower for word in ("цена", "стоимость", "прайс", "сколько")):
        reply_text = (
            "Спасибо за интерес! Уточните, пожалуйста, задачу или услугу, "
            "и я отправлю актуальные условия."
        )
    else:
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
    logger.info("Запуск бизнес-бота...")
    await asyncio.gather(
        start_web_server(),
        dp.start_polling(bot),
    )


if __name__ == "__main__":
    asyncio.run(main())
