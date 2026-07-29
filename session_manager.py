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

import time
import uuid as uuid_module
import secrets
import string
import config

sessions: dict[str, dict] = {}

_CODE_CHARS = string.ascii_uppercase + string.digits
_CODE_LEN = 6


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
        'game_passed': False,
        'turnstile_passed': False,
        'altcha_passed': False,
    }
    return session_id


def set_page_loaded(session_id: str) -> str | None:
    """Called when the captcha page is (re)served. Mints a fresh challenge and resets
    minigame/verification progress, since the front-end always restarts the whole flow
    from scratch on reload — a stale challenge/kill-count would otherwise get stuck."""
    session = sessions.get(session_id)
    if not session:
        return None
    challenge = secrets.token_hex(20)
    session['challenge'] = challenge
    session['page_loaded_at'] = time.time()
    session['kills_registered'] = 0
    session['last_kill_at'] = 0.0
    session['game_passed'] = False
    session['turnstile_passed'] = False
    session['altcha_passed'] = False
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
    if not session or is_expired(session_id) or session['completed'] or session['game_passed']:
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


def mark_game_passed(session_id: str, challenge: str) -> bool:
    """Validate minigame proof-of-play. First stage of the verification chain."""
    session = sessions.get(session_id)
    if not session or is_expired(session_id):
        return False
    if session['completed'] or session['game_passed']:
        return False
    if session.get('challenge') != challenge:
        return False
    page_loaded_at = session.get('page_loaded_at') or 0
    if time.time() - page_loaded_at < config.MIN_PLAY_TIME:
        return False
    if session['kills_registered'] < _required_interactions(session):
        return False
    session['game_passed'] = True
    return True


def mark_turnstile_passed(session_id: str, challenge: str) -> bool:
    """Second stage: Cloudflare Turnstile verified server-side."""
    session = sessions.get(session_id)
    if not session or is_expired(session_id) or session['completed']:
        return False
    if session.get('challenge') != challenge:
        return False
    if not session['game_passed']:
        return False
    session['turnstile_passed'] = True
    return True


def mark_altcha_passed(session_id: str, challenge: str) -> bool:
    """Third stage: Altcha proof-of-work solution verified server-side."""
    session = sessions.get(session_id)
    if not session or is_expired(session_id) or session['completed']:
        return False
    if session.get('challenge') != challenge:
        return False
    if not (session['game_passed'] and session['turnstile_passed']):
        return False
    session['altcha_passed'] = True
    return True


def generate_code(session_id: str) -> str | None:
    """Finalize the session once all three stages have passed. Returns the code, or None."""
    session = sessions.get(session_id)
    if not session or is_expired(session_id):
        return None
    if session['completed']:
        return None
    if not (session['game_passed'] and session['turnstile_passed'] and session['altcha_passed']):
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
