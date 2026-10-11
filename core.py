# -*- coding: utf-8 -*-
"""Ядро загрузчика: модели задач, разбор ввода, фильтры, сохранение.

Модуль не зависит от Qt и сторонних библиотек, поэтому его можно тестировать отдельно.
"""
import functools
import hashlib
import hmac
import ipaddress
import secrets
import itertools
import json
import os
import shutil
import re
import threading
import time
import urllib.parse
from dataclasses import dataclass, field, fields

APP_TITLE = "Great Turtle"
APP_VERSION = "2026.10.11.64"
# Страница со способами поддержки: показывается в «О программе», если свою ссылку в настройках не задали
DONATE_DEFAULT = "https://boosty.to/great_turtle/donate"
DATA_DIR = os.path.join(os.path.expanduser("~"), ".hitomi_clone")
STATE_FILE = os.path.join(DATA_DIR, "state.json")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
ARCHIVE_FILE = os.path.join(DATA_DIR, "archive.json")
MEMBERS_FILE = os.path.join(DATA_DIR, "tg_members.json")
THUMB_DIR = os.path.join(DATA_DIR, "thumbs")
STATE_VERSION = 1

# ---------- состояния задач ----------
QUEUED, READING, WAITING, DOWNLOADING = "queued", "reading", "waiting", "downloading"
DONE, INCOMPLETE, INVALID = "done", "incomplete", "invalid"
ACTIVE_STATES = (QUEUED, READING, WAITING, DOWNLOADING)

# ---------- метки (Ctrl+1 ... Ctrl+7) ----------
TAGS = [
    None,
    ("red", "Красный", "#e5534b"),
    ("orange", "Оранжевый", "#f0883e"),
    ("yellow", "Жёлтый", "#e3b341"),
    ("green", "Зелёный", "#3fb950"),
    ("blue", "Синий", "#388bfd"),
    ("purple", "Фиолетовый", "#a371f7"),
    ("gray", "Серый", "#8b949e"),
]

RESOLUTIONS = [(2160, "4K (2160p)"), (1440, "2K (1440p)"), (1080, "1080p"), (720, "720p"),
               (480, "480p"), (360, "360p"), (240, "240p"), (144, "144p")]
MODES = ([("best", "Видео — лучшее качество"),
          ("mp4", "Видео — лучшее качество, MP4 (без AV1)"),
          ("h264", "Видео — совместимость, H.264 + AAC (до 1080p на YouTube)")]
         + [(f"r{h}", f"Видео — {label}, MP4") for h, label in RESOLUTIONS]
         + [("audio", "Только звук"), ("subs", "Только субтитры (без видео)")])
MODE_ALIASES = {"1080": "r1080", "720": "r720"}  # старые названия режимов из прежних версий


AUDIO_QUALITIES = [("mp3_320", "MP3 320 кбит/с (максимум для mp3)"),
                   ("mp3_256", "MP3 256 кбит/с"),
                   ("mp3_192", "MP3 192 кбит/с"),
                   ("original", "Оригинальный звук без перекодирования (лучшее качество, m4a/opus)")]


def norm_mode(key):
    key = MODE_ALIASES.get(key, key)
    return key if key in dict(MODES) else "best"


# ---------- форматирование ----------
def fmt_size(b):
    if not b:
        return "—"
    if b >= 1024 ** 3:
        return f"{b / 1024 ** 3:.2f} " + tr("ГБ")
    if b >= 1024 ** 2:
        return f"{b / 1024 ** 2:.1f} " + tr("МБ")
    return f"{max(b, 1) / 1024:.0f} " + tr("КБ")


def select_label(idx, text, size=0, duration=0, mark=False):
    """Строка в окне выбора роликов: «3.  ✓ Название — 12:34 · 150 МБ» (длительность и вес — если известны)."""
    extra = " · ".join(x for x in (fmt_duration(duration) if duration else "", fmt_size(size) if size else "") if x)
    return f"{idx}.  {'✓ ' if mark else ''}{text}" + (f"  —  {extra}" if extra else "")


def select_total(chosen, sizes):
    """(сумма известных весов выбранных, сколько выбранных без веса)."""
    known = [sizes.get(i, 0) for i in chosen]
    return sum(known), sum(1 for n in known if not n)


def task_size(t):
    """Вес одного файла: у готового — фактический, пока качается — ожидаемый (0, если пока неизвестен)."""
    if t.state == DONE and t.size:
        return t.size
    return t.total or t.size or 0


def fmt_speed(bps):
    if not bps:
        return ""
    if bps >= 1024 ** 2:
        return f"{bps / 1024 ** 2:.1f} " + tr("МБ/сек.")
    return f"{bps / 1024:.0f} " + tr("КБ/сек.")


def fmt_eta(sec):
    if sec is None:
        return ""
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def fmt_duration(sec):
    if not sec:
        return ""
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


_WIN_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def safe_name(name, default="playlist"):
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", name or "").strip(" .")
    name = name[:120].strip(" .")      # после обрезки края чистим ещё раз: точка или пробел на конце недопустимы в Windows
    if name.split(".")[0].upper() in _WIN_RESERVED:
        name = "_" + name
    return name or default


# ---------- разбор ввода ----------
_SHORTCUTS = (
    (re.compile(r"^@([A-Za-z0-9_]{1,30})$"), "https://x.com/{}"),
    (re.compile(r"^insta_([A-Za-z0-9_.]+)$"), "https://www.instagram.com/{}/"),
    (re.compile(r"^pixiv_(\d+)$"), "https://www.pixiv.net/users/{}"),
    (re.compile(r"^deviant_([A-Za-z0-9_-]+)$"), "https://www.deviantart.com/{}"),
    (re.compile(r"^(\d{4,8})$"), "https://hitomi.la/galleries/{}.html"),
)
_DOMAIN_RE = re.compile(r"^(?:www\.)?[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.([A-Za-z]{2,24})(?:[/?#].*)?$")
# «readme.txt» и «photo.jpg» — это имена файлов, а не адреса сайтов
_FILE_EXTS = frozenset("txt log md doc docx pdf xls xlsx csv json xml ini cfg bat py pyw js html htm css zip rar 7z gz tar "
                       "jpg jpeg png gif bmp webp svg ico mp3 mp4 mkv avi mov wav flac exe dll msi".split())


def _unwrap(tok):
    """'(https://x.com/a).' -> 'https://x.com/a': убираем скобки и знаки препинания вокруг ссылки."""
    tok = tok.strip().strip("<>\"'")
    tok = tok.lstrip("([{")
    while tok and tok[-1] in ".,;:!?":
        tok = tok[:-1]
    for close, op in ((")", "("), ("]", "["), ("}", "{")):
        while tok.endswith(close) and tok.count(close) > tok.count(op):
            tok = tok[:-1]
    return tok.strip("<>\"'")


def expand_token(tok):
    tok = _unwrap(tok)
    if not tok:
        return None
    if re.match(r"^https?://", tok, re.I):
        return tok
    for rx, fmt in _SHORTCUTS:
        m = rx.match(tok)
        if m:
            return fmt.format(m.group(1))
    m = _DOMAIN_RE.match(tok)
    if m and not ("/" not in tok and m.group(1).lower() in _FILE_EXTS):
        return "https://" + tok
    return None


def parse_inputs(text):
    """Строка/несколько строк -> список URL. Принимает ссылки и сокращения (@user, pixiv_11, 123)."""
    out, seen = [], set()
    for chunk in (text or "").split():
        parts = [chunk] if re.match(r"^https?://", chunk, re.I) else chunk.split(",")
        for part in parts:
            u = expand_token(part)
            if u and u not in seen:
                seen.add(u)
                out.append(u)
    return out


_SITE_MAP = {
    "youtube.com": "youtube", "youtu.be": "youtube", "twitter.com": "twitter", "x.com": "twitter",
    "instagram.com": "insta", "pixiv.net": "pixiv", "pixiv.me": "pixiv", "twitch.tv": "twitch",
    "nicovideo.jp": "nico", "bilibili.com": "bili", "tiktok.com": "tiktok", "douyin.com": "tiktok",
    "vimeo.com": "vimeo", "soundcloud.com": "soundcloud", "deviantart.com": "deviantart",
    "facebook.com": "facebook", "imgur.com": "imgur", "flickr.com": "flickr", "tumblr.com": "tumblr",
    "weibo.com": "weibo", "artstation.com": "artstation", "coub.com": "coub", "archive.org": "wayback",
    "hitomi.la": "hitomi", "mastodon.social": "mastodon", "misskey.io": "misskey",
}


def site_of(url):
    try:
        host = (urllib.parse.urlparse(url).hostname or "").lower()
    except ValueError:
        return ""
    host = re.sub(r"^(www|m|mobile)\.", "", host)
    try:
        ipaddress.ip_address(host)
        return host.replace(":", "_")   # IP-адрес: целиком, без двоеточий (на Windows они недопустимы в имени папки)
    except ValueError:
        pass
    for dom, name in _SITE_MAP.items():
        if host == dom or host.endswith("." + dom):
            return name
    parts = host.split(".")
    if len(parts) >= 2:
        return parts[-3] if len(parts) >= 3 and parts[-2] in ("co", "com", "org", "net") else parts[-2]
    return host


def norm_url(url):
    """Ключ для сравнения ссылок: без http/https, www, #фрагмента и хвостового «/».
    Регистр меняется только у хоста (в путях, например в id роликов, он важен)."""
    url = (url or "").strip()
    try:
        p = urllib.parse.urlsplit(url)
    except ValueError:
        return url.rstrip("/")
    if not p.netloc:
        return url.rstrip("/")
    host = re.sub(r"^www\.", "", p.netloc.lower())
    return host + p.path.rstrip("/") + ("?" + p.query if p.query else "")


# ---------- диапазоны страниц ----------
def parse_range(text, total):
    """'~ 100' - первые 100; '-100 ~' - последние 100; '1, 10 ~ 20, -1' - выборочно.
    Возвращает отсортированный список номеров (с 1). Пустая строка = все."""
    text = (text or "").strip()
    if not text:
        return list(range(1, total + 1))
    chosen = set()

    def num(s, default):
        s = s.strip()
        if not s:
            return default
        n = int(s)  # ValueError для мусора
        if n == 0:
            raise ValueError("0")
        return total + n + 1 if n < 0 else n

    for tok in re.split(r"[,;]", text):
        tok = tok.strip()
        if not tok:
            continue
        if "~" in tok:
            left, _, right = tok.partition("~")
            a, b = num(left, 1), num(right, total)
        else:
            a = b = num(tok, 1)
        a, b = max(1, a), min(total, b)
        chosen.update(range(a, b + 1))
    return sorted(chosen)


def compress_ranges(nums):
    """[1,2,3,5] -> '1-3,5' (формат --range у gallery-dl)."""
    nums = sorted(set(nums))
    out, i = [], 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        out.append(str(nums[i]) if i == j else f"{nums[i]}-{nums[j]}")
        i = j + 1
    return ",".join(out)


# ---------- ошибки ----------
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


_PERMANENT_ERRORS = ("unsupported url", "video unavailable", "this video is not available", "private video",
                     "has been removed", "no longer available", "does not exist", "ffmpeg not found", "ffmpeg is not installed", "yt-dlp не установлен",
                     "нет субтитров", "requested format is not available", "blocked it on copyright", "copyright grounds",
                     "members-only")


def _api_rejected(low):
    """Сайт отвечает 400 на запрос данных о ролике: он изменил свой интерфейс, повтор не поможет."""
    return "http error 400" in low and ("json metadata" in low or "unable to download webpage" in low)


def is_permanent_error(msg):
    """Ошибки, которые от повтора не исчезнут (неизвестный сайт, видео удалено, нет ffmpeg...)."""
    low = _ANSI.sub("", str(msg or "")).lower()
    return any(k in low for k in _PERMANENT_ERRORS) or _api_rejected(low)


def retry_delay(n):
    """Пауза перед n-й попыткой автоповтора (n = 1, 2, 3...): 10, 30, 90, 270 секунд, не больше 5 минут."""
    return min(10 * 3 ** (max(n, 1) - 1), 300)


def friendly_error(msg):
    raw = _ANSI.sub("", str(msg or "")).strip()
    low = raw.lower()
    if "unsupported url" in low:
        return "Неизвестный сайт"
    if any(k in low for k in ("sign in to", "sign in or", "log in to", "login required", "login is required",
                              "members-only", "private video", "confirm your age", "age-restricted")):
        return "Требуется вход: обновите файл cookies"
    if "failed to parse json" in low or "nonetype" in low:
        return "Сайт отдал не те данные (проверка или защита); подключите cookies или повторите позже"
    if _api_rejected(low):
        return "Сайт не принял запрос (400): похоже, он изменил интерфейс. Обновите yt-dlp; если не помогло, ждите исправления в yt-dlp"
    if "http error 412" in low or "precondition failed" in low:
        return "Сайт отклонил запрос (412, защита от ботов); включите «Имитировать браузер» и подключите cookies"
    if re.search(r"(http error|status code|error code)\W*429\b", low) or "too many requests" in low:
        return "Слишком много запросов; повторите позже"
    if "ffmpeg" in low and ("not found" in low or "not installed" in low):
        return "Не найден ffmpeg"
    if "yt-dlp не установлен" in low:
        return raw
    raw = re.sub(r"^ERROR:\s*", "", raw)
    raw = re.sub(r"^\[[^\]]+\]\s*[^:]*:\s*", "", raw)  # "[youtube] abc: "
    return raw.replace("\n", " ")[:220] or "Неизвестная ошибка"


# ---------- настройки ----------
def _default_folder():
    return os.path.join(os.path.expanduser("~"), "Downloads")


@dataclass
class Settings:
    folder: str = field(default_factory=_default_folder)
    outtmpl: str = "%(title)s [%(id)s].%(ext)s"
    mode: str = "best"
    max_tasks: int = 3
    threads: int = 4
    retries: int = 5
    speed_limit_kb: int = 0
    proxy: str = ""
    cookies_file: str = ""
    cookies_browser: str = ""
    impersonate: bool = False
    use_gallerydl: bool = True
    all_audio: bool = False           # добавлять к оригиналу дубляж
    audio_quality: str = "mp3_320"    # mp3_320 / mp3_256 / mp3_192 / original (без перекодирования)
    site_folders: bool = True         # раскладывать загрузки по папкам-сайтам (youtube, vk, ...)
    subs_mode: str = ""               # "" — не качать, "file" — отдельными .srt, "embed" — внутрь видео
    subs_langs: str = "ru,en"         # языки субтитров через запятую (пусто — все)
    subs_auto: bool = False           # брать и автоматические (распознанные) субтитры
    audio_langs: str = "ru"           # языки дубляжа через запятую (пусто — все); оригинал берётся всегда
    auto_retry: int = 3               # сколько раз самой перезапускать задачу после ошибки (0 — не перезапускать)
    newest_first: bool = False        # в списке новые загрузки сверху
    dub_sites: str = ""               # дополнительные сайты, где искать дубляж (через запятую)
    ask_playlist: bool = False        # при загрузке плейлиста открывать окно выбора роликов
    use_archive: bool = True          # не качать повторно то, что уже скачано (даже если задачу убрали из списка)
    embed_cover: bool = True          # вшивать обложку в музыкальные файлы
    ytdlp_pre: bool = False           # обновлять yt-dlp до тестовых (pre-release) версий
    last_ytdlp_update: int = 0        # когда в последний раз обновляли yt-dlp (unix-время)
    lang: str = "ru"                  # язык интерфейса: ru / en (после смены нужен перезапуск)
    tab_order: str = ""               # порядок вкладок (ключи через запятую; «@all» — вкладка «Все»)
    tab_pinned: str = ""              # закреплённые вкладки (ключи через запятую)
    playlist_from_video_url: bool = False
    dark: bool = False
    thumb_size: int = 68
    autosave_min: int = 5
    notify_done: bool = False
    notify_all_done: bool = True
    tg_enabled: bool = False          # Telegram-бот: принимать ссылки и команды
    tg_token: str = ""                # токен бота от @BotFather
    tg_lock_hash: str = ""            # пароль-замок на токен и id руководителя (соль + PBKDF2); пусто — замка нет
    tg_owner: str = ""                # id руководителя: власть абсолютна (всегда администратор, его нельзя понизить, заблокировать или удалить)
    tg_guest_no_playlists: bool = True   # гостям нельзя плейлисты, каналы и профили целиком: только отдельные ролики
    tg_guest_sites: str = "youtube,instagram,tiktok"   # сайты, с которых гости могут скачивать (ключи из TG_SITES)
    tg_admin: str = ""                # id администраторов бота через запятую: все команды и уведомления
    tg_users: str = ""                # (устарело) гости из прежней версии; переносятся в таблицу участников
    tg_auto_guest: bool = False       # новый участник сразу становится гостем (иначе — заявка, её одобряет администратор)
    tg_notify: bool = True            # присылать в Telegram уведомления о завершении и ошибках
    after_all: str = "nothing"        # nothing | exit
    prevent_sleep: bool = True
    clipboard_monitor: bool = False
    resume_on_start: bool = False
    auto_remove_done: bool = False
    donate_url: str = ""              # ссылка для пожертвований (необязательно): показывается в «О программе»
    update_url: str = ""              # https-адрес zip-архива с новой версией программы (необязательно)
    geometry: str = ""

    @classmethod
    def load(cls, path=SETTINGS_FILE):
        s = cls()
        data = load_json(path, {})
        if not isinstance(data, dict):
            data = {}
        for f in fields(cls):
            cur = getattr(s, f.name)
            if f.name in data and isinstance(data[f.name], type(cur)) and (
                    isinstance(cur, bool) or not isinstance(data[f.name], bool)):
                setattr(s, f.name, data[f.name])
        s.mode = norm_mode(s.mode)
        if s.lang not in ("ru", "en"):
            s.lang = "ru"
        if s.audio_quality not in dict(AUDIO_QUALITIES):
            s.audio_quality = "mp3_320"
        s.max_tasks = min(max(1, s.max_tasks), 16)
        s.threads = min(max(1, s.threads), 24)
        s.thumb_size = min(max(44, s.thumb_size), 140)
        for name in ("retries", "speed_limit_kb", "autosave_min", "auto_retry"):
            if isinstance(getattr(s, name, None), int):
                setattr(s, name, max(0, getattr(s, name)))
        s.folder = os.path.expanduser((s.folder or "").strip()) or _default_folder()
        return s

    def save(self, path=SETTINGS_FILE):
        save_json(path, {f.name: getattr(self, f.name) for f in fields(self)})


