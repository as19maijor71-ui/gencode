# 06-architecture — Архитектура

## Стек

| Слой | Технология |
|---|---|
| Язык | Python 3.11+ |
| Telegram Bot | aiogram 3.x |
| Валидация | Pydantic 2.x |
| HTTP-клиент | httpx |
| Конфигурация | pydantic-settings + .env |
| AI-провайдер | OpenRouter (основной), YandexGPT (fallback) |
| Деплой | Docker + docker-compose |
| Хранение | MemoryStorage (FSM). Без БД в MVP |
