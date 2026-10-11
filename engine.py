# -*- coding: utf-8 -*-
"""Движок загрузок: планировщик + бэкенды yt-dlp (видео/аудио/плейлисты) и gallery-dl (галереи).

Qt здесь не используется: GUI передаёт обычные функции-обратные вызовы (notify, on_playlist, ...),
которые вызываются из рабочих потоков.
"""
import dataclasses
import hashlib
import importlib.util
import json
import mimetypes
import os
import queue
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from core import (Members, TG_BUTTONS_ADMIN, TG_BUTTONS_GUEST, TG_BUTTONS_OWNER, tr, files_sigs, ARCHIVE_FILE, Archive, archive_key, ACTIVE_STATES, norm_mode, DONE, DOWNLOADING, INCOMPLETE, INVALID, QUEUED, READING, THUMB_DIR,
                  WAITING, friendly_error, is_permanent_error, retry_delay, safe_name, site_of)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
IS_WIN = sys.platform.startswith("win")
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp")

FORMATS = {
    "best": "bv*+ba/b",
    # без AV1, звук AAC (m4a): файл открывается почти везде; потоки не перекодируются
    "mp4": ("bv*[vcodec!*=av01][protocol!*=m3u8]+ba[ext=m4a]"
            "/bv*[vcodec!*=av01][protocol!*=m3u8]+ba"
            "/bv*[vcodec!*=av01]+ba[ext=m4a]/bv*+ba/b"),
    # совместимость: H.264 (avc1) + AAC в MP4 — открывается на любом телевизоре, телефоне и старом плеере.
    # Если у ролика нет H.264 (например, 4K на YouTube бывает только в VP9/AV1), берётся лучший доступный поток.
    "h264": ("bv*[vcodec^=avc1][protocol!*=m3u8]+ba[ext=m4a]"
             "/bv*[vcodec^=avc][protocol!*=m3u8]+ba[ext=m4a]"
             "/b[vcodec^=avc1][ext=mp4]"
             "/bv*[vcodec!*=av01]+ba[ext=m4a]/bv*+ba/b"),
    "audio": "ba/b",
}


try:  # наследуемся от исключения yt-dlp, чтобы он не глотал отмену
    from yt_dlp.utils import DownloadCancelled as _CancelBase
except Exception:
    _CancelBase = Exception


class Cancelled(_CancelBase):
    pass


def ytdlp_available():
    return importlib.util.find_spec("yt_dlp") is not None


def gallerydl_available():
    return importlib.util.find_spec("gallery_dl") is not None


_HIT = re.compile(r"\bdate:((?:%[A-Za-z](?:[-:./ _](?=%))?)+)|\b(artist|title|id|date)\b")
_HIT_TOK = {"artist": "%(uploader,channel)s", "title": "%(title)s", "id": "%(id)s",
            "date": "%(timestamp>%Y-%m-%d,upload_date>%Y-%m-%d)s"}


def hitomi_template(fmt):
    """Шаблон имени в стиле Hitomi -> шаблон yt-dlp.

    '[artist] date:%Y-%m-%d %H:%M; title'  ->  '[<автор>] 2024-05-01 12-30; <название>.<расширение>'
    Слова artist, title, id, date заменяются; date:<формат> — дата по strftime (время по UTC,
    «:» меняется на «-», потому что в именах файлов Windows двоеточие запрещено).
    Шаблон, уже записанный в формате yt-dlp (есть «%(»), не трогаем."""
    if "%(" in fmt:
        return fmt

    def repl(m):
        if m.group(1):
            spec = m.group(1).replace(":", "-")
            # у каждого варианта свой формат: «время, а если его нет — дата»
            return f"%(timestamp>{spec},upload_date>{spec})s"
        return _HIT_TOK[m.group(2)]
    out, pos = [], 0
    for m in _HIT.finditer(fmt):
        out.append(fmt[pos:m.start()].replace("%", "%%"))   # одиночный «%» в названии шаблона ломал бы yt-dlp
        out.append(repl(m))
        pos = m.end()
    out.append(fmt[pos:].replace("%", "%%"))
    out = "".join(out)
    if "%(ext)s" not in out:
        out += ".%(ext)s"
    return out


def _name_template(t, st):
    """Шаблон имени файла. Если у сайта нет нормального названия («Video»), подставляем своё прямо в шаблон."""
    tpl = hitomi_template(st.outtmpl)
    if getattr(t, "title_fixed", False) and t.title:
        tpl = tpl.replace("%(title)s", re.sub(r"[\\/:*?\"<>|]", "_", t.title).replace("%", "%%"))
    return tpl


def target_folder(t, st):
    own = bool(t.folder) and (os.path.normcase(os.path.normpath(t.folder))
                              != os.path.normcase(os.path.normpath(st.folder)))
    folder = t.folder or st.folder
    if getattr(st, "site_folders", False) and not own:  # задача создаётся с папкой по умолчанию — это не «своя» папка
        site = safe_name(site_of(t.url) or "")
        if site:
            folder = os.path.join(folder, site)  # D:\Загрузки\youtube\...
    if t.group:
        folder = os.path.join(folder, safe_name(t.group))
    return folder


class YLogger:
    def __init__(self, log):
        self.log = log

    def debug(self, msg):
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        self.log("[yt-dlp] " + str(msg))

    def error(self, msg):
        self.log("[yt-dlp] " + str(msg))


def cookies_file_problem(path):
    """Пустая строка, если файл cookies годится (формат Netscape, как требует yt-dlp), иначе — что не так."""
    if not path:
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            first = f.readline()
    except OSError:
        return "файл не найден или не читается"
    if not re.search(r"#( Netscape)? HTTP Cookie File", first):
        return "это не формат Netscape (в расширении выберите экспорт «Netscape», а не JSON)"
    return ""


def mode_format(mode):
    """(селектор формата yt-dlp, ограничение по разрешению или None).
    Режимы «Видео — 1080p, MP4» и т. п. берут ближайшее разрешение не выше выбранного
    (по меньшей стороне кадра, поэтому вертикальные ролики тоже подходят) и совместимые с MP4 потоки."""
    mode = norm_mode(mode)
    if mode.startswith("r") and mode[1:].isdigit():
        return FORMATS["mp4"], int(mode[1:])
    return FORMATS.get(mode, FORMATS["best"]), None


_DUB_SITES = {"youtube", "vk", "vkvideo", "rutube", "dzen"}


def dubbing_possible(url, extra=""):
    """Несколько языковых дорожек бывают только на некоторых сайтах. Для остальных лишний запрос
    «а есть ли дубляж» не делаем: он удваивает число обращений к сайту, а сайты за это банят (ошибка 403).
    extra — свои сайты через запятую: короткое имя (как в папке сайта, например vimeo) или домен (example.com)."""
    site = site_of(url)
    if site in _DUB_SITES:
        return True
    try:
        host = (urllib.parse.urlparse(url).hostname or "").lower()
    except ValueError:
        host = ""
    for tok in re.split(r"[,;\s]+", (extra or "").lower()):
        tok = re.sub(r"^(https?://)?(www\.)?", "", tok).strip("/ .")
        if tok and (tok == site or host == tok or host.endswith("." + tok)):
            return True
    return False


def is_blocked_error(e):
    """Блокировка сайта (403 / 412 / 429): стоит подождать и повторить, уже «под видом браузера»."""
    s = str(e)
    return any(k in s for k in ("HTTP Error 403", "HTTP Error 412", "HTTP Error 429", "Too Many Requests"))


def with_block_retry(fn, t, log, waits=(6, 20)):
    """Вызывает fn(); при временной блокировке сайта ждёт и повторяет (всего до 3 попыток)."""
    for i in range(len(waits) + 1):
        try:
            return fn()
        except Exception as ex:
            if i >= len(waits) or not is_blocked_error(ex) or t.cancel.is_set() or getattr(t, "removed", False):
                raise
            t.force_imp = True   # следующая попытка — с имитацией браузера (если установлен curl-cffi)
            log(f"[повтор] {t.display_title}: сайт ответил отказом, жду {waits[i]} с и пробую снова")
            if t.cancel.wait(waits[i]):
                raise


def reveal_command(path, platform=None):
    """Команда «показать файл в папке»: Проводник сам откроет папку и выделит файл.
    Возвращает (команда, нужна ли строка целиком) или None, если выделять файл нечем."""
    platform = platform or sys.platform
    if platform.startswith("win"):
        return 'explorer /select,"%s"' % os.path.normpath(path).replace('"', "")
    if platform == "darwin":
        return ["open", "-R", path]
    return None


def is_youtube_mix_link(url):
    """Ссылка на видео внутри автоматического «Микса» YouTube (list=RD…, start_radio=1): это не настоящий
    плейлист, а бесконечная подборка — по такой ссылке всегда нужно только само видео."""
    try:
        p = urllib.parse.urlparse(url or "")
        if "youtube.com" not in p.netloc.lower() and "youtu.be" not in p.netloc.lower():
            return False
        q = urllib.parse.parse_qs(p.query)
        if p.path.startswith("/playlist") or not (q.get("v") or "youtu.be" in p.netloc.lower()):
            return False  # ссылка именно на плейлист — не трогаем
        return any(x.startswith("RD") for x in q.get("list", [])) or q.get("start_radio") == ["1"]
    except Exception:
        return False


def _have_curl_cffi():
    try:
        return importlib.util.find_spec("curl_cffi") is not None
    except (ValueError, ImportError):   # модуль уже загружен без __spec__ — значит, он есть
        return "curl_cffi" in sys.modules