# ---------- задача ----------
PERSIST = ("id", "url", "title", "artist", "site", "vid", "state", "error", "folder", "mode", "pages",
           "ask_select", "group", "tag", "comment", "files", "thumb_url", "thumb_file", "size", "total",
           "duration", "added", "backend", "has_info", "progress", "fmt_info", "title_fixed", "fmt_sel", "fsig")


@dataclass
class Task:
    id: int
    url: str
    title: str = ""
    artist: str = ""
    site: str = ""
    vid: str = ""
    state: str = QUEUED
    status: str = ""
    error: str = ""
    folder: str = ""
    mode: str = "best"
    pages: str = ""
    ask_select: bool = False
    group: str = ""
    tag: int = 0
    comment: str = ""
    files: list = field(default_factory=list)
    thumb_url: str = ""
    thumb_file: str = ""
    size: int = 0
    total: int = 0
    duration: float = 0.0
    added: float = 0.0
    backend: str = ""
    has_info: bool = False
    progress: float = 0.0
    fmt_info: str = ""        # какие потоки реально скачаны (id, кодек, разрешение)
    title_fixed: bool = False  # сайт дал безликое название («Video»), мы заменили его своим — и в имени файла тоже
    fsig: list = field(default_factory=list)   # [размер, время изменения] каждого файла: по ним находим файл после переименования
    fmt_sel: str = ""         # выбранный вручную формат yt-dlp (например «137+bestaudio/137»); пусто — по режиму
    # --- только во время работы, не сохраняется ---
    force_imp: bool = False   # сайт отказал (403/412/429) — дальше пробуем с имитацией браузера
    force_new: bool = False   # перезапуск вручную: качать, даже если запись есть в архиве скачанного
    speed: float = 0.0
    eta: object = None
    removed: bool = False
    cancel: threading.Event = field(default_factory=threading.Event, repr=False, compare=False)
    proc: object = field(default=None, repr=False, compare=False)
    paused: bool = False      # поставлена на паузу пользователем
    pause_req: bool = False   # идёт остановка ради паузы
    forced: bool = False      # продолжена вручную при общей паузе
    auto_tries: int = 0       # сколько раз программа уже сама перезапускала после ошибки
    retry_at: float = 0.0     # когда сделать следующий автоповтор (0 — не запланирован)

    def to_dict(self):
        d = {k: getattr(self, k) for k in PERSIST}
        if d["state"] not in (DONE, INCOMPLETE, INVALID):
            d["state"] = QUEUED if not self.has_info else WAITING
        return d

    @property
    def display_title(self):
        return self.title or self.url

    def first_file(self):
        for f in self.files:
            if os.path.exists(f):
                return f
        return None


def status_text(t, paused=False):
    if t.paused and t.state in (WAITING, DOWNLOADING):
        return tr("Пауза")
    if t.state == DOWNLOADING:
        if paused:
            return tr("Пауза")
        if t.status:
            return tr(t.status)
        parts = [f"{int(t.progress * 100)}%"]
        if t.speed:
            parts.append(fmt_speed(t.speed))
        if t.eta is not None:
            parts.append(fmt_eta(t.eta))
        return tr("Скачивание") + " " + " · ".join(parts)
    if t.state == DONE:
        return tr(t.status) if t.status else tr("Готово")
    if t.state == INCOMPLETE:
        return tr("Не завершено") + (": " + tr(t.error) if t.error else "")
    if t.state == INVALID:
        return tr("Ошибка") + ": " + tr(t.error or "неизвестная ошибка")
    if t.state == READING:
        return tr("Получаю информацию...")
    if t.state == WAITING:
        return tr("Пауза") if paused else tr(t.status or "Ожидание")
    return tr(t.status or "В очереди")


class Members:
    """Участники Telegram-бота: id -> {role, name, username, ts}. Роли: admin (всё), guest (ссылки и /list),
    pending (заявка, ждёт решения), blocked (бот не отвечает). Хранится в tg_members.json."""

    ROLES = ("admin", "guest", "pending", "blocked")
    owner = None   # id руководителя: его роль менять нельзя

    def __init__(self, path=None):
        self.path = path or MEMBERS_FILE
        self.lock = threading.RLock()
        data = load_json(self.path, {})
        self.data = {}
        for k, v in (data.items() if isinstance(data, dict) else []):
            if re.fullmatch(r"-?\d{1,20}", str(k)) and isinstance(v, dict) and v.get("role") in self.ROLES:
                try:
                    ts = int(v.get("ts") or 0)
                except (ValueError, TypeError):
                    ts = 0   # одна битая строка не должна мешать запуску программы
                self.data[int(k)] = {"role": v["role"], "name": str(v.get("name") or ""),
                                     "username": str(v.get("username") or ""), "ts": ts,
                                     "hidden": bool(v.get("hidden"))}

    def _save(self):
        save_json(self.path, {str(k): v for k, v in self.data.items()})

    def role(self, uid):
        with self.lock:
            if uid is not None and uid == self.owner:
                return "admin"
            m = self.data.get(uid)
            return m["role"] if m else ""

    def set_owner(self, uid):
        """Назначает руководителя (None — снять). Он всегда администратор."""
        with self.lock:
            old = self.owner
            self.owner = uid
            if old is not None and old != uid and old in self.data:
                self.data[old]["role"] = "guest"   # прежний руководитель больше не администратор
                self._save()
            if uid is not None:
                m = self.data.setdefault(uid, {"role": "admin", "name": "", "username": "", "ts": int(time.time())})
                m["role"] = "admin"
                self._save()

    def set_role(self, uid, role, name="", username=""):
        """False — роль не изменена (руководителя изменить нельзя)."""
        if role not in self.ROLES:
            raise ValueError(role)
        if uid == self.owner and role != "admin":
            return False
        with self.lock:
            m = self.data.setdefault(uid, {"role": role, "name": "", "username": "", "ts": int(time.time())})
            m["role"] = role
            if name:
                m["name"] = name
            if username:
                m["username"] = username
            self._save()
        return True

    def is_hidden(self, uid):
        """Участник скрыл кнопки внизу чата (/hide)."""
        with self.lock:
            m = self.data.get(uid)
            return bool(m and m.get("hidden"))

    def set_hidden(self, uid, hidden):
        with self.lock:
            m = self.data.get(uid)
            if m is None or bool(m.get("hidden")) == bool(hidden):
                return
            m["hidden"] = bool(hidden)
            self._save()

    def remove(self, uid):
        if uid == self.owner:
            return False
        with self.lock:
            if self.data.pop(uid, None) is not None:
                self._save()
        return True

    def ids(self, *roles):
        with self.lock:
            return {k for k, v in self.data.items() if v["role"] in roles}

    def touch(self, uid, name="", username="", role="pending"):
        """Сообщение от участника: запоминаем имя и время. Нового заводим с ролью role.
        Возвращает (копия записи, новый ли)."""
        with self.lock:
            m = self.data.get(uid)
            new = m is None
            now = int(time.time())
            if new:
                m = self.data[uid] = {"role": role, "name": name, "username": username, "ts": now}
                self._save()
            elif (name and m["name"] != name) or (username and m["username"] != username) or now - m["ts"] > 3600:
                m["name"] = name or m["name"]
                m["username"] = username or m["username"]
                m["ts"] = now
                self._save()
            return dict(m), new

    def items(self):
        with self.lock:
            return sorted(((k, dict(v)) for k, v in self.data.items()), key=lambda kv: (kv[1]["role"] != "pending", kv[0]))


# ---------- Telegram-бот: разбор команд и тексты ----------
def parse_chat_ids(text):
    """'123, -456;789' -> {123, -456, 789}. Всё, что не число, пропускается."""
    out = set()
    for tok in re.split(r"[\s,;]+", text or ""):
        if re.fullmatch(r"-?\d{1,20}", tok):
            out.add(int(tok))
    return out


TG_OWNER_CMDS = ("members", "allow", "admin", "block", "remove")   # только руководитель
TG_ADMIN_CMDS = ("list", "status", "pause", "resume", "retry", "file") + TG_OWNER_CMDS   # гостю доступны только ссылки
# Кнопки меню бота (клавиатура внизу чата): подпись -> команда. Нажатие приходит боту обычным текстом.
# Кнопки по правам: гость — только подсказка и список сайтов; администратор — управление загрузками;
# руководитель — плюс управление людьми.
TG_BUTTONS_GUEST = (("🌐 Сайты", "sites"), ("❓ Помощь", "help"), ("🙈 Скрыть кнопки", "hide"))
TG_BUTTONS_ADMIN = (("📋 Список", "list"), ("🔄 Перезапустить неудачные", "retry"),
                    ("⏸ Пауза", "pause"), ("▶ Продолжить", "resume"),
                    ("🌐 Сайты", "sites"), ("❓ Помощь", "help"), ("🙈 Скрыть кнопки", "hide"))
TG_BUTTONS_OWNER = TG_BUTTONS_ADMIN[:-1] + (("👥 Участники", "members"), TG_BUTTONS_ADMIN[-1])
TG_BUTTONS = TG_BUTTONS_OWNER   # все кнопки (для разбора нажатий; права проверяются отдельно)


def tg_button_cmd(text):
    """Текст нажатой кнопки (на любом из двух языков) -> команда, иначе ''."""
    text = (text or "").strip()
    for label, cmd in TG_BUTTONS:
        if text in (label, tr(label)):
            return cmd
    return ""


TG_GUEST_HELP = "Пришлите ссылку (или несколько) — программа добавит и скачает."


# Популярные сайты для гостей: (ключ, название, домены)
TG_SITES = (
    ("youtube", "YouTube", ("youtube.com", "youtu.be", "youtube-nocookie.com")),
    ("instagram", "Instagram", ("instagram.com", "instagr.am")),
    ("tiktok", "TikTok", ("tiktok.com",)),
    ("vk", "VK", ("vk.com", "vkvideo.ru", "vk.ru")),
    ("twitter", "X (Twitter)", ("twitter.com", "x.com", "t.co")),
    ("facebook", "Facebook", ("facebook.com", "fb.watch", "fb.com")),
    ("reddit", "Reddit", ("reddit.com", "redd.it")),
    ("twitch", "Twitch", ("twitch.tv",)),
    ("vimeo", "Vimeo", ("vimeo.com",)),
    ("pinterest", "Pinterest", ("pinterest.com", "pin.it")),
    ("soundcloud", "SoundCloud", ("soundcloud.com",)),
    ("rutube", "Rutube", ("rutube.ru",)),
    ("dailymotion", "Dailymotion", ("dailymotion.com", "dai.ly")),
    ("ok", "Одноклассники", ("ok.ru",)),
)
TG_SITE_DEFAULT = "youtube,instagram,tiktok"


def tg_site_keys(text):
    """'youtube, tiktok' -> {'youtube', 'tiktok'} (только известные ключи)."""
    known = {k for k, _, _ in TG_SITES}
    return {t.strip().lower() for t in str(text or "").replace(";", ",").split(",") if t.strip().lower() in known}


def tg_site_allowed(url, keys):
    """Разрешена ли ссылка гостям при включённых сайтах keys."""
    try:
        host = (urllib.parse.urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    for key, _, doms in TG_SITES:
        if key in keys and any(host == d or host.endswith("." + d) for d in doms):
            return True
    return False


def lock_make(password):
    """Пароль -> строка для хранения 'pbkdf2$соль$хэш' (сам пароль не хранится)."""
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), 200_000).hex()
    return f"pbkdf2${salt}${h}"


def lock_check(password, stored):
    """Подходит ли пароль к сохранённому хэшу."""
    try:
        kind, salt, h = str(stored).split("$")
        if kind != "pbkdf2":
            return False
        got = hashlib.pbkdf2_hmac("sha256", (password or "").encode("utf-8"), bytes.fromhex(salt), 200_000).hex()
        return hmac.compare_digest(got, h)
    except (ValueError, TypeError):
        return False


_LIST_PARAMS = ("list", "index", "start_radio", "pp")


def tg_strip_list(url):
    """Ссылка на ролик внутри плейлиста (watch?v=…&list=…) -> ссылка на сам ролик."""
    try:
        p = urllib.parse.urlparse(url)
        q = [(k, v) for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=True) if k not in _LIST_PARAMS]
        if len(q) == len(urllib.parse.parse_qsl(p.query, keep_blank_values=True)):
            return url
        return urllib.parse.urlunparse(p._replace(query=urllib.parse.urlencode(q)))
    except ValueError:
        return url


def tg_is_playlist(url):
    """Ссылка ведёт на плейлист, канал, профиль или альбом целиком (а не на один ролик)?"""
    try:
        p = urllib.parse.urlparse(url)
    except ValueError:
        return True
    host = re.sub(r"^(www|m|music)\.", "", (p.hostname or "").lower())
    path = (p.path or "/").lower()
    segs = [s for s in path.split("/") if s]
    qs = dict(urllib.parse.parse_qsl(p.query))
    if host.endswith("youtube.com") or host == "youtu.be":
        if host == "youtu.be":
            return False
        if path.startswith("/watch"):
            return "v" not in qs          # watch?v=…&list=… считается одним роликом (list потом отрезается)
        if segs and segs[0] in ("shorts", "live", "embed", "v", "clip"):
            return False
        return True                        # /playlist, /channel, /c, /user, /@имя, /results и т. п.
    if host.endswith("instagram.com") or host == "instagr.am":
        return not (segs and segs[0] in ("p", "reel", "reels", "tv") and len(segs) >= 2) and \
            not (len(segs) >= 3 and segs[1] in ("p", "reel", "tv"))
    if host.endswith("tiktok.com"):
        if host.split(".")[0] in ("vm", "vt"):
            return False
        if "video" in segs or "photo" in segs:
            return False
        return bool(segs) and (segs[0].startswith("@") or segs[0] in ("playlist", "collection", "tag", "music", "channel"))
    single = _tg_single_item(host, segs, path, qs)
    if single is not None:
        return not single
    return any(w in path for w in ("playlist", "/album", "/sets/", "/collection", "/channel", "/user/", "/users/", "/c/", "/playlists")) \
        or "list" in qs


