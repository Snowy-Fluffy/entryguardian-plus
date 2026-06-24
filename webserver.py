import asyncio
import os
from aiohttp import web
import config
import session_manager

DOOM_DIR = os.path.realpath(
    os.getenv('DOOM_DIR', os.path.dirname(os.path.abspath(__file__)))
)
_TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), 'templates', 'captcha_wrapper.html')

_ALLOWED_ASSET_EXTS = frozenset({
    '.html', '.js', '.css', '.ico', '.png', '.jpg', '.jpeg',
    '.gif', '.svg', '.webp', '.wav', '.mp3', '.ogg', '.woff', '.woff2', '.ttf',
})

_wrapper_template: str | None = None


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
    '#code-box {{ background: #000; border: 2px solid #33ff33; padding: 12px 24px;'
    ' font-size: 2.4rem; font-weight: bold; letter-spacing: 8px; color: #33ff33;'
    ' margin: 12px 0; font-family: \'Courier New\', monospace; }}'
    '#hint {{ font-size: 0.85rem; color: #888; margin-top: 8px; }}'
    '</style></head><body>'
    '<h2>CAPTCHA</h2>'
    '<div id="result">'
    '<p>&#x2705; Капча пройдена!</p>'
    '<p>Отправьте этот код боту:</p>'
    '<div id="code-box">{code}</div>'
    '<div id="hint">Введите код в чате с ботом для завершения верификации</div>'
    '</div></body></html>'
)


async def handle_captcha_page(request: web.Request) -> web.Response:
    session_id = request.match_info['uuid']
    session = session_manager.sessions.get(session_id)
    if not session or session_manager.is_expired(session_id):
        return web.Response(text=_ERROR_HTML, content_type='text/html', status=410)
    if session.get('completed') and session.get('code'):
        html = _COMPLETED_HTML_TMPL.format(code=session['code'])
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
    session_id = request.match_info['uuid']
    try:
        data = await request.json()
        challenge = str(data.get('challenge', ''))
    except Exception:
        return web.json_response({'error': 'bad request'}, status=400)
    code = session_manager.complete_session(session_id, challenge)
    if code is None:
        return web.json_response({'error': 'verification failed'}, status=403)
    return web.json_response({'code': code})


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
    app = web.Application()
    app.router.add_get('/captcha/{uuid}', handle_captcha_page)
    app.router.add_post('/api/captcha/{uuid}/kill', handle_kill)
    app.router.add_post('/api/captcha/{uuid}/complete', handle_complete)
    app.router.add_get('/doom/{path:.*}', handle_doom_file)
    app.router.add_get('/tetris/{path:.*}', handle_doom_file)
    app.router.add_get('/mario/{path:.*}', handle_doom_file)
    return app


async def start_server() -> None:
    app = create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, config.WEB_HOST, config.WEB_PORT)
    await site.start()
    await asyncio.Event().wait()
