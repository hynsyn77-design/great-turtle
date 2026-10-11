# -*- coding: utf-8 -*-
"""Great Turtle — программа для скачивания видео, музыки и картинок (окно на PyQt5).

Запуск:  pythonw app.pyw   (или python app.pyw, чтобы видеть консоль)
"""
import collections
import os
import re
import subprocess
import sys
import threading
import time
import traceback
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt5.QtCore import (QByteArray, QEvent, QLibraryInfo, QObject, QPointF, QRect, QRectF, QSize, Qt,
                          QTimer, QTranslator, QUrl, pyqtSignal)
from PyQt5.QtGui import (QBrush, QColor, QDesktopServices, QFont, QFontMetrics, QIcon, QKeySequence,
                         QLinearGradient, QPainter, QPalette, QPen, QPixmap, QPolygonF)
from PyQt5.QtWidgets import (QAbstractItemView, QAction, QApplication, QCheckBox, QComboBox, QDialog, QGridLayout,
                             QFileDialog, QFormLayout, QFrame, QHBoxLayout, QInputDialog, QLabel,
                             QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox,
                             QProgressBar, QPushButton, QSpinBox, QStyle, QStyledItemDelegate,
                             QSystemTrayIcon, QTabBar, QTabWidget, QTextBrowser, QToolButton, QTreeWidget,
                             QTreeWidgetItem, QVBoxLayout, QWidget)

import engine as eng
import updater
from core import (APP_TITLE, APP_VERSION, AUDIO_QUALITIES, DATA_DIR, DONE, DOWNLOADING, INCOMPLETE, INVALID, MODES, QUEUED, READING,
                  TAGS, WAITING, Settings, TaskStore, compute_dups, fmt_duration, fmt_size, fmt_speed, task_size, select_label, select_total,
                  parse_chat_ids, tg_button_cmd, TG_ADMIN_CMDS, TG_OWNER_CMDS, TG_OWNER_HELP, TG_SITES, lock_make, lock_check, tg_site_keys, tg_site_allowed, tg_is_playlist, tg_strip_list, TG_GUEST_HELP, tg_parse, tg_list, TG_HELP, Members, PLAYLISTS_TAB, MUSIC_TAB, ERRORS_TAB, is_music, is_error, match_task, norm_url, parse_inputs, parse_range, safe_name, status_text,
                  display_order, task_in_tab, archive_key, friendly_error, TAB_ALL, order_tabs, split_keys, NAME_TEMPLATES, files_sigs, find_renamed_dirs, find_moved_files, apply_dir_renames, tr, tr_pairs, help_html, LANG)

ROLE_KIND = Qt.UserRole + 1
ROLE_ID = Qt.UserRole + 2
ROLE_NAME = Qt.UserRole + 3

LIGHT = dict(
    bg="#ffffff", fg="#202124", muted="#6b7075", line="#e2e2e2", accent="#0f7ad8", group="#e6ecf3",
    thumb="#d9d9d9", ok="#1e8e3e", bad="#d93025", warn="#c26a00", edit="#ffffff", edit_border="#cfcfcf",
    rows={QUEUED: "#f0f0f0", READING: "#f0f0f0", WAITING: "#f0f0f0", DOWNLOADING: "#d6d6d6",
          DONE: "#e2f3e5", INCOMPLETE: "#fde7cf", INVALID: "#fbe0dd"})
DARK = dict(
    bg="#1b1c1f", fg="#e6e6e6", muted="#9aa0a6", line="#2e3035", accent="#3b9cf0", group="#26282d",
    thumb="#3a3c41", ok="#4cc16b", bad="#ff7b72", warn="#f0a04b", edit="#25262a", edit_border="#3a3c41",
    rows={QUEUED: "#2a2b2f", READING: "#2a2b2f", WAITING: "#2a2b2f", DOWNLOADING: "#3d3f45",
          DONE: "#1f3a27", INCOMPLETE: "#47361f", INVALID: "#4a2523"})


# ======================= значки (рисуются кодом, без внешних файлов) =======================
def _canvas(size):
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    return pm, p


def make_logo(size=64):
    """Логотип: весёлая бегущая черепашка на фиолетовом круге. Рисуется кодом."""
    import math
    pm, p = _canvas(size)
    R = size / 2.0
    cx, cy = R, R

    def pt(x, y):  # координаты в долях радиуса от центра
        return QPointF(cx + x * R, cy + y * R)

    def pen(color, w):
        return QPen(QColor(color), max(1.0, w * R), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)

    g = QLinearGradient(0, 0, size, size)
    g.setColorAt(0, QColor("#5b7cfa"))
    g.setColorAt(1, QColor("#7a3fe0"))
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(g))
    p.drawEllipse(QPointF(cx, cy), R, R)

    # линии скорости
    p.setPen(pen("#ffffff", 0.07))
    for y, x1 in ((-0.5, -0.74), (-0.36, -0.84), (-0.22, -0.88)):
        p.drawLine(pt(x1, y), pt(x1 + 0.22, y))

    dark, mint = "#4fc79b", "#8af0c8"
    # дальние лапы и хвост
    p.setPen(pen(dark, 0.17))
    p.drawLine(pt(0.16, 0.1), pt(0.0, 0.5))
    p.drawLine(pt(-0.28, 0.1), pt(-0.08, 0.46))
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(dark))
    p.drawPolygon(QPolygonF([pt(-0.5, 0.0), pt(-0.82, -0.04), pt(-0.52, 0.16)]))

    # ближние лапы: шаг вперёд и назад
    p.setPen(pen(mint, 0.19))
    p.drawLine(pt(0.34, 0.1), pt(0.62, 0.44))
    p.drawLine(pt(-0.42, 0.1), pt(-0.62, 0.48))

    # шея и голова
    p.setPen(pen(mint, 0.3))
    p.drawLine(pt(0.42, -0.02), pt(0.58, -0.2))
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(mint))
    p.drawEllipse(pt(0.66, -0.24), R * 0.23, R * 0.21)
    p.setBrush(QColor("white"))
    p.drawEllipse(pt(0.73, -0.29), R * 0.085, R * 0.085)
    p.setBrush(QColor("#1d2358"))
    p.drawEllipse(pt(0.76, -0.29), R * 0.045, R * 0.045)
    p.setPen(pen("#1d2358", 0.045))  # улыбка
    p.drawLine(pt(0.62, -0.17), pt(0.7, -0.14))
    p.drawLine(pt(0.7, -0.14), pt(0.78, -0.18))

    # панцирь (купол)
    g2 = QLinearGradient(cx, cy - R * 0.5, cx, cy + R * 0.2)
    g2.setColorAt(0, QColor("#ffe27a"))
    g2.setColorAt(1, QColor("#ffb938"))
    pts = [pt(-0.06 + 0.58 * math.cos(math.pi + math.pi * k / 24.0),
              0.12 + 0.5 * math.sin(math.pi + math.pi * k / 24.0)) for k in range(25)]
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(g2))
    p.drawPolygon(QPolygonF(pts))
    p.setBrush(QColor("#e8892a"))
    p.drawPolygon(QPolygonF([pt(-0.64, 0.12), pt(0.52, 0.12), pt(0.5, 0.2), pt(-0.62, 0.2)]))  # нижний край

    p.setPen(pen("#d9801f", 0.05))
    p.setBrush(Qt.NoBrush)
    p.drawPolygon(QPolygonF([pt(-0.06, -0.3), pt(0.1, -0.2), pt(0.1, -0.02), pt(-0.06, 0.07),
                             pt(-0.22, -0.02), pt(-0.22, -0.2)]))
    for x1, y1, x2, y2 in ((-0.06, -0.3, -0.06, -0.37), (0.1, -0.2, 0.34, -0.3), (-0.22, -0.2, -0.46, -0.28),
                           (0.1, -0.02, 0.38, 0.0), (-0.22, -0.02, -0.5, 0.0)):
        p.drawLine(pt(x1, y1), pt(x2, y2))

    # ступни
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(mint))
    p.drawEllipse(pt(0.65, 0.47), R * 0.12, R * 0.09)
    p.drawEllipse(pt(-0.66, 0.51), R * 0.12, R * 0.09)
    p.setBrush(QColor(dark))
    p.drawEllipse(pt(0.0, 0.53), R * 0.1, R * 0.08)
    p.drawEllipse(pt(-0.08, 0.49), R * 0.1, R * 0.08)
    p.end()
    return pm


