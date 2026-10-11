# -*- coding: utf-8 -*-
"""Обновление самой программы: из zip-архива (выбранного вручную или скачанного по адресу).

Заменяются только файлы программы из списка UPDATE_FILES. Перед заменой они проверяются
(есть все обязательные файлы, это корректный Python-код, версия новее текущей — если не
указано иначе), а старые копии кладутся в папку _backup_<версия>. При сбое всё возвращается назад."""
import ast
import io
import os
import re
import shutil
import time
import urllib.request
import zipfile
import zlib

UPDATE_FILES = ("app.pyw", "core.py", "engine.py", "updater.py")
REQUIRED = ("app.pyw", "core.py", "engine.py")
OPTIONAL = ("turtle.ico",)   # не код: копируется как есть, если есть в архиве
MAX_ZIP = 30 * 1024 * 1024
_VER = re.compile(r'^APP_VERSION\s*=\s*["\']([\d.]+)["\']', re.M)


class UpdateError(Exception):
    pass


def parse_version(text):
    m = _VER.search(text or "")
    return m.group(1) if m else ""


def version_key(v):
    return tuple(int(x) for x in re.findall(r"\d+", v or ""))


def read_package(zip_path_or_bytes):
    """Достаёт из архива файлы программы. Возвращает (версия, {имя: байты}). Бросает UpdateError."""
    try:
        src = io.BytesIO(zip_path_or_bytes) if isinstance(zip_path_or_bytes, (bytes, bytearray)) else zip_path_or_bytes
        zf = zipfile.ZipFile(src)
    except (zipfile.BadZipFile, OSError) as e:
        raise UpdateError(f"Это не zip-архив или он повреждён ({e})")
    files = {}
    with zf:
        for info in zf.infolist():
            name = os.path.basename(info.filename)
            parts = info.filename.replace("\\", "/").split("/")
            if any(p.startswith("_backup_") or p == "__MACOSX" for p in parts[:-1]):
                continue   # резервные копии прошлых обновлений и мусор macOS в архиве папки программы
            if info.is_dir() or name not in UPDATE_FILES + OPTIONAL:
                continue
            if info.file_size > MAX_ZIP:
                raise UpdateError(f"Файл «{name}» в архиве слишком большой")
            if name in files:  # два файла с одним именем в разных папках — неоднозначно
                raise UpdateError(f"В архиве несколько файлов «{name}»")
            try:
                files[name] = zf.read(info)
            except (zipfile.BadZipFile, RuntimeError, NotImplementedError, zlib.error, EOFError, OSError) as e:
                raise UpdateError(f"Файл «{name}» в архиве повреждён или защищён паролем ({e})")
    missing = [n for n in REQUIRED if n not in files]
    if missing:
        raise UpdateError("В архиве нет файлов программы: " + ", ".join(missing))
    for name, data in files.items():
        if name in OPTIONAL:
            continue
        try:
            ast.parse(data.decode("utf-8-sig"), filename=name)
        except (SyntaxError, UnicodeDecodeError, ValueError) as e:
            raise UpdateError(f"Файл {name} повреждён или не является кодом программы ({type(e).__name__})")
    ver = parse_version(files["core.py"].decode("utf-8-sig"))
    if not ver:
        raise UpdateError("В архиве не найден номер версии (core.py) — это не Great Turtle")
    return ver, files


def is_newer(new, current):
    return version_key(new) > version_key(current)


def apply_package(files, app_dir, current_version="0"):
    """Записывает файлы в папку программы с резервной копией. Возвращает путь к резервной копии."""
    backup = os.path.join(app_dir, f"_backup_{current_version}_{time.strftime('%Y%m%d-%H%M%S')}")
    os.makedirs(backup, exist_ok=True)
    done = []
    try:
        for name, data in files.items():
            dst = os.path.join(app_dir, name)
            if os.path.exists(dst):
                shutil.copy2(dst, os.path.join(backup, name))
            tmp = dst + ".new"
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, dst)
            done.append(name)
    except OSError as e:
        for name in done:  # откат (ошибка отката не должна скрыть исходную причину)
            b = os.path.join(backup, name)
            try:
                if os.path.exists(b):
                    shutil.copy2(b, os.path.join(app_dir, name))
                else:
                    os.remove(os.path.join(app_dir, name))   # файл появился только с обновлением
            except OSError:
                pass
        for name in files:
            try:
                os.remove(os.path.join(app_dir, name) + ".new")
            except OSError:
                pass
        raise UpdateError(f"Не удалось записать файлы ({e}). Проверьте, что у папки программы есть права записи.")
    return backup


def download(url, proxy="", timeout=60):
    """Скачивает архив по адресу (только https). Возвращает байты."""
    if not url.lower().startswith("https://"):
        raise UpdateError("Адрес обновления должен начинаться с https://")
    class _HttpsOnly(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            if not newurl.lower().startswith("https://"):
                raise UpdateError("Сайт перенаправил запрос на незащищённый адрес — обновление отменено")
            return super().redirect_request(req, fp, code, msg, headers, newurl)
    handlers = [_HttpsOnly()] + ([urllib.request.ProxyHandler({"http": proxy, "https": proxy})] if proxy else [])
    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(url, headers={"User-Agent": "GreatTurtle-updater"})
    try:
        with opener.open(req, timeout=timeout) as r:
            data = r.read(MAX_ZIP + 1)
    except UpdateError:
        raise
    except Exception as e:
        raise UpdateError(f"Не удалось скачать: {e}")
    if len(data) > MAX_ZIP:
        raise UpdateError("Архив слишком большой")
    return data