def _tg_single_item(host, segs, path, qs):
    """Для сайтов из списка гостей: True — ссылка на один ролик/пост, False — профиль/канал/доска, None — правил нет."""
    h = lambda *d: any(host == x or host.endswith("." + x) for x in d)
    if h("twitter.com", "x.com"):
        return "status" in segs
    if h("t.co", "redd.it", "fb.watch", "pin.it", "dai.ly"):
        return True
    if h("reddit.com"):
        return "comments" in segs or (bool(segs) and segs[0] == "s")
    if h("vk.com", "vk.ru", "vkvideo.ru"):
        return bool(re.search(r"(video|clip|wall|photo)-?\d+_\d+", path + "?" + urllib.parse.urlencode(qs))) \
            or "z" in qs and "video" in qs["z"]
    if h("facebook.com", "fb.com"):
        return any(w in segs for w in ("videos", "watch", "reel", "reels", "posts", "photo", "photos", "permalink.php")) or "v" in qs
    if h("twitch.tv"):
        return "videos" in segs or "clip" in segs or host.startswith("clips.")
    if h("soundcloud.com"):
        return len(segs) == 2 and segs[1] not in ("sets", "albums", "likes", "reposts", "tracks", "popular-tracks",
                                                  "following", "followers", "comments")
    if h("vimeo.com"):
        return bool(segs) and segs[-1].isdigit()
    if h("pinterest.com"):
        return "pin" in segs
    if h("rutube.ru", "dailymotion.com"):
        return "video" in segs
    if h("ok.ru"):
        return "video" in segs or "videoembed" in segs
    return None


def tg_parse(text):
    """Сообщение -> (команда, остаток). '/file@MyBot 12' -> ('file', '12'); обычный текст -> ('', текст)."""
    text = (text or "").strip()
    if text.startswith("/"):
        head, _, rest = text.partition(" ")
        return head[1:].split("@")[0].lower(), rest.strip()
    return "", text


_TG_ICONS = {DONE: "✅", DOWNLOADING: "⬇️", INVALID: "❌", INCOMPLETE: "⚠️"}


def tg_task_line(t):
    title = (t.display_title or t.url)[:55]
    return f"#{t.id} {_TG_ICONS.get(t.state, '⏳')} {title} — {status_text(t)}"


def tg_list(tasks, limit=15):
    """Сводка и последние задачи для команды /list. Номера (#id) подходят для /file."""
    if not tasks:
        return tr("Список пуст")
    done = sum(1 for t in tasks if t.state == DONE)
    head = tr(f"Загрузок: {len(tasks)}, готово: {done}")
    shown = tasks[-limit:]
    lines = [tg_task_line(t) for t in shown]
    if len(tasks) > len(shown):
        lines.insert(0, tr(f"(показаны последние {len(shown)})"))
    return head + "\n" + "\n".join(lines)


TG_HELP = ("Пришлите ссылку (или несколько) — программа добавит и скачает.\n"
           "/list — список загрузок\n"
           "/pause — пауза всех, /resume — продолжить\n"
           "/retry — перезапустить неудачные\n"
           "/file 12 — прислать файл загрузки #12 (до 50 МБ)\n"
           "/sites — какие сайты разрешены гостям\n"
           "/hide — скрыть кнопки, /menu — показать снова\n"
           "/help — эта подсказка\n"
           "Кнопки внизу экрана дублируют команды.")
TG_OWNER_HELP = ("Только руководитель:\n"
                 "/members — участники\n"
                 "/allow ID — сделать гостем, /admin ID — администратором\n"
                 "/block ID — заблокировать, /remove ID — удалить")


# ---------- хранилище задач ----------
class TaskStore:
    def __init__(self):
        self.tasks = []
        self.by_id = {}
        self._seq = itertools.count(1)
        self.collapsed = set()

    def new_task(self, url, **kw):
        t = Task(id=next(self._seq), url=url, site=site_of(url), added=time.time(), **kw)
        return t

    def add(self, t, index=None):
        self.by_id[t.id] = t
        if index is None:
            self.tasks.append(t)
        else:
            self.tasks.insert(index, t)

    def get(self, tid):
        return self.by_id.get(tid)

    def remove(self, t):
        t.removed = True
        t.cancel.set()
        self.by_id.pop(t.id, None)
        try:
            self.tasks.remove(t)
        except ValueError:
            pass

    def replace(self, old, new_tasks):
        pos = self.tasks.index(old) if old in self.tasks else len(self.tasks)
        self.remove(old)
        for i, t in enumerate(new_tasks):
            self.add(t, pos + i)

    def groups(self):
        seen = []
        for t in self.tasks:
            if t.group and t.group not in seen:
                seen.append(t.group)
        return seen

    def unique_group(self, name):
        existing, cand, n = set(self.groups()), name, 2
        while cand in existing:
            cand, n = f"{name} ({n})", n + 1
        return cand

    def counts(self):
        done = sum(1 for t in self.tasks if t.state == DONE)
        return done, len(self.tasks)

    # ---- сохранение ----
    def save(self, path=STATE_FILE):
        save_json(path, {"version": STATE_VERSION, "collapsed": sorted(self.collapsed),
                         "tasks": [t.to_dict() for t in self.tasks]})

    def load(self, path=STATE_FILE, resume=False):
        data = load_json(path, {})
        if not isinstance(data, dict):
            data = {}
        coll = data.get("collapsed", [])
        self.collapsed = {x for x in coll if isinstance(x, str)} if isinstance(coll, list) else set()
        names = {f.name for f in fields(Task)}
        defaults = Task(id=0, url="")
        maxid = 0
        tasks = data.get("tasks", [])
        for d in tasks if isinstance(tasks, list) else []:
            try:
                kw = {k: v for k, v in d.items() if k in names and k in PERSIST}
                for k in list(kw):   # поле неверного типа заменяем значением по умолчанию, чтобы позже ничего не упало
                    cur = getattr(defaults, k)
                    if type(cur) is int and type(kw[k]) is float and kw[k] == kw[k] and abs(kw[k]) < 1e15:
                        kw[k] = int(kw[k])   # размер, сохранённый дробным числом, не теряем
                        continue
                    if cur is not None and not isinstance(kw[k], type(cur)) and not (
                            isinstance(cur, float) and isinstance(kw[k], int) and not isinstance(kw[k], bool)):
                        del kw[k]
                t = Task(**kw)
                if not isinstance(t.id, int) or isinstance(t.id, bool) or t.id in self.by_id:
                    continue
            except (TypeError, AttributeError):
                continue
            t.mode = norm_mode(t.mode)
            if not 0 <= t.tag < len(TAGS):
                t.tag = 0
            t.size, t.total = int(t.size), int(t.total)
            maxid = max(maxid, t.id)
            if t.state in ACTIVE_STATES:
                if resume:
                    t.state = WAITING if t.has_info else QUEUED
                else:
                    t.state = INCOMPLETE
                    t.error = t.error or ""
            self.add(t)
        self._seq = itertools.count(maxid + 1)


# ---------- фильтры ----------
def archive_key(url, vid, mode):
    """Ключ записи в архиве скачанного: сайт + id ролика + что качали (звук или видео). Нет id — нет ключа."""
    if not vid:
        return ""
    return f"{site_of(url)}:{vid}:{'a' if norm_mode(mode) == 'audio' else 'v'}"


def _volatile_url(url):
    """Ссылки, за которыми со временем оказывается другое видео (прямой эфир, «последнее видео канала»):
    по ссылке такой ролик в архиве не ищем."""
    path = urllib.parse.urlparse(url).path.lower().rstrip("/")
    return path.endswith(("/live", "/latest", "/streams", "/videos", "/featured")) or path in ("", "/")


def archive_keys(url, vid, mode):
    """Все ключи ролика: по id сайта и по ссылке. У некоторых сайтов id в плейлисте и в самом ролике пишется
    по-разному (BV1… и 1…), а ссылка одна и та же."""
    kind = "a" if norm_mode(mode) == "audio" else "v"
    keys = [archive_key(url, vid, mode)] if vid else []
    if url and not _volatile_url(url):
        keys.append(f"url:{norm_url(url)}:{kind}")
    return keys


class Archive:
    """Что уже скачано: ключ -> {files, title, ts}. Запись считается действующей, пока хоть один файл на диске."""

    def __init__(self, path=ARCHIVE_FILE):
        self.path = path
        self.lock = threading.Lock()
        self.data = load_json(path, {})
        if not isinstance(self.data, dict):
            self.data = {}

    def has(self, key):
        """Возвращает список сохранившихся файлов или None, если качать нужно."""
        with self.lock:
            rec = self.data.get(key) if key else None
        if not isinstance(rec, dict):
            return None
        alive = [f for f in rec.get("files", []) if os.path.exists(f)]
        return alive or None

    def add(self, key, files, title=""):
        if not key or not files:
            return
        with self.lock:
            self.data[key] = {"files": list(files), "title": title, "ts": int(time.time())}
            save_json(self.path, self.data)

    def count(self):
        with self.lock:
            return len(self.data)

    def find(self, url, vid, mode):
        """Сохранившиеся файлы, если этот ролик уже скачан (в том же виде), иначе None."""
        for k in archive_keys(url, vid, mode):
            alive = self.has(k)
            if alive:
                return alive
        return None

    def record(self, url, vid, mode, files, title=""):
        if not files:
            return
        with self.lock:
            for k in archive_keys(url, vid, mode):
                self.data[k] = {"files": list(files), "title": title, "ts": int(time.time())}
            save_json(self.path, self.data)

    def backfill(self, tasks):
        """Завершённые загрузки, сделанные до появления архива (или уже убранные из него), тоже считаются скачанными."""
        changed = False
        with self.lock:
            for t in tasks:
                if t.state != DONE or t.backend != "yt-dlp" or t.fmt_sel or not t.files:
                    continue
                keys = archive_keys(t.url, t.vid, t.mode)
                if all(self.data.get(k) and any(os.path.exists(f) for f in self.data[k].get("files", [])) for k in keys):
                    continue
                if not any(os.path.exists(f) for f in t.files):
                    continue
                for k in keys:
                    self.data[k] = {"files": list(t.files), "title": t.title, "ts": int(time.time())}
                changed = True
            if changed:
                save_json(self.path, self.data)
        return changed

    def rebase(self, old, new):
        """Файл или папка переименованы/перенесены: обновляем пути в записях."""
        with self.lock:
            changed = False
            for rec in self.data.values():
                if isinstance(rec, dict):
                    files = [(_rebase(f, old, new) or f) for f in rec.get("files", [])]
                    if files != rec.get("files"):
                        rec["files"], changed = files, True
            if changed:
                save_json(self.path, self.data)

    def clear(self):
        with self.lock:
            self.data = {}
            save_json(self.path, self.data)


def _rebase(path, old, new):
    """Путь внутри папки old -> тот же путь внутри new (или None, если путь не там)."""
    if path == old or path.startswith(old + os.sep) or path.startswith(old + "/"):
        return new + path[len(old):]
    return None


