import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL", "sqlite:///movie_tracker.db")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    TMDB_ACCESS_TOKEN = os.environ.get("TMDB_ACCESS_TOKEN")
    TMDB_BASE_URL = "https://api.themoviedb.org/3"
    MAX_CONTENT_LENGTH = 30 * 1024 * 1024
#30mb upload