// Verified from html_test_fixtures/gs_chat_rendered.html
// Extracts conversation list from the GS chat sidebar.
// Conversations are in a scrollable panel adjacent to .tool-tabs.
// Each item contains: contact name (.font-700), time (<p>), company, preview.
() => {
    const conversations = [];
    const tabs = document.querySelector('.tool-tabs');
    if (!tabs) return conversations;
    const panel = tabs.parentElement;
    if (!panel) return conversations;

    const items = panel.querySelectorAll('.cursor-pointer');
    for (const item of items) {
        const nameEl = item.querySelector('.font-700');
        if (!nameEl) continue;
        const name = nameEl.textContent.trim();
        if (!name) continue;

        const timeEl = nameEl.parentElement
            ? nameEl.parentElement.querySelector('p')
            : null;
        const time = timeEl ? timeEl.textContent.trim() : '';

        const textContainer = nameEl.parentElement
            ? nameEl.parentElement.parentElement
            : null;
        let company = '';
        let preview = '';
        if (textContainer) {
            const rows = textContainer.querySelectorAll(':scope > div');
            if (rows.length > 1) company = rows[1].textContent.trim();
            if (rows.length > 2) preview = rows[2].textContent.trim();
        }

        conversations.push({ name, company, time, preview });
    }
    return conversations;
}
