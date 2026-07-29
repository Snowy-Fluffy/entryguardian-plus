# Entry Guardian - a Telegram bot that prevents spam bots from joining a group
# Copyright: 2026 thmunix and Luna River

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.

import asyncio
import ipaddress
import os
import re
import secrets
import time
from collections import deque
import aiohttp
from aiohttp import web
from altcha import Payload, create_challenge, verify_solution
from captcha.image import ImageCaptcha
import config
import session_manager
from dbmanager import DBManager

db_man = DBManager()

_ALTCHA_HMAC_SECRET = secrets.token_hex(32)
_ALTCHA_ALGORITHM = 'PBKDF2/SHA-512'
_ALTCHA_COST = 10000
_ALTCHA_CHALLENGE_TTL = 120

_TURNSTILE_VERIFY_URL = 'https://challenges.cloudflare.com/turnstile/v0/siteverify'

_image_captcha = ImageCaptcha(width=220, height=90)

DOOM_DIR = os.path.realpath(
    os.getenv('DOOM_DIR', os.path.dirname(os.path.abspath(__file__)))
)
_TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), 'templates', 'captcha_wrapper.html')

_ALLOWED_ASSET_EXTS = frozenset({
    '.html', '.js', '.css', '.ico', '.png', '.jpg', '.jpeg',
    '.gif', '.svg', '.webp', '.wav', '.mp3', '.ogg', '.woff', '.woff2', '.ttf',
})

_wrapper_template: str | None = None

_RATE_LIMITED_PREFIXES = ('/captcha/', '/api/captcha/')

_rate_buckets: dict[str, deque] = {}


def _is_valid_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def _client_ip(request: web.Request) -> str:
    """Resolve the visitor's real IP. Behind a reverse proxy (nginx, per the README setup) —
    and especially through Docker's published-port NAT — `request.remote` is only the proxy's
    (or Docker gateway's) own address, never the actual visitor's. Prefer the headers nginx
    sets from its real-ip-restored $remote_addr, falling back to the raw TCP peer only if
    proxy headers are disabled/absent/malformed."""
    if config.TRUST_PROXY_HEADERS:
        real_ip = (request.headers.get('X-Real-IP') or '').strip()
        if real_ip and _is_valid_ip(real_ip):
            return real_ip
        forwarded = request.headers.get('X-Forwarded-For', '')
        if forwarded:
            candidate = forwarded.split(',')[0].strip()
            if _is_valid_ip(candidate):
                return candidate
    return request.remote or 'unknown'


_UA_MAX_LEN = 300
_CONTROL_CHARS_RE = re.compile(r'[\x00-\x1f\x7f]')


def _sanitize_user_agent(raw: str) -> str:
    """Clean an untrusted User-Agent header before storing it. Parameterized SQL queries
    already make injection impossible regardless of content; this is about not persisting
    control characters (which could otherwise mangle logs/terminals) or unbounded garbage —
    it's still HTML-escaped again on display in /punl."""
    return _CONTROL_CHARS_RE.sub('', raw)[:_UA_MAX_LEN]


@web.middleware
async def rate_limit_middleware(request: web.Request, handler):
    """Simple sliding-window rate limit per IP on the captcha page/API, so a script hitting
    these endpoints directly (bypassing the minigame's own pacing) can't flood them."""
    if not request.path.startswith(_RATE_LIMITED_PREFIXES):
        return await handler(request)
    ip = _client_ip(request)
    now = time.time()
    bucket = _rate_buckets.setdefault(ip, deque())
    while bucket and now - bucket[0] > config.RATE_LIMIT_WINDOW:
        bucket.popleft()
    if len(bucket) >= config.RATE_LIMIT_MAX:
        return web.json_response({'error': 'rate limited'}, status=429)
    bucket.append(now)
    return await handler(request)


async def rate_limit_cleanup_task() -> None:
    """Drop rate-limit buckets that have gone quiet, so idle/rotating IPs don't accumulate
    in memory forever."""
    while True:
        await asyncio.sleep(300)
        now = time.time()
        for ip in list(_rate_buckets.keys()):
            bucket = _rate_buckets[ip]
            while bucket and now - bucket[0] > config.RATE_LIMIT_WINDOW:
                bucket.popleft()
            if not bucket:
                del _rate_buckets[ip]