def icon_download(size=24, color="#ffffff"):
    pm, p = _canvas(size)
    s = float(size)
    p.setPen(QPen(QColor(color), s * 0.1, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.drawLine(QPointF(s * .5, s * .14), QPointF(s * .5, s * .6))
    p.drawPolyline(QPolygonF([QPointF(s * .3, s * .42), QPointF(s * .5, s * .62), QPointF(s * .7, s * .42)]))
    p.drawLine(QPointF(s * .2, s * .84), QPointF(s * .8, s * .84))
    p.end()
    return QIcon(pm)


def icon_link(size=16, color="#8a8f98"):
    pm, p = _canvas(size)
    s = float(size)
    p.setPen(QPen(QColor(color), s * 0.11, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.translate(s / 2, s / 2)
    p.rotate(-45)
    p.drawRoundedRect(QRectF(-s * .44, -s * .15, s * .5, s * .3), s * .15, s * .15)
    p.drawRoundedRect(QRectF(-s * .06, -s * .15, s * .5, s * .3), s * .15, s * .15)
    p.end()
    return QIcon(pm)


def icon_filter(size=16, color="#8a8f98"):
    pm, p = _canvas(size)
    s = float(size)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    p.drawPolygon(QPolygonF([QPointF(s * .1, s * .18), QPointF(s * .9, s * .18), QPointF(s * .6, s * .55),
                             QPointF(s * .6, s * .86), QPointF(s * .4, s * .74), QPointF(s * .4, s * .55)]))
    p.end()
    return QIcon(pm)


def icon_pause(size=18, color="#8a8f98"):
    pm, p = _canvas(size)
    s = float(size)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    p.drawRoundedRect(QRectF(s * .22, s * .16, s * .20, s * .68), 1.5, 1.5)
    p.drawRoundedRect(QRectF(s * .58, s * .16, s * .20, s * .68), 1.5, 1.5)
    p.end()
    return QIcon(pm)


def icon_play(size=18, color="#8a8f98"):
    pm, p = _canvas(size)
    s = float(size)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    p.drawPolygon(QPolygonF([QPointF(s * .26, s * .14), QPointF(s * .26, s * .86), QPointF(s * .84, s * .5)]))
    p.end()
    return QIcon(pm)


def thumb_rect(row, height):
    th = max(20, height - 12)
    return QRect(row.left() + 10, row.top() + 6, int(th * 1.5), th)


def set_keep_awake(on):
    if sys.platform.startswith("win"):
        try:
            import ctypes
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | (0x00000001 if on else 0))
        except Exception:
            pass


def apply_theme(app, dark):
    app.setStyle("Fusion")
    if not dark:
        app.setPalette(app.style().standardPalette())
        return
    p = QPalette()
    roles = {QPalette.Window: "#1e1f22", QPalette.WindowText: "#e6e6e6", QPalette.Base: "#25262a",
             QPalette.AlternateBase: "#2a2b2f", QPalette.Text: "#e6e6e6", QPalette.Button: "#2d2f33",
             QPalette.ButtonText: "#e6e6e6", QPalette.Highlight: "#0f7ad8",
             QPalette.HighlightedText: "#ffffff", QPalette.ToolTipBase: "#2d2f33",
             QPalette.ToolTipText: "#e6e6e6", QPalette.Link: "#5aa9ec", QPalette.BrightText: "#ffffff"}
    if hasattr(QPalette, "PlaceholderText"):
        roles[QPalette.PlaceholderText] = "#80858d"
    for role, col in roles.items():
        p.setColor(role, QColor(col))
    for role in (QPalette.Text, QPalette.WindowText, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, QColor("#6b6f76"))
    app.setPalette(p)


# ======================= шина сигналов (из потоков в GUI) =======================
def ytdlp_stale(last, now=None, days=14):
    """Пора ли напомнить об обновлении yt-dlp. Первый запуск (0) только запоминает дату, не пристаёт."""
    return bool(last) and (now or time.time()) - last > days * 86400


class Bus(QObject):
    changed = pyqtSignal(int)
    playlist = pyqtSignal(int, str, object)
    done = pyqtSignal(int)
    idle = pyqtSignal()
    log = pyqtSignal(str)
    message = pyqtSignal(str, str)
    renames = pyqtSignal(object, object)  # переименованные папки и перенесённые файлы
    fmts = pyqtSignal(int, str, object)  # id задачи, название, список форматов (или текст ошибки)
    upd = pyqtSignal(str, object, str)   # версия, файлы, ошибка (результат загрузки обновления)
    tg = pyqtSignal(object)              # запрос от Telegram-бота (выполняется в окне программы)


# ======================= поля ввода с русским меню =======================
def plain_font():
    f = QFont(QApplication.font())
    f.setBold(False)
    f.setWeight(QFont.Normal)
    return f


class RuLineEdit(QLineEdit):
    """QLineEdit с контекстным меню на русском и обычным (не жирным) шрифтом."""

    def contextMenuEvent(self, e):
        m = QMenu(self)
        m.setFont(plain_font())
        ro, sel = self.isReadOnly(), self.hasSelectedText()
        has_clip = bool(QApplication.clipboard().text())
        items = [
            (tr("Отменить\tCtrl+Z"), self.undo, not ro and self.isUndoAvailable()),
            (tr("Повторить\tCtrl+Y"), self.redo, not ro and self.isRedoAvailable()),
            None,
            (tr("Вырезать\tCtrl+X"), self.cut, not ro and sel),
            (tr("Копировать\tCtrl+C"), self.copy, sel),
            (tr("Вставить\tCtrl+V"), self.paste, not ro and has_clip),
            (tr("Удалить"), self.del_, not ro and sel),
            None,
            (tr("Выделить всё\tCtrl+A"), self.selectAll, bool(self.text())),
        ]
        for it in items:
            if it is None:
                m.addSeparator()
                continue
            a = m.addAction(it[0])
            a.setEnabled(bool(it[2]))
            a.triggered.connect(lambda _=False, f=it[1]: f())
        m.exec_(e.globalPos())


class RuTextBrowser(QTextBrowser):
    def contextMenuEvent(self, e):
        m = QMenu(self)
        m.setFont(plain_font())
        a = m.addAction(tr("Копировать\tCtrl+C"))
        a.setEnabled(self.textCursor().hasSelection())
        a.triggered.connect(lambda _=False: self.copy())
        a = m.addAction(tr("Выделить всё\tCtrl+A"))
        a.triggered.connect(lambda _=False: self.selectAll())
        m.exec_(e.globalPos())


class ThumbPopup(QLabel):
    """Крупный предпросмотр миниатюры рядом с курсором."""

    def __init__(self):
        super().__init__(None, Qt.ToolTip | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setStyleSheet("background:#111; border:2px solid #0f7ad8;")


# ======================= список задач =======================
class TaskTree(QTreeWidget):
    thumbClicked = pyqtSignal(int)
    thumbHover = pyqtSignal(int, object)
    thumbLeave = pyqtSignal()
    zoom = pyqtSignal(int)

    def __init__(self):
        super().__init__()
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.row_h = 80
        self.hint = tr("Пусто   (´･ω･`)")
        self.hint_color = QColor("#8a8f98")

    def wheelEvent(self, e):
        self.thumbLeave.emit()
        if e.modifiers() & Qt.ControlModifier:
            self.zoom.emit(1 if e.angleDelta().y() > 0 else -1)
            e.accept()
            return
        super().wheelEvent(e)

    def mouseMoveEvent(self, e):
        it = self.itemAt(e.pos())
        if (it is not None and it.data(0, ROLE_KIND) == "task"
                and thumb_rect(self.visualItemRect(it), self.row_h).contains(e.pos())):
            self.thumbHover.emit(it.data(0, ROLE_ID), e.globalPos())
        else:
            self.thumbLeave.emit()
        super().mouseMoveEvent(e)

    def viewportEvent(self, e):
        if e.type() == QEvent.Leave:
            self.thumbLeave.emit()
        return super().viewportEvent(e)

    def mousePressEvent(self, e):
        self.thumbLeave.emit()
        if e.button() == Qt.LeftButton and not (e.modifiers() & (Qt.ControlModifier | Qt.ShiftModifier)):
            it = self.itemAt(e.pos())
            if it is not None and it.data(0, ROLE_KIND) == "task":
                if thumb_rect(self.visualItemRect(it), self.row_h).contains(e.pos()):
                    self.thumbClicked.emit(it.data(0, ROLE_ID))
        super().mousePressEvent(e)

    def mouseDoubleClickEvent(self, e):
        it = self.itemAt(e.pos())
        if (e.button() == Qt.LeftButton and it is not None and it.data(0, ROLE_KIND) == "task"
                and thumb_rect(self.visualItemRect(it), self.row_h).contains(e.pos())):
            e.accept()   # первое нажатие по миниатюре уже открыло файл: второй раз не открываем
            return
        super().mouseDoubleClickEvent(e)

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.topLevelItemCount() == 0:
            p = QPainter(self.viewport())
            p.setPen(self.hint_color)
            f = QFont(self.font())
            f.setPointSizeF(f.pointSizeF() + 2)
            p.setFont(f)
            p.drawText(self.viewport().rect(), Qt.AlignCenter, self.hint)
            p.end()


class TaskDelegate(QStyledItemDelegate):
    def __init__(self, win):
        super().__init__(win.tree)
        self.win = win

    def sizeHint(self, option, index):
        if index.data(ROLE_KIND) == "group":
            return QSize(100, 32)
        return QSize(100, self.win.tree.row_h)

    def paint(self, p, option, index):
        kind = index.data(ROLE_KIND)
        if kind == "task":
            self._task(p, option, index)
        elif kind == "group":
            self._group(p, option, index)
        else:
            super().paint(p, option, index)

    def _group(self, p, option, index):
        th, r = self.win.theme, option.rect
        name = index.data(ROLE_NAME) or ""
        done, total, prog, gbytes, gunk = self.win.group_stats.get(name, (0, 0, 0.0, 0, 0))
        p.save()
        p.fillRect(r, QColor(th["group"]))
        if option.state & QStyle.State_Selected:
            ov = QColor(th["accent"])
            ov.setAlpha(70)
            p.fillRect(r, ov)
        p.setPen(QColor(th["line"]))
        p.drawLine(r.left(), r.bottom(), r.right(), r.bottom())
        f = QFont(option.font)
        f.setBold(True)
        p.setFont(f)
        right = f"{done} / {total}"
        if gbytes:
            right = ("≥ " if gunk else "") + fmt_size(gbytes) + "  ·  " + right
        rw = QFontMetrics(f).horizontalAdvance(right) + 12
        p.setPen(QColor(th["muted"]))
        p.drawText(QRect(r.right() - rw, r.top(), rw - 8, r.height()), Qt.AlignVCenter | Qt.AlignRight, right)
        p.setPen(QColor(th["fg"]))
        w = max(10, r.width() - rw - 20)
        p.drawText(QRect(r.left() + 8, r.top(), w, r.height()), Qt.AlignVCenter | Qt.AlignLeft,
                   QFontMetrics(f).elidedText(name, Qt.ElideRight, w))
        if total:
            bar = QRect(r.left(), r.bottom() - 2, int(r.width() * prog / total), 3)
            p.fillRect(bar, QColor(th["accent"]))
        p.restore()

    def _task(self, p, option, index):
        t = self.win.store.get(index.data(ROLE_ID))
        if t is None:
            return
        th, r = self.win.theme, option.rect
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(r, QColor(th["rows"].get(t.state, th["rows"][QUEUED])))
        if option.state & QStyle.State_Selected:
            ov = QColor(th["accent"])
            ov.setAlpha(70)
            p.fillRect(r, ov)
        p.setPen(QColor(th["line"]))
        p.drawLine(r.left(), r.bottom(), r.right(), r.bottom())
        if t.tag:
            p.fillRect(QRect(r.left(), r.top(), 5, r.height()), QColor(TAGS[t.tag][2]))

        trect = thumb_rect(r, r.height())
        pix = self.win.thumb_pixmap(t, trect.width(), trect.height())
        if pix:
            p.drawPixmap(trect.topLeft(), pix)
        else:
            p.fillRect(trect, QColor(th["thumb"]))
            p.setPen(QColor(th["muted"]))
            p.drawText(trect, Qt.AlignCenter, (t.site[:1] or "?").upper())

        x = trect.right() + 12
        w = max(20, r.right() - 12 - x)
        f_title = QFont(option.font)
        f_title.setBold(True)
        f_small = QFont(option.font)
        f_small.setPointSizeF(max(7.0, option.font.pointSizeF() - 1))
        fm_t, fm_s = QFontMetrics(f_title), QFontMetrics(f_small)
        lh = fm_t.height() + 1
        y = r.top() + max(4, (r.height() - 3 * lh) // 2)

        title = t.display_title
        if t.artist:
            title = f"[{t.artist}] {title}"
        p.setFont(f_title)
        p.setPen(QColor(th["fg"]))
        p.drawText(QRect(x, y, w, lh), Qt.AlignVCenter | Qt.AlignLeft, fm_t.elidedText(title, Qt.ElideRight, w))

        size = task_size(t)
        meta = [t.site, fmt_size(size) if size else "", fmt_duration(t.duration),
                time.strftime("%d.%m.%Y", time.localtime(t.added)) if t.added else ""]
        meta = "  ·  ".join(m for m in meta if m)
        p.setFont(f_small)
        p.setPen(QColor(th["muted"]))
        p.drawText(QRect(x, y + lh, w, lh), Qt.AlignVCenter | Qt.AlignLeft, fm_s.elidedText(meta, Qt.ElideRight, w))

        colors = {DOWNLOADING: th["accent"], DONE: th["ok"], INVALID: th["bad"], INCOMPLETE: th["warn"]}
        st = status_text(t, self.win.engine.paused.is_set())
        st_w = fm_s.horizontalAdvance(st)
        p.setPen(QColor(colors.get(t.state, th["muted"])))
        p.drawText(QRect(x, y + 2 * lh, w, lh), Qt.AlignVCenter | Qt.AlignLeft, fm_s.elidedText(st, Qt.ElideRight, w))
        if t.comment and st_w + 30 < w:
            p.setPen(QColor(th["muted"]))
            p.drawText(QRect(x + st_w + 14, y + 2 * lh, w - st_w - 14, lh), Qt.AlignVCenter | Qt.AlignLeft,
                       fm_s.elidedText("— " + t.comment, Qt.ElideRight, w - st_w - 14))
        if t.state == DOWNLOADING:
            p.fillRect(QRect(x, r.bottom() - 6, w, 3), QColor(th["line"]))
            p.fillRect(QRect(x, r.bottom() - 6, int(w * t.progress), 3), QColor(th["accent"]))
        p.restore()


# ======================= диалоги =======================
class TextDialog(QDialog):
    def __init__(self, parent, title, text, html=False, size=(620, 520)):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(*size)
        lay = QVBoxLayout(self)
        view = RuTextBrowser()
        view.setOpenExternalLinks(True)
        if html:
            view.setHtml(text)
        else:
            view.setPlainText(text)
        lay.addWidget(view)
        btn = QPushButton(tr("Закрыть"))
        btn.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(btn)
        lay.addLayout(row)


class MergeDialog(QDialog):
    """Добавляет к готовому видео внешние субтитры и звуковые дорожки. Видео не перекодируется (быстро, без потерь)."""
    finished_ = pyqtSignal(str, str)   # (путь результата, ошибка; "cancel" — отменено)

    def __init__(self, parent, video="", start_dir=""):
        super().__init__(parent)
        self.setWindowTitle(tr("Объединить видео с субтитрами и звуком"))
        self.resize(760, 640)
        self.holder, self.running, self.result_path = {}, False, ""
        self.start_dir = start_dir or os.path.expanduser("~")
        lay = QVBoxLayout(self)
        intro = QLabel(tr("Из видео, субтитров и звуковых дорожек собирается новый файл. Видео не перекодируется, "
                       "исходные файлы не меняются. Язык берётся из имени файла (например, video.ru.srt), "
                       "его можно поправить."))
        intro.setWordWrap(True)
        lay.addWidget(intro)
        self.video = QLineEdit(video)
        lay.addLayout(self._row(tr("Видео:"), self.video, self.pick_video))
        self.subs = self._track_box(lay, tr("Субтитры"), tr("Файлы субтитров (*.srt *.ass *.ssa *.vtt);;Все файлы (*)"))
        self.audios = self._track_box(lay, tr("Звуковые дорожки"),
                                      tr("Звук (*.mp3 *.m4a *.aac *.opus *.ogg *.flac *.wav *.mka *.ac3);;Все файлы (*)"))
        self.out = QLineEdit(eng.default_merge_output(video) if video else "")
        lay.addLayout(self._row(tr("Сохранить как:"), self.out, self.pick_out))
        self.status = QLabel("")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        self.bar.hide()
        lay.addWidget(self.bar)
        row = QHBoxLayout()
        self.show_btn = QPushButton(tr("Показать файл в папке"))
        self.show_btn.clicked.connect(self.show_result)
        self.show_btn.hide()
        row.addWidget(self.show_btn)
        row.addStretch(1)
        self.go = QPushButton(tr("Объединить"))
        self.go.setDefault(True)
        self.go.clicked.connect(self.start)
        self.close_btn = QPushButton(tr("Закрыть"))
        self.close_btn.clicked.connect(self.reject)
        row.addWidget(self.go)
        row.addWidget(self.close_btn)
        lay.addLayout(row)
        self.finished_.connect(self.on_finished)

    def _row(self, label, edit, fn):
        row = QHBoxLayout()
        lb = QLabel(label)
        lb.setMinimumWidth(110)
        row.addWidget(lb)
        row.addWidget(edit, 1)
        b = QPushButton(tr("Выбрать..."))
        b.clicked.connect(fn)
        row.addWidget(b)
        return row

    def _track_box(self, lay, title, flt):
        lay.addWidget(QLabel(f"<b>{title}</b>"))
        lst = QListWidget()
        lst.setMinimumHeight(90)
        lay.addWidget(lst, 1)
        row = QHBoxLayout()
        for text, fn in ((tr("Добавить..."), lambda: self.add_files(lst, title, flt)),
                         (tr("Изменить язык..."), lambda: self.edit_lang(lst)),
                         (tr("Убрать"), lambda: self.remove_item(lst))):
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, f=fn: f())
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        lst.itemDoubleClicked.connect(lambda _it: self.edit_lang(lst))
        return lst

    @staticmethod
    def _label(path, lang):
        return tr(f"{os.path.basename(path)}   —   язык: {lang or 'не указан'}")

    def add_files(self, lst, title, flt):
        paths, _ = QFileDialog.getOpenFileNames(self, title, self.start_dir, flt)
        for p in paths:
            self.start_dir = os.path.dirname(p) or self.start_dir
            lang = eng.guess_track_lang(p)
            it = QListWidgetItem(self._label(p, lang))
            it.setData(Qt.UserRole, (p, lang))
            lst.addItem(it)

    def edit_lang(self, lst):
        it = lst.currentItem()
        if it is None:
            return
        p, lang = it.data(Qt.UserRole)
        text, ok = QInputDialog.getText(self, tr("Язык дорожки"),
                                        tr("Код языка (ru, en, de, fr...). Пусто — не указывать:"), text=lang)
        if ok:
            text = text.strip().lower()
            text = eng._norm_lang(text) if text else ""
            it.setData(Qt.UserRole, (p, text))
            it.setText(self._label(p, text))

    def remove_item(self, lst):
        for it in lst.selectedItems():
            lst.takeItem(lst.row(it))

    def pick_video(self):
        p, _ = QFileDialog.getOpenFileName(self, tr("Видео"), self.start_dir,
                                           tr("Видео (*.mp4 *.mkv *.webm *.mov *.m4v *.avi);;Все файлы (*)"))
        if p:
            self.video.setText(p)
            self.start_dir = os.path.dirname(p) or self.start_dir
            self.out.setText(eng.default_merge_output(p))

    def pick_out(self):
        p, _ = QFileDialog.getSaveFileName(self, tr("Сохранить как"), self.out.text() or self.start_dir,
                                           tr("Видео (*.mkv *.mp4)"))
        if p:
            if os.path.splitext(p)[1].lower() not in (".mkv", ".mp4"):
                p += ".mkv"
            self.out.setText(p)

    @staticmethod
    def _items(lst):
        return [lst.item(i).data(Qt.UserRole) for i in range(lst.count())]

    def set_busy(self, busy):
        self.running = busy
        self.go.setEnabled(not busy)
        self.bar.setVisible(busy)
        self.show_btn.hide()

    def start(self):
        video, out = self.video.text().strip(), self.out.text().strip()
        subs, audios = self._items(self.subs), self._items(self.audios)
        err = eng.validate_merge(video, subs, audios, out)
        if err:
            QMessageBox.warning(self, tr("Объединение"), err)
            return
        if os.path.exists(out) and QMessageBox.question(
                self, tr("Файл уже есть"), tr(f"Файл уже существует:\n{out}\n\nЗаменить его?")) != QMessageBox.Yes:
            return
        self.holder = {}
        self.set_busy(True)
        self.status.setText(tr("Идёт сборка. Для больших файлов это может занять минуту-две..."))

        def run():
            try:
                self.finished_.emit(eng.merge_tracks(video, subs, audios, out, self.holder), "")
            except eng.Cancelled:
                self.finished_.emit("", "cancel")
            except Exception as ex:
                self.finished_.emit("", str(ex))
        threading.Thread(target=run, daemon=True).start()

    def on_finished(self, path, err):
        self.set_busy(False)
        if err == "cancel":
            self.status.setText(tr("Отменено."))
        elif err:
            self.status.setText(tr("Не получилось."))
            QMessageBox.critical(self, tr("Объединение"), err)
        else:
            self.result_path = path
            self.status.setText(tr(f"Готово: {path}"))
            self.show_btn.show()

    def show_result(self):
        cmd = eng.reveal_command(self.result_path) if self.result_path else None
        try:
            if cmd:
                subprocess.Popen(cmd)
                return
        except Exception:
            pass
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(self.result_path)))

    def reject(self):
        if self.running:
            if QMessageBox.question(self, tr("Объединение"), tr("Остановить сборку?")) == QMessageBox.Yes:
                self.holder["cancel"] = True
                proc = self.holder.get("proc")
                if proc is not None:
                    try:
                        proc.kill()
                    except Exception:
                        pass
            return
        super().reject()


class ExtractDialog(QDialog):
    """Достаёт из видео отдельные звуковые дорожки и субтитры в файлы."""
    finished_ = pyqtSignal(object, object, str)   # (готовые файлы, ошибки, "cancel" / текст сбоя)
    probed = pyqtSignal(object, str)              # (дорожки, ошибка)
    progress_ = pyqtSignal(int, int)

    def __init__(self, parent, video="", start_dir=""):
        super().__init__(parent)
        self.setWindowTitle(tr("Извлечь звук и субтитры из видео"))
        self.resize(760, 620)
        self.holder, self.running, self.tracks, self.done_dir = {}, False, [], ""
        self.start_dir = start_dir or os.path.expanduser("~")
        lay = QVBoxLayout(self)
        intro = QLabel(tr("Выберите видео: программа покажет все звуковые дорожки и субтитры внутри. Отметьте нужные, "
                       "и каждая сохранится отдельным файлом (например, video.ru.srt, video.en.m4a). "
                       "Звук по умолчанию не перекодируется."))
        intro.setWordWrap(True)
        lay.addWidget(intro)
        self.video = QLineEdit(video)
        row = QHBoxLayout()
        lb = QLabel(tr("Видео:"))
        lb.setMinimumWidth(110)
        row.addWidget(lb)
        row.addWidget(self.video, 1)
        b = QPushButton(tr("Выбрать..."))
        b.clicked.connect(self.pick_video)
        row.addWidget(b)
        lay.addLayout(row)
        self.list = QListWidget()
        lay.addWidget(self.list, 1)
        row = QHBoxLayout()
        for text, state in ((tr("Выбрать все"), Qt.Checked), (tr("Снять все"), Qt.Unchecked)):
            bb = QPushButton(text)
            bb.clicked.connect(lambda _=False, s=state: self.set_all(s))
            row.addWidget(bb)
        row.addStretch(1)
        row.addWidget(QLabel(tr("Звук:")))
        self.mode = QComboBox()
        self.mode.addItem(tr("как есть, без перекодирования"), "copy")
        self.mode.addItem(tr("перекодировать в MP3 320"), "mp3")
        row.addWidget(self.mode)
        lay.addLayout(row)
        self.out = QLineEdit(os.path.dirname(video) if video else "")
        row = QHBoxLayout()
        lb = QLabel(tr("Сохранить в папку:"))
        lb.setMinimumWidth(110)
        row.addWidget(lb)
        row.addWidget(self.out, 1)
        b = QPushButton(tr("Выбрать..."))
        b.clicked.connect(self.pick_out)
        row.addWidget(b)
        lay.addLayout(row)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        self.bar = QProgressBar()
        self.bar.hide()
        lay.addWidget(self.bar)
        row = QHBoxLayout()
        self.open_btn = QPushButton(tr("Открыть папку"))
        self.open_btn.clicked.connect(self.open_result)
        self.open_btn.hide()
        row.addWidget(self.open_btn)
        row.addStretch(1)
        self.go = QPushButton(tr("Извлечь"))
        self.go.setDefault(True)
        self.go.setEnabled(False)
        self.go.clicked.connect(self.start)
        self.close_btn = QPushButton(tr("Закрыть"))
        self.close_btn.clicked.connect(self.reject)
        row.addWidget(self.go)
        row.addWidget(self.close_btn)
        lay.addLayout(row)
        self.probed.connect(self.on_probed)
        self.finished_.connect(self.on_finished)
        self.progress_.connect(self.on_progress)
        if video:
            self.load(video)

    def pick_video(self):
        p, _ = QFileDialog.getOpenFileName(self, tr("Видео"), self.start_dir,
                                           tr("Видео (*.mp4 *.mkv *.webm *.mov *.m4v *.avi *.ts);;Все файлы (*)"))
        if p:
            self.start_dir = os.path.dirname(p) or self.start_dir
            self.video.setText(p)
            self.out.setText(os.path.dirname(p))
            self.load(p)

    def pick_out(self):
        p = QFileDialog.getExistingDirectory(self, tr("Куда сохранить"), self.out.text() or self.start_dir)
        if p:
            self.out.setText(p)

    def load(self, path):
        self.list.clear()
        self.tracks = []
        self.go.setEnabled(False)
        self.status.setText(tr("Читаю дорожки..."))

        self._probe_path = path

        def run():
            try:
                res, err = eng.probe_tracks(path), ""
            except Exception as ex:
                res, err = [], str(ex)
            if self._probe_path == path:   # пока читали этот файл, могли выбрать другой — старый ответ не нужен
                self.probed.emit(res, err)
        threading.Thread(target=run, daemon=True).start()

    def on_probed(self, tracks, err):
        self.list.clear()
        if err:
            self.status.setText(err)
            return
        self.tracks = tracks
        for t in tracks:
            it = QListWidgetItem(eng.track_label(t, tr))
            ok = eng.track_supported(t)
            it.setFlags((it.flags() | Qt.ItemIsUserCheckable) if ok else (it.flags() & ~Qt.ItemIsEnabled))
            it.setCheckState(Qt.Checked if ok else Qt.Unchecked)
            it.setData(Qt.UserRole, t)
            self.list.addItem(it)
        self.go.setEnabled(bool(tracks))
        self.status.setText(tr(f"Найдено дорожек: {len(tracks)}") if tracks
                            else tr("В этом файле нет звуковых дорожек и субтитров."))

    def set_all(self, state):
        for i in range(self.list.count()):
            if self.list.item(i).flags() & Qt.ItemIsEnabled:
                self.list.item(i).setCheckState(state)

    def start(self):
        video, out_dir = self.video.text().strip(), self.out.text().strip()
        chosen = [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())
                  if self.list.item(i).checkState() == Qt.Checked]
        if not os.path.isfile(video):
            QMessageBox.warning(self, tr("Извлечение"), tr("Выберите видеофайл."))
            return
        if not chosen:
            QMessageBox.warning(self, tr("Извлечение"), tr("Отметьте хотя бы одну дорожку."))
            return
        if not out_dir:
            QMessageBox.warning(self, tr("Извлечение"), tr("Укажите папку для результата."))
            return
        ff = eng.find_ffmpeg()
        if not ff:
            QMessageBox.warning(self, tr("Извлечение"), tr("Не найден ffmpeg. Запустите install.bat или установите ffmpeg."))
            return
        try:
            os.makedirs(out_dir, exist_ok=True)
        except OSError as ex:
            QMessageBox.warning(self, tr("Извлечение"), tr(f"Не удалось создать папку: {ex}"))
            return
        jobs = eng.plan_extract(ff, video, chosen, out_dir, self.mode.currentData())
        self.holder, self.running, self.done_dir = {}, True, out_dir
        self.go.setEnabled(False)
        self.open_btn.hide()
        self.bar.setRange(0, len(jobs))
        self.bar.setValue(0)
        self.bar.show()
        self.status.setText(tr("Извлекаю..."))

        def run():
            try:
                done, errs = eng.extract_tracks(jobs, self.holder, lambda i, n: self.progress_.emit(i, n))
                self.finished_.emit(done, errs, "")
            except eng.Cancelled:
                self.finished_.emit([], [], "cancel")
            except Exception as ex:
                self.finished_.emit([], [], str(ex))
        threading.Thread(target=run, daemon=True).start()

    def on_progress(self, i, n):
        self.bar.setRange(0, n)
        self.bar.setValue(i)

    def on_finished(self, done, errs, fail):
        self.running = False
        self.bar.hide()
        self.go.setEnabled(True)
        if fail == "cancel":
            self.status.setText(tr("Отменено."))
            return
        if fail:
            self.status.setText(tr("Не получилось."))
            QMessageBox.critical(self, tr("Извлечение"), fail)
            return
        text = tr(f"Готово: {len(done)} файл(ов) в папке {self.done_dir}")
        if errs:
            text += tr("\nНе удалось:\n") + "\n".join(errs)
        self.status.setText(text)
        if done:
            self.open_btn.show()

    def open_result(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(self.done_dir))

    def reject(self):
        if self.running:
            if QMessageBox.question(self, tr("Извлечение"), tr("Остановить?")) == QMessageBox.Yes:
                self.holder["cancel"] = True
                proc = self.holder.get("proc")
                if proc is not None:
                    try:
                        proc.kill()
                    except Exception:
                        pass
            return
        super().reject()


class FormatsDialog(QDialog):
    """Список форматов ролика: выбираем строку — скачается именно она (видео без звука — со звуком)."""

    def __init__(self, parent, title, rows, current=""):
        super().__init__(parent)
        self.setWindowTitle(tr("Выбор формата — ") + title)
        self.resize(780, 520)
        self.rows, self.result_sel = rows, None
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(tr("Выберите строку и нажмите «Скачать». Если у формата нет звука, лучший звук добавится сам.")))
        self.tree = QTreeWidget()
        self.tree.setRootIsDecorated(False)
        self.tree.setHeaderLabels([tr("Код"), tr("Тип"), tr("Разрешение"), tr("Кодеки"), tr("Битрейт"), tr("Размер"), tr("Примечание")])
        kinds = {"both": tr("видео + звук"), "video": tr("видео"), "audio": tr("звук")}
        for r in rows:
            codecs = " + ".join(x for x in (r["vcodec"], r["acodec"]) if x)
            res = r["res"] + (tr(f" {r['fps']} к/с") if r["fps"] > 30 else "")
            it = QTreeWidgetItem([r["id"], f"{kinds[r['kind']]} · {r['ext']}", res, codecs,
                                  tr(f"{r['rate']} кбит/с") if r["rate"] else "",
                                  ("~" + fmt_size(r["size"])) if r["size"] else "", r["note"]])
            it.setData(0, Qt.UserRole, r["sel"])
            self.tree.addTopLevelItem(it)
            if current and r["sel"] == current:
                self.tree.setCurrentItem(it)
        for i in range(6):
            self.tree.resizeColumnToContents(i)
        self.tree.itemDoubleClicked.connect(lambda *_: self.accept())
        lay.addWidget(self.tree)
        row = QHBoxLayout()
        auto = QPushButton(tr("Как в настройках (сбросить выбор)"))
        auto.clicked.connect(self.pick_auto)
        row.addWidget(auto)
        row.addStretch(1)
        ok, cancel = QPushButton(tr("Скачать")), QPushButton(tr("Отмена"))
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        row.addWidget(ok)
        row.addWidget(cancel)
        lay.addLayout(row)

    def pick_auto(self):
        self.result_sel = ""
        self.accept()

    def chosen(self):
        """Селектор формата; пустая строка — вернуть выбор по режиму; None — ничего не выбрано."""
        if self.result_sel is not None:
            return self.result_sel
        it = self.tree.currentItem()
        return it.data(0, Qt.UserRole) if it else None


class SelectDialog(QDialog):
    """Выбор глав / роликов плейлиста перед скачиванием. done — номера уже скачанных (помечаются галочкой ✓)."""

    def __init__(self, parent, title, items, heading=tr("Выбор глав"), done=(), sizes=None, durations=None, probe=None):
        super().__init__(parent)
        self.sizes, self.durations, self.probe = dict(sizes or {}), dict(durations or {}), probe
        self.texts, self.done_ = {i: t for i, t in items}, set(done)
        self._got, self._want, self._probing, self._stop, self._closed = 0, 0, False, False, False
        self.setWindowTitle(tr(heading) + " — " + title)
        self.resize(620, 560)
        lay = QVBoxLayout(self)
        self.counter = QLabel()
        lay.addWidget(self.counter)
        self.list = QListWidget()
        for idx, text in items:
            mark = "✓ " if idx in done else ""
            it = QListWidgetItem(select_label(idx, text, self.sizes.get(idx, 0), self.durations.get(idx, 0), idx in done))
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked)
            it.setData(Qt.UserRole, idx)
            if idx in done:
                it.setToolTip(tr("Уже скачано раньше: файл не будет скачан заново, а подключится к этому списку"))
            self.list.addItem(it)
        self.list.itemChanged.connect(lambda *_: self.update_counter())
        lay.addWidget(self.list)
        if done:
            lay.addWidget(QLabel(tr("✓ — уже скачано раньше (повторно не скачивается)")))
        row = QHBoxLayout()
        for text, state in ((tr("Выбрать все"), Qt.Checked), (tr("Снять все"), Qt.Unchecked)):
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, s=state: self.set_all(s))
            row.addWidget(b)
        if probe is not None:
            self.size_btn = QPushButton(tr("Узнать размеры"))
            self.size_btn.clicked.connect(self.start_probe)
            row.addWidget(self.size_btn)
        row.addStretch(1)
        ok, cancel = QPushButton(tr("Скачать")), QPushButton(tr("Отмена"))
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        row.addWidget(ok)
        row.addWidget(cancel)
        lay.addLayout(row)
        self.update_counter()
        if probe is not None and 0 < sum(1 for i in self.texts if not self.sizes.get(i)) <= 60:
            QTimer.singleShot(200, self.start_probe)   # у больших плейлистов — только по кнопке: сотни запросов подряд могут вызвать блокировку сайта   # размеры подтягиваются сами, как только окно открылось

    def update_counter(self):
        ch = self.chosen()
        text = tr(f"Выбрано: {len(ch)} из {self.list.count()}")
        total, unknown = select_total(ch, self.sizes)
        if total:
            text += "    " + (tr(f"Общий вес: не меньше {fmt_size(total)}") if unknown
                              else tr(f"Общий вес: {fmt_size(total)}"))
        if self._probing:
            text += "    " + tr(f"Узнаю размеры: {self._got} из {self._want}")
        self.counter.setText(text)

    def refresh_item(self, idx):
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.data(Qt.UserRole) == idx:
                it.setText(select_label(idx, self.texts[idx], self.sizes.get(idx, 0),
                                        self.durations.get(idx, 0), idx in self.done_))
                break

    def start_probe(self):
        """Размеры в списке плейлиста обычно неизвестны: спрашиваем у сайта по одному ролику в фоне."""
        if self._probing or self.probe is None or self._closed:
            return
        todo = [i for i in self.texts if not self.sizes.get(i)]
        if not todo:
            return
        self._probing, self._got, self._want = True, 0, len(todo)
        self.size_btn.setEnabled(False)
        self._stop = False
        self._results, self._lock = [], threading.Lock()

        def work(chunk):
            for i in chunk:
                if self._stop:
                    return
                try:
                    n = int(self.probe(i) or 0)
                except Exception:
                    n = 0
                with self._lock:
                    self._results.append((i, n))
        for k in range(min(3, len(todo))):
            threading.Thread(target=work, args=(todo[k::3],), daemon=True).start()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.collect_probe)
        self._timer.start(300)

    def collect_probe(self):
        with self._lock:
            res, self._results = self._results, []
        for i, n in res:
            self._got += 1
            if n:
                self.sizes[i] = n
                self.refresh_item(i)
        if self._got >= self._want:
            self._timer.stop()
            self._probing = False
            self.size_btn.setEnabled(True)
        self.update_counter()

    def done(self, r):
        self._stop = self._closed = True
        t = getattr(self, "_timer", None)
        if t is not None:
            t.stop()
        super().done(r)

    def set_all(self, state):
        for i in range(self.list.count()):
            self.list.item(i).setCheckState(state)

    def chosen(self):
        return [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.Checked]


