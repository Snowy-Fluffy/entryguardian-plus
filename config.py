import os
from dotenv import load_dotenv

load_dotenv()

TOKEN: str = os.environ['TOKEN']
DB_PATH: str = os.getenv('DB_PATH', 'users.db')
MAX_ATTEMPTS: int = int(os.getenv('MAX_ATTEMPTS', '3'))
COOL_DOWN: int = int(os.getenv('COOL_DOWN', '900'))
LOCALE: str = os.getenv('LOCALE', 'ru_RU')
OWNERS: set[int] = {int(x.strip()) for x in os.getenv('OWNERS', '').split(',') if x.strip()}

WEB_HOST: str = os.getenv('WEB_HOST', '127.0.0.1')
WEB_PORT: int = int(os.getenv('WEB_PORT', '8080'))
CAPTCHA_BASE_URL: str = os.getenv('CAPTCHA_BASE_URL', 'http://localhost:8080/captcha')
CAPTCHA_TIMEOUT: int = int(os.getenv('CAPTCHA_TIMEOUT', '600'))
CAPTCHA_ENEMIES: int = int(os.getenv('CAPTCHA_ENEMIES', '4'))
MIN_PLAY_TIME: float = float(os.getenv('MIN_PLAY_TIME', '3.0'))
KILL_COOLDOWN: float = float(os.getenv('KILL_COOLDOWN', '0.5'))
CAPTCHA_MIN_PIECES: int = int(os.getenv('CAPTCHA_MIN_PIECES', '3'))
MARIO_MIN_PLAY_TIME: float = float(os.getenv('MARIO_MIN_PLAY_TIME', '5.0'))
CAPTCHA_TYPES: list[str] = [t.strip() for t in os.getenv('CAPTCHA_TYPES', 'doom,tetris,mario').split(',') if t.strip()]