def build_ydl_opts(t, st, probe=False, logger=None, hooks=None, pp_hooks=None):
    folder = target_folder(t, st)
    o = {
        "quiet": True, "no_warnings": True, "noprogress": True, "no_color": True,
        "noplaylist": (not st.playlist_from_video_url) or is_youtube_mix_link(t.url),
        "outtmpl": os.path.join(folder.replace("%", "%%"), _name_template(t, st)),
        "retries": st.retries, "fragment_retries": st.retries, "socket_timeout": 30,
        "concurrent_fragment_downloads": st.threads,
        # папка входит в шаблон имени, и yt-dlp считает лимит по всему пути: длинная папка плейлиста съедала имя файла.
        # Оставляем на весь путь около 240 знаков (предел Windows — 260), но имени даём не меньше 80.
        "trim_file_name": max(80, 240 - len(folder)),
        "continuedl": True,
        "format": mode_format(t.mode)[0],
    }
    if IS_WIN:
        o["windowsfilenames"] = True
    # YouTube: решатель JS-проверок (n challenge) подтягивается с GitHub и кэшируется;
    # старые версии yt-dlp просто игнорируют этот параметр
    o["remote_components"] = ["ejs:github"]
    res = mode_format(t.mode)[1]
    if res:
        o["format_sort"] = [f"res:{res}"]
    if norm_mode(t.mode) in ("mp4", "h264") or res:
        o["merge_output_format"] = "mp4"
    if t.fmt_sel:  # формат выбран вручную в списке форматов — он главнее режима
        o["format"] = t.fmt_sel
        o.pop("format_sort", None)
        o.pop("merge_output_format", None)
    if norm_mode(t.mode) == "audio":
        q = getattr(st, "audio_quality", "mp3_320")
        if q == "original":  # без перекодирования: оригинальная дорожка в своём контейнере
            ex = {"key": "FFmpegExtractAudio", "preferredcodec": "best"}
        else:
            ex = {"key": "FFmpegExtractAudio", "preferredcodec": "mp3",
                  "preferredquality": q.split("_")[-1] if q.startswith("mp3_") else "320"}
        o["postprocessors"] = [ex, {"key": "FFmpegMetadata", "add_metadata": True}]
        if getattr(st, "embed_cover", True):  # обложка внутрь файла (картинка сначала приводится к jpg)
            o["writethumbnail"] = True
            o["postprocessors"] += [
                {"key": "FFmpegThumbnailsConvertor", "format": "jpg", "when": "before_dl"},
                {"key": "EmbedThumbnail", "already_have_thumbnail": False},
            ]
    sm = getattr(st, "subs_mode", "")
    subs_only = norm_mode(t.mode) == "subs"
    if subs_only:  # только .srt-файлы, видео не качаем
        sm = "file"
        o["skip_download"] = True
        o["ignore_no_formats_error"] = True
        o.pop("format_sort", None)
    if sm in ("file", "embed"):
        langs = [x.strip() for x in st.subs_langs.replace(";", ",").split(",") if x.strip()]
        o["writesubtitles"] = True
        o["writeautomaticsub"] = bool(st.subs_auto)
        pats = []
        for x in langs:
            for v in lang_variants(x):
                if f"{v}.*" not in pats:
                    pats.append(f"{v}.*")
        o["subtitleslangs"] = pats or ["all"]
        o["subtitlesformat"] = "srt/vtt/best"
        pps = o.setdefault("postprocessors", [])
        pps.append({"key": "FFmpegSubtitlesConvertor", "format": "srt", "when": "before_dl"})
        if sm == "embed" and norm_mode(t.mode) != "audio":
            pps.append({"key": "FFmpegEmbedSubtitle"})
    if st.speed_limit_kb > 0:
        o["ratelimit"] = st.speed_limit_kb * 1024
    if st.proxy:
        o["proxy"] = st.proxy
    bad = cookies_file_problem(st.cookies_file) if st.cookies_file else ""
    if bad:
        # битый файл ломал бы вообще все загрузки, в том числе с сайтов, где вход не нужен
        if logger:
            logger.warning(f"файл cookies пропущен ({st.cookies_file}): {bad}")
    elif st.cookies_file:
        o["cookiefile"] = st.cookies_file
    elif st.cookies_browser:
        name, prof = browser_spec(st.cookies_browser)
        o["cookiesfrombrowser"] = (name, prof) if prof else (name,)
    if (st.impersonate or t.force_imp) and _have_curl_cffi():
        try:
            from yt_dlp.networking.impersonate import ImpersonateTarget
            o["impersonate"] = ImpersonateTarget.from_str("chrome")
        except Exception:
            pass  # старый yt-dlp — работаем без имитации
    # (без curl-cffi имитацию включить нельзя: yt-dlp ответил бы ошибкой «цель недоступна» и скрыл настоящую причину)
    if probe:
        o["extract_flat"] = "in_playlist"
        o["skip_download"] = True
    if logger:
        o["logger"] = logger
    if hooks:
        o["progress_hooks"] = hooks
    if pp_hooks:
        o["postprocessor_hooks"] = pp_hooks
    return o


def _import_ytdlp():
    try:
        import yt_dlp
        return yt_dlp
    except ImportError:
        raise RuntimeError("yt-dlp не установлен (pip install yt-dlp)")


# ---------- yt-dlp ----------
def thumb_candidates(info):
    """Адреса миниатюр от лучшего к худшему: jpg/png раньше webp/avif (их не везде читает Qt)."""
    urls = []
    ths = [x for x in (info.get("thumbnails") or []) if x.get("url")]
    ths.sort(key=lambda x: (x.get("preference") or 0, (x.get("width") or 0) * (x.get("height") or 0)),
             reverse=True)
    for u in [info.get("thumbnail")] + [x["url"] for x in ths]:
        if u and u.startswith("http") and u not in urls:
            urls.append(u)
    good = [u for u in urls if not re.search(r"\.(webp|avif)(\?|$)", u, re.I)]
    bad = [u for u in urls if u not in good]
    return (good + bad)[:6]


_GENERIC_TITLES = {"", "video", "видео", "untitled", "na", "none", "null", "unknown", "stream", "clip"}


def is_generic_title(title):
    """Безликие названия, которые отдают некоторые сайты (например «Video» у Boosty)."""
    return re.sub(r"[\s_\-\d]+$", "", (title or "").strip().lower()) in _GENERIC_TITLES


def better_title(info):
    """Название из автора и даты (или id), если сайт дал только «Video»."""
    artist = info.get("uploader") or info.get("channel") or ""
    date = ""
    ts = info.get("timestamp")
    if ts:
        try:
            date = time.strftime("%Y-%m-%d", time.gmtime(ts))
        except (OverflowError, OSError, ValueError):
            date = ""
    if not date and re.fullmatch(r"\d{8}", str(info.get("upload_date") or "")):
        u = info["upload_date"]
        date = f"{u[:4]}-{u[4:6]}-{u[6:]}"
    tail = date or str(info.get("id") or "")[:8]
    return " ".join(x for x in (artist, tail) if x) or (info.get("title") or "video")


def format_size_guess(f, duration=0):
    """Вес формата: точный или приблизительный от сайта; если сайт его не сообщает (HLS, Pornhub и т. п.) — оценка по битрейту и длительности."""
    n = f.get("filesize") or f.get("filesize_approx") or 0
    if n:
        return int(n)
    tbr = f.get("tbr") or 0
    if not tbr:
        tbr = (f.get("vbr") or 0) + (f.get("abr") or 0)
    dur = f.get("duration") or duration or 0
    return int(tbr * 125 * dur) if tbr and dur else 0


def probe_ytdlp(t, st, log):
    yt_dlp = _import_ytdlp()
    with yt_dlp.YoutubeDL(build_ydl_opts(t, st, probe=True, logger=YLogger(log))) as ydl:
        info = ydl.extract_info(t.url, download=False)
    if not info:
        raise RuntimeError("Не удалось получить информацию")

    if info.get("_type") in ("playlist", "multi_video") or info.get("entries") is not None:
        entries = []
        for e in info.get("entries") or []:
            if not e:
                continue
            url = e.get("webpage_url") or e.get("url")
            if url and not url.startswith("http"):
                if (e.get("ie_key") or "").lower() == "youtube" and e.get("id"):
                    url = "https://www.youtube.com/watch?v=" + e["id"]
                else:
                    url = None
            if not url:
                continue
            cands = thumb_candidates(e)
            entries.append({"url": url, "title": e.get("title") or "", "id": e.get("id") or "",
                            "thumb": cands[0] if cands else "",
                            "size": int(e.get("filesize") or e.get("filesize_approx") or 0),
                            "duration": int(e.get("duration") or 0)})
        return {"kind": "playlist", "title": info.get("title") or "playlist", "entries": entries}

    dur = info.get("duration") or 0
    size = format_size_guess(info, dur)
    parts = info.get("requested_formats")
    if parts:
        total = sum(format_size_guess(f, dur) for f in parts)
        size = total or size
    fixed = is_generic_title(info.get("title")) and bool(info.get("uploader") or info.get("channel")
                                                          or info.get("timestamp") or info.get("upload_date"))
    return {"kind": "single", "title": better_title(info) if fixed else (info.get("title") or t.url),
            "title_fixed": fixed,
            "artist": info.get("uploader") or info.get("channel") or "",
            "id": str(info.get("id") or ""), "duration": info.get("duration") or 0,
            "size": size, "thumb": (thumb_candidates(info) or [""])[0],
            "thumbs": thumb_candidates(info)}


def _collect_subs(info, out):
    """Пути скачанных субтитров (режим «Только субтитры»)."""
    if not info:
        return
    for sub in (info.get("requested_subtitles") or {}).values():
        fp = (sub or {}).get("filepath")
        if fp and os.path.exists(fp):
            out.append(fp)
    for e in info.get("entries") or []:
        _collect_subs(e, out)


def _collect_files(info, out):
    if not info:
        return
    for d in info.get("requested_downloads") or []:
        fp = d.get("filepath") or d.get("_filename")
        if fp:
            out.append(fp)
    for e in info.get("entries") or []:
        _collect_files(e, out)


