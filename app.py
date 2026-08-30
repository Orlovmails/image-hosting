"""
Сервер зображень на Python.

Що він робить:
   віддає сторінки сайту з папки static/
   приймає картінки на POST /upload, перевіряє їх і зберігає
   віддає список завантажених файлів на GET /api/images
   записує всі дії в лог app.log

Зі сторонніх бібліотек тут тільки Pillow, все інше дефолтні.
"""

import io
import json
import logging
import os
import re
import sys
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, unquote

from PIL import Image

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


# Розбираємо multipart руками
def extract_file_data(handler):
    """
    Дістає з тіла запиту байти файлу і його початкове ім'я.
    Якщо розібрати не вийшло, повертає порожнє, і тоді бекенд віддає 400.
    """
    length = int(handler.headers.get("Content-Length", 0) or 0)
    body = handler.rfile.read(length)

    # boundary інколи приходить у лапках або з параметром після нього, тому чистимо
    header = handler.headers["Content-Type"].split("boundary=")[-1]
    boundary = header.split(";")[0].strip().strip('"').encode()

    # Самі дані починаються після порожнього рядка і йдуть до наступної межі
    start = body.find(b"\r\n\r\n") + 4
    end = body.find(b"\r\n--" + boundary, start)
    if end == -1:
        return b"", ""
    data = body[start:end]

    # Ім'я шукаємо тільки в заголовках частини, а не в байтах самої картінки
    match = re.search(rb'filename="([^"]+)"', body[:start])
    return data, match.group(1).decode() if match else ""


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
        elif path == "/api/images":
            self.handle_list_images()
        elif path.startswith(("/css/", "/js/", "/img/")):
            self.send_file(STATIC_DIR, path, "файл не знайдено")
        elif path.startswith("/images/"):
            # У Docker ці запити забирає Nginx. А коли запускаємо без Docker,
            # картинку віддає сам Python прямо з папки IMAGES_DIR.
            self.send_file(IMAGES_DIR, unquote(path[len("/images/"):]), "зображення не знайдено")
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

    def handle_list_images(self):
        """Віддає JSON зі списком імен картинок, найновіші йдуть першими."""
        try:
            entries = []
            for name in os.listdir(IMAGES_DIR):
                full = os.path.join(IMAGES_DIR, name)
                ext = os.path.splitext(name)[1].lower()
                if os.path.isfile(full) and ext in ALLOWED_EXTENSIONS:
                    entries.append((os.path.getmtime(full), name))
            entries.sort(reverse=True)
            self.send_json(200, [name for _, name in entries])
        except OSError:
            self.send_json(500, {"error": "не вдалося прочитати каталог зображень"})

    # Маршрут DELETE

    def do_DELETE(self):
        path = urlparse(self.path).path
        if path.startswith("/api/images/"):
            self.handle_delete_image(unquote(path[len("/api/images/"):]))
        else:
            self.send_json(404, {"error": "маршрут не знайдено"})

    def handle_delete_image(self, name):
        """Видаляє картинку з папки images. Ім'я перевіряємо, щоб не вилізти за межі."""
        path = resolve_inside(IMAGES_DIR, name)
        if path is None:
            log("Помилка", f"спроба видалити неіснуючий файл ({name})")
            self.send_json(404, {"error": "зображення не знайдено"})
            return

        try:
            os.remove(path)
        except OSError:
            log("Помилка", f"не вдалося видалити файл ({name})")
            self.send_json(500, {"error": "не вдалося видалити файл"})
            return

        deleted_name = os.path.basename(path)
        log("Успіх", f"зображення {deleted_name} видалено")
        self.send_json(200, {"deleted": deleted_name})

    # Маршрути POST

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if urlparse(self.path).path == "/upload":
            self.handle_upload(length)
        else:
            # Тіло дочитуємо навіть для 404, інакше клієнт побачить обрив з'єднання і дулю
            self._discard_body(length)
            self.send_json(404, {"error": "маршрут не знайдено"})

    def _discard_body(self, length, cap=HARD_BODY_LIMIT * 2):
        """
        Дочитує і викидає тіло запиту, щоб клієнт встиг його дописати
        і побачити нашу відповідь. Без цього при ранній відмові з'єднання
        рветься (Broken pipe) ще до того, як клієнт прочитає помилку.
        Дуже велике тіло не дочитуємо, просто закриваємо з'єднання.
        """
        remaining = min(length, cap)
        while remaining > 0:
            chunk = self.rfile.read(min(65536, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
        if length > cap:
            self.close_connection = True

    def handle_upload(self, length):
        content_type = self.headers.get("Content-Type", "")

        if "multipart/form-data" not in content_type or "boundary=" not in content_type:
            self._discard_body(length)
            log("Помилка", "некоректний запит завантаження (немає multipart/form-data)")
            self.send_json(400, {"error": "очікується multipart/form-data"})
            return

        if length <= 0:
            if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
                log("Помилка", "запит завантаження без Content-Length (chunked)")
                self.send_json(400, {"error": "потрібен заголовок Content-Length"})
                return
            log("Помилка", "порожній запит завантаження")
            self.send_json(400, {"error": "файл не надіслано"})
            return

        # Розмір бачимо ще із заголовка, але тіло однаково треба дочитати,
        # інакше замість відповіді 400 клієнт отримає обрив з'єднання і дулю
        if length > HARD_BODY_LIMIT:
            self._discard_body(length)
            log("Помилка", f"тіло запиту ({length} байт) перевищує ліміт розміру (5 МБ)")
            self.send_json(400, {"error": "файл більший за 5 МБ"})
            return

        data, original_name = extract_file_data(self)
        if not data or not original_name:
            log("Помилка", "файл не знайдено у запиті")
            self.send_json(400, {"error": "файл не надіслано"})
            return

        # Тепер перевіряємо розмір уже самого файлу
        if len(data) > MAX_FILE_SIZE:
            log("Помилка", f"файл завеликий ({original_name})")
            self.send_json(400, {"error": "файл більший за 5 МБ"})
            return

        # Дивимось на розширення
        if os.path.splitext(original_name)[1].lower() not in ALLOWED_EXTENSIONS:
            log("Помилка", f"непідтримуваний формат файлу ({original_name})")
            self.send_json(400, {"error": "непідтримуваний формат файлу"})
            return

        # Перевіряємо через Pillow, чи це справді картинка, а не фігня
        try:
            image = Image.open(io.BytesIO(data))
            image_format = image.format
            image.verify()
        except Exception:
            log("Помилка", f"файл не є коректним зображенням ({original_name})")
            self.send_json(400, {"error": "файл пошкоджено або це не зображення"})
            return

        if image_format not in FORMAT_TO_EXTENSION:
            log("Помилка", f"непідтримуваний формат зображення ({original_name})")
            self.send_json(400, {"error": "непідтримуваний формат файлу"})
            return

        # Розширення ставимо за справжнім форматом, щоб png не росказував що він jpg
        unique_name = uuid.uuid4().hex + FORMAT_TO_EXTENSION[image_format]

        try:
            with open(os.path.join(IMAGES_DIR, unique_name), "wb") as f:
                f.write(data)
        except OSError:
            log("Помилка", f"не вдалося зберегти файл ({original_name})")
            self.send_json(500, {"error": "не вдалося зберегти файл"})
            return

        log("Успіх", f"зображення {unique_name} завантажено")
        self.send_json(200, {"id": unique_name, "url": "/images/" + unique_name})

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