class MembersDialog(QDialog):
    """Участники Telegram-бота: заявки и гости. Решения применяются сразу (бот читает ту же таблицу)."""
    ROLE_TEXT = {"admin": tr("администратор"), "guest": tr("гость"), "pending": tr("заявка"), "blocked": tr("заблокирован")}

    def __init__(self, parent, members, on_change=None):
        super().__init__(parent)
        self.members, self.on_change = members, on_change
        self.setWindowTitle(tr("Участники Telegram"))
        self.resize(560, 420)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(tr("Заявки ждут вашего решения.")))
        self.list = QListWidget()
        lay.addWidget(self.list)
        row = QHBoxLayout()
        for text, role in ((tr("Разрешить (гость)"), "guest"), (tr("Сделать администратором"), "admin"),
                           (tr("Заблокировать"), "blocked")):
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, r=role: self.set_role(r))
            row.addWidget(b)
        rm = QPushButton(tr("Удалить"))
        rm.clicked.connect(self.remove)
        row.addWidget(rm)
        row.addStretch(1)
        close = QPushButton(tr("Закрыть"))
        close.clicked.connect(self.accept)
        row.addWidget(close)
        lay.addLayout(row)
        self.fill()

    def fill(self):
        self.list.clear()
        for uid, m in self.members.items():
            if uid == self.members.owner:
                continue   # руководитель в списке не показывается
            who = (m["name"] or tr("Без имени")) + (f" (@{m['username']})" if m["username"] else "")
            role = tr(self.ROLE_TEXT.get(m['role'], m['role']))
            it = QListWidgetItem(f"{uid}  —  {who}  —  {role}")
            it.setData(Qt.UserRole, uid)
            self.list.addItem(it)

    def current(self):
        it = self.list.currentItem()
        return it.data(Qt.UserRole) if it is not None else None

    def set_role(self, role):
        uid = self.current()
        if uid is None:
            return
        if not self.members.set_role(uid, role):
            QMessageBox.information(self, "Great Turtle", tr("Руководителя изменить нельзя."))
            return
        self.fill()
        if self.on_change:
            self.on_change(uid, role)

    def remove(self):
        uid = self.current()
        if uid is not None:
            if not self.members.remove(uid):
                QMessageBox.information(self, "Great Turtle", tr("Руководителя изменить нельзя."))
                return
            self.fill()


