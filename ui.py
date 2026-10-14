"""GUI V8.2:
- Бегущая строка RDS на всю ширину экрана (зациклена, бежит для любых названий)
- Строгое вертикальное центрирование названия трека
- Плавный 400ms Fade-Out оверлея (без резкого исчезновения)
- Колёсико мыши больше НЕ меняет громкость (только хоткеи и клик по шкале)
- Колёсико работает исключительно для скролла меню настроек/игр
"""
from __future__ import annotations

import math
import os
import queue
import random
import sys
import time
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import (QEasingCurve, QPropertyAnimation, QPoint, QRect,
                          QSharedMemory, QSize, Qt, QTimer, pyqtProperty)
from PyQt6.QtGui import (QColor, QFont, QFontDatabase, QIcon, QImage, QPainter,
                         QPen, QPixmap, QRadialGradient, QLinearGradient, QFontMetrics)
from PyQt6.QtWidgets import (QApplication, QFileDialog, QHBoxLayout,
                             QMenu, QMessageBox, QPushButton, QSystemTrayIcon,
                             QVBoxLayout, QWidget)

from player import (BASE_DIR, DEFAULT_HOTKEYS, MusicPlayer,
                    autostart_enabled, load_config, save_config, set_autostart,
                    track_title)
from wmi_watcher import ProcessWatcher, running_processes

FONT_FILE = "thin_pixel-7.ttf"
SINGLE_INSTANCE_KEY = "RetroPlayer_SingleInstance_Key"
VOLUME_STEPS = 24
SPRITES: dict[str, QPixmap] = {}
APP_FONT_FAMILY = "Thin Pixel-7"

COLOR_THEMES = {
    "green": {
        "name": "ЗЕЛЁНЫЙ",
        "bg": "#152214", "pix": "#9ec28d", "dim": "#5f7a52", "ghost": "#24301e",
        "hud_bg": QColor(0x15, 0x22, 0x14, 215), "hud_text": "#9ec28d", "hud_edge": "#b2d49e",
    },
    "purple": {
        "name": "ФИОЛЕТОВЫЙ",
        "bg": "#1a1424", "pix": "#b88dec", "dim": "#74529e", "ghost": "#291e3b",
        "hud_bg": QColor(0x1a, 0x14, 0x24, 215), "hud_text": "#b88dec", "hud_edge": "#caa8f5",
    },
    "red": {
        "name": "КРАСНЫЙ",
        "bg": "#241414", "pix": "#ec8d8d", "dim": "#9e5252", "ghost": "#3b1e1e",
        "hud_bg": QColor(0x24, 0x14, 0x14, 215), "hud_text": "#ec8d8d", "hud_edge": "#f5a8a8",
    },
    "cyan": {
        "name": "ГОЛУБОЙ",
        "bg": "#141c24", "pix": "#8dc4ec", "dim": "#527c9e", "ghost": "#1e2d3b",
        "hud_bg": QColor(0x14, 0x1c, 0x24, 215), "hud_text": "#8dc4ec", "hud_edge": "#a8d5f5",
    },
    "orange": {
        "name": "ОРАНЖЕВЫЙ",
        "bg": "#241a14", "pix": "#ecaa8d", "dim": "#9e6b52", "ghost": "#3b271e",
        "hud_bg": QColor(0x24, 0x1a, 0x14, 215), "hud_text": "#ecaa8d", "hud_edge": "#f5c1a8",
    },
    "yellow": {
        "name": "ЖЁЛТЫЙ",
        "bg": "#242214", "pix": "#ece38d", "dim": "#9e9752", "ghost": "#3b381e",
        "hud_bg": QColor(0x24, 0x22, 0x14, 215), "hud_text": "#ece38d", "hud_edge": "#f5edaa",
    },
}


def get_asset_path(filename: str) -> Path:
    if hasattr(sys, "_MEIPASS"):
        p = Path(sys._MEIPASS) / "assets" / filename
        if p.exists():
            return p
    p = BASE_DIR / "assets" / filename
    if p.exists():
        return p
    return Path(__file__).parent / "assets" / filename


def load_sprite(filename: str) -> QPixmap:
    if filename in SPRITES:
        return SPRITES[filename]
    path = get_asset_path(filename)
    if not path.exists():
        SPRITES[filename] = QPixmap()
        return SPRITES[filename]
    
    img = QImage(str(path)).convertToFormat(QImage.Format.Format_ARGB32)
    for y in range(img.height()):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            if c.red() > 240 and c.green() > 240 and c.blue() > 240:
                img.setPixelColor(x, y, QColor(0, 0, 0, 0))
    pix = QPixmap.fromImage(img)
    SPRITES[filename] = pix
    return pix


def load_pixel_font() -> None:
    global APP_FONT_FAMILY
    font_path = get_asset_path(FONT_FILE)
    if font_path.exists():
        fid = QFontDatabase.addApplicationFont(str(font_path))
        if fid != -1:
            families = QFontDatabase.applicationFontFamilies(fid)
            if families:
                APP_FONT_FAMILY = families[0]


def make_font(px: int) -> QFont:
    f = QFont(APP_FONT_FAMILY)
    f.setPixelSize(max(8, int(px)))
    return f


def mmss(seconds: float) -> str:
    seconds = max(0, int(seconds or 0))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def format_hotkey_display(combo: str) -> str:
    return (combo.replace("<ctrl>", "Ctrl+")
                 .replace("<alt>", "Alt+")
                 .replace("<shift>", "Shift+")
                 .replace("<space>", "Space")
                 .replace("<right>", "Right")
                 .replace("<left>", "Left")
                 .replace("<up>", "Up")
                 .replace("<down>", "Down")
                 .replace("+", "+"))


