"""Аудиоядро плеера и работа с конфигурацией.

Здесь нет ничего визуального: только pygame.mixer, плейлист из папки музыки,
громкость в 14 шагов, линейный фейд-ин, перемотка, чтение/запись config.json
и автозапуск Windows через реестр.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

if getattr(sys, "frozen", False):                     # упаковка PyInstaller
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent
MUSIC_DIR = BASE_DIR / "music"
CONFIG_PATH = BASE_DIR / "config.json"
ASSETS_DIR = BASE_DIR / "assets"

VOLUME_STEPS = 14          # шкала громкости: 0..14 «делений» LCD-индикатора
EXTENSIONS = (".mp3", ".ogg", ".wav", ".flac")
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "RetroPlayer"

DEFAULT_HOTKEYS = {
    "next": "<ctrl>+<alt>+<right>",
    "prev": "<ctrl>+<alt>+<left>",
    "volup": "<ctrl>+<alt>+<up>",
    "voldown": "<ctrl>+<alt>+<down>",
    "toggle": "<ctrl>+<alt>+<space>",
}

DEFAULT_CONFIG = {
    "volume": 8,                      # сохранённая громкость (0..14)
    "profiles": [],                   # список {"name": exe, "enabled": bool}
    "autoplay_on_game": True,         # автозапуск музыки при старте игры
    "fade_seconds": 5.0,              # длительность фейда при автозапуске
    "overlay_corner": "top-right",    # угол экрана для HUD
    "overlay_seconds": 4.0,           # сколько секунд висит баннер
    "hotkeys_enabled": True,          # глобальные хоткеи
    "hotkeys": {},                    # действие -> комбинация pynput
    "music_dir": "",                  # своя папка музыки (пусто = ./music)
}

# Таблицы битрейтов/частот MPEG для оценки длительности mp3 без сторонних библиотек
_BITRATE = {
    (3, 1): [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0],
    (3, 2): [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0],
    (3, 0): [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0],
}
_RATE = {3: [44100, 48000, 32000], 2: [22050, 24000, 16000], 0: [11025, 12000, 8000]}
_LENGTH: dict[Path, float] = {}


def load_config() -> dict:
    """Читает config.json, дополняя отсутствующие ключи и мигрируя схему V1."""
    cfg = dict(DEFAULT_CONFIG)
    try:
        cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    cfg["volume"] = max(0, min(VOLUME_STEPS, int(cfg.get("volume", 8))))
    if not cfg.get("profiles") and cfg.get("game_exe"):      # миграция со схемы V1
        cfg["profiles"] = [{"name": Path(cfg["game_exe"]).name, "enabled": True}]
    cfg.pop("game_exe", None)
    return cfg


def save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def music_dir(cfg: dict) -> Path:
    custom = (cfg.get("music_dir") or "").strip()
    return Path(custom).expanduser() if custom else MUSIC_DIR


def scan_music(cfg: dict | None = None) -> list[Path]:
    """Список аудиофайлов в папке музыки, отсортированный по имени."""
    folder = music_dir(cfg or {})
    folder.mkdir(parents=True, exist_ok=True)
    return sorted(p for p in folder.iterdir()
                  if p.is_file() and p.suffix.lower() in EXTENSIONS)


def track_title(path: Path) -> str:
    """Короткое человекочитаемое имя трека для LCD и баннера."""
    return path.stem.replace("_", " ").strip().upper() or path.name.upper()


def _mutagen_length(path: Path) -> float:
    """Безопасный фолбэк длительности через mutagen (если парсер тегов не узнал)."""
    try:
        import mutagen.mp3
        return float(mutagen.mp3.MP3(str(path)).info.length)
    except Exception:
        return 0.0


def track_duration(path: Path) -> float:
    """Длительность трека в секундах: Xing/Info для VBR, иначе CBR-оценка,
    затем mutagen. Никогда не бросает исключений: неизвестно -> 0.0."""
    if path in _LENGTH:
        return _LENGTH[path]
    seconds = 0.0
    try:
        if path.suffix.lower() == ".wav":
            import wave
            with wave.open(str(path)) as fh:
                seconds = fh.getnframes() / float(fh.getframerate())
        else:
            seconds = _mpeg_seconds(path.read_bytes())
            if seconds <= 0:
                seconds = _mutagen_length(path)
    except (OSError, ValueError, EOFError, ZeroDivisionError):
        seconds = 0.0
    _LENGTH[path] = seconds
    return seconds


def _mpeg_seconds(data: bytes) -> float:
    i = 0
    while True:                                    # ищем синхрослово 0xFFE...
        i = data.find(b"\xff", i)
        if i < 0 or i + 4 > len(data):
            return 0.0
        if (int.from_bytes(data[i:i + 2], "big") & 0xFFE0) == 0xFFE0:
            break
        i += 1
    h2, h3, h4 = data[i + 1], data[i + 2], data[i + 3]
    version, layer = (h2 >> 3) & 3, (h2 >> 1) & 3
    if version == 1 or layer != 1:                 # поддерживаем только Layer III
        return 0.0
    br_index, sr_index = (h3 >> 4) & 15, (h3 >> 2) & 3
    bitrate = _BITRATE.get((3, version), [0] * 16)[br_index] * 1000
    rate = _RATE[version][sr_index]
    if not bitrate or rate == 0:
        return 0.0
    samples = 1152 if version == 3 else 576
    mono = ((h4 >> 6) & 3) == 3
    side = {3: (17 if mono else 32)}.get(version, 9 if mono else 17)
    tag = data[i + 4 + side:i + 8 + side]
    if tag in (b"Xing", b"Info"):                  # VBR: точное число кадров
        flags = int.from_bytes(data[i + 8 + side:i + 12 + side], "big")
        if flags & 1:
            frames = int.from_bytes(data[i + 12 + side:i + 16 + side], "big")
            return frames * samples / rate
    return (len(data) - i) * 8.0 / bitrate         # CBR: по размеру файла


def autostart_enabled() -> bool:
    """Стоит ли плеер в автозагрузке Windows (HKCU ...\\Run)."""
    if sys.platform != "win32":
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, RUN_VALUE)
            return True
    except OSError:
        return False


def set_autostart(on: bool) -> None:
    """Пишет/удаляет значение автозагрузки; стартует свёрнутым в трей (--tray)."""
    if sys.platform != "win32":
        return
    import winreg
    if on:
        if getattr(sys, "frozen", False):
            cmd = f'"{sys.executable}" --tray'
        else:
            interp = Path(sys.executable)
            interp = interp.with_name("pythonw.exe") if interp.with_name(
                "pythonw.exe").exists() else interp
            cmd = f'"{interp}" "{BASE_DIR / "ui.py"}" --tray'
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, cmd)
    else:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY,
                                0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, RUN_VALUE)
        except OSError:
            pass


class MusicPlayer:
    """Обёртка над pygame.mixer.music: плейлист, статус, громкость, фейд, перемотка."""

    IDLE, PLAY, PAUSE = "IDLE", "PLAY", "PAUSE"

    def __init__(self, cfg: dict | None = None) -> None:
        import pygame  # локальный импорт: модуль остаётся импортируемым без pygame

        cfg = cfg or {}
        self._pygame = pygame
        pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=1024)
        self.folder = music_dir(cfg)
        self.tracks: list[Path] = []
        self.index = 0
        self.state = self.IDLE
        self.volume = max(0, min(VOLUME_STEPS, int(cfg.get("volume", 8))))
        self._fade_stop = threading.Event()
        self._fade_thread: threading.Thread | None = None
        self._seek_base = 0.0
        self._started_at = 0.0
        self._anchor_t = 0.0          # монотонное время опорной точки
        self._anchor_s = 0.0          # секунда потока в опорной точке
        self.rescan()

    # --- плейлист -------------------------------------------------------
    def rescan(self, cfg: dict | None = None) -> bool:
        if cfg is not None:
            self.folder = music_dir(cfg)
        self.tracks = scan_music({"music_dir": str(self.folder)})
        if self.index >= len(self.tracks):
            self.index = 0
        return bool(self.tracks)

    @property
    def current(self) -> Path | None:
        return self.tracks[self.index] if self.tracks else None

    @property
    def duration(self) -> float:
        """Длительность трека; неизвестна -> 0.0 (перемотка просто отключена)."""
        return track_duration(self.current) if self.current else 0.0

    @property
    def position(self) -> float:
        """Текущая секунда: опорная точка + elapsed (не зависит от get_pos)."""
        if self.state == self.IDLE:
            return 0.0
        if self.state == self.PAUSE:
            pos = self._anchor_s
        else:
            pos = self._anchor_s + (time.monotonic() - self._anchor_t)
        return max(0.0, min(self.duration or pos, pos))

    # --- громкость ------------------------------------------------------
    @property
    def level(self) -> float:
        return self.volume / VOLUME_STEPS

    def set_volume(self, steps: int) -> int:
        self.volume = max(0, min(VOLUME_STEPS, steps))
        if not self._fading():
            self._pygame.mixer.music.set_volume(self.level)
        return self.volume

    def volume_up(self) -> int:
        return self.set_volume(self.volume + 1)

    def volume_down(self) -> int:
        return self.set_volume(self.volume - 1)

    # --- воспроизведение -------------------------------------------------
    def play_index(self, index: int, start: float = 0.0) -> Path | None:
        if not self.tracks:
            self.state = self.IDLE
            return None
        self.index = index % len(self.tracks)
        path = self.tracks[self.index]
        try:
            self.stop_fade()
            self._pygame.mixer.music.load(str(path))
            self._pygame.mixer.music.set_volume(self.level)
            self._pygame.mixer.music.play(0, start)
            self._seek_base = start
            self._anchor_s = start
            self._anchor_t = time.monotonic()
            self._started_at = self._anchor_t
            self.state = self.PLAY
            return path
        except Exception:              # любой сбой движка не должен ронять плеер
            self.state = self.IDLE
            return None

    def play_current(self) -> Path | None:
        return self.play_index(self.index)

    def next_track(self) -> Path | None:
        return self.play_index(self.index + 1)

    def prev_track(self) -> Path | None:
        return self.play_index(self.index - 1)

    def seek(self, seconds: float) -> None:
        """Надёжная перемотка через music.set_pos(seconds).

        ``play(0, start)`` для MP3 на Windows нестабилен, поэтому позицию
        ставим уже идущему потоку; из IDLE трек сначала стартует. Пауза и
        громкость сохраняются, а позицию держит опорная точка (_anchor_*).
        """
        if not self.current:
            return
        dur = self.duration
        if dur <= 0:
            return
        seconds = max(0.0, min(max(0.0, dur - 0.3), seconds))
        was_paused = self.state == self.PAUSE
        if self.state == self.IDLE:
            if self.play_index(self.index) is None:
                return
        try:
            self._pygame.mixer.music.set_pos(seconds)
        except self._pygame.error:
            self.play_index(self.index, start=seconds)   # фолбэк: reload + start
            if was_paused:
                self.pause()
            return
        self._anchor_s = seconds
        self._anchor_t = time.monotonic()
        self._seek_base = seconds
        if was_paused:
            self.pause()

    def pause(self) -> None:
        if self.state == self.PLAY:
            self._anchor_s = self.position        # запоминаем замершую секунду
            self._pygame.mixer.music.pause()
            self.state = self.PAUSE

    def unpause(self) -> None:
        if self.state == self.PAUSE:
            self._anchor_t = time.monotonic()     # отсчёт elapsed заново
            self._pygame.mixer.music.unpause()
            self.state = self.PLAY

    def toggle(self) -> str:
        if self.state == self.PLAY:
            self.pause()
        elif self.state == self.PAUSE:
            self.unpause()
        else:
            self.play_current()
        return self.state

    def stop(self) -> None:
        self.stop_fade()
        self._pygame.mixer.music.stop()
        self._seek_base = 0.0
        self.state = self.IDLE

    def finished(self) -> bool:
        """True, когда трек доиграл сам (для автоперехода к следующему)."""
        return (self.state == self.PLAY
                and time.monotonic() - self._started_at > 0.7
                and not self._pygame.mixer.music.get_busy())

    # --- фейд -------------------------------------------------------------
    def _fading(self) -> bool:
        return self._fade_thread is not None and self._fade_thread.is_alive()

    def fade_in(self, seconds: float = 5.0) -> None:
        """Линейный набор громкости от 0 до сохранённого уровня за N секунд."""
        self.stop_fade()
        target = self.level
        self._fade_stop.clear()

        def ramp() -> None:
            steps = max(1, int(seconds * 20))          # шаг 50 мс
            self._pygame.mixer.music.set_volume(0.0)
            for i in range(1, steps + 1):
                if self._fade_stop.wait(0.05):
                    return
                self._pygame.mixer.music.set_volume(target * i / steps)

        self._fade_thread = threading.Thread(target=ramp, daemon=True)
        self._fade_thread.start()

    def stop_fade(self) -> None:
        self._fade_stop.set()
        if self._fade_thread and self._fade_thread.is_alive():
            self._fade_thread.join(timeout=0.3)
        self._fade_thread = None

    def play_with_fade(self, seconds: float = 5.0) -> Path | None:
        """Старт трека с нуля и фейд-ин до сохранённой громкости."""
        path = self.play_current()
        if path:
            self.fade_in(seconds)
        return path
