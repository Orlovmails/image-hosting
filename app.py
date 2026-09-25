"""
Сервер зображень на Python.

Що він робить:
   віддає сторінки сайту з папки static/
   приймає картінки на POST /upload, перевіряє їх і зберігає
   віддає список завантажених файлів на GET /api/images
   показує таблицю картінок з бази на GET /images-list
   видаляє картінку і запис про неї на POST /delete/<id>
   записує всі дії в лог app.log
   зберігає метадані картінок у PostgreSQL

Зі сторонніх бібліотек тут Pillow і psycopg2, все інше дефолтні.
"""

import html
import io
import json
import logging
import os
import re
import sys
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse, unquote

import psycopg2
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

# Підключення до PostgreSQL. У Docker хост db, це ім'я сервісу з compose.yaml.
# Без Docker підключаємось до бази на localhost.
DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_PORT = os.environ.get("DB_PORT", "5432")
DB_NAME = os.environ.get("DB_NAME", "images_db")
DB_USER = os.environ.get("DB_USER", "postgres")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "password")

# Обмеження на завантаження
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif"}
MAX_FILE_SIZE = 5 * 1024 * 1024                  # 5 МБ максимум для самого файлу
# Трохи більше за 5 МБ, бо крім самого файлу в тілі запиту є ще службові рядки.
# Таке саме число стоїть у nginx.conf (client_max_body_size 6m), щоб не було різнобою.
HARD_BODY_LIMIT = MAX_FILE_SIZE + 1024 * 1024

# Який формат картинки якому розширенню відповідає.
# Розширення беремо з реального вмісту, а не з імені файлу, так надійніше.
FORMAT_TO_EXTENSION = {"JPEG": ".jpg", "PNG": ".png", "GIF": ".gif"}

# Скільки картинок показуємо на одній сторінці /images-list
PER_PAGE = 10

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


# База даних

def get_connection():
    """Нове з'єднання на кожну операцію, бо сервер багатопотоковий."""
    return psycopg2.connect(
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        host=DB_HOST,
        port=DB_PORT,
        connect_timeout=5,
    )


def test_connection():
    """Перевіряє базу при старті. Сервер запускається в будь-якому разі."""
    try:
        conn = get_connection()
        conn.close()
        log("Успіх", "з'єднання з базою даних успішне")
    except psycopg2.Error as error:
        log("Помилка", f"не вдалося підключитися до бази даних ({str(error).strip()})")


def save_metadata(filename, original_name, size, file_type):
    """Записує дані про збережену картинку в таблицю images і повертає id нового запису."""
    query = """
    INSERT INTO images (filename, original_name, size, file_type)
    VALUES (%s, %s, %s, %s)
    RETURNING id
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(query, (filename, original_name, size, file_type))
        image_id = cursor.fetchone()[0]
        conn.commit()
        return image_id
    finally:
        conn.close()


def get_images(page):
    """
    Одна сторінка записів з таблиці images, останні завантажені першими.
    Повертає рядки, номер сторінки і скільки всього сторінок.
    Якщо попросили сторінку за межами списку, віддаємо останню.
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM images")
        total = cursor.fetchone()[0]
        pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)
        page = min(page, pages)

        cursor.execute(
            "SELECT * FROM images ORDER BY upload_time DESC LIMIT %s OFFSET %s",
            (PER_PAGE, (page - 1) * PER_PAGE),
        )
        return cursor.fetchall(), page, pages
    finally:
        conn.close()


