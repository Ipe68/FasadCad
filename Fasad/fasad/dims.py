"""Размеры и отметки на фасаде витража (СПДС).

Снизу: цепочка шагов стоек по осям, под ней - общая ширина по наружным граням.
Слева: цепочка от низа стоек до осей ригелей и между ними до верха, рядом - общая высота.
Справа: отметки - низ (±0,000 по умолчанию), оси ригелей, верх.
"""

from __future__ import annotations

from .facade import Facade, System
from .sections import Item

DIM_LAYER = "MARK_РАЗМЕРЫ"


def _level_text(y_mm: float, zero: float) -> str:
    v = (y_mm - zero) / 1000
    if abs(v) < 0.0005:
        return "±0,000"
    return f"{'+' if v > 0 else '-'}{abs(v):.3f}".replace(".", ",")


def _level_mark(items, x_face: float, x: float, y: float, text: str, f: dict, d: dict, down: bool = False):
    """Знак отметки: выносная линия от витража, треугольник остриём на линии, полка с текстом.
    down=True - полка вниз (когда отметка выше стоит слишком близко)."""
    M = f["масштаб"]
    s = d["знак_отметки"] * M
    h = d["высота_текста"] * M
    k = -1 if down else 1
    items.append(Item("poly", DIM_LAYER, pts=[(x_face + d["отступ_выносной"] * M, y), (x + s * 1.5, y)], closed=False))
    items.append(Item("poly", DIM_LAYER, pts=[(x, y), (x - s / 2, y + k * s / 2), (x + s / 2, y + k * s / 2)]))
    shelf_y = y + k * s * 1.6
    items.append(Item("poly", DIM_LAYER, pts=[(x, y), (x, shelf_y), (x + len(text) * h * 0.75, shelf_y)], closed=False))
    ty = shelf_y + h * 0.4 if not down else shelf_y - h * 1.4
    items.append(Item("text", DIM_LAYER, x=x + h * 0.3, y=ty, h=h, text=text, align="left"))


def build_dims(fac: Facade, system: System, zero_y: float = 0.0) -> list[Item]:
    card = system.card
    f, d = card["оформление"], card["размеры"]
    M = f["масштаб"]
    half = card["система"]["ширина_профиля"] / 2
    items: list[Item] = []
    if not fac.post_x:
        return items
    lo = min(a for a, b in fac.post_ends.values())
    hi = max(b for a, b in fac.post_ends.values())
    xl, xr = fac.post_x[0] - half, fac.post_x[-1] + half
    first, step = d["первая_цепочка"] * M, d["шаг_цепочек"] * M

    def dim_h(x1, x2, y_obj, y_line):
        items.append(Item("dim", DIM_LAYER, pts=[(x1, y_obj), (x2, y_obj), ((x1 + x2) / 2, y_line)], x=0.0))

    def dim_v(y1, y2, x_obj, x_line):
        items.append(Item("dim", DIM_LAYER, pts=[(x_obj, y1), (x_obj, y2), (x_line, (y1 + y2) / 2)], x=90.0))

    # снизу: шаг стоек по осям, затем общая ширина
    y1 = lo - first
    for a, b in zip(fac.post_x, fac.post_x[1:]):
        dim_h(a, b, lo, y1)
    dim_h(xl, xr, lo, y1 - step)

    # слева: низ -> ось нижнего ригеля -> ... -> ось верхнего ригеля -> верх; затем общая высота
    levels = sorted({y for ys in fac.bays.values() for y in ys})
    chain = [lo] + levels + [hi]
    x1 = xl - first
    for a, b in zip(chain, chain[1:]):
        dim_v(a, b, xl, x1)
    dim_v(lo, hi, xl, x1 - step)

    # справа: отметки
    xm = xr + d["отметки_от_витража"] * M
    min_gap = (d["знак_отметки"] * 1.6 + d["высота_текста"] * 1.6) * M     # высота знака с текстом
    prev, prev_down = None, False
    for y in sorted({lo, hi, *levels}, reverse=True):          # сверху вниз
        down = prev is not None and prev - y < min_gap and not prev_down
        _level_mark(items, xr, xm, y, _level_text(y, zero_y), f, d, down)
        prev, prev_down = y, down
    return items
