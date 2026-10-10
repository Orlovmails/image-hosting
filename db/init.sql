-- Postgres виконує цей файл сам, але тільки при першому старті, коли том db_data ще порожній
CREATE TABLE images (
    id SERIAL PRIMARY KEY,
    filename TEXT NOT NULL,
    original_name TEXT NOT NULL,
    size INTEGER NOT NULL,
    upload_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    file_type TEXT NOT NULL
);
