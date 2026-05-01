(config) => {
    return new Promise((resolve) => {
        const poll = (tries) => {
            const ta = document.querySelector(config.textareaSel);
            if (!ta) {
                if (tries > 0) return setTimeout(() => poll(tries - 1), 500);
                return resolve({ok: false, reason: "textarea_not_found"});
            }

            // Set value and trigger React's onChange
            const nativeSetter = Object.getOwnPropertyDescriptor(
                HTMLTextAreaElement.prototype, 'value'
            ).set;
            nativeSetter.call(ta, config.message);
            ta.dispatchEvent(new Event('input', {bubbles: true}));
            ta.dispatchEvent(new Event('change', {bubbles: true}));

            // Wait for submit button to enable
            const waitBtn = (btnTries) => {
                const btn = document.querySelector(config.submitSel);
                if (!btn) {
                    if (btnTries > 0) return setTimeout(() => waitBtn(btnTries - 1), 500);
                    return resolve({ok: false, reason: "submit_not_found"});
                }
                if (btn.disabled) {
                    if (btnTries > 0) return setTimeout(() => waitBtn(btnTries - 1), 500);
                    return resolve({ok: false, reason: "submit_stayed_disabled"});
                }

                btn.click();

                // Wait for form to change (textarea disappears or DOM changes)
                const checkDone = (doneTries) => {
                    const ta2 = document.querySelector(config.textareaSel);
                    if (!ta2 || !ta2.offsetParent) return resolve({ok: true});
                    if (doneTries > 0) return setTimeout(() => checkDone(doneTries - 1), 500);
                    resolve({ok: false, reason: "form_unchanged"});
                };
                setTimeout(() => checkDone(20), 1000);
            };
            setTimeout(() => waitBtn(10), 1000);
        };
        poll(20);
    });
}
