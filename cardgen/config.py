from dotenv import load_dotenv
from pydantic_settings import BaseSettings

load_dotenv()


class Settings(BaseSettings):
    BOT_TOKEN: str

    AI_PROVIDER: str = "openrouter"

    OPENROUTER_API_KEY: str = ""
    OPENROUTER_MODEL: str = "deepseek/deepseek-r1"
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1/chat/completions"

    YANDEXGPT_API_KEY: str = ""
    YANDEXGPT_FOLDER_ID: str = ""
    YANDEXGPT_BASE_URL: str = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"

    PROXY_URL: str = ""

    MAX_INPUT_LENGTH: int = 2000
    MAX_RETRIES: int = 1
    DEFAULT_MAX_TOKENS: int = 4096
    REQUEST_TIMEOUT: int = 120


settings = Settings()
