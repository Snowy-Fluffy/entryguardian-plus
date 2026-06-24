import time
import uuid as uuid_module
import secrets
import string
import config

sessions: dict[str, dict] = {}

_CODE_CHARS = string.ascii_uppercase + string.digits
_CODE_LEN = 8


def _generate_code() -> str:
    return ''.join(secrets.choice(_CODE_CHARS) for _ in range(_CODE_LEN))


def create_session(user_id: int, captcha_type: str = 'doom') -> str:
    session_id = str(uuid_module.uuid4())
    sessions[session_id] = {
        'user_id': user_id,
        'created_at': time.time(),
        'completed': False,
        'code': None,
        'challenge': None,
        'page_loaded_at': None,
        'kills_registered': 0,
        'last_kill_at': 0.0,
        'captcha_type': captcha_type,
    }
    return session_id


def set_page_loaded(session_id: str) -> str | None:
    """Called when the captcha page is served. Generates and stores a challenge token."""
    session = sessions.get(session_id)
    if not session:
        return None
    challenge = secrets.token_hex(20)
    session['challenge'] = challenge
    session['page_loaded_at'] = time.time()
    return challenge


def is_expired(session_id: str) -> bool:
    session = sessions.get(session_id)
    if not session:
        return True
    return time.time() - session['created_at'] > config.CAPTCHA_TIMEOUT


def get_pending_session(user_id: int) -> str | None:
    for session_id, session in sessions.items():
        if (session['user_id'] == user_id
                and not session['completed']
                and not is_expired(session_id)):
            return session_id
    return None


def _required_interactions(session: dict) -> int:
    t = session.get('captcha_type')
    if t == 'tetris':
        return config.CAPTCHA_MIN_PIECES
    if t == 'mario':
        return 1
    return config.CAPTCHA_ENEMIES


def register_kill(session_id: str, challenge: str) -> bool:
    """Register one enemy kill / piece placement server-side."""
    session = sessions.get(session_id)
    if not session or is_expired(session_id) or session['completed']:
        return False
    if session.get('challenge') != challenge:
        return False
    if session['kills_registered'] >= _required_interactions(session):
        return False
    now = time.time()
    captcha_type = session.get('captcha_type')
    if captcha_type != 'tetris':
        if now - session['last_kill_at'] < config.KILL_COOLDOWN:
            return False
    if captcha_type == 'mario':
        page_loaded_at = session.get('page_loaded_at') or 0
        if now - page_loaded_at < config.MARIO_MIN_PLAY_TIME:
            return False
    session['kills_registered'] += 1
    session['last_kill_at'] = now
    return True


def complete_session(session_id: str, challenge: str) -> str | None:
    """Validate proof-of-play and mark session as completed. Returns code or None on failure."""
    session = sessions.get(session_id)
    if not session or is_expired(session_id):
        return None
    if session['completed']:
        return None
    if session.get('challenge') != challenge:
        return None
    page_loaded_at = session.get('page_loaded_at') or 0
    if time.time() - page_loaded_at < config.MIN_PLAY_TIME:
        return None
    if session['kills_registered'] < _required_interactions(session):
        return None
    code = _generate_code()
    session['completed'] = True
    session['code'] = code
    return code


def find_by_code(user_id: int, code: str) -> str | None:
    code = code.strip().upper()
    for session_id, session in sessions.items():
        if (session['user_id'] == user_id
                and session['completed']
                and session['code'] == code
                and not is_expired(session_id)):
            return session_id
    return None


def has_any_session(user_id: int) -> bool:
    return any(s['user_id'] == user_id for s in sessions.values())


def remove_session(session_id: str) -> None:
    sessions.pop(session_id, None)


def remove_user_sessions(user_id: int) -> None:
    for sid in [k for k, v in sessions.items() if v['user_id'] == user_id]:
        sessions.pop(sid)


def cleanup_expired() -> list[int]:
    """Delete expired sessions. Returns user_ids with non-completed expired sessions (once per user)."""
    notified: set[int] = set()
    result: list[int] = []
    now = time.time()
    for session_id in list(sessions.keys()):
        session = sessions[session_id]
        if now - session['created_at'] > config.CAPTCHA_TIMEOUT:
            uid = session['user_id']
            if not session['completed'] and uid not in notified:
                result.append(uid)
                notified.add(uid)
            del sessions[session_id]
    return result
