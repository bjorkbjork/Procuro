// Verified from html_test_fixtures/gs_chat_rendered.html
// Extracts the product URL from an inquiry card in the chat messages.
// GS product URLs end with "p.htm" (e.g. /55-inch-smart-tv-1212888159p.htm).
() => {
    const container = document.querySelector('.msg-content');
    if (!container) return null;

    const links = container.querySelectorAll('a[target="_blank"]');
    for (const link of links) {
        const href = link.getAttribute('href');
        if (href && href.includes('globalsources.com/') && href.endsWith('p.htm')) {
            return href;
        }
    }
    return null;
}