def describe_formats(info):
    """'401 av01.0.12M.08 3840x2160 + 251 opus' — что именно выбрал yt-dlp."""
    if not info or info.get("entries") is not None:
        return ""
    parts = info.get("requested_formats") or [info]
    out = []
    for f in parts:
        v, a = f.get("vcodec"), f.get("acodec")
        codec = v if v not in (None, "none") else (a if a not in (None, "none") else "")
        res = f.get("resolution") or ""
        if res in ("audio only", "video only"):
            res = ""
        proto = "m3u8" if "m3u8" in str(f.get("protocol") or "") else ""
        lang = "[%s]" % f["language"] if f.get("language") and v in (None, "none") else ""
        bits = [str(f.get("format_id") or "?"), codec, res, proto, lang]
        out.append(" ".join(b for b in bits if b))
    return " + ".join(out)


def browser_spec(value):
    """'operagx' -> ('opera', путь к профилю Opera GX); остальное — как есть (имя[:профиль])."""
    value = (value or "").strip()
    if value.lower() == "operagx":
        return "opera", os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"),
                                     "Opera Software", "Opera GX Stable")
    if ":" in value and not value.lower().startswith("operagx"):
        name, prof = value.split(":", 1)
        return name.strip().lower(), prof.strip() or None
    return value.lower(), None


def lang_variants(code):
    """'ru' -> ['ru', 'rus'], 'eng' -> ['en', 'eng']: сайты пишут язык по-разному (YouTube — ru, ВК — rus)."""
    base = _norm_lang(code)
    out = [base]
    if base in _ISO3:
        out.append(_ISO3[base])
    elif code.strip().lower() != base:
        out.append(code.strip().lower())
    return out


def _norm_lang(lang):
    """'ru', 'rus', 'ru-RU' -> 'ru'; 'pt-BR' -> 'pt'. Неизвестные коды остаются как есть."""
    base = (lang or "").lower().replace("_", "-").split("-")[0]
    back = {v: k for k, v in _ISO3.items()}
    return back.get(base, base)


def _lang_matches(lang, wanted):
    return any(_norm_lang(lang) == _norm_lang(w) for w in wanted)


def pick_audio_formats(info, prefer_m4a, langs=None):
    """По одной лучшей звуковой дорожке на язык. Оригинал («по умолчанию») идёт первым.
    langs — языки дубляжа, например ['ru']; оригинал берётся всегда. Пусто / None — все языки.
    Возвращает список format_id. Пропускает DRC-версии (с выровненной громкостью) и HLS."""
    groups = {}
    for f in (info or {}).get("formats") or []:
        if f.get("vcodec") not in (None, "none") or f.get("acodec") in (None, "none"):
            continue
        if "m3u8" in str(f.get("protocol") or ""):
            continue
        if "drc" in str(f.get("format_note") or "").lower() or str(f.get("format_id") or "").endswith("-drc"):
            continue
        groups.setdefault(f.get("language") or "und", []).append(f)

    def pref(f):
        return f.get("language_preference") or 0

    def note(f):
        return str(f.get("format_note") or "").lower()

    orig = {lg for lg, fs in groups.items() if any("(default)" in note(f) or "original" in note(f) for f in fs)}
    if not orig:  # нет пометки — оригиналом считаем дорожку с наибольшим приоритетом, если приоритеты разные
        allf = [f for fs in groups.values() for f in fs]
        top = max((pref(f) for f in allf), default=0)
        if any(pref(f) != top for f in allf):
            orig = {lg for lg, fs in groups.items() if any(pref(f) == top for f in fs)}
    if not orig:  # ВК и подобные: у видеопотока стоит язык — это и есть язык оригинала
        vids = [f for f in (info or {}).get("formats") or []
                if f.get("vcodec") not in (None, "none") and f.get("language")]
        if vids:
            vl = _norm_lang(max(vids, key=lambda f: (f.get("height") or 0, f.get("tbr") or 0))["language"])
            orig = {lg for lg in groups if _norm_lang(lg) == vl}
    wanted = [w.strip().lower() for w in (langs or []) if w and w.strip()]

    picks = []
    for lg, fs in groups.items():
        if wanted and lg not in orig and not _lang_matches(lg, wanted):
            continue
        top = max(pref(f) for f in fs)
        fs = [f for f in fs if pref(f) == top]
        pool = ([f for f in fs if f.get("ext") == "m4a"] if prefer_m4a else []) or fs
        best = max(pool, key=lambda f: f.get("abr") or f.get("tbr") or 0)
        widx = next((i for i, w in enumerate(wanted) if _lang_matches(lg, [w])), 99)
        picks.append((lg not in orig, widx, -top, best))
    picks.sort(key=lambda p: p[:3])
    return [str(p[3]["format_id"]) for p in picks]


def multi_audio_format(mode, ids):
    """Селектор «видео + все выбранные дорожки», с откатом на обычный формат режима."""
    mode = norm_mode(mode)
    plain, res = mode_format(mode)
    video = "bv*[vcodec!*=av01][protocol!*=m3u8]" if (mode == "mp4" or res) else "bv*"
    audio = "+".join(ids)
    first = f"{video}+{audio}"
    return f"{first}/bv*+{audio}/{plain}" if video != "bv*" else f"{first}/{plain}"


_ISO3 = {"en": "eng", "ru": "rus", "de": "deu", "fr": "fra", "es": "spa", "it": "ita", "pt": "por",
         "ja": "jpn", "ko": "kor", "zh": "zho", "uk": "ukr", "pl": "pol", "tr": "tur", "ar": "ara",
         "hi": "hin", "id": "ind", "nl": "nld", "cs": "ces", "th": "tha", "vi": "vie"}


def audio_meta_args(info, ids):
    """Аргументы ffmpeg: язык у каждой дорожки и «по умолчанию» только у первой."""
    langs = {f.get("format_id"): (f.get("language") or "") for f in info.get("formats") or []}
    args = []
    for i, fid in enumerate(ids):
        base = langs.get(fid, "").split("-")[0].lower()
        if base:
            args += [f"-metadata:s:a:{i}", f"language={_ISO3.get(base, base)}"]
        args += [f"-disposition:a:{i}", "default" if i == 0 else "0"]
    return args


def audio_track_suffixes(info, ids):
    """Уникальные суффиксы имён для отдельных звуковых файлов: ['en', 'ru'] (без языка — 'orig' / номер)."""
    langs = {str(f.get("format_id")): (f.get("language") or "") for f in (info or {}).get("formats") or []}
    out = []
    for i, fid in enumerate(ids):
        s = re.sub(r"[^\w-]", "_", langs.get(fid, "").strip().lower()) or ("orig" if i == 0 else f"track{i + 1}")
        if s in out:
            s = f"{s}_{fid}"
        out.append(s)
    return out


def _audio_tracks(t, st, yt_dlp, log):
    """(info, [format_id...]) — оригинал и выбранный дубляж; None, если дорожка одна или список не получен."""
    try:
        with yt_dlp.YoutubeDL(build_ydl_opts(t, st, logger=YLogger(log))) as ydl:
            info = ydl.extract_info(t.url, download=False)
        if not info or info.get("entries") is not None:
            return None
        langs = [x.strip() for x in st.audio_langs.replace(";", ",").split(",") if x.strip()]
        ids = pick_audio_formats(info, prefer_m4a=False, langs=langs)
        if len(ids) < 2:
            log(f"[дорожки] {t.display_title}: дубляжа на нужных языках ({', '.join(langs) or 'все'}) не найдено")
            return None
        return info, ids
    except Exception as e:
        log(f"[дорожки] не удалось получить список дорожек, качаю обычным способом: {e}")
        return None


def download_audio_tracks(t, st, engine, yt_dlp, pp_hook):
    """Режим «Только звук» + дубляж: каждая дорожка отдельным файлом, имя.язык.mp3. Вернёт info-список или None."""
    got = _audio_tracks(t, st, yt_dlp, engine.log)
    if not got:
        return None
    info, ids = got
    sufs = audio_track_suffixes(info, ids)
    engine.log(f"[дорожки] {t.display_title}: " + ", ".join(f"{s}={i}" for s, i in zip(sufs, ids)))
    infos = []
    for fid, suf in zip(ids, sufs):
        o = build_ydl_opts(t, st, logger=YLogger(engine.log), hooks=[engine.make_hook(t)], pp_hooks=[pp_hook])
        o["format"] = fid
        tail = ".%(ext)s"
        if o["outtmpl"].endswith(tail):
            o["outtmpl"] = o["outtmpl"][:-len(tail)] + f".{suf}" + tail
        else:   # свой шаблон без «.%(ext)s» в конце: суффикс всё равно нужен, иначе дорожки перезапишут друг друга
            o["outtmpl"] = o["outtmpl"] + f".{suf}"
        with yt_dlp.YoutubeDL(o) as ydl:
            infos.append(ydl.extract_info(t.url, download=True))
    return infos


def _multi_audio_selector(t, st, yt_dlp, log):
    try:
        with yt_dlp.YoutubeDL(build_ydl_opts(t, st, logger=YLogger(log))) as ydl:
            info = ydl.extract_info(t.url, download=False)
        mode = norm_mode(t.mode)
        langs = [x.strip() for x in st.audio_langs.replace(";", ",").split(",") if x.strip()]
        ids = pick_audio_formats(info, prefer_m4a=(mode == "mp4" or mode_format(mode)[1] is not None),
                                 langs=langs)
        if len(ids) < 2:
            log(f"[дорожки] {t.display_title}: дубляжа на нужных языках ({', '.join(langs) or 'все'}) не найдено")
            return None  # дорожка одна — обычная загрузка
        log(f"[дорожки] {t.display_title}: {', '.join(ids)}")
        return multi_audio_format(mode, ids), audio_meta_args(info, ids)
    except Exception as e:
        log(f"[дорожки] не удалось получить список дорожек, качаю обычным способом: {e}")
        return None


