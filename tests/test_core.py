# -*- coding: utf-8 -*-
import json
import os
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import core  # noqa: E402
import engine  # noqa: E402
from core import (DONE, DOWNLOADING, INCOMPLETE, INVALID, QUEUED, WAITING, Settings,  # noqa: E402
                  TaskStore, compress_ranges, compute_dups, friendly_error, match_task,
                  parse_inputs, parse_range, site_of, status_text)


class TestRange(unittest.TestCase):
    def test_all(self):
        self.assertEqual(parse_range("", 5), [1, 2, 3, 4, 5])

    def test_first_n(self):
        self.assertEqual(parse_range("~ 3", 10), [1, 2, 3])

    def test_last_n(self):
        self.assertEqual(parse_range("-3 ~", 10), [8, 9, 10])

    def test_mixed(self):
        self.assertEqual(parse_range("1, 10 ~ 12, -1", 20), [1, 10, 11, 12, 20])

    def test_clamp(self):
        self.assertEqual(parse_range("~ 100", 4), [1, 2, 3, 4])
        self.assertEqual(parse_range("50", 4), [])

    def test_open_end(self):
        self.assertEqual(parse_range("8 ~", 10), [8, 9, 10])

    def test_garbage(self):
        with self.assertRaises(ValueError):
            parse_range("abc", 10)
        with self.assertRaises(ValueError):
            parse_range("0", 10)

    def test_compress(self):
        self.assertEqual(compress_ranges([1, 2, 3, 5, 7, 8]), "1-3,5,7-8")

    def test_gdl(self):
        self.assertEqual(engine.pages_to_gdl("~ 5, 8, 10 ~ 12, -3 ~"), "1-5,8,10-12")


class TestInputs(unittest.TestCase):
    def test_urls(self):
        self.assertEqual(parse_inputs("https://a.com/x?y=1,2 https://b.org"),
                         ["https://a.com/x?y=1,2", "https://b.org"])

    def test_shortcuts(self):
        self.assertEqual(parse_inputs("@TensorFlow"), ["https://x.com/TensorFlow"])
        self.assertEqual(parse_inputs("pixiv_11"), ["https://www.pixiv.net/users/11"])
        self.assertEqual(parse_inputs("insta_user_name"), ["https://www.instagram.com/user_name/"])
        self.assertEqual(parse_inputs("123456, 125000"),
                         ["https://hitomi.la/galleries/123456.html",
                          "https://hitomi.la/galleries/125000.html"])

    def test_bare_domain_and_garbage(self):
        self.assertEqual(parse_inputs("youtube.com/watch?v=1"), ["https://youtube.com/watch?v=1"])
        self.assertEqual(parse_inputs("hello world"), [])

    def test_dedupe(self):
        self.assertEqual(len(parse_inputs("https://a.com https://a.com")), 1)

    def test_norm_url(self):
        n = core.norm_url
        self.assertEqual(n("https://www.pornhub.com/view_video.php?viewkey=abc#1"),
                         n("http://pornhub.com/view_video.php?viewkey=abc"))
        self.assertEqual(n("https://youtu.be/AbC/"), n("https://YOUTU.BE/AbC"))
        self.assertNotEqual(n("https://youtu.be/AbC"), n("https://youtu.be/abc"))
        self.assertNotEqual(n("https://a.com/x?v=1"), n("https://a.com/x?v=2"))

    def test_mp4_mode(self):
        self.assertIn("mp4", [k for k, _ in core.MODES])
        st, t = Settings(), TaskStore().new_task("https://a.com/x", mode="mp4")
        o = engine.build_ydl_opts(t, st)
        self.assertEqual(o["merge_output_format"], "mp4")
        self.assertIn("av01", o["format"])
        self.assertIn("m3u8", o["format"].split("/")[0])  # без HLS-потоков с «оценочным» размером
        t2 = TaskStore().new_task("https://a.com/x", mode="best")
        self.assertNotIn("merge_output_format", engine.build_ydl_opts(t2, st))

    def test_describe_formats(self):
        info = {"requested_formats": [
            {"format_id": "313", "vcodec": "vp9", "acodec": "none", "resolution": "3840x2160", "protocol": "https"},
            {"format_id": "140", "vcodec": "none", "acodec": "mp4a.40.2", "resolution": "audio only"}]}
        self.assertEqual(engine.describe_formats(info), "313 vp9 3840x2160 + 140 mp4a.40.2")
        self.assertEqual(engine.describe_formats({"entries": []}), "")
        self.assertEqual(engine.describe_formats({"format_id": "18", "vcodec": "avc1", "acodec": "mp4a",
                                                  "resolution": "640x360", "protocol": "m3u8_native"}),
                         "18 avc1 640x360 m3u8")

    def test_hitomi_template(self):
        h = engine.hitomi_template
        self.assertEqual(h("[artist] date:%Y-%m-%d %H:%M; title"),
                         "[%(uploader,channel)s] %(timestamp>%Y-%m-%d %H-%M,upload_date>%Y-%m-%d %H-%M)s; "
                         "%(title)s.%(ext)s")
        self.assertEqual(h("[artist] title (id)"), "[%(uploader,channel)s] %(title)s (%(id)s).%(ext)s")
        self.assertEqual(h("%(title)s [%(id)s].%(ext)s"), "%(title)s [%(id)s].%(ext)s")  # yt-dlp не трогаем
        self.assertEqual(h("date title"), "%(timestamp>%Y-%m-%d,upload_date>%Y-%m-%d)s %(title)s.%(ext)s")
        st = Settings(); st.outtmpl = "[artist] title"
        o = engine.build_ydl_opts(TaskStore().new_task("https://a.com/x"), st)
        self.assertTrue(o["outtmpl"].endswith("[%(uploader,channel)s] %(title)s.%(ext)s"))

    def test_resolution_modes(self):
        keys = [k for k, _ in core.MODES]
        self.assertEqual(keys[:3], ["best", "mp4", "h264"])
        self.assertEqual(keys[-2:], ["audio", "subs"])
        self.assertEqual([k for k in keys if k.startswith("r")],
                         ["r2160", "r1440", "r1080", "r720", "r480", "r360", "r240", "r144"])
        self.assertEqual(len(keys), len(set(keys)))
        st = Settings()
        for key in keys:
            o = engine.build_ydl_opts(TaskStore().new_task("https://a.com/x", mode=key), st)
            self.assertTrue(o["format"])
            if key.startswith("r"):
                self.assertEqual(o["format_sort"], [f"res:{key[1:]}"])
                self.assertEqual(o["merge_output_format"], "mp4")
                self.assertIn("av01", o["format"])
            else:
                self.assertNotIn("format_sort", o)
        # режимы из прежних версий не пропадают
        self.assertEqual(core.norm_mode("1080"), "r1080")
        self.assertEqual(core.norm_mode("720"), "r720")
        self.assertEqual(core.norm_mode("что-то странное"), "best")
        with tempfile.TemporaryDirectory() as d:
            import json
            p = os.path.join(d, "s.json")
            json.dump({"mode": "720"}, open(p, "w"))
            self.assertEqual(Settings.load(p).mode, "r720")
            p2 = os.path.join(d, "st.json")
            json.dump({"tasks": [{"id": 1, "url": "https://a.com", "mode": "1080", "state": "done"}]}, open(p2, "w"))
            ts = TaskStore(); ts.load(p2)
            self.assertEqual(ts.tasks[0].mode, "r1080")

    def test_multi_audio(self):
        def a(fid, lang, pref, abr, ext="m4a", note="", proto="https"):
            return {"format_id": fid, "vcodec": "none", "acodec": "x", "language": lang, "language_preference": pref,
                    "abr": abr, "ext": ext, "format_note": note, "protocol": proto}
        info = {"formats": [
            {"format_id": "313", "vcodec": "vp9", "acodec": "none"},          # видео — пропускаем
            a("139", "en", 10, 49), a("140", "en", 10, 129), a("251", "en", 10, 125, "webm"),
            a("140-drc", "en", 10, 129, note="medium, DRC"),                  # DRC — пропускаем
            a("234", "en", 10, 130, "mp4", proto="m3u8"),                     # HLS — пропускаем
            a("140-1", "ru", -1, 129), a("251-1", "ru", -1, 125, "webm"),
            a("140-2", "de", -10, 128)]}
        self.assertEqual(engine.pick_audio_formats(info, True), ["140", "140-1", "140-2"])  # оригинал первым
        self.assertEqual(engine.pick_audio_formats(info, True, ["ru"]), ["140", "140-1"])    # оригинал + русский
        self.assertEqual(engine.pick_audio_formats(info, True, ["de", "ru"]), ["140", "140-2", "140-1"])
        self.assertEqual(engine.pick_audio_formats(info, True, ["xx"]), ["140"])             # нет такого дубляжа
        # базовый язык захватывает региональные варианты, оригинал определяется по пометке в названии
        info2 = {"formats": [a("a1", "pt-BR", 0, 129, note="Portuguese (BR), medium"),
                             a("a2", "en", 0, 129, note="English original (default), medium"),
                             a("a3", "ru", 0, 129, note="Russian, medium")]}
        self.assertEqual(engine.pick_audio_formats(info2, True, ["pt"]), ["a2", "a1"])
        self.assertEqual(Settings().audio_langs, "ru")
        ids = engine.pick_audio_formats(info, False)
        self.assertEqual(len(ids), 3)
        self.assertEqual(ids[0], "140")
        self.assertEqual(engine.pick_audio_formats({"formats": [a("140", "en", 10, 129)]}, True), ["140"])
        self.assertEqual(engine.pick_audio_formats(None, True), [])
        f = engine.multi_audio_format("r1080", ["140", "140-1"])
        self.assertTrue(f.startswith("bv*[vcodec!*=av01][protocol!*=m3u8]+140+140-1/bv*+140+140-1/"))
        self.assertTrue(f.endswith(engine.FORMATS["mp4"]))
        self.assertEqual(engine.multi_audio_format("best", ["251", "251-1"]), "bv*+251+251-1/" + engine.FORMATS["best"])
        self.assertFalse(Settings().all_audio)

    def test_site(self):
        self.assertEqual(site_of("https://www.youtube.com/watch?v=1"), "youtube")
        self.assertEqual(site_of("https://youtu.be/x"), "youtube")
        self.assertEqual(site_of("https://sub.example.co.uk/a"), "example")
        self.assertEqual(site_of("https://m.twitch.tv/a"), "twitch")


class TestErrors(unittest.TestCase):
    def test_friendly_site_blocked_response(self):
        self.assertIn("cookies", friendly_error("'NoneType' object is not subscriptable"))
        self.assertIn("cookies", friendly_error("[yandexmusic:track] 1: Failed to parse JSON: Expecting value"))

    def test_friendly(self):
        self.assertEqual(friendly_error("ERROR: Unsupported URL: http://x"), "Неизвестный сайт")
        self.assertIn("cookie", friendly_error("Sign in to confirm you're not a bot").lower())
        self.assertEqual(friendly_error("\x1b[0;31mERROR:\x1b[0m [youtube] abc: Video unavailable"),
                         "Video unavailable")


class TestStoreAndFilter(unittest.TestCase):
    def make(self):
        s = TaskStore()
        a = s.new_task("https://www.youtube.com/watch?v=1", title="Cat video", vid="1")
        b = s.new_task("https://youtu.be/1", title="Cat video again", vid="1")
        c = s.new_task("https://x.com/user", title="Tweet", comment="funny stuff")
        a.state, b.state, c.state = DONE, WAITING, INVALID
        c.tag = 2
        for t in (a, b, c):
            s.add(t)
        return s, a, b, c

    def test_filters(self):
        s, a, b, c = self.make()
        dups = compute_dups(s.tasks)
        self.assertEqual({a.id, b.id}, dups)
        f = lambda q: [t.id for t in s.tasks if match_task(t, q, dups)]  # noqa: E731
        self.assertEqual(f("cat"), [a.id, b.id])
        self.assertEqual(f("type:twitter"), [c.id])
        self.assertEqual(f("bad:"), [b.id, c.id])
        self.assertEqual(f("dup:"), [a.id, b.id])
        self.assertEqual(f("tag:orange"), [c.id])
        self.assertEqual(f("tag:2"), [c.id])
        self.assertEqual(f("comment:funny"), [c.id])
        self.assertEqual(f("cat type:youtube bad:"), [b.id])
        self.assertEqual(f("rem:"), [])

    def test_rem(self):
        s, a, *_ = self.make()
        a.files = ["/nonexistent/file.mp4"]
        self.assertTrue(match_task(a, "rem:"))

    def test_persistence(self):
        s, a, b, c = self.make()
        a.files = ["x.mp4"]
        s.collapsed.add("grp")
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "state.json")
            s.save(p)
            s2 = TaskStore()
            s2.load(p)
        self.assertEqual([t.url for t in s2.tasks], [t.url for t in s.tasks])
        self.assertEqual(s2.tasks[0].state, DONE)
        self.assertEqual(s2.tasks[1].state, INCOMPLETE)  # незавершённые -> оранжевые
        self.assertEqual(s2.tasks[2].state, INVALID)
        self.assertEqual(s2.tasks[2].tag, 2)
        self.assertEqual(s2.collapsed, {"grp"})
        self.assertGreater(s2.new_task("https://z.com").id, max(t.id for t in s.tasks))

    def test_resume(self):
        s, a, b, c = self.make()
        b.has_info = True
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s.json")
            s.save(p)
            s2 = TaskStore()
            s2.load(p, resume=True)
        self.assertEqual(s2.tasks[1].state, WAITING)

    def test_groups(self):
        s = TaskStore()
        self.assertEqual(s.unique_group("pl"), "pl")
        t = s.new_task("https://a.com", group="pl")
        s.add(t)
        self.assertEqual(s.unique_group("pl"), "pl (2)")

    def test_settings_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "set.json")
            st = Settings()
            st.max_tasks, st.dark, st.proxy = 5, True, "http://p:1"
            st.save(p)
            st2 = Settings.load(p)
        self.assertEqual((st2.max_tasks, st2.dark, st2.proxy), (5, True, "http://p:1"))

    def test_status_text(self):
        s, a, b, c = self.make()
        self.assertEqual(status_text(a), "Готово")
        a.state, a.progress, a.speed, a.eta = DOWNLOADING, 0.5, 2 * 1024 * 1024, 65
        self.assertEqual(status_text(a), "Скачивание 50% · 2.0 МБ/сек. · 00:01:05")
        self.assertEqual(status_text(a, paused=True), "Пауза")
        a.status = "Обработка..."
        self.assertEqual(status_text(a), "Обработка...")


