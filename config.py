import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key")
    APP_BASE_URL = os.getenv("APP_BASE_URL", "http://127.0.0.1:7700")

    BIRDEYE_API_KEY = os.getenv("BIRDEYE_API_KEY", "")
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5")

    STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
    STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
    STRIPE_PRICE_ID_PRO = os.getenv("STRIPE_PRICE_ID_PRO", "")

    FREE_DAILY_ANALYSIS_LIMIT = int(os.getenv("FREE_DAILY_ANALYSIS_LIMIT", "3"))

    SQLALCHEMY_DATABASE_URI = "sqlite:///memecheck.db"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
