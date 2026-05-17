# Поток данных

## Путь запроса

```
Пользователь (Telegram)
    ↓ текстовое описание товара
bot/handlers.py (FSM: category_select → product_input → generating)
    ↓ product_description, category, seller_name, brand
templates/prompts.py (build_prompt)
    ↓ полный промт с правилами WB и Ozon
engine/generator.py (generate_card)
    ↓ HTTP POST → OpenRouter / YandexGPT
AI-модель (JSON ответ)
    ↓ парсинг через Pydantic CardResult
bot/handlers.py (форматирование)
    ↓ 5 сообщений в Telegram
Пользователь (готовая стратегия)
```

## Компоненты

1. **bot/** — интерфейс (Telegram-бот, FSM, клавиатуры)
2. **engine/** — ядро (генерация промта, вызов AI, парсинг ответа)
3. **templates/** — знания (правила WB/Ozon, категории товаров, промт)
4. **config.py** — конфигурация (токены, ключи, настройки)
