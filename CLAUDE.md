# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Entry Guardian is a Telegram anti-spam bot (aiogram 3 + aiohttp) that gates group entry behind an interactive
captcha (a minigame — DOOM/Tetris/Mario — plus optional Cloudflare Turnstile and an Altcha proof-of-work stage),
and also implements a full per-chat moderation system (roles, bans/mutes local and global, anti-raid, bulk message
deletion, an inline-keyboard admin panel, etc). See `README.md` for the full user-facing feature list and command
table — it is kept up to date and is the primary reference for *behavior*; this file is about *where things live*
and the non-obvious mechanics you need across files to change something safely.

## Running

No test suite, linter, or build step exists in this repo — there is nothing to run except the bot itself.

```bash
pip install -r requirements.txt
cp .env.example .env      # then edit — TOKEN is the only required value
python run.py
```

Or via Docker (`docker-compose.yml` mounts `./users.db` as a file — `touch users.db` first if it doesn't exist):

```bash
docker compose up -d
docker compose logs -f
```

To sanity-check a change compiles (there's no CI): `python3 -m py_compile <file>.py`. To validate a locale edit:
`python3 -c "import json; json.load(open('l10n/ru_RU.json'))"`.

To inspect the SQLite DB directly: `sqlite3 users.db "SELECT ...;"` (stop the bot first for anything beyond a
read-only query, to avoid racing its own writes).

## Process architecture

`run.py` is the only entry point. It starts one aiogram `Bot`/`Dispatcher` and runs everything concurrently via a
single `asyncio.gather`: Telegram long-polling, the aiohttp captcha web server (`webserver.py`), and several
infinite-loop background tasks (session expiry, raid reminders, 24h-kick sweep, pending-unban retries, message-log
flush/purge). All of this is **one process** — there's no worker/queue split. Every module-level `db_man = DBManager()`
in each handler file opens its own `sqlite3` connection (`check_same_thread=False`) to the same file; this is
intentional and matches the existing pattern, not a bug to "fix" into a shared singleton.

Routers register in `run.py`: `moderation_handler.router`, `personal_msg_handler.router`, `chat_member_handler.router`,
`reaction_handler.router`. `moderation_handler.UserTrackingMiddleware` is installed as an **outer** middleware on
`dp.message`, so it runs before *every* message hits *any* router (see below).

## The captcha verification pipeline (multi-file, easy to break silently)

This is the part most likely to bite you across files. State lives in `session_manager.sessions` (a plain in-memory
dict, keyed by session UUID — **not persisted**, a restart drops all in-flight captchas). It's a 4-flag state machine:
`game_passed` → `turnstile_passed` → `altcha_passed` → `completed` (code generated). Each `mark_*_passed` function
refuses to set its flag unless the previous ones are already set — the ordering is enforced server-side, not just by
UI flow.

- `GET /captcha/{uuid}` (`webserver.handle_captcha_page`) mints a fresh per-page-load `challenge` token
  (`session_manager.set_page_loaded`) and **resets** `game_passed`/`turnstile_passed`/`altcha_passed`/kill-count —
  every reload restarts the whole chain from scratch (unless the session is already `completed`, which short-circuits
  to the "here's your code" screen).
- The minigame iframe (`captcha.html`=DOOM, `tetris_captcha.html`+`.js`, `mario_captcha.html`+`FullScreenMario.min.js`)
  posts `challenge` back on every kill/placement event to `POST /api/captcha/{uuid}/kill`, then calls `/complete`.
  The three games signal completion to the wrapper (`templates/captcha_wrapper.html`) differently — DOOM/Mario via
  `window.postMessage`, Tetris calls `/complete` itself and just tells the wrapper it's `already_done` — the wrapper
  has to special-case this, see the `message` listener there if you touch the minigame handoff.
- If `TURNSTILE_ENABLED` (both `TURNSTILE_SITE_KEY`/`SECRET_KEY` set) is false, `/complete` auto-marks
  `turnstile_passed` too, skipping straight to Altcha — don't assume the Turnstile stage always runs.
- Altcha's challenge (`/altcha_challenge`) is bound to the session id via its `data={'sid': session_id}` payload,
  HMAC-signed with an **in-process** secret (`_ALTCHA_HMAC_SECRET`, regenerated on every restart) — a solved payload
  can't be replayed against a different session or survive a restart.
- The final 6-char code is only ever exposed as a rendered CAPTCHA **image** (`/api/captcha/{uuid}/code.png`), never
  as text/JSON — the user has to read and retype it in a DM to the bot, which is what `personal_msg_handler` verifies
  (`session_manager.find_by_code`) and turns into `db_man.verify_user()` + unmuting every chat in `pending_chats`.

Per-IP defenses live in `webserver.py`: a sliding-window rate limiter (`rate_limit_middleware`) on `/captcha/*` and
`/api/captcha/*`, and `_client_ip()` which prefers `X-Real-IP`/`X-Forwarded-For` over the raw TCP peer (required
behind nginx — and doubly so through Docker's published-port NAT, which otherwise hands you the Docker gateway IP,
not the visitor's) gated by `config.TRUST_PROXY_HEADERS`. If `COLLECT_CAPTCHA_IPS` is on, the **first** IP/User-Agent
seen for a user is recorded once (`captcha_ips` table, `INSERT OR IGNORE` — never overwritten) and surfaced only via
the owner-only, DM-only `/uinfo` command, deliberately kept out of `/punl` (which staff/admins can also reach).

## Moderation system (`moderation_handler.py`, ~2800 lines — the biggest file by far)

Three-tier role model in `permissions.py`: **owner** (in `config.OWNERS`, global, not stored in DB) > **admin**
(per-chat, `roles` table) > **moderator** (per-chat). Almost every command has 4 variants: local/global ×
announced/silent (`ban`/`gban`/`sban`/`sgban`, same pattern for mute and their `un*` counterparts) — when adding a
punishment-type command, follow that naming/behavior grid rather than inventing a new shape.

**Channels vs anonymous admins vs users** — a recurring gotcha. When someone posts *as a channel* (linked channel
auto-forward, or "Send As" picking a channel), `message.from_user` is **always** the same generic pseudo-account
(`@Channel_Bot`, id `136817688`) regardless of which channel posted — you cannot use `from_user` to identify the
channel. The actual channel is in `message.sender_chat` (type `'channel'`). `_reply_channel()` extracts it from a
replied-to message, and `_channel_mention()` builds its display label; commands that need to support banning a
channel (`_maybe_channel_ban`/`_maybe_channel_unban`, `/delete_user` via `_run_delete_user_channel`, `/punl`) check
`_reply_channel()` explicitly *before* falling back to normal user-target resolution (`_resolve_target`,
`_get_target_or_reply`) — those generic resolvers do **not** understand channels and will happily resolve to the
`@Channel_Bot` id if you reply to a channel's post without checking `_reply_channel()` first. An **anonymous group
admin** (not a channel — a real admin posting "as the group") is a *different* case: `sender_chat` is the group
itself, `from_user` is Telegram's fixed `GroupAnonymousBot` id (`1087968824`); the codebase does not currently grant
this pseudo-account any role automatically (that was tried and reverted — don't be surprised if you find a "why
isn't this handled" seam here).

`UserTrackingMiddleware` (outer middleware, runs before every message reaches a router) does, in order: caches
username↔id for `@username` resolution, drops commands from `command_banned` users, tracks messages for
`/delete_user`/`/delete_chat` (separately for user vs channel senders — `recent_messages`/`recent_channel_messages`),
enforces the global blocklist reactively (bans+deletes on sight for members who joined before the bot could enforce
it at join time), enforces channel bans/the "channels forbidden" toggle, and enforces a **flat 10s per-user command
cooldown for plain members** (no role — moderators/admins/owners are exempt via `permissions.is_staff`) using a
reserved pseudo-command key (`__any__`) in the existing `cooldown_use` table — separate from the admin-configurable
per-command cooldowns (`cooldowns` table, `/admin` panel → per-command cooldown menu), which only ever throttle
*moderators*, not plain members. Finally it enforces `stopped_chats` (owner-only `/stopchat`).

The admin panel (`/admin`, DM-only) is a single `F.data.startswith('adm:')` callback-query handler
(`admin_callback`) dispatching on an `action` token parsed out of `adm:<action>:<chat_id>[:...]` — if you add a new
toggle, follow the existing pattern of a button in `_build_chat_menu` plus a branch in that dispatcher, not a new
handler.

## Database (`dbmanager.py`)

Single SQLite file, one `DBManager` class, no ORM. Schema migration is inline in `__init__`: every table is
`CREATE TABLE IF NOT EXISTS`-style (checked against `sqlite_master`), and columns added after initial release are
added via `PRAGMA table_info` + conditional `ALTER TABLE ... ADD COLUMN` — **this is the only migration mechanism**;
there are no numbered migration files. Follow that pattern for schema changes (see the `captcha_ips`/`user_agent`
column addition for a recent example). All queries are parameterized (`?` placeholders) — never string-format a
value into SQL.

Global vs. local state is a real distinction, not just naming: `blocklist`/`global_mutes` apply across every chat
the bot is in (`db_man.get_bot_chats()`), while `mutes`/per-chat ban state apply to one chat only. `effective_mute()`
checks global before local. The bot can only discover/enumerate chats it has *observed* since a given deploy
(`bot_chats` table, populated by `remember_chat()`) — Telegram gives bots no API to list all chats they're in, so
global actions (global ban sweep, etc.) are scoped to that observed set, not "everywhere the bot technically is."

## Localization

`translator.py` is a ~10-line flat JSON key lookup (`l10n/ru_RU.json`, `l10n/en_US.json`), selected once at startup
via `config.LOCALE` — **not** per-user/per-chat. Every user-facing string must exist in both locale files with the
same key and the same `{0}`/`{1}`-style positional placeholders (uses `str.format()`, not `%`-style or f-strings) or
you'll get the raw key echoed back in prod for whichever locale is missing it. When adding a string, add it to both
files in the same place, and check with `python3 -c "import json; json.load(open('l10n/....json'))"` — a trailing
comma or missed bracket breaks the whole locale silently loud (`json.load` raises, so this crashes the bot's
`Translator.__init__`).

HTML output discipline: most staff-facing messages are sent with `parse_mode='HTML'`. Any interpolated
user-controlled text (names, titles, reasons, User-Agent, etc.) must go through `_esc()` (in `moderation_handler.py`,
`html.escape(..., quote=False)`) before being embedded — several helpers (`_channel_mention`, `_user_mention`,
`_identity_html`) already do this for you; don't bypass them by hand-building an HTML string from raw fields.
