"""
Тести бекенду на unittest. Запускати з кореня проєкту:
    python -m unittest discover -s tests

Перед імпортом app підставляємо тимчасові папки для картинок і логів,
щоб тести не чіпали робочі дані.
"""

import io
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

# Щоб можна було зробити import app, додаємо корінь проєкту в шляхи пошуку
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_TMP = tempfile.mkdtemp()
os.environ["IMAGES_DIR"] = os.path.join(_TMP, "images")
os.environ["LOGS_DIR"] = os.path.join(_TMP, "logs")

import app  # noqa: E402 імпортуємо саме тут, після підстановки папок
from PIL import Image  # noqa: E402

_server = None
_base_url = None


def setUpModule():
    global _server, _base_url
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




class PageTests(unittest.TestCase):
    def test_home_page(self):
        code, body = http_get("/")
        self.assertEqual(code, 200)
        self.assertIn(b"<html", body.lower())

    def test_upload_page(self):
        code, body = http_get("/upload")
        self.assertEqual(code, 200)
        self.assertIn(b"<html", body.lower())

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


if __name__ == "__main__":
    unittest.main()