class SettingsDialog(QDialog):
    def __init__(self, parent, s):
        super().__init__(parent)
        self.s = s
        self.w = []
        self.setWindowTitle(tr("Параметры"))
        self.resize(580, 500)
        lay = QVBoxLayout(self)
        tabs = QTabWidget()
        lay.addWidget(tabs)

        # --- основные ---
        f = self.page(tabs, tr("Основные"))
        self.path_row(f, tr("Папка загрузки"), "folder", folder=True)
        self.combo(f, tr("Формат видео"), "mode", tr_pairs(MODES))
        self.template_row(f)
        self.spin(f, tr("Одновременных задач"), "max_tasks", 1, 16)
        self.spin(f, tr("Лимит скорости, КБ/с (0 — без лимита)"), "speed_limit_kb", 0, 10 ** 6)
        self.check(f, tr("Для ссылок «видео из плейлиста» скачивать весь плейлист"), "playlist_from_video_url")
        self.check(f, tr("Спрашивать, что скачать, при загрузке плейлиста (окно выбора роликов)"), "ask_playlist")
        self.check(f, tr("Использовать gallery-dl для сайтов с картинками/галереями"), "use_gallerydl")
        self.check(f, tr("Не качать повторно уже скачанное (архив; работает, пока файл на месте)"), "use_archive")
        self.check(f, tr("Вшивать обложку в музыкальные файлы (режим «Только звук»)"), "embed_cover")
        self.check(f, tr("Обновлять yt-dlp до тестовых версий (раньше получает исправления, но менее стабильно)"), "ytdlp_pre")
        self.check(f, tr("Добавлять к оригинальной дорожке дубляж (если он есть)"), "all_audio")
        self.line(f, tr("Языки дубляжа через запятую, например ru,de (пусто — все)"), "audio_langs")
        self.line(f, tr("Ещё сайты, где искать дубляж (через запятую, например vimeo, example.com)"), "dub_sites")
        self.combo(f, tr("Качество звука (режим «Только звук»)"), "audio_quality", tr_pairs(AUDIO_QUALITIES))
        self.check(f, tr("Раскладывать загрузки по папкам сайтов (youtube, vk...)"), "site_folders")
        self.combo(f, tr("Субтитры"), "subs_mode",
                   [("", tr("Не скачивать")), ("file", tr("Отдельными файлами (.srt)")), ("embed", tr("Вшить в видео"))])
        self.line(f, tr("Языки субтитров через запятую (пусто — все)"), "subs_langs")
        self.check(f, tr("Брать и автоматические субтитры (распознанные YouTube)"), "subs_auto")

        # --- сеть ---
        f = self.page(tabs, tr("Сеть"))
        self.spin(f, tr("Потоков на одну задачу (1–24)"), "threads", 1, 24)
        self.spin(f, tr("Повторов при ошибке"), "retries", 0, 50)
        self.spin(f, tr("Сколько раз программа сама перезапустит загрузку после ошибки (0 — не перезапускать)"), "auto_retry", 0, 10)
        self.line(f, tr("Прокси (например http://127.0.0.1:8080)"), "proxy")
        self.path_row(f, tr("Файл cookies (Netscape .txt)"), "cookies_file", folder=False)
        self.combo(f, tr("Cookies из браузера"), "cookies_browser",
                   [("", tr("Не использовать")), ("firefox", "Firefox"), ("chrome", "Chrome"), ("edge", "Edge"),
                    ("chromium", "Chromium"), ("brave", "Brave"), ("opera", "Opera"), ("operagx", "Opera GX"), ("vivaldi", "Vivaldi")])
        self.check(f, tr("Имитировать браузер (нужен curl-cffi)"), "impersonate")

        # --- вид ---
        f = self.page(tabs, tr("Внешний вид"))
        self.check(f, tr("Тёмный режим"), "dark")
        self.spin(f, tr("Размер миниатюры (44–140)"), "thumb_size", 44, 140)

        # --- прочее ---
        f = self.page(tabs, tr("Прочее"))
        self.spin(f, tr("Автосохранение, мин (0 — выключено)"), "autosave_min", 0, 240)
        self.line(f, tr("Адрес обновления программы (https://…zip, необязательно)"), "update_url")
        self.line(f, tr("Ссылка «Поддержать разработку» для окна «О программе» (https://…, необязательно)"), "donate_url")
        self.check(f, tr("Уведомлять о завершении каждой загрузки"), "notify_done")
        self.check(f, tr("Уведомлять, когда все задачи выполнены"), "notify_all_done")
        self.combo(f, tr("Когда все задачи выполнены"), "after_all",
                   [("nothing", tr("Ничего не делать")), ("exit", tr("Закрыть программу"))])
        self.check(f, tr("Не давать системе уснуть во время загрузки (Windows)"), "prevent_sleep")
        self.check(f, tr("Автоскачивание: скопированные ссылки сразу добавляются и скачиваются"), "clipboard_monitor")
        self.check(f, tr("Продолжать незавершённые задачи при запуске"), "resume_on_start")
        self.check(f, tr("Убирать завершённые задачи из списка"), "auto_remove_done")

        # --- Telegram ---
        f = self.page(tabs, "Telegram")
        self.check(f, tr("Telegram-бот: принимать ссылки и команды"), "tg_enabled")
        # токен длинный (около 46 знаков): строка идёт на всю ширину вкладки, то есть растёт влево, правый край не двигается
        tok = RuLineEdit()
        tok.setMinimumWidth(380)
        tok_row = QHBoxLayout()
        tok_row.addWidget(QLabel(tr("Telegram: токен бота (от @BotFather)")))
        tok_row.addWidget(tok, 1)   # одна строка: подпись слева, поле занимает всё остальное
        f.addRow(tok_row)
        self.w.append(("tg_token", tok))
        try:
            tok.setEchoMode(QLineEdit.Password)   # токен — как пароль: на экране не показываем
        except Exception:
            pass
        self.line(f, tr("Telegram: id руководителя (власть абсолютна: всегда администратор, его нельзя понизить или заблокировать)"), "tg_owner")
        self.owner_edit = self.w[-1][1]
        try:
            self.owner_edit.setEchoMode(QLineEdit.Password)   # id руководителя тоже скрыт точками
        except Exception:
            pass
        self.tok_edit = tok
        self.lock_hash = self.s.tg_lock_hash   # новый хэш попадает в настройки только по «ОК»
        self.unlocked = False
        lock_row = QHBoxLayout()
        self.lock_note = QLabel(tr("Замок: токен и id руководителя меняются только по паролю."))
        self.lock_btn = QPushButton()
        self.pw_btn = QPushButton(tr("Сменить пароль..."))
        self.rm_btn = QPushButton(tr("Снять пароль"))
        self.lock_btn.clicked.connect(self.lock_clicked)
        self.pw_btn.clicked.connect(lambda _=False: self.change_password(False))
        self.rm_btn.clicked.connect(lambda _=False: self.change_password(True))
        lock_row.addWidget(self.lock_note, 1)
        for b in (self.lock_btn, self.pw_btn, self.rm_btn):
            lock_row.addWidget(b)
        f.addRow(lock_row)
        self.refresh_lock()
        self.line(f, tr("Telegram: id администраторов через запятую (полный доступ и уведомления)"), "tg_admin")
        self.check(f, tr("Telegram: новые участники сразу становятся гостями (иначе — заявка)"), "tg_auto_guest")
        self.check(f, tr("Telegram: присылать администраторам уведомления о завершении и ошибках"), "tg_notify")
        f.addRow(QLabel(tr("Гостей и заявки смотрите в «Настройки → Участники Telegram».")))
        self.check(f, tr("Telegram: гостям нельзя плейлисты, каналы и профили целиком (только отдельные ролики)"), "tg_guest_no_playlists")
        f.addRow(QLabel(tr("Сайты, с которых могут скачивать гости (администраторам можно всё):")))
        grid_w = QWidget()
        grid = QGridLayout(grid_w)
        self.site_boxes = {}
        for i, (key, title, _doms) in enumerate(TG_SITES):
            cb = QCheckBox(title)
            self.site_boxes[key] = cb
            grid.addWidget(cb, i // 3, i % 3)
        f.addRow(grid_w)

        row = QHBoxLayout()
        row.addStretch(1)
        ok, cancel = QPushButton("OK"), QPushButton(tr("Отмена"))
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        row.addWidget(ok)
        row.addWidget(cancel)
        lay.addLayout(row)
        self.load()

    def page(self, tabs, title):
        w = QWidget()
        f = QFormLayout(w)
        f.setLabelAlignment(Qt.AlignLeft)
        tabs.addTab(w, title)
        return f

    def line(self, f, label, attr):
        e = RuLineEdit()
        f.addRow(label, e)
        self.w.append((attr, e))

    def template_row(self, f):
        """Строка шаблона имени + список готовых шаблонов: выбор подставляет шаблон в строку."""
        pick = QComboBox()
        pick.addItem(tr("Свой шаблон (впишите ниже)"), "")
        for tpl, label in NAME_TEMPLATES:
            pick.addItem(tr(label), tpl)
        f.addRow(tr("Готовые шаблоны имени файла"), pick)
        e = RuLineEdit()
        f.addRow(tr("Шаблон имени файла"), e)
        self.w.append(("outtmpl", e))

        def from_pick(i):
            tpl = pick.itemData(i)
            if tpl:
                e.setText(tpl)

        def from_text(text):
            i = pick.findData(text.strip())
            pick.blockSignals(True)
            pick.setCurrentIndex(max(0, i))
            pick.blockSignals(False)
        pick.currentIndexChanged.connect(from_pick)
        e.textChanged.connect(from_text)
        self.tpl_pick = pick

    def spin(self, f, label, attr, lo, hi):
        sp = QSpinBox()
        sp.setRange(lo, hi)
        f.addRow(label, sp)
        self.w.append((attr, sp))

    def check(self, f, label, attr):
        c = QCheckBox(label)
        f.addRow(c)
        self.w.append((attr, c))

    def combo(self, f, label, attr, items):
        c = QComboBox()
        for value, text in items:
            c.addItem(text, value)
        f.addRow(label, c)
        self.w.append((attr, c))

    def path_row(self, f, label, attr, folder):
        box = QWidget()
        hl = QHBoxLayout(box)
        hl.setContentsMargins(0, 0, 0, 0)
        e = RuLineEdit()
        b = QPushButton(tr("Обзор..."))
        hl.addWidget(e, 1)
        hl.addWidget(b)

        def browse():
            if folder:
                p = QFileDialog.getExistingDirectory(self, tr("Выберите папку"), e.text())
            else:
                p, _ = QFileDialog.getOpenFileName(self, tr("Выберите файл"), e.text(),
                                                   tr("Текстовые файлы (*.txt);;Все файлы (*)"))
            if p:
                e.setText(p)
        b.clicked.connect(lambda _=False: browse())
        f.addRow(label, box)
        self.w.append((attr, e))

    def refresh_lock(self):
        """Замок закрыт: токен и id руководителя только для чтения (точками), пока не введён пароль."""
        locked = bool(self.lock_hash) and not self.unlocked
        for e in (self.tok_edit, self.owner_edit):
            e.setReadOnly(locked)
        self.lock_btn.setText(tr("🔒 Разблокировать...") if locked else
                              tr("🔒 Заблокировать") if self.lock_hash else tr("🔓 Поставить пароль..."))
        self.pw_btn.setVisible(bool(self.lock_hash) and not locked)
        self.rm_btn.setVisible(bool(self.lock_hash) and not locked)

    def ask_password(self, label):
        pw, ok = QInputDialog.getText(self, tr("Замок на токен и id руководителя"), label, QLineEdit.Password)
        return pw if ok else None

    def new_password(self):
        """Дважды спрашивает новый пароль. Возвращает пароль или None."""
        p1 = self.ask_password(tr("Новый пароль:"))
        if p1 is None:
            return None
        if len(p1) < 4:
            QMessageBox.information(self, "Great Turtle", tr("Пароль слишком короткий: нужно не меньше 4 знаков."))
            return None
        p2 = self.ask_password(tr("Повторите пароль:"))
        if p2 is None:
            return None
        if p1 != p2:
            QMessageBox.information(self, "Great Turtle", tr("Пароли не совпадают."))
            return None
        return p1

    def lock_clicked(self):
        if not self.lock_hash:            # пароля ещё нет: поставить
            pw = self.new_password()
            if pw:
                self.lock_hash, self.unlocked = lock_make(pw), True
                QMessageBox.information(self, "Great Turtle", tr("Пароль установлен. Токен и id руководителя защищены: после закрытия настроек они меняются только по паролю."))
        elif self.unlocked:               # закрыть замок обратно
            self.unlocked = False
        else:                             # открыть по паролю
            pw = self.ask_password(tr("Введите пароль:"))
            if pw is None:
                return
            if not lock_check(pw, self.lock_hash):
                QMessageBox.information(self, "Great Turtle", tr("Неверный пароль."))
                return
            self.unlocked = True
        self.refresh_lock()

    def change_password(self, remove):
        if remove:
            self.lock_hash = ""
            QMessageBox.information(self, "Great Turtle", tr("Пароль снят: токен и id руководителя снова можно менять свободно."))
        else:
            pw = self.new_password()
            if not pw:
                return
            self.lock_hash = lock_make(pw)
        self.refresh_lock()

    def load(self):
        keys = tg_site_keys(self.s.tg_guest_sites)
        for key, cb in getattr(self, "site_boxes", {}).items():
            cb.setChecked(key in keys)
        for attr, w in self.w:
            v = getattr(self.s, attr)
            if isinstance(w, QCheckBox):
                w.setChecked(bool(v))
            elif isinstance(w, QSpinBox):
                w.setValue(int(v))
            elif isinstance(w, QComboBox):
                i = w.findData(v)
                if i < 0 and v not in (None, ""):   # значение из файла настроек, которого нет в списке: не затираем его при «ОК»
                    w.addItem(str(v), v)
                    i = w.count() - 1
                w.setCurrentIndex(max(0, i))
            else:
                w.setText(str(v))

    def collect(self):
        self.s.tg_lock_hash = self.lock_hash
        if getattr(self, "site_boxes", None):
            self.s.tg_guest_sites = ",".join(k for k, cb in self.site_boxes.items() if cb.isChecked())
        for attr, w in self.w:
            if isinstance(w, QCheckBox):
                v = w.isChecked()
            elif isinstance(w, QSpinBox):
                v = w.value()
            elif isinstance(w, QComboBox):
                v = w.currentData()
            else:
                v = w.text().strip()
                if attr == "folder":
                    v = os.path.expanduser(v) or os.path.join(os.path.expanduser("~"), "Downloads")
                elif attr == "outtmpl" and not v:
                    v = "%(title)s [%(id)s].%(ext)s"   # пустой шаблон дал бы файлы «.mp4», которые затирают друг друга
            setattr(self.s, attr, v)


HELP_HTML = """
<h2>Как пользоваться</h2>

<h3>Самое короткое объяснение</h3>
<ol>
<li>Скопируйте ссылку на видео, песню или картинку.</li>
<li>Вставьте её в строку сверху и нажмите синюю кнопку со стрелкой вниз.</li>
<li>Готовое лежит в папке загрузок. Открыть её: «Инструменты → Открыть папку загрузки».</li>
</ol>

<h3>Как добавить ссылку</h3>
<ul>
<li><b>Строка сверху + синяя кнопка.</b> Можно вставить сразу несколько ссылок: через пробел или с новой строки.</li>
<li><b>Значок слева от строки.</b> Берёт ссылку из буфера (то, что вы только что скопировали) и сразу начинает скачивать.</li>
<li><b>Ctrl+V</b>, когда выбран список загрузок, делает то же самое: добавляет скопированные ссылки (то же — «Инструменты → Вставить ссылки из буфера»).</li>
<li><b>Перетаскивание.</b> Можно взять ссылку или файл со ссылками мышкой и бросить в окно.</li>
<li><b>Кнопка «Авто» внизу.</b> Когда там написано «Авто: вкл», любая скопированная ссылка сразу добавляется и скачивается. Нажмите ещё раз, чтобы выключить. Если выключено, программа ничего сама не берёт. То же включается в «Инструменты → Автоскачивание скопированных ссылок» и в Параметры → Прочее.</li>
</ul>
<p>Если вы вставили ссылку, которая уже есть в списке, программа не скачивает её второй раз, а показывает старую загрузку.</p>

<h3>Что делает стрелка рядом с синей кнопкой</h3>
<ul>
<li><b>Выбор страниц и скачивание</b> — когда нужна не вся подборка, а часть (см. ниже).</li>
<li><b>Выбор глав и скачивание</b> — для сайтов, где всё разбито на главы.</li>
<li><b>Скачать в качестве</b> — один раз выбрать качество (от 144p до 4K), не меняя настройки.</li>
<li><b>Скачать только звук</b> — только музыка или голос, без картинки.</li>
<li><b>Скачать только субтитры</b> — файлы .srt без видео (языки задаются в Параметры → Основные → «Языки субтитров»; пусто — все). Файлы называются так же, как видео, если шаблон имени тот же, поэтому плеер подхватит их сам.</li>
</ul>

<h3>Плейлисты и подборки</h3>
<p>Если по ссылке много видео (плейлист, канал, альбом), программа создаёт папку с названием плейлиста и кладёт всё туда. В списке такие загрузки собраны в одну группу, её можно свернуть и развернуть (пробел).</p>
<p><b>Ссылка из «Микса» YouTube</b> (в адресе есть <code>list=RD…</code> или <code>start_radio=1</code>) скачивает только одно это видео: микс бесконечный, и целиком его брать нет смысла. Нужный плейлист открывайте ссылкой вида <code>youtube.com/playlist?list=…</code>.</p>
<p>Если ссылка ведёт на видео внутри плейлиста (в адресе есть и <code>v=…</code>, и <code>list=…</code>), по умолчанию скачивается только это видео. Чтобы брать весь плейлист, включите в Параметры → Основные «Для ссылок «видео из плейлиста» скачивать весь плейлист».</p>
<p>У каждого ролика в строке указан его вес, а в заголовке группы справа — общий вес плейлиста. Пока размер части роликов неизвестен (они ещё не начали скачиваться), перед общим весом стоит «≥».</p>
<p>Чтобы при каждом плейлисте программа сама спрашивала, что скачать, включите «Настройки → Спрашивать, что скачать, при загрузке плейлиста». Откроется окно со списком роликов: отметьте нужные. Те, что уже скачаны раньше, помечены «✓». Для плейлиста из одного видео окно не показывается. В окне у роликов указаны длительность и вес (если сайт их сообщает), а внизу — общий вес выбранных. Если веса в списке нет, программа сама спрашивает сайт по каждому ролику, и размеры появляются в строках по мере получения. В большом плейлисте (больше 60 роликов с неизвестным размером) это не запускается само, чтобы сайт не заблокировал частые запросы: нажмите «Узнать размеры». Кнопка запускает опрос и повторно.</p>
<p>Чтобы взять только часть, выберите «Выбор страниц» и напишите номера:</p>
<pre>~ 100            первые 100
-100 ~           последние 100
1, 10 ~ 20, -1   первое, с 10-го по 20-е и последнее</pre>

<h3>Качество видео</h3>
<p>Выбирается в «Настройки → Параметры → Основные» (поле «Формат видео»).</p>
<ul>
<li><b>Лучшее качество</b> — самое большое, что есть у сайта.</li>
<li><b>MP4 (без AV1)</b> — понятный всем формат, открывается почти везде. Хороший выбор, если не знаете, что брать.</li>
<li><b>H.264 + AAC</b> — для телевизоров, телефонов и старых плееров, которые не тянут новые форматы. На YouTube такое бывает до 1080p.</li>
<li><b>144p … 4K</b> — выбрать потолок. Если такого размера у видео нет, возьмётся ближайший меньший.</li>
<li><b>Только звук</b> и <b>Только субтитры</b> — без видео (см. ниже).</li>
</ul>
<p>Чем выше качество, тем больше файл. Видео в 4K может весить десятки гигабайт. У роликов с возрастным ограничением доступное качество бывает ниже: для них обычно нужен вход в аккаунт (cookies, см. ниже).</p>

<h3>Музыка</h3>
<p>Кнопка «Скачать только звук» (в выпадающем меню) или режим «Только звук». Качество звука выбирается в Параметры → Основные → «Качество звука»:</p>
<ul>
<li><b>MP3 320</b> — лучший mp3, открывается на любом плеере и телефоне.</li>
<li><b>MP3 256 / 192</b> — файлы поменьше.</li>
<li><b>Оригинальный звук</b> — звук без пересжатия, самое честное качество. Файл будет в формате m4a или opus.</li>
</ul>
<p>В файл записываются название и исполнитель, а также обложка (если сайт её отдаёт). Не нужна обложка или она вызывает ошибки — снимите галочку в Параметры → Основные. Если вшить её не получилось, программа сама скачает трек без обложки.</p>

<h3>Дубляж: несколько языков в одном видео</h3>
<p>У некоторых видео есть несколько звуковых дорожек (например, английская и русская). Чтобы скачать оригинал и дубляж вместе:</p>
<ol>
<li>Включите «Настройки → Добавлять дубляж к оригиналу».</li>
<li>В Параметры → Основные в поле «Языки дубляжа» напишите нужные: <code>ru</code> или <code>ru,de</code>. Если оставить пустым, возьмутся все языки (файл станет больше).</li>
<li>Скачайте видео. Оригинал будет первой дорожкой, дубляж — второй.</li>
</ol>
<p>В плеере звуковые дорожки переключаются в его меню (например, в VLC: «Аудио → Звуковая дорожка»). Если дубляжа на нужном языке нет, видео скачается обычным способом. Проверка дубляжа делается только на YouTube, VK, Rutube и Дзене: на других сайтах она обычно не нужна, а лишние запросы могут вызвать блокировку. Если дубляж есть на другом сайте, впишите его в Параметры → Основные → «Ещё сайты, где искать дубляж» (короткое имя, как у папки сайта, или домен: <code>vimeo, example.com</code>).</p>
<p><b>В режиме «Только звук»</b> та же настройка работает иначе: каждая дорожка сохраняется отдельным файлом, например <code>Название.en.mp3</code> и <code>Название.ru.mp3</code>. Если у видео одна дорожка, файл будет один.</p>

<h3>Объединить видео с субтитрами и звуком</h3>
<p>Если у вас уже есть видео и отдельные файлы, например 4K-ролик с сайта и субтитры (.srt) или русская дорожка с другого источника: «Инструменты → Объединить видео с субтитрами и звуком». Можно и правой кнопкой по скачанному видео → «Добавить субтитры / звук к файлу».</p>
<ol>
<li>Выберите видео и добавьте файлы субтитров и звука кнопками «Добавить...». Язык определяется по имени файла (<code>video.ru.srt</code>), его можно поправить кнопкой «Изменить язык».</li>
<li>Укажите, куда сохранить результат (.mkv или .mp4) и нажмите «Объединить».</li>
</ol>
<p>Видео и звук не перекодируются, поэтому всё быстро и без потери качества. Исходные файлы не меняются, рядом появляется новый. Если нужны субтитры в .mp4, они сохраняются как текстовые дорожки (mov_text), в .mkv — как есть. Нужен ffmpeg.</p>

<h3>Достать звук и субтитры из видео</h3>
<p>«Инструменты → Извлечь звук и субтитры из видео» (или правая кнопка по скачанному видео). Программа покажет все звуковые дорожки и субтитры внутри файла: отметьте нужные, и каждая сохранится отдельным файлом, например <code>video.ru.srt</code>, <code>video.en.m4a</code>. Звук по умолчанию копируется без перекодирования, можно выбрать MP3 320. Графические субтитры (с картинками, как на DVD) не поддерживаются, а PGS сохраняются как .sup.</p>

<h3>Субтитры</h3>
<p>В Параметры → Основные → «Субтитры» выберите:</p>
<ul>
<li><b>Не скачивать</b> (так по умолчанию).</li>
<li><b>Отдельными файлами (.srt)</b> — рядом с видео появятся файлы с текстом. Плеер подхватит их сам.</li>
<li><b>Вшить в видео</b> — субтитры будут внутри видеофайла, их включают в плеере.</li>
</ul>
<p>Языки пишутся через запятую, например <code>ru,en</code>. Галочка «автоматические субтитры» нужна, если у видео нет настоящих субтитров, а есть распознанные ютубом.</p>

<h3>Обновление программы</h3>
<p><b>Видео не качается, сайт изменился:</b> «Инструменты → Обновить yt-dlp и сразу перезапустить программу» — одна кнопка вместо двух шагов. Пункт «Обновить yt-dlp и gallery-dl» только обновляет, перезапускать придётся самим. Если после обновления ошибка осталась, в Параметры → Основные можно включить тестовые версии yt-dlp («Обновлять yt-dlp до тестовых версий»): исправления выходят в них раньше, но они менее стабильны. Раз в две недели программа сама напомнит об обновлении.</p>
<p><b>Новая версия самой программы:</b> «Инструменты → Обновить программу из архива» и выберите полученный zip-файл. Программа проверит файлы, сохранит старые в папку <code>_backup_…</code>, установит новые и предложит перезапуск. Настройки и список загрузок не теряются. Если вам дали постоянную ссылку на архив, впишите её в Параметры → Прочее → «Адрес обновления программы» и пользуйтесь пунктом «Проверить обновление программы по адресу». Ссылку берите только из надёжного источника: обновление запускает код из архива.</p>
<p>Номер установленной версии виден в «Справка → О программе». Ярлык с черепашкой на рабочем столе создаёт установщик, а при необходимости его можно сделать заново: «Инструменты → Создать ярлык на рабочем столе».</p>

<h3>Архив скачанного</h3>
<p>Программа помнит, что уже скачала (ролик и вид: видео или звук). Если ссылка попадётся снова, даже после удаления из списка, она не будет качаться второй раз: задача сразу станет «Уже скачано раньше». Если такой ролик встретится в плейлисте, он не скачивается заново: в плейлисте появится его задача со статусом «Уже скачано раньше», которая подключена к тому же файлу. Файл при этом не копируется, а лежит там, где был, и открывается из обеих задач; об этом пишет строка внизу. Запись действует, пока файл лежит на диске: удалили файл — ролик снова можно скачать. Пункты правого меню «Перезапустить» и «Скачать заново в формате» всегда качают заново. Отключить архив — Параметры → Основные; стереть всю память — «Инструменты → Забыть архив скачанного».</p>

<h3>Telegram-бот</h3>
<p>Можно управлять программой из Telegram: присылать ссылки с телефона и получать уведомления. Программа должна быть запущена на компьютере, а компьютер включён.</p>
<ol>
<li>В Telegram откройте <b>@BotFather</b>, отправьте <code>/newbot</code> и получите <b>токен</b> (длинная строка с двоеточием).</li>
<li>В программе: Параметры → вкладка Telegram → включите «Telegram-бот: принимать ссылки и команды» и вставьте токен. Нажмите OK.</li>
<li>Напишите своему боту что угодно. Он ответит, что заявка отправлена, и назовёт ваш <b>Telegram ID</b> (число).</li>
<li>Впишите это число в «Telegram: id руководителя» (руководитель — владелец бота) и снова нажмите OK. Теперь у вас полный доступ. Других администраторов можно перечислить через запятую в «Telegram: id администраторов».</li>
</ol>
<p><b>Участники.</b> Если кто-то другой напишет боту, он получит ответ «Заявка отправлена владельцу бота», а администраторам придёт сообщение о новой заявке. Решение принимается в «Настройки → Участники Telegram…»: <b>Разрешить (гость)</b>, <b>Сделать администратором</b>, <b>Заблокировать</b> (бот перестаёт отвечать этому человеку) или <b>Удалить</b>. Одобренному человеку бот сам сообщит об этом. Если включить «Telegram: новые участники сразу становятся гостями», заявки не нужны. Список участников хранится в файле <code>tg_members.json</code> в папке данных. Бот отвечает только в личных чатах: сообщения в группах и каналах он игнорирует. Одновременно хранится не больше 50 заявок от посторонних, дальше бот им не отвечает, пока вы не разберёте список.</p>
<p><b>Руководитель и сайты гостей.</b> В поле «Telegram: id руководителя» впишите свой id: власть руководителя абсолютна — он всегда администратор, его нельзя понизить, заблокировать или удалить (ни в окне участников, ни командами), в списке участников его не видно, и только он управляет людьми прямо из бота: <code>/members</code>, <code>/allow ID</code>, <code>/admin ID</code>, <code>/block ID</code>, <code>/remove ID</code>. Гости могут скачивать только с сайтов, отмеченных галочками на вкладке Telegram (по умолчанию YouTube, Instagram и TikTok; в списке ещё VK, X, Facebook, Reddit, Twitch и другие); ссылки с остальных сайтов бот отклоняет. Плейлисты, каналы и профили целиком гостям тоже недоступны, только отдельные ролики (ссылка на ролик с &list=… урезается до этого ролика); это отключается галочкой на той же вкладке. Администраторам и руководителю можно любые сайты. Бота можно быстро включать и выключать в меню «Настройки → Telegram-бот включён».</p>
<p><b>Что можно.</b> Гость может только присылать <b>ссылки</b> (можно несколько): программа добавит и скачает. Все команды — только у администратора: <code>/list</code> — список загрузок с номерами (#12); <code>/pause</code> и <code>/resume</code> — пауза всех и продолжение; <code>/retry</code> — перезапустить неудачные; <code>/file 12</code> — прислать файл загрузки №12; <code>/help</code> — подсказка. <code>/sites</code> — какие сайты разрешены гостям (доступно всем). Те же действия есть кнопками внизу чата, и они зависят от прав: гость видит только «Сайты» и «Помощь»; администратор — ещё «Список», «Перезапустить неудачные», «Пауза», «Продолжить»; руководитель — ещё «Участники». Слева от поля ввода есть меню команд (тоже по правам), а под ответом на /list — кнопки «📎» для присылки файлов последних завершённых загрузок. Кнопки можно скрыть командой <code>/hide</code> (или кнопкой «Скрыть кнопки») и вернуть командой <code>/menu</code>; выбор запоминается для каждого участника. Если включено «Telegram: присылать администраторам уведомления…», бот пишет всем администраторам, когда загрузка завершилась или закончилась ошибкой и когда выполнены все задачи.</p>
<p><b>Замок.</b> Кнопка «Поставить пароль…» на вкладке Telegram защищает токен бота и id руководителя: они становятся недоступными для правки, а кнопка «Разблокировать…» спрашивает пароль. Сам пароль не хранится, только его хэш с солью. Если пароль забыт, замок снимается только удалением строки <code>tg_lock_hash</code> в файле настроек. Важно: токен — как пароль от бота, он хранится в файле настроек без шифрования, никому его не показывайте. Файлы можно получать до 50 МБ (предел Telegram для ботов). Если включено «Спрашивать, что скачать, при загрузке плейлиста», окно выбора откроется на компьютере, а не в Telegram. Если Telegram у вас не открывается без прокси, укажите его в Параметры → Сеть. Ход работы бота виден в «Инструменты → Журнал…» (строки [telegram]).</p>

<h3>Меню и настройки</h3>
<p><b>Файл:</b> «Импорт ссылок из файла…» добавляет ссылки из .txt (такой файл можно и перетащить в окно); «Экспорт ссылок…» сохраняет ссылки всех загрузок в файл; «Выйти без сохранения» закрывает программу, не записывая изменения списка и настроек.</p>
<p><b>Задачи:</b> те же действия, что в правом меню загрузки, и ещё «Перезапустить все неудачные», «Пауза выбранных» / «Продолжить выбранные», «Убрать все завершённые», «Очистить список».</p>
<p><b>Параметры → Основные:</b> папка загрузки, формат видео по умолчанию, шаблон имени файла, число одновременных задач, лимит скорости, качество звука, субтитры, дубляж, архив, обложка, использование gallery-dl для сайтов с картинками, раскладка по папкам сайтов.</p>
<p><b>Параметры → Сеть:</b> число потоков на одну загрузку; «Повторов при ошибке» (повторы внутри загрузки одного файла) и число автоповторов всей загрузки; <b>прокси</b> (например <code>http://127.0.0.1:8080</code>) — нужен, если сайт открывается только через прокси; файл cookies, cookies из браузера и «Имитировать браузер».</p>
<p><b>Параметры → Внешний вид:</b> тёмный режим и размер миниатюр. <b>Параметры → Прочее:</b> автосохранение, адрес обновления программы, уведомления о завершении, действие после всех загрузок (ничего или закрыть программу), запрет засыпания (Windows), автоскачивание скопированных ссылок, продолжение незавершённых задач при запуске, автоматическое убирание завершённых. <b>Параметры → Telegram:</b> бот (см. выше). <b>Меню «Настройки»</b> — быстрые переключатели: тёмный режим, автоповтор, новые загрузки сверху, дубляж, окно выбора при загрузке плейлиста, Telegram-бот; там же «Участники Telegram…» и выбор языка.</p>
<p>Список и настройки хранятся в папке данных программы. Перед каждой записью предыдущая версия сохраняется рядом с расширением <code>.bak</code>. Если файл окажется повреждён (например, при сбое питания), он переименуется в <code>.corrupt</code>, а данные восстановятся из копии <code>.bak</code>.</p>

<h3>Язык интерфейса</h3>
<p>Основной язык программы — русский, дополнительный — английский. Переключается в «Настройки → Язык / Language»: программа сохранит выбор и предложит перезапуск, после него весь интерфейс и эта справка будут на выбранном языке. Пункт меню «Язык / Language» всегда показан на обоих языках. Тексты ошибок самих сайтов остаются как есть.</p>

<h3>Куда всё скачивается</h3>
<p><b>Как называть файлы.</b> Параметры → Основные → «Готовые шаблоны имени файла»: выберите вариант из списка, и шаблон подставится в строку ниже. Строку можно править вручную: слова <code>artist</code>, <code>title</code>, <code>id</code> заменяются на автора, название и код ролика, а <code>date:%Y-%m-%d</code> — на дату. Подойдёт и обычный шаблон yt-dlp, например <code>%(title)s [%(id)s].%(ext)s</code>. Шаблон без id: если у двух роликов одинаковые названия, второй файл перезапишет первый.</p>
<p>Если переименовать папку плейлиста в Проводнике, программа заметит это в течение нескольких секунд: группа в списке получит новое название, а пути к файлам обновятся, так что они откроются как раньше. То же с отдельным файлом, который вы переименовали или перенесли: программа находит его по размеру и времени изменения и исправляет путь. Это работает, пока в эту папку ничего не скачивается; папка должна остаться на прежнем месте (изменено лишь имя), а перенесённый файл ищется в папке загрузок и рядом со старым местом. Папка загрузок задаётся в Параметрах. Внутри для каждого сайта создаётся своя папка (youtube, vkvideo и так далее), а плейлисты лежат ещё в одной папке внутри неё. Если не нравится — выключите галочку «Раскладывать загрузки по папкам сайтов».</p>
<p>Порядок списка переключается в «Настройки → Новые загрузки сверху»: когда галочка стоит, свежие загрузки появляются вверху списка, а когда снята (по умолчанию) — внизу. Плейлист при этом остаётся в своём порядке внутри группы. Очередь скачивания от этого не меняется.</p>
<p>Под строкой ссылки есть <b>вкладки</b>: «Все», «Ошибки» (всё, что не скачалось или скачалось не до конца; задача уходит из вкладки, когда повтор удался), «Музыка» (загрузки в режиме «Только звук»), «Плейлисты» (загрузки из плейлистов) и по одной на каждый сайт, с которого вы качали. Вкладки появляются, когда в них есть что показать. Нажмите на вкладку, чтобы увидеть только эти загрузки. Вкладки можно перетаскивать мышкой, чтобы расставить их как удобно. Правая кнопка по вкладке → «Закрепить»: закреплённые вкладки (с значком 📌) стоят левее остальных и не уезжают. Порядок и закрепление запоминаются.</p>

<h3>Что можно делать с загрузкой</h3>
<p>Нажмите на загрузку правой кнопкой мыши:</p>
<ul>
<li><b>Пауза / Продолжить</b> — остановить и потом продолжить с того же места. Кнопка с двумя палочками внизу ставит на паузу все загрузки сразу; когда всё на паузе, она превращается в «Пуск». Если поставить общую паузу, а потом снять, продолжатся только те загрузки, которые остановила она; поставленные на паузу вручную останутся на паузе.</li>
<li><b>Показать в папке</b> — откроет Проводник и выделит скачанный файл. Двойной щелчок открывает сам файл в плеере.</li>
<li><b>Добавить субтитры / звук к файлу</b> и <b>Извлечь звук / субтитры из файла</b> — работа с дорожками готового видео (см. выше).</li>
<li><b>Открыть ссылку в браузере</b> — страница видео или песни откроется в браузере (можно выбрать сразу несколько, до 10).</li>
<li><b>Перезапустить</b> — начать заново, если была ошибка. Сама программа тоже перезапускает неудавшиеся загрузки: «Настройки → Автоповтор при ошибке» (по умолчанию 3 раза, с паузами 10, 30 и 90 секунд; число попыток — Параметры → Сеть → «Сколько раз программа сама перезапустит загрузку после ошибки»; поле «Повторов при ошибке» — это другое: повторы внутри загрузки одного файла). Не повторяются ошибки, которые сами не пройдут: неизвестный сайт, удалённое или закрытое видео, нет ffmpeg. Просьба войти или подтвердить возраст повторяется: на YouTube она иногда проходит сама.</li>
<li><b>Выбрать формат</b> — программа прочитает все доступные качества ролика (разрешение, кодек, размер) и скачает именно ту строку, которую вы выберете. Если у строки нет звука, лучший звук добавится сам. «Как в настройках» сбрасывает выбор. Дубляж к такой загрузке не добавляется, а в архив скачанного она не записывается.</li>
<li><b>Скачать заново в формате</b> — например, был mp4, а теперь нужен только звук.</li>
<li><b>Цветная метка и комментарий</b> — чтобы не потеряться в большом списке.</li>
<li><b>Информация</b> — подробности: какие потоки скачаны, размер, папка. Пункты «Информация», «Выбрать формат», «Добавить субтитры / звук» и «Извлечь…» доступны, когда выбрана одна загрузка.</li>
<li><b>Переместить в группу…</b> / <b>Убрать из группы</b> — соберите любые загрузки в свою группу (папка при этом не меняется). Правая кнопка по заголовку группы: <b>Переименовать группу…</b> (меняет название группы, а не папку на диске) и <b>Копировать названия групп</b>.</li>
<li><b>Открыть первый файл</b> (Enter) и <b>Скопировать ссылку</b> (Ctrl+C).</li>
<li><b>Убрать из списка</b> — файл остаётся на диске. <b>Удалить файлы</b> — удаляет и файл (идущая загрузка сначала останавливается; если файл занят другой программой, об этом будет сообщение). <b>Убрать все завершённые</b> — одним действием.</li>
</ul>
<p>Если навести мышку на маленькую картинку, она увеличится. Щелчок по ней открывает файл.</p>

<h3>Как читать цвета в списке</h3>
<p>Светло-серый — ждёт или читает информацию · тёмно-серый — скачивается · зелёный — готово ·
оранжевый — скачано не полностью · красный — ошибка (текст ошибки написан в строке).</p>

<h3>Если нужно войти на сайт (cookies)</h3>
<p>Некоторые сайты показывают видео только тем, кто вошёл в аккаунт. YouTube иногда пишет «Sign in to confirm you're not a bot». Программе нужно «показать», что вы вошли. Это делает файл cookies.txt. Как его получить:</p>
<ol>
<li>Откройте в браузере <b>приватное окно</b> и войдите на сайт.</li>
<li>В этой же вкладке откройте <code>youtube.com/robots.txt</code> (для YouTube).</li>
<li>Установите в браузер расширение «Get cookies.txt LOCALLY» и нажмите Export.</li>
<li>Закройте приватное окно.</li>
<li>В программе: Параметры → Сеть → «Файл cookies» — выберите сохранённый файл.</li>
</ol>
<p>Лучше входить запасным аккаунтом. Есть и более простой путь: Параметры → Сеть → «Cookies из браузера» (программа сама возьмёт их из Chrome, Firefox и других). Он работает не у всех: если не получилось, используйте файл. Расширение должно сохранять файл в формате <b>Netscape</b> (не JSON): первая строка файла — <code># Netscape HTTP Cookie File</code>. Если формат другой, программа пропустит такой файл и напишет об этом в журнале. Файл cookies.txt — это как ключ от вашего аккаунта, никому его не показывайте. Если снова просит войти, файл устарел: сделайте новый.</p>

<h3>Если что-то не работает</h3>
<ol>
<li>Нажмите «Инструменты → Обновить yt-dlp и сразу перезапустить программу». Сайты часто меняются, и это решает большинство ошибок.</li>
<li>Откройте «Инструменты → Журнал»: там написано, что именно пошло не так. Эти строки можно скопировать и показать тому, кто помогает.</li>
<li>Если пишет, что видео доступно только подписчикам или по возрасту, нужен вход (cookies) и подписка.</li>
<li>Ошибка <b>403</b>, <b>412</b> или «слишком много запросов» значит, что сайт временно не пускает (412 — типичная защита от ботов у Bilibili). Программа сама повторит попытку через несколько секунд, включив для этой загрузки имитацию браузера (если установлен curl-cffi; он ставится вместе с yt-dlp при обновлении). Если не помогло: включите «Имитировать браузер» и подключите cookies (Параметры → Сеть), уменьшите число одновременных загрузок или попробуйте позже.</li>
<li>Склейка видео и звука, mp3, субтитры, объединение и извлечение дорожек работают только при установленном ffmpeg (с ним ставится и ffprobe).</li>
</ol>

<h3>Фильтр списка (внизу слева)</h3>
<pre>слово            по названию, автору, ссылке, группе, комментарию, сайту
type:youtube     по сайту
tag:red          по цветной метке (red, orange, yellow, green, blue, purple, gray или 1–7)
comment:слово    по комментарию
bad:             всё, что ещё не скачано (в очереди, качается, ошибка, не до конца)
dup:             только повторяющиеся
rem:             файлы которых больше нет на диске</pre>
<p>Условия можно соединять через пробел: <code>youtube bad:</code></p>

<h3>Горячие клавиши</h3>
<p>Клавиши Enter, Del, Shift+Del, Ctrl+↑ ↓ Home End, Ctrl+0…7, Ctrl+C, Ctrl+V, Ctrl+A, Ctrl+ +/− и Пробел работают, когда выбран список загрузок (в строке ссылки Ctrl+V просто вставляет текст). Остальные работают в любом месте окна.</p>
<table cellpadding="3">
<tr><td><b>Ctrl+S</b></td><td>сохранить список и настройки</td></tr>
<tr><td><b>Ctrl+,</b></td><td>параметры</td></tr>
<tr><td><b>Enter</b>, двойной щелчок, щелчок по картинке</td><td>открыть первый файл</td></tr>
<tr><td><b>Del</b> / <b>Shift+Del</b></td><td>убрать из списка / удалить ещё и файлы</td></tr>
<tr><td><b>Ctrl+↑ ↓ Home End</b></td><td>изменить порядок скачивания</td></tr>
<tr><td><b>Ctrl+1…7</b>, <b>Ctrl+0</b></td><td>цветные метки / снять метку</td></tr>
<tr><td><b>Alt+D</b></td><td>адресная строка (строка ссылки) ↔ список загрузок</td></tr>
<tr><td><b>Ctrl+F</b></td><td>перейти в фильтр</td></tr>
<tr><td><b>Ctrl+Tab</b></td><td>показать/скрыть адресную строку (строку ссылки)</td></tr>
<tr><td><b>Ctrl+колесо</b>, <b>Ctrl+ +/−</b></td><td>размер картинок</td></tr>
<tr><td><b>Пробел</b></td><td>развернуть/свернуть группы</td></tr>
<tr><td><b>Ctrl+C / Ctrl+V / Ctrl+A</b></td><td>копировать ссылки / вставить ссылки / выбрать всё</td></tr>
<tr><td><b>Ctrl+P</b></td><td>пауза / продолжить все</td></tr>
<tr><td><b>F1</b></td><td>эта справка</td></tr>
</table>

<p><i>Пожалуйста, используйте скачанные файлы только для личного пользования.</i></p>
"""


# ======================= главное окно =======================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = Settings.load()
        self.theme = DARK if self.settings.dark else LIGHT
        self.store = TaskStore()
        self.store.load(resume=self.settings.resume_on_start)
        self.bus = Bus()
        self.engine = eng.Engine(self.store, self.settings, notify=self.bus.changed.emit,
                                 on_playlist=self.bus.playlist.emit, log=self.bus.log.emit,
                                 on_done=self.bus.done.emit, on_idle=self.bus.idle.emit)
        self.logs = collections.deque(maxlen=3000)
        self.tg = None          # Telegram-бот (запускается ниже по настройкам)
        self.members = Members()   # участники бота: админы, гости, заявки, заблокированные
        self._tg_err = {}
        self.items = {}
        self.group_stats = {}
        self._thumbs = {}
        self._dirty = True
        self._rb_pending = False
        self._building = False
        self._awake = False
        self._ran = False
        self._nosave = False
        self._force = False
        self._flash_id = 0
        self._last_clip = ""
        self._hover_id = 0
        self._hover_pos = None
        self._big_thumbs = {}
        self.popup = ThumbPopup()
        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.timeout.connect(self.show_popup)

        ico = os.path.join(os.path.dirname(os.path.abspath(__file__)), "turtle.ico")
        self.logo = QIcon(ico) if os.path.exists(ico) else QIcon(make_logo(64))
        if self.logo.isNull():
            self.logo = QIcon(make_logo(64))
        self.setWindowIcon(self.logo)
        self.setAcceptDrops(True)
        self.build_ui()
        self.build_menu()
        self.build_hotkeys()
        self.tray = QSystemTrayIcon(self.logo, self)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()

        self.bus.changed.connect(self.on_changed)
        self.bus.playlist.connect(self.on_playlist)
        self.bus.done.connect(self.on_done)
        # сигнал «готово» от потока загрузки приходит с задержкой: чуть ждём, чтобы «Все задачи выполнены» не потерялось
        self.bus.idle.connect(lambda: QTimer.singleShot(400, self.on_idle))
        self.bus.log.connect(lambda s: self.logs.append(time.strftime("%H:%M:%S  ") + tr(s)))
        self.bus.message.connect(self.on_message)
        self.bus.upd.connect(self.on_update_downloaded)
        self.bus.fmts.connect(self.on_formats)
        self.bus.renames.connect(self.on_renames)
        self.bus.tg.connect(self.on_tg)
        QApplication.clipboard().dataChanged.connect(self.on_clip)
        self.apply_telegram()

        self.t_engine = QTimer(self)
        self.t_engine.timeout.connect(self.engine.tick)
        self.t_engine.start(250)
        self.t_refresh = QTimer(self)
        self.t_refresh.timeout.connect(self.on_refresh_timer)
        self.t_refresh.start(200)
        self.t_save = QTimer(self)
        self.t_save.timeout.connect(lambda: self.save_state(quiet=True))
        self.restart_autosave()

        self.update_title()
        if self.settings.geometry:
            self.restoreGeometry(QByteArray.fromHex(self.settings.geometry.encode()))
        else:
            self.resize(1000, 660)
        self.apply_styles()
        self.rebuild()
        if self.settings.use_archive:   # старые завершённые загрузки тоже попадают в архив
            self.engine.archive.backfill(self.store.tasks)
        if not eng.ytdlp_available():
            self.flash(tr("yt-dlp не найден: pip install yt-dlp"))
        elif not self.settings.last_ytdlp_update:
            self.settings.last_ytdlp_update = int(time.time())  # точка отсчёта для напоминания
        elif ytdlp_stale(self.settings.last_ytdlp_update):
            QTimer.singleShot(1500, self.remind_ytdlp)

    # ---------- построение интерфейса ----------
    def build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        lay = QVBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self.topbar = QWidget()
        self.topbar.setObjectName("topbar")
        self.topbar.setAttribute(Qt.WA_StyledBackground, True)
        tl = QHBoxLayout(self.topbar)
        tl.setContentsMargins(8, 8, 8, 8)
        tl.setSpacing(0)
        self.url_edit = RuLineEdit()
        self.url_edit.setObjectName("url")
        self.url_edit.setPlaceholderText(tr("Пожалуйста, введите URL-адрес"))
        link_act = self.url_edit.addAction(icon_link(16), QLineEdit.LeadingPosition)
        link_act.setToolTip(tr("Вставить ссылку из буфера обмена и сразу скачать"))
        link_act.triggered.connect(lambda _=False: self.paste_and_download())
        self.url_edit.setClearButtonEnabled(True)
        self.url_edit.setFixedHeight(36)
        self.url_edit.returnPressed.connect(self.add_from_entry)
        self.btn_dl = QToolButton()
        self.btn_dl.setObjectName("dl")
        self.btn_dl.setIcon(icon_download(22))
        self.btn_dl.setFixedSize(60, 36)
        self.btn_dl.setCursor(Qt.PointingHandCursor)
        self.btn_dl.setToolTip(tr("Скачать"))
        self.btn_dl.clicked.connect(lambda _=False: self.add_from_entry())
        self.btn_menu = QToolButton()
        self.btn_menu.setObjectName("dlmenu")
        self.btn_menu.setText("▾")
        self.btn_menu.setFixedSize(26, 36)
        self.btn_menu.setCursor(Qt.PointingHandCursor)
        self.btn_menu.setToolTip(tr("Другие способы скачать"))
        dl_menu = QMenu(self)
        dl_menu.addAction(tr("Выбор страниц и скачивание"), self.add_with_pages)
        dl_menu.addAction(tr("Выбор глав и скачивание"), self.add_with_select)
        dl_menu.addSeparator()
        q_menu = dl_menu.addMenu(tr("Скачать в качестве"))
        for key, label in tr_pairs(MODES):
            if key not in ("audio", "subs"):  # «только звук» и «только субтитры» — отдельными пунктами ниже
                q_menu.addAction(label, lambda _=False, k=key: self.add_with_mode(k))
        dl_menu.addSeparator()
        dl_menu.addAction(tr("Скачать только звук"), self.add_audio_only)
        dl_menu.addAction(tr("Скачать только субтитры"), self.add_subs_only)
        self.btn_menu.setMenu(dl_menu)
        self.btn_menu.setPopupMode(QToolButton.InstantPopup)
        tl.addWidget(self.url_edit, 1)
        tl.addSpacing(8)
        tl.addWidget(self.btn_dl)
        tl.addWidget(self.btn_menu)
        lay.addWidget(self.topbar)

        # вкладки сайтов: «Все» + по одной на каждый сайт, с которого есть загрузки
        self.site_filter = ""
        self._tabs_sig = None
        self._ren_last, self._ren_cache, self._mv_cache, self._ren_busy = 0.0, {}, {}, False
        self.site_tabs = QTabBar()
        self.site_tabs.setObjectName("sitetabs")
        self.site_tabs.setDrawBase(False)
        self.site_tabs.setExpanding(False)
        self.site_tabs.setUsesScrollButtons(True)
        self.site_tabs.setCursor(Qt.PointingHandCursor)
        self.site_tabs.setMovable(True)   # вкладки можно перетаскивать
        self.site_tabs.setContextMenuPolicy(Qt.CustomContextMenu)
        self.site_tabs.customContextMenuRequested.connect(self.tab_menu)
        self.site_tabs.tabMoved.connect(lambda *_: QTimer.singleShot(0, self.on_tab_moved))
        self.site_tabs.currentChanged.connect(self.on_site_tab)
        self.site_tabs.hide()
        lay.addWidget(self.site_tabs)

        self.tree = TaskTree()
        t = self.tree
        t.setHeaderHidden(True)
        t.setSelectionMode(QAbstractItemView.ExtendedSelection)
        t.setIndentation(16)
        t.setUniformRowHeights(False)
        t.setExpandsOnDoubleClick(False)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.setFrameShape(QFrame.NoFrame)
        t.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        t.setContextMenuPolicy(Qt.CustomContextMenu)
        t.row_h = self.settings.thumb_size + 12
        t.setItemDelegate(TaskDelegate(self))
        t.customContextMenuRequested.connect(self.show_menu)
        t.itemDoubleClicked.connect(self.on_double_click)
        t.itemExpanded.connect(self.on_expand)
        t.itemCollapsed.connect(self.on_collapse)
        t.thumbClicked.connect(lambda tid: self.open_task(self.store.get(tid)))
        t.zoom.connect(self.zoom_thumbs)
        t.thumbHover.connect(self.on_thumb_hover)
        t.thumbLeave.connect(self.hide_popup)
        lay.addWidget(t, 1)

        self.bottom = QWidget()
        self.bottom.setObjectName("bottom")
        self.bottom.setAttribute(Qt.WA_StyledBackground, True)
        bl = QHBoxLayout(self.bottom)
        bl.setContentsMargins(10, 6, 10, 6)
        bl.setSpacing(8)
        fbtn = QToolButton()
        fbtn.setObjectName("flt")
        fbtn.setIcon(icon_filter(16))
        fbtn.setAutoRaise(True)
        fbtn.setToolTip(tr("Фильтр (Ctrl+F)"))
        fbtn.clicked.connect(lambda _=False: self.filter_edit.setFocus())
        self.filter_edit = RuLineEdit()
        self.filter_edit.setObjectName("filter")
        self.filter_edit.setPlaceholderText(tr("Фильтр: слово, type:, tag:, bad:, dup:, rem:"))
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.setFixedWidth(280)
        self._ftimer = QTimer(self)
        self._ftimer.setSingleShot(True)
        self._ftimer.timeout.connect(self.rebuild)
        self.filter_edit.textChanged.connect(lambda _s: self._ftimer.start(200))
        self.msg_label = QLabel("")
        self.btn_pause = QToolButton()
        self.btn_pause.setAutoRaise(True)
        self.btn_pause.setCursor(Qt.PointingHandCursor)
        self.btn_pause.setToolTip(tr("Пауза / продолжить все загрузки (Ctrl+P)"))
        self.btn_pause.clicked.connect(lambda _=False: self.pause_act.setChecked(not self.pause_act.isChecked()))
        self.btn_clip = QToolButton()
        self.btn_clip.setAutoRaise(True)
        self.btn_clip.setCursor(Qt.PointingHandCursor)
        self.btn_clip.clicked.connect(lambda _=False: self.clip_act.setChecked(not self.clip_act.isChecked()))
        self.speed_label = QLabel("")
        self.counter = QLabel("0 / 0")
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedSize(170, 6)
        bl.addWidget(fbtn)
        bl.addWidget(self.filter_edit)
        bl.addWidget(self.msg_label, 1)
        bl.addWidget(self.btn_clip)
        bl.addWidget(self.btn_pause)
        bl.addWidget(self.speed_label)
        bl.addWidget(self.counter)
        bl.addWidget(self.bar)
        lay.addWidget(self.bottom)

    def apply_styles(self):
        self.theme = th = DARK if self.settings.dark else LIGHT
        self.centralWidget().setStyleSheet(f"""
            QWidget#topbar {{ background:{th['bg']}; }}
            QWidget#bottom {{ background:{th['bg']}; border-top:1px solid {th['line']}; }}
            QLineEdit#url {{ border:1px solid {th['edit_border']}; border-right:none; padding:0 6px;
                border-top-left-radius:4px; border-bottom-left-radius:4px; background:{th['edit']}; }}
            QLineEdit#filter {{ border:1px solid {th['edit_border']}; border-radius:3px; padding:2px 4px;
                background:{th['edit']}; }}
            QToolButton#dl, QToolButton#dlmenu {{ background:{th['accent']}; border:none; color:white;
                font-size:15px; }}
            QToolButton#dlmenu {{ border-left:1px solid #7ab8f0; border-top-right-radius:4px;
                border-bottom-right-radius:4px; }}
            QToolButton#dl:hover, QToolButton#dlmenu:hover {{ background:#2b90ee; }}
            QToolButton::menu-indicator {{ image:none; }}
            QTabBar#sitetabs {{ background:{th['bg']}; }}
            QTabBar#sitetabs::tab {{ padding:4px 12px; margin:0 2px; color:{th['muted']};
                border:none; border-bottom:2px solid transparent; }}
            QTabBar#sitetabs::tab:selected {{ color:{th['accent']}; border-bottom:2px solid {th['accent']}; }}
            QTabBar#sitetabs::tab:hover {{ color:{th['accent']}; }}
            QProgressBar {{ border:none; background:{th['line']}; border-radius:3px; }}
            QProgressBar::chunk {{ background:{th['accent']}; border-radius:3px; }}
            QTreeWidget {{ background:{th['bg']}; }}
            QLabel {{ color:{th['muted']}; }}
        """)
        self.tree.hint_color = QColor(th["muted"])
        self.update_pause_button()
        self.update_clip_button()
        self.tree.viewport().update()

    def act(self, menu, text, fn, shortcut=None, checkable=False, checked=False):
        a = QAction(text, self)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        if checkable:
            a.setCheckable(True)
            a.setChecked(checked)
            a.toggled.connect(fn)
        else:
            a.triggered.connect(lambda _=False, f=fn: f())
        menu.addAction(a)
        return a

    def build_menu(self):
        mb = self.menuBar()
        m = mb.addMenu(tr("Файл"))
        self.act(m, tr("Сохранить"), self.save_state, "Ctrl+S")
        self.act(m, tr("Импорт ссылок из файла..."), self.import_links)
        self.act(m, tr("Экспорт ссылок..."), self.export_links)
        m.addSeparator()
        self.act(m, tr("Сохранить и выйти"), self.close)
        self.act(m, tr("Выйти без сохранения"), self.quit_nosave)

        m = mb.addMenu(tr("Задачи"))
        self.act(m, tr("Открыть первый файл (Enter)"), lambda: self.open_task_sel())
        self.act(m, tr("Показать в папке"), lambda: self.open_folder_sel())
        self.act(m, tr("Открыть ссылку в браузере"), lambda: self.open_links(self.selected_tasks()))
        self.act(m, tr("Перезапустить выбранные"), lambda: self.restart_tasks(self.selected_tasks()))
        self.act(m, tr("Перезапустить все неудачные"), self.restart_failed)
        self.pause_act = self.act(m, tr("Пауза всех / продолжить"), self.toggle_pause, "Ctrl+P", checkable=True)
        self.act(m, tr("Пауза выбранных"), lambda: self.pause_tasks(self.selected_tasks()))
        self.act(m, tr("Продолжить выбранные"), lambda: self.resume_tasks(self.selected_tasks()))
        m.addSeparator()
        self.act(m, tr("Убрать из списка (Del)"), lambda: self.remove_tasks(self.selected_tasks()))
        self.act(m, tr("Удалить файлы (Shift+Del)"), lambda: self.delete_files(self.selected_tasks()))
        self.act(m, tr("Убрать все завершённые"), self.clear_done)
        self.act(m, tr("Очистить список"), self.clear_all)
        m.addSeparator()
        self.act(m, tr("Развернуть / свернуть группы (Space)"), self.toggle_groups)

        m = mb.addMenu(tr("Инструменты"))
        self.act(m, tr("Вставить ссылки из буфера (Ctrl+V)"), self.paste_links)
        self.clip_act = self.act(m, tr("Автоскачивание скопированных ссылок"), self.set_clip_monitor,
                                 checkable=True, checked=self.settings.clipboard_monitor)
        self.act(m, tr("Показать / скрыть адресную строку"), self.toggle_topbar, "Ctrl+Tab")
        m.addSeparator()
        self.act(m, tr("Открыть папку загрузки"), self.open_download_folder)
        self.act(m, tr("Объединить видео с субтитрами и звуком..."), self.open_merge)
        self.act(m, tr("Извлечь звук и субтитры из видео..."), self.open_extract)
        self.act(m, tr("Создать ярлык на рабочем столе"), self.create_shortcut)
        self.act(m, tr("Журнал..."), self.show_log)
        self.act(m, tr("Обновить yt-dlp и gallery-dl"), self.update_ytdlp)
        self.act(m, tr("Обновить yt-dlp и сразу перезапустить программу"), lambda: self.update_ytdlp(restart=True))
        self.act(m, tr("Забыть архив скачанного"), self.forget_archive)
        self.act(m, tr("Обновить программу из архива..."), self.update_from_zip)
        self.act(m, tr("Проверить обновление программы по адресу"), self.update_from_url)

        m = mb.addMenu(tr("Настройки"))
        self.act(m, tr("Параметры..."), self.open_settings, "Ctrl+,")
        self.dark_act = self.act(m, tr("Тёмный режим"), self.set_dark, checkable=True, checked=self.settings.dark)
        self.retry_act = self.act(m, tr("Автоповтор при ошибке"), self.set_auto_retry,
                                  checkable=True, checked=self.settings.auto_retry > 0)
        self.order_act = self.act(m, tr("Новые загрузки сверху"), self.set_newest_first,
                                  checkable=True, checked=self.settings.newest_first)
        self.audio_act = self.act(m, tr("Добавлять дубляж к оригиналу"), self.set_all_audio,
                                  checkable=True, checked=self.settings.all_audio)
        self.ask_act = self.act(m, tr("Спрашивать, что скачать, при загрузке плейлиста"), self.set_ask_playlist,
                                checkable=True, checked=self.settings.ask_playlist)
        self.tg_act = self.act(m, tr("Telegram-бот включён"), self.set_tg_enabled,
                               checkable=True, checked=self.settings.tg_enabled)
        self.act(m, tr("Участники Telegram..."), self.open_members)
        lm = m.addMenu("Язык / Language")
        self.lang_acts = {}
        for code, name in (("ru", "Русский"), ("en", "English")):
            self.lang_acts[code] = self.act(lm, ("✓ " if self.settings.lang == code else "    ") + name,
                                            lambda _=False, c=code: self.set_language(c))

        m = mb.addMenu(tr("Справка"))
        self.act(m, tr("Справка"), self.show_help, "F1")
        self.act(m, tr("О программе"), self.show_about)

    def build_hotkeys(self):
        # окно целиком
        for keys, fn in (("Alt+D", self.toggle_focus), ("Ctrl+F", lambda: self.filter_edit.setFocus())):
            a = QAction(self)
            a.setShortcut(QKeySequence(keys))
            a.triggered.connect(lambda _=False, f=fn: f())
            self.addAction(a)
        # только когда в фокусе список (не мешают полям ввода)
        tree_keys = [
            (("Return", "Enter"), self.open_task_sel),
            (("Delete",), lambda: self.remove_tasks(self.selected_tasks())),
            (("Shift+Delete",), lambda: self.delete_files(self.selected_tasks())),
            (("Ctrl+Up",), lambda: self.move_selected("up")),
            (("Ctrl+Down",), lambda: self.move_selected("down")),
            (("Ctrl+Home",), lambda: self.move_selected("top")),
            (("Ctrl+End",), lambda: self.move_selected("bottom")),
            (("Space",), self.toggle_groups),
            (("Ctrl+C",), lambda: self.copy_links(self.selected_tasks())),
            (("Ctrl+V",), self.paste_links),
            (("Ctrl+A",), self.tree.selectAll),
            (("Ctrl+0",), lambda: self.set_tag(0)),
            (("Ctrl++", "Ctrl+="), lambda: self.zoom_thumbs(1)),
            (("Ctrl+-",), lambda: self.zoom_thumbs(-1)),
        ]
        for n in range(1, 8):
            tree_keys.append(((f"Ctrl+{n}",), lambda n=n: self.set_tag(n)))
        for keys, fn in tree_keys:
            a = QAction(self.tree)
            a.setShortcuts([QKeySequence(k) for k in keys])
            a.setShortcutContext(Qt.WidgetShortcut)
            a.triggered.connect(lambda _=False, f=fn: f())
            self.tree.addAction(a)

    # ---------- вспомогательное ----------
    def update_title(self):
        self.setWindowTitle(f"{APP_TITLE} — {self.settings.folder}")

    def flash(self, text, ms=5000):
        self._flash_id += 1
        fid = self._flash_id
        self.msg_label.setText(text)
        QTimer.singleShot(ms, lambda: self.msg_label.setText("") if fid == self._flash_id else None)

    def notify_tray(self, title, text):
        if self.tray.isVisible():
            self.tray.showMessage(title, text, QSystemTrayIcon.Information, 4000)

    def thumb_pixmap(self, t, w, h):
        if not t.thumb_file:
            return None
        key = (t.thumb_file, w, h)
        pm = self._thumbs.get(key)
        if pm is None:
            if len(self._thumbs) > 600:
                self._thumbs.clear()
            src = QPixmap(t.thumb_file)
            if src.isNull():
                pm = False
            else:
                sc = src.scaled(w, h, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
                pm = sc.copy((sc.width() - w) // 2, (sc.height() - h) // 2, w, h)
            self._thumbs[key] = pm
        return pm or None

    def selected_tasks(self):
        out, seen = [], set()
        for it in self.tree.selectedItems():
            if it.data(0, ROLE_KIND) == "group":
                ids = [it.child(i).data(0, ROLE_ID) for i in range(it.childCount())]
            else:
                ids = [it.data(0, ROLE_ID)]
            for tid in ids:
                t = self.store.get(tid)
                if t is not None and tid not in seen:
                    seen.add(tid)
                    out.append(t)
        return out

    def selected_groups(self):
        return [it.data(0, ROLE_NAME) for it in self.tree.selectedItems()
                if it.data(0, ROLE_KIND) == "group"]

    # ---------- список ----------
    def schedule_rebuild(self):
        if not self._rb_pending:
            self._rb_pending = True
            QTimer.singleShot(30, self.rebuild)

    def rebuild(self):
        self._rb_pending = False
        self.hide_popup()
        self._building = True
        sel = {t.id for t in self.selected_tasks()}
        gsel = set(self.selected_groups())
        vbar = self.tree.verticalScrollBar()
        pos = vbar.value()
        self.tree.setUpdatesEnabled(False)
        self.tree.clear()
        self.items = {}
        q = self.filter_edit.text()
        dups = compute_dups(self.store.tasks) if "dup:" in q.lower() else None
        groups = {}
        self.update_site_tabs()
        shown = [t for t in self.store.tasks
                 if (not q or match_task(t, q, dups)) and task_in_tab(t, self.site_filter)]

        def add_task(parent, t):
            it = QTreeWidgetItem(parent)
            it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            it.setData(0, ROLE_KIND, "task")
            it.setData(0, ROLE_ID, t.id)
            self.items[t.id] = it
            if t.id in sel:
                it.setSelected(True)

        for entry in display_order(shown, self.settings.newest_first):
            if entry[0] == "task":
                add_task(self.tree, entry[1])
                continue
            g = QTreeWidgetItem(self.tree)
            g.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            g.setData(0, ROLE_KIND, "group")
            g.setData(0, ROLE_NAME, entry[1])
            groups[entry[1]] = g
            for t in entry[2]:
                add_task(g, t)
        for name, g in groups.items():
            g.setExpanded(bool(q) or name not in self.store.collapsed)
            if name in gsel:
                g.setSelected(True)   # выделенная группа остаётся выделенной после перестройки списка
        self.tree.setUpdatesEnabled(True)
        vbar.setValue(pos)
        self._building = False
        self.refresh()

    def update_site_tabs(self, rebuild_if_changed=False):
        """Пересобирает вкладки, только если изменился набор вкладок или их счётчики."""
        counts = {}
        for t in self.store.tasks:
            if t.site:
                counts[t.site] = counts.get(t.site, 0) + 1
        n_pl = sum(1 for t in self.store.tasks if t.group)
        n_music = sum(1 for t in self.store.tasks if is_music(t))
        n_err = sum(1 for t in self.store.tasks if is_error(t))
        sig = (tuple(counts.items()), len(self.store.tasks), n_pl, n_music, n_err,
               self.settings.tab_order, self.settings.tab_pinned)
        if sig == self._tabs_sig:
            return
        self._tabs_sig = sig
        special = {PLAYLISTS_TAB: n_pl, MUSIC_TAB: n_music, ERRORS_TAB: n_err}
        stale = False
        if self.site_filter and not (self.site_filter in counts or special.get(self.site_filter)):
            self.site_filter = ""
            stale = True
        titles = {TAB_ALL: tr(f"Все ({len(self.store.tasks)})")}
        for key, title in ((ERRORS_TAB, tr("Ошибки")), (MUSIC_TAB, tr("Музыка")), (PLAYLISTS_TAB, tr("Плейлисты"))):
            if special[key]:
                titles[key] = f"{title} ({special[key]})"
        for site, n in counts.items():
            titles[site] = f"{site} ({n})"
        pinned = split_keys(self.settings.tab_pinned)
        keys = order_tabs(list(titles), split_keys(self.settings.tab_order), pinned)
        tb = self.site_tabs
        tb.blockSignals(True)
        for i in range(tb.count() - 1, -1, -1):
            tb.removeTab(i)
        cur = 0
        for key in keys:
            i = tb.addTab(("📌 " if key in pinned else "") + titles[key])
            flt = "" if key == TAB_ALL else key
            tb.setTabData(i, flt)
            if flt == self.site_filter:
                cur = i
        tb.setCurrentIndex(cur)
        tb.blockSignals(False)
        tb.setVisible(bool(counts) or any(special.values()))
        # задача могла выйти из вкладки (ошибка исчезла после повтора) — список надо показать заново
        if rebuild_if_changed and (stale or self.site_filter in (MUSIC_TAB, ERRORS_TAB)):
            self.rebuild()

    def tab_keys(self):
        """Ключи вкладок в том порядке, как они сейчас стоят на экране."""
        tb = self.site_tabs
        return [(tb.tabData(i) or TAB_ALL) for i in range(tb.count())]

    def on_tab_moved(self):
        """Вкладку перетащили: запоминаем порядок. Закреплённые всё равно остаются левее остальных."""
        try:
            dragging = bool(QApplication.mouseButtons() & Qt.LeftButton)
        except TypeError:
            dragging = False
        if dragging:   # кнопка ещё нажата: перестройка вкладок посреди перетаскивания сбила бы его
            QTimer.singleShot(150, self.on_tab_moved)
            return
        keys = self.tab_keys()
        self.settings.tab_order = ",".join(keys + [k for k in split_keys(self.settings.tab_order) if k not in keys])
        self._tabs_sig = None
        self.update_site_tabs()

    def tab_menu(self, pos):
        i = self.site_tabs.tabAt(pos)
        if i < 0:
            return
        key = self.site_tabs.tabData(i) or TAB_ALL
        pinned = split_keys(self.settings.tab_pinned)
        m = QMenu(self)
        if key in pinned:
            m.addAction(tr("Открепить вкладку"), lambda: self.pin_tab(key, False))
        else:
            m.addAction(tr("Закрепить вкладку (слева, перед остальными)"), lambda: self.pin_tab(key, True))
        m.exec_(self.site_tabs.mapToGlobal(pos))

    def pin_tab(self, key, on):
        pinned = [k for k in split_keys(self.settings.tab_pinned) if k != key]
        if on:
            pinned.append(key)
        self.settings.tab_order = ",".join(self.tab_keys())  # фиксируем текущий порядок, чтобы вкладки не прыгали
        self.settings.tab_pinned = ",".join(pinned)
        self._tabs_sig = None
        self.update_site_tabs()

    def on_site_tab(self, i):
        site = self.site_tabs.tabData(i) if i >= 0 else ""
        site = site or ""
        if site == self.site_filter:
            return
        self.site_filter = site
        self.rebuild()

    def on_expand(self, it):
        if not self._building and it.data(0, ROLE_KIND) == "group":
            self.store.collapsed.discard(it.data(0, ROLE_NAME))

    def on_collapse(self, it):
        if not self._building and it.data(0, ROLE_KIND) == "group":
            self.store.collapsed.add(it.data(0, ROLE_NAME))

    def toggle_groups(self):
        gitems = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        gitems = [g for g in gitems if g.data(0, ROLE_KIND) == "group"]
        sel = [g for g in gitems if g.isSelected()]
        targets = sel or gitems
        if not targets:
            return
        expand = not all(g.isExpanded() for g in targets) if sel else any(not g.isExpanded() for g in gitems)
        for g in targets:
            g.setExpanded(expand)

    def refresh(self):
        stats, speed, prog_sum = {}, 0.0, 0.0
        for t in self.store.tasks:
            p = 1.0 if t.state == DONE else t.progress
            prog_sum += p
            if t.state == DOWNLOADING:
                speed += t.speed or 0
            if t.group:
                d, n, pr, gb, gu = stats.get(t.group, (0, 0, 0.0, 0, 0))
                sz = task_size(t)
                stats[t.group] = (d + (1 if t.state == DONE else 0), n + 1, pr + p, gb + sz,
                                  gu + (0 if sz or t.state == INVALID else 1))
        self.group_stats = stats
        self.update_site_tabs(rebuild_if_changed=True)
        done, total = self.store.counts()
        self.counter.setText(f"{done} / {total}")
        self.bar.setMaximum(1000)
        self.bar.setValue(int(prog_sum * 1000 / total) if total else 0)
        self.speed_label.setText(fmt_speed(speed))
        want = self.settings.prevent_sleep and any(t.state == DOWNLOADING for t in self.store.tasks)
        if want != self._awake:
            self._awake = want
            set_keep_awake(want)
        self.tree.viewport().update()

    def on_refresh_timer(self):
        if self._dirty:
            self._dirty = False
            self.refresh()
        self.check_renamed_folders()

    def check_renamed_folders(self):
        """Следим за файлами: папку плейлиста или сам файл переименовали / перенесли в Проводнике — обновляем пути
        (и название группы), чтобы файлы открывались как раньше. Проверка в фоне, не чаще раза в 5 секунд."""
        now = time.time()
        if now - self._ren_last < 5 or self._ren_busy:
            return
        self._ren_last = now
        snap = [types.SimpleNamespace(id=t.id, files=list(t.files), fsig=list(t.fsig), size=t.size,
                                      group=t.group, state=t.state) for t in self.store.tasks if t.files]
        if not snap:
            return
        self._ren_busy = True
        roots = [self.settings.folder]
        busy = lambda t: t.state in (READING, DOWNLOADING)

        def work():
            dirs, moves = {}, {}
            try:
                dirs = find_renamed_dirs(snap, skip=busy, cache=self._ren_cache, now=now)
                if not dirs:   # переименованные папки сначала, переименованные файлы — на следующем круге
                    moves = find_moved_files(snap, roots, skip=busy, cache=self._mv_cache, now=now)
            except Exception:
                pass
            self.bus.renames.emit(dirs, moves)
        threading.Thread(target=work, daemon=True).start()

    def on_renames(self, dirs, moves):
        self._ren_busy = False
        if not dirs and not moves:
            return
        shown = ""
        if dirs:
            groups, n = apply_dir_renames(self.store.tasks, dirs, self.store.collapsed)
            for old, new in dirs.items():
                self.engine.archive.rebase(old, new)
            self.logs.append(time.strftime("%H:%M:%S  ") + tr("[папки] " + "; ".join(f"{o} -> {w}" for o, w in dirs.items())))
            if groups:
                old, new = next(iter(groups.items()))
                shown = tr(f"Папка «{old}» переименована в «{new}»: группа и пути к файлам обновлены")
            else:
                shown = tr(f"Папка переименована: пути к файлам обновлены ({n})")
        n = 0
        for (tid, i), (old, new) in moves.items():
            t = self.store.get(tid)
            if t is not None and i < len(t.files) and t.files[i] == old and os.path.exists(new):
                t.files[i] = new
                self.engine.archive.rebase(old, new)
                self.logs.append(time.strftime("%H:%M:%S  ") + tr(f"[файлы] {old} -> {new}"))
                n += 1
        if n:
            shown = tr(f"Файл переименован или перенесён: путь обновлён ({n})")
        if shown:
            self.flash(shown, 8000)
            self.rebuild()
            self.save_state(quiet=True)

    # ---------- сигналы движка ----------
    def on_changed(self, tid):
        self._dirty = True
        if self.tg is not None and self.settings.tg_notify:
            t = self.store.get(tid)
            if t is None:
                return
            if (t.state in (INVALID, INCOMPLETE) and t.error and not t.retry_at and t.error != "Остановлено"
                    and self._tg_err.get(tid) != t.state):
                self._tg_err[tid] = t.state
                self.tg_notify(tr(f"❌ Ошибка: {t.display_title}\n{tr(t.error)}"))
            elif t.state not in (INVALID, INCOMPLETE):
                self._tg_err.pop(tid, None)

    # ---------- Telegram-бот ----------
    def apply_telegram(self):
        """Запускает или останавливает бота по настройкам (вызывается при старте и после «Параметров»)."""
        st = self.settings
        if self.tg is not None:
            self.tg.stop()
            self.tg = None
        if st.tg_enabled and st.tg_token.strip():
            guests = parse_chat_ids(st.tg_users)   # гости из прежней версии: переносим в таблицу участников и забываем
            if guests:
                st.tg_users = ""
            m = re.search(r"-?\d+", st.tg_owner or "")   # руководитель — первый id в поле
            owner = [int(m.group())] if m else []
            self.members.set_owner(owner[0] if owner else None)
            self.tg = eng.TelegramBot(st.tg_token, parse_chat_ids(st.tg_admin) | set(owner), self.tg_on_message,
                                      log=lambda s: self.bus.log.emit(s), proxy=st.proxy,
                                      guests=guests, members=self.members)
            self.tg.auto_accept = st.tg_auto_guest
            self.tg.start()
        else:
            self.members.owner = None   # бот выключен: руководителя не «снимаем» в таблице, просто не действует


    def tg_notify(self, text):
        """Уведомление всем администраторам (через очередь бота: окно не ждёт сети)."""
        bot = self.tg
        if bot is None:
            return
        for aid in bot.admins:
            bot.say(aid, text)

    def tg_on_message(self, chat, text, user_id, is_admin):
        """Работает в потоке бота (бот уже проверил, что участник допущен). Гостю админ-команды недоступны."""
        bot = self.tg
        if bot is None:
            return
        cmd, arg = tg_parse(text)
        if not cmd:
            cmd = tg_button_cmd(text)   # нажата кнопка меню внизу чата (права проверяются ниже)
        kb = bot.keyboard_for(user_id)
        if cmd == "hide":   # скрыть кнопки внизу чата (доступно всем)
            self.members.set_hidden(user_id, True)
            bot.say(chat, tr("Кнопки скрыты. Чтобы вернуть их, отправьте /menu."), {"remove_keyboard": True})
            return
        if cmd == "menu":
            self.members.set_hidden(user_id, False)
            bot.say(chat, tr("Кнопки показаны."), bot.keyboard_for(user_id, True))
            return
        if cmd == "sites":   # доступно всем
            bot.say(chat, self.tg_sites_text(is_admin), kb)
            return
        if not is_admin and cmd:   # гость: только ссылки; все команды — для администратора
            bot.say(chat, tr("Эта команда только для администратора.") if cmd in TG_ADMIN_CMDS
                    else tr(TG_GUEST_HELP) + "\n" + self.tg_sites_text(False), kb)
            return
        if cmd in TG_OWNER_CMDS and user_id != self.members.owner:   # управление людьми — только руководитель
            bot.say(chat, tr("Эта команда только для руководителя."), kb)
            return
        if not is_admin:   # гостю — только ссылки с разрешённых сайтов
            keys = tg_site_keys(self.settings.tg_guest_sites)
            urls = parse_inputs(text)
            ok = [u for u in urls if tg_site_allowed(u, keys)]
            if urls and not ok:
                names = ", ".join(tr(t) for k, t, _ in TG_SITES if k in keys) or tr("ничего")
                bot.say(chat, tr("Гостям разрешены только сайты: ") + names + ".", kb)
                return
            if len(ok) < len(urls):
                bot.say(chat, tr(f"Часть ссылок пропущена: сайт не разрешён гостям ({len(urls) - len(ok)})."))
            if ok and self.settings.tg_guest_no_playlists:
                singles = [tg_strip_list(u) for u in ok if not tg_is_playlist(u)]
                if len(singles) < len(ok):
                    bot.say(chat, tr("Плейлисты, каналы и профили целиком гостям недоступны: пришлите ссылку на отдельный ролик.")
                            if not singles else tr(f"Пропущено плейлистов и каналов: {len(ok) - len(singles)}."), kb)
                ok = singles
                if not ok:
                    return
            if ok:
                text = "\n".join(ok)
        req = types.SimpleNamespace(cmd=cmd, arg=arg, text=text, result=None, inline=None, owner=(user_id == self.members.owner), ev=threading.Event())
        self.bus.tg.emit(req)
        if not req.ev.wait(30):
            bot.say(chat, tr("Программа не ответила вовремя. Попробуйте ещё раз."))
            return
        reply, path = req.result or ("", None)
        if reply:
            bot.say(chat, reply, getattr(req, "inline", None) or kb)
        if path:
            bot.send_file(chat, path)

    def tg_sites_text(self, is_admin):
        keys = tg_site_keys(self.settings.tg_guest_sites)
        names = ", ".join(tr(t) for k, t, _ in TG_SITES if k in keys) or tr("ничего")
        if is_admin:
            return tr("Вам разрешены любые сайты. Гостям: ") + names + "."
        return tr("Гостям разрешены только сайты: ") + names + "."

    def members_changed(self, uid, role):
        """Администратор решил судьбу участника в окне «Участники Telegram»: сообщаем ему об этом."""
        bot = self.tg
        if bot is not None and role in ("admin", "guest"):
            bot.say(uid, tr("Доступ открыт. Пришлите ссылку или /help."), bot.keyboard_for(uid))
            bot.refresh_menu(uid)

    def open_members(self):
        MembersDialog(self, self.members, self.members_changed).exec_()

    def on_tg(self, req):
        try:
            req.result = self.tg_handle(req.cmd, req.arg, req.text)
            if req.cmd in ("start", "help") and getattr(req, "owner", False):
                req.result = (req.result[0] + "\n" + tr(TG_OWNER_HELP), None)
            if req.cmd in ("list", "status"):
                req.inline = self.tg_file_buttons()
        except Exception as ex:
            req.result = (tr(f"Ошибка: {ex}"), None)
        finally:
            req.ev.set()

    def tg_file_buttons(self):
        """Кнопки под списком: нажмёшь — бот пришлёт файл этой загрузки (последние 8 готовых)."""
        rows = []
        for t in reversed(self.store.tasks):
            if len(rows) >= 8:
                break
            f = t.first_file()
            if t.state == DONE and f and os.path.getsize(f) <= eng.TelegramBot.MAX_FILE:
                rows.append([{"text": f"📎 #{t.id} {(t.display_title or '')[:35]}", "callback_data": f"file:{t.id}"}])
        return {"inline_keyboard": rows} if rows else None

    def tg_owner_cmd(self, cmd, arg):
        """Команды руководителя: управление участниками."""
        mem = self.members
        if cmd == "members":
            rows = [f"{uid} — {m['name'] or tr('Без имени')}" + (f" (@{m['username']})" if m["username"] else "")
                    + " — " + tr(MembersDialog.ROLE_TEXT.get(m['role'], m['role'])) for uid, m in mem.items() if uid != mem.owner]
            return "\n".join(rows) or tr("Участников пока нет.")
        m = re.fullmatch(r"\s*(-?\d{1,20})\s*", arg or "")   # только число целиком: «@ivan_2» не должно дать id 2
        if not m:
            return tr("Укажите id: например /allow 123456789. Id и роли — в /members.")
        uid = int(m.group(1))
        if uid == mem.owner:
            return tr("Руководителя изменить нельзя.")
        if cmd == "remove":
            mem.remove(uid)
            return tr(f"Удалён: {uid}")
        role = {"allow": "guest", "admin": "admin", "block": "blocked"}[cmd]
        mem.set_role(uid, role)
        self.members_changed(uid, role)
        return tr(f"Готово: {uid} — {tr(MembersDialog.ROLE_TEXT.get(role, role))}")

    def tg_handle(self, cmd, arg, text):
        """Команда от бота -> (ответ, путь к файлу или None). Выполняется в окне программы."""
        if cmd in ("start", "help"):
            return tr(TG_HELP), None
        if cmd in TG_OWNER_CMDS:
            return self.tg_owner_cmd(cmd, arg), None
        if cmd in ("list", "status"):
            return tg_list(self.store.tasks), None
        if cmd == "pause":
            self.pause_act.setChecked(True)
            return tr("Все загрузки на паузе"), None
        if cmd == "resume":
            self.pause_act.setChecked(False)
            return tr("Скачивание возобновлено"), None
        if cmd == "retry":
            n = sum(1 for t in self.store.tasks if t.state in (INVALID, INCOMPLETE))
            self.restart_failed()
            return tr(f"Перезапущено задач: {n}"), None
        if cmd == "file":
            m = re.search(r"\d+", arg or "")
            t = self.store.get(int(m.group())) if m else None
            if t is None:
                return tr("Не нашёл загрузку с таким номером. Номера — в /list (например #12)."), None
            f = t.first_file()
            if not f:
                return tr("У этой загрузки нет файла на диске."), None
            if os.path.getsize(f) > eng.TelegramBot.MAX_FILE:
                return tr("Файл больше 50 МБ: Telegram не принимает такие от ботов."), None
            return tr("Отправляю файл..."), f
        if cmd:
            return tr("Не знаю такой команды.") + "\n" + tr(TG_HELP), None
        if not parse_inputs(text):
            return tr("Не нашёл ссылок. Пришлите ссылку или /help."), None
        n = self.add_from_text(text)
        return tr(f"Принято ссылок: {n}"), None

    def on_done(self, tid):
        self._ran = True
        t = self.store.get(tid)
        if t is None:
            return
        if self.settings.notify_done:
            self.notify_tray(tr("Скачивание завершено"), t.display_title)
        if self.tg is not None and self.settings.tg_notify:
            self.tg_notify(tr(f"✅ Готово: {t.display_title} ({fmt_size(t.size)})" if t.size else f"✅ Готово: {t.display_title}"))
        if self.settings.auto_remove_done:
            QTimer.singleShot(2500, lambda: self.remove_tasks([x for x in [self.store.get(tid)]
                                                               if x and x.state == DONE]))

    def on_idle(self):
        self.save_state(quiet=True)
        if self._ran:
            self._ran = False
            done, total = self.store.counts()
            if self.settings.notify_all_done:
                self.notify_tray(tr("Все задачи выполнены"), f"{done} / {total}")
            if self.tg is not None and self.settings.tg_notify:
                self.tg_notify(tr(f"Все задачи выполнены: {done} / {total}"))
            if self.settings.after_all == "exit":
                def _exit_if_idle():
                    if any(t.state in (READING, DOWNLOADING, WAITING, QUEUED) or t.retry_at for t in self.store.tasks):
                        return   # за эти 1,5 секунды добавили новое — не закрываемся
                    self._force = True
                    self.close()
                QTimer.singleShot(1500, _exit_if_idle)

    def on_playlist(self, tid, title, entries):
        t = self.store.get(tid)
        if t is None:
            return
        total = len(entries)
        if total == 0:
            t.state, t.error = INVALID, tr("Список пуст")
            self.refresh()
            return
        idxs = list(range(1, total + 1))
        if t.pages:
            try:
                idxs = parse_range(t.pages, total)
            except ValueError:
                pass
        if t.ask_select or (self.settings.ask_playlist and len(idxs) > 1):
            reuse_ = self.settings.use_archive and not t.force_new
            if reuse_:
                self.engine.archive.backfill(self.store.tasks)
            done = {i for i in idxs if reuse_ and self.engine.archive.find(
                entries[i - 1]["url"], entries[i - 1]["id"], t.mode)}
            dlg = SelectDialog(self, title, [(i, entries[i - 1]["title"] or entries[i - 1]["url"])
                                             for i in idxs],
                               heading=tr("Выбор глав") if t.ask_select else tr("Выбор роликов"), done=done,
                               sizes={i: entries[i - 1].get("size", 0) for i in idxs},
                               durations={i: entries[i - 1].get("duration", 0) for i in idxs},
                               probe=lambda i: self.probe_entry_size(entries[i - 1]["url"], t.mode))
            ok = dlg.exec_() == QDialog.Accepted
            picked = dlg.chosen() if ok else []
            getattr(dlg, "deleteLater", lambda: None)()
            if not ok:
                self.remove_tasks([t])
                return
            idxs = picked
        if not idxs:
            self.remove_tasks([t])
            return
        group = t.group or safe_name(title)  # папка = название плейлиста (повторная загрузка идёт в ту же)
        reuse = self.settings.use_archive and not t.force_new
        if reuse:   # загрузки, сделанные до появления архива, тоже считаются
            self.engine.archive.backfill(self.store.tasks)
        new, linked = [], []
        for i in idxs:
            e = entries[i - 1]
            nt = self.store.new_task(e["url"], title=e["title"], vid=e["id"], thumb_url=e["thumb"],
                                     folder=t.folder, mode=t.mode, group=group)
            old = self.engine.archive.find(e["url"], e["id"], t.mode) if reuse else None
            if old:   # уже скачано раньше: не качаем второй раз, а подключаем тот же файл (он откроется и отсюда)
                nt.files, nt.state, nt.progress, nt.has_info, nt.backend = old, DONE, 1.0, True, "yt-dlp"
                nt.status = "Уже скачано раньше"
                nt.size = sum(os.path.getsize(f) for f in old if os.path.exists(f))
                nt.fsig = files_sigs(old)
                linked.append(nt)
            new.append(nt)
        self.engine.cancel(t)
        self.store.replace(t, new)
        self.rebuild()
        self.flash(tr(f"Добавлено из плейлиста «{title}»: {len(new)}") +
                   (tr(f"; уже скачано раньше, подключено без повторной загрузки: {len(linked)}") if linked else ""))
        if linked:
            threading.Thread(target=self.fill_thumbs, args=(linked,), daemon=True).start()

    def probe_entry_size(self, url, mode):
        """Вес одного ролика (точный, от сайта, или оценка по битрейту); 0, если определить не удалось."""
        pt = self.store.new_task(url, mode=mode)
        res = eng.probe_ytdlp(pt, self.settings, lambda *_: None)
        return res.get("size", 0) if res and res.get("kind") == "single" else 0

    def fill_thumbs(self, tasks):
        """Картинки для задач, подключённых из архива (скачивать их заново не нужно, нужна только миниатюра)."""
        for t in tasks:
            try:
                if t.thumb_url:
                    t.thumb_file = eng.fetch_thumb_any([t.thumb_url], referer=t.url, proxy=self.settings.proxy)
                f = t.first_file()
                if not t.thumb_file and f and f.lower().endswith(eng.VIDEO_EXT):
                    t.thumb_file = eng.thumb_from_video(f)
            except Exception:
                continue
            self.bus.changed.emit(t.id)

    # ---------- добавление ----------
    def add_from_text(self, text, pages="", ask=False, mode=None):
        urls = parse_inputs(text)
        if not urls:
            self.flash(tr("Ссылки не найдены"))
            return 0
        existing = {}
        for t in self.store.tasks:
            existing.setdefault(norm_url(t.url), t)
        added, found = 0, []
        for u in urls:
            key = norm_url(u)
            old = existing.get(key)
            if old is not None:  # уже есть в списке — не дублируем, а покажем её
                if old not in found:
                    found.append(old)
                continue
            t = self.store.new_task(u, folder=self.settings.folder, mode=mode or self.settings.mode,
                                    pages=pages, ask_select=ask)
            self.store.add(t)
            existing[key] = t
            added += 1
        if added:
            self.rebuild()
        if found:
            self.reveal(found)
            if added:
                self.flash(tr(f"Добавлено: {added}; уже в списке: {len(found)} (выделено)"))
            elif len(found) == 1:
                self.flash(tr("Эта ссылка уже в списке. Другой формат: правая кнопка → «Скачать заново в формате»"))
            else:
                self.flash(tr(f"Уже в списке: {len(found)} (выделены)"))
        else:
            if self.settings.newest_first:
                self.tree.scrollToTop()
            else:
                self.tree.scrollToBottom()
            self.flash(tr(f"Добавлено задач: {added}"))
        return len(urls)

    def reveal(self, tasks):
        """Выделить задачи в списке и прокрутить к первой (снимает фильтр и раскрывает группы)."""
        changed = False
        if self.site_filter and any(not task_in_tab(t, self.site_filter) for t in tasks):
            self.site_filter = ""
            self._tabs_sig = None
            changed = True
        if self.filter_edit.text():
            self._ftimer.stop()
            self.filter_edit.blockSignals(True)
            self.filter_edit.clear()
            self.filter_edit.blockSignals(False)
            changed = True
        for t in tasks:
            if t.group and t.group in self.store.collapsed:
                self.store.collapsed.discard(t.group)
                changed = True
        if changed or any(t.id not in self.items for t in tasks):
            self.rebuild()
        items = [self.items[t.id] for t in tasks if t.id in self.items]
        if not items:
            return
        self.tree.clearSelection()
        self.tree.setCurrentItem(items[0])  # выделяет первую
        for it in items[1:]:
            it.setSelected(True)
        self.tree.scrollToItem(items[0], QAbstractItemView.PositionAtCenter)

    def add_from_entry(self):
        if self.add_from_text(self.url_edit.text()):
            self.url_edit.clear()

    def add_with_pages(self):
        if not parse_inputs(self.url_edit.text()):
            self.flash(tr("Сначала введите ссылку"))
            return
        text, ok = QInputDialog.getText(
            self, tr("Выбор страниц и скачивание"),
            tr("Страницы:\n~ 100   — первые 100\n-100 ~   — последние 100\n1, 10 ~ 20, -1   — выборочно"))
        if not ok:
            return
        try:
            parse_range(text, 1000)
        except ValueError:
            QMessageBox.warning(self, tr("Ошибка"), tr("Не удалось разобрать диапазон страниц."))
            return
        if self.add_from_text(self.url_edit.text(), pages=text.strip()):
            self.url_edit.clear()

    def add_with_select(self):
        if self.add_from_text(self.url_edit.text(), ask=True):
            self.url_edit.clear()

    def clipboard_text(self):
        return QApplication.clipboard().text() or ""

    def paste_links(self):
        self.add_from_text(self.clipboard_text())

    def paste_and_download(self):
        """Скрепка: берёт ссылку из буфера обмена и сразу ставит на скачивание."""
        text = self.clipboard_text()
        if not parse_inputs(text):
            self.flash(tr("В буфере обмена нет ссылки"))
            return
        self.add_from_text(text)

    def add_with_mode(self, mode):
        if self.add_from_text(self.url_edit.text(), mode=mode):
            self.url_edit.clear()

    def add_subs_only(self):
        if self.add_from_text(self.url_edit.text(), mode="subs"):
            self.url_edit.clear()

    def add_audio_only(self):
        if self.add_from_text(self.url_edit.text(), mode="audio"):
            self.url_edit.clear()

    def open_links(self, tasks):
        """Открывает страницы выбранных загрузок в браузере по умолчанию (не больше 10 за раз)."""
        urls = [t.url for t in tasks if (t.url or "").lower().startswith(("http://", "https://"))]
        if not urls:
            self.flash(tr("Нечего открывать: выберите загрузку со ссылкой"))
            return
        for u in urls[:10]:
            QDesktopServices.openUrl(QUrl(u))
        if len(urls) > 10:
            self.flash(tr("Открыты первые 10 ссылок из %d") % len(urls))

    def copy_links(self, tasks):
        if not tasks:
            return
        text = "\n".join(t.url for t in tasks)
        self._last_clip = text
        QApplication.clipboard().setText(text)
        self.flash(tr(f"Скопировано ссылок: {len(tasks)}"))

    def import_links(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("Импорт ссылок"), "", tr("Текстовые файлы (*.txt);;Все файлы (*)"))
        if path:
            try:
                with open(path, encoding="utf-8-sig", errors="replace") as f:   # utf-8-sig: файл из «Блокнота» может начинаться с BOM
                    self.add_from_text(f.read())
            except OSError as e:
                QMessageBox.warning(self, tr("Ошибка"), str(e))

    def export_links(self):
        path, _ = QFileDialog.getSaveFileName(self, tr("Экспорт ссылок"), "links.txt", tr("Текстовые файлы (*.txt)"))
        if path:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write("\n".join(t.url for t in self.store.tasks))
            except OSError as e:
                QMessageBox.warning(self, tr("Ошибка"), str(e))

    def on_clip(self):
        if not self.settings.clipboard_monitor:
            return
        text = QApplication.clipboard().text().strip()
        if not text or text == self._last_clip or len(text) > 5000:
            return
        self._last_clip = text
        toks = text.split()
        if toks and all(tok.lower().startswith(("http://", "https://")) for tok in toks):
            self.add_from_text(text)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() or e.mimeData().hasText():
            e.acceptProposedAction()

    def dropEvent(self, e):
        md = e.mimeData()
        text = ""
        if md.hasUrls():
            text = "\n".join(u.toString() for u in md.urls() if u.scheme() in ("http", "https"))
            if not text:   # перетащили файл со ссылками (.txt): читаем из него
                for u in md.urls():
                    p = u.toLocalFile()
                    if p and os.path.isfile(p) and os.path.getsize(p) < 5 * 1024 * 1024:
                        try:
                            with open(p, encoding="utf-8-sig", errors="replace") as f:
                                text += f.read() + "\n"
                        except OSError:
                            pass
        if not text and md.hasText():
            text = md.text()
        self.add_from_text(text)

    # ---------- действия над задачами ----------
    def target_dir(self, t):
        f = t.first_file()
        return os.path.dirname(f) if f else eng.target_folder(t, self.settings)

    def open_task(self, t):
        if t is None:
            return
        f = t.first_file()
        if f:
            QDesktopServices.openUrl(QUrl.fromLocalFile(f))
        else:
            self.open_folder(t)

    def open_task_sel(self):
        for t in self.selected_tasks()[:10]:
            self.open_task(t)

    def open_folder(self, t):
        """Открывает папку; если файл уже скачан — выделяет его в Проводнике."""
        f = t.first_file()
        if f and os.path.exists(f):
            cmd = eng.reveal_command(f)
            if cmd:
                try:
                    subprocess.Popen(cmd)
                    return
                except Exception:
                    pass  # не вышло — откроем просто папку
        d = self.target_dir(t)
        if os.path.isdir(d):
            QDesktopServices.openUrl(QUrl.fromLocalFile(d))
        else:
            self.flash(tr("Папка ещё не создана"))

    def open_folder_sel(self):
        sel = self.selected_tasks()
        if sel:
            self.open_folder(sel[0])

    def open_merge(self, video=""):
        MergeDialog(self, video, self.settings.folder if os.path.isdir(self.settings.folder) else "").exec_()

    def open_extract(self, video=""):
        ExtractDialog(self, video, self.settings.folder if os.path.isdir(self.settings.folder) else "").exec_()

    def open_download_folder(self):
        try:
            os.makedirs(self.settings.folder, exist_ok=True)
        except OSError as e:
            QMessageBox.warning(self, tr("Ошибка"), tr(f"Не удалось открыть папку загрузки: {e}"))
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(self.settings.folder))

    def on_double_click(self, item, col):
        if item.data(0, ROLE_KIND) == "task":
            self.open_task(self.store.get(item.data(0, ROLE_ID)))

    def remove_tasks(self, tasks):
        if not tasks:
            return
        for t in tasks:
            self.engine.cancel(t)
            self.store.remove(t)
        self.rebuild()

    def delete_files(self, tasks):
        if not tasks:
            return
        files = list(dict.fromkeys(f for t in tasks for f in t.files if os.path.exists(f)))   # один файл у двух задач — один раз
        text = (tr(f"Удалить {len(files)} файл(ов) с диска и убрать задачи ({len(tasks)}) из списка?")
                if files else tr(f"Файлов на диске нет. Убрать задачи ({len(tasks)}) из списка?"))
        if QMessageBox.question(self, tr("Удалить файлы"), text, QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        busy = any(t.state in (READING, DOWNLOADING) for t in tasks)
        for t in tasks:   # сначала останавливаем загрузки: открытый ffmpeg/yt-dlp файл в Windows удалить нельзя
            self.engine.cancel(t)
        failed = []
        for f in files:
            for attempt in range(6 if busy else 1):
                try:
                    os.remove(f)
                    break
                except FileNotFoundError:
                    break
                except OSError as e:
                    if attempt >= (5 if busy else 0):
                        failed.append(f)
                        self.logs.append(tr(f"Не удалось удалить {f}: {e}"))
                    else:
                        time.sleep(0.25)
        self.remove_tasks(tasks)
        if failed:
            QMessageBox.warning(self, tr("Удалить файлы"),
                                tr(f"Не удалось удалить файлов: {len(failed)}. Возможно, они открыты в другой программе. Подробности в журнале."))

    def clear_done(self):
        self.remove_tasks([t for t in self.store.tasks if t.state == DONE])

    def clear_all(self):
        if self.store.tasks and QMessageBox.question(
                self, tr("Очистить список"), tr("Убрать все задачи из списка?"),
                QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            self.remove_tasks(list(self.store.tasks))

    def restart_tasks(self, tasks):
        n = sum(1 for t in tasks if self.engine.restart(t))
        self.rebuild()
        if n:
            self.flash(tr(f"Перезапущено задач: {n}"))

    def redownload(self, tasks, mode):
        """Скачать задачи заново в выбранном формате (старые файлы остаются на диске)."""
        n = 0
        for t in tasks:
            if t.state in (READING, DOWNLOADING):
                continue
            t.mode = mode
            t.fmt_sel = ""   # выбранный вручную формат перекрывал бы выбранный режим
            t.files = []
            if self.engine.restart(t):
                n += 1
        self.rebuild()
        if n:
            self.flash(tr(f"Скачиваю заново ({dict(tr_pairs(MODES)).get(mode, mode)}): {n}"))

    def restart_failed(self):
        self.restart_tasks([t for t in self.store.tasks if t.state in (INVALID, INCOMPLETE)])

    def move_selected(self, where):
        if self.settings.newest_first:  # список показан наоборот: «вверх» на экране — это «вниз» в очереди
            where = {"up": "down", "down": "up", "top": "bottom", "bottom": "top"}.get(where, where)
        ids = {t.id for t in self.selected_tasks()}
        if not ids:
            return
        tasks = self.store.tasks
        if where == "top":
            tasks[:] = [t for t in tasks if t.id in ids] + [t for t in tasks if t.id not in ids]
        elif where == "bottom":
            tasks[:] = [t for t in tasks if t.id not in ids] + [t for t in tasks if t.id in ids]
        elif where == "up":
            for i in range(1, len(tasks)):
                if tasks[i].id in ids and tasks[i - 1].id not in ids:
                    tasks[i - 1], tasks[i] = tasks[i], tasks[i - 1]
        elif where == "down":
            for i in range(len(tasks) - 2, -1, -1):
                if tasks[i].id in ids and tasks[i + 1].id not in ids:
                    tasks[i + 1], tasks[i] = tasks[i], tasks[i + 1]
        self.rebuild()

    def set_tag(self, n):
        for t in self.selected_tasks():
            t.tag = n
        self.tree.viewport().update()

    def edit_comment(self, tasks):
        if not tasks:
            return
        text, ok = QInputDialog.getText(self, tr("Комментарий"), tr("Комментарий:"), text=tasks[0].comment)
        if ok:
            for t in tasks:
                t.comment = text.strip()
            self.tree.viewport().update()

    def move_to_group(self, tasks):
        if not tasks:
            return
        groups = self.store.groups()
        if groups:
            name, ok = QInputDialog.getItem(self, tr("Переместить в группу"),
                                            tr("Выберите группу или введите новое название:"), groups, 0, True)
        else:
            name, ok = QInputDialog.getText(self, tr("Новая группа"), tr("Название группы:"))
        name = safe_name(name, "") if ok else ""
        if not name:
            return
        for t in tasks:
            t.group = name
        self.rebuild()

    def ungroup(self, tasks):
        for t in tasks:
            t.group = ""
        self.rebuild()

    def rename_group(self, old):
        name, ok = QInputDialog.getText(self, tr("Переименовать группу"), tr("Название группы:"), text=old)
        name = safe_name(name, "") if ok else ""
        if not name or name == old:
            return
        if name in self.store.groups():
            QMessageBox.warning(self, tr("Ошибка"), tr("Группа с таким названием уже есть."))
            return
        for t in self.store.tasks:
            if t.group == old:
                t.group = name
        if old in self.store.collapsed:
            self.store.collapsed.discard(old)
            self.store.collapsed.add(name)
        self.rebuild()

    def show_info(self, t):
        rows = [(tr("Название"), t.display_title), (tr("Автор"), t.artist), (tr("Ссылка"), t.url), (tr("Сайт"), t.site),
                (tr("Состояние"), status_text(t)), (tr("Бэкенд"), t.backend),
                (tr("Режим"), dict(tr_pairs(MODES)).get(t.mode, t.mode)), (tr("Скачанные потоки"), tr(t.fmt_info)), (tr("Папка"), self.target_dir(t)),
                (tr("Размер"), fmt_size(t.total or t.size)), (tr("Длительность"), fmt_duration(t.duration)),
                (tr("Группа"), t.group), (tr("Комментарий"), t.comment)]
        text = "\n".join(f"{k}: {v}" for k, v in rows if v)
        if t.files:
            text += tr("\n\nФайлы:\n") + "\n".join(t.files[:200])
        TextDialog(self, tr("Информация о задаче"), text).exec_()

    def show_menu(self, pos):
        item = self.tree.itemAt(pos)
        if item is not None and not item.isSelected():
            self.tree.clearSelection()
            item.setSelected(True)
        tasks = self.selected_tasks()
        groups = self.selected_groups()
        self.hide_popup()
        m = QMenu(self)
        m.setFont(plain_font())
        if tasks:
            m.addAction(tr("Открыть первый файл (Enter)"), self.open_task_sel)
            m.addAction(tr("Показать в папке"), self.open_folder_sel)
            if len(tasks) == 1 and tasks[0].first_file() and os.path.exists(tasks[0].first_file()) \
                    and tasks[0].first_file().lower().endswith(eng.VIDEO_EXT):
                m.addAction(tr("Добавить субтитры / звук к файлу..."),
                            lambda: self.open_merge(tasks[0].first_file()))
                m.addAction(tr("Извлечь звук / субтитры из файла..."),
                            lambda: self.open_extract(tasks[0].first_file()))
            m.addAction(tr("Открыть ссылку в браузере"), lambda: self.open_links(tasks))
            m.addAction(tr("Скопировать ссылку (Ctrl+C)"), lambda: self.copy_links(tasks))
            m.addSeparator()
            live = [t for t in tasks if t.state in (QUEUED, READING, WAITING, DOWNLOADING)]
            if any(not t.paused for t in live):
                m.addAction(tr("Пауза"), lambda: self.pause_tasks(tasks))
            if any(t.paused for t in live):
                m.addAction(tr("Продолжить"), lambda: self.resume_tasks(tasks))
            m.addAction(tr("Перезапустить"), lambda: self.restart_tasks(tasks))
            if len(tasks) == 1 and tasks[0].backend != "gallery-dl" and tasks[0].state not in (READING, DOWNLOADING):
                m.addAction(tr("Выбрать формат..."), lambda: self.pick_format(tasks[0]))
            fmt_menu = m.addMenu(tr("Скачать заново в формате"))
            for key, label in tr_pairs(MODES):
                fmt_menu.addAction(label, lambda _=False, k=key: self.redownload(tasks, k))
            tag_menu = m.addMenu(tr("Цветная метка"))
            for n in range(1, 8):
                a = tag_menu.addAction(f"{n}.  {tr(TAGS[n][1])}   (Ctrl+{n})", lambda _=False, n=n: self.set_tag(n))
                pm = QPixmap(14, 14)
                pm.fill(QColor(TAGS[n][2]))
                a.setIcon(QIcon(pm))
            tag_menu.addAction(tr("Снять метку (Ctrl+0)"), lambda: self.set_tag(0))
            m.addAction(tr("Комментарий..."), lambda: self.edit_comment(tasks))
            m.addAction(tr("Переместить в группу..."), lambda: self.move_to_group(tasks))
            if any(t.group for t in tasks):
                m.addAction(tr("Убрать из группы"), lambda: self.ungroup(tasks))
            if len(tasks) == 1:
                m.addAction(tr("Информация..."), lambda: self.show_info(tasks[0]))
            m.addSeparator()
        if groups:
            m.addAction(tr("Переименовать группу..."), lambda: self.rename_group(groups[0]))
            m.addAction(tr("Копировать названия групп"), lambda: QApplication.clipboard().setText("\n".join(groups)))
            m.addSeparator()
        if tasks:
            m.addAction(tr("Убрать из списка (Del)"), lambda: self.remove_tasks(tasks))
            m.addAction(tr("Удалить файлы (Shift+Del)"), lambda: self.delete_files(tasks))
        m.addAction(tr("Убрать все завершённые"), self.clear_done)
        m.exec_(self.tree.viewport().mapToGlobal(pos))

    # ---------- прочие действия ----------
    def toggle_pause(self, checked):
        self.engine.set_paused(checked)
        self.flash(tr("Все загрузки на паузе") if checked else tr("Скачивание возобновлено"))
        self.update_pause_button()
        self.refresh()

    def update_pause_button(self):
        paused = self.engine.paused.is_set()
        th = self.theme
        self.btn_pause.setIcon(icon_play(18, th["accent"]) if paused else icon_pause(18, th["muted"]))
        self.btn_pause.setToolTip(tr("Продолжить все загрузки (Ctrl+P)") if paused
                                  else tr("Поставить все загрузки на паузу (Ctrl+P)"))

    def pause_tasks(self, tasks):
        n = sum(1 for t in tasks if self.engine.pause_task(t))
        self.refresh()
        if n:
            self.flash(tr(f"На паузе: {n}"))

    def resume_tasks(self, tasks):
        n = sum(1 for t in tasks if t.paused and self.engine.resume_task(t))
        self.refresh()
        if n:
            self.flash(tr(f"Продолжено: {n}"))

    # ---------- крупный предпросмотр миниатюры ----------
    def on_thumb_hover(self, tid, pos):
        self._hover_pos = pos
        if tid != self._hover_id:
            self.popup.hide()
            self._hover_id = tid
            self._hover_timer.start(250)
        elif self.popup.isVisible():
            self.place_popup()

    def hide_popup(self):
        self._hover_timer.stop()
        self._hover_id = 0
        self.popup.hide()

    def big_pixmap(self, path):
        pm = self._big_thumbs.get(path)
        if pm is None:
            if len(self._big_thumbs) > 30:
                self._big_thumbs.clear()
            src = QPixmap(path)
            pm = False if src.isNull() else src.scaled(560, 420, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self._big_thumbs[path] = pm
        return pm or None

    def show_popup(self):
        t = self.store.get(self._hover_id)
        if t is None or not t.thumb_file or self._hover_pos is None:
            return
        pm = self.big_pixmap(t.thumb_file)
        if pm is None:
            return
        self.popup.setPixmap(pm)
        self.popup.adjustSize()
        self.place_popup()
        self.popup.show()

    def place_popup(self):
        pos = self._hover_pos
        screen = QApplication.screenAt(pos) or QApplication.primaryScreen()
        ag = screen.availableGeometry()
        w, h = self.popup.width(), self.popup.height()
        x, y = pos.x() + 24, pos.y() + 16
        if x + w > ag.right():
            x = pos.x() - w - 24
        if y + h > ag.bottom():
            y = ag.bottom() - h
        self.popup.move(max(ag.left(), x), max(ag.top(), y))

    def toggle_focus(self):
        if self.url_edit.hasFocus():
            self.tree.setFocus()
        else:
            self.topbar.show()
            self.url_edit.setFocus()

    def toggle_topbar(self):
        self.topbar.setVisible(not self.topbar.isVisible())

    def zoom_thumbs(self, d):
        s = self.settings
        s.thumb_size = min(140, max(44, s.thumb_size + 6 * d))
        self.tree.row_h = s.thumb_size + 12
        self.tree.doItemsLayout()
        self.tree.viewport().update()

    def set_clip_monitor(self, on):
        self.settings.clipboard_monitor = on
        self._last_clip = QApplication.clipboard().text().strip()
        self.update_clip_button()
        self.flash(tr("Автоскачивание скопированных ссылок включено") if on
                   else tr("Автоскачивание скопированных ссылок выключено"))
        try:
            self.settings.save()
        except OSError:
            pass

    def update_clip_button(self):
        if not hasattr(self, "btn_clip"):
            return
        on = self.settings.clipboard_monitor
        th = self.theme
        self.btn_clip.setText(tr("Авто: вкл") if on else tr("Авто: выкл"))
        self.btn_clip.setStyleSheet(
            f"QToolButton {{ color:{th['accent'] if on else th['muted']}; font-weight:{'600' if on else '400'}; }}")
        self.btn_clip.setToolTip(
            tr("Скопированные ссылки сразу добавляются и скачиваются. Нажмите, чтобы выключить.") if on
            else tr("Включить: скопированные ссылки будут сразу добавляться и скачиваться."))

    def set_tg_enabled(self, on):
        """Быстрое включение и выключение бота из меню «Настройки»."""
        if on and not self.settings.tg_token.strip():
            self.tg_act.setChecked(False)
            QMessageBox.information(self, "Great Turtle", tr("Сначала впишите токен бота: Параметры → вкладка Telegram."))
            return
        self.settings.tg_enabled = bool(on)
        try:
            self.settings.save()
        except OSError as e:
            self.flash(tr(f"Не удалось сохранить настройки: {e}"), 8000)
        self.apply_telegram()
        self.flash(tr("Telegram-бот включён") if on else tr("Telegram-бот выключен"))

    def set_ask_playlist(self, on):
        self.settings.ask_playlist = on
        self.flash(tr("При загрузке плейлиста откроется окно выбора") if on
                   else tr("Плейлисты будут скачиваться целиком"))

    def set_all_audio(self, on):
        self.settings.all_audio = on
        langs = self.settings.audio_langs.strip() or tr("все языки")
        self.flash(tr(f"К оригиналу будет добавляться дубляж: {langs} (для звука — отдельными файлами)") if on
                   else tr("Будет скачиваться одна звуковая дорожка"))

    def set_language(self, code):
        """Смена языка интерфейса: сохраняем выбор и предлагаем перезапуск (тексты строятся при запуске)."""
        if code == self.settings.lang:
            return
        self.settings.lang = code
        for c, act in getattr(self, "lang_acts", {}).items():
            names = {"ru": "Русский", "en": "English"}
            act.setText(("✓ " if c == code else "    ") + names[c])
        self.save_state(quiet=True)
        if code == LANG:   # вернули тот язык, на котором программа уже работает: перезапуск не нужен
            return
        if QMessageBox.question(self, "Язык / Language",
                                "Язык изменится после перезапуска. Перезапустить сейчас?\n\n"
                                "The language will change after a restart. Restart now?",
                                QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            self.restart_app()

    def set_auto_retry(self, on):
        if on and self.settings.auto_retry <= 0:
            self.settings.auto_retry = 3
        elif not on:
            self.settings.auto_retry = 0
        self.flash(tr(f"Автоповтор при ошибке: {self.settings.auto_retry} раз") if on else tr("Автоповтор выключен"))

    def set_newest_first(self, on):
        self.settings.newest_first = on
        self.rebuild()
        if on:
            self.tree.scrollToTop()
        else:
            self.tree.scrollToBottom()
        self.flash(tr("Новые загрузки сверху") if on else tr("Новые загрузки снизу"))

    def set_dark(self, on):
        self.settings.dark = on
        apply_theme(QApplication.instance(), on)
        self.apply_styles()

    def open_settings(self):
        dlg = SettingsDialog(self, self.settings)
        tg_keys = ("tg_enabled", "tg_token", "tg_admin", "tg_owner", "tg_users", "tg_auto_guest", "proxy")
        before = tuple(getattr(self.settings, k) for k in tg_keys)
        if dlg.exec_() == QDialog.Accepted:
            dlg.collect()
            try:
                self.settings.save()
            except OSError as e:
                QMessageBox.warning(self, tr("Ошибка"), tr(f"Не удалось сохранить настройки: {e}"))
            if before != tuple(getattr(self.settings, k) for k in tg_keys):   # бота перезапускаем, только если менялось его
                self.apply_telegram()
            self.tg_act.blockSignals(True)
            self.tg_act.setChecked(self.settings.tg_enabled)
            self.tg_act.blockSignals(False)
            apply_theme(QApplication.instance(), self.settings.dark)
            self.apply_styles()
            self.dark_act.blockSignals(True)
            self.dark_act.setChecked(self.settings.dark)
            self.dark_act.blockSignals(False)
            self.clip_act.blockSignals(True)
            self.clip_act.setChecked(self.settings.clipboard_monitor)
            self.clip_act.blockSignals(False)
            self.update_clip_button()
            self.audio_act.blockSignals(True)
            self.audio_act.setChecked(self.settings.all_audio)
            self.audio_act.blockSignals(False)
            self.ask_act.blockSignals(True)
            self.ask_act.setChecked(self.settings.ask_playlist)
            self.ask_act.blockSignals(False)
            self.retry_act.blockSignals(True)
            self.retry_act.setChecked(self.settings.auto_retry > 0)
            self.retry_act.blockSignals(False)
            self.tree.row_h = self.settings.thumb_size + 12
            self.tree.doItemsLayout()
            self.restart_autosave()
            self.update_title()

    def restart_autosave(self):
        self.t_save.stop()
        if self.settings.autosave_min > 0:
            self.t_save.start(self.settings.autosave_min * 60 * 1000)

    def show_help(self):
        TextDialog(self, tr("Справка"), help_html(HELP_HTML), html=True, size=(680, 640)).exec_()

    def show_about(self):
        QMessageBox.about(self, tr("О программе"),
                          tr(f"<b>{APP_TITLE}</b> — программа для скачивания видео, музыки и изображений.<br>"
                          f"Версия программы: <b>{APP_VERSION}</b><br>"
                          "Скачивание: <a href='https://github.com/yt-dlp/yt-dlp'>yt-dlp</a> и "
                          "<a href='https://github.com/mikf/gallery-dl'>gallery-dl</a>.<br>"
                          "Интерфейс: PyQt5.<br><br>Используйте скачанные файлы только для личного пользования.")
                          + "<br><br>" + tr("Свободная программа под лицензией GPL v3, без гарантий. Вы сами отвечаете за то, что скачиваете: соблюдайте законы и правила сайтов. Текст лицензии — файл LICENSE в папке программы.")
                          + self.donate_html())

    def donate_html(self):
        """Ссылка «Поддержать разработку» в окне «О программе»: показывается, только если адрес задан в настройках."""
        url = (self.settings.donate_url or "").strip()
        if not re.match(r"^https?://\S+$", url, re.I):
            return ""
        from html import escape
        return f"<br><br><a href='{escape(url, quote=True)}'>{escape(tr('Поддержать разработку'))}</a>"

    def show_log(self):
        TextDialog(self, tr("Журнал"), "\n".join(self.logs) or tr("Журнал пуст.")).exec_()

    def create_shortcut(self):
        """Ярлык на рабочем столе со значком черепашки (Windows)."""
        if sys.platform != "win32":
            QMessageBox.information(self, tr("Ярлык"), tr("Создание ярлыка работает только в Windows."))
            return
        import subprocess
        here = os.path.dirname(os.path.abspath(__file__))
        py = sys.executable
        pyw = os.path.join(os.path.dirname(py), "pythonw.exe")
        target = pyw if os.path.exists(pyw) else py
        ico = os.path.join(here, "turtle.ico")
        q = lambda x: x.replace("'", "''")   # одинарная кавычка в пути для PowerShell
        target, here, ico = q(target), q(here), q(ico)
        script = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut("
                  "[Environment]::GetFolderPath('Desktop')+'\\Great Turtle.lnk');"
                  f"$s.TargetPath='{target}';$s.Arguments='\"{here}\\app.pyw\"';"
                  f"$s.WorkingDirectory='{here}';"
                  + (f"$s.IconLocation='{ico}';" if os.path.exists(ico.replace("''", "'")) else "") + "$s.Save()")
        try:
            r = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True,
                               timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
            ok = r.returncode == 0
            msg = tr("Ярлык «Great Turtle» создан на рабочем столе.") if ok else (r.stderr or r.stdout)[-500:]
        except Exception as e:
            ok, msg = False, str(e)
        (QMessageBox.information if ok else QMessageBox.warning)(self, tr("Ярлык"), msg)

    # ---------- обновление самой программы ----------
    def update_from_zip(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("Архив с новой версией"), "", tr("Архив (*.zip)"))
        if not path:
            return
        try:
            ver, files = updater.read_package(path)
        except Exception as e:
            QMessageBox.warning(self, tr("Обновление"), tr(str(e)))
            return
        self.install_update(ver, files)

    def update_from_url(self):
        url = self.settings.update_url.strip()
        if not url:
            QMessageBox.information(self, tr("Обновление"),
                                    tr("Адрес не задан. Впишите ссылку на zip-архив в Параметры → Прочее → "
                                    "«Адрес обновления программы», или используйте «Обновить программу из архива»."))
            return
        self.flash(tr("Проверяю обновление..."), 15000)

        def work():
            try:
                ver, files = updater.read_package(updater.download(url, self.settings.proxy))
                self.bus.upd.emit(ver, files, "")
            except Exception as e:   # любая ошибка (сеть, битый архив) должна дойти до окна, а не убить поток молча
                self.bus.upd.emit("", None, tr(str(e)))
        threading.Thread(target=work, daemon=True).start()

    def on_update_downloaded(self, ver, files, err):
        if err:
            QMessageBox.warning(self, tr("Обновление"), err)
            return
        self.install_update(ver, files)

    def install_update(self, ver, files):
        if not updater.is_newer(ver, APP_VERSION):
            if QMessageBox.question(self, tr("Обновление"),
                                    tr(f"В архиве версия {ver}, у вас {APP_VERSION}: новее она не является.\n"
                                    "Всё равно установить?"), QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
                self.flash(tr("Обновление отменено"))
                return
        elif QMessageBox.question(self, tr("Обновление"), tr(f"Установить версию {ver} (сейчас {APP_VERSION})?\n"
                                  "Старые файлы сохранятся в папке _backup_…."),
                                  QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        app_dir = os.path.dirname(os.path.abspath(__file__))
        try:
            backup = updater.apply_package(files, app_dir, APP_VERSION)
        except updater.UpdateError as e:
            QMessageBox.warning(self, tr("Обновление"), tr(str(e)))
            return
        self.logs.append(time.strftime("%H:%M:%S  ") + tr(f"[обновление] установлена версия {ver}, копия: {backup}"))
        if QMessageBox.question(self, tr("Обновление"), tr(f"Версия {ver} установлена. Перезапустить программу сейчас?"),
                                QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            self.restart_app()
        else:
            self.flash(tr("Обновление установлено. Перезапустите программу, чтобы оно заработало."), 10000)

    def on_message(self, title, text):
        if title == "restart":  # yt-dlp обновлён — перезапускаемся без лишних вопросов
            self.restart_app()
        else:
            QMessageBox.information(self, title, text)

    def restart_app(self):
        import subprocess

        def spawn():  # запускаем новую копию уже после того, как эта сохранила настройки и закрылась
            try:
                subprocess.Popen([sys.executable, os.path.abspath(sys.argv[0])] + sys.argv[1:],
                                 cwd=os.path.dirname(os.path.abspath(__file__)))
            except OSError:
                pass
        self._spawn_fn = spawn
        QApplication.instance().aboutToQuit.connect(spawn)
        self.close()

    def update_ytdlp(self, restart=False):
        self.flash(tr("Обновляю yt-dlp... (до минуты)"), 60000)
        pre = self.settings.ytdlp_pre

        def work():
            ok, out = eng.update_ytdlp(pre)
            if ok:
                self.settings.last_ytdlp_update = int(time.time())
            if ok and restart:
                self.bus.message.emit("restart", out)
            else:
                self.bus.message.emit(tr("Обновление yt-dlp"), (tr("Готово. Перезапустите программу.\n\n") if ok
                                                           else tr("Не удалось обновить.\n\n")) + out)
        threading.Thread(target=work, daemon=True).start()

    def remind_ytdlp(self):
        if QMessageBox.question(self, tr("yt-dlp давно не обновлялся"),
                                tr("С последнего обновления yt-dlp прошло больше двух недель. Сайты меняются, и старая "
                                "версия начинает давать ошибки.\n\nОбновить сейчас и перезапустить программу?"),
                                QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            self.update_ytdlp(restart=True)
        else:
            self.settings.last_ytdlp_update = int(time.time()) - 7 * 86400  # напомним ещё раз через неделю

    def forget_archive(self):
        n = self.engine.archive.count()
        if not n:
            self.flash(tr("Архив скачанного пуст"))
        elif QMessageBox.question(self, tr("Архив скачанного"), tr(f"В архиве записей: {n}. Забыть их все? Файлы останутся на диске, "
                                  "но программа снова будет качать эти ролики."),
                                  QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            self.engine.archive.clear()
            self.flash(tr("Архив очищен"))

    def pick_format(self, t):
        self.flash(tr("Читаю список форматов..."), 30000)

        def work():
            try:
                title, rows = eng.probe_formats(t, self.settings, lambda s: self.bus.log.emit(s))
                self.bus.fmts.emit(t.id, title, rows)
            except Exception as e:
                self.bus.fmts.emit(t.id, "", friendly_error(e))
        threading.Thread(target=work, daemon=True).start()

    def on_formats(self, tid, title, rows):
        t = self.store.get(tid)
        if t is None:
            return
        if isinstance(rows, str):
            self.flash(tr("Не удалось получить форматы: ") + tr(rows), 8000)
            return
        self.flash("", 1)
        dlg = FormatsDialog(self, title, rows, t.fmt_sel)
        if dlg.exec_() != QDialog.Accepted:
            return
        sel = dlg.chosen()
        if sel is None:
            return
        t.fmt_sel = sel
        t.files = []
        if self.engine.restart(t):
            self.rebuild()
            self.flash(tr("Скачиваю выбранный формат") if sel else tr("Выбор сброшен, качаю по настройкам"))

    # ---------- сохранение и выход ----------
    def save_state(self, quiet=False):
        try:
            self.store.save()
            self.settings.save()
        except OSError as e:
            if quiet:   # автосохранение и выход: окно с ошибкой каждые несколько секунд только мешало бы
                self.logs.append(time.strftime("%H:%M:%S  ") + tr(f"Ошибка сохранения: {e}"))
                self.flash(tr("Не удалось сохранить список и настройки (подробности в журнале)"), 5000)
            else:
                QMessageBox.warning(self, tr("Ошибка сохранения"), str(e))
            return
        if not quiet:
            self.flash(tr("Сохранено"), 2000)

    def quit_nosave(self):
        self._nosave = True
        self.close()

    def closeEvent(self, e):
        if (not self._force and any(t.state == DOWNLOADING for t in self.store.tasks)
                and QMessageBox.question(self, tr("Выход"), tr("Идёт скачивание. Выйти?"),
                                         QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes):
            e.ignore()
            self._nosave = False
            fn = getattr(self, "_spawn_fn", None)
            if fn is not None:   # перезапуск отменён: новую копию на выходе запускать не нужно
                try:
                    QApplication.instance().aboutToQuit.disconnect(fn)
                except (TypeError, RuntimeError):
                    pass
                self._spawn_fn = None
            return
        self.hide_popup()
        if self.tg is not None:
            self.tg.stop()
        self.engine.cancel_all()
        self.settings.geometry = bytes(self.saveGeometry().toHex()).decode()
        if self._nosave:
            try:   # «без сохранения»: пишем только положение окна, остальные настройки сеанса не записываем
                fresh = Settings.load()
                fresh.geometry = self.settings.geometry
                fresh.save()
            except OSError:
                pass
        else:
            self.save_state(quiet=True)
        set_keep_awake(False)
        self.tray.hide()
        e.accept()


_hook_busy, _hook_last = False, 0.0


def excepthook(exc_type, exc, tb):
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(os.path.join(DATA_DIR, "crash.log"), "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S\n") + text + "\n")
    except OSError:
        pass
    global _hook_busy, _hook_last
    if QApplication.instance() is not None and not _hook_busy and time.time() - _hook_last > 10:
        _hook_busy = True     # ошибка из таймера повторялась бы каждые 200 мс: показываем одно окно и не чаще раза в 10 секунд
        try:
            QMessageBox.critical(None, tr("Ошибка"), text[-1500:] + "\n" + tr(f"Подробности записаны в {os.path.join(DATA_DIR, 'crash.log')}"))
        finally:
            _hook_busy, _hook_last = False, time.time()


def main():
    sys.excepthook = excepthook
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    if sys.platform == "win32":  # чтобы в панели задач был значок программы, а не Python
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("GreatTurtle.Downloader")
        except Exception:
            pass
    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    f = app.font()
    f.setBold(False)
    app.setFont(f)
    qt_tr = QTranslator()  # «Да/Нет/Отмена» и др. в стандартных окнах Qt (для английского свои встроены)
    if LANG == "ru" and qt_tr.load("qtbase_ru", QLibraryInfo.location(QLibraryInfo.TranslationsPath)):
        app.installTranslator(qt_tr)
    app.qt_tr = qt_tr
    settings_dark = Settings.load().dark
    apply_theme(app, settings_dark)
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