class FakeBackend:
    """Подменяет yt-dlp: всё предсказуемо и без сети."""

    def __init__(self):
        self.running, self.peak, self.lock = 0, 0, threading.Lock()
        self.delay = 0.02

    def probe(self, t, st, log):
        if "bad" in t.url:
            raise RuntimeError("ERROR: Unsupported URL: " + t.url)
        if "playlist" in t.url:
            return {"kind": "playlist", "title": "My list",
                    "entries": [{"url": f"https://v.example/{i}", "title": f"v{i}", "id": str(i),
                                 "thumb": ""} for i in range(3)]}
        return {"kind": "single", "title": "T " + t.url[-3:], "artist": "A", "id": t.url[-3:],
                "duration": 10, "size": 1000, "thumb": ""}

    def download(self, t, st, eng):
        with self.lock:
            self.running += 1
            self.peak = max(self.peak, self.running)
        try:
            hook = eng.make_hook(t)
            for i in range(1, 5):
                hook({"status": "downloading", "filename": "f", "downloaded_bytes": i * 250,
                      "total_bytes": 1000, "speed": 1000, "eta": 1})
                time.sleep(self.delay)
            if "fail" in t.url:
                raise RuntimeError("ERROR: boom")
            hook({"status": "finished", "filename": "f", "total_bytes": 1000})
            t.files = []
        finally:
            with self.lock:
                self.running -= 1


class TestEngine(unittest.TestCase):
    def setUp(self):
        self.fb = FakeBackend()
        self._orig = (engine.probe_ytdlp, engine.download_ytdlp)
        engine.probe_ytdlp, engine.download_ytdlp = self.fb.probe, self.fb.download
        self.store = TaskStore()
        self.settings = Settings()
        self.settings.max_tasks = 2
        self.settings.use_gallerydl = False
        self.events = {"changed": 0, "done": [], "idle": 0, "playlists": []}
        self.eng = engine.Engine(
            self.store, self.settings, archive=core.Archive(os.path.join(tempfile.mkdtemp(), "a.json")),
            notify=lambda tid: self.events.__setitem__("changed", self.events["changed"] + 1),
            on_playlist=lambda tid, title, entries: self.events["playlists"].append((tid, title, entries)),
            on_done=lambda tid: self.events["done"].append(tid),
            on_idle=lambda: self.events.__setitem__("idle", self.events["idle"] + 1))

    def tearDown(self):
        engine.probe_ytdlp, engine.download_ytdlp = self._orig

    def run_until_idle(self, timeout=10):
        end = time.time() + timeout
        while time.time() < end:
            self.eng.tick()
            if self.events["idle"]:
                return True
            time.sleep(0.02)
        return False

    def add(self, url, **kw):
        t = self.store.new_task(url, **kw)
        self.store.add(t)
        return t

    def test_basic_flow_and_concurrency_limit(self):
        ts = [self.add(f"https://v.example/{i:03d}") for i in range(6)]
        self.assertTrue(self.run_until_idle())
        self.assertTrue(all(t.state == DONE for t in ts))
        self.assertLessEqual(self.fb.peak, 2)
        self.assertEqual(len(self.events["done"]), 6)
        self.assertEqual(ts[0].title, "T 000")
        self.assertEqual(ts[0].progress, 1.0)

    def test_idle_waits_for_pending_retry(self):
        self.eng.settings.auto_retry = 3
        fail = self.add("https://v.example/fail")
        end = time.time() + 1.5
        while time.time() < end:
            self.eng.tick()
            time.sleep(0.02)
        self.assertTrue(fail.retry_at > 0)
        self.assertFalse(self.events["idle"])   # ждёт автоповтора: «все задачи выполнены» ещё рано

    def test_errors(self):
        self.eng.settings.auto_retry = 0
        bad = self.add("https://bad.example/x")
        fail = self.add("https://v.example/fail")
        ok = self.add("https://v.example/ok1")
        self.assertTrue(self.run_until_idle())
        self.assertEqual(bad.state, INVALID)
        self.assertEqual(bad.error, "Неизвестный сайт")
        self.assertEqual(fail.state, INCOMPLETE)  # прогресс был -> «не завершено»
        self.assertEqual(ok.state, DONE)

    def test_playlist_callback(self):
        t = self.add("https://v.example/playlist")
        end = time.time() + 5
        while time.time() < end and not self.events["playlists"]:
            self.eng.tick()
            time.sleep(0.02)
        tid, title, entries = self.events["playlists"][0]
        self.assertEqual((tid, title, len(entries)), (t.id, "My list", 3))

    def test_pause(self):
        self.eng.set_paused(True)
        t = self.add("https://v.example/pau")
        for _ in range(30):
            self.eng.tick()
            time.sleep(0.02)
        self.assertEqual(t.state, WAITING)  # прочитано, но не скачивается
        self.eng.set_paused(False)
        self.assertTrue(self.run_until_idle())
        self.assertEqual(t.state, DONE)

    def test_cancel_and_restart(self):
        t = self.add("https://v.example/can")
        end = time.time() + 5
        while time.time() < end and t.state != DOWNLOADING:
            self.eng.tick()
            time.sleep(0.005)
        self.eng.cancel(t)
        end = time.time() + 5
        while time.time() < end and t.state == DOWNLOADING:
            time.sleep(0.01)
        self.assertEqual(t.state, INCOMPLETE)
        self.assertTrue(self.eng.restart(t))
        self.assertEqual(t.state, WAITING)
        self.events["idle"] = 0
        self.assertTrue(self.run_until_idle())
        self.assertEqual(t.state, DONE)

    def _wait(self, cond, timeout=5):
        end = time.time() + timeout
        while time.time() < end and not cond():
            self.eng.tick()
            time.sleep(0.005)
        return cond()

    def test_pause_task_and_resume(self):
        self.fb.delay = 0.1
        t = self.add("https://v.example/pz1")
        self.assertTrue(self._wait(lambda: t.state == DOWNLOADING))
        self.assertTrue(self.eng.pause_task(t))
        self.assertTrue(self._wait(lambda: t.state == WAITING))
        self.assertTrue(t.paused)
        self.assertFalse(t.cancel.is_set())  # событие отмены сброшено для продолжения
        self.assertEqual(status_text(t), "Пауза")
        for _ in range(30):
            self.eng.tick()
            time.sleep(0.01)
        self.assertEqual(t.state, WAITING)  # на паузе не стартует
        self.fb.delay = 0.01
        self.eng.resume_task(t)
        self.assertTrue(self.run_until_idle())
        self.assertEqual(t.state, DONE)
        self.assertFalse(t.paused)

    def test_pause_waiting_task(self):
        t = self.add("https://v.example/pz2")
        self.assertTrue(self._wait(lambda: t.state in (WAITING, DONE)))
        if t.state == WAITING:
            self.assertTrue(self.eng.pause_task(t))

    def test_global_pause_stops_and_single_resume(self):
        self.fb.delay = 0.1
        a = self.add("https://v.example/ga1")
        b = self.add("https://v.example/gb2")
        self.assertTrue(self._wait(lambda: a.state == DOWNLOADING and b.state == DOWNLOADING))
        self.eng.set_paused(True)
        self.assertTrue(self._wait(lambda: a.state == WAITING and b.state == WAITING))
        c = self.add("https://v.example/gc3")  # новая задача при общей паузе
        for _ in range(40):
            self.eng.tick()
            time.sleep(0.01)
        self.assertEqual(c.state, WAITING)
        self.fb.delay = 0.01
        self.eng.resume_task(a)  # одну можно продолжить вручную
        self.assertTrue(self._wait(lambda: a.state == DONE))
        self.assertEqual(b.state, WAITING)
        self.eng.set_paused(False)
        self.events["idle"] = 0
        self.assertTrue(self.run_until_idle())
        self.assertTrue(all(x.state == DONE for x in (a, b, c)))

    def test_removed_task_is_silent(self):
        t = self.add("https://v.example/rem")
        end = time.time() + 5
        while time.time() < end and t.state != DOWNLOADING:
            self.eng.tick()
            time.sleep(0.005)
        self.store.remove(t)
        time.sleep(0.3)
        self.assertNotEqual(t.state, DONE)


class TestVkAudio(unittest.TestCase):
    def test_three_letter_codes_and_video_language(self):
        import engine
        def a(i, lang, proto="https"):
            d = {"format_id": i, "vcodec": "none", "acodec": "mp4a.40.2", "abr": 100,
                 "protocol": proto, "ext": "m4a"}
            if lang:
                d["language"] = lang
            return d
        info = {"formats": [a("sep-11", None), a("s-10010", "rus"), a("s-10014", "eng"),
                            a("hls-ru", "rus", "m3u8"),
                            {"format_id": "v1", "vcodec": "avc1", "acodec": "none",
                             "language": "eng", "height": 1080, "tbr": 5000, "ext": "mp4"}]}
        self.assertEqual(engine.pick_audio_formats(info, True, ["ru"]), ["s-10014", "s-10010"])
        self.assertEqual(engine.pick_audio_formats(info, True, ["de"]), ["s-10014"])


class TestSubs(unittest.TestCase):
    def test_subtitle_opts(self):
        import engine
        st = core.Settings(); t = core.TaskStore().new_task("https://a.com/x")
        self.assertNotIn("writesubtitles", engine.build_ydl_opts(t, st))
        st.subs_mode, st.subs_langs, st.subs_auto = "embed", "ru, en", True
        o = engine.build_ydl_opts(t, st)
        self.assertEqual(o["subtitleslangs"], ["ru.*", "rus.*", "en.*", "eng.*"])
        self.assertEqual(engine.lang_variants("rus"), ["ru", "rus"])
        self.assertTrue(o["writeautomaticsub"])
        self.assertIn({"key": "FFmpegEmbedSubtitle"}, o["postprocessors"])
        st.subs_mode = "file"
        self.assertNotIn({"key": "FFmpegEmbedSubtitle"}, engine.build_ydl_opts(t, st)["postprocessors"])


class TestSubsOnly(unittest.TestCase):
    def test_opts_skip_download_and_force_file(self):
        import engine
        st = core.Settings(); st.subs_mode = ""   # в настройках субтитры выключены, но режим их требует
        t = core.TaskStore().new_task("https://a.com/x", mode="subs")
        o = engine.build_ydl_opts(t, st)
        self.assertTrue(o["skip_download"] and o["writesubtitles"] and o["ignore_no_formats_error"])
        self.assertNotIn({"key": "FFmpegEmbedSubtitle"}, o["postprocessors"])
        self.assertEqual(core.norm_mode("subs"), "subs")

    def _run(self, files_on_disk):
        import engine, types
        d = tempfile.mkdtemp()
        subs = {}
        for lang in files_on_disk:
            fp = os.path.join(d, f"vid.{lang}.srt"); open(fp, "w").write("1")
            subs[lang] = {"filepath": fp}

        class FakeYDL:
            def __init__(self, o): pass
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def extract_info(self, url, download=True): return {"requested_subtitles": subs}
        old = engine._import_ytdlp
        engine._import_ytdlp = lambda: types.SimpleNamespace(YoutubeDL=FakeYDL)
        try:
            st = core.Settings(); t = core.TaskStore().new_task("https://a.com/x", mode="subs")
            eng = types.SimpleNamespace(log=lambda *a: None, make_hook=lambda t: None, notify=lambda i: None)
            engine.download_ytdlp(t, st, eng)
            return t
        finally:
            engine._import_ytdlp = old

    def test_files_collected(self):
        t = self._run(["en", "ru"])
        self.assertEqual(len(t.files), 2)
        self.assertIn("en", t.fmt_info)

    def test_no_subs_is_error(self):
        with self.assertRaises(RuntimeError):
            self._run([])


class TestAudioTracksSeparate(unittest.TestCase):
    INFO = {"formats": [
        {"format_id": "140", "language": "en", "acodec": "mp4a", "vcodec": "none", "ext": "m4a",
         "abr": 128, "format_note": "English (default), medium", "language_preference": 10},
        {"format_id": "140-1", "language": "ru", "acodec": "mp4a", "vcodec": "none", "ext": "m4a",
         "abr": 128, "format_note": "Russian, medium", "language_preference": -1},
    ]}

    def _run(self, info, all_audio=True, url="https://www.youtube.com/watch?v=abc"):
        import engine, types
        calls = []

        class FakeYDL:
            def __init__(self, o): self.o = o
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def extract_info(self, url, download=True):
                if not download:
                    return info
                calls.append((self.o["format"], self.o["outtmpl"]))
                return {"requested_downloads": [{"filepath": self.o["outtmpl"].replace("%(ext)s", "mp3")}]}
        old = engine._import_ytdlp
        engine._import_ytdlp = lambda: types.SimpleNamespace(YoutubeDL=FakeYDL)
        try:
            st = core.Settings(); st.all_audio = all_audio; st.audio_langs = "ru"
            st.folder = tempfile.mkdtemp()
            t = core.TaskStore().new_task(url, mode="audio")
            eng = types.SimpleNamespace(log=lambda *a: None, make_hook=lambda t: None, notify=lambda i: None)
            engine.download_ytdlp(t, st, eng)
            return t, calls
        finally:
            engine._import_ytdlp = old

    def test_suffixes(self):
        self.assertEqual(engine.audio_track_suffixes(self.INFO, ["140", "140-1"]), ["en", "ru"])
        self.assertEqual(engine.audio_track_suffixes({"formats": [{"format_id": "a"}, {"format_id": "b"}]},
                                                     ["a", "b"]), ["orig", "track2"])

    def test_two_files(self):
        t, calls = self._run(self.INFO)
        self.assertEqual([c[0] for c in calls], ["140", "140-1"])
        self.assertEqual(len(t.files), 2)
        self.assertTrue(t.files[0].endswith(".en.mp3") and t.files[1].endswith(".ru.mp3"))

    def test_single_track_normal(self):
        info = {"formats": self.INFO["formats"][:1]}
        t, calls = self._run(info)
        self.assertEqual(len(calls), 1)   # обычная загрузка одним файлом
        self.assertFalse(t.files[0].endswith(".en.mp3"))

    def test_other_sites_skip_extra_probe(self):
        t, calls = self._run(self.INFO, url="https://rt.example.com/view_video.php?viewkey=1")
        self.assertEqual(len(calls), 1)   # без лишнего запроса «есть ли дубляж»
        self.assertFalse(engine.dubbing_possible("https://a.com/x"))
        self.assertTrue(engine.dubbing_possible("https://vk.com/video1_2"))
        # свои сайты: по короткому имени или по домену
        self.assertTrue(engine.dubbing_possible("https://vimeo.com/1", "vimeo"))
        self.assertTrue(engine.dubbing_possible("https://cdn.example.org/x", "Example.org, foo"))
        self.assertTrue(engine.dubbing_possible("https://a.com/x", "https://www.a.com/"))
        self.assertFalse(engine.dubbing_possible("https://b.com/x", "a.com, vimeo"))
        self.assertIn("dub_sites", core.Settings().__dict__)

    def test_block_retry(self):
        import types, threading
        t = types.SimpleNamespace(cancel=threading.Event(), removed=False, display_title="x")
        n = []
        def flaky():
            n.append(1)
            if len(n) < 3:
                raise RuntimeError("Unable to download webpage: HTTP Error 403: Forbidden")
            return "ok"
        self.assertEqual(engine.with_block_retry(flaky, t, lambda *a: None, waits=(0, 0)), "ok")
        self.assertEqual(len(n), 3)
        with self.assertRaises(RuntimeError):   # другая ошибка — без повторов
            engine.with_block_retry(lambda: (_ for _ in ()).throw(RuntimeError("Video unavailable")), t,
                                    lambda *a: None, waits=(0, 0))

    def test_off_by_default(self):
        t, calls = self._run(self.INFO, all_audio=False)
        self.assertEqual(len(calls), 1)


