"""
Сервер зображень на Python.

Що він робить:
   віддає сторінки сайту з папки static/
   приймає картінки на POST /upload, перевіряє їх і зберігає
   віддає список завантажених файлів на GET /api/images
   записує всі дії в лог app.log

Зі сторонніх бібліотек тут тільки Pillow, все інше дефолтні.
"""

import json
import logging
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

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

# Сторінки сайту маршрут і файл у static/, який на ньому показуємо
PAGES = {
    "/": "index.html",
    "/upload": "upload.html",
    "/gallery": "images.html",
    "/images/": "images.html",  
}

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


# HTTP обробник

class ImageServerHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    head_only = False  # вмикаємо на час HEAD, щоб віддати лише заголовки

    # Дрібні методи, якими відповідаємо клієнту

    def _send(self, code, body, content_type):
        """Відправляє відповідь. Content-Length ставимо завжди."""
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        # Сторінки і JSON просимо не кешувати, інакше після оновлення сайту
        # браузер ще довго показує стару версію
        if content_type.startswith(("text/html", "application/json")):
            self.send_header("Cache-Control", "no-cache")
        if code not in (204, 304):
            self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not self.head_only:
            self.wfile.write(body)

    def send_json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self._send(code, body, "application/json; charset=utf-8")

    def send_file(self, base_dir, name, not_found_message):
        """Віддає файл з base_dir і не дає вийти за межі цієї папки."""
        path = resolve_inside(base_dir, name)
        if path is None:
            self.send_json(404, {"error": not_found_message})
            return
        with open(path, "rb") as f:
            body = f.read()
        self._send(200, body, content_type_for(path))

    # Маршрути GET і HEAD

    def do_GET(self):
        path = urlparse(self.path).path

        if path in PAGES:
            self.send_file(STATIC_DIR, PAGES[path], "сторінку не знайдено")
        elif path.startswith(("/css/", "/js/", "/img/")):
            self.send_file(STATIC_DIR, path, "файл не знайдено")
        elif path == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
        else:
            self.send_json(404, {"error": "сторінку не знайдено"})

    def do_HEAD(self):
        """Ті самі маршрути, що і GET, тільки без тіла відповіді."""
        self.head_only = True
        try:
            self.do_GET()
        finally:
            self.head_only = False

    def log_message(self, format, *args):
        # У нас є свій мега лог, а стандартний http.server тільки заважає
        pass


def main():
    server = ThreadingHTTPServer((HOST, PORT), ImageServerHandler)
    logger.info("Сервер запущено на http://%s:%s", HOST, PORT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Сервер зупинено")
        server.shutdown()


if __name__ == "__main__":
    main()
