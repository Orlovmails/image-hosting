# Спочатку окремий етап збірки, щоб зайві інструменти встановлення
# не потрапили у фінальний образ.
FROM python:3.12-slim AS builder

WORKDIR /app
COPY requirements.txt .
# Ставимо залежності в окрему папку /install, звідки потім їх скопіюємо
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Фінальний образ
FROM python:3.12-slim

# Номери юзера і групи можна підмінити під власника на хості,
# docker compose build --build-arg UID=$(id -u) --build-arg GID=$(id -g)
# Для іменованих томів згодиться і типова 1000, а от для bind-mount збіг важливий.
ARG UID=1000
ARG GID=1000

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

# Анпривілегед юзер.
# Каталоги в образі і одразу віддаємо appuser, докер
# створює пустий том і переносить власника з цього шляху на сам том,
# і апка пише туди вже не від root.
RUN groupadd --gid ${GID} appuser && \
    useradd --uid ${UID} --gid ${GID} --create-home appuser && \
    mkdir -p /images /logs && \
    chown -R appuser:appuser /app /images /logs

USER appuser

EXPOSE 8000

CMD ["python", "app.py"]