def format_selector(f):
    """Селектор yt-dlp для выбранной строки списка форматов: только видео — добавляем лучший звук."""
    fid = str(f.get("id", ""))
    if f.get("kind") == "video":
        return f"{fid}+bestaudio/{fid}"
    return fid


def format_rows(info):
    """Список форматов ролика для окна выбора: от лучшего видео к худшему, потом звук."""
    rows = []
    for f in (info or {}).get("formats") or []:
        fid = str(f.get("format_id") or "")
        if not fid or f.get("ext") == "mhtml" or fid.startswith("sb"):
            continue
        vc = (f.get("vcodec") or "none")
        ac = (f.get("acodec") or "none")
        has_v, has_a = vc != "none", ac != "none"
        if not has_v and not has_a and not f.get("height"):
            if (f.get("resolution") or "") != "audio only":
                continue
        kind = "both" if (has_v and has_a) else ("video" if has_v or f.get("height") else "audio")
        h = f.get("height") or 0
        res = f"{f.get('width') or '?'}x{h}" if h else ("только звук" if kind == "audio" else "")
        rate = f.get("abr") if kind == "audio" else f.get("tbr")
        row = {"id": fid, "ext": f.get("ext") or "", "res": res, "height": h,
               "fps": int(f.get("fps") or 0), "vcodec": vc.split(".")[0] if has_v else "",
               "acodec": ac.split(".")[0] if has_a else "",
               "rate": int(rate) if rate else 0,
               "size": f.get("filesize") or f.get("filesize_approx") or 0,
               "note": f.get("format_note") or f.get("language") or "", "kind": kind}
        row["sel"] = format_selector(row)
        rows.append(row)
    order = {"both": 0, "video": 0, "audio": 1}
    rows.sort(key=lambda r: (order[r["kind"]], -r["height"], -r["rate"]))
    return rows


def probe_formats(t, st, log):
    """Полный список форматов ролика (долго: запрашивает все потоки). Возвращает (название, строки)."""
    yt_dlp = _import_ytdlp()
    o = build_ydl_opts(t, st, logger=YLogger(log))
    for k in ("format", "format_sort", "postprocessors", "writethumbnail", "merge_output_format"):
        o.pop(k, None)
    o.update({"skip_download": True, "ignore_no_formats_error": True, "noplaylist": True})
    with yt_dlp.YoutubeDL(o) as ydl:
        info = ydl.extract_info(t.url, download=False)
    rows = format_rows(info)
    if not rows:
        raise RuntimeError("Сайт не отдал список форматов")
    return (info.get("title") or t.display_title), rows


def is_cover_error(e):
    m = str(e).lower()
    return any(w in m for w in ("thumbnail", "cover", "mutagen", "atomicparsley"))


def download_ytdlp(t, st, engine):
    yt_dlp = _import_ytdlp()

    def pp_hook(d):
        if d.get("status") == "started":
            t.status = "Обработка..."
            engine.notify(t.id)

    opts = build_ydl_opts(t, st, logger=YLogger(engine.log), hooks=[engine.make_hook(t)],
                          pp_hooks=[pp_hook])
    if st.all_audio and not t.fmt_sel and norm_mode(t.mode) not in ("audio", "subs") \
            and dubbing_possible(t.url, st.dub_sites):
        sel = _multi_audio_selector(t, st, yt_dlp, engine.log)
        if sel:
            fmt, meta = sel
            opts["format"] = fmt
            opts["postprocessor_args"] = {"merger": meta}
            opts["allow_multiple_audio_streams"] = True
            opts["audio_multistreams"] = True
    if st.all_audio and not t.fmt_sel and norm_mode(t.mode) == "audio" \
            and dubbing_possible(t.url, st.dub_sites):
        infos = download_audio_tracks(t, st, engine, yt_dlp, pp_hook)
        if infos:
            files = []
            for inf in infos:
                _collect_files(inf, files)
            t.files = files
            t.size = sum(os.path.getsize(f) for f in files if os.path.exists(f))
            t.fmt_info = " | ".join(x for x in (describe_formats(i) for i in infos) if x)
            return
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(t.url, download=True)
    files = []
    if norm_mode(t.mode) == "subs":
        _collect_subs(info, files)
        if not files:
            raise RuntimeError("У видео нет субтитров на выбранных языках (языки задаются в Параметры → Основные)")
        t.files = files
        t.size = sum(os.path.getsize(f) for f in files)
        t.fmt_info = "субтитры: " + ", ".join(sorted({os.path.basename(f).rsplit(".", 2)[-2] for f in files}))
        return
    _collect_files(info, files)
    t.files = files
    t.size = sum(os.path.getsize(f) for f in files if os.path.exists(f))
    t.fmt_info = describe_formats(info)
    if t.fmt_info:
        engine.log(f"[формат] {t.display_title}: {t.fmt_info}")


# ---------- gallery-dl ----------
def pages_to_gdl(text):
    """Диапазон страниц -> --range у gallery-dl. Поддерживаются только положительные границы."""
    out = []
    for tok in re.split(r"[,;]", text or ""):
        tok = tok.strip()
        if not tok:
            continue
        if "~" in tok:
            a, _, b = tok.partition("~")
            a, b = a.strip() or "1", b.strip()
            if a.startswith("-") or b.startswith("-"):
                continue
            out.append(f"{a}-{b}")
        elif not tok.startswith("-"):
            out.append(tok)
    return ",".join(out)


def download_gallerydl(t, st, engine):
    folder = target_folder(t, st)
    cmd = [sys.executable, "-m", "gallery_dl", "-d", folder]
    if st.cookies_file:
        cmd += ["--cookies", st.cookies_file]
    elif st.cookies_browser:
        name, prof = browser_spec(st.cookies_browser)
        cmd += ["--cookies-from-browser", f"{name}:{prof}" if prof else name]
    if st.proxy:
        cmd += ["--proxy", st.proxy]
    if st.speed_limit_kb > 0:
        cmd += ["--limit-rate", f"{st.speed_limit_kb}k"]
    rng = pages_to_gdl(t.pages)
    if rng:
        cmd += ["--range", rng]
    cmd.append(t.url)

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace",
                            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                            creationflags=subprocess.CREATE_NO_WINDOW if IS_WIN else 0)
    t.proc = proc
    files, last_msg = [], ""
    try:
        for line in proc.stdout:
            if t.cancel.is_set():
                proc.terminate()
                raise Cancelled()
            line = line.strip()
            if not line:
                continue
            path = line[1:].strip() if line.startswith("#") else line
            if os.path.exists(path):
                files.append(path)
                t.status = f"Файлов: {len(files)}"
                engine.notify(t.id)
            else:
                last_msg = line
                engine.log("[gallery-dl] " + line)
        rc = proc.wait()
    finally:
        if proc.poll() is None:
            proc.kill()
        try:
            proc.wait(timeout=10)   # забираем процесс, иначе остаётся «зомби» и открытый канал
        except Exception:
            pass
        try:
            proc.stdout.close()
        except Exception:
            pass
    if t.cancel.is_set():
        raise Cancelled()
    t.files = files
    t.size = sum(os.path.getsize(f) for f in files if os.path.exists(f))
    if files:
        t.thumb_file = next((f for f in files if f.lower().endswith(IMAGE_EXT)), "")
        if not t.title or t.title == t.url:
            t.title = os.path.basename(os.path.dirname(files[0])) or t.title
    if rc != 0:
        if not files:
            raise RuntimeError(last_msg or f"gallery-dl завершился с кодом {rc}")
        raise RuntimeError(f"gallery-dl завершился с кодом {rc}")
    if not files:
        raise RuntimeError(last_msg or "Файлы не найдены")


# ---------- миниатюры ----------
_IMG_MAGIC = (b"\xff\xd8\xff", b"\x89PNG", b"GIF8", b"BM", b"RIFF")


def _looks_like_image(data):
    return any(data.startswith(m) for m in _IMG_MAGIC) and (not data.startswith(b"RIFF") or data[8:12] == b"WEBP")


def fetch_thumb(url, referer="", proxy=""):
    """Скачивает одну миниатюру. Пустая строка — не получилось. Неудачи в кэш не пишутся."""
    if not url:
        return ""
    try:
        os.makedirs(THUMB_DIR, exist_ok=True)
        path = os.path.join(THUMB_DIR, hashlib.md5(url.encode("utf-8")).hexdigest() + ".img")
        if os.path.exists(path) and os.path.getsize(path) > 200:
            return path
        headers = {"User-Agent": UA, "Accept": "image/jpeg,image/png,image/*;q=0.8,*/*;q=0.5"}
        if referer:
            p = urllib.parse.urlparse(referer)
            headers["Referer"] = f"{p.scheme}://{p.netloc}/"
        opener = urllib.request.build_opener(
            *([urllib.request.ProxyHandler({"http": proxy, "https": proxy})] if proxy else []))
        with opener.open(urllib.request.Request(url, headers=headers), timeout=12) as r:
            data = r.read(3_000_000)
        if len(data) < 200 or not _looks_like_image(data):
            return ""  # страница с ошибкой вместо картинки
        if len(data) >= 3_000_000:
            return ""   # обрезанная картинка: не кэшируем битый файл
        tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)   # другой поток никогда не увидит наполовину записанный файл
        return path
    except Exception:
        return ""


def fetch_thumb_any(urls, referer="", proxy=""):
    for u in urls:
        p = fetch_thumb(u, referer, proxy)
        if p:
            return p
    return ""


