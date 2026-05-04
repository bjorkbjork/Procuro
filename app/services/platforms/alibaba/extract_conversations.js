() => {
    const seen = new Set();
    const names = [];
    const allEls = document.querySelectorAll('*');
    for (const el of allEls) {
        const text = el.textContent.trim();
        if (/^\d{1,2}:\d{2}$/.test(text)) {
            const parent = el.parentElement;
            if (!parent) continue;
            const nameEl = parent.firstElementChild;
            if (nameEl && nameEl !== el) {
                const name = nameEl.textContent.trim();
                if (name && !seen.has(name) && name !== 'Inbox') {
                    seen.add(name);
                    names.push(name);
                }
            }
        }
    }
    return names;
}
