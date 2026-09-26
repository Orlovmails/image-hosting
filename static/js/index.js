const allImgBloks = document.querySelectorAll('.hero__img');
const randomIndex = Math.floor(Math.random() * allImgBloks.length);
const randomBlock = allImgBloks[randomIndex];
if (randomBlock) {
    randomBlock.classList.add('is-visible');
}

document.body.style.setProperty('background-color', '#151515');
