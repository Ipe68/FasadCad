"""Связь с открытым AutoCAD через LISP-диспетчер autocad-mcp (mcp_dispatch.lsp).

Протокол тот же, что у autocad-mcp: пишем JSON-команду в C:/temp,
вызываем "(c:mcp-dispatch)", ждём файл с ответом.
Отличия от autocad-mcp:
  - вызов передаётся одной строкой (WM_COPYDATA), а не по буквам - не ломается при смене активного окна;
  - окно AutoCAD ищется по началу заголовка, поэтому подходят и .dwg, и .dxf;
  - ответ AutoCAD (cp1251) декодируется правильно, русский текст не искажается.
Только стандартная библиотека Python.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import time
import uuid
from pathlib import Path

IPC_DIR = Path("C:/temp")
TIMEOUT = 30.0

_user32 = ctypes.windll.user32
_WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


class AcadError(RuntimeError):
    pass


class _COPYDATASTRUCT(ctypes.Structure):
    _fields_ = [("dwData", ctypes.c_size_t), ("cbData", wt.DWORD), ("lpData", ctypes.c_void_p)]


_user32.SendMessageTimeoutW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, ctypes.c_void_p, wt.UINT, wt.UINT,
                                        ctypes.POINTER(ctypes.c_size_t)]


def _window_text(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(512)
    _user32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


def _class_name(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    _user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def find_autocad_window() -> int | None:
    found: list[int] = []

    def cb(hwnd, _):
        if _user32.IsWindowVisible(hwnd) and _window_text(hwnd).startswith("Autodesk AutoCAD"):
            found.append(hwnd)
            return False
        return True

    _user32.EnumWindows(_WNDENUMPROC(cb), 0)
    return found[0] if found else None


def _find_mdi_client(hwnd) -> int | None:
    found: list[int] = []

    def cb(child, _):
        if _class_name(child) == "MDIClient":
            found.append(child)
            return False
        return True

    _user32.EnumChildWindows(hwnd, _WNDENUMPROC(cb), 0)
    return found[0] if found else None


def _decode(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1251")


class Acad:
    """Открытый AutoCAD. Главный метод — run_lisp(код)."""

    def __init__(self):
        self.hwnd = find_autocad_window()
        if not self.hwnd:
            raise AcadError("Не найдено окно AutoCAD. Откройте AutoCAD с чертежом.")
        self.target = _find_mdi_client(self.hwnd) or self.hwnd
        IPC_DIR.mkdir(parents=True, exist_ok=True)
        if self._dispatch("ping", {}) is None:
            raise AcadError("AutoCAD найден, но mcp_dispatch.lsp не загружен (команда APPLOAD).")

    @property
    def title(self) -> str:
        return _window_text(self.hwnd)

    def wait_ready(self, pause: float = 1.0):
        """После закрытия своих окон: дать AutoCAD получить фокус и проверить связь."""
        time.sleep(pause)
        if self._dispatch("ping", {}) is None:
            raise AcadError("AutoCAD не отвечает после закрытия окна")

    def drawing_name(self) -> str:
        """Имя активного чертежа (DWGNAME), например 'Чертеж1.dwg'."""
        return self.run_lisp('(getvar "DWGNAME")') or ""

    def drawing_path(self) -> str:
        """Полный путь активного чертежа."""
        return self.run_lisp('(strcat (getvar "DWGPREFIX") (getvar "DWGNAME"))') or ""

    def require_drawing(self, name: str):
        """Защита: работать только в указанном чертеже, чтобы не испортить другой открытый файл."""
        active = self.drawing_name()
        if active.lower() != name.lower():
            raise AcadError(f"Активный чертёж в AutoCAD - \"{active}\", а работать разрешено только в \"{name}\". "
                            f"Переключитесь на вкладку \"{name}\" и запустите снова.")

    def require_drawing_path(self, path: str):
        """Проверить, что активен тот же файл, включая путь."""
        active = Path(self.drawing_path())
        expected = Path(path)
        if active.resolve() != expected.resolve():
            raise AcadError(f"Активный чертёж изменился: \"{active}\" вместо \"{expected}\".")

    @staticmethod
    def _lisp_string(value: str) -> str:
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'

    def save_as(self, path: Path, overwrite: bool = False):
        """Сохранить активный чертёж по полному пути."""
        path = path.resolve()
        if path.exists() and not overwrite:
            raise AcadError(f"Файл уже существует, сохранение отменено: {path}")
        code = (f'(progn (vl-load-com) '
                f'(vla-SaveAs (vla-get-ActiveDocument (vlax-get-acad-object)) '
                f'{self._lisp_string(str(path))}) (princ "да"))')
        self.run_lisp(code, timeout=120)
        if Path(self.drawing_path()).resolve() != path:
            raise AcadError(f"AutoCAD не переключился на сохранённый чертёж: {path}")

    def save_drawing(self):
        """Сохранить активный чертёж."""
        self.run_lisp('(progn (vla-Save (vla-get-ActiveDocument (vlax-get-acad-object))) (princ "да"))',
                      timeout=120)

    def _trigger(self):
        """Вызвать диспетчер: вся строка одним сообщением WM_COPYDATA в главное окно AutoCAD.
        Раньше буквы посылались по одной (WM_CHAR) - при смене активного окна часть терялась:
        в AutoCAD оставалась "(" и всё висло, а обрывки букв запускали чужие команды (стирание)."""
        text = "\x03\x03(c:mcp-dispatch)\n"            # Ctrl+C x2 - прервать текущую команду
        buf = ctypes.create_unicode_buffer(text)
        cds = _COPYDATASTRUCT(1, ctypes.sizeof(buf), ctypes.cast(buf, ctypes.c_void_p))
        res = ctypes.c_size_t()
        WM_COPYDATA, SMTO_ABORTIFHUNG = 0x004A, 0x0002
        _user32.SendMessageTimeoutW(self.hwnd, WM_COPYDATA, 0, ctypes.byref(cds), SMTO_ABORTIFHUNG, 5000,
                                    ctypes.byref(res))

    def _dispatch(self, command: str, params: dict, timeout: float = TIMEOUT):
        rid = uuid.uuid4().hex[:12]
        cmd = IPC_DIR / f"autocad_mcp_cmd_{rid}.json"
        res = IPC_DIR / f"autocad_mcp_result_{rid}.json"
        tmp = cmd.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps({"request_id": rid, "command": command, "params": params}), encoding="utf-8")
            tmp.rename(cmd)
            self._trigger()
            deadline = time.time() + timeout
            # повторно НЕ вызывать: Ctrl+C повторного вызова прерывает долгую команду на полпути,
            # а набранный текст попал бы в запрос работающей команды
            while time.time() < deadline:
                if res.exists():
                    try:
                        data = json.loads(_decode(res.read_bytes()))
                    except (json.JSONDecodeError, OSError):
                        data = None
                    if data and data.get("request_id") == rid:
                        if not data.get("ok"):
                            raise AcadError(data.get("error") or "ошибка AutoCAD")
                        return data.get("payload")
                time.sleep(0.1)
            raise AcadError("AutoCAD не ответил (возможно, занят командой или открыт диалог).")
        finally:
            for f in (cmd, res, tmp):
                f.unlink(missing_ok=True)

    def run_lisp(self, code: str, timeout: float = TIMEOUT):
        """Выполнить код AutoLISP. Возвращает результат последнего выражения строкой.
        timeout - сколько ждать ответа, с (для тяжёлых операций - больше)."""
        f = IPC_DIR / f"fasad_{uuid.uuid4().hex[:8]}.lsp"
        f.write_text(code, encoding="utf-8")
        try:
            return self._dispatch("execute-lisp", {"code_file": f.as_posix()}, timeout)
        finally:
            f.unlink(missing_ok=True)