def _get_wrapper_template() -> str:
    global _wrapper_template
    if _wrapper_template is None:
        with open(_TEMPLATE_PATH, encoding='utf-8') as f:
            _wrapper_template = f.read()
    return _wrapper_template


def _captcha_page_html(session_id: str, challenge: str, captcha_type: str) -> str:
    iframe_style = ''
    if captcha_type == 'tetris':
        iframe_src = f'/tetris/tetris_captcha.html?uuid={session_id}&challenge={challenge}'
        iframe_w, iframe_h = '380', '620'
        captcha_desc = f'Расставь {config.CAPTCHA_MIN_PIECES} фигуры по отмеченным клеткам'
    elif captcha_type == 'mario':
        iframe_src = f'/mario/mario_captcha.html?uuid={session_id}&challenge={challenge}'
        iframe_w, iframe_h = '100%', '580'
        iframe_style = 'max-width:560px'
        captcha_desc = 'Добеги до флагштока в конце уровня'
    else:
        iframe_src = (
            f'/doom/captcha.html?enemies={config.CAPTCHA_ENEMIES}'
            f'&uuid={session_id}&challenge={challenge}'
        )
        iframe_w, iframe_h = '300', '150'
        captcha_desc = f'Убей {config.CAPTCHA_ENEMIES} врагов, чтобы пройти проверку'
    return (
        _get_wrapper_template()
        .replace('__UUID__', session_id)
        .replace('__CHALLENGE__', challenge)
        .replace('__IFRAME_SRC__', iframe_src)
        .replace('__IFRAME_WIDTH__', iframe_w)
        .replace('__IFRAME_HEIGHT__', iframe_h)
        .replace('__IFRAME_STYLE__', iframe_style)
        .replace('__CAPTCHA_DESC__', captcha_desc)
        .replace('__TURNSTILE_SITE_KEY__', config.TURNSTILE_SITE_KEY)
        .replace('__TURNSTILE_ENABLED__', 'true' if config.TURNSTILE_ENABLED else 'false')
    )


_ERROR_HTML = (
    '<html><body style="background:#111;color:#eee;font-family:monospace;'
    'text-align:center;padding:40px">'
    '<h2 style="color:red">Капча не найдена или истекло время</h2>'
    '<p>Отправьте /start боту чтобы получить новую ссылку</p>'
    '</body></html>'
)

_COMPLETED_HTML_TMPL = (
    '<!DOCTYPE html><html lang="ru"><head><title>Captcha</title>'
    '<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">'
    '<style>'
    '* {{ box-sizing: border-box; margin: 0; padding: 0; }}'
    'body {{ background: #111; display: flex; flex-direction: column; align-items: center;'
    ' justify-content: center; min-height: 100vh; font-family: \'Courier New\', monospace;'
    ' color: #eee; gap: 16px; }}'
    'h2 {{ color: #ff4444; font-size: 1.3rem; letter-spacing: 2px; }}'
    '#result {{ text-align: center; padding: 24px 32px; background: #1a1a1a;'
    ' border: 2px solid #33aa33; border-radius: 4px; }}'
    '#result p {{ margin-bottom: 10px; font-size: 1rem; color: #ccc; }}'
    '#code-box {{ display: inline-block; background: #0a0a0a; border: 2px solid #33ff33;'
    ' border-radius: 10px; padding: 14px 18px; margin: 14px 0; max-width: 100%;'
    ' box-shadow: 0 0 18px rgba(51, 255, 51, 0.35), inset 0 0 24px rgba(51, 255, 51, 0.06); }}'
    '#code-box img {{ display: block; width: 220px; max-width: 100%; height: auto; border-radius: 5px; }}'
    '#hint {{ font-size: 0.85rem; color: #888; margin-top: 8px; }}'
    '</style></head><body>'
    '<h2>CAPTCHA</h2>'
    '<div id="result">'
    '<p>&#x2705; Капча пройдена!</p>'
    '<p>Отправьте этот код боту:</p>'
    '<div id="code-box"><img src="/api/captcha/{session_id}/code.png" alt="код"></div>'
    '<div id="hint">Введите код в чате с ботом для завершения верификации</div>'
    '</div></body></html>'
)


