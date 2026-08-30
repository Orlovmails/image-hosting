const allImgBloks = document.querySelectorAll('.hero__img');
const randomIndex = Math.floor(Math.random() * allImgBloks.length);
const randomBlock = allImgBloks[randomIndex];
if (randomBlock) {
    randomBlock.classList.add('is-visible');
}

document.body.style.setProperty('background-color', '#151515');

document.addEventListener('DOMContentLoaded', function () {
    // Кнопки в шапці ведуть туди, що вказано в data-href: /upload і /images/
    document.querySelectorAll('.header__button-btn[data-href]').forEach(function (button) {
        button.addEventListener('click', function () {
            window.location.href = button.dataset.href;
        });
    });
});
