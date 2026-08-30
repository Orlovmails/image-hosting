# Спочатку окремий етап збірки, щоб зайві інструменти встановлення
# не потрапили у фінальний образ.
FROM python:3.12-slim AS builder

WORKDIR /app
COPY requirements.txt .
# Ставимо залежності в окрему папку /install, звідки потім їх скопіюємо
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Фінальний образ
FROM python:3.12-slim

WORKDIR /app

# Переносимо тільки готові пакети, без кешу і компіляторів
COPY --from=builder /install /usr/local

# Копіюємо код і статичні файли сайту
COPY app.py .
COPY static ./static

# Шляхи до томів усередині контейнера
ENV IMAGES_DIR=/images \
    LOGS_DIR=/logs \
    PORT=8000 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

CMD ["python", "app.py"]
