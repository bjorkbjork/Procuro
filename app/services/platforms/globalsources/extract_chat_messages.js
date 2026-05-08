// Verified from html_test_fixtures/gs_chat_rendered.html
// Extracts supplier messages from the active GS chat conversation.
// Our messages use flex-row-reverse (avatar right); supplier messages do not.
// FIXME: Supplier message bubble structure not yet verified from a real reply —
// only our sent inquiry is in the fixture. The text extraction below handles
// both .editor-box-style (inquiry cards) and plain innerText (regular bubbles).
() => {
    const messages = [];
    const container = document.querySelector('.msg-content');
    if (!container) return messages;

    const content = container.querySelector('.break-words');
    if (!content) return messages;

    let currentTime = null;
    const group = content.firstElementChild;
    if (!group) return messages;

    for (const el of group.children) {
        if (el.classList.contains('text-center')) {
            currentTime = el.textContent.trim();
            continue;
        }

        if (!el.classList.contains('flex-wrap') || !el.classList.contains('mb-16')) {
            continue;
        }

        // Our messages have flex-row-reverse — skip them
        if (el.classList.contains('flex-row-reverse')) continue;

        // Supplier message: avatar is first child, content is second
        const children = el.children;
        const contentDiv = children.length > 1 ? children[1] : children[0];
        if (!contentDiv) continue;

        const editorBox = contentDiv.querySelector('.editor-box-style');
        const text = editorBox
            ? editorBox.innerText.trim()
            : contentDiv.innerText.trim();

        if (text) {
            messages.push({ text, sent_at: currentTime });
        }
    }

    return messages;
}