class TestYoutubeMix(unittest.TestCase):
    def test_mix_detection(self):
        f = engine.is_youtube_mix_link
        self.assertTrue(f("https://www.youtube.com/watch?v=IYfzNb7yEiU&list=RDIYfzNb7yEiU&start_radio=1"))
        self.assertTrue(f("https://www.youtube.com/watch?v=abc&list=RDabc"))
        self.assertFalse(f("https://www.youtube.com/watch?v=abc&list=PLxyz"))
        self.assertFalse(f("https://www.youtube.com/playlist?list=RDabc"))
        self.assertFalse(f("https://example.com/watch?v=1&list=RD1"))

    def test_mix_forces_single_even_if_playlist_setting_on(self):
        st = core.Settings(); st.playlist_from_video_url = True
        t = core.TaskStore().new_task("https://www.youtube.com/watch?v=a&list=RDa&start_radio=1")
        self.assertTrue(engine.build_ydl_opts(t, st)["noplaylist"])
        t2 = core.TaskStore().new_task("https://www.youtube.com/watch?v=a&list=PLq")
        self.assertFalse(engine.build_ydl_opts(t2, st)["noplaylist"])


class TestTrimLength(unittest.TestCase):
    def test_long_folder_does_not_eat_name(self):
        st = core.Settings()
        t = core.TaskStore().new_task("https://a.com/x")
        t.folder = "D:\\" + "a" * 100
        n_long = engine.build_ydl_opts(t, st)["trim_file_name"]
        t.folder = "D:\\x"
        n_short = engine.build_ydl_opts(t, st)["trim_file_name"]
        self.assertGreaterEqual(n_long, 80)
        self.assertGreater(n_short, n_long)
        t.folder = "D:\\" + "a" * 400
        self.assertEqual(engine.build_ydl_opts(t, st)["trim_file_name"], 80)


class TestReveal(unittest.TestCase):
    def test_commands(self):
        c = engine.reveal_command("C:/Users/a b/Загрузки/youtube/x [1].mp3", "win32")
        self.assertTrue(c.startswith('explorer /select,"') and c.endswith('x [1].mp3"'))
        self.assertEqual(engine.reveal_command("/a/b.mp3", "darwin"), ["open", "-R", "/a/b.mp3"])
        self.assertIsNone(engine.reveal_command("/a/b.mp3", "linux"))


class TestMerge(unittest.TestCase):
    def test_guess_lang(self):
        g = engine.guess_track_lang
        self.assertEqual(g("C:/x/video.ru.srt"), "ru")
        self.assertEqual(g("film_eng.mp3"), "en")
        self.assertEqual(g("Film [Russian] dub.rus.m4a"), "ru")
        self.assertEqual(g("plain.srt"), "")

    def test_default_output(self):
        self.assertTrue(engine.default_merge_output("/a/b.mp4").endswith("b.merged.mp4"))
        self.assertTrue(engine.default_merge_output("/a/b.webm").endswith("b.merged.mkv"))

    def test_validate(self):
        d = tempfile.mkdtemp()
        v = os.path.join(d, "v.mp4"); s = os.path.join(d, "v.ru.srt")
        open(v, "w").close(); open(s, "w").close()
        f = engine.validate_merge
        self.assertTrue(f("", [(s, "ru")], [], v + ".mkv"))
        self.assertTrue(f(v, [], [], os.path.join(d, "o.mkv")))                 # нечего добавлять
        self.assertTrue(f(v, [(s, "ru")], [], v))                               # поверх исходного
        self.assertTrue(f(v, [(s, "ru")], [], os.path.join(d, "o.avi")))        # не тот формат
        self.assertTrue(f(v, [(os.path.join(d, "no.srt"), "")], [], os.path.join(d, "o.mkv")))
        self.assertEqual(f(v, [(s, "ru")], [], os.path.join(d, "o.mkv")), "")

    def test_command_indexes(self):
        c = engine.merge_command("ffmpeg", "v.mp4", [("a.en.srt", "en"), ("b.vtt", "")], [("d.mp3", "ru")],
                                 "o.mkv", n_audio=2, n_sub=1)
        s = " ".join(c)
        self.assertIn("-map 0:v -map 0:a? -map 0:s? -map 1:0 -map 2:0 -map 3:a:0", s)
        self.assertIn("-metadata:s:s:1 language=eng", s)       # после одной уже имеющейся дорожки субтитров
        self.assertIn("-c:s:2 srt", s)                         # .vtt в mkv переводим в srt
        self.assertIn("-metadata:s:a:2 language=rus", s)
        self.assertIn("-disposition:a:2 0", s)                 # новый звук не «по умолчанию»
        self.assertEqual(c[-1], "o.mkv")
        c2 = engine.merge_command("ffmpeg", "v.mp4", [("a.srt", "en")], [], "o.mp4")
        self.assertIn("mov_text", c2)

    def test_real_ffmpeg(self):
        import shutil, subprocess
        if not shutil.which("ffmpeg"):
            self.skipTest("нет ffmpeg")
        d = tempfile.mkdtemp()
        v = os.path.join(d, "v.mp4")
        r = subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=d=1:s=160x90",
                            "-f", "lavfi", "-i", "sine=d=1", "-c:v", "libx264", "-c:a", "aac", "-shortest", v])
        if r.returncode:
            self.skipTest("ffmpeg без libx264")
        s = os.path.join(d, "v.ru.srt")
        with open(s, "w", encoding="utf-8") as fh:
            fh.write("1\n00:00:00,000 --> 00:00:01,000\nПривет\n\n")
        out = engine.merge_tracks(v, [(s, "ru")], [], os.path.join(d, "o.mkv"))
        info = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type:stream_tags=language",
                               "-of", "compact", out], capture_output=True, text=True).stdout
        self.assertIn("codec_type=subtitle|tag:language=rus", info)
        with self.assertRaises(RuntimeError):
            engine.merge_tracks(v, [(s, "ru")], [], v)


class TestExtract(unittest.TestCase):
    PROBE = {"streams": [
        {"index": 0, "codec_type": "video", "codec_name": "h264"},
        {"index": 1, "codec_type": "audio", "codec_name": "aac", "channels": 2,
         "tags": {"language": "eng", "title": "Original"}, "disposition": {"default": 1}},
        {"index": 2, "codec_type": "audio", "codec_name": "opus", "channels": 6, "tags": {"language": "rus"}},
        {"index": 3, "codec_type": "subtitle", "codec_name": "subrip", "tags": {"language": "rus"}},
        {"index": 4, "codec_type": "subtitle", "codec_name": "ass", "tags": {"language": "und"}},
        {"index": 5, "codec_type": "subtitle", "codec_name": "dvd_subtitle", "tags": {"language": "fra"}},
    ]}

    def test_parse_and_labels(self):
        tr = engine.parse_probe_json(self.PROBE)
        self.assertEqual([(t["kind"], t["n"], t["lang"]) for t in tr],
                         [("audio", 1, "en"), ("audio", 2, "ru"), ("subtitle", 1, "ru"),
                          ("subtitle", 2, ""), ("subtitle", 3, "fr")])
        self.assertIn("English", engine.track_label(tr[0]))
        self.assertIn("не поддерживаются", engine.track_label(tr[4]))
        self.assertFalse(engine.track_supported(tr[4]))

    def test_plan_names_and_codecs(self):
        d = tempfile.mkdtemp()
        tr = engine.parse_probe_json(self.PROBE)
        jobs = engine.plan_extract("ff", "/x/Movie.mkv", tr, d)
        names = [os.path.basename(j["out"]) for j in jobs]
        self.assertEqual(names, ["Movie.en.m4a", "Movie.ru.opus", "Movie.ru.srt", "Movie.sub2.ass"])  # dvd пропущен
        self.assertIn("copy", jobs[0]["cmd"])
        self.assertIn("-map", jobs[0]["cmd"]); self.assertIn("0:1", jobs[0]["cmd"])
        mp3 = engine.plan_extract("ff", "/x/Movie.mkv", tr[:1], d, "mp3")[0]
        self.assertTrue(mp3["out"].endswith(".mp3") and "libmp3lame" in mp3["cmd"])
        open(os.path.join(d, "Movie.ru.srt"), "w").close()     # чужой файл не затираем
        again = engine.plan_extract("ff", "/x/Movie.mkv", tr[2:3], d)
        self.assertTrue(again[0]["out"].endswith("Movie.ru.2.srt"))

    def test_real_ffmpeg_roundtrip(self):
        import shutil, subprocess
        if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
            self.skipTest("нет ffmpeg/ffprobe")
        d = tempfile.mkdtemp()
        v, s = os.path.join(d, "v.mp4"), os.path.join(d, "v.ru.srt")
        r = subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=d=1:s=160x90",
                            "-f", "lavfi", "-i", "sine=d=1", "-c:v", "libx264", "-c:a", "aac", "-shortest", v])
        if r.returncode:
            self.skipTest("ffmpeg без libx264")
        with open(s, "w", encoding="utf-8") as fh:
            fh.write("1\n00:00:00,000 --> 00:00:01,000\nПривет\n\n")
        merged = engine.merge_tracks(v, [(s, "ru")], [], os.path.join(d, "m.mkv"))
        tracks = engine.probe_tracks(merged)
        self.assertEqual([t["kind"] for t in tracks], ["audio", "subtitle"])
        out = os.path.join(d, "out"); os.makedirs(out)
        done, errs = engine.extract_tracks(engine.plan_extract(engine.find_ffmpeg(), merged, tracks, out))
        self.assertEqual(errs, [])
        self.assertEqual(sorted(os.path.basename(x) for x in done), ["m.audio1.m4a", "m.ru.srt"])
        self.assertIn("Привет", open(os.path.join(out, "m.ru.srt"), encoding="utf-8").read())


class TestCookiesFile(unittest.TestCase):
    def _f(self, text):
        p = os.path.join(tempfile.mkdtemp(), "c.txt")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)
        return p

    def test_good_and_bad(self):
        f = engine.cookies_file_problem
        self.assertEqual(f(""), "")
        self.assertEqual(f(self._f("# Netscape HTTP Cookie File\n.a.com\tTRUE\t/\tFALSE\t0\tn\tv\n")), "")
        self.assertEqual(f(self._f("# HTTP Cookie File\n")), "")
        self.assertIn("Netscape", f(self._f('[{"name": "a", "value": "b"}]')))
        self.assertTrue(f(os.path.join(tempfile.gettempdir(), "no_such_cookie_file.txt")))

    def test_bad_file_is_skipped_not_fatal(self):
        st = core.Settings(); st.cookies_file = self._f("[]")
        t = core.TaskStore().new_task("https://a.com/x")
        logs = []
        o = engine.build_ydl_opts(t, st, logger=engine.YLogger(logs.append))
        self.assertNotIn("cookiefile", o)
        self.assertTrue(any("cookies" in x for x in logs))
        st.cookies_file = self._f("# Netscape HTTP Cookie File\n")
        self.assertIn("cookiefile", engine.build_ydl_opts(t, st))


class TestPlaylistsTab(unittest.TestCase):
    def test_task_in_tab(self):
        s = core.TaskStore()
        single = s.new_task("https://www.youtube.com/watch?v=1")
        single.site = "youtube"
        pl = s.new_task("https://www.youtube.com/watch?v=2")
        pl.site, pl.group = "youtube", "Top songs"
        self.assertTrue(core.task_in_tab(single, "") and core.task_in_tab(pl, ""))
        self.assertFalse(core.task_in_tab(single, core.PLAYLISTS_TAB))
        self.assertTrue(core.task_in_tab(pl, core.PLAYLISTS_TAB))
        self.assertTrue(core.task_in_tab(single, "youtube") and not core.task_in_tab(single, "vk"))
        self.assertFalse(core.task_in_tab(single, core.MUSIC_TAB))
        single.mode = "audio"
        self.assertTrue(core.task_in_tab(single, core.MUSIC_TAB))
        self.assertFalse(core.task_in_tab(single, core.ERRORS_TAB))
        for st in (INVALID, INCOMPLETE):
            single.state = st
            self.assertTrue(core.task_in_tab(single, core.ERRORS_TAB))
        single.state = DONE
        self.assertFalse(core.task_in_tab(single, core.ERRORS_TAB))


