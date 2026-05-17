# feedback_no_env_read

**type:** feedback

**Правило:** .env читать только через config.py (pydantic-settings). Никогда не читать .env напрямую.

**Why:** Прямое чтение .env обходит валидацию. Переменная может отсутствовать — приложение упадёт, а не сообщит об ошибке при старте.

**How to apply:** Все настройки — через `from config import settings`. `settings.BOT_TOKEN`, `settings.OPENROUTER_API_KEY` и т.д. Никаких `os.getenv()`.
