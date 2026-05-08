// Verified from html_test_fixtures/gs_chat_rendered.html
// Extracts supplier contact name and company from the active conversation.
// Contact name is in the chat header; company is in the right info panel.
() => {
    const nameEl = document.querySelector('.font-size-16.font-600');
    const name = nameEl ? nameEl.textContent.trim() : 'Unknown';

    const companyEl = document.querySelector('.font-size-18.font-500');
    const company = companyEl ? companyEl.textContent.trim() : '';

    return { name, company };
}
