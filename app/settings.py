import os
from pathlib import Path

from dotenv import load_dotenv as _load_dotenv

_load_dotenv(Path(__file__).resolve().parents[1] / ".env")

APP_CONFIG_DIR = Path(
    os.environ.get("ACCOUNT_CHECKER_CONFIG", "~/.config/account-checker")
).expanduser()
APP_DATA_DIR = Path(
    os.environ.get("ACCOUNT_CHECKER_DATA", "~/.local/share/account-checker")
).expanduser()
URLS_ROUTER = "app.urls.AppRouter"
TIME_ZONE = os.environ.get("ACCOUNT_CHECKER_TIME_ZONE", "UTC")
DEBUG = os.environ.get("ACCOUNT_CHECKER_DEBUG", "false").lower() == "true"
SECRET_KEY = (APP_CONFIG_DIR / "secret-key").read_text().strip()
POSTGRES_URL = (
    os.environ.get("ACCOUNT_CHECKER_DATABASE_URL")
    or (APP_CONFIG_DIR / "database-url").read_text().strip()
)
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "testserver"]
INSTALLED_PACKAGES = [
    "plain.assets",
    "plain.templates",
    "plain.postgres",
    "plain.tailwind",
    "plain.auth",
    "plain.passwords",
    "plain.sessions",
    "plain.htmx",
    "app.users",
    "app.core",
]
AUTH_LOGIN_URL = "login"
MIDDLEWARE = [
    "plain.postgres.DatabaseConnectionMiddleware",
    "plain.sessions.middleware.SessionMiddleware",
]

# The prototype binds exclusively to loopback; enable TLS before remote deployment.
HTTPS_REDIRECT_ENABLED = False
SESSION_COOKIE_SECURE = False
