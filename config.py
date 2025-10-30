import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    """Set Flask configuration from environment variables."""

    # Database
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL")
    SECRET_KEY = os.environ.get("SECRET_KEY")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    ZERODHA_API_KEY = os.environ.get("ZERODHA_API_KEY")
    ZERODHA_API_SECRET = os.environ.get("ZERODHA_API_SECRET")