async def handle_captcha_page(request: web.Request) -> web.Response:
    session_id = request.match_info['uuid']
    session = session_manager.sessions.get(session_id)
    if not session or session_manager.is_expired(session_id):
        return web.Response(text=_ERROR_HTML, content_type='text/html', status=410)

    if config.COLLECT_CAPTCHA_IPS:
        db_man.record_first_captcha_visit(
            session['user_id'],
            _client_ip(request),
            _sanitize_user_agent(request.headers.get('User-Agent', '')),
            int(time.time()),
        )

    if session.get('completed') and session.get('code'):
        html = _COMPLETED_HTML_TMPL.format(session_id=session_id)
        return web.Response(text=html, content_type='text/html')
    challenge = session_manager.set_page_loaded(session_id)
    captcha_type = session.get('captcha_type', 'doom')
    return web.Response(text=_captcha_page_html(session_id, challenge, captcha_type), content_type='text/html')


async def handle_kill(request: web.Request) -> web.Response:
    session_id = request.match_info['uuid']
    try:
        data = await request.json()
        challenge = str(data.get('challenge', ''))
    except Exception:
        return web.json_response({'error': 'bad request'}, status=400)
    ok = session_manager.register_kill(session_id, challenge)
    if not ok:
        return web.json_response({'error': 'rejected'}, status=403)
    return web.json_response({'ok': True})


async def handle_complete(request: web.Request) -> web.Response:
    """Stage 1: minigame proof-of-play passed."""
    session_id = request.match_info['uuid']
    try:
        data = await request.json()
        challenge = str(data.get('challenge', ''))
    except Exception:
        return web.json_response({'error': 'bad request'}, status=400)
    ok = session_manager.mark_game_passed(session_id, challenge)
    if not ok:
        return web.json_response({'error': 'verification failed'}, status=403)
    if not config.TURNSTILE_ENABLED:
        session_manager.mark_turnstile_passed(session_id, challenge)
    return web.json_response({'ok': True})


async def handle_verify_turnstile(request: web.Request) -> web.Response:
    """Stage 2: verify a Cloudflare Turnstile token server-to-server."""
    session_id = request.match_info['uuid']
    try:
        data = await request.json()
        challenge = str(data.get('challenge', ''))
        token = str(data.get('token', ''))
    except Exception:
        return web.json_response({'error': 'bad request'}, status=400)
    if not token:
        return web.json_response({'error': 'missing token'}, status=400)

    form = {'secret': config.TURNSTILE_SECRET_KEY, 'response': token}
    client_ip = _client_ip(request)
    if client_ip and client_ip != 'unknown':
        form['remoteip'] = client_ip

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as http:
            async with http.post(_TURNSTILE_VERIFY_URL, data=form) as resp:
                result = await resp.json(content_type=None)
    except Exception:
        return web.json_response({'error': 'verification unavailable'}, status=502)

    if not result.get('success'):
        return web.json_response(
            {'error': 'turnstile failed', 'error-codes': result.get('error-codes', [])}, status=403
        )

    ok = session_manager.mark_turnstile_passed(session_id, challenge)
    if not ok:
        return web.json_response({'error': 'rejected'}, status=403)
    return web.json_response({'ok': True})


async def handle_altcha_challenge(request: web.Request) -> web.Response:
    """Stage 3a: mint a fresh, genuinely random-effort Altcha challenge (no `counter`)."""
    session_id = request.match_info['uuid']
    challenge = request.query.get('challenge', '')
    session = session_manager.sessions.get(session_id)
    if not session or session_manager.is_expired(session_id):
        return web.json_response({'error': 'not found'}, status=404)
    if session.get('challenge') != challenge:
        return web.json_response({'error': 'rejected'}, status=403)
    if not (session.get('game_passed') and session.get('turnstile_passed')):
        return web.json_response({'error': 'rejected'}, status=403)

    ch = create_challenge(
        algorithm=_ALTCHA_ALGORITHM,
        cost=_ALTCHA_COST,
        hmac_secret=_ALTCHA_HMAC_SECRET,
        expires_at=int(time.time()) + _ALTCHA_CHALLENGE_TTL,
        data={'sid': session_id},
    )
    return web.json_response(ch.to_dict())


