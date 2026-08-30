document.addEventListener('DOMContentLoaded', () => {
    const fileListWrapper = document.getElementById('file-list-wrapper');
    const uploadRedirectButton = document.getElementById('upload-tab-btn');

    // Малює таблицю за списком імен файлів
    const renderFiles = (files) => {
        fileListWrapper.innerHTML = '';

        if (!files || files.length === 0) {
            fileListWrapper.innerHTML =
                '<p class="upload__promt" style="text-align: center; margin-top: 50px;">Ще немає завантажених зображень.</p>';
            return;
        }

        const container = document.createElement('div');
        container.className = 'file-list-container';

        const header = document.createElement('div');
        header.className = 'file-list-header';
        header.innerHTML = `
            <div class="file-col file-col-name">Файл</div>
            <div class="file-col file-col-url">Посилання</div>
            <div class="file-col file-col-delete">Перегляд</div>
        `;
        container.appendChild(header);

        const list = document.createElement('div');
        list.id = 'file-list';

        files.forEach((name) => {
            const url = '/images/' + name;
            const fullUrl = window.location.origin + url;
            const item = document.createElement('div');
            item.className = 'file-list-item';
            item.innerHTML = `
                <div class="file-col file-col-name">
                    <span class="file-icon"><img src="/img/icon/Group.png" alt="icon"></span>
                    <span class="file-name">${name}</span>
                </div>
                <div class="file-col file-col-url"><a href="${url}" target="_blank">${fullUrl}</a></div>
                <div class="file-col file-col-delete"><a href="${url}" target="_blank">Відкрити</a></div>
            `;
            list.appendChild(item);
        });

        container.appendChild(list);
        fileListWrapper.appendChild(container);
    };

    // Забираємо список імен з бекенду
    const loadFiles = async () => {
        try {
            const response = await fetch('/api/images');
            const files = await response.json();
            renderFiles(files);
        } catch (err) {
            fileListWrapper.innerHTML =
                '<p class="upload__promt" style="text-align: center; margin-top: 50px;">Не вдалося завантажити список зображень.</p>';
            console.error('Помилка завантаження списку:', err);
        }
    };

    // Кнопка повертає на сторінку завантаження
    if (uploadRedirectButton) {
        uploadRedirectButton.addEventListener('click', () => {
            window.location.href = '/upload';
        });
    }

    loadFiles();
});