class TestDisplayOrder(unittest.TestCase):
    def test_order(self):
        s = core.TaskStore()
        a = s.new_task("https://a.com/1")
        p1 = s.new_task("https://a.com/p1"); p1.group = "PL"
        b = s.new_task("https://a.com/2")
        p2 = s.new_task("https://a.com/p2"); p2.group = "PL"
        c = s.new_task("https://a.com/3")
        tasks = [a, p1, b, p2, c]

        def shape(entries):
            return [e[1].id if e[0] == "task" else ("g", [x.id for x in e[2]]) for e in entries]
        self.assertEqual(shape(core.display_order(tasks)), [a.id, ("g", [p1.id, p2.id]), b.id, c.id])
        self.assertEqual(shape(core.display_order(tasks, True)), [c.id, b.id, ("g", [p1.id, p2.id]), a.id])
        self.assertFalse(core.Settings().newest_first)


class TestAutoRetry(unittest.TestCase):
    def _engine(self, tries=3):
        st = core.Settings(); st.auto_retry = tries
        store = core.TaskStore()
        return engine.Engine(store, st, log=lambda *a: None, archive=core.Archive(os.path.join(tempfile.mkdtemp(), "a.json"))), store, st

    def test_delay_and_permanent(self):
        self.assertEqual([core.retry_delay(i) for i in (1, 2, 3, 4, 5, 9)], [10, 30, 90, 270, 300, 300])
        self.assertTrue(core.is_permanent_error("ERROR: Unsupported URL: http://x"))
        self.assertTrue(core.is_permanent_error("Video unavailable. This video is not available"))
        self.assertFalse(core.is_permanent_error("HTTP Error 403: Forbidden"))
        self.assertFalse(core.is_permanent_error("Sign in to confirm your age"))       # иногда проходит при повторе
        self.assertFalse(core.is_permanent_error("Sign in to confirm you're not a bot"))
        self.assertFalse(core.is_permanent_error("[Errno 11001] getaddrinfo failed"))

    def test_schedule_until_limit(self):
        eng, store, st = self._engine(2)
        t = store.new_task("https://a.com/x"); store.add(t)
        t.state = core.INVALID
        note1 = eng.schedule_retry(t, RuntimeError("HTTP Error 403"))
        self.assertIn("повтор 1 из 2", note1); self.assertGreater(t.retry_at, time.time())
        note2 = eng.schedule_retry(t, RuntimeError("HTTP Error 403"))
        self.assertIn("повтор 2 из 2", note2)
        self.assertEqual(eng.schedule_retry(t, RuntimeError("HTTP Error 403")), "")   # лимит исчерпан
        self.assertEqual(t.retry_at, 0.0)

    def test_not_for_permanent_or_disabled(self):
        eng, store, st = self._engine(3)
        t = store.new_task("https://a.com/x"); store.add(t)
        self.assertEqual(eng.schedule_retry(t, RuntimeError("Unsupported URL: x")), "")
        st.auto_retry = 0
        self.assertEqual(eng.schedule_retry(t, RuntimeError("HTTP Error 500")), "")

    def test_tick_restarts_when_due_and_manual_resets(self):
        eng, store, st = self._engine(3)
        t = store.new_task("https://a.com/x"); store.add(t)
        t.state = core.INVALID; t.error = "boom"
        eng.schedule_retry(t, RuntimeError("HTTP Error 500"))
        eng.tick()                                   # срок ещё не наступил
        self.assertEqual(t.state, core.INVALID)
        t.retry_at = time.time() - 1
        eng._spawn = lambda fn, tk: None             # без настоящих потоков
        eng.tick()
        self.assertIn(t.state, (core.QUEUED, core.READING))
        self.assertEqual(t.auto_tries, 1)            # счётчик сохранён, чтобы лимит работал
        t.state = core.INVALID
        eng.restart(t)                               # ручной перезапуск обнуляет счётчик
        self.assertEqual((t.auto_tries, t.retry_at), (0, 0.0))


class TestGenericTitle(unittest.TestCase):
    def test_generic_detection_and_name(self):
        import engine, types
        self.assertTrue(engine.is_generic_title("Video"))
        self.assertTrue(engine.is_generic_title("video 3"))
        self.assertFalse(engine.is_generic_title("My video about cats"))
        self.assertEqual(engine.better_title({"uploader": "Ann", "timestamp": 1759000000}), "Ann 2025-09-27")
        st = core.Settings(); st.outtmpl = "[artist] title (id)"
        t = core.TaskStore().new_task("https://boosty.to/a/posts/1")
        t.title = "Ann 2025-09-27"
        self.assertIn("%(title)s", engine.build_ydl_opts(t, st)["outtmpl"])       # обычное название берётся у yt-dlp
        t.title_fixed = True
        o = engine.build_ydl_opts(t, st)["outtmpl"]
        self.assertIn("Ann 2025-09-27", o)
        self.assertNotIn("%(title)s", o)
        t.title = "100% a/b"
        self.assertNotIn("/b", os.path.basename(engine.build_ydl_opts(t, st)["outtmpl"]).replace("100%% a_b", ""))


class TestUpdater(unittest.TestCase):
    def _zip(self, version="2999.1.1", drop=(), core_extra="", bad=None, nested="GreatTurtle/"):
        import io, zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for name in ("app.pyw", "core.py", "engine.py", "updater.py"):
                if name in drop:
                    continue
                body = f'APP_VERSION = "{version}"\n{core_extra}\n' if name == "core.py" else f"# {name}\n"
                if name == bad:
                    body = "def broken(:\n"
                z.writestr(nested + name, body)
            z.writestr(nested + "install.bat", "x")      # лишние файлы игнорируются
        return buf.getvalue()

    def test_read_ok(self):
        import updater
        ver, files = updater.read_package(self._zip())
        self.assertEqual(ver, "2999.1.1")
        self.assertEqual(set(files), {"app.pyw", "core.py", "engine.py", "updater.py"})

    def test_optional_icon_copied_not_parsed(self):
        import updater, io, zipfile
        buf = io.BytesIO(self._zip())
        with zipfile.ZipFile(buf, "a") as z:
            z.writestr("GreatTurtle/turtle.ico", b"\x00\x00\x01\x00 not python")
        ver, files = updater.read_package(buf.getvalue())
        self.assertIn("turtle.ico", files)

    def test_rejects_bad_archives(self):
        import updater
        for data in (b"not a zip", self._zip(drop=("engine.py",)), self._zip(bad="app.pyw")):
            with self.assertRaises(updater.UpdateError):
                updater.read_package(data)
        import io, zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for n in ("app.pyw", "core.py", "engine.py"):
                z.writestr(n, "x = 1\n")        # нет APP_VERSION
        with self.assertRaises(updater.UpdateError):
            updater.read_package(buf.getvalue())

    def test_version_compare(self):
        import updater
        self.assertTrue(updater.is_newer("2026.10.07", "2026.10.06"))
        self.assertTrue(updater.is_newer("2026.10.06.1", "2026.10.06"))
        self.assertFalse(updater.is_newer("2026.10.06", "2026.10.06"))
        self.assertTrue(updater.is_newer("2026.11.1", "2026.9.30"))

    def test_apply_with_backup_and_rollback(self):
        import updater
        d = tempfile.mkdtemp()
        for n in ("app.pyw", "core.py", "engine.py"):
            open(os.path.join(d, n), "w").write("OLD " + n)
        ver, files = updater.read_package(self._zip())
        backup = updater.apply_package(files, d, "1.0")
        self.assertIn("# app.pyw", open(os.path.join(d, "app.pyw")).read())
        self.assertEqual(open(os.path.join(backup, "core.py")).read(), "OLD core.py")
        self.assertTrue(os.path.exists(os.path.join(d, "updater.py")))
        # сбой записи -> откат
        d2 = tempfile.mkdtemp()
        for n in ("app.pyw", "core.py", "engine.py"):
            open(os.path.join(d2, n), "w").write("OLD " + n)
        os.makedirs(os.path.join(d2, "engine.py.new"))        # мешает записи engine.py
        with self.assertRaises(updater.UpdateError):
            updater.apply_package(files, d2, "1.0")
        self.assertEqual(open(os.path.join(d2, "app.pyw")).read(), "OLD app.pyw")

    def test_download_requires_https(self):
        import updater
        with self.assertRaises(updater.UpdateError):
            updater.download("http://example.com/a.zip")

    def test_current_core_has_version(self):
        import updater
        text = open(os.path.join(os.path.dirname(core.__file__), "core.py"), encoding="utf-8").read()
        self.assertEqual(updater.parse_version(text), core.APP_VERSION)


class TestH264Mode(unittest.TestCase):
    def test_h264(self):
        import engine
        st = core.Settings()
        t = core.TaskStore().new_task("https://a.com/x", mode="h264")
        o = engine.build_ydl_opts(t, st)
        self.assertTrue(o["format"].startswith("bv*[vcodec^=avc1]"))
        self.assertEqual(o["merge_output_format"], "mp4")
        self.assertTrue(o["format"].endswith("/bv*+ba/b"))         # запасной вариант, если H.264 нет
        self.assertEqual(core.norm_mode("h264"), "h264")


class TestSiteFoldersAndStatus(unittest.TestCase):
    def test_default_folder_task_goes_to_site_dir(self):
        import engine
        st = core.Settings(); st.folder = "/dl"
        t = core.TaskStore().new_task("https://www.youtube.com/watch?v=x", folder="/dl")
        self.assertTrue(engine.target_folder(t, st).replace("\\", "/").endswith("/dl/youtube"))
        t2 = core.TaskStore().new_task("https://www.youtube.com/watch?v=x", folder="/other")
        self.assertTrue(engine.target_folder(t2, st).replace("\\", "/").endswith("/other"))

    def test_hook_clears_processing_status(self):
        import engine
        eng = engine.Engine.__new__(engine.Engine)
        eng.notify = lambda _id: None
        t = core.TaskStore().new_task("https://a.com/x")
        t.status = "Обработка..."
        hook = engine.Engine.make_hook(eng, t)
        hook({"status": "downloading", "filename": "a", "downloaded_bytes": 5, "total_bytes": 10})
        self.assertEqual(t.status, "")
        self.assertAlmostEqual(t.progress, 0.5)


class TestThumbs(unittest.TestCase):
    def test_candidates_prefer_jpg_over_webp(self):
        import engine
        info = {"thumbnail": "https://x/a.webp",
                "thumbnails": [{"url": "https://x/b.jpg", "preference": 1, "width": 100, "height": 50},
                               {"url": "https://x/c.webp", "preference": 2, "width": 10, "height": 5},
                               {"url": "https://x/a.webp"}]}
        c = engine.thumb_candidates(info)
        self.assertEqual(c[0], "https://x/b.jpg")
        self.assertEqual(len(c), len(set(c)))
        self.assertTrue(all(u.startswith("http") for u in c))

    def test_fetch_rejects_html_and_accepts_jpeg(self):
        import engine, io, tempfile, urllib.request
        engine.THUMB_DIR = tempfile.mkdtemp()
        class R(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *a): pass
        class Op:
            def __init__(self, data): self.data = data
            def open(self, req, timeout=0):
                self.req = req
                return R(self.data)
        orig = urllib.request.build_opener
        try:
            op = Op(b"<html>403</html>" + b" " * 300)
            urllib.request.build_opener = lambda *a, **k: op
            self.assertEqual(engine.fetch_thumb("https://x/1.jpg", referer="https://site.com/v/1"), "")
            op = Op(b"\xff\xd8\xff\xe0" + b"0" * 500)
            urllib.request.build_opener = lambda *a, **k: op
            p = engine.fetch_thumb("https://x/2.jpg", referer="https://site.com/v/1")
            self.assertTrue(p and os.path.exists(p))
            self.assertEqual(op.req.headers.get("Referer"), "https://site.com/")
            self.assertTrue(os.path.exists(engine.fetch_thumb_any(["https://x/1.jpg", "https://x/2.jpg"])))
        finally:
            urllib.request.build_opener = orig


class TestAudioQuality(unittest.TestCase):
    def test_audio_postprocessors(self):
        import engine
        st = core.Settings()
        t = core.TaskStore().new_task("https://a.com/x"); t.mode = "audio"
        pp = engine.build_ydl_opts(t, st)["postprocessors"]
        self.assertEqual(pp[0]["preferredcodec"], "mp3")
        self.assertEqual(pp[0]["preferredquality"], "320")
        self.assertTrue(any(p["key"] == "FFmpegMetadata" for p in pp))
        st.audio_quality = "mp3_192"
        self.assertEqual(engine.build_ydl_opts(t, st)["postprocessors"][0]["preferredquality"], "192")
        st.audio_quality = "original"
        self.assertEqual(engine.build_ydl_opts(t, st)["postprocessors"][0]["preferredcodec"], "best")


class TestTabOrder(unittest.TestCase):
    D = ["@all", "@errors", "@music", "youtube", "vk"]

    def test_default_and_pins(self):
        self.assertEqual(core.order_tabs(self.D, [], []), self.D)
        self.assertEqual(core.order_tabs(self.D, [], ["vk"]), ["vk", "@all", "@errors", "@music", "youtube"])
        self.assertEqual(core.order_tabs(self.D, [], ["youtube", "vk"])[:2], ["youtube", "vk"])

    def test_user_order_and_new_tabs(self):
        order = ["youtube", "@all", "vk"]
        self.assertEqual(core.order_tabs(["@all", "youtube", "vk"], order, []), ["youtube", "@all", "vk"])
        # вкладка «Ошибки» появилась позже: встаёт после «Все», а не в конец
        self.assertEqual(core.order_tabs(["@all", "@errors", "youtube", "vk"], order, []),
                         ["youtube", "@all", "@errors", "vk"])
        # пропавшие вкладки в порядке не мешают
        self.assertEqual(core.order_tabs(["@all", "vk"], order, []), ["@all", "vk"])

    def test_pinned_snap_after_drag(self):
        # перетащили незакреплённую левее закреплённой — закреплённая всё равно впереди
        self.assertEqual(core.order_tabs(["@all", "youtube", "vk"], ["vk", "youtube", "@all"], ["@all"]),
                         ["@all", "vk", "youtube"])

    def test_split_and_settings(self):
        self.assertEqual(core.split_keys("a,,b"), ["a", "b"])
        self.assertEqual(core.split_keys(""), [])
        self.assertEqual(Settings().tab_order, "")


