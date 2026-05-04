() => {
    const cards = document.querySelectorAll('.message-item-wrapper.item-right-card');
    for (const card of cards) {
        const actionEl = card.querySelector('[data-dx-event-datasource]');
        if (!actionEl) continue;
        try {
            const ds = JSON.parse(actionEl.getAttribute('data-dx-event-datasource'));
            const url = ds.actionParams && ds.actionParams.url;
            if (url && url.includes('alibaba.com/product-detail/')) {
                return url;
            }
        } catch { /* malformed JSON */ }
    }
    return null;
}
