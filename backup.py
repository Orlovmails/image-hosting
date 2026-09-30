"""
Резервна копія бази PostgreSQL.

Запускати з кореня проєкту, коли контейнери працюють (docker compose up):
    python backup.py

Робить pg_dump у контейнері бази і кладе результат у backups/backup_<дата>_<час>.sql.
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


def main():
    os.makedirs(BACKUPS_DIR, exist_ok=True)
    filename = f"backup_{datetime.now():%Y-%m-%d_%H%M%S}.sql"
    path = os.path.join(BACKUPS_DIR, filename)

    # Без -t, хоч у команді з ТЗ він є: з ним docker додає у вивід \r і дамп псується.
    # --clean --if-exists: дамп спершу видаляє таблицю, тому відновлення в робочу базу
    # її перезаписує, а не падає на "already exists" і дублікатах id.
    # Користувача і базу підставляє сам контейнер зі своїх змінних, тобто з того ж .env.
    command = ["docker", "exec", DB_CONTAINER, "sh", "-c",
               'pg_dump --clean --if-exists -U "$POSTGRES_USER" "$POSTGRES_DB"']
    try:
        with open(path, "wb") as f:
            result = subprocess.run(command, stdout=f, stderr=subprocess.PIPE)
    except FileNotFoundError:
        os.remove(path)
        print("Команду docker не знайдено")
        return 1

    if result.returncode != 0:
        os.remove(path)
        # Помилка буває в кілька рядків, а в лозі все має бути одним рядком
        error = " ".join(result.stderr.decode("utf-8", errors="replace").split())
        print(f"Помилка резервного копіювання: {error}")
        write_log("Помилка", f"резервну копію бази не створено ({error})")
        return 1

    size = os.path.getsize(path)
    print(f"Резервну копію збережено: backups/{filename} ({size} байт)")
    write_log("Успіх", f"резервну копію бази {filename} створено ({size} байт)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
