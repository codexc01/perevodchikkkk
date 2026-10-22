import asyncio
import hashlib
import hmac
import logging
import os
import re

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


# секретный токен для проверки подлинности вебхука от telegram
WEBHOOK_SECRET = (
    hashlib.sha256(TELEGRAM_BOT_TOKEN.encode("utf-8")).hexdigest()[:32]
    if TELEGRAM_BOT_TOKEN
    else ""
)


@dp.inline_query()
async def handle_inline_query(query: InlineQuery) -> None:
    """обработчик inline query для мгновенного сленгового перевода"""
    text = query.query.strip()

    # валидация: меньше 2 или больше 300 символов — не делаем запрос к api для защиты от исчерпания лимитов
    if len(text) < 2 or len(text) > 300:
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


# вспомогательная функция установки вебхука
async def _do_set_webhook(request: Request):
    if not bot:
        return JSONResponse(
            status_code=500,
            content={"error": "TELEGRAM_BOT_TOKEN не задан в переменных vercel"},
        )

    # санитарная валидация host header для предотвращения host header injection / webhook hijacking
    raw_host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    host = raw_host.strip()
    if not re.match(r"^[a-zA-Z0-9.-]+(:[0-9]+)?$", host):
        return JSONResponse(
            status_code=400,
            content={"error": "недопустимый заголовок host"},
        )

    proto = request.headers.get("x-forwarded-proto", "https")
    webhook_url = f"{proto}://{host}/webhook"

    # привязка с передачей криптографического секретного токена
    await bot.set_webhook(
        url=webhook_url,
        secret_token=WEBHOOK_SECRET if WEBHOOK_SECRET else None,
        drop_pending_updates=True,
    )
    info = await bot.get_webhook_info()
    return {
        "ok": True,
        "webhook_url": info.url,
        "pending_update_count": info.pending_update_count,
    }


# вспомогательная функция обработки обновлений telegram
async def _do_process_webhook(request: Request):
    if not bot:
        return Response(status_code=500)

    # защита от memory exhaustion / dos: проверка размера тела запроса (максимум 64 кб)
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > 65536:
        logger.warning("отклонен запрос с превышением лимита размера тела: %s байт", content_length)
        return Response(status_code=413)

    # защита от timing-атак: константное по времени сравнение hmac.compare_digest
    if WEBHOOK_SECRET:
        secret_header = request.headers.get("x-telegram-bot-api-secret-token") or ""
        if not hmac.compare_digest(secret_header, WEBHOOK_SECRET):
            logger.warning("отклонен неавторизованный запрос к webhook: несовпадение секрета")
            return Response(status_code=403)

    try:
        data = await request.json()
        update = Update.model_validate(data, context={"bot": bot})
        await dp.feed_update(bot, update)
        return {"ok": True}
    except Exception as exc:
        logger.error("ошибка обработки обновления webhook: %s", exc, exc_info=True)
        return Response(status_code=200)


@app.get("/")
@app.get("/api")
@app.get("/api/index")
@app.get("/api/index.py")
async def root(request: Request):
    """проверка статуса или установка вебхука"""
    # если пользователь передал параметр ?action=set_webhook или зашел с пути set_webhook
    original_path = (
        request.headers.get("x-invoke-path")
        or request.headers.get("x-matched-path")
        or request.url.path
    )
    if "set_webhook" in original_path or request.query_params.get("action") == "set_webhook":
        return await _do_set_webhook(request)

    return {
        "status": "ok",
        "service": "telegram slang translator bot",
        "ready": bool(bot and gemini_client),
        "setup_webhook_url": f"{request.url.scheme}://{request.url.netloc}/set_webhook",
    }


@app.get("/set_webhook")
@app.get("/api/set_webhook")
async def set_webhook_route(request: Request):
    """привязка вебхука через прямой url"""
    return await _do_set_webhook(request)


@app.post("/")
@app.post("/webhook")
@app.post("/api")
@app.post("/api/webhook")
@app.post("/api/index")
@app.post("/api/index.py")
async def process_webhook_route(request: Request):
    """прием обновлений telegram по любому из путей"""
    return await _do_process_webhook(request)


@app.api_route("/{full_path:path}", methods=["GET", "POST"])
async def catch_all_route(request: Request, full_path: str):
    """универсальный перехватчик любых путей vercel rewrite"""
    if request.method == "POST":
        return await _do_process_webhook(request)

    if "set_webhook" in full_path or request.query_params.get("action") == "set_webhook":
        return await _do_set_webhook(request)

    return {
        "status": "ok",
        "service": "telegram slang translator bot",
        "ready": bool(bot and gemini_client),
        "path": full_path,
    }

