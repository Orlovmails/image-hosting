"""
Тести бекенду на unittest. Запускати з кореня проєкту:
    python -m unittest discover -s tests

Перед імпортом app підставляємо тимчасові папки для картинок і логів,
щоб тести не чіпали робочі дані.
"""

import http.client
import io
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta
from http.server import ThreadingHTTPServer
from unittest import mock

# Щоб можна було зробити import app, додаємо корінь проєкту в шляхи пошуку
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_TMP = tempfile.mkdtemp()
os.environ["IMAGES_DIR"] = os.path.join(_TMP, "images")
os.environ["LOGS_DIR"] = os.path.join(_TMP, "logs")

import app  # noqa: E402 імпортуємо саме тут, після підстановки папок

# Справжні функції бази, до підміни. Потрібні в DatabaseQueryTests.
ORIGINAL_GET_IMAGES = app.get_images
ORIGINAL_DELETE_IMAGE_RECORD = app.delete_image_record
import psycopg2  # noqa: E402
from PIL import Image  # noqa: E402

_server = None
_base_url = None

# Фейкова база: справжнього Postgres у тестах немає, тому рядки таблиці images
# просто складаємо в список. Функції нижче підміняють ті, що ходять у базу.
fake_rows = []
_next_id = [1]


def fake_save_metadata(filename, original_name, size, file_type, upload_time=None):
    fake_rows.append({
        "id": _next_id[0],
        "filename": filename,
        "original_name": original_name,
        "size": size,
        "upload_time": upload_time or datetime.now(),
        "file_type": file_type,
    })
    _next_id[0] += 1


def fake_get_images(page):
    rows = sorted(fake_rows, key=lambda r: r["upload_time"], reverse=True)
    pages = max(1, (len(rows) + app.PER_PAGE - 1) // app.PER_PAGE)
    page = min(page, pages)
    start = (page - 1) * app.PER_PAGE
    result = [
        (r["id"], r["filename"], r["original_name"], r["size"], r["upload_time"], r["file_type"])
        for r in rows[start:start + app.PER_PAGE]
    ]
    return result, page, pages


def fake_delete_image_record(image_id):
    for row in fake_rows:
        if row["id"] == image_id:
            fake_rows.remove(row)
            return row["filename"]
    return None


def fill_fake_rows(count, with_files=False):
    """Кладе в фейкову базу count записів, кожен наступний на хвилину новіший."""
    fake_rows.clear()
    start = datetime(2025, 1, 24, 15, 30, 0)
    for i in range(count):
        filename = uuid.uuid4().hex + ".png"
        if with_files:
            with open(os.path.join(app.IMAGES_DIR, filename), "wb") as f:
                f.write(make_image("PNG"))
        fake_save_metadata(filename, f"pic_{i + 1}.png", 2048, "png", start + timedelta(minutes=i))


def setUpModule():
    global _server, _base_url
    app.save_metadata = fake_save_metadata
    app.get_images = fake_get_images
    app.delete_image_record = fake_delete_image_record
    _server = ThreadingHTTPServer(("127.0.0.1", 0), app.ImageServerHandler)
    port = _server.server_address[1]
    _base_url = f"http://127.0.0.1:{port}"
    threading.Thread(target=_server.serve_forever, daemon=True).start()


def tearDownModule():
    if _server is not None:
        _server.shutdown()


def make_image(fmt, size=(20, 20)):
    """Робить байти справжньої картинки потрібного формату."""
    buf = io.BytesIO()
    mode = "P" if fmt == "GIF" else "RGB"
    Image.new(mode, size, 0 if mode == "P" else (10, 120, 200)).save(buf, fmt)
    return buf.getvalue()


def post_upload(filename, data, content_type="application/octet-stream", boundary_header=None):
    """
    Надсилає multipart-запит на /upload.
    Через boundary_header можна підсунути незвичний запис межі
    (у лапках або з параметром), бо деякі клієнти шлють саме так.
    """
    boundary = "----unittestboundary"
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="image"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode()
    body = head + data + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        _base_url + "/upload",
        data=body,
        method="POST",
        headers={"Content-Type": "multipart/form-data; boundary=" + (boundary_header or boundary)},
    )
    try:
        resp = urllib.request.urlopen(req)
        return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def http_request(path, method="GET"):
    req = urllib.request.Request(_base_url + path, method=method)
    try:
        resp = urllib.request.urlopen(req)
        return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def http_get(path):
    return http_request(path)