def thumb_from_video(video_path):
    """Запасной вариант: кадр из скачанного видео через ffmpeg."""
    try:
        import shutil
        ff = shutil.which("ffmpeg")
        if not ff or not video_path or not os.path.exists(video_path):
            return ""
        os.makedirs(THUMB_DIR, exist_ok=True)
        out = os.path.join(THUMB_DIR, hashlib.md5(video_path.encode("utf-8")).hexdigest() + "_f.img")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        for ss in ("5", "1", "0"):
            subprocess.run([ff, "-y", "-loglevel", "error", "-ss", ss, "-i", video_path, "-frames:v", "1",
                            "-vf", "scale=480:-2", "-f", "image2", "-c:v", "mjpeg", out],
                           timeout=60, creationflags=flags, stdin=subprocess.DEVNULL)
            if os.path.exists(out) and os.path.getsize(out) > 200:
                return out
        return ""
    except Exception:
        return ""


# ---------- объединение видео с внешними субтитрами и звуком ----------
LANG_NAMES = {"ru": "Русский", "en": "English", "de": "Deutsch", "fr": "Français", "es": "Español",
              "it": "Italiano", "pt": "Português", "ja": "日本語", "ko": "한국어", "zh": "中文",
              "uk": "Українська", "pl": "Polski", "tr": "Türkçe", "ar": "العربية", "hi": "हिन्दी",
              "id": "Indonesia", "nl": "Nederlands", "cs": "Čeština", "th": "ไทย", "vi": "Tiếng Việt"}
SUB_EXT = (".srt", ".ass", ".ssa", ".vtt")
AUDIO_EXT = (".mp3", ".m4a", ".aac", ".opus", ".ogg", ".flac", ".wav", ".mka", ".ac3", ".eac3", ".dts")
VIDEO_EXT = (".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi")


def find_ffmpeg():
    import shutil
    return shutil.which("ffmpeg") or ""


def guess_track_lang(path):
    """Язык по имени файла: 'video.ru.srt', 'film_eng.mp3' -> 'ru', 'en'. Не нашли — пустая строка."""
    stem = os.path.splitext(os.path.basename(path or ""))[0].lower()
    toks = [x for x in re.split(r"[.\-_ \[\]()]+", stem) if x]
    for tok in reversed(toks[-2:]):
        n = _norm_lang(tok)
        if n in LANG_NAMES:
            return n
    return ""


def default_merge_output(video):
    base, ext = os.path.splitext(video)
    return f"{base}.merged{ext if ext.lower() in ('.mp4', '.mkv', '.m4v', '.mov') else '.mkv'}"


def validate_merge(video, subs, audios, out):
    """Текст ошибки или пустая строка, если можно запускать."""
    if not video or not os.path.isfile(video):
        return "Выберите видеофайл."
    if not subs and not audios:
        return "Добавьте хотя бы один файл субтитров или звука."
    for p, _ in list(subs) + list(audios):
        if not os.path.isfile(p):
            return f"Файл не найден: {p}"
    if not out:
        return "Укажите, куда сохранить результат."
    if os.path.splitext(out)[1].lower() not in (".mp4", ".mkv"):
        return "Результат должен быть в формате .mkv или .mp4."
    key = lambda q: os.path.normcase(os.path.abspath(q))
    if key(out) == key(video):
        return "Результат нельзя сохранять поверх исходного видео — выберите другое имя."
    if any(key(out) == key(q) for q, _ in list(subs) + list(audios)):
        return "Результат нельзя сохранять поверх добавляемого файла — выберите другое имя."
    return ""