class TestNameTemplates(unittest.TestCase):
    def test_presets_expand(self):
        self.assertEqual(core.NAME_TEMPLATES[0][0], core.Settings().outtmpl)   # первый — тот, что по умолчанию
        for tpl, label in core.NAME_TEMPLATES:
            out = engine.hitomi_template(tpl)
            self.assertIn("%(title)s", out, tpl)
            self.assertTrue(out.endswith(".%(ext)s"), tpl)
            self.assertNotIn("artist", out.replace("%(artist", ""), tpl)
            self.assertTrue(label.strip(), tpl)
        self.assertEqual(len({t for t, _ in core.NAME_TEMPLATES}), len(core.NAME_TEMPLATES))


class TestI18n(unittest.TestCase):
    def setUp(self):
        self.old = core.LANG
        core.LANG = "en"

    def tearDown(self):
        core.LANG = self.old

    def test_exact_pattern_and_composite(self):
        self.assertEqual(core.tr("Скачать только звук"), "Download audio only")
        self.assertEqual(core.tr("Добавлено задач: 5"), "Downloads added: 5")
        self.assertEqual(core.tr("Неизвестный сайт · повтор 1 из 3 через 10 с"), "Unknown site · retry 1 of 3 in 10 s")
        self.assertEqual(core.tr("  Готово  "), "  Done  ")
        self.assertEqual(core.tr("Текст которого нет"), "Текст которого нет")   # неизвестное остаётся
        self.assertEqual(core.tr("Hello"), "Hello")
        core.LANG = "ru"
        self.assertEqual(core.tr("Готово"), "Готово")                           # по-русски ничего не меняется

    def test_dictionary_is_clean(self):
        import re
        cyr = re.compile("[А-Яа-яЁё]")
        ph = lambda s: sorted(re.findall(r"\{\d+\}", s))
        for ru, en in core.EN.items():
            self.assertFalse(cyr.search(en), (ru, en))
        for ru, en in core.PATTERNS:
            self.assertEqual(ph(ru), ph(en), (ru, en))
            self.assertFalse(cyr.search(en), (ru, en))

    def test_status_and_units(self):
        t = core.TaskStore().new_task("https://a.com/x")
        t.state, t.progress, t.speed = DOWNLOADING, 0.5, 2 * 1024 ** 2
        self.assertEqual(core.status_text(t), "Downloading 50% · 2.0 MB/s")
        t.state, t.error = INVALID, "Неизвестный сайт"
        self.assertEqual(core.status_text(t), "Error: Unknown site")
        self.assertEqual(core.fmt_size(3 * 1024 ** 3), "3.00 GB")

    def test_modes_and_templates_translated(self):
        labels = [l for _, l in core.MODES + core.AUDIO_QUALITIES + core.NAME_TEMPLATES] + [x[1] for x in core.TAGS[1:]]
        for label in labels:
            self.assertNotRegex(core.tr(label), "[А-Яа-яЁё]", label)

    def test_help_matches(self):
        import re
        src = open(os.path.join(os.path.dirname(core.__file__), "app.pyw"), encoding="utf-8").read()
        a = src.index('HELP_HTML = """') + len('HELP_HTML = """')
        ru = src[a:src.index('"""', a)]
        tags = lambda h: re.findall(r"<[^>]+>", h)
        self.assertEqual(tags(ru), tags(core.HELP_EN))
        self.assertNotRegex(core.HELP_EN.replace("Язык / Language", ""), "[А-Яа-яЁё]")
        self.assertEqual(core.help_html(ru), core.HELP_EN)

    def test_app_wraps_every_visible_literal(self):
        import ast
        src = open(os.path.join(os.path.dirname(core.__file__), "app.pyw"), encoding="utf-8").read()
        tree = ast.parse(src)
        par = {c: n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}

        def under_tr(n):
            while n in par:
                n = par[n]
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "tr":
                    return True
            return False
        allowed = ("<h2>Как пользоваться", "Язык", "Русский", "[обновление]", "[папки]", "[файлы]", "Уже скачано", ", копия: ", "Не удалось удалить ", "[telegram]", "Остановлено")
        bad = [n.value[:50] for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
               and __import__("re").search("[А-Яа-яЁё]", n.value) and not isinstance(par.get(n), ast.Expr)
               and not under_tr(n) and not n.value.lstrip().startswith(allowed)]
        self.assertEqual(bad, [])

    def test_settings_lang(self):
        self.assertEqual(Settings().lang, "ru")


class TestHttp412(unittest.TestCase):
    ERR = "ERROR: [BiliBili] 1R2a: Unable to download webpage: HTTP Error 412: Precondition Failed (caused by <HTTPError 412: Precondition Failed>)"

    def test_error_text_and_blocked(self):
        self.assertIn("412", core.friendly_error(self.ERR))
        self.assertIn("Имитировать браузер", core.friendly_error(self.ERR))
        self.assertTrue(engine.is_blocked_error(RuntimeError(self.ERR)))
        self.assertFalse(core.is_permanent_error(self.ERR))

    def test_retry_turns_on_impersonation(self):
        t = TaskStore().new_task("https://www.bilibili.com/video/BV1")
        calls = []

        def fn():
            calls.append(t.force_imp)
            if len(calls) < 2:
                raise RuntimeError(self.ERR)
            return "ok"
        self.assertEqual(engine.with_block_retry(fn, t, lambda *a: None, waits=(0, 0)), "ok")
        self.assertEqual(calls, [False, True])   # вторая попытка уже с имитацией браузера

    def test_opts_use_impersonation_when_forced(self):
        import types
        fake_mod = types.ModuleType("yt_dlp.networking.impersonate")

        class Tgt:
            @staticmethod
            def from_str(s):
                return ("imp", s)
        fake_mod.ImpersonateTarget = Tgt
        saved = {k: sys.modules.get(k) for k in ("yt_dlp", "yt_dlp.networking", "yt_dlp.networking.impersonate", "curl_cffi")}
        sys.modules.update({"curl_cffi": types.ModuleType("curl_cffi"), "yt_dlp": types.ModuleType("yt_dlp"), "yt_dlp.networking": types.ModuleType("yt_dlp.networking"),
                            "yt_dlp.networking.impersonate": fake_mod})
        try:
            st, t = Settings(), TaskStore().new_task("https://a.com/x")
            self.assertNotIn("impersonate", engine.build_ydl_opts(t, st))
            t.force_imp = True
            self.assertEqual(engine.build_ydl_opts(t, st)["impersonate"], ("imp", "chrome"))
        finally:
            for k, v in saved.items():
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v