def post_no_redirect(path):
    """POST без переходу за редиректом, щоб побачити сам 303 і Location."""
    conn = http.client.HTTPConnection(_base_url[len("http://"):])
    conn.request("POST", path, body=b"", headers={"Content-Length": "0"})
    resp = conn.getresponse()
    body = resp.read()
    conn.close()
    return resp.status, resp.getheader("Location"), body.decode("utf-8")


def last_log_line():
    with open(os.path.join(app.LOGS_DIR, "app.log"), encoding="utf-8") as f:
        return f.read().strip().splitlines()[-1]


def count_rows(page_html):
    return page_html.count("<tr><td>")


class UploadTests(unittest.TestCase):
    def test_valid_png(self):
        code, res = post_upload("pic.png", make_image("PNG"), "image/png")
        self.assertEqual(code, 200)
        self.assertTrue(res["url"].startswith("/images/"))
        self.assertTrue(res["url"].endswith(".png"))

    def test_valid_gif(self):
        code, res = post_upload("anim.gif", make_image("GIF"), "image/gif")
        self.assertEqual(code, 200)
        self.assertTrue(res["url"].endswith(".gif"))

    def test_valid_jpeg(self):
        code, res = post_upload("photo.jpg", make_image("JPEG"), "image/jpeg")
        self.assertEqual(code, 200)
        self.assertTrue(res["url"].endswith(".jpg"))

    def test_saved_image_is_served_and_matches(self):
        png = make_image("PNG")
        code, res = post_upload("pic.png", png, "image/png")
        self.assertEqual(code, 200)
        code, body = http_get(res["url"])
        self.assertEqual(code, 200)
        self.assertEqual(body, png)

    def test_boundary_in_quotes(self):
        """boundary у лапках теж правильний за RFC 2046, має прийматись."""
        code, res = post_upload(
            "pic.png", make_image("PNG"), "image/png",
            boundary_header='"----unittestboundary"',
        )
        self.assertEqual(code, 200)

    def test_boundary_with_extra_parameter(self):
        code, res = post_upload(
            "pic.png", make_image("PNG"), "image/png",
            boundary_header="----unittestboundary; charset=utf-8",
        )
        self.assertEqual(code, 200)

    def test_uploaded_appears_in_listing(self):
        code, res = post_upload("pic.png", make_image("PNG"), "image/png")
        name = res["id"]
        code, body = http_get("/api/images")
        self.assertEqual(code, 200)
        self.assertIn(name, json.loads(body))

    def test_reject_text_disguised_as_jpg(self):
        code, res = post_upload("fake.jpg", b"this is not an image", "image/jpeg")
        self.assertEqual(code, 400)

    def test_reject_unsupported_extension(self):
        code, res = post_upload("note.txt", make_image("PNG"), "text/plain")
        self.assertEqual(code, 400)
        self.assertIn("формат", res["error"])

    def test_reject_oversize(self):
        big = b"\x00" * (7 * 1024 * 1024)
        code, res = post_upload("big.png", big, "image/png")
        self.assertEqual(code, 400)

    def test_reject_non_multipart(self):
        req = urllib.request.Request(
            _base_url + "/upload", data=b"x", method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            code = urllib.request.urlopen(req).status
        except urllib.error.HTTPError as e:
            code = e.code
        self.assertEqual(code, 400)

    def test_unknown_post_route_404(self):
        req = urllib.request.Request(
            _base_url + "/nope", data=b"x" * 1024, method="POST",
            headers={"Content-Type": "multipart/form-data; boundary=zzz"},
        )
        try:
            code = urllib.request.urlopen(req).status
        except urllib.error.HTTPError as e:
            code = e.code
        self.assertEqual(code, 404)


class MetadataTests(unittest.TestCase):
    def upload_and_get_row(self, filename, data, content_type):
        code, res = post_upload(filename, data, content_type)
        self.assertEqual(code, 200)
        self.assertEqual(fake_rows[-1]["filename"], res["id"])
        return fake_rows[-1]

    def test_metadata_for_jpg_png_gif(self):
        for fmt, filename, file_type in (
            ("JPEG", "photo1.jpg", "jpg"),
            ("PNG", "diagram.png", "png"),
            ("GIF", "anim.gif", "gif"),
        ):
            data = make_image(fmt)
            row = self.upload_and_get_row(filename, data, "image/" + file_type)
            self.assertEqual(row["original_name"], filename)
            self.assertEqual(row["size"], len(data))
            self.assertEqual(row["file_type"], file_type)

    def test_metadata_for_big_file(self):
        # Шум погано стискається, тож png виходить близько 3 МБ
        image = Image.frombytes("RGB", (1000, 1000), os.urandom(3 * 1000 * 1000))
        buf = io.BytesIO()
        image.save(buf, "PNG")
        data = buf.getvalue()
        row = self.upload_and_get_row("big.png", data, "image/png")
        self.assertEqual(row["size"], len(data))

    def test_file_type_from_content_not_name(self):
        # png, названий як jpg, у базу потрапляє як png
        row = self.upload_and_get_row("fake_name.jpg", make_image("PNG"), "image/jpeg")
        self.assertEqual(row["file_type"], "png")
        self.assertTrue(row["filename"].endswith(".png"))

    def test_rejected_file_not_saved_to_db(self):
        count = len(fake_rows)
        code, _ = post_upload("fake.jpg", b"this is not an image", "image/jpeg")
        self.assertEqual(code, 400)
        self.assertEqual(len(fake_rows), count)

    def test_file_removed_when_db_fails(self):
        files_before = set(os.listdir(app.IMAGES_DIR))
        error = psycopg2.OperationalError("база не відповідає")
        with mock.patch.object(app, "save_metadata", side_effect=error):
            code, res = post_upload("pic.png", make_image("PNG"), "image/png")
        self.assertEqual(code, 500)
        self.assertIn("error", res)
        self.assertEqual(set(os.listdir(app.IMAGES_DIR)), files_before)

    def test_db_error_is_logged(self):
        error = psycopg2.OperationalError("база не відповідає")
        with mock.patch.object(app, "save_metadata", side_effect=error):
            post_upload("logged.png", make_image("PNG"), "image/png")
        with open(os.path.join(app.LOGS_DIR, "app.log"), encoding="utf-8") as f:
            last_line = f.read().strip().splitlines()[-1]
        self.assertIn("Помилка", last_line)
        self.assertIn("logged.png", last_line)
        self.assertIn("база не відповідає", last_line)


class FakeHandler:
    """Проста підміна HTTP-обробника, у якій є тільки заголовки і тіло запиту."""

    def __init__(self, body, boundary_header="XYZ", with_length=True):
        self.headers = {"Content-Type": f"multipart/form-data; boundary={boundary_header}"}
        if with_length:
            self.headers["Content-Length"] = str(len(body))
        self.rfile = io.BytesIO(body)


def multipart_body(data=b"BYTES", filename="a.png", boundary="XYZ", extra_field=False):
    """Збирає тіло multipart-запиту приблизно так, як це робить браузер."""
    body = b""
    if extra_field:
        body += (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="comment"\r\n\r\n'
            "текст\r\n"
        ).encode()
    body += (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="image"; filename="{filename}"\r\n'
        f"Content-Type: image/png\r\n\r\n"
    ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
    return body


class ExtractFileDataTests(unittest.TestCase):
    def test_extracts_data_and_filename(self):
        data, name = app.extract_file_data(FakeHandler(multipart_body()))
        self.assertEqual(data, b"BYTES")
        self.assertEqual(name, "a.png")

    def test_keeps_binary_data_with_crlf_inside(self):
        """Якщо всередині даних є переноси рядків, вони не повинні обрізатись."""
        payload = b"line1\r\nline2\r\n"
        data, _ = app.extract_file_data(FakeHandler(multipart_body(data=payload)))
        self.assertEqual(data, payload)

    def test_boundary_in_quotes(self):
        """boundary у лапках теж правильний запис за RFC 2046."""
        handler = FakeHandler(multipart_body(), boundary_header='"XYZ"')
        data, name = app.extract_file_data(handler)
        self.assertEqual(data, b"BYTES")
        self.assertEqual(name, "a.png")

    def test_boundary_with_extra_parameter(self):
        handler = FakeHandler(multipart_body(), boundary_header="XYZ; charset=utf-8")
        data, _ = app.extract_file_data(handler)
        self.assertEqual(data, b"BYTES")

    def test_missing_content_length_returns_empty(self):
        """Без Content-Length функція має повернути порожньо, а не впасти з помилкою."""
        handler = FakeHandler(multipart_body(), with_length=False)
        self.assertEqual(app.extract_file_data(handler), (b"", ""))

    def test_broken_body_returns_empty(self):
        """Якщо межу не знайшли, краще віддати порожньо, ніж обрізані дані."""
        handler = FakeHandler("--XYZ\r\nбез заголовків і межі".encode())
        self.assertEqual(app.extract_file_data(handler), (b"", ""))




class DeleteTests(unittest.TestCase):
    def test_delete_removes_file(self):
        code, res = post_upload("pic.png", make_image("PNG"), "image/png")
        name = res["id"]

        code, body = http_request("/api/images/" + name, method="DELETE")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["deleted"], name)

        # файл більше не віддається і зник зі списку
        self.assertEqual(http_get("/images/" + name)[0], 404)
        self.assertNotIn(name, json.loads(http_get("/api/images")[1]))

    def test_delete_missing_file_returns_404(self):
        code, _ = http_request("/api/images/nemaje.png", method="DELETE")
        self.assertEqual(code, 404)

    def test_delete_cannot_escape_images_dir(self):
        """Спробу видалити файл за межами папки з картинками треба відхиляти."""
        outside = os.path.join(os.environ["LOGS_DIR"], "app.log")
        code, _ = http_request("/api/images/..%2Flogs%2Fapp.log", method="DELETE")
        self.assertEqual(code, 404)
        self.assertTrue(os.path.exists(outside))

    def test_delete_unknown_route_returns_404(self):
        code, _ = http_request("/api/nope/x.png", method="DELETE")
        self.assertEqual(code, 404)


class PageTests(unittest.TestCase):
    def test_home_page(self):
        code, body = http_get("/")
        self.assertEqual(code, 200)
        self.assertIn(b"<html", body.lower())

    def test_upload_page(self):
        code, body = http_get("/upload")
        self.assertEqual(code, 200)
        self.assertIn(b"<html", body.lower())

    def test_home_page_links_to_upload_and_catalog(self):
        """За ТЗ на головній мають бути посилання на /upload і на каталог /images/."""
        code, body = http_get("/")
        self.assertIn(b'data-href="/upload"', body)
        self.assertIn(b'data-href="/images/"', body)

    def test_catalog_page_at_images_slash(self):
        """За ТЗ каталог зображень має відкриватись за адресою /images/."""
        code, body = http_get("/images/")
        self.assertEqual(code, 200)
        self.assertIn(b"<html", body.lower())

    def test_head_returns_headers_without_body(self):
        code, body = http_request("/", method="HEAD")
        self.assertEqual(code, 200)
        self.assertEqual(body, b"")

    def test_unknown_route_404(self):
        code, _ = http_get("/does-not-exist")
        self.assertEqual(code, 404)


class StaticFileTests(unittest.TestCase):
    def test_stylesheet_is_served(self):
        code, body = http_get("/css/style.css")
        self.assertEqual(code, 200)
        self.assertIn(b"body", body)

    def test_cannot_escape_static_directory(self):
        """Шлях з ../ не повинен віддавати файли за межами папки static/."""
        code, body = http_get("/css/../app.py")
        self.assertEqual(code, 404)
        self.assertNotIn(b"BASE_DIR", body)


class ImagesListTests(unittest.TestCase):
    def get_page(self, query=""):
        code, body = http_get("/images-list" + query)
        self.assertEqual(code, 200)
        return body.decode("utf-8")

    def test_empty_list_message(self):
        fill_fake_rows(0)
        page = self.get_page()
        self.assertIn("Немає завантажених зображень", page)
        self.assertNotIn("<table", page)
        self.assertNotIn("pagination", page)

    def test_row_has_all_columns_and_link(self):
        fill_fake_rows(1)
        row = fake_rows[0]
        page = self.get_page()
        self.assertIn(f'href="/images/{row["filename"]}"', page)
        self.assertIn("pic_1.png", page)
        self.assertIn("<td>2.0</td>", page)
        self.assertIn("2025-01-24 15:30:00", page)
        self.assertIn("<td>png</td>", page)

    def test_newest_first(self):
        fill_fake_rows(3)
        page = self.get_page()
        self.assertLess(page.index("pic_3.png"), page.index("pic_2.png"))
        self.assertLess(page.index("pic_2.png"), page.index("pic_1.png"))

    def test_original_name_is_escaped(self):
        fill_fake_rows(1)
        fake_rows[0]["original_name"] = "<script>alert(1)</script>.png"
        page = self.get_page()
        self.assertIn("&lt;script&gt;", page)
        self.assertNotIn("<script>alert", page)

    def test_ten_records_fit_one_page(self):
        fill_fake_rows(10)
        page = self.get_page()
        self.assertEqual(count_rows(page), 10)
        self.assertIn("Сторінка 1 з 1", page)
        self.assertIn("disabled>Попередня сторінка", page)
        self.assertIn("disabled>Наступна сторінка", page)

    def test_eleven_records_make_two_pages(self):
        fill_fake_rows(11)
        first = self.get_page("?page=1")
        self.assertEqual(count_rows(first), 10)
        self.assertIn("disabled>Попередня сторінка", first)
        self.assertIn('href="/images-list?page=2"', first)

        second = self.get_page("?page=2")
        self.assertEqual(count_rows(second), 1)
        self.assertIn("pic_1.png", second)
        self.assertIn('href="/images-list?page=1"', second)
        self.assertIn("disabled>Наступна сторінка", second)

    def test_middle_page_has_both_buttons(self):
        fill_fake_rows(25)
        page = self.get_page("?page=2")
        self.assertIn("Сторінка 2 з 3", page)
        self.assertIn('href="/images-list?page=1"', page)
        self.assertIn('href="/images-list?page=3"', page)
        self.assertNotIn("disabled", page)

    def test_bad_page_values(self):
        fill_fake_rows(25)
        for query in ("?page=abc", "?page=0", "?page=-3", "?page="):
            self.assertIn("Сторінка 1 з 3", self.get_page(query), query)
        page = self.get_page("?page=999")
        self.assertIn("Сторінка 3 з 3", page)
        self.assertEqual(count_rows(page), 5)

    def test_rows_have_delete_buttons(self):
        fill_fake_rows(11)
        page = self.get_page("?page=2")
        row_id = fake_rows[0]["id"]
        self.assertIn(f'action="/delete/{row_id}?page=2"', page)
        self.assertIn(">Видалити</button>", page)

    def test_db_error_returns_500(self):
        error = psycopg2.OperationalError("база не відповідає")
        with mock.patch.object(app, "get_images", side_effect=error):
            code, body = http_get("/images-list")
        self.assertEqual(code, 500)
        self.assertIn("Не вдалося отримати список зображень", body.decode("utf-8"))
        self.assertIn("база не відповідає", last_log_line())


class FakeCursor:
    """Запам'ятовує SQL-запити і віддає наперед задані відповіді."""

    def __init__(self, fetchone_results):
        self.queries = []
        self.fetchone_results = list(fetchone_results)

    def execute(self, query, params=None):
        self.queries.append((" ".join(query.split()), params))

    def fetchone(self):
        return self.fetchone_results.pop(0)

    def fetchall(self):
        return []


class FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.committed = False
        self.closed = False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True


class DatabaseQueryTests(unittest.TestCase):
    """Справжні get_images і delete_image_record, тільки з фейковим з'єднанням."""

    def run_get_images(self, total, page):
        cursor = FakeCursor([(total,)])
        conn = FakeConnection(cursor)
        with mock.patch.object(app, "get_connection", return_value=conn):
            result = ORIGINAL_GET_IMAGES(page)
        self.assertTrue(conn.closed)
        return result, cursor.queries[-1]

    def test_uses_limit_and_offset(self):
        (rows, page, pages), (query, params) = self.run_get_images(25, 2)
        self.assertEqual(query, "SELECT * FROM images ORDER BY upload_time DESC LIMIT %s OFFSET %s")
        self.assertEqual(params, (10, 10))
        self.assertEqual((page, pages), (2, 3))

    def test_page_after_end_becomes_last(self):
        (rows, page, pages), (query, params) = self.run_get_images(25, 999)
        self.assertEqual(params, (10, 20))
        self.assertEqual((page, pages), (3, 3))

    def test_empty_table(self):
        (rows, page, pages), (query, params) = self.run_get_images(0, 1)
        self.assertEqual(params, (10, 0))
        self.assertEqual((page, pages), (1, 1))

    def run_delete(self, fetchone_result):
        cursor = FakeCursor([fetchone_result])
        conn = FakeConnection(cursor)
        with mock.patch.object(app, "get_connection", return_value=conn):
            result = ORIGINAL_DELETE_IMAGE_RECORD(7)
        self.assertTrue(conn.committed)
        self.assertTrue(conn.closed)
        self.assertEqual(cursor.queries[0], ("DELETE FROM images WHERE id = %s RETURNING filename", (7,)))
        return result

    def test_delete_returns_filename(self):
        self.assertEqual(self.run_delete(("abc.png",)), "abc.png")

    def test_delete_unknown_id_returns_none(self):
        self.assertIsNone(self.run_delete(None))


class DeleteByIdTests(unittest.TestCase):
    def setUp(self):
        fill_fake_rows(3, with_files=True)

    def test_delete_removes_record_and_file(self):
        row = fake_rows[0]
        path = os.path.join(app.IMAGES_DIR, row["filename"])
        code, location, _ = post_no_redirect(f"/delete/{row['id']}?page=2")
        self.assertEqual(code, 303)
        self.assertEqual(location, "/images-list?page=2")
        self.assertNotIn(row, fake_rows)
        self.assertFalse(os.path.exists(path))
        self.assertIn("Успіх", last_log_line())
        self.assertIn(row["filename"], last_log_line())

    def test_list_updated_after_delete(self):
        row = fake_rows[0]
        post_no_redirect(f"/delete/{row['id']}")
        page = http_get("/images-list")[1].decode("utf-8")
        self.assertNotIn(row["filename"], page)
        self.assertEqual(count_rows(page), 2)

    def test_unknown_id_returns_404(self):
        code, _, body = post_no_redirect("/delete/99999")
        self.assertEqual(code, 404)
        self.assertIn("Зображення з id 99999 не знайдено", body)
        self.assertEqual(len(fake_rows), 3)
        self.assertIn("Помилка", last_log_line())

    def test_bad_id_returns_404(self):
        code, _, _ = post_no_redirect("/delete/abc")
        self.assertEqual(code, 404)
        self.assertEqual(len(fake_rows), 3)

    def test_missing_file_is_logged(self):
        row = fake_rows[0]
        os.remove(os.path.join(app.IMAGES_DIR, row["filename"]))
        code, location, _ = post_no_redirect(f"/delete/{row['id']}")
        self.assertEqual(code, 303)
        self.assertNotIn(row, fake_rows)
        self.assertIn("відсутній на диску", last_log_line())

    def test_db_error_returns_500(self):
        row = fake_rows[0]
        error = psycopg2.OperationalError("база не відповідає")
        with mock.patch.object(app, "delete_image_record", side_effect=error):
            code, _, body = post_no_redirect(f"/delete/{row['id']}")
        self.assertEqual(code, 500)
        self.assertIn("Не вдалося видалити зображення", body)
        self.assertTrue(os.path.exists(os.path.join(app.IMAGES_DIR, row["filename"])))

    def test_get_does_not_delete(self):
        row = fake_rows[0]
        code, _ = http_get(f"/delete/{row['id']}")
        self.assertEqual(code, 404)
        self.assertIn(row, fake_rows)


class PathSafetyTests(unittest.TestCase):
    def test_parent_directory_traversal_blocked(self):
        code, _ = http_get("/images/..%2F..%2Fapp.py")
        self.assertEqual(code, 404)

    def test_sibling_directory_with_same_prefix_blocked(self):
        """Сусідня папка images_secret не повинна бути доступна."""
        sibling = os.environ["IMAGES_DIR"] + "_secret"
        os.makedirs(sibling, exist_ok=True)
        with open(os.path.join(sibling, "secret.png"), "wb") as f:
            f.write(b"TOP-SECRET")
        code, body = http_get("/images/..%2Fimages_secret%2Fsecret.png")
        self.assertEqual(code, 404)


if __name__ == "__main__":
    unittest.main()
