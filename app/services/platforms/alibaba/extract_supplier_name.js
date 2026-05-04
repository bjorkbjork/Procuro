() => {
    const nameEl = document.querySelector('.contact-name');
    const name = nameEl ? nameEl.getAttribute('title') || nameEl.textContent.trim() : 'Unknown';
    const companyEl = document.querySelector('.contact-company');
    const company = companyEl ? companyEl.textContent.trim() : '';
    return { name, company };
}
