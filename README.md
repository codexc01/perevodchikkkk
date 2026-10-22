# slang translator bot

минималистичный инлайн телеграм бот который переводит текст с русского на живой американский сленг как в переписках imessage

переводит прямо на лету в любом чате через `@bot_username текст`

## фичи

- только инлайн режим без спама в лс
- натуральный gen-z сленг и сокращения (rn, tbh, ngl, lowkey, wanna, fr)
- все строчными буквами без лишних знаков препинания
- работает на google gemini flash
- кэширование одинаковых запросов на 5 минут чтобы не дергать апи лишний раз
- готов к бесплатному деплою на vercel serverless

## стек

- python 3.11+
- aiogram 3
- google-genai
- fastapi + uvicorn

## как запустить локально

1. клонируем репозиторий:
```bash
git clone https://github.com/codexc01/perevodchikkkk.git
cd perevodchikkkk
```

2. создаем виртуальное окружение и ставим зависимости:
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

3. копируем конфиг и вставляем свои ключи:
```bash
cp .env.example .env
```

4. запускаем:
```bash
python3 bot.py
```

## деплой на vercel

1. импортируй репозиторий в vercel
2. в настройках проекта добавь environment variables:
   - `TELEGRAM_BOT_TOKEN`
   - `GEMINI_API_KEY`
3. жми deploy
4. после деплоя открой в браузере один раз:
   `https://твой-проект.vercel.app/set_webhook`
   это привяжет бота к телеграму и все готово

## настройка в ботфазере

не забудь включить инлайн режим у @botfather через команду `/setinline`
