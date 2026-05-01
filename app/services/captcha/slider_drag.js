(config) => {
    const handle = document.querySelector(config.handleSel);
    const track = document.querySelector(config.trackSel);
    if (!handle || !track) return false;

    const hRect = handle.getBoundingClientRect();
    const tRect = track.getBoundingClientRect();
    const startX = hRect.x + hRect.width / 2;
    const startY = hRect.y + hRect.height / 2;
    const endX = tRect.x + tRect.width - config.endOffset;

    // Bezier control points with vertical jitter
    const dx = endX - startX;
    const cp1x = startX + dx * (0.2 + Math.random() * 0.2);
    const cp1y = startY + (-8 + Math.random() * 6);
    const cp2x = startX + dx * (0.6 + Math.random() * 0.2);
    const cp2y = startY + (2 + Math.random() * 6);

    const N = config.steps;
    const points = [];
    for (let i = 0; i <= N; i++) {
        const t = i / N;
        const u = 1 - t;
        let x = u*u*u*startX + 3*u*u*t*cp1x + 3*u*t*t*cp2x + t*t*t*endX;
        let y = u*u*u*startY + 3*u*u*t*cp1y + 3*u*t*t*cp2y + t*t*t*startY;
        x += (Math.random() - 0.5) * 2;
        y += (Math.random() - 0.5) * 2;
        points.push({x, y});
    }

    const ev = (type, x, y) => new MouseEvent(type, {
        clientX: x, clientY: y, bubbles: true, cancelable: true
    });

    handle.dispatchEvent(ev('mousedown', startX, startY));

    const totalMs = config.durationMs;
    const stepMs = totalMs / N;
    let idx = 1;
    return new Promise(resolve => {
        const tick = () => {
            if (idx >= points.length) {
                handle.dispatchEvent(ev('mouseup', points[points.length-1].x, points[points.length-1].y));
                resolve(true);
                return;
            }
            const p = points[idx++];
            document.dispatchEvent(ev('mousemove', p.x, p.y));
            setTimeout(tick, stepMs + (Math.random() - 0.5) * stepMs * 0.4);
        };
        tick();
    });
}
