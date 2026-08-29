"""
Тести бекенду на unittest. Запускати з кореня проєкту:
    python -m unittest discover -s tests

Перед імпортом app підставляємо тимчасові папки для картинок і логів,
щоб тести не чіпали робочі дані.
"""

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


def http_request(path, method="GET"):
    req = urllib.request.Request(_base_url + path, method=method)
    try:
        resp = urllib.request.urlopen(req)
        return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def http_get(path):
    return http_request(path)


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