def find_renamed_dirs(tasks, skip=None, cache=None, now=0.0, retry=60, isdir=os.path.isdir,
                      listdir=os.listdir, exists=os.path.exists):
    """Папки, которые пользователь переименовал в Проводнике: {старая: новая}.
    Папка считается переименованной, если старой уже нет, а рядом (в той же родительской папке) появилась
    одна, где лежит не меньше половины скачанных файлов оттуда. Неоднозначные случаи не трогаем.
    cache — {папка: когда искали}: исчезнувшие папки не перебираем чаще, чем раз в retry секунд."""
    names = {}   # старая папка -> имена файлов, которые в ней были
    for t in tasks:
        if skip and skip(t):
            continue
        for f in t.files:
            names.setdefault(os.path.dirname(f), set()).add(os.path.basename(f))
    alive = {d for d in names if isdir(d)}
    out = {}
    for old, files in names.items():
        if old in alive or not old:
            continue
        if cache is not None and now - cache.get(old, -retry) < retry:
            continue
        if cache is not None:
            cache[old] = now
        parent = os.path.dirname(old)
        try:
            sibs = [os.path.join(parent, n) for n in listdir(parent)] if parent and isdir(parent) else []
        except OSError:
            sibs = []
        need = max(1, (len(files) + 1) // 2)
        scored = []
        for s in sibs:
            if s in alive or s in out.values() or not isdir(s):
                continue   # эта папка уже принадлежит другим загрузкам
            n = sum(1 for f in files if exists(os.path.join(s, f)))
            if n >= need:
                scored.append((n, s))
        scored.sort(reverse=True)
        if scored and (len(scored) == 1 or scored[0][0] > scored[1][0]):
            out[old] = scored[0][1]
    return out


def apply_dir_renames(tasks, mapping, collapsed=None):
    """Переносит пути файлов в переименованные папки; если это была папка плейлиста — переименовывает и группу.
    Возвращает ({старая группа: новая}, число обновлённых файлов)."""
    groups, n = {}, 0
    for t in tasks:
        new_files = []
        for f in t.files:
            for old, new in mapping.items():
                r = _rebase(f, old, new)
                if r is not None:
                    f, n = r, n + 1
                    break
            new_files.append(f)
        if t.group and t.files:
            for old, new in mapping.items():
                if safe_name(t.group) == os.path.basename(old) and any(
                        os.path.dirname(f) == new for f in new_files):
                    groups[t.group] = os.path.basename(new)
        t.files = new_files
    for t in tasks:   # остальные задачи группы (ещё не скачанные) тоже должны идти в новую папку
        if t.group in groups:
            t.group = groups[t.group]
    if collapsed is not None:
        for old, new in groups.items():
            if old in collapsed:
                collapsed.discard(old)
                collapsed.add(new)
    return groups, n


def file_sig(path):
    """Отпечаток файла: [размер, время изменения в секундах]. При переименовании и переносе он не меняется."""
    try:
        st = os.stat(path)
        return [st.st_size, int(st.st_mtime)]
    except OSError:
        return None


def files_sigs(files, limit=200):
    """Отпечатки всех файлов задачи (пустой список, если какого-то нет или файлов слишком много)."""
    if not files or len(files) > limit:
        return []
    sigs = [file_sig(f) for f in files]
    return sigs if all(sigs) else []


def _expected_sig(t, i):
    fs = getattr(t, "fsig", None)
    if fs and len(fs) == len(t.files) and fs[i]:
        return list(fs[i])
    if len(t.files) == 1 and t.size > 0:   # задачи, скачанные до появления отпечатков: известен хотя бы размер
        return [t.size, None]
    return None


def find_moved_files(tasks, roots, skip=None, cache=None, now=0.0, retry=300, exists=os.path.exists,
                     walk=os.walk, stat=os.stat, max_entries=100000, budget=8.0, clock=time.time):
    """Файлы, которые пропали со своего места, а на самом деле переименованы или перенесены:
    {(id задачи, номер файла): (старый путь, новый путь)}. Ищем в папке загрузок (со вложенными) и рядом со старым
    местом файл с тем же расширением, размером и временем изменения. Неоднозначное не трогаем.
    cache — {путь: когда искали}: потерянные файлы перебираем не чаще, чем раз в retry секунд."""
    known = {f for t in tasks for f in t.files}
    missing = []
    for t in tasks:
        if skip and skip(t):
            continue
        for i, f in enumerate(t.files):
            if exists(f):
                continue
            sig = _expected_sig(t, i)
            if not sig or (cache is not None and now - cache.get(f, -retry) < retry):
                continue
            missing.append((t.id, i, f, sig))
    if not missing:
        return {}
    if cache is not None:
        for _, _, f, _ in missing:
            cache[f] = now
    exts = {os.path.splitext(f)[1].lower() for _, _, f, _ in missing}
    cand, seen, n, start = {}, set(), 0, clock()

    def scan(top, recursive):
        nonlocal n
        for dp, dn, fn in walk(top):
            if not recursive:
                dn[:] = []
            for name in fn:
                p = os.path.join(dp, name)
                if p in known or p in seen or os.path.splitext(name)[1].lower() not in exts:
                    continue
                seen.add(p)
                n += 1
                try:
                    st = stat(p)
                except OSError:
                    continue
                cand.setdefault(st.st_size, []).append((p, int(st.st_mtime)))
            if n > max_entries or clock() - start > budget:
                return

    for r in roots:
        if r:
            scan(r, True)
    for _, _, f, _ in missing:
        scan(os.path.dirname(f), False)
    out, claimed = {}, set()
    by_path = {}   # один и тот же файл может быть у нескольких задач (например, ролик есть и отдельно, и в плейлисте)
    for item in missing:
        by_path.setdefault(item[2], []).append(item)
    todo = [v[0] for v in by_path.values()]
    while todo:   # несколько кругов: найденное на одном круге убирает неоднозначность на следующем
        left = []
        for item in todo:
            tid, i, old, (size, mtime) = item
            ext = os.path.splitext(old)[1].lower()
            opts = [p for p, m in cand.get(size, []) if p not in claimed
                    and os.path.splitext(p)[1].lower() == ext and (mtime is None or abs(m - mtime) <= 2)]
            if len(opts) > 1:
                same = [p for p in opts if os.path.basename(p) == os.path.basename(old)]
                opts = same if same else opts
            if len(opts) == 1:
                for tid2, i2, _, _ in by_path[old]:
                    out[(tid2, i2)] = (old, opts[0])
                claimed.add(opts[0])
            else:
                left.append(item)
        if len(left) == len(todo):
            break
        todo = left
    return out


def compute_dups(tasks):
    seen = {}
    for t in tasks:
        key = (t.site, t.vid) if t.vid else ("url", norm_url(t.url))
        seen.setdefault(key, []).append(t.id)
    return {i for ids in seen.values() if len(ids) > 1 for i in ids}


def _tag_matches(t, val):
    if not t.tag:
        return False
    key, name, _ = TAGS[t.tag]
    val = val.strip().lower()
    return val in (key, name.lower(), str(t.tag))


PLAYLISTS_TAB = "@playlists"   # служебное значение вкладки «Плейлисты» (имена сайтов с «@» не начинаются)
MUSIC_TAB = "@music"           # вкладка «Музыка»: загрузки в режиме «Только звук»
ERRORS_TAB = "@errors"         # вкладка «Ошибки»: задачи с ошибкой или недокачанные


def is_music(t):
    return norm_mode(t.mode) == "audio"


def is_error(t):
    return t.state in (INVALID, INCOMPLETE)


def task_in_tab(t, tab):
    """Входит ли задача во вкладку: пусто — «Все», PLAYLISTS_TAB — загрузки из плейлистов, иначе — имя сайта."""
    if not tab:
        return True
    if tab == PLAYLISTS_TAB:
        return bool(t.group)
    if tab == MUSIC_TAB:
        return is_music(t)
    if tab == ERRORS_TAB:
        return is_error(t)
    return t.site == tab


TAB_ALL = "@all"   # ключ вкладки «Все» в сохранённых настройках


# готовые шаблоны имени файла: (шаблон, подпись с примером результата)
NAME_TEMPLATES = [
    ("%(title)s [%(id)s].%(ext)s", "Название [id]  →  Песня [abc123].mp3 (по умолчанию)"),
    ("title", "Название  →  Песня.mp3 (одинаковые названия перезапишут друг друга)"),
    ("[artist] title", "[Автор] Название  →  [Группа] Песня.mp3"),
    ("[artist] title (id)", "[Автор] Название (id)  →  [Группа] Песня (abc123).mp3"),
    ("artist - title", "Автор - Название  →  Группа - Песня.mp3"),
    ("date:%Y-%m-%d title", "Дата Название  →  2024-05-01 Песня.mp3"),
    ("[date:%Y-%m-%d] [artist] title", "[Дата] [Автор] Название  →  [2024-05-01] [Группа] Песня.mp3"),
    ("title (id)", "Название (id)  →  Песня (abc123).mp3"),
]


def split_keys(text):
    return [x for x in (text or "").split(",") if x]


def order_tabs(keys, order, pinned):
    """Порядок вкладок: сперва закреплённые, затем остальные; внутри — как сохранено пользователем.
    keys — ключи вкладок в порядке по умолчанию; новая вкладка (которой нет в order) встаёт сразу после той,
    что стоит перед ней по умолчанию."""
    pos = {k: float(i) for i, k in enumerate(order)}
    rank, prev = {}, -0.5
    for k in keys:   # новая вкладка встаёт рядом с той, что стоит перед ней по умолчанию
        if k in pos:
            prev = pos[k]
        else:
            prev += 0.001
        rank[k] = pos.get(k, prev)
    ordered = sorted(keys, key=lambda k: rank[k])
    return [k for k in ordered if k in pinned] + [k for k in ordered if k not in pinned]


def display_order(tasks, newest_first=False):
    """Порядок показа верхнего уровня списка: [('task', задача) | ('group', название, [задачи])].
    Группа (плейлист) стоит там, где появилась её первая задача; внутри группы порядок не меняется,
    чтобы треки плейлиста шли как в плейлисте. newest_first переворачивает верхний уровень."""
    entries, groups = [], {}
    for t in tasks:
        if t.group:
            g = groups.get(t.group)
            if g is None:
                g = ("group", t.group, [])
                groups[t.group] = g
                entries.append(g)
            g[2].append(t)
        else:
            entries.append(("task", t))
    if newest_first:
        entries.reverse()
    return entries


def match_task(t, query, dups=None):
    """Фильтр списка: слово, type:, tag:, comment:, bad:, dup:, rem:. Слова через пробел — это «И»."""
    query = (query or "").strip().lower()
    if not query:
        return True
    for tok in query.split():
        if tok == "bad:":
            ok = t.state != DONE
        elif tok == "dup:":
            ok = bool(dups) and t.id in dups
        elif tok == "rem:":
            ok = t.state == DONE and bool(t.files) and not any(os.path.exists(f) for f in t.files)
        elif tok.startswith("type:"):
            ok = tok[5:] in (t.site or "").lower()
        elif tok.startswith("tag:"):
            ok = _tag_matches(t, tok[4:])
        elif tok.startswith("comment:"):
            ok = tok[8:] in (t.comment or "").lower()
        else:
            hay = " ".join((t.title, t.artist, t.url, t.group, t.comment, t.site)).lower()
            ok = tok in hay
        if not ok:
            return False
    return True


# ---------- JSON ----------
def _read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_json(path, default):
    """Читает JSON. Битый файл не теряется: он переименовывается в *.corrupt, а данные берутся из копии *.bak."""
    try:
        return _read_json(path)
    except OSError:
        pass
    except ValueError:
        try:
            os.replace(path, path + ".corrupt")
        except OSError:
            pass
    try:
        return _read_json(path + ".bak")
    except (OSError, ValueError):
        return default


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"   # свой временный файл у каждого потока
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
    if os.path.exists(path):
        try:
            shutil.copy2(path, path + ".bak")   # предыдущая рабочая копия
        except OSError:
            pass
    os.replace(tmp, path)

# ======================= язык интерфейса =======================
# Основной язык — русский, дополнительный — английский. Язык выбирается в «Настройки → Язык» и вступает в силу
# после перезапуска. Тексты в программе написаны по-русски; tr() подменяет их английскими, если выбран английский.
def _read_lang():
    try:
        return "en" if load_json(os.path.join(DATA_DIR, "settings.json"), {}).get("lang") == "en" else "ru"
    except (OSError, ValueError, AttributeError):
        return "ru"


LANG = _read_lang()
_PAT = []
_DEBUG = bool(os.environ.get("GT_I18N_DEBUG"))
MISSING = set()


def _compile_patterns():
    pats = []
    for ru, en in PATTERNS:
        parts = re.split(r"(\{\d+\})", ru)
        rx = "".join("(.*?)" if re.fullmatch(r"\{\d+\}", p) else re.escape(p) for p in parts)
        order = [int(p[1:-1]) for p in parts if re.fullmatch(r"\{\d+\}", p)]
        pats.append((len("".join(p for p in parts if not re.fullmatch(r"\{\d+\}", p))),
                     re.compile(rx, re.S), order, en))
    pats.sort(key=lambda x: -x[0])   # сначала самые «конкретные» шаблоны
    return [(rx, order, en) for _, rx, order, en in pats]


@functools.lru_cache(maxsize=8192)
def _tr_en(s):
    if s in EN:
        return EN[s]
    if not _PAT:
        _PAT.extend(_compile_patterns())
    for rx, order, en in _PAT:
        m = rx.fullmatch(s)
        if m:
            vals = {n: tr(g) for n, g in zip(order, m.groups())}
            return re.sub(r"\{(\d+)\}", lambda x: vals.get(int(x.group(1)), x.group(0)), en)
    st = s.strip()
    if not st:   # одни пробелы и переводы строк (пустая строка между абзацами): переводить нечего
        return s
    if st != s:   # пробелы по краям сохраняем
        i = s.index(st)
        return s[:i] + _tr_en(st) + s[i + len(st):]
    if len(s) > 4000:   # слишком длинный текст (список файлов, лог) не разбираем: незачем и дорого
        return s
    for sep in ("\n", " · ", " | "):   # составные строки переводим по частям (разделитель остаётся у правой части)
        if sep in s:
            cuts = [m.start() for m in re.finditer(re.escape(sep), s)]
            bounds = ([0] if cuts[0] else []) + cuts + [len(s)]
            return "".join(_tr_en(s[a:b]) for a, b in zip(bounds, bounds[1:]))   # у правой части разделитель остаётся
    return s


def tr(s):
    """Русский текст -> текст на выбранном языке. Неизвестное остаётся как есть."""
    if LANG != "en" or not s or not isinstance(s, str):
        return s
    r = _tr_en(s)
    if _DEBUG and r == s and re.search("[А-Яа-яЁё]", s):
        MISSING.add(s)   # для проверки полноты перевода (тесты)
    return r


def tr_pairs(pairs):
    return [(k, tr(v)) for k, v in pairs]


def help_html(ru):
    return HELP_EN if LANG == "en" else ru




EN = {
    '\n\nФайлы:\n': '\n\nFiles:\n',
    '\nНе удалось:\n': '\nFailed:\n',
    ' (графические, не поддерживаются)': ' (graphical, not supported)',
    'Cookies из браузера': 'Cookies from browser',
    'MP3 192 кбит/с': 'MP3 192 kbps',
    'MP3 256 кбит/с': 'MP3 256 kbps',
    'MP3 320 кбит/с (максимум для mp3)': 'MP3 320 kbps (highest quality for MP3)',
    '[Автор] Название  →  [Группа] Песня.mp3': '[Author] Title  →  [Band] Song.mp3',
    '[Автор] Название (id)  →  [Группа] Песня (abc123).mp3': '[Author] Title (id)  →  [Band] Song (abc123).mp3',
    '[Дата] [Автор] Название  →  [2024-05-01] [Группа] Песня.mp3': '[Date] [Author] Title  →  [2024-05-01] [Band] Song.mp3',
    'ffmpeg не смог собрать файл:\n': 'ffmpeg could not build the file:\n',
    'yt-dlp давно не обновлялся': 'yt-dlp is out of date',
    'yt-dlp не найден: pip install yt-dlp': 'yt-dlp not found: pip install yt-dlp',
    'yt-dlp не установлен': 'yt-dlp is not installed',
    'yt-dlp не установлен (pip install yt-dlp)': 'yt-dlp is not installed (pip install yt-dlp)',
    'Авто: вкл': 'Auto: on',
    'Авто: выкл': 'Auto: off',
    'Автоповтор выключен': 'Auto-retry off',
    'Автоповтор при ошибке': 'Auto-retry on error',
    'Автор': 'Author',
    'Автор - Название  →  Группа - Песня.mp3': 'Author - Title  →  Band - Song.mp3',
    'Автоскачивание скопированных ссылок': 'Auto-download copied links',
    'Автоскачивание скопированных ссылок включено': 'Auto-download of copied links enabled',
    'Автоскачивание скопированных ссылок выключено': 'Auto-download of copied links disabled',
    'Автоскачивание: скопированные ссылки сразу добавляются и скачиваются': 'Auto-download: copied links are added and downloaded immediately',
    'Автосохранение, мин (0 — выключено)': 'Autosave, min (0 — off)',
    'Адрес не задан. Впишите ссылку на zip-архив в Параметры → Прочее → «Адрес обновления программы», или используйте «Обновить программу из архива».': 'No address set. Enter a link to a zip archive in Preferences → Other → "Program update URL", or use "Update program from archive".',
    'Адрес обновления должен начинаться с https://': 'The update URL must start with https://',
    'Адрес обновления программы (https://…zip, необязательно)': 'Program update URL (https://…zip, optional)',
    'Ссылка «Поддержать разработку» для окна «О программе» (https://…, необязательно)': '"Support development" link for the About window (https://…, optional)',
    'Поддержать разработку': 'Support development',
    'Свободная программа под лицензией GPL v3, без гарантий. Вы сами отвечаете за то, что скачиваете: соблюдайте законы и правила сайтов. Текст лицензии — файл LICENSE в папке программы.': 'Free software under the GPL v3 licence, with no warranty. You are responsible for what you download: obey the law and the rules of the sites. The licence text is the LICENSE file in the program folder.',
    'Архив (*.zip)': 'Archive (*.zip)',
    'Архив очищен': 'Archive cleared',
    'Архив с новой версией': 'Archive with the new version',
    'Архив скачанного': 'Download archive',
    'Архив скачанного пуст': 'The download archive is empty',
    'Архив слишком большой': 'The archive is too large',
    'Битрейт': 'Bitrate',
    'Брать и автоматические субтитры (распознанные YouTube)': 'Include automatic subtitles (YouTube speech recognition)',
    'Будет скачиваться одна звуковая дорожка': 'A single audio track will be downloaded',
    'Бэкенд': 'Backend',
    'В архиве не найден номер версии (core.py) — это не Great Turtle': 'No version number found in the archive (core.py) — this is not Great Turtle',
    'В архиве нет файлов программы: ': 'The archive contains no program files: ',
    'В буфере обмена нет ссылки': 'No link in the clipboard',
    'В очереди': 'Queued',
    'В этом файле нет звуковых дорожек и субтитров.': 'This file has no audio tracks or subtitles.',
    'Видео': 'Video',
    'Видео (*.mkv *.mp4)': 'Video (*.mkv *.mp4)',
    'Видео (*.mp4 *.mkv *.webm *.mov *.m4v *.avi *.ts);;Все файлы (*)': 'Video (*.mp4 *.mkv *.webm *.mov *.m4v *.avi *.ts);;All files (*)',
    'Видео (*.mp4 *.mkv *.webm *.mov *.m4v *.avi);;Все файлы (*)': 'Video (*.mp4 *.mkv *.webm *.mov *.m4v *.avi);;All files (*)',
    'Видео — лучшее качество': 'Video — best quality',
    'Видео — лучшее качество, MP4 (без AV1)': 'Video — best quality, MP4 (no AV1)',
    'Видео — совместимость, H.264 + AAC (до 1080p на YouTube)': 'Video — compatibility, H.264 + AAC (up to 1080p on YouTube)',
    'Видео:': 'Video:',
    'Включить: скопированные ссылки будут сразу добавляться и скачиваться.': 'Turn on: copied links will be added and downloaded right away.',
    'Внешний вид': 'Appearance',
    'Все загрузки на паузе': 'All downloads paused',
    'Все задачи выполнены': 'All downloads complete',
    'Вставить\tCtrl+V': 'Paste\tCtrl+V',
    'Вставить ссылки из буфера (Ctrl+V)': 'Paste links from clipboard (Ctrl+V)',
    'Вставить ссылку из буфера обмена и сразу скачать': 'Paste link from clipboard and download immediately',
    'Вшивать обложку в музыкальные файлы (режим «Только звук»)': 'Embed cover art in music files ("Audio only" mode)',
    'Вшить в видео': 'Embed in video',
    'Выберите видео: программа покажет все звуковые дорожки и субтитры внутри. Отметьте нужные, и каждая сохранится отдельным файлом (например, video.ru.srt, video.en.m4a). Звук по умолчанию не перекодируется.': 'Select a video: the app will list all audio tracks and subtitles inside it. Check the ones you need, and each will be saved as a separate file (for example, video.ru.srt, video.en.m4a). Audio is not re-encoded by default.',
    'Выберите видеофайл.': 'Select a video file.',
    'Выберите группу или введите новое название:': 'Select a group or enter a new name:',
    'Выберите папку': 'Select a folder',
    'Выберите строку и нажмите «Скачать». Если у формата нет звука, лучший звук добавится сам.': 'Select a row and click "Download". If the format has no audio, the best audio will be added automatically.',
    'Выберите файл': 'Select a file',
    'Выбор глав и скачивание': 'Select chapters and download',
    'Выбор глав — ': 'Chapter selection — ',
    'Выбор сброшен, качаю по настройкам': 'Selection cleared: downloading with the current settings',
    'Выбор страниц и скачивание': 'Select pages and download',
    'Выбор формата — ': 'Format selection — ',
    'Выбрать все': 'Select all',
    'Выбрать формат...': 'Select format...',
    'Выбрать...': 'Browse...',
    'Выделить всё\tCtrl+A': 'Select all\tCtrl+A',
    'Выйти без сохранения': 'Exit without saving',
    'Вырезать\tCtrl+X': 'Cut\tCtrl+X',
    'Выход': 'Exit',
    'Готово': 'Done',
    'Готово. Перезапустите программу.\n\n': 'Done. Restart the program.\n\n',
    'Готовые шаблоны имени файла': 'Filename presets',
    'Группа': 'Group',
    'Группа с таким названием уже есть.': 'A group with this name already exists.',
    'Дата Название  →  2024-05-01 Песня.mp3': 'Date Title  →  2024-05-01 Song.mp3',
    'Длительность': 'Duration',
    'Для ссылок «видео из плейлиста» скачивать весь плейлист': 'For "video from playlist" links, download the whole playlist',
    'Добавить субтитры / звук к файлу...': 'Add subtitles / audio to file...',
    'Добавить...': 'Add...',
    'Добавлять дубляж к оригиналу': 'Add dubbed audio to the original',
    'Добавлять к оригинальной дорожке дубляж (если он есть)': 'Add dubbed audio to the original track (if available)',
    'Добавьте хотя бы один файл субтитров или звука.': 'Add at least one subtitle or audio file.',
    'Ещё сайты, где искать дубляж (через запятую, например vimeo, example.com)': 'Additional sites to check for dubbed audio (comma-separated, e.g. vimeo, example.com)',
    'Журнал': 'Log',
    'Журнал пуст.': 'The log is empty.',
    'Журнал...': 'Log...',
    'Жёлтый': 'Yellow',
    'Забыть архив скачанного': 'Forget download archive',
    'Задачи': 'Downloads',
    'Закрепить вкладку (слева, перед остальными)': 'Pin tab (to the left, before the others)',
    'Закрыть': 'Close',
    'Закрыть программу': 'Exit the program',
    'Звук (*.mp3 *.m4a *.aac *.opus *.ogg *.flac *.wav *.mka *.ac3);;Все файлы (*)': 'Audio (*.mp3 *.m4a *.aac *.opus *.ogg *.flac *.wav *.mka *.ac3);;All files (*)',
    'Звук:': 'Audio:',
    'Звуковые дорожки': 'Audio tracks',
    'Зелёный': 'Green',
    'Идёт сборка. Для больших файлов это может занять минуту-две...': 'Merging in progress. For large files this may take a minute or two...',
    'Идёт скачивание. Выйти?': 'A download is in progress. Exit?',
    'Из видео, субтитров и звуковых дорожек собирается новый файл. Видео не перекодируется, исходные файлы не меняются. Язык берётся из имени файла (например, video.ru.srt), его можно поправить.': 'Creates a new file from the video, subtitles and audio tracks. The video is not re-encoded and the source files are not changed. The language is taken from the file name (for example, video.ru.srt) and can be edited.',
    'Извлекаю...': 'Extracting...',
    'Извлечение': 'Extraction',
    'Извлечь': 'Extract',
    'Извлечь звук / субтитры из файла...': 'Extract audio / subtitles from file...',
    'Извлечь звук и субтитры из видео': 'Extract audio and subtitles from video',
    'Извлечь звук и субтитры из видео...': 'Extract audio and subtitles from video...',
    'Изменить язык...': 'Change language...',
    'Имитировать браузер (нужен curl-cffi)': 'Impersonate browser (requires curl-cffi)',
    'Импорт ссылок': 'Import links',
    'Импорт ссылок из файла...': 'Import links from file...',
    'Инструменты': 'Tools',
    'Информация о задаче': 'Download info',
    'Информация...': 'Info...',
    'Использовать gallery-dl для сайтов с картинками/галереями': 'Use gallery-dl for image/gallery sites',
    'Как в настройках (сбросить выбор)': 'As in settings (reset selection)',
    'Качество звука (режим «Только звук»)': 'Audio quality ("Audio only" mode)',
    'Когда все задачи выполнены': 'When all downloads are complete',
    'Код': 'ID',
    'Код языка (ru, en, de, fr...). Пусто — не указывать:': 'Language code (ru, en, de, fr...). Leave empty to omit:',
    'Кодеки': 'Codecs',
    'Комментарий': 'Comment',
    'Комментарий...': 'Comment...',
    'Комментарий:': 'Comment:',
    'Копировать\tCtrl+C': 'Copy\tCtrl+C',
    'Копировать названия групп': 'Copy group names',
    'Красный': 'Red',
    'Куда сохранить': 'Save to',
    'Лимит скорости, КБ/с (0 — без лимита)': 'Speed limit, KB/s (0 — unlimited)',
    'Музыка': 'Music',
    'Название': 'Title',
    'Название  →  Песня.mp3 (одинаковые названия перезапишут друг друга)': 'Title  →  Song.mp3 (identical titles will overwrite each other)',
    'Название (id)  →  Песня (abc123).mp3': 'Title (id)  →  Song (abc123).mp3',
    'Название [id]  →  Песня [abc123].mp3 (по умолчанию)': 'Title [id]  →  Song [abc123].mp3 (default)',
    'Название группы:': 'Group name:',
    'Настройки': 'Settings',
    'Не давать системе уснуть во время загрузки (Windows)': 'Prevent the system from sleeping while downloading (Windows)',
    'Не завершено': 'Incomplete',
    'Не использовать': 'None',
    'Не качать повторно уже скачанное (архив; работает, пока файл на месте)': "Skip videos that were already downloaded (archive; works while the file is still in place)",
    'Не найден ffmpeg': 'ffmpeg not found',
    'Не найден ffmpeg. Запустите install.bat или установите ffmpeg.': 'ffmpeg not found. Run install.bat or install ffmpeg.',
    'Не найден ffprobe (он ставится вместе с ffmpeg). Запустите install.bat.': 'ffprobe not found (it is installed together with ffmpeg). Run install.bat.',
    'Не получилось.': 'Failed.',
    'Не скачивать': 'None',
    'Не удалось обновить.\n\n': 'Update failed.\n\n',
    'Не удалось получить информацию': 'Failed to get information',
    'Не удалось получить форматы: ': 'Could not get formats: ',
    'Не удалось прочитать файл: ': 'Failed to read the file: ',
    'Не удалось разобрать диапазон страниц.': 'Could not parse the page range.',
    'Неизвестная ошибка': 'Unknown error',
    'Неизвестный сайт': 'Unknown site',
    'Нечего открывать: выберите загрузку со ссылкой': 'Nothing to open: select a download with a link',
    'Ничего не делать': 'Do nothing',
    'Новая группа': 'New group',
    'Новые загрузки сверху': 'New downloads on top',
    'Новые загрузки снизу': 'New downloads at the bottom',
    'О программе': 'About',
    'Обзор...': 'Browse...',
    'Обновить yt-dlp и gallery-dl': 'Update yt-dlp and gallery-dl',
    'Обновить yt-dlp и сразу перезапустить программу': 'Update yt-dlp and restart the program immediately',
    'Обновить программу из архива...': 'Update program from archive...',
    'Обновление': 'Update',
    'Обновление yt-dlp': 'yt-dlp update',
    'Обновление отменено': 'Update canceled',
    'Обновление установлено. Перезапустите программу, чтобы оно заработало.': 'Update installed. Restart the program for it to take effect.',
    'Обновлять yt-dlp до тестовых версий (раньше получает исправления, но менее стабильно)': 'Update yt-dlp to nightly builds (get fixes sooner, but less stable)',
    'Обновляю yt-dlp... (до минуты)': 'Updating yt-dlp... (up to a minute)',
    'Обработка...': 'Processing...',
    'Объединение': 'Merge',
    'Объединить': 'Merge',
    'Объединить видео с субтитрами и звуком': 'Merge video with subtitles and audio',
    'Объединить видео с субтитрами и звуком...': 'Merge video with subtitles and audio...',
    'Одновременных задач': 'Simultaneous downloads',
    'Ожидание': 'Waiting',
    'Оранжевый': 'Orange',
    'Оригинальный звук без перекодирования (лучшее качество, m4a/opus)': 'Original audio without re-encoding (best quality, m4a/opus)',
    'Основные': 'General',
    'Остановить сборку?': 'Stop merging?',
    'Остановить?': 'Stop?',
    'Остановлено': 'Stopped',
    'Отдельными файлами (.srt)': 'As separate files (.srt)',
    'Открепить вкладку': 'Unpin tab',
    'Другие способы скачать': 'Other ways to download',
    'Telegram: id администраторов через запятую (полный доступ и уведомления)': 'Telegram: administrator IDs, comma-separated (full access and notifications)',
    'Telegram: новые участники сразу становятся гостями (иначе — заявка)': 'Telegram: new members become guests right away (otherwise a request)',
    'Telegram: присылать администраторам уведомления о завершении и ошибках': 'Telegram: send administrators notifications about completed downloads and errors',
    'Гостей и заявки смотрите в «Настройки → Участники Telegram».': 'Guests and requests are in "Settings → Telegram members".',
    'Участники Telegram...': 'Telegram members...',
    'Участники Telegram': 'Telegram members',
    'Заявки ждут вашего решения.': 'Requests are waiting for your decision.',
    'Разрешить (гость)': 'Allow (guest)',
    'Сделать администратором': 'Make administrator',
    'Заблокировать': 'Block',
    'администратор': 'administrator',
    'гость': 'guest',
    'заявка': 'request',
    'заблокирован': 'blocked',
    'Без имени': 'No name',
    'Доступ открыт. Пришлите ссылку или /help.': 'Access granted. Send a link or /help.',
    'Новый участник (гость):': 'New member (guest):',
    'Новая заявка:': 'New request:',
    '. Разрешите в программе: Настройки → Участники Telegram.': '. Allow it in the program: Settings → Telegram members.',
    'Пришлите ссылку текстом.': 'Send the link as text.',
    '[telegram] токен не подходит — бот остановлен': '[telegram] the token does not fit: the bot is stopped',
    'Telegram-бот: принимать ссылки и команды': 'Telegram bot: accept links and commands',
    'Telegram: токен бота (от @BotFather)': 'Telegram: bot token (from @BotFather)',
    'Эта команда только для администратора.': 'This command is for the administrator only.',
    'Программа не ответила вовремя. Попробуйте ещё раз.': 'The program did not respond in time. Please try again.',
    'Не знаю такой команды.': 'Unknown command.',
    'Не нашёл загрузку с таким номером. Номера — в /list (например #12).': 'No download with that number. The numbers are in /list (for example #12).',
    'У этой загрузки нет файла на диске.': 'This download has no file on the disk.',
    'Файл больше 50 МБ: Telegram не принимает такие от ботов.': 'The file is larger than 50 MB: Telegram does not accept such files from bots.',
    'Отправляю файл...': 'Sending the file...',
    'Не нашёл ссылок. Пришлите ссылку или /help.': 'No links found. Send a link or /help.',
    '📋 Список': '📋 List',
    '🔄 Перезапустить неудачные': '🔄 Restart failed',
    '⏸ Пауза': '⏸ Pause',
    '▶ Продолжить': '▶ Resume',
    '❓ Помощь': '❓ Help',
    'Список загрузок': 'List of downloads',
    'Пауза всех': 'Pause all',
    'Перезапустить неудачные': 'Restart failed',
    'Прислать файл: /file 12': 'Send a file: /file 12',
    'Подсказка': 'Hint',
    'Кнопки внизу экрана дублируют команды.': 'The buttons at the bottom of the screen duplicate the commands.',
    'Telegram: id руководителя (власть абсолютна: всегда администратор, его нельзя понизить или заблокировать)': 'Telegram: owner ID (absolute power: always an administrator, cannot be demoted or blocked)',
    'Сайты, с которых могут скачивать гости (администраторам можно всё):': 'Sites guests may download from (administrators may use any):',
    'Telegram-бот включён': 'Telegram bot enabled',
    'Telegram-бот выключен': 'Telegram bot disabled',
    'Сначала впишите токен бота: Параметры → вкладка Telegram.': 'Enter the bot token first: Preferences → Telegram tab.',
    'Эта команда только для руководителя.': 'This command is for the owner only.',
    'Гостям разрешены только сайты: ': 'Guests may use only these sites: ',
    'ничего': 'none',
    'руководитель': 'owner',
    'Сайт не принял запрос (400): похоже, он изменил интерфейс. Обновите yt-dlp; если не помогло, ждите исправления в yt-dlp': 'The site rejected the request (400): it seems to have changed its interface. Update yt-dlp; if that does not help, wait for a yt-dlp fix',
    'файл больше 50 МБ': 'file is larger than 50 MB',
    'файл недоступен (удалён или перемещён)': 'file is unavailable (deleted or moved)',
    'Результат нельзя сохранять поверх добавляемого файла — выберите другое имя.': 'The result cannot be saved over a file you are adding — choose a different name.',
    '[telegram] SOCKS-прокси бот не поддерживает: укажите HTTP-прокси': '[telegram] The bot does not support SOCKS proxies: use an HTTP proxy',
    'Одноклассники': 'Odnoklassniki',
    'ошибка Telegram': 'Telegram error',
    '🔓 Поставить пароль...': '🔓 Set password...',
    '🔒 Разблокировать...': '🔒 Unlock...',
    '🔒 Заблокировать': '🔒 Lock',
    'Сменить пароль...': 'Change password...',
    'Снять пароль': 'Remove password',
    'Замок: токен и id руководителя меняются только по паролю.': 'Lock: the token and the owner ID can be changed only with the password.',
    'Замок на токен и id руководителя': 'Lock for the token and owner ID',
    'Введите пароль:': 'Enter the password:',
    'Новый пароль:': 'New password:',
    'Повторите пароль:': 'Repeat the password:',
    'Пароли не совпадают.': 'The passwords do not match.',
    'Неверный пароль.': 'Wrong password.',
    'Пароль установлен. Токен и id руководителя защищены: после закрытия настроек они меняются только по паролю.': 'The password is set. The token and the owner ID are protected: after the settings are closed they can be changed only with the password.',
    'Пароль снят: токен и id руководителя снова можно менять свободно.': 'The password is removed: the token and the owner ID can be changed freely again.',
    'Telegram: гостям нельзя плейлисты, каналы и профили целиком (только отдельные ролики)': 'Telegram: guests may not download whole playlists, channels or profiles (single videos only)',
    'Плейлисты, каналы и профили целиком гостям недоступны: пришлите ссылку на отдельный ролик.': 'Whole playlists, channels and profiles are not available to guests: send a link to a single video.',
    'Пароль слишком короткий: нужно не меньше 4 знаков.': 'The password is too short: at least 4 characters are needed.',
    'Участников пока нет.': 'No members yet.',
    'Укажите id: например /allow 123456789. Id и роли — в /members.': 'Specify an id, for example /allow 123456789. IDs and roles are in /members.',
    'Руководителя изменить нельзя.': 'The owner cannot be changed.',
    'Участники': 'Members',
    'Сделать гостем: /allow ID': 'Make a guest: /allow ID',
    'Сделать администратором: /admin ID': 'Make an administrator: /admin ID',
    'Заблокировать: /block ID': 'Block: /block ID',
    'Удалить: /remove ID': 'Delete: /remove ID',
    '/hide — скрыть кнопки, /menu — показать снова': '/hide — hide the buttons, /menu — show them again',
    '🙈 Скрыть кнопки': '🙈 Hide buttons',
    'Показать кнопки': 'Show buttons',
    'Скрыть кнопки': 'Hide buttons',
    'Кнопки скрыты. Чтобы вернуть их, отправьте /menu.': 'The buttons are hidden. To bring them back, send /menu.',
    'Кнопки показаны.': 'The buttons are shown.',
    '/sites — какие сайты разрешены гостям': '/sites — which sites are allowed for guests',
    '🌐 Сайты': '🌐 Sites',
    '👥 Участники': '👥 Members',
    'Разрешённые сайты': 'Allowed sites',
    'Вам разрешены любые сайты. Гостям: ': 'You may use any site. Guests: ',
    'Только руководитель:\n/members — участники\n/allow ID — сделать гостем, /admin ID — администратором\n/block ID — заблокировать, /remove ID — удалить':
        'Owner only:\n/members — members\n/allow ID — make a guest, /admin ID — make an administrator\n/block ID — block, /remove ID — delete',
    'Пришлите ссылку (или несколько) — программа добавит и скачает.': 'Send a link (or several): the program will add and download them.',
    '/list — список загрузок': '/list — the list of downloads',
    '/pause — пауза всех, /resume — продолжить': '/pause — pause all, /resume — continue',
    '/retry — перезапустить неудачные': '/retry — restart the failed ones',
    '/file 12 — прислать файл загрузки #12 (до 50 МБ)': '/file 12 — send the file of download #12 (up to 50 MB)',
    '/help — эта подсказка': '/help — this hint',
    '[telegram] неверный токен бота': '[telegram] invalid bot token',
    'Не удалось сохранить список и настройки (подробности в журнале)': 'Could not save the list and settings (see the log)',
    'Открыты первые 10 ссылок из %d': 'Opened the first 10 of %d links',
    'Открыть папку': 'Open folder',
    'Открыть папку загрузки': 'Open download folder',
    'Открыть первый файл (Enter)': 'Open first file (Enter)',
    'Открыть ссылку в браузере': 'Open link in browser',
    'Отмена': 'Cancel',
    'Отменено.': 'Canceled.',
    'Отменить\tCtrl+Z': 'Undo\tCtrl+Z',
    'Отметьте хотя бы одну дорожку.': 'Select at least one track.',
    'Очистить список': 'Clear list',
    'Ошибка': 'Error',
    'Ошибка сохранения': 'Save error',
    'Ошибка: ': 'Error: ',
    'Ошибки': 'Errors',
    'Папка': 'Folder',
    'Папка ещё не создана': "The folder hasn't been created yet",
    'Папка загрузки': 'Download folder',
    'Параметры': 'Preferences',
    'Параметры...': 'Preferences...',
    'Пауза': 'Pause',
    'Пауза / продолжить все загрузки (Ctrl+P)': 'Pause / resume all downloads (Ctrl+P)',
    'Пауза всех / продолжить': 'Pause all / resume',
    'Пауза выбранных': 'Pause selected',
    'Перезапустить': 'Restart',
    'Перезапустить все неудачные': 'Restart all failed',
    'Перезапустить выбранные': 'Restart selected',
    'Переименовать группу': 'Rename group',
    'Переименовать группу...': 'Rename group...',
    'Переместить в группу': 'Move to group',
    'Переместить в группу...': 'Move to group...',
    'Плейлисты': 'Playlists',
    'Повторить\tCtrl+Y': 'Redo\tCtrl+Y',
    'Повторов при ошибке': 'Retries on error',
    'Пожалуйста, введите URL-адрес': 'Please enter a URL',
    'Показать / скрыть адресную строку': 'Show / hide address bar',
    'Показать в папке': 'Show in folder',
    'Показать файл в папке': 'Show file in folder',
    'Получаю информацию...': 'Getting information...',
    'Поставить все загрузки на паузу (Ctrl+P)': 'Pause all downloads (Ctrl+P)',
    'Потоков на одну задачу (1–24)': 'Threads per download (1–24)',
    'Примечание': 'Note',
    'Проверить обновление программы по адресу': 'Check for program update at URL',
    'Проверяю обновление...': 'Checking for updates...',
    'Продолжать незавершённые задачи при запуске': 'Resume unfinished downloads on startup',
    'Продолжить': 'Resume',
    'Продолжить все загрузки (Ctrl+P)': 'Resume all downloads (Ctrl+P)',
    'Продолжить выбранные': 'Resume selected',
    'Прокси (например http://127.0.0.1:8080)': 'Proxy (e.g. http://127.0.0.1:8080)',
    'Прочее': 'Other',
    'Пусто   (´･ω･`)': 'Empty   (´･ω･`)',
    'Развернуть / свернуть группы (Space)': 'Expand / collapse groups (Space)',
    'Размер': 'Size',
    'Размер миниатюры (44–140)': 'Thumbnail size (44–140)',
    'Разрешение': 'Resolution',
    'Раскладывать загрузки по папкам сайтов (youtube, vk...)': 'Sort downloads into site folders (youtube, vk...)',
    'Режим': 'Mode',
    'Результат должен быть в формате .mkv или .mp4.': 'The result must be in .mkv or .mp4 format.',
    'Результат нельзя сохранять поверх исходного видео — выберите другое имя.': 'The result cannot be saved over the original video — choose a different name.',
    'Русский': 'Russian',
    'С последнего обновления yt-dlp прошло больше двух недель. Сайты меняются, и старая версия начинает давать ошибки.\n\nОбновить сейчас и перезапустить программу?': 'More than two weeks have passed since the last yt-dlp update. Sites change, and an old version starts to produce errors.\n\nUpdate now and restart the program?',
    'Сайт': 'Site',
    'Сайт не отдал список форматов': 'The site did not provide a format list',
    'Сайт отдал не те данные (проверка или защита); подключите cookies или повторите позже': 'The site returned unexpected data (a check or protection page); connect cookies or try again later',
    'Свой шаблон (впишите ниже)': 'Custom template (enter below)',
    'Серый': 'Gray',
    'Сеть': 'Network',
    'Синий': 'Blue',
    'Скачанные потоки': 'Downloaded streams',
    'Скачать': 'Download',
    'Скачать в качестве': 'Download as',
    'Скачать заново в формате': 'Re-download as',
    'Скачать только звук': 'Download audio only',
    'Скачать только субтитры': 'Download subtitles only',
    'Скачивание ': 'Downloading ',
    'Скачивание возобновлено': 'Downloading resumed',
    'Скачивание завершено': 'Download complete',
    'Скачиваю выбранный формат': 'Downloading the selected format',
    'Сколько раз программа сама перезапустит загрузку после ошибки (0 — не перезапускать)': "How many times to restart a download automatically after an error (0 — never)",
    'Скопированные ссылки сразу добавляются и скачиваются. Нажмите, чтобы выключить.': 'Copied links are added and downloaded right away. Click to turn off.',
    'Скопировать ссылку (Ctrl+C)': 'Copy link (Ctrl+C)',
    'Слишком много запросов; повторите позже': 'Too many requests; try again later',
    'Сначала введите ссылку': 'Enter a link first',
    'Снять все': 'Deselect all',
    'Снять метку (Ctrl+0)': 'Remove tag (Ctrl+0)',
    'Создание ярлыка работает только в Windows.': 'Creating a shortcut works only on Windows.',
    'Создать ярлык на рабочем столе': 'Create desktop shortcut',
    'Состояние': 'Status',
    'Сохранено': 'Saved',
    'Сохранить': 'Save',
    'Сохранить в папку:': 'Save to folder:',
    'Сохранить и выйти': 'Save and exit',
    'Сохранить как': 'Save as',
    'Сохранить как:': 'Save as:',
    'Список пуст': 'The list is empty',
    'Справка': 'Help',
    'Ссылка': 'URL',
    'Ссылки не найдены': 'No links found',
    'Страницы:\n~ 100   — первые 100\n-100 ~   — последние 100\n1, 10 ~ 20, -1   — выборочно': 'Pages:\n~ 100   — first 100\n-100 ~   — last 100\n1, 10 ~ 20, -1   — selected',
    'Субтитры': 'Subtitles',
    'Текстовые файлы (*.txt)': 'Text files (*.txt)',
    'Текстовые файлы (*.txt);;Все файлы (*)': 'Text files (*.txt);;All files (*)',
    'Тип': 'Type',
    'Только звук': 'Audio only',
    'Только субтитры (без видео)': 'Subtitles only (no video)',
    'Требуется вход: обновите файл cookies': 'Sign-in required: update your cookies file',
    'Тёмный режим': 'Dark mode',
    'У видео нет субтитров на выбранных языках (языки задаются в Параметры → Основные)': 'The video has no subtitles in the selected languages (languages are set in Preferences → General)',
    'Убирать завершённые задачи из списка': 'Remove completed downloads from the list',
    'Убрать': 'Remove',
    'Убрать все завершённые': 'Remove all completed',
    'Убрать все задачи из списка?': 'Remove all downloads from the list?',
    'Убрать из группы': 'Remove from group',
    'Убрать из списка (Del)': 'Remove from list (Del)',
    'Уведомлять о завершении каждой загрузки': 'Notify when each download completes',
    'Уведомлять, когда все задачи выполнены': 'Notify when all downloads are complete',
    'Удалить': 'Delete',
    'Удалить файлы': 'Delete files',
    'Удалить файлы (Shift+Del)': 'Delete files (Shift+Del)',
    'Уже скачано раньше': 'Already downloaded',
    'Укажите папку для результата.': 'Specify an output folder.',
    'Укажите, куда сохранить результат.': 'Specify where to save the result.',
    'Українська': 'Ukrainian',
    'Файл': 'File',
    'Файл cookies (Netscape .txt)': 'Cookies file (Netscape .txt)',
    'Файл уже есть': 'File already exists',
    'Файлы не найдены': 'No files found',
    'Файлы субтитров (*.srt *.ass *.ssa *.vtt);;Все файлы (*)': 'Subtitle files (*.srt *.ass *.ssa *.vtt);;All files (*)',
    'Фильтр (Ctrl+F)': 'Filter (Ctrl+F)',
    'Фильтр: слово, type:, tag:, bad:, dup:, rem:': 'Filter: word, type:, tag:, bad:, dup:, rem:',
    'Фиолетовый': 'Purple',
    'Формат видео': 'Video format',
    'Цветная метка': 'Color tag',
    'Читаю дорожки...': 'Reading tracks...',
    'Читаю список форматов...': 'Reading the format list...',
    'Шаблон имени файла': 'Filename template',
    'Экспорт ссылок': 'Export links',
    'Экспорт ссылок...': 'Export links...',
    'Эта ссылка уже в списке. Другой формат: правая кнопка → «Скачать заново в формате»': 'This link is already in the list. For another format: right-click → "Re-download as"',
    'Язык дорожки': 'Track language',
    'Языки дубляжа через запятую, например ru,de (пусто — все)': 'Dub languages, comma-separated, e.g. ru,de (empty — all)',
    'Языки субтитров через запятую (пусто — все)': 'Subtitle languages, comma-separated (empty — all)',
    'Ярлык': 'Shortcut',
    'Ярлык «Great Turtle» создан на рабочем столе.': 'The "Great Turtle" shortcut was created on the desktop.',
    'видео': 'video',
    'видео + звук': 'video + audio',
    'все': 'all',
    'все языки': 'all languages',
    'звук': 'audio',
    'как есть, без перекодирования': 'as is, no re-encoding',
    'моно': 'mono',
    'не указан': 'not set',
    'неизвестная ошибка': 'unknown error',
    'нет субтитров': 'no subtitles',
    'ошибка': 'error',
    'перекодировать в MP3 320': 're-encode to MP3 320',
    'стерео': 'stereo',
    'субтитры: ': 'subtitles: ',
    'только звук': 'audio only',
    'файл не найден или не читается': 'file not found or unreadable',
    'это не формат Netscape (в расширении выберите экспорт «Netscape», а не JSON)': 'this is not Netscape format (in the extension, choose "Netscape" export, not JSON)',
    'язык не указан': 'language not specified',
}

PATTERNS = [
    ('Файл «{0}» в архиве повреждён или защищён паролем ({1})', 'File "{0}" in the archive is damaged or password-protected ({1})'),
    ('Не удалось сохранить настройки: {0}', 'Could not save the settings: {0}'),
    ('[автоповтор] {0}: попытка {1} из {2}', '[auto-retry] {0}: attempt {1} of {2}'),
    ('[формат] {0}: {1}', '[format] {0}: {1}'),
    ('файл cookies пропущен ({0}): {1}', 'cookies file skipped ({0}): {1}'),
    ('Ошибка сохранения: {0}', 'Save error: {0}'),
    ('В архиве нет файлов программы: {0}', 'The archive has no program files: {0}'),
    ('Часть ссылок пропущена: сайт не разрешён гостям ({0}).', 'Some links were skipped: the site is not allowed for guests ({0}).'),
    ('Удалён: {0}', 'Removed: {0}'),
    ('Пропущено плейлистов и каналов: {0}.', 'Playlists and channels skipped: {0}.'),
    ('{0}   —   язык: {1}', '{0}   —   language: {1}'),
    ('Файл уже существует:\n{0}\n\nЗаменить его?', 'The file already exists:\n{0}\n\nReplace it?'),
    ('Готово: {0}', 'Done: {0}'),
    ('Найдено дорожек: {0}', 'Tracks found: {0}'),
    ('Не удалось создать папку: {0}', 'Could not create folder: {0}'),
    ('Готово: {0} файл(ов) в папке {1}', 'Done: {0} file(s) in folder {1}'),
    (' {0} к/с', ' {0} fps'),
    ('{0} кбит/с', '{0} kbps'),
    ('Все ({0})', 'All ({0})'),
    ('Всё из списка «{0}» уже скачано ({1}). Забыть: Инструменты → Забыть архив', 'Everything from playlist "{0}" is already downloaded ({1}). To forget: Tools → Forget download archive'),
    ('Добавлено из плейлиста «{0}»: {1}', 'Added from playlist "{0}": {1}'),
    ('; пропущено уже скачанных: {0}', '; already downloaded, skipped: {0}'),
    ('Добавлено: {0}; уже в списке: {1} (выделено)', 'Added: {0}; already in list: {1} (selected)'),
    ('Уже в списке: {0} (выделены)', 'Already in list: {0} (selected)'),
    ('Добавлено задач: {0}', 'Downloads added: {0}'),
    ('Скопировано ссылок: {0}', 'Links copied: {0}'),
    ('Удалить {0} файл(ов) с диска и убрать задачи ({1}) из списка?', 'Delete {0} file(s) from disk and remove the downloads ({1}) from the list?'),
    ('Файлов на диске нет. Убрать задачи ({0}) из списка?', 'There are no files on disk. Remove the downloads ({0}) from the list?'),
    ('Не удалось удалить {0}: {1}', 'Could not delete {0}: {1}'),
    ('Перезапущено задач: {0}', 'Downloads restarted: {0}'),
    ('Скачиваю заново ({0}): {1}', 'Re-downloading ({0}): {1}'),
    ('На паузе: {0}', 'Paused: {0}'),
    ('Продолжено: {0}', 'Resumed: {0}'),
    ('К оригиналу будет добавляться дубляж: {0} (для звука — отдельными файлами)', 'Dubbing will be added to the original: {0} (as separate files for audio)'),
    ('Автоповтор при ошибке: {0} раз', 'Auto-retry on error: {0} times'),
    ("<b>{0}</b> — программа для скачивания видео, музыки и изображений.<br>Версия программы: <b>{1}</b><br>Скачивание: <a href='https://github.com/yt-dlp/yt-dlp'>yt-dlp</a> и <a href='https://github.com/mikf/gallery-dl'>gallery-dl</a>.<br>Интерфейс: PyQt5.<br><br>Используйте скачанные файлы только для личного пользования.", "<b>{0}</b> is a program for downloading videos, music and images.<br>Program version: <b>{1}</b><br>Downloading: <a href='https://github.com/yt-dlp/yt-dlp'>yt-dlp</a> and <a href='https://github.com/mikf/gallery-dl'>gallery-dl</a>.<br>Interface: PyQt5.<br><br>Use downloaded files for personal use only."),
    ('В архиве версия {0}, у вас {1}: новее она не является.\nВсё равно установить?', 'The archive contains version {0}, you have {1}: it is not newer.\nInstall anyway?'),
    ('Установить версию {0} (сейчас {1})?\nСтарые файлы сохранятся в папке _backup_….', 'Install version {0} (currently {1})?\nOld files will be saved in the _backup_... folder.'),
    ('[обновление] установлена версия {0}, копия: {1}', '[update] version {0} installed, backup: {1}'),
    ('Версия {0} установлена. Перезапустить программу сейчас?', 'Version {0} installed. Restart the program now?'),
    ('В архиве записей: {0}. Забыть их все? Файлы останутся на диске, но программа снова будет качать эти ролики.', 'The archive has {0} entries. Forget them all? The files will stay on disk, but the program will download these videos again.'),
    ('Видео — {0}, MP4', 'Video — {0}, MP4'),
    ('{0} ГБ', '{0} GB'),
    ('{0} МБ', '{0} MB'),
    ('{0} КБ', '{0} KB'),
    ('{0} МБ/сек.', '{0} MB/s'),
    ('{0} КБ/сек.', '{0} KB/s'),
    ('[повтор] {0}: сайт ответил отказом, жду {1} с и пробую снова', '[retry] {0}: the site refused, waiting {1} s and trying again'),
    ('файл cookies пропущен ({0}): {1}', 'cookies file skipped ({0}): {1}'),
    ('[дорожки] {0}: дубляжа на нужных языках ({1}) не найдено', '[tracks] {0}: no dubbing found in the required languages ({1})'),
    ('[дорожки] не удалось получить список дорожек, качаю обычным способом: {0}', '[tracks] failed to get the track list, downloading the usual way: {0}'),
    ('[дорожки] {0}: ', '[tracks] {0}: '),
    ('[дорожки] {0}: {1}', '[tracks] {0}: {1}'),
    ('[формат] {0}: {1}', '[format] {0}: {1}'),
    ('Файлов: {0}', 'Files: {0}'),
    ('gallery-dl завершился с кодом {0}', 'gallery-dl exited with code {0}'),
    ('Файл не найден: {0}', 'File not found: {0}'),
    ('код {0}', 'code {0}'),
    ('{0} кан.', '{0} ch.'),
    ('Звук {0}', 'Audio {0}'),
    ('Субтитры {0}', 'Subtitles {0}'),
    ('[автоповтор] {0}: попытка {1} из {2}', '[auto-retry] {0}: attempt {1} of {2}'),
    (' · повтор {0} из {1} через {2} с', ' · retry {0} of {1} in {2} s'),
    ('[архив] {0}: уже скачано, пропускаю', '[archive] {0}: already downloaded, skipping'),
    ('[обложка] не удалось вшить ({0}); качаю без обложки', '[cover] failed to embed ({0}); downloading without cover'),
    ('Это не zip-архив или он повреждён ({0})', 'This is not a zip archive or it is corrupted ({0})'),
    ('В архиве несколько файлов «{0}»', 'The archive contains several "{0}" files'),
    ('Файл {0} повреждён или не является кодом программы ({1})', 'File {0} is corrupted or is not program code ({1})'),
    ('Не удалось записать файлы ({0}). Проверьте, что у папки программы есть права записи.', 'Failed to write files ({0}). Check that the program folder is writable.'),
    ('Не удалось скачать: {0}', 'Download failed: {0}'),
    ('Доступ закрыт. Ваш chat id: {0}. Чтобы разрешить, впишите его в Параметры → Telegram → «Telegram: id администратора (полный доступ и уведомления)».', 'Access denied. Your chat id: {0}. To allow it, enter it in Preferences → Telegram → "Telegram: administrator ID (full access and notifications)".'),
    ('Не удалось отправить файл: {0}', 'Could not send the file: {0}'),
    ('Ошибка: {0}', 'Error: {0}'),
    ('❌ Ошибка: {0}', '❌ Error: {0}'),
    ('✅ Готово: {0} ({1})', '✅ Done: {0} ({1})'),
    ('✅ Готово: {0}', '✅ Done: {0}'),
    ('Все задачи выполнены: {0} / {1}', 'All tasks are done: {0} / {1}'),
    ('Принято ссылок: {0}', 'Links accepted: {0}'),
    ('Загрузок: {0}, готово: {1}', 'Downloads: {0}, done: {1}'),
    ('(показаны последние {0})', '(showing the last {0})'),
    ('[telegram] бот запущен: @{0}', '[telegram] bot started: @{0}'),
    ('[telegram] не удалось подключиться: {0}', '[telegram] could not connect: {0}'),
    ('[telegram] нет связи: {0}', '[telegram] no connection: {0}'),
    ('[telegram] ошибка обработки сообщения: {0}', '[telegram] message handling error: {0}'),
    ('[telegram] не удалось отправить: {0}', '[telegram] could not send: {0}'),
    ('[telegram] отказано в доступе: chat id {0}', '[telegram] access denied: chat id {0}'),
    ('Заявка отправлена владельцу бота. Ваш Telegram ID: {0}. Когда вас одобрят, пришлите ссылку.', 'Your request was sent to the bot owner. Your Telegram ID: {0}. When you are approved, send a link.'),
    ('Не удалось отправить файл {0}: {1}', 'Could not send the file {0}: {1}'),
    ('[telegram] не удалось войти ({0}). Проверьте токен и интернет/прокси.', '[telegram] could not sign in ({0}). Check the token and the internet/proxy.'),
    ('[telegram] нет связи с Telegram: {0}', '[telegram] no connection to Telegram: {0}'),
    ('[telegram] ошибка обработки: {0}', '[telegram] processing error: {0}'),
    ('Файл «{0}» в архиве слишком большой', 'The file "{0}" in the archive is too large'),
    ('Сайт перенаправил запрос на незащищённый адрес — обновление отменено', 'The site redirected the request to an insecure address: update cancelled'),
    ('[архив] не удалось записать: {0}', '[archive] could not save: {0}'),
    ('[папки] {0}', '[folders] {0}'),
    ('[файлы] {0} -> {1}', '[files] {0} -> {1}'),
    ('Не удалось удалить файлов: {0}. Возможно, они открыты в другой программе. Подробности в журнале.', 'Could not delete {0} file(s). They may be open in another program. See the log for details.'),
    ('Не удалось открыть папку загрузки: {0}', 'Could not open the download folder: {0}'),
    ('Подробности записаны в {0}', 'Details were written to {0}'),
]


PATTERNS.extend([
    ("Папка «{0}» переименована в «{1}»: группа и пути к файлам обновлены",
     "Folder \"{0}\" was renamed to \"{1}\": the group and file paths are updated"),
    ("Папка переименована: пути к файлам обновлены ({0})", "Folder renamed: file paths updated ({0})"),
    ("Выбрано: {0} из {1}", "Selected: {0} of {1}"),
    ("Общий вес: {0}", "Total size: {0}"),
    ("Узнаю размеры: {0} из {1}", "Getting sizes: {0} of {1}"),
    ("Общий вес: не меньше {0}", "Total size: at least {0}"),
    ("; уже скачано раньше, подключено без повторной загрузки: {0}", "; already downloaded earlier, linked without re-downloading: {0}"),
    ("Файл переименован или перенесён: путь обновлён ({0})", "File renamed or moved: path updated ({0})"),
])
EN.update({
    "Выбор глав": "Select chapters",
    "Узнать размеры": "Get sizes",
    "Размер известен не у всех роликов: нажмите «Узнать размеры»": "The size is not known for every video: click \"Get sizes\"",
    "Выбор роликов": "Select videos",
    "Уже скачано раньше: файл не будет скачан заново, а подключится к этому списку": "Already downloaded before: the file will not be downloaded again, it will be linked to this list",
    "✓ — уже скачано раньше (повторно не скачивается)": "✓ — already downloaded before (not downloaded again)",
    "Спрашивать, что скачать, при загрузке плейлиста": "Ask what to download when adding a playlist",
    "Спрашивать, что скачать, при загрузке плейлиста (окно выбора роликов)": "Ask what to download when adding a playlist (video selection window)",
    "При загрузке плейлиста откроется окно выбора": "A selection window will open when you add a playlist",
    "Плейлисты будут скачиваться целиком": "Playlists will be downloaded in full",
    "Сайт отклонил запрос (412, защита от ботов); включите «Имитировать браузер» и подключите cookies":
        "The site rejected the request (412, bot protection); turn on \"Impersonate browser\" and connect cookies",
    "ГБ": "GB", "МБ": "MB", "КБ": "KB", "МБ/сек.": "MB/s", "КБ/сек.": "KB/s",
    "Скачивание": "Downloading", "язык не указан": "language not set", "моно": "mono", "стерео": "stereo",
    "кан.": "ch.", "Звук": "Audio", "графические, не поддерживаются": "image-based, not supported",
})


HELP_EN = """
<h2>How to use</h2>

<h3>The quick version</h3>
<ol>
<li>Copy the link to a video, song or picture.</li>
<li>Paste it into the box at the top and click the blue button with the down arrow.</li>
<li>Your files are in the download folder. To open it: "Tools → Open download folder".</li>
</ol>

<h3>How to add a link</h3>
<ul>
<li><b>The box at the top + the blue button.</b> You can paste several links at once, separated by spaces or on new lines.</li>
<li><b>The icon to the left of the box.</b> Takes the link from the clipboard (whatever you just copied) and starts downloading right away.</li>
<li><b>Ctrl+V</b> in the program window does the same thing: it adds the links you copied.</li>
<li><b>Drag and drop.</b> You can grab a link or a file with links in it and drop it into the window.</li>
<li><b>The "Auto" button at the bottom.</b> When it says "Auto: on", any link you copy is added and downloaded right away. Click it again to turn it off. When it is off, the program does not pick up anything by itself. The same switch is in "Tools → Auto-download copied links" and in Preferences → Other.</li>
</ul>
<p>If you paste a link that is already in the list, the program does not download it a second time, but shows you the existing download instead.</p>

<h3>What the arrow next to the blue button does</h3>
<ul>
<li><b>Select pages and download</b> — for when you want only part of a collection, not all of it (see below).</li>
<li><b>Select chapters and download</b> — for sites where everything is split into chapters.</li>
<li><b>Download as</b> — pick the quality just once (from 144p to 4K) without changing your settings.</li>
<li><b>Download audio only</b> — just the music or voice, without the picture.</li>
<li><b>Download subtitles only</b> — .srt files without the video (languages are set in Preferences → General → "Subtitle languages"; leave it empty for all). The files get the same name as the video if the filename template is the same, so your player will pick them up on its own.</li>
</ul>

<h3>Playlists and collections</h3>
<p>If a link has many videos (a playlist, a channel, an album), the program creates a folder named after the playlist and puts everything in it. In the list, such downloads are gathered into one group, which you can collapse and expand (Space).</p>
<p><b>A link from a YouTube "Mix"</b> (the address contains <code>list=RD…</code> or <code>start_radio=1</code>) downloads only that one video: a mix is endless, so there is no point in taking all of it. Open the playlist you actually want with a link like <code>youtube.com/playlist?list=…</code>.</p>
<p>If a link points to a video inside a playlist (the address has both <code>v=…</code> and <code>list=…</code>), only that video is downloaded by default. To download the whole playlist, turn on Preferences → General → "For "video from playlist" links, download the whole playlist".</p>
<p>Each video row shows its size, and the group header shows the total size of the playlist on the right. While the size of some videos is still unknown (they have not started downloading), the total is preceded by "≥".</p>
<p>To have the program ask what to download for every playlist, turn on "Settings → Ask what to download when adding a playlist". A window with the list of videos opens: tick the ones you want. Those already downloaded earlier are marked "✓". The window is not shown for a playlist with a single video. The window shows each video's duration and size (if the site reports them) and the total size of the selection. If the list has no sizes, the program asks the site about each video by itself, and the sizes appear in the rows as they arrive. In a big playlist (more than 60 videos with an unknown size) this does not start by itself, so that the site does not block frequent requests: click "Get sizes". The button also starts the check again.</p>
<p>To take only part of a collection, choose "Select pages and download" and type the numbers:</p>
<pre>~ 100            first 100
-100 ~           last 100
1, 10 ~ 20, -1   the first, 10th to 20th, and the last</pre>

<h3>Video quality</h3>
<p>Set it in "Settings → Preferences → General" (the "Video format" field).</p>
<ul>
<li><b>Best quality</b> — the highest quality the site has.</li>
<li><b>MP4 (no AV1)</b> — a format everyone understands, which opens almost anywhere. A good choice if you are not sure what to pick.</li>
<li><b>H.264 + AAC</b> — for TVs, phones and old players that cannot handle newer formats. On YouTube this is available up to 1080p.</li>
<li><b>144p … 4K</b> — set a maximum. If the video does not have that size, the closest smaller one is used.</li>
<li><b>Audio only</b> and <b>Subtitles only</b> — no video (see below).</li>
</ul>
<p>The higher the quality, the bigger the file. A 4K video can weigh tens of gigabytes. For videos with an age restriction the available quality can be lower: they usually need you to be signed in (cookies, see below).</p>

<h3>Music</h3>
<p>Use the "Download audio only" button (in the drop-down menu) or the "Audio only" mode. Audio quality is chosen in Preferences → General → "Audio quality":</p>
<ul>
<li><b>MP3 320</b> — the best mp3, plays on any player and phone.</li>
<li><b>MP3 256 / 192</b> — smaller files.</li>
<li><b>Original audio</b> — sound without re-compression, the most faithful quality. The file will be in m4a or opus format.</li>
</ul>
<p>The title and artist are saved into the file, as well as the cover art (if the site provides it). If you do not want the cover art, or it causes errors, clear the checkbox in Preferences → General. If embedding it fails, the program downloads the track without the cover art on its own.</p>

<h3>Dubbed audio: several languages in one video</h3>
<p>Some videos have several audio tracks (for example, English and Russian). To download the original and the dubbed audio together:</p>
<ol>
<li>Turn on "Settings → Add dubbed audio to the original".</li>
<li>In Preferences → General, in the "Dub languages" field, type the ones you need: <code>ru</code> or <code>ru,de</code>. If you leave it empty, all languages are taken (the file gets bigger).</li>
<li>Download the video. The original will be the first track and the dubbed audio the second.</li>
</ol>
<p>In a player, audio tracks are switched in its menu (for example, in VLC: "Audio → Audio Track"). If there is no dubbed audio in the language you want, the video is downloaded the usual way. The dubbed-audio check is done only on YouTube, VK, Rutube and Dzen: on other sites it is usually not needed, and extra requests may get you blocked. If another site has dubbed audio, add it in Preferences → General → "Additional sites to check for dubbed audio" (a short name, like the site's folder name, or a domain: <code>vimeo, example.com</code>).</p>
<p><b>In "Audio only" mode</b> the same setting works differently: each track is saved as a separate file, for example <code>Title.en.mp3</code> and <code>Title.ru.mp3</code>. If the video has only one track, there will be just one file.</p>

<h3>Merge video with subtitles and audio</h3>
<p>If you already have a video and separate files, for example a 4K video from a site and subtitles (.srt), or a Russian audio track from another source: "Tools → Merge video with subtitles and audio...". You can also right-click a downloaded video → "Add subtitles / audio to file".</p>
<ol>
<li>Choose the video and add subtitle and audio files with the "Add..." buttons. The language is detected from the file name (<code>video.ru.srt</code>), and you can fix it with the "Change language" button.</li>
<li>Choose where to save the result (.mkv or .mp4) and click "Merge".</li>
</ol>
<p>The video and audio are not re-encoded, so it is fast and there is no loss of quality. The original files are not changed; a new one appears next to them. If you need subtitles in .mp4, they are saved as text tracks (mov_text); in .mkv they are kept as they are. Requires ffmpeg.</p>

<h3>Extract audio and subtitles from a video</h3>
<p>"Tools → Extract audio and subtitles from video..." (or right-click a downloaded video). The program shows all the audio tracks and subtitles inside the file: tick the ones you want, and each one is saved as a separate file, for example <code>video.ru.srt</code>, <code>video.en.m4a</code>. By default the audio is copied without re-encoding, or you can choose MP3 320. Picture-based subtitles (like on a DVD) are not supported, but PGS ones are saved as .sup.</p>

<h3>Subtitles</h3>
<p>In Preferences → General → "Subtitles" choose:</p>
<ul>
<li><b>None</b> (the default).</li>
<li><b>As separate files (.srt)</b> — text files appear next to the video. Your player will pick them up on its own.</li>
<li><b>Embed in video</b> — the subtitles are inside the video file, and you turn them on in the player.</li>
</ul>
<p>Languages are separated by commas, for example <code>ru,en</code>. The "Include automatic subtitles" checkbox is for videos that have no real subtitles, only ones generated by YouTube's speech recognition.</p>

<h3>Updating the program</h3>
<p><b>A video will not download, the site has changed:</b> "Tools → Update yt-dlp and restart the program immediately" — one button instead of two steps. The "Update yt-dlp and gallery-dl" item only updates; you have to restart yourself. If the error is still there after updating, you can turn on the nightly builds of yt-dlp in Preferences → General ("Update yt-dlp to nightly builds"): fixes appear in them sooner, but they are less stable. Every two weeks the program will remind you about updating.</p>
<p><b>A new version of the program itself:</b> "Tools → Update program from archive..." and choose the zip file you received. The program checks the files, saves the old ones into a <code>_backup_…</code> folder, installs the new ones and offers to restart. Your settings and download list are not lost. If you were given a permanent link to the archive, enter it in Preferences → Other → "Program update URL" and use the "Check for program update at URL" item. Only use a link from a trusted source: the update runs code from the archive.</p>
<p>The installed version number is shown in "Help → About". The turtle shortcut on the desktop is created by the installer, and if needed you can make it again: "Tools → Create desktop shortcut".</p>

<h3>The download archive</h3>
<p>The program remembers what it has already downloaded (the video and its kind: video or audio). If the link comes up again, even after being removed from the list, it will not be downloaded a second time: the task immediately becomes "Already downloaded". If such a video turns up in a playlist, it is not downloaded again: the playlist gets its own item with the status "Already downloaded", linked to the same file. The file is not copied; it stays where it was and opens from both items, and the line at the bottom tells you so. A record counts only while the file is on the disk: if you delete the file, the video can be downloaded again. The "Restart" and "Re-download as" items of the right-click menu always download again. You can turn the archive off in Preferences → General; to erase all of its memory, use "Tools → Forget download archive".</p>

<h3>Telegram bot</h3>
<p>You can control the program from Telegram: send links from your phone and receive notifications. The program must be running on the computer, and the computer must be on.</p>
<ol>
<li>In Telegram, open <b>@BotFather</b>, send <code>/newbot</code> and get a <b>token</b> (a long string with a colon).</li>
<li>In the program: Preferences → Telegram tab → turn on "Telegram bot: accept links and commands" and paste the token. Click OK.</li>
<li>Write anything to your bot. It replies that your request was sent and tells you your <b>Telegram ID</b> (a number).</li>
<li>Enter that number in "Telegram: owner ID" (the owner is the bot's owner) and click OK again. Now you have full access. You can list other administrators, separated by commas, in "Telegram: administrator IDs".</li>
</ol>
<p><b>Members.</b> If someone else writes to the bot, they get the reply "Your request was sent to the bot owner", and the administrators receive a message about the new request. The decision is made in "Settings → Telegram members…": <b>Allow (guest)</b>, <b>Make administrator</b>, <b>Block</b> (the bot stops answering that person) or <b>Delete</b>. The bot tells the approved person by itself. If you turn on "Telegram: new members become guests right away", requests are not needed. The member list is kept in the <code>tg_members.json</code> file in the data folder. The bot answers only in private chats: it ignores messages in groups and channels. At most 50 requests from strangers are kept at once; after that the bot does not answer them until you clear the list.</p>
<p><b>Owner and guest sites.</b> Enter your id in the "Telegram: owner ID" field: the owner's power is absolute. The owner is always an administrator and cannot be demoted, blocked or deleted (neither in the members window nor by commands), the owner is not shown in the members list, and only the owner manages people right from the bot: <code>/members</code>, <code>/allow ID</code>, <code>/admin ID</code>, <code>/block ID</code>, <code>/remove ID</code>. Guests can download only from the sites ticked on the Telegram tab (YouTube, Instagram and TikTok by default; the list also has VK, X, Facebook, Reddit, Twitch and more); links to other sites are rejected by the bot. Guests also cannot download whole playlists, channels or profiles, only single videos (a video link with &list=… is trimmed to that one video); this can be turned off with the tick on the same tab. Administrators and the owner may use any site. You can quickly turn the bot on and off in the menu "Settings → Telegram bot enabled".</p>
<p><b>What is allowed.</b> A guest can only send <b>links</b> (one or several): the program adds and downloads them. All commands are for the administrator only: <code>/list</code> — the list of downloads with numbers (#12); <code>/pause</code> and <code>/resume</code> — pause everything and continue; <code>/retry</code> — restart the failed ones; <code>/file 12</code> — send the file of download #12; <code>/help</code> — a hint. <code>/sites</code> — which sites guests may use (available to everyone). The same actions are available as buttons at the bottom of the chat, and they depend on the rights: a guest sees only "Sites" and "Help"; an administrator also sees "List", "Restart failed", "Pause" and "Resume"; the owner additionally sees "Members". There is also a command menu next to the input field (again by rights), and under the reply to /list there are "📎" buttons that send the files of the latest completed downloads. You can hide the buttons with <code>/hide</code> (or the "Hide buttons" button) and bring them back with <code>/menu</code>; the choice is remembered for each member. If "Telegram: send administrators notifications…" is on, the bot writes to all administrators when a download is completed or ended with an error and when all tasks are done.</p>
<p><b>Lock.</b> The "Set password…" button on the Telegram tab protects the bot token and the owner ID: they become read-only, and the "Unlock…" button asks for the password. The password itself is not stored, only its salted hash. If you forget it, the lock can be removed only by deleting the <code>tg_lock_hash</code> line in the settings file. Important: the token is like the bot's password; it is stored in the settings file unencrypted, so do not show it to anyone. You can receive files up to 50 MB (Telegram's limit for bots). If "Ask what to download when adding a playlist" is on, the selection window opens on the computer, not in Telegram. If Telegram does not open for you without a proxy, set one in Preferences → Network. The bot's activity is visible in "Tools → Log…" (the [telegram] lines).</p>

<h3>Menus and settings</h3>
<p><b>File:</b> "Import links from file…" adds links from a .txt file (you can also drag such a file into the window); "Export links…" saves the links of all downloads to a file; "Exit without saving" closes the program without writing changes to the list and settings.</p>
<p><b>Downloads:</b> the same actions as in the download's right-click menu, plus "Restart all failed", "Pause selected" / "Resume selected", "Remove all completed" and "Clear list".</p>
<p><b>Preferences → General:</b> the download folder, the default video format, the filename template, the number of simultaneous downloads, the speed limit, audio quality, subtitles, dubbed audio, the archive, cover art, using gallery-dl for picture sites, and sorting into site folders.</p>
<p><b>Preferences → Network:</b> the number of threads per download; "Retries on error" (retries inside the download of a single file) and the number of automatic restarts of the whole download; the <b>proxy</b> (for example <code>http://127.0.0.1:8080</code>), needed if a site opens only through a proxy; the cookies file, cookies from the browser and "Impersonate browser".</p>
<p><b>Preferences → Appearance:</b> dark mode and the thumbnail size. <b>Preferences → Other:</b> autosave, the program update URL, completion notifications, the action after all downloads (nothing or exit the program), preventing sleep (Windows), auto-download of copied links, resuming unfinished downloads at startup, and automatically removing completed downloads. <b>Preferences → Telegram:</b> the bot (see above). <b>The "Settings" menu</b> has quick switches: dark mode, automatic retry, new downloads on top, dubbed audio, the selection window for playlists, the Telegram bot; it also has "Telegram members…" and the language choice.</p>
<p>The list and settings are kept in the program's data folder. Before each write, the previous version is saved next to it with the <code>.bak</code> extension. If a file turns out to be damaged (for example, after a power failure), it is renamed to <code>.corrupt</code> and the data is restored from the <code>.bak</code> copy.</p>

<h3>Interface language</h3>
<p>The main language of the program is Russian, with English as an additional one. Switch it in "Settings → Язык / Language": the program saves your choice and offers to restart, after which the whole interface and this Help appear in the chosen language. The "Язык / Language" menu item is always shown in both languages. The error texts that come from the websites themselves stay as they are.</p>

<h3>Where everything is downloaded</h3>
<p><b>How to name files.</b> Preferences → General → "Filename presets": pick an option from the list and the template is filled into the line below it. You can edit that line by hand: the words <code>artist</code>, <code>title</code>, <code>id</code> are replaced with the author, the title and the video's code, and <code>date:%Y-%m-%d</code> with the date. An ordinary yt-dlp template works too, for example <code>%(title)s [%(id)s].%(ext)s</code>. A template without id: if two videos have the same title, the second file will overwrite the first.</p>
<p>If you rename a playlist folder in File Explorer, the program notices within a few seconds: the group in the list gets the new name and the paths to the files are updated, so they open as before. The same goes for a single file that you rename or move: the program finds it again by its size and modification time and fixes the path. This works while nothing is being downloaded into that folder; a folder has to stay in the same place (only renamed), and a moved file is searched for in the download folder and next to its old location. The download folder is set in Preferences. Inside it, each site gets its own folder (youtube, vkvideo and so on), and playlists go into one more folder inside that. If you do not like this, clear the "Sort downloads into site folders" checkbox.</p>
<p>The order of the list is switched in "Settings → New downloads on top": when the checkbox is ticked, fresh downloads appear at the top of the list, and when it is cleared (the default) they appear at the bottom. A playlist keeps its own order inside its group. The download queue does not change because of this.</p>
<p>Under the link box there are <b>tabs</b>: "All", "Errors" (everything that failed or only partly downloaded; a task leaves the tab once a retry succeeds), "Music" (downloads in "Audio only" mode), "Playlists" (downloads from playlists) and one for each site you have downloaded from. Tabs appear when there is something to show in them. Click a tab to see only those downloads. You can drag tabs with the mouse to arrange them as you like. Right-click a tab → "Pin tab": pinned tabs (with the 📌 icon) stay to the left of the others and do not move away. The order and pinning are remembered.</p>

<h3>What you can do with a download</h3>
<p>Right-click a download:</p>
<ul>
<li><b>Pause / Resume</b> — stop and then continue from the same place. The button with two bars at the bottom pauses all downloads at once; when everything is paused, it turns into a play button. If you pause everything and then resume, only the downloads that this button stopped continue; the ones you paused by hand stay paused.</li>
<li><b>Show in folder</b> — opens File Explorer and highlights the downloaded file. A double click opens the file itself in your player.</li>
<li><b>Add subtitles / audio to file</b> and <b>Extract audio / subtitles from file</b> — work with the tracks of a finished video (see above).</li>
<li><b>Open link in browser</b> — the video or song page opens in your browser (you can select several at once, up to 10).</li>
<li><b>Restart</b> — start over if there was an error. The program also restarts failed downloads by itself: "Settings → Auto-retry on error" (3 times by default, with pauses of 10, 30 and 90 seconds; the number of attempts is set in Preferences → Network → "How many times to restart a download automatically after an error"; the "Retries on error" field is different: retries inside the download of a single file). Errors that will not go away on their own are not retried: an unknown site, a deleted or private video, no ffmpeg. A request to sign in or confirm your age is retried: on YouTube it sometimes goes away by itself.</li>
<li><b>Select format...</b> — the program reads all the qualities available for the video (resolution, codec, size) and downloads exactly the line you choose. If the line has no sound, the best audio is added automatically. "As in settings" resets your choice. Dubbed audio is not added to such a download, and it is not recorded in the download archive.</li>
<li><b>Re-download as</b> — for example, it was mp4 and now you need audio only.</li>
<li><b>Color tag and Comment...</b> — so you do not get lost in a big list.</li>
<li><b>Info</b> — details: which streams were downloaded, the size, the folder. "Info", "Select format", "Add subtitles / audio" and "Extract…" are available when a single download is selected.</li>
<li><b>Move to group…</b> / <b>Remove from group</b> — gather any downloads into your own group (the folder is not changed). Right-click a group header: <b>Rename group…</b> (changes the group's name, not the folder on the disk) and <b>Copy group names</b>.</li>
<li><b>Open first file</b> (Enter) and <b>Copy link</b> (Ctrl+C).</li>
<li><b>Remove from list</b> — the file stays on the disk. <b>Delete files</b> — also deletes the file (a running download is stopped first; if the file is in use by another program, you get a message). <b>Remove all completed</b> — in one action.</li>
</ul>
<p>If you hover the mouse over the small picture, it gets bigger. Clicking it opens the file.</p>

<h3>How to read the colors in the list</h3>
<p>Light gray — waiting or reading information · dark gray — downloading · green — done ·
orange — not fully downloaded · red — error (the error text is written in the line).</p>

<h3>If you need to sign in to a site (cookies)</h3>
<p>Some sites show videos only to people who are signed in. YouTube sometimes says "Sign in to confirm you're not a bot". The program needs to "show" that you are signed in. A cookies.txt file does this. How to get it:</p>
<ol>
<li>Open a <b>private window</b> in your browser and sign in to the site.</li>
<li>In that same tab, open <code>youtube.com/robots.txt</code> (for YouTube).</li>
<li>Install the "Get cookies.txt LOCALLY" extension in your browser and click Export.</li>
<li>Close the private window.</li>
<li>In the program: Preferences → Network → "Cookies file" — choose the file you saved.</li>
</ol>
<p>It is better to sign in with a spare account. There is also a simpler way: Preferences → Network → "Cookies from browser" (the program takes them from Chrome, Firefox and others by itself). It does not work for everyone: if it fails, use the file. The extension must save the file in <b>Netscape</b> format (not JSON): the first line of the file is <code># Netscape HTTP Cookie File</code>. If the format is different, the program skips the file and says so in the log. A cookies.txt file is like a key to your account, so do not show it to anyone. If it asks you to sign in again, the file is out of date: make a new one.</p>

<h3>If something does not work</h3>
<ol>
<li>Click "Tools → Update yt-dlp and restart the program immediately". Sites change often, and this fixes most errors.</li>
<li>Open "Tools → Log...": it says exactly what went wrong. You can copy those lines and show them to whoever is helping you.</li>
<li>If it says the video is available only to subscribers or by age, you need to sign in (cookies) and have a subscription.</li>
<li>An error <b>403</b>, <b>412</b> or "too many requests" means the site is temporarily not letting you in (412 is typical of Bilibili's bot protection). The program will try again by itself in a few seconds, turning on browser impersonation for that download (if curl-cffi is installed; it comes with yt-dlp when you update). If that does not help: turn on "Impersonate browser" and connect cookies (Preferences → Network), reduce the number of simultaneous downloads, or try later.</li>
<li>Joining video and audio, mp3, subtitles, merging and extracting tracks work only if ffmpeg is installed (ffprobe is installed with it).</li>
</ol>

<h3>List filter (bottom left)</h3>
<pre>word             by title, artist, link, group, comment, site
type:youtube     by site
tag:red          by color tag (red, orange, yellow, green, blue, purple, gray or 1–7)
comment:word     by comment
bad:             everything not downloaded yet (queued, downloading, error, incomplete)
dup:             only duplicates
rem:             files that are no longer on the disk</pre>
<p>You can combine conditions with a space: <code>youtube bad:</code></p>

<h3>Keyboard shortcuts</h3>
<p>The keys Enter, Del, Shift+Del, Ctrl+↑ ↓ Home End, Ctrl+0…7, Ctrl+C, Ctrl+V, Ctrl+A, Ctrl+ +/− and Space work when the download list is focused (in the link box, Ctrl+V just pastes text). The other keys work anywhere in the window.</p>
<table cellpadding="3">
<tr><td><b>Ctrl+S</b></td><td>save the list and settings</td></tr>
<tr><td><b>Ctrl+,</b></td><td>preferences</td></tr>
<tr><td><b>Enter</b>, double click, click on the picture</td><td>open the first file</td></tr>
<tr><td><b>Del</b> / <b>Shift+Del</b></td><td>remove from list / also delete the files</td></tr>
<tr><td><b>Ctrl+↑ ↓ Home End</b></td><td>change the download order</td></tr>
<tr><td><b>Ctrl+1…7</b>, <b>Ctrl+0</b></td><td>color tags / clear the tag</td></tr>
<tr><td><b>Alt+D</b></td><td>address bar (link box) ↔ download list</td></tr>
<tr><td><b>Ctrl+F</b></td><td>go to the filter</td></tr>
<tr><td><b>Ctrl+Tab</b></td><td>show/hide the address bar (the link box)</td></tr>
<tr><td><b>Ctrl+wheel</b>, <b>Ctrl+ +/−</b></td><td>picture size</td></tr>
<tr><td><b>Space</b></td><td>expand/collapse groups</td></tr>
<tr><td><b>Ctrl+C / Ctrl+V / Ctrl+A</b></td><td>copy links / paste links / select all</td></tr>
<tr><td><b>Ctrl+P</b></td><td>pause / resume all</td></tr>
<tr><td><b>F1</b></td><td>this help</td></tr>
</table>

<p><i>Please use downloaded files for personal use only.</i></p>
"""
