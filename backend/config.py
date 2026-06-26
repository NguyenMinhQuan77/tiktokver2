import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    TIKTOK_USERNAME: str = os.getenv("TIKTOK_USERNAME", "")
    TIKTOK_PASSWORD: str = os.getenv("TIKTOK_PASSWORD", "")
    TIKTOK_HANDLE: str = os.getenv("TIKTOK_HANDLE", "")
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    SECRET_KEY: str = os.getenv("SECRET_KEY", "change-me-in-production-please")

    # File storage
    TEMP_DIR: str = os.path.join(os.path.dirname(os.path.dirname(__file__)), "temp")

    # App
    BASE_URL: str = "http://localhost:8000"


settings = Settings()