async def handle_verify_altcha(request: web.Request) -> web.Response:
    """Stage 3b: verify the solved Altcha payload, then finalize the session (generate code)."""
    session_id = request.match_info['uuid']
    try:
        data = await request.json()
        challenge = str(data.get('challenge', ''))
        payload_str = data.get('payload')
    except Exception:
        return web.json_response({'error': 'bad request'}, status=400)
    if not isinstance(payload_str, str) or not payload_str:
        return web.json_response({'error': 'missing payload'}, status=400)

    try:
        decoded = Payload.from_base64(payload_str)
    except Exception:
        return web.json_response({'error': 'bad payload'}, status=400)

    bound_sid = (decoded.challenge.parameters.data or {}).get('sid')
    if bound_sid != session_id:
        return web.json_response({'error': 'session mismatch'}, status=403)

    result = verify_solution(decoded, hmac_secret=_ALTCHA_HMAC_SECRET)
    if not result.verified:
        return web.json_response({'error': 'altcha failed'}, status=403)

    ok = session_manager.mark_altcha_passed(session_id, challenge)
    if not ok:
        return web.json_response({'error': 'rejected'}, status=403)

    code = session_manager.generate_code(session_id)
    if code is None:
        return web.json_response({'error': 'rejected'}, status=403)
    return web.json_response({'ok': True})


async def handle_code_image(request: web.Request) -> web.Response:
    session_id = request.match_info['uuid']
    session = session_manager.sessions.get(session_id)
    if not session or not session.get('completed') or not session.get('code'):
        raise web.HTTPNotFound()
    buf = _image_captcha.generate(session['code'])
    return web.Response(body=buf.getvalue(), content_type='image/png', headers={'Cache-Control': 'no-store'})


async def handle_doom_file(request: web.Request) -> web.FileResponse:
    rel_path = request.match_info['path']
    full_path = os.path.realpath(os.path.join(DOOM_DIR, rel_path))
    if not full_path.startswith(DOOM_DIR + os.sep) and full_path != DOOM_DIR:
        raise web.HTTPForbidden()
    if os.path.splitext(full_path)[1].lower() not in _ALLOWED_ASSET_EXTS:
        raise web.HTTPForbidden()
    if not os.path.isfile(full_path):
        raise web.HTTPNotFound()
    headers = {}
    if full_path.endswith(('.js', '.html')):
        headers['Cache-Control'] = 'no-store'
    return web.FileResponse(full_path, headers=headers)


def create_app() -> web.Application:
    app = web.Application(middlewares=[rate_limit_middleware])
    app.router.add_get('/captcha/{uuid}', handle_captcha_page)
    app.router.add_post('/api/captcha/{uuid}/kill', handle_kill)
    app.router.add_post('/api/captcha/{uuid}/complete', handle_complete)
    app.router.add_post('/api/captcha/{uuid}/verify_turnstile', handle_verify_turnstile)
    app.router.add_get('/api/captcha/{uuid}/altcha_challenge', handle_altcha_challenge)
    app.router.add_post('/api/captcha/{uuid}/verify_altcha', handle_verify_altcha)
    app.router.add_get('/api/captcha/{uuid}/code.png', handle_code_image)
    app.router.add_get('/doom/{path:.*}', handle_doom_file)
    app.router.add_get('/tetris/{path:.*}', handle_doom_file)
    app.router.add_get('/mario/{path:.*}', handle_doom_file)
    app.router.add_get('/altcha/{path:.*}', handle_doom_file)
    return app


async def start_server() -> None:
    app = create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, config.WEB_HOST, config.WEB_PORT)
    await site.start()
    await asyncio.Event().wait()
