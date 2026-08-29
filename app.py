"""
Сервер зображень на Python.

Що він робить:
   віддає сторінки сайту з папки static/
   приймає картінки на POST /upload, перевіряє їх і зберігає
   віддає список завантажених файлів на GET /api/images
   записує всі дії в лог app.log

Зі сторонніх бібліотек тут тільки Pillow, все інше дефолтні.
"""

import logging
import os
import sys

# Налаштування

# Папка, де лежить сам app.py. Від неї рахуємо шлях до static/
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")

# Шляхи до томів. У Docker ми передаємо IMAGES_DIR=/images і LOGS_DIR=/logs.
# Якщо запускати без Docker, беруться звичайні папки в корені проєкту.
IMAGES_DIR = os.environ.get("IMAGES_DIR", os.path.join(BASE_DIR, "images"))
LOGS_DIR = os.environ.get("LOGS_DIR", os.path.join(BASE_DIR, "logs"))

HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", "8000"))

# Обмеження на завантаження
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif"}
MAX_FILE_SIZE = 5 * 1024 * 1024                  # 5 МБ максимум для самого файлу
# Трохи більше за 5 МБ, бо крім самого файлу в тілі запиту є ще службові рядки.
# Таке саме число стоїть у nginx.conf (client_max_body_size 6m), щоб не було різнобою.
HARD_BODY_LIMIT = MAX_FILE_SIZE + 1024 * 1024

# Який формат картинки якому розширенню відповідає.
# Розширення беремо з реального вмісту, а не з імені файлу, так надійніше.
FORMAT_TO_EXTENSION = {"JPEG": ".jpg", "PNG": ".png", "GIF": ".gif"}

# Типи вмісту для статичних файлів
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".ico": "image/x-icon",
    ".svg": "image/svg+xml",
}

# Логи

os.makedirs(IMAGES_DIR, exist_ok=True)
os.makedirs(LOGS_DIR, exist_ok=True)

# Консоль у Windows не в UTF-8, і без цього замість українських літер
# у ній виходять кракозябри. logging пише в stderr, тому чіпаємо обидва потоки.
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(os.path.join(LOGS_DIR, "app.log"), encoding="utf-8"),
        logging.StreamHandler(),  # дублюємо в консоль, щоб було видно в docker logs
    ],
)
logger = logging.getLogger("image-server")


def log(action, message):
    """Пише рядок у лог у форматі з ТЗ: [Дата/час] Дія: повідомлення."""
    level = logging.WARNING if action == "Помилка" else logging.INFO
    logger.log(level, "%s: %s", action, message)


# Доп функції

def resolve_inside(base_dir, name):
    """
    Робить з імені у посиланні шлях усередині base_dir.
    Якщо файлу немає або шлях виводить за межі папки, повертаємо None.
    Порівнюємо разом з роздільником, інакше пройшла б сусідня папка images_secret.
    """
    path = os.path.normpath(os.path.join(base_dir, name.lstrip("/\\")))
    inside = path == base_dir or path.startswith(base_dir + os.sep)
    return path if inside and os.path.isfile(path) else None


def content_type_for(path):
    """Повертає тип вмісту за розширенням файлу."""
    return CONTENT_TYPES.get(os.path.splitext(path)[1].lower(), "application/octet-stream")
