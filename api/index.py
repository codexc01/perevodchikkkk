import asyncio
import hashlib
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.types import (
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
    Message,
    Update,
)
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from google import genai
from google.genai import types

from pathlib import Path

# загрузка переменных окружения
env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=env_path)
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("translator_bot")

# инициализация клиентов
bot = Bot(token=TELEGRAM_BOT_TOKEN) if TELEGRAM_BOT_TOKEN else None
dp = Dispatcher()
gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

SYSTEM_INSTRUCTION = (
    "You are an expert translator specializing in modern Gen-Z / youth texting slang.\n"
    "Translate the input Russian text into natural conversational English as sent by native speakers on iPhone/iMessage.\n"
    "STRICT FORMAT RULES:\n"
    "- all lowercase ONLY (never use capital letters anywhere, including 'i' or sentence beginnings).\n"
    "- no period at the end of the sentence.\n"
    "- never use em-dashes (—).\n"
    "- minimal punctuation; drop commas unless essential for clarity.\n"
    "- use natural texting abbreviations and slang where appropriate (rn, tbh, ngl, idk, fr, lowkey, wanna, gonna, bro, lol, etc.).\n"
    "- return ONLY the final translated phrase, no quotes, no commentary."
)


def _translate_to_slang_sync(client: genai.Client, text: str) -> str:
    """синхронный вызов официального sdk google gemini с fallback-моделью"""
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_INSTRUCTION,
        temperature=0.75,
        max_output_tokens=80,
    )
    models_to_try = ("gemini-3.5-flash-lite", "gemini-3.8-flash", "gemini-flash-latest")
    last_error = None

    for model_name in models_to_try:
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=text,
                config=config,
            )
            result = (response.text or "").strip().lower()
            # санитизация: удаление случайных кавычек и точки на конце
            return result.strip('"\'`').rstrip(".")
        except Exception as exc:
            last_error = exc
            logger.warning("модель %s недоступна: %s, пробуем следующую", model_name, exc)
            continue

    if last_error:
        raise last_error
    return ""


@dp.inline_query()
async def handle_inline_query(query: InlineQuery) -> None:
    """обработчик inline query для мгновенного сленгового перевода"""
    text = query.query.strip()

    # валидация: меньше 2 символов — не делаем запрос к api
    if len(text) < 2:
        await query.answer(results=[], cache_time=300, is_personal=True)
        return

    if not gemini_client:
        logger.error("ошибка: GEMINI_API_KEY не настроен")
        await query.answer(results=[], cache_time=5, is_personal=True)
        return

    try:
        # неблокирующий вызов синхронного api gemini в отдельном потоке
        translated_text = await asyncio.to_thread(
            _translate_to_slang_sync, gemini_client, text
        )

        if not translated_text:
            await query.answer(results=[], cache_time=60, is_personal=True)
            return

        result_id = hashlib.md5(text.encode("utf-8")).hexdigest()

        # весь текст для пользователя строчными буквами и без точки в конце
        article = InlineQueryResultArticle(
            id=result_id,
            title=translated_text,
            description=f"оригинал: {text.lower()}",
            input_message_content=InputTextMessageContent(
                message_text=translated_text
            ),
        )

        await query.answer(results=[article], cache_time=300, is_personal=True)

    except Exception as exc:
        logger.error("ошибка при запросе к gemini api: %s", exc, exc_info=True)
        # не роняем сервис при ошибках api, возвращаем пустой результат с коротким кэшем
        await query.answer(results=[], cache_time=5, is_personal=True)


@dp.message()
async def handle_direct_messages(message: Message) -> None:
    """подсказка при попытке написать боту в лс"""
    bot_info = await bot.get_me()
    bot_username = (bot_info.username or "bot").lower()
    await message.answer(
        f"этот бот работает только в инлайн-режиме\n\n"
        f"чтобы перевести фразу, введи в любом чате:\n"
        f"@{bot_username} твой текст на русском"
    )


# fastapi приложение для serverless-среды vercel
app = FastAPI(title="translator-bot-serverless")


@app.get("/")
async def root():
    """проверка работоспособности сервиса"""
    return {
        "status": "ok",
        "service": "telegram slang translator bot",
        "ready": bool(bot and gemini_client),
    }


@app.get("/set_webhook")
@app.get("/api/set_webhook")
async def set_webhook(request: Request):
    """быстрая привязка вебхука в один клик через браузер"""
    if not bot:
        return JSONResponse(
            status_code=500,
            content={"error": "TELEGRAM_BOT_TOKEN не задан в переменных vercel"},
        )

    # формируем публичный https url проекта vercel
    host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    proto = request.headers.get("x-forwarded-proto", "https")
    webhook_url = f"{proto}://{host}/webhook"

    await bot.set_webhook(url=webhook_url, drop_pending_updates=True)
    info = await bot.get_webhook_info()
    return {
        "ok": True,
        "webhook_url": info.url,
        "pending_update_count": info.pending_update_count,
    }


@app.post("/webhook")
@app.post("/api/webhook")
@app.post("/api/index")
async def process_webhook(request: Request):
    """прием обновлений от telegram через webhook"""
    if not bot:
        return Response(status_code=500)

    try:
        data = await request.json()
        update = Update.model_validate(data, context={"bot": bot})
        await dp.feed_update(bot, update)
        return {"ok": True}
    except Exception as exc:
        logger.error("ошибка обработки обновления webhook: %s", exc, exc_info=True)
        return Response(status_code=200)  # всегда возвращаем 200 телеграму, чтобы он не спамил повторами