class SpriteButton(QPushButton):
    def __init__(self, normal_png: str, pressed_png: str = "", text: str = "", parent=None):
        super().__init__(text, parent)
        self.normal_png = normal_png
        self.pressed_png = pressed_png or normal_png
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setCheckable(False)
        self.setFont(make_font(18))

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        is_down = self.isDown() or self.isChecked()
        pix = load_sprite(self.pressed_png if is_down else self.normal_png)
        if not pix.isNull():
            p.drawPixmap(self.rect(), pix)
        else:
            p.fillRect(self.rect(), QColor("#333333" if is_down else "#555555"))
        if self.text():
            p.setFont(self.font())
            p.setPen(QColor("#ffffff" if is_down else "#d8d3c5"))
            p.drawText(self.rect().adjusted(0, -1, 0, -1), Qt.AlignmentFlag.AlignCenter, self.text())


class EmbeddedLcdScreen(QWidget):
    MODE_MUSIC = 0
    MODE_SETTINGS = 1
    MODE_EXE = 2

    def __init__(self, main_win: ConsoleWindow, parent=None):
        super().__init__(parent)
        self.main_win = main_win
        self.mode = self.MODE_MUSIC
        self.font_px = 18
        self.title = "НЕТ ТРЕКОВ"
        self.pos_sec = self.dur_sec = 0.0
        self.game_run = False
        self.game_name = "---"

        self._blink = False
        self._seek_rect = QRect()
        self._vol_rect = QRect()
        self._menu_rects: list[tuple[QRect, str]] = []
        self._scroll_y = 0
        self._listening_action: str | None = None
        self._cursor_blink = False

        # Плавный сдвиг пиксель за пикселем (30 FPS)
        self._pixel_offset = 0
        self._tick_count = 0

        self.timer = QTimer(self, interval=33)  # 30 кадров в секунду для плавности!
        self.timer.timeout.connect(self._tick)
        self.timer.start()

    def _tick(self):
        self._tick_count += 1
        # Мигание курсора в настройках
        if self._tick_count % 8 == 0:
            self._blink = not self._blink
            self._cursor_blink = not self._cursor_blink
        
        # Плавное движение текста на 1 пиксель каждый кадр:
        if self.title and self.title != "НЕТ ТРЕКОВ":
            self._pixel_offset += 1
        self.update()

    def _marquee(self, limit: int = 24) -> str:
        if not self.title or self.title == "НЕТ ТРЕКОВ":
            return self.title
        # Карусель на всю ширину экрана с разделителем:
        unit = self.title + "   •   "
        offset = self._offset % len(unit)
        stream = unit * ((limit // len(unit)) + 3)
        return stream[offset:offset + limit]

    def wheelEvent(self, e):
        # Колёсико мыши работает ТОЛЬКО для скролла меню настроек и игр!
        if self.mode != self.MODE_MUSIC:
            delta = e.angleDelta().y()
            if delta > 0:
                self._scroll_y = min(0, self._scroll_y + 24)
            else:
                self._scroll_y = max(-220, self._scroll_y - 24)
            self.update()

    def mousePressEvent(self, e):
        pos = e.pos()
        if self.mode == self.MODE_MUSIC:
            if self.dur_sec > 0 and self._seek_rect.contains(pos):
                frac = max(0.0, min(1.0, (pos.x() - self._seek_rect.left()) / max(1, self._seek_rect.width())))
                self.main_win.player.seek(frac * self.dur_sec)
                self.update()
            elif self._vol_rect.contains(pos):
                frac = max(0.0, min(1.0, (pos.x() - self._vol_rect.left()) / max(1, self._vol_rect.width())))
                step = int(round(frac * VOLUME_STEPS))
                self.main_win._set_volume_step(step)
                self.update()
        else:
            for rect, action in self._menu_rects:
                if rect.contains(pos):
                    if action.startswith("SET_HK_"):
                        self._listening_action = action.replace("SET_HK_", "")
                        self.update()
                        return
                    if self.mode == self.MODE_SETTINGS:
                        self.main_win._on_menu_setting_toggle(action)
                    elif self.mode == self.MODE_EXE:
                        self.main_win._on_menu_exe_action(action)
                    self.update()
                    break

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        w, h = self.width(), self.height()

        theme_key = self.main_win.cfg.get("screen_theme", "green")
        theme = COLOR_THEMES.get(theme_key, COLOR_THEMES["green"])
        lcd_bg = QColor(theme["bg"])
        lcd_pix = QColor(theme["pix"])
        lcd_dim = QColor(theme["dim"])
        lcd_ghost = QColor(theme["ghost"])

        # 1. Фон дисплея
        bx, by, bw, bh = 5, 5, w - 10, h - 10
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(lcd_bg)
        p.drawRect(QRect(bx + 4, by, bw - 8, bh))
        p.drawRect(QRect(bx, by + 4, bw, bh - 8))

        # Сканлайны
        p.setPen(QColor(255, 255, 255, 6))
        for y in range(by + 2, by + bh - 2, 3):
            p.drawLine(bx + 2, y, bx + bw - 2, y)

        # 2. Отрисовка контента
        p.setFont(make_font(self.font_px))
        p.setPen(lcd_pix)

        if self.mode == self.MODE_MUSIC:
            self._draw_music_mode(p, bx + 6, by + 6, bw - 12, bh - 12, lcd_pix, lcd_dim, lcd_ghost)
        elif self.mode == self.MODE_SETTINGS:
            self._draw_settings_mode(p, bx + 6, by + 6, bw - 12, bh - 12, lcd_pix, lcd_dim)
        elif self.mode == self.MODE_EXE:
            self._draw_exe_mode(p, bx + 6, by + 6, bw - 12, bh - 12, lcd_pix, lcd_dim, lcd_ghost)

        # 3. ЭЛТ-стекло
        crt = QRadialGradient(bx + bw / 2, by + bh / 2, max(bw, bh) * 0.7)
        crt.setColorAt(0.0, QColor(255, 255, 255, 18))
        crt.setColorAt(0.6, QColor(255, 255, 255, 0))
        crt.setColorAt(0.95, QColor(0, 0, 0, 70))
        crt.setColorAt(1.0, QColor(0, 0, 0, 130))
        p.setBrush(crt)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRect(QRect(bx + 4, by + 4, bw - 8, bh - 8))

        # 4. Прозрачный безель поверх
        bezel_pix = load_sprite("bezel.png")
        if not bezel_pix.isNull():
            p.drawPixmap(0, 0, w, h, bezel_pix)

    def _draw_music_mode(self, p: QPainter, x: int, y: int, w: int, h: int, pix_col, dim_col, ghost_col):
        line_h = int(self.font_px * 1.25)

        # Нижние элементы (строго привязаны снизу вверх)
        status_y = y + h - line_h - 4
        vol_y = status_y - line_h - 6
        bar_y = vol_y - 14
        time_y = bar_y - line_h - 6

        # Название трека: увеличенный шрифт x1.3
        title_f = int(self.font_px * 1.3)
        p.setFont(make_font(title_f))
        fm = p.fontMetrics()
        title_h = fm.height()

        # СТРОГИЙ ГЕОМЕТРИЧЕСКИЙ ЦЕНТР между верхом стекла (y) и строкой времени (time_y):
        zone_h = time_y - y
        track_y = y + max(1, (zone_h - title_h) // 2)

        # 1. Плавная бегущая строка на ВСЮ ширину экрана (-4 px с каждого края):
        track_rect = QRect(x + 4, track_y, w - 8, title_h)

        if not self.title or self.title == "НЕТ ТРЕКОВ":
            p.setPen(pix_col)
            p.drawText(track_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.title)
        else:
            # Бесконечная плавная лента с разделителем:
            unit = self.title + "   •   "
            unit_w = max(10, fm.horizontalAdvance(unit))
            shift = self._pixel_offset % unit_w

            p.save()
            p.setClipRect(track_rect)  # Обрезаем строго по рамке экрана (-4 px по бокам)
            p.setPen(pix_col)

            # Рисуем ленту, заполняя всю ширину стекла:
            draw_x = track_rect.left() - shift
            while draw_x < track_rect.right():
                p.drawText(QRect(draw_x, track_y, unit_w, title_h),
                           Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, unit)
                draw_x += unit_w
            p.restore()

        # 2. Время (по центру)
        p.setFont(make_font(self.font_px))
        p.setPen(pix_col)
        stamp = f"{mmss(self.pos_sec)} / {mmss(self.dur_sec)}" if self.dur_sec else "--:-- / --:--"
        p.drawText(QRect(x, time_y, w, line_h), Qt.AlignmentFlag.AlignCenter, stamp)

        # 3. Перемотка
        self._seek_rect = QRect(x + 4, bar_y, w - 8, 6)
        p.fillRect(self._seek_rect, ghost_col)
        frac = (self.pos_sec / self.dur_sec) if self.dur_sec else 0.0
        p.fillRect(x + 4, bar_y, int((w - 8) * frac), 6, dim_col)
        thumb_x = x + 4 + int((w - 14) * frac)
        p.fillRect(thumb_x, bar_y - 3, 6, 12, pix_col)

        # 4. Шкала громкости
        vol_step = self.main_win.cfg.get("volume_step", 16)
        p.setFont(make_font(self.font_px))
        p.setPen(dim_col)
        p.drawText(QRect(x + 4, vol_y, 55, line_h), Qt.AlignmentFlag.AlignLeft, "ГРОМК:")
        
        self._vol_rect = QRect(x + 62, vol_y + 4, w - 66, 8)
        bar_w = (w - 66) // VOLUME_STEPS
        for s in range(VOLUME_STEPS):
            bx = x + 62 + s * bar_w
            p.fillRect(QRect(bx, vol_y + 4, bar_w - 1, 8), pix_col if s < vol_step else ghost_col)

        # 5. Статус игры
        p.setFont(make_font(self.font_px))
        p.setPen(dim_col)
        p.drawText(QRect(x + 4, status_y, w - 8, line_h), Qt.AlignmentFlag.AlignLeft, f"ИГРА: {self.game_name[:8]}")
        badge = "В ИГРЕ" if self.game_run else "ОЖИДАНИЕ"
        p.setPen(pix_col if self.game_run else dim_col)
        p.drawText(QRect(x + 4, status_y, w - 8, line_h), Qt.AlignmentFlag.AlignRight, badge)

    def _draw_settings_mode(self, p: QPainter, x: int, y: int, w: int, h: int, pix_col, dim_col):
        self._menu_rects = []
        line_h = int(self.font_px * 1.25)
        p.setFont(make_font(self.font_px))
        p.setPen(pix_col)
        p.drawText(QRect(x, y + 2, w, line_h), Qt.AlignmentFlag.AlignCenter, "-- НАСТРОЙКИ --")

        cfg = self.main_win.cfg
        corners_map = {
            "top-right": "СПРАВА ВВЕРХ", "top-left": "СЛЕВА ВВЕРХ",
            "bottom-right": "СПРАВА ВНИЗ", "bottom-left": "СЛЕВА ВНИЗ"
        }
        hud_pos = corners_map.get(cfg.get("overlay_corner", "top-right"), "СПРАВА ВВЕРХ")
        hk = cfg.get("hotkeys", {})
        curr_theme = COLOR_THEMES.get(cfg.get("screen_theme", "green"), COLOR_THEMES["green"])["name"]

        items = [
            ("THEME",    f"ЦВЕТ ЭКРАНА:   [{curr_theme}]"),
            ("AUTOPLAY", f"АВТОЗАПУСК:   {'[ВКЛ]' if cfg.get('autoplay_on_game', True) else '[ВЫКЛ]'}"),
            ("STARTUP",  f"СТАРТ ПРИ ЗАПУСКЕ: {'[ВКЛ]' if cfg.get('run_on_startup', False) else '[ВЫКЛ]'}"),
            ("CORNER",   f"HUD ОВЕРЛЕЙ:  [{hud_pos}]"),
            ("FADE",     f"ФЕЙД ЗВУКА:   [{int(cfg.get('fade_seconds', 5))} СЕК]"),
            ("HOTKEYS",  f"ХОТКЕИ:       {'[ВКЛ]' if cfg.get('hotkeys_enabled', True) else '[ВЫКЛ]'}"),
            ("SET_HK_next",    "СЛЕД. ТРЕК", hk.get("next", "<ctrl>+<alt>+<right>")),
            ("SET_HK_prev",    "ПРЕД. ТРЕК", hk.get("prev", "<ctrl>+<alt>+<left>")),
            ("SET_HK_toggle",  "ПУСК/ПАУЗА", hk.get("toggle", "<ctrl>+<alt>+<space>")),
            ("SET_HK_volup",   "ГРОМЧЕ",     hk.get("volup", "<ctrl>+<shift>+<up>")),
            ("SET_HK_voldown", "ТИШЕ",       hk.get("voldown", "<ctrl>+<shift>+<down>")),
        ]

        p.setClipRect(QRect(x, y + line_h + 4, w, h - line_h * 2 - 8))
        cur_y = y + line_h + 6 + self._scroll_y

        for item in items:
            action = item[0]
            rect = QRect(x + 2, cur_y, w - 4, line_h)
            self._menu_rects.append((rect, action))

            if action.startswith("SET_HK_"):
                act_name = action.replace("SET_HK_", "")
                lbl, val = item[1], item[2]
                if self._listening_action == act_name:
                    cursor = "_" if self._cursor_blink else " "
                    txt = f"{lbl}: [ {cursor} ]"
                    p.setPen(QColor("#ffe873"))
                else:
                    txt = f"{lbl}: [{format_hotkey_display(val)}]"
                    p.setPen(pix_col)
            else:
                txt = item[1]
                p.setPen(pix_col)

            p.drawText(rect, Qt.AlignmentFlag.AlignLeft, txt)
            cur_y += line_h + 2

        p.setClipping(False)
        p.setFont(make_font(max(10, int(self.font_px * 0.7))))
        p.setPen(dim_col)
        hint = "НАЖМИТЕ КЛАВИШИ НА КЛАВИАТУРЕ..." if self._listening_action else "[SET - ВЫХОД | СКРОЛЛ]"
        p.drawText(QRect(x, y + h - line_h, w, line_h), Qt.AlignmentFlag.AlignCenter, hint)

    def _draw_exe_mode(self, p: QPainter, x: int, y: int, w: int, h: int, pix_col, dim_col, ghost_col):
        self._menu_rects = []
        line_h = int(self.font_px * 1.25)
        p.setFont(make_font(self.font_px))
        p.setPen(pix_col)
        p.drawText(QRect(x, y + 2, w, line_h), Qt.AlignmentFlag.AlignCenter, "-- ВЫБОР ИГРЫ --")

        profiles = self.main_win.cfg.get("profiles", [])

        p.setClipRect(QRect(x, y + line_h + 4, w, h - line_h * 2 - 8))
        cur_y = y + line_h + 6 + self._scroll_y

        add_proc_rect = QRect(x + 2, cur_y, w - 4, line_h)
        self._menu_rects.append((add_proc_rect, "PICK_PROCESS"))
        p.setPen(pix_col)
        p.drawText(add_proc_rect, Qt.AlignmentFlag.AlignLeft, "> [ИЗ ЗАПУЩЕННЫХ]")
        cur_y += line_h + 2

        add_file_rect = QRect(x + 2, cur_y, w - 4, line_h)
        self._menu_rects.append((add_file_rect, "PICK_FILE"))
        p.setPen(pix_col)
        p.drawText(add_file_rect, Qt.AlignmentFlag.AlignLeft, "> [ИЗ ФАЙЛОВ .EXE]")
        cur_y += line_h + 6

        for i, prof in enumerate(profiles):
            name = prof.get("name", "")
            on = prof.get("enabled", True)
            
            del_rect = QRect(x + 2, cur_y, 22, line_h)
            self._menu_rects.append((del_rect, f"DEL_{i}"))
            p.setPen(QColor("#d06048"))
            p.drawText(del_rect, Qt.AlignmentFlag.AlignLeft, "[X]")

            toggle_rect = QRect(x + 26, cur_y, w - 30, line_h)
            self._menu_rects.append((toggle_rect, f"TOGGLE_{i}"))
            p.setPen(pix_col if on else ghost_col)
            status_tag = "[*]" if on else "[ ]"
            p.drawText(toggle_rect, Qt.AlignmentFlag.AlignLeft, f"{status_tag} {name[:12]}")
            
            cur_y += line_h + 2

        p.setClipping(False)
        p.setFont(make_font(max(10, int(self.font_px * 0.7))))
        p.setPen(dim_col)
        p.drawText(QRect(x, y + h - line_h, w, line_h), Qt.AlignmentFlag.AlignCenter, "[EXE - ВЫХОД | СКРОЛЛ]")


class ConsoleWindow(QWidget):
    WIN_W = 360
    WIN_H = 211

    def __init__(self, cfg, start_hidden=False):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.cfg = cfg
        self.setWindowTitle("RetroPlayer")
        self.setFixedSize(self.WIN_W, self.WIN_H)

        self.player = MusicPlayer(cfg)
        self.events: queue.Queue = queue.Queue()
        self.hotkey_queue: queue.Queue = queue.Queue()
        self.overlay = OverlayHud(cfg.get("overlay_corner", "top-right"),
                                  float(cfg.get("overlay_seconds", 4.0)),
                                  cfg.get("screen_theme", "green"))
        self.watcher = ProcessWatcher(lambda action, name: self.events.put((action, name)))
        self._game_running = False
        self._listener = None
        self._quitting = False
        self._drag_pos: QPoint | None = None

        self._build_ui()
        self._build_tray()
        self._start_hotkeys()
        self._restore_last_state()
        self._set_volume_step(self.cfg.get("volume_step", 8))
        self._watch_profiles()

        self._poll = QTimer(self, interval=150)
        self._poll.timeout.connect(self._drain)
        self._poll.start()

        if not start_hidden:
            self.show()

    def _build_ui(self):
        k = 1.2
        
        # 1. LCD-Экран слева
        self.lcd = EmbeddedLcdScreen(main_win=self, parent=self)
        self.lcd.setGeometry(int(16 * k), int(16 * k), int(188 * k), int(144 * k))
        self.lcd.font_px = int(18 * k)

        # 2. Серые кнопки 33x21
        bw, bh = int(33 * k), int(21 * k)
        col1_x, col2_x = int(210 * k), int(249 * k)
        r1_y, r2_y, r3_y = int(37 * k), int(63 * k), int(89 * k)

        self.btn_upd = SpriteButton("btn_gray.png", "btn_gray_press.png", "UPD", self)
        self.btn_upd.clicked.connect(self._rescan)
        self.btn_hud = SpriteButton("btn_gray.png", "btn_gray_press.png", "HUD", self)
        self.btn_hud.clicked.connect(lambda: self._announce(self.player.current or None))

        self.btn_set = SpriteButton("btn_gray.png", "btn_gray_press.png", "SET", self)
        self.btn_set.setCheckable(True)
        self.btn_set.clicked.connect(self._toggle_settings_mode)
        self.btn_exe = SpriteButton("btn_gray.png", "btn_gray_press.png", "EXE", self)
        self.btn_exe.setCheckable(True)
        self.btn_exe.clicked.connect(self._toggle_exe_mode)

        self.btn_dir = SpriteButton("btn_gray.png", "btn_gray_press.png", "DIR", self)
        self.btn_dir.clicked.connect(self._choose_folder)
        self.btn_mute = SpriteButton("btn_gray.png", "btn_gray_press.png", "RND", self)
        self.btn_mute.setToolTip("Случайный трек")
        self.btn_mute.clicked.connect(self._shuffle_track)

        for btn in (self.btn_upd, self.btn_hud, self.btn_set, self.btn_exe, self.btn_dir, self.btn_mute):
            btn.setFont(make_font(int(18 * k)))

        self.btn_upd.setGeometry(col1_x, r1_y, bw, bh)
        self.btn_hud.setGeometry(col2_x, r1_y, bw, bh)
        self.btn_set.setGeometry(col1_x, r2_y, bw, bh)
        self.btn_exe.setGeometry(col2_x, r2_y, bw, bh)
        self.btn_dir.setGeometry(col1_x, r3_y, bw, bh)
        self.btn_mute.setGeometry(col2_x, r3_y, bw, bh)

        # 3. Кнопки трея
        cap_sz = int(14 * k)
        close_x = col2_x + bw - cap_sz
        min_x = close_x - cap_sz - int(4 * k)

        self.btn_min = SpriteButton("btn_min.png", text="", parent=self)
        self.btn_min.setGeometry(min_x, int(16 * k), cap_sz, cap_sz)
        self.btn_min.clicked.connect(self._to_tray)

        self.btn_close = SpriteButton("btn_close.png", text="", parent=self)
        self.btn_close.setGeometry(close_x, int(16 * k), cap_sz, cap_sz)
        self.btn_close.clicked.connect(self._quit)

        # 4. Нижние кнопки треков 21x21
        tbw, tbh = int(21 * k), int(21 * k)
        r4_y = int(117 * k)
        self.btn_prev = SpriteButton("btn_prev.png", "btn_prev_press.png", "", self)
        self.btn_prev.clicked.connect(lambda: self._act("prev"))
        self.btn_play = SpriteButton("btn_play.png", "btn_play_press.png", "", self)
        self.btn_play.clicked.connect(lambda: self._act("toggle"))
        self.btn_next = SpriteButton("btn_next.png", "btn_next_press.png", "", self)
        self.btn_next.clicked.connect(lambda: self._act("next"))

        self.btn_prev.setGeometry(int(210 * k), r4_y, tbw, tbh)
        self.btn_play.setGeometry(int(235 * k), r4_y, tbw, tbh)
        self.btn_next.setGeometry(int(261 * k), r4_y, tbw, tbh)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        shell = load_sprite("player.png")
        if not shell.isNull():
            p.drawPixmap(self.rect(), shell)
        else:
            p.fillRect(self.rect(), QColor("#1a1a1a"))

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            child = self.childAt(e.pos())
            if not isinstance(child, QPushButton) and child != self.lcd:
                self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            else:
                self._drag_pos = None

    def mouseMoveEvent(self, e):
        if self._drag_pos:
            self.move(e.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, e):
        self._drag_pos = None

    # Колёсико мыши больше НЕ меняет громкость в окне!
    def wheelEvent(self, e):
        pass

    def keyPressEvent(self, e):
        if self.lcd._listening_action:
            action = self.lcd._listening_action
            mods = []
            if e.modifiers() & Qt.KeyboardModifier.ControlModifier: mods.append("<ctrl>")
            if e.modifiers() & Qt.KeyboardModifier.AltModifier: mods.append("<alt>")
            if e.modifiers() & Qt.KeyboardModifier.ShiftModifier: mods.append("<shift>")

            key = e.key()
            key_name = ""
            if key == Qt.Key.Key_Right: key_name = "<right>"
            elif key == Qt.Key.Key_Left: key_name = "<left>"
            elif key == Qt.Key.Key_Up: key_name = "<up>"
            elif key == Qt.Key.Key_Down: key_name = "<down>"
            elif key == Qt.Key.Key_Space: key_name = "<space>"
            elif Qt.Key.Key_A <= key <= Qt.Key.Key_Z:
                key_name = chr(key).lower()

            if key_name and mods:
                new_combo = "+".join(mods + [key_name])
                if "hotkeys" not in self.cfg:
                    self.cfg["hotkeys"] = {}
                self.cfg["hotkeys"][action] = new_combo
                save_config(self.cfg)
                self._start_hotkeys()
                self.lcd._listening_action = None
                self.lcd.update()
                return

        super().keyPressEvent(e)

    def _set_volume_step(self, step: int):
        step = max(0, min(VOLUME_STEPS, step))
        self.cfg["volume_step"] = step
        frac = step / float(VOLUME_STEPS)
        real_vol = int(round((frac ** 2.2) * 100))
        self.player.set_volume(real_vol)
        save_config(self.cfg)
        self.lcd.update()

    def _change_volume_step(self, delta: int):
        cur = self.cfg.get("volume_step", 16)
        self._set_volume_step(cur + delta)

    def _toggle_mute(self):
        cur = self.cfg.get("volume_step", 16)
        if cur > 0:
            self._mute_saved = cur
            self._set_volume_step(0)
        else:
            self._set_volume_step(getattr(self, "_mute_saved", 16))

    def _toggle_settings_mode(self):
        if self.btn_set.isChecked():
            self.btn_exe.setChecked(False)
            self.lcd.mode = EmbeddedLcdScreen.MODE_SETTINGS
        else:
            self.lcd.mode = EmbeddedLcdScreen.MODE_MUSIC
        self.lcd._listening_action = None
        self.lcd._scroll_y = 0
        self.lcd.update()

    def _toggle_exe_mode(self):
        if self.btn_exe.isChecked():
            self.btn_set.setChecked(False)
            self.lcd.mode = EmbeddedLcdScreen.MODE_EXE
        else:
            self.lcd.mode = EmbeddedLcdScreen.MODE_MUSIC
        self.lcd._listening_action = None
        self.lcd._scroll_y = 0
        self.lcd.update()

    def _on_menu_setting_toggle(self, action: str):
        if action == "THEME":
            keys = list(COLOR_THEMES.keys())
            curr = self.cfg.get("screen_theme", "green")
            idx = (keys.index(curr) + 1) % len(keys) if curr in keys else 0
            self.cfg["screen_theme"] = keys[idx]
            self.overlay.set_theme(keys[idx])
        elif action == "AUTOPLAY":
            self.cfg["autoplay_on_game"] = not self.cfg.get("autoplay_on_game", True)
        elif action == "STARTUP":
            on = not self.cfg.get("run_on_startup", False)
            self.cfg["run_on_startup"] = on
            set_autostart(on)
        elif action == "CORNER":
            corners = ["top-right", "top-left", "bottom-right", "bottom-left"]
            curr = self.cfg.get("overlay_corner", "top-right")
            idx = (corners.index(curr) + 1) % len(corners) if curr in corners else 0
            self.cfg["overlay_corner"] = corners[idx]
            self.overlay.corner = corners[idx]
        elif action == "FADE":
            f = int(self.cfg.get("fade_seconds", 5))
            f = 2 if f >= 8 else f + 2
            self.cfg["fade_seconds"] = f
        elif action == "HOTKEYS":
            self.cfg["hotkeys_enabled"] = not self.cfg.get("hotkeys_enabled", True)
            self._start_hotkeys()
        save_config(self.cfg)
        self.lcd.update()

    def _on_menu_exe_action(self, action: str):
        if action == "PICK_PROCESS":
            procs = sorted(list(set(running_processes())), key=str.lower)
            from PyQt6.QtWidgets import QInputDialog
            item, ok = QInputDialog.getItem(self, "Выбор игры", "Запущенные процессы:", procs, 0, False)
            if ok and item:
                profs = self.cfg.get("profiles", [])
                if not any(p.get("name", "").lower() == item.lower() for p in profs):
                    profs.append({"name": item, "enabled": True})
                    self.cfg["profiles"] = profs
                    save_config(self.cfg)
                    self._watch_profiles()
                    self._refresh_lcd()
        elif action == "PICK_FILE":
            file_path, _ = QFileDialog.getOpenFileName(self, "Выберите exe игры", "", "Исполняемые файлы (*.exe)")
            if file_path:
                exe_name = Path(file_path).name
                profs = self.cfg.get("profiles", [])
                if not any(p.get("name", "").lower() == exe_name.lower() for p in profs):
                    profs.append({"name": exe_name, "enabled": True})
                    self.cfg["profiles"] = profs
                    save_config(self.cfg)
                    self._watch_profiles()
                    self._refresh_lcd()
        elif action.startswith("DEL_"):
            idx = int(action.replace("DEL_", ""))
            profs = self.cfg.get("profiles", [])
            if 0 <= idx < len(profs):
                profs.pop(idx)
                self.cfg["profiles"] = profs
                save_config(self.cfg)
                self._watch_profiles()
                self._refresh_lcd()
        elif action.startswith("TOGGLE_"):
            idx = int(action.replace("TOGGLE_", ""))
            profs = self.cfg.get("profiles", [])
            if 0 <= idx < len(profs):
                profs[idx]["enabled"] = not profs[idx].get("enabled", True)
                save_config(self.cfg)
                self._watch_profiles()
                self._refresh_lcd()

    def _restore_last_state(self):
        last_track = self.cfg.get("last_track")
        last_pos = float(self.cfg.get("last_pos", 0.0))
        tracks = getattr(self.player, "tracks", getattr(self.player, "_tracks", []))
        if last_track and tracks:
            for idx, path in enumerate(tracks):
                if getattr(path, "name", str(path)) == last_track:
                    self.player.index = idx
                    try:
                        self.player.seek(last_pos)
                        self.player.pause()
                    except Exception:
                        pass
                    break
        self._refresh_lcd()

    def _to_tray(self):
        self.hide()

    def _quit(self):
        self._quitting = True
        try:
            if self.player.current:
                self.cfg["last_track"] = self.player.current.name
                self.cfg["last_pos"] = self.player.position
                save_config(self.cfg)
            if self._listener:
                self._listener.stop()
            self.watcher.stop()
            self.player.stop()
            if hasattr(self, "_tray") and self._tray:
                self._tray.hide()
        except Exception:
            pass
        QApplication.quit()
        os._exit(0)

    def _build_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        pix = QPixmap(32, 32)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.fillRect(QRect(4, 4, 24, 24), QColor("#1a1a1a"))
        p.fillRect(QRect(8, 8, 16, 16), QColor("#152214"))
        p.end()
        self._tray = QSystemTrayIcon(QIcon(pix), self)
        menu = QMenu()
        menu.addAction("Показать / Скрыть", lambda: self.setVisible(not self.isVisible()))
        menu.addAction("Пуск / Пауза", lambda: self._act("toggle"))
        menu.addSeparator()
        menu.addAction("Выход", self._quit)
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(lambda r: self.setVisible(not self.isVisible())
                                     if r == QSystemTrayIcon.ActivationReason.DoubleClick else None)
        self._tray.show()

    def _start_hotkeys(self):
        if self._listener:
            self._listener.stop()
        if not self.cfg.get("hotkeys_enabled", True):
            return
        try:
            from pynput import keyboard
            hk = self.cfg.get("hotkeys", {})
            mapping = {
                hk.get("next", "<ctrl>+<alt>+<right>"): lambda: self.hotkey_queue.put("next"),
                hk.get("prev", "<ctrl>+<alt>+<left>"): lambda: self.hotkey_queue.put("prev"),
                hk.get("volup", "<ctrl>+<shift>+<up>"): lambda: self.hotkey_queue.put("volup"),
                hk.get("voldown", "<ctrl>+<shift>+<down>"): lambda: self.hotkey_queue.put("voldown"),
                hk.get("toggle", "<ctrl>+<alt>+<space>"): lambda: self.hotkey_queue.put("toggle"),
            }
            self._listener = keyboard.GlobalHotKeys(mapping)
            self._listener.daemon = True
            self._listener.start()
        except Exception:
            self._listener = None

    def _watch_profiles(self):
        names = [p["name"] for p in self.cfg.get("profiles", []) if p.get("enabled", True)]
        if names and self.cfg.get("autoplay_on_game", True):
            self.watcher.watch(names)
        else:
            self.watcher.stop()

    def _drain(self):
        for q, handler in ((self.events, self._on_event), (self.hotkey_queue, self._act)):
            while True:
                try:
                    handler(q.get_nowait())
                except queue.Empty:
                    break
        if self.player.finished():
            self._announce(self.player.next_track())
        self._refresh_lcd()

    def _on_event(self, event):
        action, name = event[0], event[1]
        if action == "start":
            self._game_running = True
            if self.cfg.get("autoplay_on_game", True):
                path = self.player.play_with_fade(float(self.cfg.get("fade_seconds", 5.0)))
                self._announce(path)
        elif action == "stop":
            self._game_running = False
            if self.player.state == MusicPlayer.PLAY:
                self.player.pause()
                # Сохраняем точную секунду, на которой закрылась игра:
                if self.player.current:
                    self.cfg["last_track"] = self.player.current.name
                    self.cfg["last_pos"] = self.player.position
                    save_config(self.cfg)

    def _shuffle_track(self):
        tracks = getattr(self.player, "tracks", getattr(self.player, "_tracks", []))
        if len(tracks) > 1:
            next_idx = random.choice([i for i in range(len(tracks)) if i != self.player.index])
            self.player.index = next_idx
            try:
                self.player.play_index(next_idx)
            except Exception:
                pass
            self._announce(self.player.current)
            self._refresh_lcd()

    def _act(self, action):
        if action == "next":
            self._announce(self.player.next_track())
        elif action == "prev":
            self._announce(self.player.prev_track())
        elif action == "volup":
            self._change_volume_step(+1)
        elif action == "voldown":
            self._change_volume_step(-1)
        elif action == "toggle":
            before = self.player.state
            if self.player.toggle() == MusicPlayer.PLAY and before != MusicPlayer.PLAY:
                self._announce(self.player.current)
        self._refresh_lcd()

    def _announce(self, path):
        if path:
            # Сохраняем трек в файл сразу же при его включении!
            self.cfg["last_track"] = Path(path).name
            self.cfg["last_pos"] = 0.0
            save_config(self.cfg)
            self.overlay.show_banner(track_title(path))

    def _refresh_lcd(self):
        new_title = track_title(self.player.current) if self.player.current else "НЕТ ТРЕКОВ"
        if new_title != self.lcd.title:
            self.lcd.title = new_title
            self.lcd._pixel_offset = 0  # Сбрасываем скролл на начало нового трека!

        self.lcd.pos_sec, self.lcd.dur_sec = self.player.position, self.player.duration
        self.lcd.game_run = self._game_running
        names = [p["name"] for p in self.cfg.get("profiles", []) if p.get("enabled")]
        self.lcd.game_name = names[0].upper() if names else "---"

        if self.player.state == MusicPlayer.PLAY:
            self.btn_play.normal_png = "btn_pause.png"
            self.btn_play.pressed_png = "btn_pause_press.png"
        else:
            self.btn_play.normal_png = "btn_play.png"
            self.btn_play.pressed_png = "btn_play_press.png"
        self.btn_play.update()
        self.lcd.update()

    def _rescan(self):
        self.player.rescan(self.cfg)
        self._refresh_lcd()

    def _choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Папка с музыкой", str(BASE_DIR))
        if folder:
            self.cfg["music_dir"] = folder
            save_config(self.cfg)
            self._rescan()


class OverlayHud(QWidget):
    def __init__(self, corner="top-right", seconds=4.0, theme_key="green"):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.corner, self.seconds = corner, seconds
        self.theme_key = theme_key
        self._text, self._opacity, self._slide = "", 0.0, 0.0
        self._hide_timer = QTimer(self, singleShot=True)
        # Плавное закрытие по таймеру:
        self._hide_timer.timeout.connect(self.hide_banner)
        self._make_clickthrough()

    def set_theme(self, theme_key: str):
        self.theme_key = theme_key
        self.update()

    def _make_clickthrough(self):
        if sys.platform != "win32":
            return
        import ctypes
        GWL_EXSTYLE, WS_EX_TRANSPARENT = -20, 0x20
        WS_EX_LAYERED, WS_EX_NOACTIVATE = 0x80000, 0x08000000
        hwnd = int(self.winId())
        ex = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex | WS_EX_TRANSPARENT | WS_EX_LAYERED | WS_EX_NOACTIVATE)

    opacity = pyqtProperty(float, lambda self: self._opacity, lambda self, v: (setattr(self, "_opacity", v), self.update()))
    slide = pyqtProperty(float, lambda self: self._slide, lambda self, v: (setattr(self, "_slide", v), self._place()))

    def show_banner(self, text: str):
        self._text = text.upper()
        fm = QFontMetrics(make_font(36))
        text_w = fm.horizontalAdvance(f">> {self._text}")
        self.setFixedSize(text_w + 34, 43)
        self._hide_timer.stop()
        self._slide, self._opacity = 0.0, 0.0
        self._place()
        self._animate(show=True)
        self.show()
        self._hide_timer.start(int(self.seconds * 1000))

    def hide_banner(self):
        # Плавный Fade-Out при завершении показа
        self._hide_timer.stop()
        self._animate(show=False)

    def _target_pos(self) -> QPoint:
        geo = QApplication.primaryScreen().availableGeometry()
        m = 24
        x = geo.left() + m if "left" in self.corner else geo.right() - self.width() - m
        y = geo.top() + m if "top" in self.corner else geo.bottom() - self.height() - m
        return QPoint(int(x), int(y))

    def _place(self):
        target = self._target_pos()
        dx = -(self.width() + 40) if "left" in self.corner else (self.width() + 40)
        self.move(int(target.x() + dx * (1.0 - self._slide)), int(target.y()))

    def _animate(self, show: bool):
        slide = QPropertyAnimation(self, b"slide", self)
        fade = QPropertyAnimation(self, b"opacity", self)
        for anim in (slide, fade):
            anim.setDuration(400)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic if show else QEasingCurve.Type.InCubic)
            anim.setStartValue(0.0 if show else 1.0)
            anim.setEndValue(1.0 if show else 0.0)
            anim.start()
        self._anims = (slide, fade)
        if not show:
            fade.finished.connect(self.hide)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setOpacity(self._opacity)
        theme = COLOR_THEMES.get(self.theme_key, COLOR_THEMES["green"])

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme["hud_bg"])
        p.drawRoundedRect(self.rect().adjusted(2, 2, -2, -2), 6, 6)
        p.setPen(QPen(QColor(theme["hud_edge"]), 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(self.rect().adjusted(2, 2, -2, -2), 6, 6)
        p.setFont(make_font(36))
        p.setPen(QColor(theme["hud_text"]))
        p.drawText(QRect(20, 0, self.width() - 30, self.height()),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, f">> {self._text}")


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("RetroPlayer")
    app.setQuitOnLastWindowClosed(False)

    guard = QSharedMemory(SINGLE_INSTANCE_KEY)
    if not guard.create(1):
        if sys.platform == "win32":
            import ctypes
            hwnd = ctypes.windll.user32.FindWindowW(None, "RetroPlayer")
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 9)
                ctypes.windll.user32.SetForegroundWindow(hwnd)
        return 0

    load_pixel_font()
    cfg = load_config()
    try:
        ConsoleWindow(cfg, start_hidden="--tray" in sys.argv)
    except Exception as exc:
        QMessageBox.critical(None, "ОШИБКА", f"Не удалось запустить плеер:\n{exc}")
        return 1
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())