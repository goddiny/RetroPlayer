"""Асинхронный WMI-наблюдатель за запуском И ЗАКРЫТИЕМ процессов.

Слушает Win32_ProcessStartTrace и Win32_ProcessStopTrace без опроса в цикле.
Шлёт события: on_event("start", name) и on_event("stop", name).
"""
from __future__ import annotations

import ctypes
import sys
import threading
from ctypes import wintypes

QUERY_START = "SELECT * FROM Win32_ProcessStartTrace"
QUERY_STOP = "SELECT * FROM Win32_ProcessStopTrace"


def running_processes() -> list[str]:
    """Сортированный список имён запущенных процессов (toolhelp)."""
    if sys.platform != "win32":
        return []

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID",
                    ctypes.c_void_p), ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD), ("th32ParentProcessID",
                    wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 261)]

    kernel = ctypes.windll.kernel32
    snap = kernel.CreateToolhelp32Snapshot(0x2, 0)
    if snap == -1:
        return []
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
    names: set[str] = set()
    try:
        if kernel.Process32FirstW(snap, ctypes.byref(entry)):
            while True:
                names.add(entry.szExeFile)
                if not kernel.Process32NextW(snap, ctypes.byref(entry)):
                    break
    finally:
        kernel.CloseHandle(snap)
    return sorted(names, key=str.lower)


class ProcessWatcher:
    """Следит за запуском и выходом процессов: дёргает on_event(action, name)."""

    def __init__(self, on_event: "callable") -> None:
        self._on_event = on_event
        self._names: set[str] = set()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._sinks = []
        self._connections = []

    def watch(self, names: list[str]) -> bool:
        self.stop()
        if sys.platform != "win32":
            return False
        self._names = {n.strip().lower() for n in names if n.strip()}
        if not self._names:
            return False
        self._stop.clear()
        try:
            async_ok = self._start_async()
        except Exception:
            async_ok = False
        self._thread = threading.Thread(target=self._loop, args=(async_ok,), daemon=True)
        self._thread.start()
        return async_ok

    def stop(self) -> None:
        self._stop.set()
        self._sinks.clear()
        self._connections.clear()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=0.5)
        self._thread = None

    @property
    def active(self) -> bool:
        return bool(self._names) and self._thread is not None and self._thread.is_alive()

    def _match(self, name: str) -> str:
        low = name.lower()
        return low if low in self._names else ""

    def _notify(self, name: str, action: str) -> None:
        hit = self._match(name)
        if hit:
            self._on_event(action, hit)

    def _start_async(self) -> bool:
        """Подписка на старт и закрытие процессов через WMI."""
        import comtypes.client

        watcher = self

        class EventSink:
            def __init__(self, action: str):
                self.action = action

            def OnObjectReady(self, obj, _ctx=None):
                try:
                    name = obj.ProcessName or obj.TargetInstance.Name
                except Exception:
                    return
                watcher._notify(str(name), self.action)

        services = comtypes.client.GetObject("winmgmts:")

        # 1. Ловим запуск
        sink_start = comtypes.client.CreateObject("WbemScripting.SWbemSink")
        conn_start = comtypes.client.GetEvents(sink_start, EventSink("start"))
        services.ExecNotificationQueryAsync(sink_start, QUERY_START)

        # 2. Ловим закрытие
        sink_stop = comtypes.client.CreateObject("WbemScripting.SWbemSink")
        conn_stop = comtypes.client.GetEvents(sink_stop, EventSink("stop"))
        services.ExecNotificationQueryAsync(sink_stop, QUERY_STOP)

        self._sinks = [sink_start, sink_stop]
        self._connections = [conn_start, conn_stop]
        return True

    def _loop(self, async_ok: bool) -> None:
        if async_ok:
            self._stop.wait()
            return
        # Фолбэк: если WMI недоступен, отслеживаем разницу снимков раз в 2 секунды
        known = set(running_processes())
        while not self._stop.wait(2.0):
            now = set(running_processes())
            for name in now - known:
                self._notify(name, "start")
            for name in known - now:
                self._notify(name, "stop")
            known = now