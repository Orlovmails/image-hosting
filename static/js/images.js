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
            <div class="file-col file-col-delete">Видалити</div>
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
                <div class="file-col file-col-delete">
                    <button class="delete-btn" data-name="${name}">Видалити</button>
                </div>
            `;
            list.appendChild(item);
        });

        container.appendChild(list);
        fileListWrapper.appendChild(container);
        addDeleteListeners();
    };

    // Видаляє файл на сервері через DELETE /api/images/ім'я і оновлює список
    const addDeleteListeners = () => {
        document.querySelectorAll('.delete-btn').forEach((button) => {
            button.addEventListener('click', async (event) => {
                const name = event.currentTarget.dataset.name;
                try {
                    const response = await fetch('/api/images/' + encodeURIComponent(name), {
                        method: 'DELETE',
                    });
                    if (!response.ok) {
                        throw new Error(`сервер відповів ${response.status}`);
                    }
                    loadFiles();
                } catch (err) {
                    console.error('Не вдалося видалити файл:', err);
                }
            });
        });
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
