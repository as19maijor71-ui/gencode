# Технические решения

1. **Python 3.11+** — стабильный async, зрелые библиотеки для Telegram
2. **aiogram 3.x** — лучшая async-библиотека для Telegram Bot API, FSM из коробки
3. **Pydantic** — строгая типизация AI-ответов, валидация JSON
4. **httpx** — async HTTP, таймауты, retry
5. **pydantic-settings** — загрузка конфигурации из .env
6. **Docker** — один контейнер, один деплой. Без оркестрации.
7. **Polling** — не webhook. Проще для MVP. Не нужен домен.
8. **MemoryStorage** — не PostgreSQL. Без персистентности в MVP.
9. **OpenRouter primary + YandexGPT fallback** — два провайдера, автоматическое переключение
