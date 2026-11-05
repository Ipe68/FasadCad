"""Геометрия профилей из библиотеки блоков сечений (КП50К_блоки.dxf), только стандартная библиотека.

Камера под закладную: самый большой внутренний контур блока. Камера бывает не прямоугольной
(у стойки КП45548 со стороны стекла узкий паз 7x6 мм), поэтому глубина камеры - не габарит контура,
а длина участка, где камера во всю ширину: туда входит закладная.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path


def _pairs(path: Path):
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("cp1251")
    lines = text.splitlines()
    for i in range(0, len(lines) - 1, 2):
        yield lines[i].strip(), lines[i + 1].strip()


@lru_cache(maxsize=8)
def block_polylines(path: Path) -> dict[str, list[list[tuple[float, float]]]]:
    """Имя блока -> замкнутые LWPOLYLINE (вершины; дуги - хордами)."""
    blocks: dict[str, list] = {}
    name, in_blocks, poly, x = None, False, None, None
    for code, val in _pairs(path):
        if code == "2" and val == "BLOCKS":
            in_blocks = True
        elif code == "0" and val == "ENDSEC":
            in_blocks = False
        if not in_blocks:
            continue
        if code == "0":
            if poly is not None and poly[1] and len(poly[0]) > 2:
                blocks.setdefault(name, []).append(poly[0])
            poly = [[], False] if val == "LWPOLYLINE" else None
            if val == "BLOCK":
                name = None
        elif code == "2" and name is None and poly is None:
            name = val
        elif poly is not None:
            if code == "70":
                poly[1] = bool(int(val) & 1)
            elif code == "10":
                x = float(val)
            elif code == "20":
                poly[0].append((x, float(val)))
    return blocks


def _bbox(pts):
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _width_at(pts, t: float, axis: int) -> float:
    """Ширина многоугольника поперёк оси axis (0 - X, 1 - Y) в сечении axis = t."""
    hits = []
    for (a, b) in zip(pts, pts[1:] + pts[:1]):
        ta, tb = a[axis], b[axis]
        if (ta - t) * (tb - t) < 0:
            k = (t - ta) / (tb - ta)
            hits.append(a[1 - axis] + k * (b[1 - axis] - a[1 - axis]))
    hits.sort()
    return sum(h2 - h1 for h1, h2 in zip(hits[::2], hits[1::2]))


def chamber_depth(path: Path, block: str, tol: float = 0.5) -> tuple[float, float, float] | None:
    """(глубина камеры во всю ширину, ширина камеры, габарит камеры), мм. Глубина - вдоль длинной стороны профиля."""
    polys = block_polylines(Path(path)).get(block)
    if not polys or len(polys) < 2:
        return None
    area = lambda p: (lambda b: (b[2] - b[0]) * (b[3] - b[1]))(_bbox(p))
    outer, *inner = sorted(polys, key=area, reverse=True)
    cham = inner[0]
    ox0, oy0, ox1, oy1 = _bbox(outer)
    axis = 0 if ox1 - ox0 > oy1 - oy0 else 1                # глубина профиля - по длинной стороне
    ts = sorted({p[axis] for p in cham})
    mids = [((a + b) / 2, b - a) for a, b in zip(ts, ts[1:])]
    widths = [(_width_at(cham, t, axis), step) for t, step in mids]
    full = max(w for w, _ in widths)
    best = run = 0.0
    for w, step in widths:
        run = run + step if w >= full - tol else 0.0
        best = max(best, run)
    return round(best, 1), round(full, 1), round(ts[-1] - ts[0], 1)
