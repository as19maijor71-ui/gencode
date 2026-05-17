# Технические риски

| Риск | Уровень | Смягчение |
|---|---|---|
| OpenRouter недоступен | High | YandexGPT fallback |
| JSON невалиден | Medium | Повторный запрос, fallback raw_response |
| Polling не масштабируется | Low | В Фазе 2 — webhook |
| MemoryStorage теряет состояние | Low | Приемлемо для MVP |
| Таймаут AI (60 сек) | Low | Сообщение об ошибке, кнопка «попробовать ещё раз» |
