(config) => {
    return new Promise((resolve) => {
        const poll = (tries) => {
            const ta = document.querySelector(config.textareaSel);
            if (!ta) {
                if (tries > 0) return setTimeout(() => poll(tries - 1), 500);
                return resolve({ok: false, step: "fill", reason: "textarea_not_found"});
            }

            const nativeSetter = Object.getOwnPropertyDescriptor(
                HTMLTextAreaElement.prototype, 'value'
            ).set;
            nativeSetter.call(ta, config.message);
            ta.dispatchEvent(new Event('input', {bubbles: true}));
            ta.dispatchEvent(new Event('change', {bubbles: true}));

            const waitBtn = (btnTries) => {
                const btn = document.querySelector(config.submitSel);
                if (!btn) {
                    if (btnTries > 0) return setTimeout(() => waitBtn(btnTries - 1), 500);
                    return resolve({ok: false, step: "click", reason: "submit_not_found"});
                }
                if (btn.disabled) {
                    if (btnTries > 0) return setTimeout(() => waitBtn(btnTries - 1), 500);
                    return resolve({ok: false, step: "click", reason: "submit_stayed_disabled"});
                }
                btn.click();
                resolve({ok: true, step: "clicked"});
            };
            setTimeout(() => waitBtn(10), 1000);
        };
        poll(20);
    });
}