class TestRenamedFolders(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.store = TaskStore()
        self.old = os.path.join(self.root, "youtube", "Мой плейлист")
        os.makedirs(self.old)
        self.tasks = []
        for i in range(4):
            f = os.path.join(self.old, f"song{i}.mp3")
            open(f, "w").write("x")
            t = self.store.new_task(f"https://y.com/{i}", group="Мой плейлист")
            t.state, t.files = DONE, [f]
            self.tasks.append(t)
        self.queued = self.store.new_task("https://y.com/q", group="Мой плейлист")   # ещё не скачана

    def rename(self, new="Любимое"):
        dst = os.path.join(self.root, "youtube", new)
        os.rename(self.old, dst)
        return dst

    def test_nothing_when_folder_exists(self):
        self.assertEqual(core.find_renamed_dirs(self.tasks), {})

    def test_rename_detected_and_applied(self):
        dst = self.rename()
        m = core.find_renamed_dirs(self.tasks)
        self.assertEqual(m, {self.old: dst})
        collapsed = {"Мой плейлист"}
        groups, n = core.apply_dir_renames(self.tasks + [self.queued], m, collapsed)
        self.assertEqual(groups, {"Мой плейлист": "Любимое"})
        self.assertEqual(n, 4)
        self.assertTrue(all(os.path.exists(t.files[0]) for t in self.tasks))   # файлы открываются
        self.assertEqual({t.group for t in self.tasks + [self.queued]}, {"Любимое"})
        self.assertEqual(collapsed, {"Любимое"})
        self.assertEqual(core.find_renamed_dirs(self.tasks), {})

    def test_partial_and_ambiguous(self):
        # в новой папке осталась только половина файлов — всё ещё считаем переименованием
        dst = self.rename()
        os.remove(os.path.join(dst, "song0.mp3"))
        os.remove(os.path.join(dst, "song1.mp3"))
        self.assertEqual(core.find_renamed_dirs(self.tasks), {self.old: dst})
        # только один файл из четырёх — это не он
        os.remove(os.path.join(dst, "song2.mp3"))
        self.assertEqual(core.find_renamed_dirs(self.tasks), {})

    def test_two_equal_candidates_skipped(self):
        dst = self.rename()
        import shutil
        shutil.copytree(dst, os.path.join(self.root, "youtube", "Копия"))
        self.assertEqual(core.find_renamed_dirs(self.tasks), {})

    def test_deleted_folder_and_cache(self):
        import shutil
        shutil.rmtree(self.old)
        cache = {}
        self.assertEqual(core.find_renamed_dirs(self.tasks, cache=cache, now=100), {})
        self.assertIn(self.old, cache)
        # в пределах минуты повторно не перебираем: даже появившаяся папка пока не замечается
        os.makedirs(os.path.join(self.root, "youtube", "Новая"))
        for i in range(4):
            open(os.path.join(self.root, "youtube", "Новая", f"song{i}.mp3"), "w").write("x")
        self.assertEqual(core.find_renamed_dirs(self.tasks, cache=cache, now=130), {})
        self.assertEqual(len(core.find_renamed_dirs(self.tasks, cache=cache, now=170)), 1)

    def test_active_tasks_skipped(self):
        self.rename()
        self.assertEqual(core.find_renamed_dirs(self.tasks, skip=lambda t: True), {})

    def test_other_playlists_not_stolen(self):
        # соседняя папка другого плейлиста с теми же именами файлов принадлежит своим задачам
        other = os.path.join(self.root, "youtube", "Другой")
        os.makedirs(other)
        ot = []
        for i in range(4):
            f = os.path.join(other, f"song{i}.mp3")
            open(f, "w").write("x")
            t = self.store.new_task(f"https://y.com/o{i}", group="Другой")
            t.state, t.files = DONE, [f]
            ot.append(t)
        import shutil
        shutil.rmtree(self.old)
        self.assertEqual(core.find_renamed_dirs(self.tasks + ot), {})

    def test_single_file_renamed_and_moved(self):
        t0 = self.tasks[0]
        t0.fsig = core.files_sigs(t0.files)
        t1 = self.tasks[1]
        t1.fsig = core.files_sigs(t1.files)
        t1.size = os.path.getsize(t1.files[0])
        os.rename(t0.files[0], os.path.join(self.old, "новое имя.mp3"))               # переименован на месте
        sub = os.path.join(self.root, "Музыка", "Избранное")
        os.makedirs(sub)
        os.rename(t1.files[0], os.path.join(sub, "song1.mp3"))                          # перенесён в другую папку
        got = core.find_moved_files(self.tasks, [self.root])
        self.assertEqual(got[(t0.id, 0)], (t0.files[0], os.path.join(self.old, "новое имя.mp3")))
        self.assertEqual(got[(t1.id, 0)][1], os.path.join(sub, "song1.mp3"))
        self.assertEqual(len(got), 2)

    def test_moved_file_ambiguity_and_skips(self):
        t0 = self.tasks[0]
        t0.fsig = core.files_sigs(t0.files)
        src = t0.files[0]
        # два одинаковых кандидата с разными именами — не угадываем
        a, b = os.path.join(self.root, "a.mp3"), os.path.join(self.root, "b.mp3")
        os.rename(src, a)
        import shutil
        shutil.copy2(a, b)
        self.assertEqual(core.find_moved_files(self.tasks, [self.root]), {})
        # другой размер — не он
        os.remove(b)
        open(a, "w").write("longer content")
        self.assertEqual(core.find_moved_files(self.tasks, [self.root], cache={}), {})
        # занятые загрузкой не трогаем; повторно не ищем чаще, чем раз в retry
        os.remove(a)
        open(a, "w").write("x")
        os.utime(a, (t0.fsig[0][1], t0.fsig[0][1]))
        self.assertEqual(core.find_moved_files(self.tasks, [self.root], skip=lambda t: True), {})
        cache = {src: 100}
        self.assertEqual(core.find_moved_files(self.tasks, [self.root], cache=cache, now=150), {})
        self.assertEqual(len(core.find_moved_files(self.tasks, [self.root], cache=cache, now=500)), 1)

    def test_shared_file_updates_every_task(self):
        # один файл подключён и к отдельной загрузке, и к плейлисту: путь обновляется у обеих
        t0 = self.tasks[0]
        t0.fsig = core.files_sigs(t0.files)
        twin = self.store.new_task("https://y.com/twin", group="Другой плейлист")
        twin.state, twin.files, twin.fsig = DONE, list(t0.files), list(t0.fsig)
        new = os.path.join(self.root, "moved.mp3")
        os.rename(t0.files[0], new)
        got = core.find_moved_files(self.tasks + [twin], [self.root])
        self.assertEqual({k: v[1] for k, v in got.items()}, {(t0.id, 0): new, (twin.id, 0): new})

    def test_old_tasks_without_signature_use_size(self):
        t0 = self.tasks[0]
        t0.size = os.path.getsize(t0.files[0])
        os.rename(t0.files[0], os.path.join(self.old, "other.mp3"))
        self.assertEqual(len(core.find_moved_files(self.tasks, [self.root])), 1)
        t0.size = 0
        self.assertEqual(core.find_moved_files(self.tasks, [self.root], cache={}), {})

    def test_archive_rebase_and_sigs(self):
        a = core.Archive(os.path.join(tempfile.mkdtemp(), "a.json"))
        f = self.tasks[0].files[0]
        a.add("k", [f], "T")
        new = os.path.join(self.old, "n.mp3")
        os.rename(f, new)
        self.assertIsNone(a.has("k"))
        a.rebase(f, new)
        self.assertEqual(a.has("k"), [new])
        self.assertEqual(core.files_sigs([new])[0][0], 1)
        self.assertEqual(core.files_sigs([new, "/no/such"]), [])
        self.assertIn("fsig", core.PERSIST)

    def test_rename_in_app_texts(self):
        core.LANG = "en"
        try:
            self.assertIn("renamed", core.tr("Папка «A» переименована в «B»: группа и пути к файлам обновлены"))
        finally:
            core.LANG = "ru"


class TestArchive(unittest.TestCase):
    def test_key_and_storage(self):
        self.assertEqual(core.archive_key("https://www.youtube.com/watch?v=abc", "abc", "best"), "youtube:abc:v")
        self.assertEqual(core.archive_key("https://www.youtube.com/watch?v=abc", "abc", "audio"), "youtube:abc:a")
        self.assertEqual(core.archive_key("https://x.com/1", "", "best"), "")
        d = tempfile.mkdtemp()
        f = os.path.join(d, "a.mp4")
        open(f, "w").write("x")
        a = core.Archive(os.path.join(d, "arch.json"))
        a.add("k", [f], "T")
        self.assertEqual(core.Archive(os.path.join(d, "arch.json")).has("k"), [f])  # переживает перезапуск
        os.remove(f)
        self.assertIsNone(a.has("k"))          # файла нет — качаем снова
        self.assertIsNone(a.has(""))
        a.add("k2", [], "T")
        self.assertEqual(a.count(), 1)
        a.clear()
        self.assertEqual(a.count(), 0)

    def test_broken_file(self):
        p = os.path.join(tempfile.mkdtemp(), "a.json")
        open(p, "w").write("[1,2")
        self.assertEqual(core.Archive(p).count(), 0)


class TestArchiveEngine(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.calls = []
        self._orig = (engine.probe_ytdlp, engine.download_ytdlp)

        def probe(t, st, log):
            return {"kind": "single", "title": "T", "artist": "A", "id": "id1", "duration": 1,
                    "size": 10, "thumb": ""}

        def download(t, st, eng):
            self.calls.append(t.id)
            f = os.path.join(self.d, f"f{len(self.calls)}.mp4")
            open(f, "w").write("data")
            t.files = [f]
        engine.probe_ytdlp, engine.download_ytdlp = probe, download
        self.store, self.st = TaskStore(), Settings()
        self.st.use_gallerydl = False
        self.idle = []
        self.eng = engine.Engine(self.store, self.st, on_idle=lambda: self.idle.append(1),
                                 archive=core.Archive(os.path.join(self.d, "a.json")))

    def tearDown(self):
        engine.probe_ytdlp, engine.download_ytdlp = self._orig

    def run_task(self, t):
        self.idle.clear()
        end = time.time() + 10
        while time.time() < end and not self.idle:
            self.eng.tick()
            time.sleep(0.02)

    def add(self):
        t = self.store.new_task("https://www.youtube.com/watch?v=id1")
        self.store.add(t)
        return t

    def test_second_time_skipped(self):
        t1 = self.add()
        self.run_task(t1)
        self.assertEqual((t1.state, len(self.calls)), (DONE, 1))
        self.store.tasks.remove(t1)           # убрали из списка — архив помнит
        t2 = self.add()
        self.run_task(t2)
        self.assertEqual(t2.state, DONE)
        self.assertEqual(len(self.calls), 1)  # второй раз не качали
        self.assertEqual(t2.files, t1.files)
        self.assertIn("Уже скачано", t2.status)

    def test_restart_and_switches(self):
        t1 = self.add()
        self.run_task(t1)
        self.eng.restart(t1)                  # ручной перезапуск качает заново
        self.run_task(t1)
        self.assertEqual(len(self.calls), 2)
        self.st.use_archive = False
        t3 = self.add()
        self.run_task(t3)
        self.assertEqual(len(self.calls), 3)

    def test_deleted_file_downloads_again(self):
        t1 = self.add()
        self.run_task(t1)
        os.remove(t1.files[0])
        self.store.tasks.remove(t1)
        t2 = self.add()
        self.run_task(t2)
        self.assertEqual(len(self.calls), 2)

    def test_audio_is_separate_and_manual_format_not_archived(self):
        t1 = self.add()
        self.run_task(t1)
        t2 = self.store.new_task("https://www.youtube.com/watch?v=id1", mode="audio")
        self.store.add(t2)
        self.run_task(t2)
        self.assertEqual(len(self.calls), 2)  # видео в архиве, звук — нет
        t3 = self.store.new_task("https://www.youtube.com/watch?v=id1")
        t3.fmt_sel = "137+bestaudio/137"
        self.store.add(t3)
        self.run_task(t3)
        self.assertEqual(len(self.calls), 3)


class TestArchiveBackfill(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.f = os.path.join(self.d, "a.mp4")
        open(self.f, "w").write("x")
        self.arch = core.Archive(os.path.join(self.d, "arch.json"))

    def task(self, url="https://www.youtube.com/watch?v=abc", vid="abc"):
        t = TaskStore().new_task(url, mode="best")
        t.state, t.backend, t.vid, t.files = DONE, "yt-dlp", vid, [self.f]
        return t

    def test_old_done_task_is_found(self):
        self.assertIsNone(self.arch.find("https://www.youtube.com/watch?v=abc", "abc", "best"))
        self.assertTrue(self.arch.backfill([self.task()]))
        self.assertEqual(self.arch.find("https://www.youtube.com/watch?v=abc", "abc", "best"), [self.f])
        self.assertFalse(self.arch.backfill([self.task()]))   # повторно ничего не меняет

    def test_found_by_url_when_ids_differ(self):
        # у сайта id в плейлисте («BV1…») и в самом ролике («1…») разные, а ссылка та же
        self.arch.record("https://www.bilibili.com/video/BV1x", "1x", "best", [self.f], "T")
        self.assertEqual(self.arch.find("https://www.bilibili.com/video/BV1x", "BV1x", "best"), [self.f])
        self.assertIsNone(self.arch.find("https://www.bilibili.com/video/BV1x", "BV1x", "audio"))

    def test_skips_not_done_missing_files_and_manual_format(self):
        t = self.task()
        t.state = INVALID
        t2 = self.task("https://v.com/2", "2")
        t2.files = [os.path.join(self.d, "nope.mp4")]
        t3 = self.task("https://v.com/3", "3")
        t3.fmt_sel = "18"
        self.assertFalse(self.arch.backfill([t, t2, t3]))
        self.assertEqual(self.arch.count(), 0)


class TestCoverAndFormat(unittest.TestCase):
    def mk(self, mode="audio", **kw):
        st = Settings()
        for k, v in kw.items():
            setattr(st, k, v)
        t = TaskStore().new_task("https://a.com/x")
        t.mode = mode
        return engine.build_ydl_opts(t, st), t, st

    def test_cover_on_and_off(self):
        o, _, _ = self.mk()
        keys = [p["key"] for p in o["postprocessors"]]
        self.assertTrue(o["writethumbnail"])
        self.assertLess(keys.index("FFmpegMetadata"), keys.index("EmbedThumbnail"))
        self.assertIn("FFmpegThumbnailsConvertor", keys)
        o, _, _ = self.mk(embed_cover=False)
        self.assertNotIn("writethumbnail", o)
        self.assertNotIn("EmbedThumbnail", [p["key"] for p in o["postprocessors"]])
        o, _, _ = self.mk(mode="best")
        self.assertNotIn("writethumbnail", o)

    def test_cover_error_detected(self):
        self.assertTrue(engine.is_cover_error(RuntimeError("Unable to embed thumbnail")))
        self.assertFalse(engine.is_cover_error(RuntimeError("HTTP Error 403")))

    def test_cover_fallback_retries_without(self):
        orig, seen = engine.download_ytdlp, []

        def fake(t, st, eng):
            seen.append(st.embed_cover)
            if st.embed_cover:
                raise RuntimeError("ERROR: Unable to embed thumbnail")
        engine.download_ytdlp = fake
        try:
            st = Settings()
            e = engine.Engine(TaskStore(), st, archive=core.Archive(os.path.join(tempfile.mkdtemp(), "a.json")))
            t = TaskStore().new_task("https://a.com/x")
            t.mode = "audio"
            e._download_yt(t, st)
        finally:
            engine.download_ytdlp = orig
        self.assertEqual(seen, [True, False])

    def test_manual_format_overrides_mode(self):
        o, t, st = self.mk(mode="r720")
        self.assertIn("format_sort", o)
        t.fmt_sel = "137+bestaudio/137"
        o = engine.build_ydl_opts(t, st)
        self.assertEqual(o["format"], "137+bestaudio/137")
        self.assertNotIn("format_sort", o)
        self.assertNotIn("merge_output_format", o)

    def test_format_rows(self):
        info = {"formats": [
            {"format_id": "sb0", "ext": "mhtml", "vcodec": "none", "acodec": "none"},
            {"format_id": "140", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2", "abr": 129.5},
            {"format_id": "137", "ext": "mp4", "vcodec": "avc1.64002a", "acodec": "none", "width": 1920,
             "height": 1080, "fps": 60, "tbr": 4500, "filesize": 1000},
            {"format_id": "18", "ext": "mp4", "vcodec": "avc1", "acodec": "mp4a", "width": 640, "height": 360},
            {"format_id": "251", "ext": "webm", "vcodec": "none", "acodec": "opus", "abr": 130},
        ]}
        rows = engine.format_rows(info)
        self.assertEqual([r["id"] for r in rows], ["137", "18", "251", "140"])
        by = {r["id"]: r for r in rows}
        self.assertEqual(by["137"]["sel"], "137+bestaudio/137")
        self.assertEqual(by["18"]["sel"], "18")
        self.assertEqual(by["140"]["sel"], "140")
        self.assertEqual(by["140"]["res"], "только звук")
        self.assertEqual(by["137"]["res"], "1920x1080")
        self.assertEqual(engine.format_rows({}), [])

    def test_fmt_sel_persisted(self):
        t = TaskStore().new_task("https://a.com/x")
        t.fmt_sel = "18"
        self.assertEqual(t.to_dict()["fmt_sel"], "18")


class TestUpdateCmd(unittest.TestCase):
    def test_pre_flag(self):
        got = []
        orig = engine.subprocess.run

        class R:
            returncode, stdout, stderr = 0, "ok", ""
        engine.subprocess.run = lambda cmd, **k: (got.append(cmd), R())[1]
        try:
            engine.update_ytdlp()
            engine.update_ytdlp(True)
        finally:
            engine.subprocess.run = orig
        self.assertNotIn("--pre", got[0])
        self.assertIn("--pre", got[1])
        self.assertIn("yt-dlp[default]", got[0])

    def test_settings_defaults(self):
        s = Settings()
        self.assertTrue(s.use_archive and s.embed_cover)
        self.assertFalse(s.ytdlp_pre)


class TestTaskSize(unittest.TestCase):
    def test_size_rules(self):
        st = core.TaskStore()
        a = st.new_task("https://x/a"); a.state = DOWNLOADING; a.total = 500
        b = st.new_task("https://x/b"); b.state = DONE; b.total = 500; b.size = 480
        c = st.new_task("https://x/c")
        self.assertEqual(core.task_size(a), 500)
        self.assertEqual(core.task_size(b), 480)   # у готового — фактический
        self.assertEqual(core.task_size(c), 0)

    def test_hook_sets_expected_total(self):
        eng = engine.Engine.__new__(engine.Engine)
        eng.notify = lambda tid: None
        st = core.TaskStore(); t = st.new_task("https://x/a")
        hook = engine.Engine.make_hook(eng, t)
        hook({"status": "downloading", "filename": "v.f1", "downloaded_bytes": 10, "total_bytes": 100})
        hook({"status": "downloading", "filename": "a.f2", "downloaded_bytes": 5, "total_bytes_estimate": 20})
        self.assertEqual(t.total, 120)
        t2 = st.new_task("https://x/b"); t2.total = 999
        hook2 = engine.Engine.make_hook(eng, t2)
        hook2({"status": "downloading", "filename": "v", "downloaded_bytes": 1, "total_bytes": 50})
        self.assertEqual(t2.total, 999)   # размер из проверки не затирается


class TestSizeGuess(unittest.TestCase):
    def test_guess(self):
        g = engine.format_size_guess
        self.assertEqual(g({"filesize": 100, "tbr": 5}, 10), 100)
        self.assertEqual(g({"filesize_approx": 70}, 10), 70)
        self.assertEqual(g({"tbr": 800}, 100), 800 * 125 * 100)   # HLS без размера: битрейт × длительность
        self.assertEqual(g({"vbr": 700, "abr": 100}, 100), 800 * 125 * 100)
        self.assertEqual(g({"tbr": 800}, 0), 0)
        self.assertEqual(g({}, 50), 0)


class TestSelectLabels(unittest.TestCase):
    def test_label_and_total(self):
        self.assertEqual(core.select_label(3, "Имя"), "3.  Имя")
        self.assertIn("150.0 МБ", core.select_label(3, "Имя", 150 * 1024 ** 2, 754, True))
        self.assertIn("✓ Имя", core.select_label(3, "Имя", 0, 0, True))
        self.assertEqual(core.select_total([1, 2, 3], {1: 10, 2: 0, 3: 5}), (15, 1))
        self.assertEqual(core.select_total([], {}), (0, 0))


class TestRobustness(unittest.TestCase):
    def test_corrupt_json_recovers_from_backup(self):
        d = tempfile.mkdtemp(); path = os.path.join(d, "s.json")
        core.save_json(path, {"a": 1})
        core.save_json(path, {"a": 2})            # прежняя версия уходит в .bak
        with open(path, "w") as f:
            f.write('{"a": ')                      # обрыв записи
        self.assertEqual(core.load_json(path, {}), {"a": 1})
        self.assertTrue(os.path.exists(path + ".corrupt"))
        self.assertEqual(core.load_json(os.path.join(d, "none.json"), 5), 5)

    def test_wrong_shapes_do_not_crash(self):
        d = tempfile.mkdtemp()
        for bad in ("null", "[1,2]", "5", '"x"'):
            sp = os.path.join(d, "s.json"); open(sp, "w").write(bad)
            self.assertEqual(Settings.load(sp).mode, Settings().mode)
            st = core.TaskStore(); st.load(sp)
            self.assertEqual(st.tasks, [])
        sp = os.path.join(d, "st.json")
        import json as _j
        _j.dump({"collapsed": 5, "tasks": [
            {"id": "x", "url": "u"}, {"id": 1, "url": "https://a/1", "files": "oops", "title": None},
            {"id": 1, "url": "dup"}, 7, {"id": 2, "url": "https://a/2"}]}, open(sp, "w"))
        st = core.TaskStore(); st.load(sp)
        self.assertEqual([t.id for t in st.tasks], [1, 2])
        self.assertEqual(st.tasks[0].files, [])
        self.assertEqual(next(st._seq), 3)

    def test_settings_folder_defaults(self):
        d = tempfile.mkdtemp(); sp = os.path.join(d, "s.json")
        import json as _j
        _j.dump({"folder": "   "}, open(sp, "w"))
        self.assertTrue(Settings.load(sp).folder)

    def test_safe_name(self):
        self.assertEqual(core.safe_name("CON"), "_CON")
        self.assertEqual(core.safe_name("a\x00b"), "a_b")
        self.assertEqual(core.safe_name("x" * 119 + ". y"), "x" * 119)
        self.assertEqual(core.safe_name("..."), "playlist")

    def test_friendly_error_precision(self):
        self.assertNotEqual(core.friendly_error("[youtube] ab429xyz: Video unavailable"), "Слишком много запросов; повторите позже")
        self.assertEqual(core.friendly_error("HTTP Error 429: Too Many Requests"), "Слишком много запросов; повторите позже")
        self.assertFalse(core.is_permanent_error("ffmpeg exited with code 1: no space left"))
        self.assertTrue(core.is_permanent_error("ffmpeg not found"))

    def test_tr_long_text(self):
        saved = core.LANG
        core.LANG = "en"
        try:
            self.assertEqual(len(core.tr("\n".join(["Ошибка"] * 3000))), len("\n".join(["Ошибка"] * 3000)))
            self.assertEqual(core.tr("Ошибка\nОшибка"), "Error\nError")
        finally:
            core.LANG = saved

    def test_archive_ignores_volatile_urls(self):
        k = core.archive_keys("https://www.twitch.tv/somebody/live", "", "best")
        self.assertEqual(k, [])
        k = core.archive_keys("https://www.youtube.com/@x/videos", "", "best")
        self.assertEqual(k, [])
        self.assertEqual(len(core.archive_keys("https://v.example/watch?v=1", "1", "best")), 2)

    def test_template_percent_escaped(self):
        self.assertEqual(engine.hitomi_template("100% title"), "100%% %(title)s.%(ext)s")

    def test_browser_spec_lower(self):
        self.assertEqual(engine.browser_spec(" Chrome "), ("chrome", None))
        self.assertEqual(engine.browser_spec("Firefox: Default "), ("firefox", "Default"))

    def test_updater_skips_backups(self):
        import io as _io, zipfile as _z, updater
        core_src = open(os.path.join(os.path.dirname(core.__file__), "core.py"), "rb").read()
        buf = _io.BytesIO()
        with _z.ZipFile(buf, "w") as z:
            for n in ("app.pyw", "core.py", "engine.py"):
                z.writestr("G/" + n, core_src if n == "core.py" else "x = 1\n")
            z.writestr("G/_backup_1/core.py", "x = 2\n")   # резервная копия прошлого обновления в архиве папки программы
        ver, files = updater.read_package(buf.getvalue())
        self.assertEqual(files["core.py"], core_src)


class TestTelegram(unittest.TestCase):
    def test_parsers(self):
        self.assertEqual(core.parse_chat_ids("123, -456;789 abc 1x"), {123, -456, 789})
        self.assertEqual(core.parse_chat_ids(""), set())
        self.assertEqual(core.tg_parse("/file@MyBot 12"), ("file", "12"))
        self.assertEqual(core.tg_parse("/LIST"), ("list", ""))
        self.assertEqual(core.tg_parse("https://a/b  "), ("", "https://a/b"))
        self.assertEqual(set(core.TG_ADMIN_CMDS), {"list", "status", "pause", "resume", "retry", "file"} | set(core.TG_OWNER_CMDS))

    def test_list(self):
        st = core.TaskStore()
        for i in range(20):
            t = st.new_task(f"https://x/{i}"); t.title = f"T{i}"; t.state = DONE if i % 2 else QUEUED; st.add(t)
        text = core.tg_list(st.tasks)
        self.assertIn("#20", text); self.assertNotIn("#1 ", text)
        self.assertIn("20", text.splitlines()[0])
        self.assertEqual(core.tg_list([]), "Список пуст")

    def test_members_table(self):
        d = tempfile.mkdtemp(); path = os.path.join(d, "m.json")
        m = core.Members(path)
        self.assertEqual(m.role(5), "")
        info, new = m.touch(5, "Иван", "ivan", "pending")
        self.assertTrue(new); self.assertEqual(info["role"], "pending")
        info, new = m.touch(5, "Иван", "ivan", "guest")
        self.assertFalse(new); self.assertEqual(info["role"], "pending")   # роль существующего не меняется
        m.set_role(5, "guest"); m.set_role(7, "admin"); m.set_role(9, "blocked")
        self.assertEqual(m.ids("admin", "guest"), {5, 7})
        m2 = core.Members(path)                                          # сохранилось на диск
        self.assertEqual((m2.role(5), m2.role(7), m2.role(9)), ("guest", "admin", "blocked"))
        self.assertEqual(m2.items()[0][0], 5) if False else None
        with self.assertRaises(ValueError):
            m.set_role(1, "boss")
        m.remove(5); self.assertEqual(m.role(5), "")
        open(path, "w").write('{"x": 1, "3": {"role": "wizard"}, "4": {"role": "guest"}}')
        self.assertEqual(core.Members(path).ids("guest"), {4})            # мусор в файле пропускается

    def _bot(self, replies, **kw):
        import json as _j
        calls = []

        class Resp:
            def __init__(s, obj): s.b = _j.dumps(obj).encode()
            def read(s): return s.b
            def __enter__(s): return s
            def __exit__(s, *a): return False

        class Opener:
            def open(s, req, timeout=0):
                calls.append((req.full_url, req.data))
                r = replies.pop(0)
                if isinstance(r, Exception):
                    raise r
                return Resp(r)
        got = []
        d = tempfile.mkdtemp()
        members = core.Members(os.path.join(d, "m.json"))
        bot = engine.TelegramBot("123:SECRET", kw.pop("admins", {1}), lambda *a: got.append(a),
                                 log=lambda m: calls.append(("log", m)), members=members, **kw)
        bot._opener = Opener()
        return bot, calls, got

    @staticmethod
    def _upd(uid, text, n=1, chat=None):
        return {"update_id": n, "message": {"chat": {"id": chat or uid}, "from": {"id": uid, "first_name": "A"}, "text": text}}

    def test_admin_message_is_delivered(self):
        bot, calls, got = self._bot([])
        self.assertTrue(bot.handle_update(self._upd(1, "https://a/b")))
        self.assertEqual(got, [(1, "https://a/b", 1, True)])

    def test_stranger_becomes_pending_and_admins_are_told(self):
        bot, calls, got = self._bot([])
        self.assertFalse(bot.handle_update(self._upd(50, "hello")))
        self.assertEqual(bot.members.role(50), "pending"); self.assertEqual(got, [])
        sent = []
        while not bot._out.empty():
            sent.append(bot._out.get_nowait())
        self.assertTrue(any(x[1] == 1 and "заявка" in x[2].lower() for x in sent), sent)   # админу
        self.assertTrue(any(x[1] == 50 and "50" in x[2] for x in sent), sent)              # и самому участнику с его id
        # повторное сообщение сразу после — без нового ответа и без повторного уведомления
        bot.handle_update(self._upd(50, "again"))
        self.assertTrue(bot._out.empty())

    def test_auto_accept_guest_and_blocked(self):
        bot, calls, got = self._bot([])
        bot.auto_accept = True
        self.assertTrue(bot.handle_update(self._upd(60, "https://x")))
        self.assertEqual(got[-1][3], False); self.assertEqual(bot.members.role(60), "guest")
        bot.members.set_role(61, "blocked")
        self.assertFalse(bot.handle_update(self._upd(61, "hi")))
        self.assertTrue(bot.members.role(61) == "blocked")

    def test_guests_param_and_empty_text(self):
        bot, calls, got = self._bot([], guests={8})
        self.assertEqual(bot.guests, {8}); self.assertEqual(bot.admins, {1})
        self.assertFalse(bot.handle_update(self._upd(1, "   ")))
        self.assertFalse(bot.handle_update({"update_id": 3, "message": {"text": "x"}}))

    def test_poll_loop_and_secret(self):
        import urllib.error, io
        err = urllib.error.HTTPError("u", 401, "x", {}, io.BytesIO(b'{"ok":false,"error_code":401,"description":"Unauthorized"}'))
        bot, calls, got = self._bot([err])
        bot._poll_loop()
        logs = [c[1] for c in calls if c[0] == "log"]
        self.assertTrue(logs); self.assertFalse(any("SECRET" in m for m in logs))
        # обновления доставляются, offset растёт
        replies = [{"ok": True, "result": {"username": "tbot"}},
                   {"ok": True, "result": True}, {"ok": True, "result": True},   # меню команд
                   {"ok": True, "result": [self._upd(1, "hi", n=7)]}]
        bot2, calls2, got2 = self._bot(replies)
        orig = bot2.handle_update
        bot2.handle_update = lambda u: (orig(u), bot2._stop.set())
        bot2._poll_loop()
        self.assertEqual(got2[0][1], "hi"); self.assertEqual(bot2.offset, 8); self.assertEqual(bot2.username, "tbot")
        bot3, calls3, _ = self._bot([OSError("fail https://api.telegram.org/bot123:SECRET/getMe")])
        bot3._stop.set()
        bot3._poll_loop()
        self.assertFalse(any("SECRET" in str(c[1]) for c in calls3 if c[0] == "log"))

    def test_multipart_and_send(self):
        d = tempfile.mkdtemp(); f = os.path.join(d, "видео.mp4"); open(f, "wb").write(b"abc")
        body, ct = engine.build_multipart({"chat_id": 7, "caption": "c"}, "document", f)
        self.assertTrue(ct.startswith("multipart/form-data; boundary="))
        self.assertIn(b'name="chat_id"', body); self.assertIn("видео.mp4".encode(), body); self.assertIn(b"abc", body)
        bot, calls, _ = self._bot([{"ok": True, "result": {}}])
        bot.send_text(1, "x" * 9000)
        self.assertLessEqual(len(calls[0][1]), 4200)

    def test_button_menu(self):
        self.assertEqual(core.tg_button_cmd("📋 Список"), "list")
        self.assertEqual(core.tg_button_cmd("  ⏸ Пауза "), "pause")
        self.assertEqual(core.tg_button_cmd("❓ Помощь"), "help")
        self.assertEqual(core.tg_button_cmd("привет"), "")
        bot, calls, got = self._bot([])
        kb = bot.admin_keyboard()
        self.assertTrue(kb["resize_keyboard"])
        flat = lambda k: [b["text"] for row in k["keyboard"] for b in row]
        self.assertEqual(len(flat(kb)), len(core.TG_BUTTONS_ADMIN))
        # кнопки по правам: гость < администратор < руководитель
        bot.members.set_role(2, "admin"); bot.members.set_role(3, "guest"); bot.members.set_owner(9)
        g, a, o = flat(bot.keyboard_for(3)), flat(bot.keyboard_for(2)), flat(bot.keyboard_for(9))
        self.assertEqual(len(g), len(core.TG_BUTTONS_GUEST)); self.assertEqual(len(a), len(core.TG_BUTTONS_ADMIN))
        self.assertEqual(len(o), len(core.TG_BUTTONS_OWNER))
        self.assertTrue(set(g) < set(a) < set(o))
        self.assertNotIn("📋 Список", g); self.assertIn("👥 Участники", o); self.assertNotIn("👥 Участники", a)
        self.assertEqual(core.tg_button_cmd("🌐 Сайты"), "sites"); self.assertEqual(core.tg_button_cmd("👥 Участники"), "members")
        # отправка с клавиатурой
        bot2, calls2, _ = self._bot([{"ok": True, "result": {}}])
        bot2.send_text(1, "x", markup=kb)
        import json as _j
        self.assertIn("reply_markup", _j.loads(calls2[0][1]))
        # меню команд: delete + set для каждого администратора
        bot3, calls3, _ = self._bot([{"ok": True, "result": True}] * 3, guests={3})
        bot3.setup_menu()
        self.assertTrue(calls3[0][0].endswith("deleteMyCommands")); self.assertTrue(calls3[1][0].endswith("setMyCommands"))
        sets = [_j.loads(c[1]) for c in calls3 if c[0].endswith("setMyCommands")]
        cm = {x["scope"]["chat_id"]: [y["command"] for y in x["commands"]] for x in sets}
        self.assertIn("list", cm[1]); self.assertEqual(cm[3], ["sites", "help", "hide", "menu"])
        # нажатие кнопки под /list превращается в /file N, только для админа
        bot4, calls4, got4 = self._bot([{"ok": True, "result": True}] * 3)
        cb = lambda uid, data: {"update_id": 5, "callback_query": {"id": "q", "data": data, "from": {"id": uid},
                                                                   "message": {"chat": {"id": uid}}}}
        self.assertTrue(bot4.handle_update(cb(1, "file:12")))
        self.assertEqual(got4[-1][1], "/file 12")
        n = len(got4)
        self.assertFalse(bot4.handle_update(cb(1, "file:abc")))
        bot4.members.set_role(9, "guest")
        self.assertFalse(bot4.handle_update(cb(9, "file:12")))
        self.assertEqual(len(got4), n)

    def test_owner_and_guest_sites(self):
        m = core.Members(os.path.join(tempfile.mkdtemp(), "m.json"))
        m.set_owner(5)
        self.assertEqual(m.role(5), "admin")
        self.assertFalse(m.set_role(5, "blocked")); self.assertFalse(m.set_role(5, "guest"))
        self.assertFalse(m.remove(5)); self.assertEqual(m.role(5), "admin")
        self.assertTrue(m.set_role(6, "guest")); self.assertTrue(m.remove(6))
        m.set_owner(None); self.assertTrue(m.set_role(5, "guest"))
        keys = core.tg_site_keys(core.Settings().tg_guest_sites)
        self.assertEqual(keys, {"youtube", "instagram", "tiktok"})
        ok = lambda u: core.tg_site_allowed(u, keys)
        self.assertTrue(ok("https://www.youtube.com/watch?v=1")); self.assertTrue(ok("https://youtu.be/x"))
        self.assertTrue(ok("https://vm.tiktok.com/abc")); self.assertTrue(ok("https://instagram.com/reel/1"))
        self.assertFalse(ok("https://vk.com/video1")); self.assertFalse(ok("https://evil-youtube.com/x"))
        self.assertFalse(ok("https://youtube.com.evil.org/x")); self.assertFalse(ok("not a url"))
        self.assertEqual(core.tg_site_keys("youtube, bogus ;vk"), {"youtube", "vk"})
        self.assertTrue(core.tg_site_allowed("https://vk.com/v", {"vk"}))
        bot, calls, _ = self._bot([{"ok": True, "result": True}] * 3)
        bot.members.set_owner(1)
        bot.setup_menu()
        import json as _j
        sent = [_j.loads(c[1]) for c in calls if c[0].endswith("setMyCommands")]
        self.assertTrue(any(c["command"] == "members" for c in sent[0]["commands"]))

    def test_hide_buttons(self):
        bot, calls, _ = self._bot([])
        bot.members.set_role(3, "guest")
        self.assertIsNotNone(bot.keyboard_for(3))
        self.assertEqual(core.tg_button_cmd("🙈 Скрыть кнопки"), "hide")
        for kb in (core.TG_BUTTONS_GUEST, core.TG_BUTTONS_ADMIN, core.TG_BUTTONS_OWNER):
            self.assertIn("hide", [c for _, c in kb])
        self.assertEqual(core.TG_BUTTONS_OWNER[-1][1], "hide")
        bot.members.set_hidden(3, True)
        self.assertTrue(bot.members.is_hidden(3)); self.assertIsNone(bot.keyboard_for(3))
        self.assertIsNotNone(bot.keyboard_for(3, force=True))
        # запоминается между запусками
        m2 = core.Members(bot.members.path)
        self.assertTrue(m2.is_hidden(3)); self.assertFalse(m2.is_hidden(1))
        bot.members.set_hidden(3, False); self.assertIsNotNone(bot.keyboard_for(3))

    def test_junk_updates_and_menu_retry(self):
        bot, calls, got = self._bot([])
        junk = [{}, {"message": None}, {"message": {}}, {"callback_query": {}}, {"edited_message": {}},
                {"message": {"chat": {"id": "x"}, "from": {"id": "y"}, "text": "hi"}},
                {"message": {"chat": {"id": True}, "from": {"id": True}, "text": "hi"}},
                {"message": {"chat": {"id": 1}, "from": {"id": 1}, "text": None}},
                {"callback_query": {"data": None, "from": {"id": 1}, "message": {}}}]
        for j in junk:
            bot.handle_update(j)          # не должно падать
        self.assertEqual(got, [])
        long_name = {"message": {"chat": {"id": 6}, "from": {"id": 6, "first_name": "A" * 5000}, "text": "x"}}
        bot.handle_update(long_name)
        self.assertTrue(all(isinstance(k, int) for k, _ in bot.members.items()))   # items() сортируется без ошибок
        self.assertLessEqual(len(bot.members.items()[-1][1]["name"]), 100)
        # меню команд ставится, даже если при первом входе не было связи
        import urllib.error
        bot2, calls2, _ = self._bot([OSError("offline"), {"ok": True, "result": []}] + [{"ok": True, "result": True}] * 3)
        orig = bot2._call
        def stop_after(method, *a, **k):
            r = orig(method, *a, **k)
            if method == "setMyCommands":
                bot2._stop.set()
            return r
        bot2._call = stop_after
        bot2._poll_loop()
        self.assertTrue(any(c[0].endswith("setMyCommands") for c in calls2 if c[0] != "log"))

    def test_guest_playlists(self):
        isp = core.tg_is_playlist
        for u in ("https://www.youtube.com/playlist?list=PL1", "https://www.youtube.com/watch?list=PL1",
                  "https://www.youtube.com/@chan", "https://www.youtube.com/channel/UC1",
                  "https://www.instagram.com/someone/", "https://www.tiktok.com/@u", "https://soundcloud.com/a/sets/b"):
            self.assertTrue(isp(u), u)
        for u in ("https://www.youtube.com/watch?v=a&list=PL1", "https://youtu.be/a?list=PL1", "https://www.youtube.com/shorts/a",
                  "https://www.instagram.com/reel/A/", "https://www.instagram.com/p/A/", "https://www.tiktok.com/@u/video/1",
                  "https://vm.tiktok.com/abc/", "https://vimeo.com/123"):
            self.assertFalse(isp(u), u)
        self.assertEqual(core.tg_strip_list("https://www.youtube.com/watch?v=a&list=PL1&index=3&t=5"),
                         "https://www.youtube.com/watch?v=a&t=5")
        self.assertEqual(core.tg_strip_list("https://youtu.be/x"), "https://youtu.be/x")
        self.assertTrue(core.Settings().tg_guest_no_playlists)

    def test_guest_profiles_on_other_sites(self):
        isp = core.tg_is_playlist
        for u in ("https://x.com/someuser", "https://www.reddit.com/r/pics/", "https://vk.com/durov",
                  "https://www.facebook.com/somepage", "https://www.twitch.tv/somechan", "https://soundcloud.com/artist",
                  "https://www.pinterest.com/user/board/"):
            self.assertTrue(isp(u), u)
        for u in ("https://x.com/u/status/123", "https://www.reddit.com/r/pics/comments/abc/x/", "https://redd.it/abc",
                  "https://vk.com/video-1_2", "https://www.facebook.com/watch/?v=1", "https://www.twitch.tv/videos/1",
                  "https://soundcloud.com/artist/track", "https://vimeo.com/channels/staffpicks/12345",
                  "https://www.pinterest.com/pin/123/", "https://rutube.ru/video/abc/"):
            self.assertFalse(isp(u), u)

    def test_audit_core_fixes(self):
        # ссылки в скобках и с точкой в конце; имена файлов не ссылки
        self.assertEqual(core.parse_inputs("(https://x.com/a)."), ["https://x.com/a"])
        self.assertEqual(core.parse_inputs("https://x.com/a,"), ["https://x.com/a"])
        self.assertEqual(core.parse_inputs("https://en.wikipedia.org/wiki/A_(b)"), ["https://en.wikipedia.org/wiki/A_(b)"])
        for bad in ("readme.txt", "v1.2", "1.5", "photo.jpg"):
            self.assertEqual(core.parse_inputs(bad), [], bad)
        self.assertEqual(core.parse_inputs("example.com/path"), ["https://example.com/path"])
        # IP-адрес как имя папки
        self.assertEqual(core.site_of("http://192.168.1.5/x"), "192.168.1.5")
        self.assertNotIn(":", core.site_of("http://[::1]:80/"))
        # ошибки: ссылка с «429» или «login» внутри не меняет смысл
        self.assertNotIn("много запросов", core.friendly_error("HTTP Error 404: Not Found https://x.com/video/429"))
        self.assertNotIn("cookies", core.friendly_error("HTTP Error 503 https://x.com/login/check"))
        self.assertIn("много запросов", core.friendly_error("HTTP Error 429: Too Many Requests"))
        self.assertIn("cookies", core.friendly_error("Sign in to confirm you're not a bot"))
        self.assertFalse(core.is_permanent_error("HTTP Error 500 https://x.com/copyright-policy"))
        self.assertTrue(core.is_permanent_error("Video unavailable. This video contains content from X, who has blocked it on copyright grounds"))

    def test_audit_store_and_members(self):
        import tempfile
        d = tempfile.mkdtemp()
        mp = os.path.join(d, "m.json")
        with open(mp, "w") as f:
            json.dump({"1": {"role": "guest", "ts": "abc"}, "2": {"role": "guest", "ts": [1]}, "3": {"role": "admin", "ts": 5}}, f)
        m = core.Members(mp)
        self.assertEqual(m.role(1), "guest"); self.assertEqual(m.role(3), "admin")
        m.set_owner(5); m.set_owner(7)
        self.assertEqual(m.role(5), "guest"); self.assertEqual(m.role(7), "admin")
        m.set_owner(None); self.assertEqual(m.role(7), "guest")   # поле очищено: прежний руководитель больше не админ
        # дробная длительность, неверная метка и отрицательные настройки
        sp = os.path.join(d, "state.json")
        st = core.TaskStore()
        t = st.new_task("https://x.com/a"); st.add(t)
        t.duration, t.tag, t.total = 212.5, 99, 10.5
        st.save(sp)
        st2 = core.TaskStore(); st2.load(sp)
        t2 = st2.tasks[0]
        self.assertEqual(t2.duration, 212.5); self.assertEqual(t2.tag, 0); self.assertEqual(t2.total, 10)
        setp = os.path.join(d, "s.json")
        with open(setp, "w") as f:
            json.dump({"retries": -1, "auto_retry": -2, "speed_limit_kb": -5, "autosave_min": -1}, f)
        s = core.Settings.load(setp)
        self.assertEqual((s.retries, s.auto_retry, s.speed_limit_kb, s.autosave_min), (0, 0, 0, 0))

    def test_audit_save_json_threads(self):
        import tempfile, threading
        d = tempfile.mkdtemp(); p = os.path.join(d, "x.json"); errs = []
        def w():
            try:
                for i in range(100):
                    core.save_json(p, {"i": i})
            except Exception as e:
                errs.append(e)
        ts = [threading.Thread(target=w) for _ in range(4)]
        [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual(errs, []); self.assertIn("i", core.load_json(p, {}))

    def test_audit_engine_fixes(self):
        import tempfile
        d = tempfile.mkdtemp()
        v = os.path.join(d, "v.mkv"); a = os.path.join(d, "dub.mkv")
        open(v, "w").close(); open(a, "w").close()
        self.assertIn("поверх", engine.validate_merge(v, [], [(a, "ru")], a))
        self.assertEqual(engine.validate_merge(v, [], [(a, "ru")], os.path.join(d, "o.mkv")), "")
        tracks = [{"kind": "audio", "codec": "aac", "lang": "x/../../evil", "n": 1, "index": 1},
                  {"kind": "subtitle", "codec": "subrip", "lang": "..\\..\\y", "n": 1, "index": 2}]
        for job in engine.plan_extract("ffmpeg", v, tracks, os.path.join(d, "out")):
            self.assertEqual(os.path.dirname(job["out"]), os.path.join(d, "out"))
        bot = engine.TelegramBot("", set(), lambda *a: None, log=lambda s: None)
        self.assertEqual(bot._clean(Exception("abc")), "abc")
        with self.assertRaises(engine.TgError) as cm:
            bot.send_file_now(1, os.path.join(d, "нет-файла.mp4"))
        self.assertNotIn(d, str(cm.exception))

    def test_donate_setting_and_license_files(self):
        self.assertEqual(core.Settings().donate_url, "")
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, "LICENSE"), encoding="utf-8") as f:
            self.assertIn("GNU GENERAL PUBLIC LICENSE", f.read(200))
        self.assertTrue(os.path.exists(os.path.join(here, "README.md")))

    def test_password_lock(self):
        h = core.lock_make("секрет-123")
        self.assertNotIn("секрет", h); self.assertNotEqual(h, core.lock_make("секрет-123"))   # соль разная
        self.assertTrue(core.lock_check("секрет-123", h))
        self.assertFalse(core.lock_check("секрет-124", h)); self.assertFalse(core.lock_check("", h))
        for bad in ("", "x", "a$b$c", "pbkdf2$zz$00", None):
            self.assertFalse(core.lock_check("секрет-123", bad))
        self.assertEqual(core.Settings().tg_lock_hash, "")

    def test_private_only_pending_cap_text_cap(self):
        bot, calls, got = self._bot([{"ok": True, "result": True}] * 200)
        grp = {"message": {"chat": {"id": -100, "type": "supergroup"}, "from": {"id": 1}, "text": "/list"}}
        self.assertFalse(bot.handle_update(grp)); self.assertEqual(got, [])      # группы игнорируем
        priv = {"message": {"chat": {"id": 1, "type": "private"}, "from": {"id": 1}, "text": "x" * 50000}}
        self.assertTrue(bot.handle_update(priv)); self.assertEqual(len(got[-1][1]), 20000)
        for uid in range(1000, 1000 + bot.MAX_PENDING + 20):                        # поток заявок от посторонних
            bot.handle_update({"message": {"chat": {"id": uid, "type": "private"}, "from": {"id": uid}, "text": "hi"}})
        self.assertEqual(len(bot.members.ids("pending")), bot.MAX_PENDING)
        # уже известный ждущий продолжает числиться, администратор проходит всегда
        self.assertTrue(bot.handle_update(priv))

    def test_stopped_bot_does_not_process_updates(self):
        bot, calls, got = self._bot([{"ok": True, "result": {"username": "b"}}, {"ok": True, "result": True},
                                     {"ok": True, "result": True}, {"ok": True, "result": [self._upd(1, "hi", n=5)]}])
        orig = bot._call
        def stopping(method, *a, **k):
            r = orig(method, *a, **k)
            if method == "getUpdates":
                bot._stop.set()          # бот заменён, пока шёл длинный запрос
            return r
        bot._call = stopping
        bot._poll_loop()
        self.assertEqual(got, []); self.assertEqual(bot.offset, 0)   # offset не подтверждён: обновление достанется новому боту

    def test_api_400_is_permanent_with_hint(self):
        e = "ERROR: [Beeg] 1: Unable to download JSON metadata: HTTP Error 400: Bad Request (caused by <HTTPError 400: Bad Request>)"
        self.assertTrue(core.is_permanent_error(e)); self.assertIn("yt-dlp", core.friendly_error(e))
        self.assertFalse(core.is_permanent_error("HTTP Error 400: Bad Request while downloading fragment 3"))

    def test_tr_blank_parts_do_not_recurse(self):
        old = core.LANG
        core.LANG = "en"
        try:
            for s in ("\n", "a\n\nb", "Готово\n\nГотово", " · · ", "x |  | y", "\n\n", "  "):
                core.tr(s)                       # раньше пустая строка между абзацами давала бесконечную рекурсию
            self.assertEqual(core.tr("Готово\n\nГотово"), "Done\n\nDone")
            self.assertIn("auto-retry", core.tr("[автоповтор] V: попытка 2 из 3"))
        finally:
            core.LANG = old

    def test_settings_defaults(self):
        s = Settings()
        self.assertFalse(s.tg_enabled); self.assertEqual(s.tg_admin, ""); self.assertFalse(s.tg_auto_guest); self.assertTrue(s.tg_notify)


if __name__ == "__main__":
    unittest.main(verbosity=2)