def delete_image_record(image_id):
    """Видаляє запис з images. Повертає ім'я файлу або None, якщо такого id немає."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM images WHERE id = %s RETURNING filename", (image_id,))
        row = cursor.fetchone()
        conn.commit()
        return row[0] if row else None
    finally:
        conn.close()


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


# Сторінка списку зображень

def render_images_table(rows, page=1):
    """
    Робить HTML-таблицю з рядків бази. Оригінальне ім'я прийшло від користувача, тому екрануємо.
    Номер сторінки передаємо у форму видалення, щоб після неї повернутись туди ж.
    """
    if not rows:
        return '<p class="images-list__empty">Немає завантажених зображень</p>'

    lines = []
    for image_id, filename, original_name, size, upload_time, file_type in rows:
        name = html.escape(filename)
        lines.append(
            "<tr>"
            f'<td><a href="/images/{name}" target="_blank">{name}</a></td>'
            f"<td>{html.escape(original_name)}</td>"
            f"<td>{size / 1024:.1f}</td>"
            f"<td>{upload_time:%Y-%m-%d %H:%M:%S}</td>"
            f"<td>{html.escape(file_type)}</td>"
            f'<td><form method="post" action="/delete/{image_id}?page={page}">'
            '<button class="delete-btn" type="submit">Видалити</button>'
            "</form></td>"
            "</tr>"
        )

    return (
        '<table class="images-table">'
        "<thead><tr>"
        "<th>Назва файлу</th><th>Оригінальна назва</th><th>Розмір (КБ)</th>"
        "<th>Дата завантаження</th><th>Тип файлу</th><th>Дія</th>"
        "</tr></thead>"
        "<tbody>" + "".join(lines) + "</tbody>"
        "</table>"
    )


def render_pagination(page, pages):
    """Кнопки між сторінками. На краях списку кнопка вимкнена."""
    if page > 1:
        prev_btn = f'<a class="pagination__btn" href="/images-list?page={page - 1}">Попередня сторінка</a>'
    else:
        prev_btn = '<button class="pagination__btn" disabled>Попередня сторінка</button>'

    if page < pages:
        next_btn = f'<a class="pagination__btn" href="/images-list?page={page + 1}">Наступна сторінка</a>'
    else:
        next_btn = '<button class="pagination__btn" disabled>Наступна сторінка</button>'

    return (
        '<nav class="pagination">'
        f'{prev_btn}<span class="pagination__info">Сторінка {page} з {pages}</span>{next_btn}'
        "</nav>"
    )


def parse_page(query):
    """Номер сторінки з ?page=N. Все, що не є додатним числом, вважаємо першою сторінкою."""
    value = parse_qs(query).get("page", ["1"])[0]
    try:
        return max(1, int(value))
    except ValueError:
        return 1


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

    def send_page(self, code, template, content):
        """Підставляє готовий HTML у шаблон зі static/ на місце {{content}}."""
        with open(os.path.join(STATIC_DIR, template), encoding="utf-8") as f:
            page = f.read().replace("{{content}}", content)
        self._send(code, page.encode("utf-8"), "text/html; charset=utf-8")

    def redirect(self, location):
        """303 після POST, щоб браузер відкрив сторінку звичайним GET."""
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

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
        elif path == "/images-list":
            self.handle_images_list(parse_page(urlparse(self.path).query))
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

    def handle_images_list(self, page):
        """Сторінка з таблицею картінок з бази, по PER_PAGE на сторінку."""
        try:
            rows, page, pages = get_images(page)
        except psycopg2.Error as error:
            log("Помилка", f"не вдалося отримати список зображень з бази ({str(error).strip()})")
            message = '<p class="images-list__empty">Не вдалося отримати список зображень</p>'
            self.send_page(500, "images-list.html", message)
            return
        content = render_images_table(rows, page)
        if rows:
            content += render_pagination(page, pages)
        self.send_page(200, "images-list.html", content)

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
        url = urlparse(self.path)
        if url.path == "/upload":
            self.handle_upload(length)
        elif url.path.startswith("/delete/"):
            self._discard_body(length)
            self.handle_delete(url.path[len("/delete/"):], parse_page(url.query))
        else:
            # Тіло дочитуємо навіть для 404, інакше клієнт побачить обрив з'єднання і дулю
            self._discard_body(length)
            self.send_json(404, {"error": "маршрут не знайдено"})

    def handle_delete(self, image_id, page):
        """Видаляє запис з бази і сам файл, потім повертає на ту ж сторінку списку."""
        back_link = '<p class="images-list__empty"><a href="/images-list">Повернутися до списку</a></p>'

        if not image_id.isdigit():
            log("Помилка", f"спроба видалити зображення з некоректним id ({image_id})")
            message = '<p class="images-list__empty">Зображення не знайдено</p>'
            self.send_page(404, "images-list.html", message + back_link)
            return

        try:
            filename = delete_image_record(int(image_id))
        except psycopg2.Error as error:
            log("Помилка", f"не вдалося видалити запис id {image_id} з бази ({str(error).strip()})")
            message = '<p class="images-list__empty">Не вдалося видалити зображення</p>'
            self.send_page(500, "images-list.html", message + back_link)
            return

        if filename is None:
            log("Помилка", f"зображення з id {image_id} не знайдено в базі")
            message = f'<p class="images-list__empty">Зображення з id {image_id} не знайдено</p>'
            self.send_page(404, "images-list.html", message + back_link)
            return

        # Запис уже видалено, тож навіть якщо файлу немає, користувача повертаємо до списку
        try:
            os.remove(os.path.join(IMAGES_DIR, os.path.basename(filename)))
            log("Успіх", f"зображення {filename} (id {image_id}) видалено")
        except FileNotFoundError:
            log("Помилка", f"запис id {image_id} видалено, але файл {filename} відсутній на диску")
        except OSError as error:
            log("Помилка", f"запис id {image_id} видалено, але файл {filename} не вдалося видалити ({error})")

        self.redirect(f"/images-list?page={page}")

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
        file_path = os.path.join(IMAGES_DIR, unique_name)

        try:
            with open(file_path, "wb") as f:
                f.write(data)
        except OSError:
            log("Помилка", f"не вдалося зберегти файл ({original_name})")
            self.send_json(500, {"error": "не вдалося зберегти файл"})
            return

        # Файл без запису в базі нікому не потрібен, тому якщо база не відповіла, прибираємо його
        file_type = FORMAT_TO_EXTENSION[image_format].lstrip(".")
        try:
            image_id = save_metadata(unique_name, original_name, len(data), file_type)
        except psycopg2.Error as error:
            os.remove(file_path)
            log("Помилка", f"не вдалося зберегти метадані в базу ({original_name}): {str(error).strip()}")
            self.send_json(500, {"error": "не вдалося зберегти дані про файл"})
            return

        log("Успіх", f"зображення {unique_name} (id {image_id}) завантажено")
        self.send_json(200, {"id": unique_name, "url": "/images/" + unique_name})

    def log_message(self, format, *args):
        # У нас є свій мега лог, а стандартний http.server тільки заважає
        pass


def main():
    server = ThreadingHTTPServer((HOST, PORT), ImageServerHandler)
    logger.info("Сервер запущено на http://%s:%s", HOST, PORT)
    test_connection()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Сервер зупинено")
        server.shutdown()


if __name__ == "__main__":
    main()
