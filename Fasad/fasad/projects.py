"""Project folders and drawings used by the AutoCAD build command."""

from __future__ import annotations

import re
from pathlib import Path

PROJECTS_DIR = Path(r"C:\cad\Projects")
_INVALID_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                   *(f"LPT{i}" for i in range(1, 10))}


class ProjectError(ValueError):
    pass


def validate_project_name(name: str) -> str:
    name = name.strip()
    if not name:
        raise ProjectError("Введите имя проекта.")
    if _INVALID_NAME.search(name) or name.endswith((".", " ")):
        raise ProjectError("Имя проекта содержит недопустимые символы или заканчивается точкой/пробелом.")
    if name in {".", ".."} or name.split(".", 1)[0].upper() in _RESERVED_NAMES:
        raise ProjectError("Это имя нельзя использовать для папки или файла проекта.")
    if len(name) > 120:
        raise ProjectError("Имя проекта должно быть не длиннее 120 символов.")
    return name


def project_folder(name: str) -> Path:
    name = validate_project_name(name)
    return PROJECTS_DIR / name


def project_drawing(name: str, folder: Path | None = None) -> Path:
    directory = folder or project_folder(name)
    return directory / f"{name}.dwg"


def find_project_drawing(name: str) -> Path | None:
    """Find an existing DWG by its filename, case-insensitively."""
    if not PROJECTS_DIR.exists():
        return None
    matches = [
        drawing
        for folder in PROJECTS_DIR.iterdir()
        if folder.is_dir()
        for drawing in folder.glob("*.dwg")
        if drawing.stem.casefold() == name.casefold()
    ]
    if len(matches) > 1:
        raise ProjectError(f"В Projects найдено несколько DWG с именем «{name}»: " +
                           ", ".join(str(path) for path in matches))
    return matches[0] if matches else None
