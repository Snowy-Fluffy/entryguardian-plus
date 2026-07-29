(function () {
    'use strict';

    var _params = new URLSearchParams(document.location.search);
    var _uuid      = _params.get('uuid')      || '';
    var _challenge = _params.get('challenge') || '';

    const PALETTE = [
        '#00d4ff','#ffd600','#b347d9','#3fb950','#f85149','#f0883e','#58a6ff',
        '#e879f9','#06b6d4','#fb923c','#a78bfa','#4ade80','#f472b6','#fbbf24',
        '#34d399','#f87171','#c084fc','#38bdf8','#fb7185','#22d3ee','#a3e635',
    ];

    const CELL = 18;
    const BOARD_CELLS = 15;
    const BOARD_PX = BOARD_CELLS * CELL + 2;
    const PIECE_SIZE = 16;

    /* ── Utility ── */
    function shuffle(a) {
        for (let i = a.length - 1; i > 0; i--) {
            const j = Math.floor(Math.random() * (i + 1));
            [a[i], a[j]] = [a[j], a[i]];
        }
        return a;
    }

    function pieceBounds(cells) {
        let mx = 0, my = 0;
        for (const [x, y] of cells) { mx = Math.max(mx, x); my = Math.max(my, y); }
        return { w: mx + 1, h: my + 1 };
    }

    function normalizeCells(cells) {
        let minX = Infinity, minY = Infinity;
        for (const [x, y] of cells) { minX = Math.min(minX, x); minY = Math.min(minY, y); }
        return cells.map(([x, y]) => [x - minX, y - minY]).sort((a, b) => a[0] - b[0] || a[1] - b[1]);
    }

    function cellsEqual(a, b) {
        if (a.length !== b.length) return false;
        const na = normalizeCells(a), nb = normalizeCells(b);
        return na.every((c, i) => c[0] === nb[i][0] && c[1] === nb[i][1]);
    }

    function rotateCW(cells) {
        return normalizeCells(cells.map(([x, y]) => [-y, x]));
    }

    function rotateN(cells, n) {
        let c = cells;
        for (let i = 0; i < ((n % 4) + 4) % 4; i++) c = rotateCW(c);
        return c;
    }

    /* ── Procedural piece generation ── */
    function generatePolyomino(n) {
        const cells = [[0, 0]];
        const set = new Set(['0,0']);
        const dirs = [[1,0],[-1,0],[0,1],[0,-1]];
        let attempts = 0;
        while (cells.length < n && attempts < 500) {
            attempts++;
            const base = cells[Math.floor(Math.random() * cells.length)];
            const d = dirs[Math.floor(Math.random() * 4)];
            const nx = base[0] + d[0], ny = base[1] + d[1];
            const key = nx + ',' + ny;
            if (!set.has(key)) { cells.push([nx, ny]); set.add(key); }
        }
        return normalizeCells(cells);
    }

    function generateDistinctPiece(existing, sizeRange) {
        for (let attempt = 0; attempt < 150; attempt++) {
            const size = sizeRange[0] + Math.floor(Math.random() * (sizeRange[1] - sizeRange[0] + 1));
            const cells = generatePolyomino(size);
            const bounds = pieceBounds(cells);
            if (bounds.w === 1 || bounds.h === 1) { if (cells.length > 3) continue; }
            let isDup = false;
            for (const ex of existing) {
                for (let r = 0; r < 4; r++) {
                    if (cellsEqual(rotateN(cells, r), ex)) { isDup = true; break; }
                }
                if (isDup) break;
            }
            if (!isDup) return cells;
        }
        return generatePolyomino(sizeRange[0]);
    }

    function cellsOverlap(cells, offset, occupied) {
        for (const [cx, cy] of cells) {
            if (occupied.has((cx + offset.x) + ',' + (cy + offset.y))) return true;
        }
        return false;
    }

    /* ── Challenge generation ── */
    let challenge = null;
    let dragState = null;
    var _killPromises = [];

    function generateChallenge() {
        const numTargets = 3 + Math.floor(Math.random() * 2);
        const targets = [];
        const occupied = new Set();

        for (let t = 0; t < numTargets; t++) {
            const size = 7 + Math.floor(Math.random() * 4);
            let cells, bounds, offset, ok = false;
            for (let attempt = 0; attempt < 100; attempt++) {
                cells = rotateN(generatePolyomino(size), Math.floor(Math.random() * 4));
                bounds = pieceBounds(cells);
                if (bounds.w > BOARD_CELLS - 1 || bounds.h > BOARD_CELLS - 1) continue;
                offset = {
                    x: Math.floor(Math.random() * (BOARD_CELLS - bounds.w + 1)),
                    y: Math.floor(Math.random() * (BOARD_CELLS - bounds.h + 1))
                };
                if (!cellsOverlap(cells, offset, occupied)) { ok = true; break; }
            }
            if (!ok) continue;
            if (targets.some(tt => cellsEqual(tt.cells, cells))) continue;

            const color = PALETTE[(t * 7 + Math.floor(Math.random() * PALETTE.length)) % PALETTE.length];
            const displayRotation = 1 + Math.floor(Math.random() * 3);
            targets.push({ cells, color, offset, displayRotation });
            for (const [cx, cy] of cells) occupied.add((cx + offset.x) + ',' + (cy + offset.y));
        }

        const allTargetCells = targets.map(t => t.cells);
        const distractorCells = [];
        const numDistractors = 3 + Math.floor(Math.random() * 2);
        for (let i = 0; i < numDistractors; i++) {
            const avgSize = targets.length > 0 ? targets[0].cells.length : 6;
            distractorCells.push(generateDistinctPiece(
                [...allTargetCells, ...distractorCells],
                [Math.max(4, avgSize - 1), avgSize + 1]
            ));
        }

        const usedColors = new Set(targets.map(t => t.color));
        function pickColor() {
            for (let i = 0; i < 50; i++) {
                const c = PALETTE[Math.floor(Math.random() * PALETTE.length)];
                if (!usedColors.has(c)) { usedColors.add(c); return c; }
            }
            return PALETTE[Math.floor(Math.random() * PALETTE.length)];
        }

        const pieces = shuffle([
            ...targets.map((t, i) => ({
                cells: rotateN(t.cells, t.displayRotation),
                currentRotation: t.displayRotation,
                holeCells: t.cells,
                color: t.color,
                correct: true,
                targetIdx: i,
                placed: false,
            })),
            ...distractorCells.map(cells => ({
                cells: rotateN(cells, Math.floor(Math.random() * 4)),
                currentRotation: 0,
                color: pickColor(),
                correct: false,
                placed: false,
            })),
        ]);

        challenge = { targets, pieces, placedCount: 0 };
    }

    /* ── Drawing ── */
    function drawBoard() {
        const canvas = document.getElementById('boardCanvas');
        canvas.width = BOARD_PX;
        canvas.height = BOARD_PX;
        const ctx = canvas.getContext('2d');

        ctx.fillStyle = '#252836';
        ctx.fillRect(0, 0, BOARD_PX, BOARD_PX);

        ctx.strokeStyle = '#333750';
        ctx.lineWidth = 1;
        for (let i = 0; i <= BOARD_CELLS; i++) {
            const p = i * CELL + 1;
            ctx.beginPath(); ctx.moveTo(p, 1); ctx.lineTo(p, BOARD_PX - 1); ctx.stroke();
            ctx.beginPath(); ctx.moveTo(1, p); ctx.lineTo(BOARD_PX - 1, p); ctx.stroke();
        }

        const holeSet = new Set();
        for (const t of challenge.targets) {
            for (const [cx, cy] of t.cells) holeSet.add((cx + t.offset.x) + ',' + (cy + t.offset.y));
        }

        for (let y = 0; y < BOARD_CELLS; y++) {
            for (let x = 0; x < BOARD_CELLS; x++) {
                if (!holeSet.has(x + ',' + y)) {
                    ctx.fillStyle = '#3a3f55';
                    ctx.fillRect(x * CELL + 2, y * CELL + 2, CELL - 2, CELL - 2);
                }
            }
        }

        for (const t of challenge.targets) {
            for (const [cx, cy] of t.cells) {
                const bx = cx + t.offset.x, by = cy + t.offset.y;
                ctx.fillStyle = '#151821';
                ctx.fillRect(bx * CELL + 2, by * CELL + 2, CELL - 2, CELL - 2);
                ctx.strokeStyle = t.color + '40';
                ctx.lineWidth = 1.5;
                ctx.strokeRect(bx * CELL + 2, by * CELL + 2, CELL - 2, CELL - 2);
            }
        }

        for (const p of challenge.pieces) {
            if (p.placed && p.correct) {
                const t = challenge.targets[p.targetIdx];
                for (const [cx, cy] of t.cells) {
                    const bx = cx + t.offset.x, by = cy + t.offset.y;
                    ctx.fillStyle = t.color;
                    ctx.fillRect(bx * CELL + 2, by * CELL + 2, CELL - 2, CELL - 2);
                    ctx.fillStyle = 'rgba(255,255,255,0.18)';
                    ctx.fillRect(bx * CELL + 2, by * CELL + 2, CELL - 2, 3);
                    ctx.fillRect(bx * CELL + 2, by * CELL + 2, 3, CELL - 2);
                }
            }
        }

        ctx.strokeStyle = '#2e3147';
        ctx.lineWidth = 2;
        ctx.strokeRect(0, 0, BOARD_PX, BOARD_PX);
    }

    function drawPieceCanvas(canvas, cells, color, size) {
        const bounds = pieceBounds(cells);
        const cs = size || PIECE_SIZE;
        const pad = 3;
        canvas.width  = bounds.w * cs + pad * 2;
        canvas.height = bounds.h * cs + pad * 2;
        const ctx = canvas.getContext('2d');
        for (const [cx, cy] of cells) {
            const x = cx * cs + pad, y = cy * cs + pad;
            ctx.fillStyle = color;
            ctx.fillRect(x + 1, y + 1, cs - 2, cs - 2);
            ctx.fillStyle = 'rgba(255,255,255,0.2)';
            ctx.fillRect(x + 1, y + 1, cs - 2, 3);
            ctx.fillRect(x + 1, y + 1, 3, cs - 2);
            ctx.fillStyle = 'rgba(0,0,0,0.25)';
            ctx.fillRect(x + 1, y + cs - 3, cs - 2, 2);
            ctx.fillRect(x + cs - 3, y + 1, 2, cs - 2);
        }
    }

    /* ── Anti-bot ── */
    let mouseTrail = [];
    let captchaOpenTime = 0;
    let failCount = 0;
    const MAX_FAILS = 5;

    function recordMouse(e) {
        if (!challenge) return;
        mouseTrail.push({ x: e.clientX, y: e.clientY, t: Date.now() });
        if (mouseTrail.length > 200) mouseTrail.shift();
    }

    function recordTouch(e) {
        if (!challenge) return;
        const t = e.touches[0];
        if (!t) return;
        mouseTrail.push({ x: t.clientX, y: t.clientY, t: Date.now() });
        if (mouseTrail.length > 200) mouseTrail.shift();
    }

    function validateBehavior() {
        if (mouseTrail.length < 5) return false;
        if (Date.now() - captchaOpenTime < 1500) return false;
        let dirChanges = 0;
        for (let i = 2; i < mouseTrail.length; i++) {
            const dx1 = mouseTrail[i-1].x - mouseTrail[i-2].x;
            const dy1 = mouseTrail[i-1].y - mouseTrail[i-2].y;
            const dx2 = mouseTrail[i].x - mouseTrail[i-1].x;
            const dy2 = mouseTrail[i].y - mouseTrail[i-1].y;
            if ((dx1 * dy2 - dy1 * dx2) !== 0) dirChanges++;
        }
        return dirChanges >= 3;
    }

    /* ── Render pieces ── */
    function updateProgress() {
        const hint = document.getElementById('captchaHint');
        if (!hint) return;
        const placed = challenge.placedCount;
        const total = challenge.targets.length;
        hint.textContent = placed > 0 && placed < total
            ? 'Размещено ' + placed + ' из ' + total
            : 'Поставьте фигуры на свои места';
    }

    function renderPieces() {
        const topPanel    = document.getElementById('piecesPanelTop');
        const bottomPanel = document.getElementById('piecesPanelBottom');
        topPanel.innerHTML = '';
        bottomPanel.innerHTML = '';

        const visible = challenge.pieces.map((p, i) => i).filter(i => !challenge.pieces[i].placed);
        const half = Math.ceil(visible.length / 2);

        visible.forEach((idx, i) => {
            const p = challenge.pieces[idx];
            const panel = i < half ? topPanel : bottomPanel;

            const wrap = document.createElement('div');
            wrap.className = 'piece-wrap';

            const c = document.createElement('canvas');
            c.className = 'piece-canvas';
            c.dataset.idx = idx;
            drawPieceCanvas(c, p.cells, p.color, PIECE_SIZE);

            const rotBtn = document.createElement('button');
            rotBtn.textContent = '↻';
            rotBtn.title = 'Повернуть';
            rotBtn.style.cssText = 'background:none;border:1px solid #2e3147;color:#8890b5;' +
                'border-radius:4px;padding:1px 5px;cursor:pointer;font-size:12px;line-height:1;';
            rotBtn.addEventListener('click', e => {
                e.stopPropagation();
                p.cells = rotateCW(p.cells);
                p.currentRotation = (p.currentRotation + 1) % 4;
                drawPieceCanvas(c, p.cells, p.color, PIECE_SIZE);
            });

            wrap.appendChild(c);
            wrap.appendChild(rotBtn);
            panel.appendChild(wrap);

            c.addEventListener('mousedown', e => startDrag(e, idx, c));
            c.addEventListener('touchstart', e => startDragTouch(e, idx, c), { passive: false });
        });
    }

    /* ── Drag & Drop ── */
    function startDrag(e, idx, srcCanvas) {
        if (!e.isTrusted) return;
        e.preventDefault();
        const piece = challenge.pieces[idx];
        if (piece.placed) return;
        const ghost = document.createElement('canvas');
        ghost.className = 'drag-ghost';
        drawPieceCanvas(ghost, piece.cells, piece.color, CELL);
        document.body.appendChild(ghost);
        srcCanvas.classList.add('dragging');
        dragState = { pieceIdx: idx, ghost, srcCanvas,
            offsetX: ghost.width / 2, offsetY: ghost.height / 2 };
        moveGhost(e.clientX, e.clientY);
        document.addEventListener('mousemove', onDragMove);
        document.addEventListener('mouseup', onDragEnd);
    }

    function startDragTouch(e, idx, srcCanvas) {
        if (!e.isTrusted) return;
        e.preventDefault();
        const piece = challenge.pieces[idx];
        if (piece.placed) return;
        const touch = e.touches[0];
        const ghost = document.createElement('canvas');
        ghost.className = 'drag-ghost';
        drawPieceCanvas(ghost, piece.cells, piece.color, CELL);
        document.body.appendChild(ghost);
        srcCanvas.classList.add('dragging');
        dragState = { pieceIdx: idx, ghost, srcCanvas,
            offsetX: ghost.width / 2, offsetY: ghost.height / 2 };
        moveGhost(touch.clientX, touch.clientY);
        document.addEventListener('touchmove', onDragMoveTouch, { passive: false });
        document.addEventListener('touchend', onDragEndTouch);
    }

    function moveGhost(cx, cy) {
        if (!dragState) return;
        dragState.ghost.style.left = (cx - dragState.offsetX) + 'px';
        dragState.ghost.style.top  = (cy - dragState.offsetY) + 'px';
    }

    function onDragMove(e) { moveGhost(e.clientX, e.clientY); }
    function onDragMoveTouch(e) { e.preventDefault(); moveGhost(e.touches[0].clientX, e.touches[0].clientY); }
    function onDragEnd(e) { finishDrag(e.clientX, e.clientY); }
    function onDragEndTouch(e) { finishDrag(e.changedTouches[0].clientX, e.changedTouches[0].clientY); }

    function finishDrag(cx, cy) {
        if (!dragState) return;
        const boardCanvas = document.getElementById('boardCanvas');
        const rect = boardCanvas.getBoundingClientRect();
        const piece = challenge.pieces[dragState.pieceIdx];
        const inBoard = cx >= rect.left && cx <= rect.right && cy >= rect.top && cy <= rect.bottom;
        cleanupDrag();
        if (!inBoard) return;

        if (piece.correct && cellsEqual(piece.cells, piece.holeCells)) {
            piece.placed = true;
            challenge.placedCount++;
            var kp = fetch('/api/captcha/' + _uuid + '/kill', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ challenge: _challenge })
            }).catch(function () {});
            _killPromises.push(kp);
            drawBoard();
            renderPieces();
            updateProgress();
            if (challenge.placedCount >= challenge.targets.length) {
                Promise.allSettled(_killPromises).then(onCaptchaSuccess);
            }
        } else {
            onCaptchaFail();
        }
    }

    function cleanupDrag() {
        if (!dragState) return;
        dragState.ghost.remove();
        if (dragState.srcCanvas) dragState.srcCanvas.classList.remove('dragging');
        document.removeEventListener('mousemove', onDragMove);
        document.removeEventListener('mouseup', onDragEnd);
        document.removeEventListener('touchmove', onDragMoveTouch);
        document.removeEventListener('touchend', onDragEndTouch);
        dragState = null;
    }

    /* ── Result handling ── */
    function onCaptchaSuccess() {
        const fb = document.getElementById('captchaFeedback');
        if (!validateBehavior()) {
            fb.textContent = '⚠ Подозрительное поведение. Попробуйте ещё раз.';
            fb.className = 'captcha-feedback fail';
            setTimeout(resetChallenge, 1500);
            return;
        }
        fb.textContent = '✓ Проверка пройдена! Получаем код…';
        fb.className = 'captcha-feedback ok';
        failCount = 0;

        fetch('/api/captcha/' + _uuid + '/complete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ challenge: _challenge })
        })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (data.ok) {
                // The wrapper page (not this iframe) shows the result — it owns the code image.
                window.parent.postMessage({ type: 'doom_complete', already_done: true }, window.location.origin);
            } else {
                fb.textContent = '✗ ' + (data.error || 'Ошибка сервера') + '. Попробуйте ещё раз.';
                fb.className = 'captcha-feedback fail';
                setTimeout(resetChallenge, 6000);
            }
        })
        .catch(function () {
            fb.textContent = '✗ Нет соединения. Попробуйте ещё раз.';
            fb.className = 'captcha-feedback fail';
            setTimeout(resetChallenge, 6000);
        });
    }

    function onCaptchaFail() {
        failCount++;
        const fb = document.getElementById('captchaFeedback');
        if (failCount >= MAX_FAILS) {
            fb.textContent = '✗ Слишком много ошибок. Попробуйте позже.';
            fb.className = 'captcha-feedback fail';
            setTimeout(function () { failCount = 0; resetChallenge(); }, 30000);
            return;
        }
        fb.textContent = '✗ Неверная фигура. Попробуйте ещё раз.';
        fb.className = 'captcha-feedback fail';
        setTimeout(resetChallenge, 800 + failCount * 300);
    }

    function resetChallenge() {
        _killPromises = [];
        generateChallenge();
        drawBoard();
        renderPieces();
        updateProgress();
        mouseTrail = [];
        captchaOpenTime = Date.now();
        const fb = document.getElementById('captchaFeedback');
        fb.textContent = '';
        fb.className = 'captcha-feedback';
    }

    /* ── Init ── */
    document.addEventListener('DOMContentLoaded', function () {
        generateChallenge();
        drawBoard();
        renderPieces();
        updateProgress();
        document.addEventListener('mousemove', recordMouse);
        document.addEventListener('touchmove', recordTouch, { passive: true });
        captchaOpenTime = Date.now();
    });

})();
