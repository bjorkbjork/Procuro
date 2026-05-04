() => {
    const messages = [];
    const items = document.querySelectorAll('.message-item-wrapper.item-left-text');
    for (const item of items) {
        const text = item.getAttribute('data-original');
        if (text && text.trim()) {
            let sendTime = null;
            try {
                const expinfo = JSON.parse(item.getAttribute('data-expinfo') || '{}');
                if (expinfo.sendTime) {
                    sendTime = new Date(expinfo.sendTime).toISOString();
                }
            } catch { /* malformed JSON */ }
            messages.push({ text: text.trim(), sent_at: sendTime });
        }
    }
    messages.reverse();
    return messages;
}
