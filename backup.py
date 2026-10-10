"""
Резервна копія бази PostgreSQL.

Запускати з кореня проєкту, коли контейнери працюють (docker compose up):
    python backup.py

Робить pg_dump у файл усередині контейнера бази, забирає його через docker cp
у backups/backup_<дата>_<час>.sql і прибирає тимчасовий файл у контейнері.
Результат записує в app.log усередині контейнера бекенду, поруч з іншими діями.
"""

import os
import subprocess
import sys
from datetime import datetime

DB_CONTAINER = "postgres_container"
APP_CONTAINER = "image-server-app"

BACKUPS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backups")

# Як і в app.py: без цього консоль Windows показує українські літери кракозябрами
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")


def write_log(action, message):
    """Дописує рядок у /logs/app.log контейнера бекенду. Час ставить сам контейнер, як і в app.py."""
    line = f"{action}: {message}"
    result = subprocess.run(
        ["docker", "exec", APP_CONTAINER, "sh", "-c",
         'echo "[$(date "+%Y-%m-%d %H:%M:%S")] $1" >> /logs/app.log', "sh", line],
        capture_output=True,
    )
    if result.returncode != 0:
        print("Не вдалося записати в app.log, контейнер бекенду не запущений?")


def run(command):
    """Запускає команду. Повертає текст помилки одним рядком або None, якщо все добре."""
    try:
        result = subprocess.run(command, capture_output=True)
    except FileNotFoundError:
        return "команду docker не знайдено"
    if result.returncode != 0:
        return " ".join(result.stderr.decode("utf-8", errors="replace").split())
    return None


def main():
    os.makedirs(BACKUPS_DIR, exist_ok=True)
    filename = f"backup_{datetime.now():%Y-%m-%d_%H%M%S}.sql"
    path = os.path.join(BACKUPS_DIR, filename)
    tmp_path = f"/tmp/{filename}"

    # Дамп пишемо у файл прямо в контейнері, а вже готовий файл копіюємо на хост.
    # --clean --if-exists: дамп спершу видаляє таблицю, тому відновлення в робочу базу
    # її перезаписує, а не падає на "already exists" і дублікатах id.
    # Користувача і базу підставляє сам контейнер зі своїх змінних, тобто з того ж .env.
    error = run(["docker", "exec", DB_CONTAINER, "sh", "-c",
                 'pg_dump --clean --if-exists -U "$POSTGRES_USER" -f "$1" "$POSTGRES_DB"',
                 "sh", tmp_path])
    if error is None:
        error = run(["docker", "cp", f"{DB_CONTAINER}:{tmp_path}", path])
    run(["docker", "exec", DB_CONTAINER, "rm", "-f", tmp_path])

    if error is not None:
        if os.path.exists(path):
            os.remove(path)
        print(f"Помилка резервного копіювання: {error}")
        write_log("Помилка", f"резервну копію бази не створено ({error})")
        return 1

    size = os.path.getsize(path)
    print(f"Резервну копію збережено: backups/{filename} ({size} байт)")
    write_log("Успіх", f"резервну копію бази {filename} створено ({size} байт)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
