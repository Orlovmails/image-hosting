"""
Сервер зображень на Python.

Що він робить:
   віддає сторінки сайту з папки static/
   приймає картінки на POST /upload, перевіряє їх і зберігає
   віддає список завантажених файлів на GET /api/images
   записує всі дії в лог app.log

Зі сторонніх бібліотек тут тільки Pillow, все інше дефолтні.
"""

import os

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