def count_media_streams(ff, path):
    """(число звуковых дорожек, число дорожек субтитров) в файле — по выводу ffmpeg -i."""
    try:
        r = subprocess.run([ff, "-hide_banner", "-i", path], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=60, stdin=subprocess.DEVNULL,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        return 0, 0
    a = s = 0
    for line in (r.stderr or "").splitlines():
        m = re.search(r"Stream #0:\d+\S*: (Audio|Subtitle):", line)
        if m:
            a += m.group(1) == "Audio"
            s += m.group(1) == "Subtitle"
    return a, s


def merge_command(ff, video, subs, audios, out, n_audio=0, n_sub=0):
    """Команда ffmpeg: всё из исходного видео + новые субтитры и звуковые дорожки, без перекодирования.
    subs / audios — списки (путь, язык); n_audio / n_sub — сколько таких дорожек уже есть в видео."""
    ext = os.path.splitext(out)[1].lower()
    cmd = [ff, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", video]
    for p, _ in list(subs) + list(audios):
        cmd += ["-i", p]
    cmd += ["-map", "0:v", "-map", "0:a?", "-map", "0:s?"]
    i = 1
    for _ in subs:
        cmd += ["-map", f"{i}:0"]
        i += 1
    for _ in audios:
        cmd += ["-map", f"{i}:a:0"]
        i += 1
    cmd += ["-c:v", "copy", "-c:a", "copy"]
    if ext == ".mp4":
        cmd += ["-c:s", "mov_text"]
    else:
        cmd += ["-c:s", "copy"]
        for k, (p, _) in enumerate(subs):
            if os.path.splitext(p)[1].lower() == ".vtt":
                cmd += [f"-c:s:{n_sub + k}", "srt"]
    for k, (_, lang) in enumerate(subs):
        if lang:
            cmd += [f"-metadata:s:s:{n_sub + k}", f"language={_ISO3.get(lang, lang)}",
                    f"-metadata:s:s:{n_sub + k}", f"title={LANG_NAMES.get(lang, lang)}"]
    for k, (_, lang) in enumerate(audios):
        if lang:
            cmd += [f"-metadata:s:a:{n_audio + k}", f"language={_ISO3.get(lang, lang)}",
                    f"-metadata:s:a:{n_audio + k}", f"title={LANG_NAMES.get(lang, lang)}"]
        cmd += [f"-disposition:a:{n_audio + k}", "0"]
    cmd.append(out)
    return cmd


def merge_tracks(video, subs, audios, out, holder=None):
    """Собирает новый файл. holder — словарь для отмены: holder['proc'] — процесс, holder['cancel'] = True.
    Исходный файл не меняется. Бросает RuntimeError с понятным текстом."""
    err = validate_merge(video, subs, audios, out)
    if err:
        raise RuntimeError(err)
    ff = find_ffmpeg()
    if not ff:
        raise RuntimeError("Не найден ffmpeg. Запустите install.bat или установите ffmpeg.")
    holder = holder if holder is not None else {}
    na, ns = count_media_streams(ff, video)
    existed = os.path.exists(out)
    cmd = merge_command(ff, video, subs, audios, out, na, ns)
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                            errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    holder["proc"] = proc
    _, errtext = proc.communicate()
    if holder.get("cancel") or proc.returncode != 0:
        try:
            if os.path.exists(out) and not existed:   # чужой уже лежавший файл не трогаем
                os.remove(out)
        except OSError:
            pass
        if holder.get("cancel"):
            raise Cancelled()
        tail = "\n".join((errtext or "").strip().splitlines()[-4:])
        raise RuntimeError("ffmpeg не смог собрать файл:\n" + (tail or f"код {proc.returncode}"))
    return out



# ---------- извлечение дорожек из видео ----------
TEXT_SUB_CODECS = {"subrip", "srt", "ass", "ssa", "mov_text", "webvtt", "text", "microdvd", "subviewer"}
_AUDIO_COPY_EXT = {"aac": ".m4a", "mp3": ".mp3", "opus": ".opus", "vorbis": ".ogg", "flac": ".flac",
                   "ac3": ".ac3", "eac3": ".eac3", "dts": ".dts", "alac": ".m4a", "truehd": ".thd",
                   "pcm_s16le": ".wav", "pcm_s24le": ".wav", "pcm_s32le": ".wav"}


def find_ffprobe():
    import shutil
    p = shutil.which("ffprobe")
    if p:
        return p
    ff = find_ffmpeg()
    if ff:
        cand = os.path.join(os.path.dirname(ff), "ffprobe.exe" if IS_WIN else "ffprobe")
        if os.path.exists(cand):
            return cand
    return ""


def parse_probe_json(data):
    """Дорожки звука и субтитров из ответа ffprobe (-show_streams -of json)."""
    out, na, ns = [], 0, 0
    for s in (data or {}).get("streams") or []:
        kind = s.get("codec_type")
        if kind not in ("audio", "subtitle"):
            continue
        tags = {str(k).lower(): v for k, v in (s.get("tags") or {}).items()}
        lang = _norm_lang(tags.get("language", "")) if tags.get("language", "") not in ("", "und") else ""
        if kind == "audio":
            na += 1
            n = na
        else:
            ns += 1
            n = ns
        out.append({"index": int(s.get("index", 0)), "kind": kind, "codec": s.get("codec_name") or "?",
                    "lang": lang, "title": tags.get("title", ""), "channels": s.get("channels") or 0,
                    "n": n, "default": bool((s.get("disposition") or {}).get("default"))})
    return out


def probe_tracks(path):
    """Звуковые дорожки и субтитры видеофайла. Бросает RuntimeError, если файл не прочитать."""
    import json
    fp = find_ffprobe()
    if not fp:
        raise RuntimeError("Не найден ffprobe (он ставится вместе с ffmpeg). Запустите install.bat.")
    r = subprocess.run([fp, "-v", "error", "-show_streams", "-of", "json", path], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120, stdin=subprocess.DEVNULL,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode != 0:
        raise RuntimeError("Не удалось прочитать файл: " + (r.stderr or "").strip()[-300:])
    return parse_probe_json(json.loads(r.stdout or "{}"))


def track_label(t, tr=lambda s: s):
    """Подпись дорожки в списке. tr — перевод интерфейса (по умолчанию текст остаётся русским)."""
    lang = LANG_NAMES.get(t["lang"], t["lang"]) or tr("язык не указан")
    if t["kind"] == "audio":
        ch = {1: tr("моно"), 2: tr("стерео"), 6: "5.1", 8: "7.1"}.get(
            t["channels"], f"{t['channels']} " + tr("кан.") if t["channels"] else "")
        info = ", ".join(x for x in (t["codec"], ch) if x)
        head = tr("Звук") + f" {t['n']}"
    else:
        info = t["codec"] + ("" if t["codec"] in TEXT_SUB_CODECS or t["codec"] == "hdmv_pgs_subtitle"
                             else " (" + tr("графические, не поддерживаются") + ")")
        head = tr("Субтитры") + f" {t['n']}"
    title = f" «{t['title']}»" if t["title"] else ""
    return f"{head}: {lang} — {info}{title}"


def track_supported(t):
    return t["kind"] == "audio" or t["codec"] in TEXT_SUB_CODECS or t["codec"] == "hdmv_pgs_subtitle"


def plan_extract(ff, video, tracks, out_dir, audio_mode="copy"):
    """Список заданий [{'out': путь, 'cmd': [...]}]. Имена: <видео>.<язык>.<расширение>, без перезаписи чужих файлов.
    audio_mode: 'copy' — звук как есть (без перекодирования), 'mp3' — перекодировать в MP3 320."""
    stem = os.path.splitext(os.path.basename(video))[0]
    used, jobs = set(), []
    _safe = lambda x: re.sub(r"[^\w-]", "_", str(x))[:12]
    for t in tracks:
        if not track_supported(t):
            continue
        if t["kind"] == "audio":
            if audio_mode == "mp3":
                ext, args = ".mp3", ["-vn", "-c:a", "libmp3lame", "-b:a", "320k"]
            else:
                ext, args = _AUDIO_COPY_EXT.get(t["codec"], ".mka"), ["-vn", "-c:a", "copy"]
            tag = _safe(t["lang"] or f"audio{t['n']}")
        else:
            if t["codec"] == "hdmv_pgs_subtitle":
                ext, args = ".sup", ["-c:s", "copy"]
            elif t["codec"] in ("ass", "ssa"):
                ext, args = ".ass", ["-c:s", "copy"]
            else:
                ext, args = ".srt", ["-c:s", "srt"]
            tag = _safe(t["lang"] or f"sub{t['n']}")
        base = f"{stem}.{tag}"
        out = os.path.join(out_dir, base + ext)
        k = 2
        while out.lower() in used or os.path.exists(out):
            out = os.path.join(out_dir, f"{base}.{k}{ext}")
            k += 1
        used.add(out.lower())
        cmd = [ff, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", video,
               "-map", f"0:{t['index']}"] + args + [out]
        jobs.append({"out": out, "cmd": cmd})
    return jobs


def extract_tracks(jobs, holder=None, progress=None):
    """Выполняет задания по очереди. Возвращает (список готовых файлов, список ошибок).
    holder: {'cancel': True} прерывает, holder['proc'] — текущий процесс. progress(i, total)."""
    holder = holder if holder is not None else {}
    done, errors = [], []
    for i, job in enumerate(jobs):
        if holder.get("cancel"):
            raise Cancelled()
        if progress:
            progress(i, len(jobs))
        proc = subprocess.Popen(job["cmd"], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
                                encoding="utf-8", errors="replace",
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        holder["proc"] = proc
        _, err = proc.communicate()
        if holder.get("cancel"):
            try:
                os.remove(job["out"])
            except OSError:
                pass
            raise Cancelled()
        if proc.returncode != 0 or not os.path.exists(job["out"]):
            try:
                os.remove(job["out"])
            except OSError:
                pass
            errors.append(f"{os.path.basename(job['out'])}: " + ((err or "").strip().splitlines() or ["ошибка"])[-1])
        else:
            done.append(job["out"])
    if progress:
        progress(len(jobs), len(jobs))
    return done, errors



def update_ytdlp(pre=False):
    """pip install -U yt-dlp[default] gallery-dl (pre — тестовые версии). Возвращает (успех, вывод)."""
    cmd = [sys.executable, "-m", "pip", "install", "-U"] + (["--pre"] if pre else []) + ["yt-dlp[default]", "gallery-dl"]
    try:
        r = subprocess.run(cmd,
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=300, creationflags=subprocess.CREATE_NO_WINDOW if IS_WIN else 0)
        return r.returncode == 0, (r.stdout + "\n" + r.stderr).strip()[-1500:]
    except Exception as e:
        return False, str(e)


# ---------- планировщик ----------
def _noop(*a, **k):
    pass


# ======================= Telegram-бот =======================
# Бот без Qt и сторонних библиотек (только urllib): принимает сообщения от разрешённых участников (по Telegram ID),
# передаёт их программе (on_message) и отправляет текст и файлы через очередь в отдельном потоке.
API = "https://api.telegram.org"


class TgError(Exception):
    def __init__(self, text, code=0):
        super().__init__(text)
        self.code = code


def build_multipart(fields, file_field, path):
    """Тело multipart/form-data для sendDocument. Возвращает (bytes, content_type)."""
    boundary = uuid.uuid4().hex
    parts = []
    for k, v in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode("utf-8"))
    name = os.path.basename(path)
    ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
    # имя файла в UTF-8: Telegram принимает его как есть
    head = (f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; '
            f'filename="{name}"\r\nContent-Type: {ctype}\r\n\r\n').encode("utf-8")
    with open(path, "rb") as f:
        data = f.read()
    body = b"".join(parts) + head + data + f"\r\n--{boundary}--\r\n".encode("utf-8")
    return body, f"multipart/form-data; boundary={boundary}"


class TelegramBot:
    MAX_FILE = 50 * 1024 * 1024    # предел Bot API для отправки файла
    MAX_PENDING = 50               # сколько заявок от посторонних держим одновременно

    def __init__(self, token, allowed_ids, on_message, log=print, proxy="", guests=(), members=None):
        """allowed_ids — администраторы (попадают в таблицу участников), guests — гости.
        members — таблица участников (core.Members). on_message(chat_id, text, user_id, is_admin)."""
        self.token = (token or "").strip()
        self.members = members if members is not None else Members()
        for uid in allowed_ids:
            if self.members.role(uid) != "admin":
                self.members.set_role(uid, "admin")
        for uid in guests:
            if not self.members.role(uid):
                self.members.set_role(uid, "guest")
        self.auto_accept = False   # True — новый участник сразу становится гостем
        self.on_message = on_message
        self.log = log
        self.proxy = (proxy or "").strip()
        self.offset = 0
        self.username = ""
        self._stop = threading.Event()
        self._out = queue.Queue()
        self._threads = []
        self._denied = {}  # chat_id -> время последнего отказа
        handlers = [urllib.request.ProxyHandler({"http": self.proxy, "https": self.proxy})] if self.proxy else []
        self._opener = urllib.request.build_opener(*handlers)
        self._last_err = ""
        self._menu_done = False

    @property
    def admins(self):
        return self.members.ids("admin")

    @property
    def guests(self):
        return self.members.ids("guest")

    @property
    def allowed(self):
        return self.members.ids("admin", "guest")

    # ---------- запуск / остановка ----------
    def start(self):
        self._stop.clear()
        if self.proxy.lower().startswith("socks"):
            self.log("[telegram] SOCKS-прокси бот не поддерживает: укажите HTTP-прокси")
        for fn in (self._poll_loop, self._out_loop):
            t = threading.Thread(target=fn, daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self):
        self._stop.set()
        self._out.put(None)

    # ---------- вызовы API ----------
    def _call(self, method, params=None, body=None, content_type=None, timeout=35):
        url = f"{API}/bot{self.token}/{method}"
        if body is None:
            body = json.dumps(params or {}).encode("utf-8")
            content_type = "application/json"
        req = urllib.request.Request(url, data=body, headers={"Content-Type": content_type})
        try:
            with self._opener.open(req, timeout=timeout) as r:
                data = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                data = json.loads(e.read().decode("utf-8"))
            except Exception:
                raise TgError(f"HTTP {e.code}", e.code)
            raise TgError(data.get("description") or f"HTTP {e.code}", data.get("error_code") or e.code)
        if not data.get("ok"):
            raise TgError(data.get("description") or "ошибка Telegram", data.get("error_code") or 0)
        return data.get("result")

    def send_text(self, chat_id, text, markup=None):
        p = {"chat_id": chat_id, "text": text[:4000], "disable_web_page_preview": True}
        if markup:
            p["reply_markup"] = markup
        self._call("sendMessage", p)

    def keyboard_for(self, uid, force=False):
        """Кнопки внизу чата по правам: гость, администратор или руководитель.
        None — участник скрыл кнопки (/hide); force=True показывает их в любом случае (/menu)."""
        if not force and self.members.is_hidden(uid):
            return None
        if uid is not None and uid == self.members.owner:
            btns = TG_BUTTONS_OWNER
        elif self.members.role(uid) == "admin":
            btns = TG_BUTTONS_ADMIN
        else:
            btns = TG_BUTTONS_GUEST
        labels = [{"text": tr(l)} for l, _ in btns]
        rows = [labels[i:i + 2] for i in range(0, len(labels), 2)]
        return {"keyboard": rows, "resize_keyboard": True, "is_persistent": True}

    def admin_keyboard(self):
        return self.keyboard_for(next(iter(sorted(self.admins)), None))

    def refresh_menu(self, uid):
        """Обновляет меню команд одного участника после смены роли (в отдельном потоке: это сетевой вызов)."""
        threading.Thread(target=lambda: self._set_menu(uid), daemon=True).start()

    def _set_menu(self, uid):
        guest = [("sites", "Разрешённые сайты"), ("help", "Подсказка"), ("hide", "Скрыть кнопки"), ("menu", "Показать кнопки")]
        admin = [("list", "Список загрузок"), ("pause", "Пауза всех"), ("resume", "Продолжить"),
                 ("retry", "Перезапустить неудачные"), ("file", "Прислать файл: /file 12"),
                 ("sites", "Разрешённые сайты"), ("help", "Подсказка"), ("hide", "Скрыть кнопки"), ("menu", "Показать кнопки")]
        owner = admin + [("members", "Участники"), ("allow", "Сделать гостем: /allow ID"),
                         ("admin", "Сделать администратором: /admin ID"),
                         ("block", "Заблокировать: /block ID"), ("remove", "Удалить: /remove ID")]
        role = self.members.role(uid)
        cmds = owner if uid == self.members.owner else admin if role == "admin" else guest
        try:
            if role in ("admin", "guest"):
                self._call("setMyCommands", {"commands": [{"command": k, "description": tr(v)} for k, v in cmds],
                                             "scope": {"type": "chat", "chat_id": uid}})
            else:
                self._call("deleteMyCommands", {"scope": {"type": "chat", "chat_id": uid}})
        except Exception:
            pass

    def setup_menu(self):
        """Меню команд Telegram (кнопка «Меню» у поля ввода) для каждого участника по его правам."""
        try:
            self._call("deleteMyCommands")
        except Exception:
            pass
        for uid in sorted(self.members.ids("admin", "guest")):
            self._set_menu(uid)   # у кого нет чата с ботом, Telegram вернёт ошибку: меню появится позже

    def send_file_now(self, chat_id, path, caption=""):
        try:
            size = os.path.getsize(path)
        except OSError:
            raise TgError("файл недоступен (удалён или перемещён)", 404)   # без локального пути: он уйдёт в чат
        if size > self.MAX_FILE:
            raise TgError("файл больше 50 МБ", 413)
        body, ct = build_multipart({"chat_id": chat_id, "caption": caption[:1000]}, "document", path)
        self._call("sendDocument", body=body, content_type=ct, timeout=600)

    # ---------- очередь отправки ----------
    def say(self, chat_id, text, markup=None):
        self._out.put(("text", chat_id, text, markup))

    def send_file(self, chat_id, path, caption=""):
        self._out.put(("file", chat_id, path, caption))

    def _out_loop(self):
        while not self._stop.is_set():
            item = self._out.get()
            if item is None:
                break
            try:
                if item[0] == "text":
                    self.send_text(item[1], item[2], item[3] if len(item) > 3 else None)
                else:
                    self.send_file_now(item[1], item[2], item[3])
            except Exception as e:
                self.log(f"[telegram] не удалось отправить: {self._clean(e)}")
                if item[0] == "file":
                    try:
                        self.send_text(item[1], tr(f"Не удалось отправить файл {os.path.basename(item[2])}: {self._clean(e)}"))
                    except Exception:
                        pass

    def _clean(self, e):
        txt = str(e)
        return txt.replace(self.token, "***") if self.token else txt  # токен не должен попадать в журнал

    # ---------- приём ----------
    def handle_update(self, u):
        """Разбирает одно обновление. Возвращает True, если сообщение передано программе."""
        cq = u.get("callback_query")
        if cq:   # нажатие кнопки под сообщением: «file:12» превращаем в обычную команду /file 12 (с теми же проверками прав)
            try:
                self._call("answerCallbackQuery", {"callback_query_id": cq.get("id")})
            except Exception:
                pass
            m = re.fullmatch(r"file:(\d+)", str(cq.get("data") or ""))
            if (not m or not (cq.get("message") or {}).get("chat")
                    or self.members.role((cq.get("from") or {}).get("id")) != "admin"):
                return False   # кнопки есть только у администраторов
            u = {"message": {"chat": cq["message"]["chat"], "from": cq.get("from") or {}, "text": "/file " + m.group(1)}}
        msg = u.get("message") or {}
        chat_id = (msg.get("chat") or {}).get("id")
        user_id = (msg.get("from") or {}).get("id")
        text = msg.get("text") or msg.get("caption") or ""
        if not all(isinstance(x, int) and not isinstance(x, bool) for x in (chat_id, user_id)):
            return False   # битое обновление: id должны быть числами
        text = text if isinstance(text, str) else ""
        text = text[:20000]   # длиннее ссылок и пары десятков строк не бывает: огромный текст не разбираем
        if (msg.get("chat") or {}).get("type", "private") != "private":
            return False   # группы и каналы игнорируем: ответы (список загрузок, файлы) увидели бы все участники чата
        frm = msg.get("from") or {}
        name = " ".join(str(x) for x in (frm.get("first_name"), frm.get("last_name")) if x)[:100]
        username = str(frm.get("username") or "")[:64]
        role = self.members.role(user_id)
        if role == "blocked":
            return False  # заблокированным не отвечаем
        if role in ("admin", "guest"):
            self.members.touch(user_id, name, username, role)
        else:
            if not self.auto_accept and len(self.members.ids("pending")) >= self.MAX_PENDING and not self.members.role(user_id):
                return False   # поток заявок от посторонних: после предела молчим, чтобы не засорять список и не заваливать администраторов
            m, new = self.members.touch(user_id, name, username, "guest" if self.auto_accept else "pending")
            if new:
                who = f"{name or tr('Без имени')}" + (f" (@{username})" if username else "") + f", ID {user_id}"
                for aid in self.members.ids("admin"):
                    self.say(aid, (tr("Новый участник (гость): ") if m["role"] == "guest" else tr("Новая заявка: "))
                             + who + ("" if m["role"] == "guest" else
                                      tr(". Разрешите в программе: Настройки → Участники Telegram.")))
            if m["role"] not in ("admin", "guest"):
                now = time.time()
                if new or now - self._denied.get(chat_id, 0) > 600:  # ждущим отвечаем не чаще раза в 10 минут
                    self._denied[chat_id] = now
                    self.say(chat_id, tr(f"Заявка отправлена владельцу бота. Ваш Telegram ID: {user_id}. Когда вас одобрят, пришлите ссылку."))
                return False
        if not text.strip():
            self.say(chat_id, tr("Пришлите ссылку текстом."))
            return False
        self.on_message(chat_id, text, user_id, self.members.role(user_id) == "admin")
        return True

    def _poll_loop(self):
        try:
            me = self._call("getMe")
            self.username = me.get("username", "")
            self.log(f"[telegram] бот запущен: @{self.username}")
            self._menu_done = True
            self.setup_menu()
        except TgError as e:
            self.log(f"[telegram] не удалось войти ({self._clean(e)}). Проверьте токен и интернет/прокси.")
            if e.code in (401, 404):
                return
        except Exception as e:
            self.log(f"[telegram] нет связи с Telegram: {self._clean(e)}")
        delay = 3
        while not self._stop.is_set():
            try:
                ups = self._call("getUpdates", {"offset": self.offset, "timeout": 25,
                                                "allowed_updates": ["message", "callback_query"]}, timeout=40)
                delay = 3
                self._last_err = ""
                if self._stop.is_set():
                    break   # бот уже заменён новым: не разбираем полученное, оно придёт новому боту (offset не подтверждён)
                if not self._menu_done:   # первый вход не удался (не было сети): меню команд ставим, как только связь появилась
                    self._menu_done = True
                    self.setup_menu()
                for u in ups:
                    self.offset = max(self.offset, u.get("update_id", 0) + 1)
                    try:
                        self.handle_update(u)
                    except Exception as e:
                        self.log(f"[telegram] ошибка обработки: {self._clean(e)}")
            except TgError as e:
                if e.code in (401, 404):
                    self.log("[telegram] токен не подходит — бот остановлен")
                    return
                msg = self._clean(e)
                if msg != self._last_err:
                    self._last_err = msg
                    self.log(f"[telegram] {msg}")
                self._stop.wait(delay)
                delay = min(delay * 2, 60)
            except Exception as e:
                msg = self._clean(e)
                if msg != self._last_err:  # одну и ту же ошибку связи пишем один раз
                    self._last_err = msg
                    self.log(f"[telegram] нет связи: {msg}")
                self._stop.wait(delay)
                delay = min(delay * 2, 60)


class Engine:
    MAX_READERS = 3

    def __init__(self, store, settings, notify=_noop, on_playlist=_noop, log=_noop,
                 on_done=_noop, on_idle=_noop, archive=None):
        self.store, self.settings = store, settings
        self.archive = archive if archive is not None else Archive(ARCHIVE_FILE)
        self.notify, self.on_playlist, self.log = notify, on_playlist, log
        self.on_done, self.on_idle = on_done, on_idle
        self.paused = threading.Event()
        self._global_paused = set()
        self._busy = False
        self._backfilled = False

    # --- вызывается из потока GUI по таймеру ---
    def tick(self):
        tasks = list(self.store.tasks)
        now = time.time()
        for t in tasks:  # автоповтор после ошибки: срок наступил — запускаем заново
            if t.retry_at and now >= t.retry_at and t.state in (INVALID, INCOMPLETE) and not t.removed:
                tries = t.auto_tries
                if self.restart(t):
                    t.auto_tries = tries
                    self.log(f"[автоповтор] {t.display_title}: попытка {tries} из {self.settings.auto_retry}")
                    self.notify(t.id)
            elif t.retry_at and t.state not in (INVALID, INCOMPLETE):
                t.retry_at = 0.0
        reading = sum(1 for t in tasks if t.state == READING)
        downloading = sum(1 for t in tasks if t.state == DOWNLOADING)
        for t in tasks:
            if t.state == QUEUED and reading < self.MAX_READERS:
                t.state, t.status = READING, ""
                reading += 1
                self._busy = True
                self._spawn(self._run_probe, t)
            elif (t.state == WAITING and downloading < self.settings.max_tasks
                  and not t.paused and (t.forced or not self.paused.is_set())):
                t.state, t.status = DOWNLOADING, ""
                t.speed, t.eta = 0, None
                downloading += 1
                self._busy = True
                self._spawn(self._run_download, t)
        if self._busy and not any(t.state in ACTIVE_STATES or (t.retry_at and not t.removed)
                                  for t in self.store.tasks):   # задача ждёт автоповтора — работа ещё не закончена
            self._busy = False
            self.on_idle()

    def _spawn(self, fn, t):
        threading.Thread(target=fn, args=(t,), daemon=True).start()

    # --- управление ---
    def cancel(self, t):
        t.cancel.set()
        p = t.proc
        if p is not None:
            try:
                p.terminate()
            except Exception:
                pass

    def cancel_all(self):
        for t in list(self.store.tasks):
            self.cancel(t)

    def restart(self, t):
        if t.state in (READING, DOWNLOADING):
            return False
        t.force_new = True                  # перезапуск вручную — качаем, даже если есть в архиве
        t.auto_tries, t.retry_at = 0, 0.0   # ручной перезапуск начинает счёт попыток заново
        t.error, t.status, t.progress, t.speed, t.eta = "", "", 0.0, 0, None
        t.paused = t.pause_req = t.forced = False
        t.cancel = threading.Event()
        t.state = WAITING if t.has_info else QUEUED
        return True

    def schedule_retry(self, t, raw_error):
        """После ошибки: если включён автоповтор и ошибка может пройти сама, назначает новую попытку.
        Возвращает текст-пояснение для строки ошибки или пустую строку."""
        n = self.settings.auto_retry
        if (n <= 0 or t.removed or t.cancel.is_set() or t.auto_tries >= n
                or is_permanent_error(raw_error) or is_permanent_error(t.error)):
            t.retry_at = 0.0
            return ""
        t.auto_tries += 1
        wait = retry_delay(t.auto_tries)
        t.retry_at = time.time() + wait
        return f" · повтор {t.auto_tries} из {n} через {wait} с"

    def pause_task(self, t):
        """Пауза: загрузка останавливается, частично скачанное остаётся и докачивается при продолжении."""
        if t.state not in (QUEUED, READING, WAITING, DOWNLOADING):
            return False
        t.paused, t.forced = True, False
        if t.state == DOWNLOADING:
            t.pause_req = True
            self.cancel(t)
        return True

    def resume_task(self, t):
        t.paused = False
        t.forced = self.paused.is_set()  # при общей паузе запускаем эту задачу вручную
        return True

    def set_paused(self, flag):
        """Общая пауза: останавливает идущие загрузки и не даёт стартовать новым."""
        if flag:
            self.paused.set()
            self._global_paused = set()
            for t in list(self.store.tasks):
                t.forced = False
                if t.state == DOWNLOADING:
                    self.pause_task(t)
                    self._global_paused.add(t.id)
        else:
            self.paused.clear()
            mine = getattr(self, "_global_paused", set())
            for t in list(self.store.tasks):
                t.forced = False
                if t.paused and t.id in mine:
                    self.resume_task(t)   # поставленные на паузу вручную остаются на паузе
            self._global_paused = set()

    def _on_cancel(self, t):
        if t.removed:
            return
        if t.pause_req:  # это пауза, а не отмена: вернуть в очередь, но не запускать
            t.pause_req = False
            t.cancel = threading.Event()
            t.state, t.status, t.error = WAITING, "", ""
        else:
            t.state, t.error = INCOMPLETE, "Остановлено"

    # --- рабочие потоки ---
    def _run_probe(self, t):
        st = self.settings
        try:
            try:
                res = with_block_retry(lambda: probe_ytdlp(t, st, self.log), t, self.log)
            except Exception as e:
                if ("unsupported url" in str(e).lower() and st.use_gallerydl
                        and gallerydl_available()):
                    res = None
                else:
                    raise
            if t.removed:
                return
            if t.cancel.is_set():   # «Стоп» нажали, пока шла проверка: загрузку не начинаем
                t.state, t.error = INCOMPLETE, "Остановлено"
                self.notify(t.id)
                return
            if res is None:  # передаём ссылку на gallery-dl
                t.backend = "gallery-dl"
                if not t.title:
                    path = urllib.parse.urlparse(t.url).path.strip("/").split("/")
                    t.title = path[-1] or urllib.parse.urlparse(t.url).hostname or t.url
                t.has_info, t.state = True, WAITING
            elif res["kind"] == "playlist":
                self.on_playlist(t.id, res["title"], res["entries"])
                return
            else:
                t.title = res["title"]
                t.title_fixed = bool(res.get("title_fixed"))
                t.artist, t.vid = res["artist"], res["id"]
                t.duration, t.total = res["duration"], res["size"]
                t.thumb_url = res["thumb"] or t.thumb_url
                t._thumbs = res.get("thumbs") or []
                t.backend = "yt-dlp"
                t.has_info, t.state = True, WAITING
                old = self._archived(t)
                if old:
                    t.files, t.progress, t.state = old, 1.0, DONE
                    t.status = "Уже скачано раньше"
                    t.size = sum(os.path.getsize(f) for f in old if os.path.exists(f))
                    t.fsig = files_sigs(old)
                    self.log(f"[архив] {t.display_title}: уже скачано, пропускаю")
            self.notify(t.id)
            if t.thumb_url and not t.thumb_file:
                t.thumb_file = fetch_thumb_any(getattr(t, "_thumbs", None) or [t.thumb_url],
                                               referer=t.url, proxy=self.settings.proxy)
                if not t.removed:
                    self.notify(t.id)
        except Exception as e:
            if t.removed:
                return
            t.error = friendly_error(e)
            t.state = INVALID
            t.error += self.schedule_retry(t, e)
            self.log(f"[{t.url}] {e}")
            self.notify(t.id)

    def _archived(self, t):
        """Файлы из архива скачанного, если эта загрузка уже была и файлы целы (иначе None)."""
        st = self.settings
        if not st.use_archive or t.force_new or t.fmt_sel or t.backend != "yt-dlp":
            return None
        if not self._backfilled:   # раз за запуск: загрузки из списка, сделанные до появления архива
            self._backfilled = True
            self.archive.backfill(self.store.tasks)
        return self.archive.find(t.url, t.vid, t.mode)

    def _download_yt(self, t, st):
        try:
            download_ytdlp(t, st, self)
        except Exception as e:
            if st.embed_cover and norm_mode(t.mode) == "audio" and is_cover_error(e) and not t.cancel.is_set():
                self.log(f"[обложка] не удалось вшить ({e}); качаю без обложки")
                download_ytdlp(t, dataclasses.replace(st, embed_cover=False), self)
            else:
                raise

    def _run_download(self, t):
        st = self.settings
        try:
            if t.backend == "gallery-dl":
                download_gallerydl(t, st, self)
            else:
                with_block_retry(lambda: self._download_yt(t, st), t, self.log)
            if t.removed:
                return
            t.progress, t.error, t.status, t.state = 1.0, "", "", DONE
            t.auto_tries, t.retry_at = 0, 0.0
            t.paused = t.pause_req = t.forced = False
            try:   # учёт скачанного не должен превращать успешную загрузку в ошибку (файл занят антивирусом, диск полон)
                t.fsig = files_sigs(t.files)
                if t.backend == "yt-dlp" and not t.fmt_sel:
                    self.archive.record(t.url, t.vid, t.mode, t.files, t.title)
            except Exception as ex:
                self.log(f"[архив] не удалось записать: {ex}")
        except Cancelled:
            self._on_cancel(t)
        except Exception as e:
            if t.removed:
                return
            if t.cancel.is_set():  # отмена, обёрнутая внутри yt-dlp в другую ошибку
                self._on_cancel(t)
            else:
                t.error = friendly_error(e)
                t.state = INCOMPLETE if (t.progress > 0 or t.files) else INVALID
                t.error += self.schedule_retry(t, e)
                self.log(f"[{t.url}] {e}")
        finally:
            t.speed, t.eta, t.proc = 0, None, None
            if not t.removed:
                self.notify(t.id)
                if t.state == DONE:
                    self.on_done(t.id)
                    if not t.thumb_file and t.thumb_url:
                        t.thumb_file = fetch_thumb_any(getattr(t, "_thumbs", None) or [t.thumb_url],
                                                       referer=t.url, proxy=self.settings.proxy)
                    if not t.thumb_file:  # сайт не отдал картинку — первая картинка галереи или кадр из видео
                        for fp in t.files:
                            ext = os.path.splitext(fp)[1].lower()
                            if ext in (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp") and os.path.exists(fp):
                                t.thumb_file = fp
                            elif ext in (".mp4", ".mkv", ".webm", ".mov", ".avi", ".flv", ".m4v"):
                                t.thumb_file = thumb_from_video(fp)
                            if t.thumb_file:
                                break
                    self.notify(t.id)

    # --- хук прогресса yt-dlp ---
    def make_hook(self, t):
        parts, last = {}, [0.0]
        probed = [bool(t.total)]   # размер известен из проверки — не трогаем; иначе берём из хода загрузки

        def hook(d):
            if t.cancel.is_set():
                raise Cancelled()
            fn = d.get("filename") or d.get("tmpfilename") or ""
            status = d.get("status")
            if status == "downloading":
                if t.status:  # «Обработка...» от пост-обработки до загрузки (субтитры) не должна закрывать проценты
                    t.status = ""
                parts[fn] = [d.get("downloaded_bytes") or 0,
                             d.get("total_bytes") or d.get("total_bytes_estimate") or 0]
            elif status == "finished":
                n = d.get("total_bytes") or d.get("downloaded_bytes") or 0
                parts[fn] = [n, n]
            got = sum(p[0] for p in parts.values())
            expected = t.total or sum(p[1] for p in parts.values())
            if not probed[0]:
                est = sum(p[1] for p in parts.values())
                if est:
                    t.total = est
            if expected:
                t.progress = min(0.99, got / max(expected, got))
            t.speed = d.get("speed") or 0
            t.eta = d.get("eta")
            now = time.time()
            if now - last[0] > 0.2 or status == "finished":
                last[0] = now
                self.notify(t.id)
        return hook
