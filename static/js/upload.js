document.addEventListener('DOMContentLoaded', () => {
    const fileUpload = document.getElementById('file-upload');
    const imagesButton = document.getElementById('images-tab-btn');
    const dropzone = document.querySelector('.upload__dropzone');
    const currentUploadInput = document.querySelector('.upload__input');
    const copyButton = document.querySelector('.upload__copy');
    const fileListWrapper = document.getElementById('file-list-wrapper');

    // Ті самі обмеження, що й на бекенді. Тут перевіряємо просто для зручності
    const ALLOWED_TYPES = ['image/jpeg', 'image/png', 'image/gif'];
    const MAX_SIZE_BYTES = 5 * 1024 * 1024; // 5 МБ

    // Створюємо таблицю результатів, якщо її ще немає
    const ensureResultList = () => {
        let list = document.getElementById('file-list');
        if (list) {
            return list;
        }
        const container = document.createElement('div');
        container.className = 'file-list-container';

        const header = document.createElement('div');
        header.className = 'file-list-header';
        header.innerHTML = `
            <div class="file-col file-col-name">Файл</div>
            <div class="file-col file-col-url">Посилання</div>
            <div class="file-col file-col-delete">Статус</div>
        `;
        container.appendChild(header);

        list = document.createElement('div');
        list.id = 'file-list';
        container.appendChild(list);
        fileListWrapper.appendChild(container);
        return list;
    };

    // Додає в таблицю рядок з результатом: або успіх, або помилка
    const addResultRow = (fileName, url, errorText) => {
        const list = ensureResultList();
        const item = document.createElement('div');
        item.className = 'file-list-item';
        if (url) {
            const fullUrl = window.location.origin + url;
            item.innerHTML = `
                <div class="file-col file-col-name">
                    <span class="file-icon"><img src="/img/icon/Group.png" alt="icon"></span>
                    <span class="file-name">${fileName}</span>
                </div>
                <div class="file-col file-col-url"><a href="${url}" target="_blank">${fullUrl}</a></div>
                <div class="file-col file-col-delete">завантажено</div>
            `;
        } else {
            item.innerHTML = `
                <div class="file-col file-col-name">
                    <span class="file-name">${fileName}</span>
                </div>
                <div class="file-col file-col-url">-</div>
                <div class="file-col file-col-delete">✖ ${errorText}</div>
            `;
        }
        list.appendChild(item);
    };

    // Відправляє один файл на бекенд і повертає відповідь {id, url}
    const uploadFile = async (file) => {
        const formData = new FormData();
        formData.append('image', file, file.name);

        const response = await fetch('/upload', { method: 'POST', body: formData });
        let data = {};
        try {
            data = await response.json();
        } catch (e) {
            data = {};
        }
        if (!response.ok) {
            throw new Error(data.error || `Помилка сервера (${response.status})`);
        }
        return data; // { id, url }
    };

    // Обробляє по черзі всі обрані або перетягнуті файли
    const handleFiles = async (files) => {
        if (!files || files.length === 0) {
            return;
        }
        for (const file of files) {
            // Перевіряємо формат і розмір тут, щоб не гнати зайве на сервер
            if (!ALLOWED_TYPES.includes(file.type)) {
                addResultRow(file.name, null, 'непідтримуваний формат');
                continue;
            }
            if (file.size > MAX_SIZE_BYTES) {
                addResultRow(file.name, null, 'файл більший за 5 МБ');
                continue;
            }
            try {
                const result = await uploadFile(file);
                addResultRow(file.name, result.url, null);
                if (currentUploadInput) {
                    currentUploadInput.value = window.location.origin + result.url;
                }
            } catch (err) {
                addResultRow(file.name, null, err.message);
            }
        }
    };

    // Копіювання поточного посилання
    if (copyButton && currentUploadInput) {
        copyButton.addEventListener('click', () => {
            const textToCopy = currentUploadInput.value;
            if (textToCopy && textToCopy !== 'https://') {
                navigator.clipboard.writeText(textToCopy).then(() => {
                    copyButton.textContent = 'СКОПІЙОВАНО!';
                    setTimeout(() => { copyButton.textContent = 'КОПІЮВАТИ'; }, 2000);
                }).catch(err => console.error('Не вдалося скопіювати:', err));
            }
        });
    }

    // Перехід на список зображень
    if (imagesButton) {
        imagesButton.addEventListener('click', () => {
            window.location.href = '/images-list';
        });
    }

    // Повернення на головну сторінку
    const homeButton = document.getElementById('home-btn');
    if (homeButton) {
        homeButton.addEventListener('click', () => {
            window.location.href = '/';
        });
    }

    // Вибір файлу через діалог
    fileUpload.addEventListener('change', (event) => {
        handleFiles(event.target.files);
        event.target.value = '';
    });

    // Drag & drop
    ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
        dropzone.addEventListener(eventName, (e) => {
            e.preventDefault();
            e.stopPropagation();
        });
    });
    dropzone.addEventListener('drop', (event) => {
        handleFiles(event.dataTransfer.files);
    });
});
