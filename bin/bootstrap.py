"""Create a private local installation. Run with `uv run python bin/bootstrap.py`."""

import os
import secrets
import subprocess
from pathlib import Path

from cryptography.fernet import Fernet
from dotenv import load_dotenv

project = Path(__file__).resolve().parents[1]
load_dotenv(project / ".env")
config = Path(os.environ.get("ACCOUNT_CHECKER_CONFIG", "~/.config/account-checker")).expanduser()
config.mkdir(parents=True, exist_ok=True, mode=0o700)
config.chmod(0o700)


def write_once(name, value):
    try:
        fd = os.open(config / name, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return (config / name).read_text().strip()
    with os.fdopen(fd, "w") as f:
        f.write(value)
    return value


for name in ["secret-key", "admin-password", "api-token", "opencode-password"]:
    write_once(name, secrets.token_urlsafe(36))
write_once("snapshot-key", Fernet.generate_key().decode())
password = write_once("database-password", secrets.token_urlsafe(36))
if "ACCOUNT_CHECKER_DATABASE_URL" not in os.environ:
    write_once(
        "database-url", f"postgresql://account_checker:{password}@127.0.0.1:54429/account_checker"
    )
    exists = (
        subprocess.run(
            ["docker", "inspect", "account-checker-postgres"], capture_output=True, check=False
        ).returncode
        == 0
    )
    if exists:
        subprocess.run(
            ["docker", "start", "account-checker-postgres"], check=True, capture_output=True
        )
    else:
        subprocess.run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                "account-checker-postgres",
                "-p",
                "127.0.0.1:54429:5432",
                "-e",
                "POSTGRES_USER=account_checker",
                "-e",
                "POSTGRES_DB=account_checker",
                "-e",
                "POSTGRES_PASSWORD",
                "-v",
                "account-checker-postgres:/var/lib/postgresql",
                "postgres:18-alpine",
            ],
            check=True,
            capture_output=True,
            env={**os.environ, "POSTGRES_PASSWORD": password},
        )
subprocess.run(
    ["docker", "build", "-f", "Dockerfile.browser", "-t", "account-checker-browser:dev", "."],
    cwd=project,
    check=True,
)
for command in [
    ["postgres", "wait"],
    ["postgres", "sync"],
    ["accounts", "init"],
    ["tailwind", "build"],
    ["assets", "compile"],
    ["preflight"],
]:
    subprocess.run(["uv", "run", "plain", *command], cwd=project, check=True)
print(f"Local installation ready. Credentials: {config}. No existing secrets were replaced.")
