"""Маркировка стоек и ригелей - по правилам старой Calk_prof (layer_processor.finalize_construction_marks).

Стойки: S1, S2... слева направо.
Ригели: номер по длине внутри витража (короче - меньше номер; разные артикулы - разные номера). Если ригели одной длины стоят
на нескольких уровнях: самый верхний - "_в", самый нижний - "_н", промежуточные - без суффикса.
"""

from __future__ import annotations

from .facade import Facade, System
from .sections import Item

POST_MARK_LAYER = "MARK_S"
BEAM_MARK_LAYER = "MARK_R"


def beam_marks(fac: Facade, system: System) -> list[tuple[float, float, float, str]]:
    """Марки ригелей витража: посчитанные при построении (по правилам, до правок таблицы профилей)."""
    return fac.marks or compute_marks(fac, system)


def compute_marks(fac: Facade, system: System) -> list[tuple[float, float, float, str]]:
    """(y, x_лев, x_прав, марка) для каждого пролёта ригеля и фальш-ригеля.
    Ригели разных артикулов (КП45550, стоечный профиль и т.п.) одной длины - разные марки.
    Фальш-ригели - ФР1, ФР2... по длине."""
    kind = system.card["ригель"]["тип"]
    out = _numbered(fac, list(fac.beam_art),
                    lambda p: p.kind.startswith(kind), "R")
    out += _numbered(fac, [(y, xl, xr) for y, xl, xr, _ in fac.false_spans],
                     lambda p: p.kind == "Фальш-ригель", "ФР")
    return out


def _numbered(fac: Facade, spans_in, is_beam, prefix: str) -> list[tuple[float, float, float, str]]:
    parts = {p.span: (p.article, p.length) for p in fac.parts if is_beam(p) and not p.vertical and p.span}
    spans = [(y, xl, xr, parts.get((round(y, 3), xl, xr))) for y, xl, xr in spans_in]
    sizes = sorted({L for *_, L in spans if L is not None}, key=lambda al: (al[1], al[0]))
    num = {L: i for i, L in enumerate(sizes, 1)}
    out = []
    for L in sizes:
        same = [s for s in spans if s[3] == L]
        levels = sorted({round(s[0] / 10) * 10 for s in same}, reverse=True)
        for y, xl, xr, _ in same:
            lv = round(y / 10) * 10
            mark = f"{prefix}{num[L]}"
            if len(levels) > 1:
                if lv == levels[0]:
                    mark += "_в"
                elif lv == levels[-1]:
                    mark += "_н"
            out.append((y, xl, xr, mark))
    return out


def build_marks(fac: Facade, system: System) -> list[Item]:
    card = system.card
    f = card["оформление"]
    h = f["высота_марки"] * f["масштаб"]
    half = card["система"]["ширина_профиля"] / 2
    items: list[Item] = []
    # стойки - на высоте середины нижнего ряда стёкол, сбоку от оси
    rows = sorted({gl.y0 for gl in fac.glasses})
    for n, px in enumerate(fac.post_x, 1):
        lo, hi = fac.post_ends[px]
        y = (lo + hi) / 2
        items.append(Item("text", POST_MARK_LAYER, x=px + half + h * 0.9, y=y, h=h, text=f"S{n}"))
    # ригели - над ригелем посередине пролёта
    for y, xl, xr, mark in beam_marks(fac, system):
        items.append(Item("text", BEAM_MARK_LAYER, x=(xl + xr) / 2, y=y + half + h * 0.9, h=h, text=mark))
    return items
