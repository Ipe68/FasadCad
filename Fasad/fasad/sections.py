"""Сечения витража, закладные и подкладки на фасаде, обозначения разрезов, выноски.

Горизонтальное сечение Б-Б - под фасадом, по всем стойкам.
Вертикальное сечение А-А - справа от фасада, по первому пролёту без двери.
Узлы собираются из блоков по шаблонам (fasad/nodes.py), стекло - по формуле стеклопакета.
Всё кладётся на слои MARK_..., чтобы старая Calk_prof не приняла это за профили.
Выноски - на отдельном слое MARK_ВЫНОСКИ (можно выключить).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .catalog_nodes import block_name, load_base, load_perimeter, node_template
from .catalog_pads import LIP_FROM_NOSE, RUBBER, bearing_y
from .facade import TOL, Facade, ObjectParams, System, _pad_heavy, formula_layers, parse_items, pick_filler
from .nodes import Place, node_places
from .profiles import block_polylines

SECTION_LAYER = "MARK_СЕЧЕНИЕ"
GLASS_LAYER = "MARK_СЕЧЕНИЕ_СТЕКЛО"
CUT_LAYER = "MARK_РАЗРЕЗ"
LEADER_LAYER = "MARK_ВЫНОСКИ"
SETTING_LAYER = "MARK_ПОДКЛАДКИ"
FACADE_SETTING_LAYER = "MARK_ФАСАД_ПОДКЛАДКИ"
INSERT_LAYER_PREFIX = "MARK_ЗАКЛ_"

_BAD_LAYER_CHARS = '<>/\\":;?*|,=`'          # запрещены в именах слоёв AutoCAD


def _layer_safe(name: str) -> str:
    """Имя слоя AutoCAD: запрещённые символы (например запятая в 'КП1336-131,5-б') - в точки."""
    return "".join(ch if ch not in _BAD_LAYER_CHARS else "." for ch in str(name))


@dataclass
class Item:
    kind: str                 # "insert" | "poly" | "text"
    layer: str
    place: Place | None = None
    pts: list | None = None   # (x, y) или (x, y, bulge)
    width: float = 0.0        # постоянная ширина полилинии
    x: float = 0.0
    y: float = 0.0
    h: float = 0.0
    text: str = ""
    align: str = "center"     # "center" | "left"
    closed: bool = True
    widths: tuple | None = None   # (начальная, конечная) ширина - для стрелки


def _label(block: str) -> str:
    """Подпись детали по имени блока: другая форма того же артикула - блок "Т50-04 (2)", подпись "Т50-04"."""
    return re.sub(r" \(\d+\)$", "", block)


def _names_map(fac: Facade, system: System) -> dict[str, str]:
    """Артикул -> расшифровка для легенды выносок ('КПС 344' -> 'ригель', 'КП45309' -> 'крышка ригеля')."""
    out: dict[str, str] = {}
    for p in fac.parts:
        if p.article:
            out.setdefault(_label(p.article), p.kind)
    for r in system.profiles:
        out.setdefault(_label(r["Шифр"]), r["Тип"])
    kit_cols = {"Термовставка": "термовставка", "Прижим": "прижим",
                "Крышка стойки": "крышка стойки", "Крышка ригеля": "крышка ригеля",
                "Подкладка опорная": "подкладка опорная", "Подкладка фиксир.": "подкладка фиксирующая",
                "Винт прижима": "винт прижима", "Винт под подкладку": "винт подкладки"}
    for r in system.kits:
        for col, nm in kit_cols.items():
            for a, alt, _ in parse_items(str(r.get(col) or "")):
                for x in (a, alt.strip()):
                    if x:
                        out.setdefault(_label(x), nm)
    for key, nm in (("стойки", "уплотнитель стойки"), ("ригели", "уплотнитель ригеля"),
                    ("прижим", "уплотнитель прижима")):
        a = system.card.get("уплотнители", {}).get(key)
        if a:
            out.setdefault(_label(a), nm)
    for r in system.perimeters:                     # в последнюю очередь: в наборе бывают уплотнители
        for col in ("Крайняя стойка", "Крайний ригель"):
            for a, alt, _ in parse_items(str(r.get(col) or "")):
                for x in (a, alt.strip()):
                    if x:
                        out.setdefault(_label(x), "доборный по периметру")
    return out


class _Legend:
    """Нумерованная расшифровка выносок сечения: на полке - номер, рядом столбик '1 — КПС 344 ригель'.
    Один на витраж: номера общие для всех сечений (одна деталь - один номер)."""

    def __init__(self, names: dict[str, str]):
        self.names = names
        self.num: dict[str, int] = {}

    def no(self, text: str) -> int:
        if text not in self.num:
            self.num[text] = len(self.num) + 1
        return self.num[text]

    def items(self, x_left: float, y_top: float, h: float) -> list[Item]:
        out = []
        for text, n in sorted(self.num.items(), key=lambda kv: kv[1]):
            desc = self.names.get(text, "")
            desc = desc[:1].lower() + desc[1:] if desc else ""      # "Стойка" -> "стойка"
            line = f"{n} — {text}" + (f" {desc}" if desc else "")
            out.append(Item("text", LEADER_LAYER, x=x_left, y=y_top - (n - 1) * h * 1.8,
                            h=h, text=line, align="left"))
        return out


def _rect(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def _role_index(template: dict, role: str, old_name: str) -> int | None:
    """Номер детали шаблона по роли (узлы из базы каталога) или по имени (старый шаблон)."""
    for i, d in enumerate(template["детали"]):
        if d.get("роль") == role or "роль" not in d and d["блок"] == old_name:
            return i
    return None


def _orient(template: dict, outward: tuple, edge: tuple | None, x: float, y: float) -> Place:
    """Подобрать поворот/зеркало профиля: прижим наружу, доборный периметра - к краю витража."""
    ic = _role_index(template, "прижим", "КПС 758")
    ie = _role_index(template, "доборный", "КПС 263")
    for mirror in (False, True):
        for rot in (0, 90, 180, 270):
            base = Place(template["профиль"], x, y, rot, mirror)
            parts = node_places(template, base)[1:]
            if ic is None or ((parts[ic].x - x) * outward[0] + (parts[ic].y - y) * outward[1]) <= 1:
                continue
            if edge and ie is not None:
                if ((parts[ie].x - x) * edge[0] + (parts[ie].y - y) * edge[1]) <= 1:
                    continue
            return base
    raise ValueError(f"Не удалось развернуть узел {template['профиль']}")


def _formula_layers(formula: str) -> list[tuple[str, float]]:
    """Слои стеклопакета снаружи внутрь: [('стекло', 6), ('рамка', 16), ...] - разбор в facade.formula_layers."""
    return [(kind, t) for kind, t, _ in formula_layers(formula)]


def _glass_section(items, a0, a1, inner, outward, formula, horizontal, depth, edge, thick: float = 0.0):
    """Стеклопакет в сечении: a0..a1 - вдоль витража, inner - внутренняя грань, outward - +1/-1.
    Нет формулы - условный прямоугольник во всю толщину заполнения."""
    pos = inner
    layers = _formula_layers(formula) if formula else ([("стекло", thick)] if thick else [])
    for kind, t in reversed(layers):                                       # формула - снаружи внутрь
        lo, hi = sorted((pos, pos + outward * t))
        rects = [(a0, lo, a1, hi)] if kind == "стекло" else \
            [(a0 + edge, lo, a0 + edge + depth, hi), (a1 - edge - depth, lo, a1 - edge, hi)]
        for b0, c0, b1, c1 in rects:
            items.append(Item("poly", GLASS_LAYER, pts=_rect(b0, c0, b1, c1) if horizontal else _rect(c0, b0, c1, b1)))
        pos += outward * t


def _leaders(items, anchors: list[tuple[float, float, str]], col_x: float, top_y: float, h: float,
             left: bool, legend: _Legend):
    """Ровный столбик номеров; порядок строк дополнительно распутывает наклонные линии."""
    seen, rows = set(), []
    for ax, ay, text in sorted(anchors, key=lambda a: (-a[1], a[0] if left else -a[0])):
        if text not in seen:
            seen.add(text)
            rows.append((ax, ay, text))
    shelf_y = [top_y - row * h * 1.6 for row in range(len(rows))]

    def crossings(seq) -> int:
        def orient(a, b, c):
            return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        total = 0
        for i in range(len(seq)):
            a, b = seq[i][:2], (col_x, shelf_y[i])
            for j in range(i + 1, len(seq)):
                c, d = seq[j][:2], (col_x, shelf_y[j])
                if orient(a, b, c) * orient(a, b, d) < 0 and orient(c, d, a) * orient(c, d, b) < 0:
                    total += 1
        return total

    starts = [rows, sorted(rows, key=lambda a: (-a[1], -a[0] if left else a[0])),
              sorted(rows, key=lambda a: a[0]), sorted(rows, key=lambda a: -a[0])]
    best_rows, best_n = rows, crossings(rows)
    for start in starts:
        candidate = start[:]
        for _ in range(len(rows) * 2):
            current, swap = crossings(candidate), None
            for i in range(len(rows)):
                for j in range(i + 1, len(rows)):
                    trial = candidate[:]
                    trial[i], trial[j] = trial[j], trial[i]
                    n = crossings(trial)
                    if n < current:
                        current, swap = n, (i, j)
            if swap is None:
                break
            i, j = swap
            candidate[i], candidate[j] = candidate[j], candidate[i]
        if crossings(candidate) < best_n:
            best_rows, best_n = candidate, crossings(candidate)
        if best_n == 0:
            break
    rows = best_rows

    shelf = 2 * h * 0.7
    x_end = col_x - shelf if left else col_x + shelf
    for (ax, ay, text), ty in zip(rows, shelf_y):
        num = str(legend.no(text))
        items.append(Item("poly", LEADER_LAYER, pts=[(ax, ay), (col_x, ty), (x_end, ty)], closed=False))
        items.append(Item("poly", LEADER_LAYER, pts=_rect(ax - h * 0.12, ay - h * 0.12, ax + h * 0.12, ay + h * 0.12)))
        items.append(Item("text", LEADER_LAYER, x=min(col_x, x_end), y=ty + h * 0.35, h=h, text=num, align="left"))


def _cut_mark(items, x, y, view: tuple, stroke_dir: tuple, letter: str, f: dict):
    """Разомкнутая линия разреза (ГОСТ 2.305), размеры - по образцу пользователя.

    (x, y) - внутренний конец штриха, stroke_dir - куда штрих идёт от витража,
    view - направление взгляда: стрелка стоит со стороны, противоположной взгляду,
    и остриём упирается в штрих.
    """
    M = f["масштаб"]
    L, w = f["штрих_разреза"] * M, f["толщина_штриха"] * M
    sx, sy = stroke_dir
    vx, vy = view
    items.append(Item("poly", CUT_LAYER, pts=[(x, y), (x + sx * L, y + sy * L)], width=w, closed=False))
    qx, qy = x + sx * (L - f["стрелка_от_конца"] * M), y + sy * (L - f["стрелка_от_конца"] * M)
    tip = (qx - vx * f["стрелка_зазор"] * M, qy - vy * f["стрелка_зазор"] * M)
    neck = (tip[0] - vx * f["наконечник"] * M, tip[1] - vy * f["наконечник"] * M)
    tail = (neck[0] - vx * f["хвост"] * M, neck[1] - vy * f["хвост"] * M)
    items.append(Item("poly", CUT_LAYER, pts=[tail, neck], width=f["хвост_ширина"] * M, closed=False))
    items.append(Item("poly", CUT_LAYER, pts=[neck, tip], closed=False,
                      widths=(f["наконечник_ширина"] * M, 0.0)))
    hb = f.get("высота_обозначения", f["высота_буквы_разреза"] / 2) * M
    if vx == 0:      # взгляд по вертикали (Б-Б): буква сбоку от хвоста, наружу от витража
        cx, cy = tail[0] + sx * hb * 0.6, tail[1] + hb * 0.3
    else:            # взгляд по горизонтали (А-А): буква за хвостом
        cx, cy = tail[0] - vx * hb * 0.6, tail[1]
    items.append(Item("text", CUT_LAYER, x=cx, y=cy, h=hb, text=letter))


def _leaders_by_layout(items, layout: list, bx: float, by: float, names: set, glass_name: str,
                       setting_name: str, h: float, points: dict | None = None,
                       legend: _Legend | None = None, fallback: tuple | None = None) -> set:
    """Выноски по столбикам из раскладки; fallback - все точки и положение для деталей вне раскладки."""
    done = set()
    groups = {}
    legend = legend or _Legend({})
    for e in layout:
        text = e["текст"]
        if text.startswith(("Стеклопакет", "Заполнение")):
            text = glass_name
        elif text not in names:
            if not setting_name or setting_name not in names:
                continue
            text = setting_name                       # подкладка - по текущей комплектации
        if text in done:
            continue
        done.add(text)
        a = (points or {}).get(text) or (bx + e["точка"][0], by + e["точка"][1])   # точка - на настоящей детали
        to_left = e["полка"][0] < e["перелом"][0]
        col_x = bx + e["перелом"][0] if text == glass_name else bx + layout[0]["перелом"][0]
        top_y = by + e["полка"][1]
        key = (to_left, col_x)
        if key not in groups:
            groups[key] = [top_y, []]
        else:
            groups[key][0] = max(groups[key][0], top_y)
        groups[key][1].append((a[0], a[1], text))
    if fallback:
        all_anchors, col_x, top_y, to_left = fallback
        key = (to_left, col_x)
        if key not in groups:
            groups[key] = [top_y, []]
        for ax, ay, text in all_anchors:
            if text not in done:
                groups[key][1].append((ax, ay, text))
                done.add(text)
    for (to_left, col_x), (top_y, anchors) in groups.items():
        _leaders(items, anchors, col_x, top_y, h, left=to_left, legend=legend)
    return done


def _riegel_nose(lib_file: Path, riegel: str) -> float:
    """Носик ригеля в системе его блока: край со стороны прижима (у КП45550 x = -21)."""
    polys = block_polylines(lib_file).get(riegel)
    return min(x for x, _ in max(polys, key=len)) if polys else -21.0


def build_sections(fac: Facade, system: System, obj: ObjectParams, folder: Path
                   ) -> tuple[list[Item], list[str], set[str], object]:
    """Сечения Б-Б и А-А. Возвращает (объекты, замечания, блоки деталей - их надо определить в чертеже,
    библиотека блоков: Library узлов пользователя или None - тогда блоки из базы каталога)."""
    from .library import Source
    src = Source(system, folder, obj, fac.kit, fac.perimeter)
    if src.ready():
        items, warnings = _sections_library(fac, system, obj, folder, src)
        return items, warnings, src.used, src.lib
    items, warnings, used = _sections_catalog(fac, system, obj, folder)
    return items, ["Сечение: нет библиотеки узлов - нарисовано по базе каталога (лист 03)"] + warnings, used, None


def _sections_library(fac: Facade, system: System, obj: ObjectParams, folder: Path, src) -> tuple[list[Item], list[str]]:
    """Сечения из библиотеки узлов пользователя: узел ставится носиком профиля на ось, зеркалится,
    профиль - выбранный в исходных данных; стеклопакет - по формуле (или прямоугольник по толщине)."""
    from . import rules as R
    from .library import label
    from .nodes import to_world
    card = system.card
    sc, f = card["сечение"], card["оформление"]
    M = f["масштаб"]
    warnings: list[str] = []
    lay_file = folder / sc.get("выноски", "-")
    layouts = json.loads(lay_file.read_text(encoding="utf-8"))["узлы"] if lay_file.is_file() else {}
    items: list[Item] = []
    half = card["система"]["ширина_профиля"] / 2
    hl = f["высота_выноски"] * f.get("масштаб_сечений", M)
    hb = f["высота_буквы_разреза"] * M
    hb_t = f.get("высота_обозначения", f["высота_буквы_разреза"] / 2) * M   # подпись "А-А" у сечения
    glass_name = f"Стеклопакет {obj.формула}" if obj.формула else f"Заполнение {obj.заполнение} мм"
    legend = _Legend(_names_map(fac, system))       # номера выносок общие для всех сечений витража

    def put(node, base) -> list:
        """Вставить узел в системе base; вернуть точки выносок (середина детали, подпись)."""
        anchors = []
        for p in node.places:
            w = to_world(base, p)
            items.append(Item("insert", SECTION_LAYER, place=w))
            b = src.lib.placed_box(p)
            c = to_world(base, Place("", (b[0] + b[2]) / 2, (b[1] + b[3]) / 2))
            anchors.append((c.x, c.y, label(p.name, src.known)))
        for a0, b0, a1, b1 in node.pads:
            pts = [to_world(base, Place("", x, y)) for x, y in ((a0, b0), (a1, b0), (a1, b1), (a0, b1))]
            items.append(Item("poly", SETTING_LAYER, pts=[(q.x, q.y) for q in pts]))
        for n in node.notes:
            if f"Сечение: {n}" not in warnings:
                warnings.append(f"Сечение: {n}")
        return anchors

    # ---------- Б-Б: горизонтальное сечение, наружу = вниз; y0 - носики стоек ----------
    floor = min(lo for lo, hi in fac.post_ends.values())
    y0 = floor - sc["отступ_сечения"] - 2 * hb
    left, right = fac.post_x[0], fac.post_x[-1]
    y_top = max(hi for lo, hi in fac.post_ends.values())

    def post_art(px) -> str:
        """Профиль стойки для сечения: по детали марки (с учётом правок таблицы профилей)."""
        n = fac.post_x.index(px) + 1
        p = next((p for p in fac.parts if p.where == f"S{n}" and p.kind == "Стойка"), None)
        return p.article if p else (obj.стойка or card["стойка"]["артикул"])

    def _bb(y0_pos: float, cut_y: float | None, letter: str, prefix: str,
            x_range: tuple | None = None, mark_ends: tuple | None = None) -> None:
        """Горизонтальное сечение по стойкам. y0_pos - носики стоек (где рисовать),
        cut_y - высота засечки на фасаде (None - без засечки).
        x_range - только стойки и ячейки в пределах линии (частичное сечение);
        mark_ends - концы линии: засечки ставятся по ним (а не у крайних стоек)."""
        lo, hi = (min(x_range), max(x_range)) if x_range is not None else (None, None)
        pxs_show = [px for px in fac.post_x if lo is None or lo - 1 <= px <= hi + 1]
        crossed = lo is None or any(max(a, lo) < min(b, hi) - 100
                                    for a, b in zip(fac.post_x, fac.post_x[1:]))
        if not pxs_show and not crossed:
            warnings.append(f"Сечение {letter}: линия не пересекла ни одной стойки или ячейки - не построено")
            return
        x_lo, x_hi = (pxs_show[0], pxs_show[-1]) if pxs_show else (lo, hi)
        leader_bb = []
        node_cache: dict = {}
        mid = None
        for px in pxs_show:
            edge = px in (fac.post_x[0], fac.post_x[-1])
            key = (edge, post_art(px))
            if key not in node_cache:
                node_cache[key] = src.node(beam=False, edge=edge, art=key[1])
            node = node_cache[key]
            mid = mid or node
            vdir = (-1, 0) if edge and px == x_hi else (1, 0)          # сторона стекла крайнего - внутрь сечения
            anchors = put(node, R._frame((px, y0_pos), (0, -1), vdir))
            if px == x_lo:
                leader_bb = anchors
        if mid is None:                             # линия прошла только сквозь стекло
            mid = src.node(beam=False, edge=False, art=obj.стойка or card["стойка"]["артикул"])
        g, inner = mid.g_lat, y0_pos - mid.g_in                 # наружу вниз: внутренняя грань стекла выше носика
        first_bay = None
        for xl, xr in zip(fac.post_x, fac.post_x[1:]):
            if (xl, xr) in fac.floor_bays:
                continue                                     # дверь до пола - стекла в сечении нет
            if lo is not None:
                xl, xr = max(xl, lo), min(xr, hi)
                if xr - xl < 100:
                    continue
            first_bay = first_bay or (xl, xr)
            _glass_section(items, xl + g, xr - g, inner, -1, obj.формула, True, sc["рамка_глубина"], sc["рамка_от_края"], obj.заполнение)
        if first_bay:
            leader_bb.append(((first_bay[0] + first_bay[1]) / 2, inner - obj.заполнение / 2, glass_name))
        lay = layouts.get(f"{prefix}/стойка_край")
        pts = {}
        for x, y, n in leader_bb:
            pts.setdefault(n, (x, y))
        if lay:
            _leaders_by_layout(items, lay, x_lo, y0_pos, set(pts), glass_name, "", hl, pts,
                               legend=legend, fallback=(leader_bb, x_lo - 120, y0_pos - 120, True))
        elif leader_bb:
            _leaders(items, leader_bb, x_lo - 120, y0_pos + 150, hl, left=True, legend=legend)
        items.append(Item("text", SECTION_LAYER, x=(x_lo + x_hi) / 2, y=y0_pos + 250, h=hb_t,
                          text=f"{letter}-{letter}"))
        if cut_y is not None:
            if mark_ends is not None:               # по концам линии пользователя
                _cut_mark(items, mark_ends[0], cut_y, (0, 1), (-1, 0), letter, f)
                _cut_mark(items, mark_ends[1], cut_y, (0, 1), (1, 0), letter, f)
            else:
                _cut_mark(items, x_lo - half - 100, cut_y, (0, 1), (-1, 0), letter, f)
                _cut_mark(items, x_hi + half + 100, cut_y, (0, 1), (1, 0), letter, f)

    rows = sorted({gl.y0 for gl in fac.glasses})
    if rows:
        low = [gl for gl in fac.glasses if abs(gl.y0 - rows[0]) < TOL]
        _bb(y0, (rows[0] + min(gl.y1 for gl in low)) / 2, "Б", "Б-Б")
    else:
        _bb(y0, None, "Б", "Б-Б")

    # ---------- А-А: вертикальное сечение по пролёту, наружу = вправо ----------
    def _aa(bay, x0_pos: float, letter: str, prefix: str, y_range: tuple | None = None,
            mark_x: float | None = None) -> None:
        """Вертикальное сечение через пролёт bay. x0_pos - ось выносок (как у А-А).
        y_range - частичное сечение: узлы - ригели в линии, стекло - обрезано по линии.
        mark_x - где на фасаде ставить засечки (по умолчанию - центр пролёта)."""
        rn = _riegel_nose(folder / sc["блоки"], card["ригель"]["артикул"])
        nose_x = x0_pos - rn
        ys = sorted(fac.bays.get(bay, []))
        lo, hi = (min(y_range), max(y_range)) if y_range is not None else (None, None)
        ys_show = [y for y in ys if lo is None or lo - 1 <= y <= hi + 1]
        crossed = lo is None or any(max(a, lo) < min(b, hi) - 100
                                    for a, b in zip(ys, ys[1:]))
        if not ys_show and not crossed:
            warnings.append(f"Сечение {letter}: линия не пересекла пролёт - не построено")
            return
        groups: dict[float, tuple] = {}

        def b_art(y) -> str:
            """Профиль ригеля пролёта: по марке (с учётом правок таблицы профилей)."""
            return fac.beam_art.get((round(y, 3), bay[0], bay[1])) or obj.ригель \
                   or card["ригель"]["артикул"]

        # вес стекла на ригеле -> вариант узла: тяжёлый вариант только под тяжёлыми пакетами,
        # под лёгкими и без пакета - обычный (карта "подкладки_по_весу" в карточке)
        fix_wt = {}
        for where, wt, *_ in fac.fixings:
            try:
                y_s, posts = where.split()
                ia, ib = (int(p[1:]) for p in posts.split("-"))
                fix_wt[(round(float(y_s[1:])), ia, ib)] = wt
            except (ValueError, IndexError):
                continue
        def _tint(v):
            try:
                return int(float(str(v).replace(",", ".")))
            except ValueError:
                return None
        heavy_variant = next((str(r.get("Вариант")) for r in system.kits
                              if _tint(r.get("Толщина")) == obj.заполнение
                              and str(r.get("Вариант", "")).startswith("Тяжелые")), None)

        def b_variant(y):
            # под тяжёлым пакетом - тяжёлый узел (вариант витража обычный), иначе - вариант витража
            try:
                ia, ib = fac.post_x.index(bay[0]) + 1, fac.post_x.index(bay[1]) + 1
                wt = fix_wt.get((round(y), ia, ib), 0.0)
            except ValueError:
                wt = 0.0
            return heavy_variant if (_pad_heavy(wt, card) and heavy_variant) else None

        bnode_cache: dict = {}
        mid_b = None
        for y in ys_show:
            if abs(y - fac.bottom_y) < TOL:
                key, vdir = "ригель_край", (0, 1)
                gb = (True, False)
            elif abs(y - fac.top_y) < TOL:
                key, vdir = "ригель_край", (0, -1)
                gb = (True, True)
            else:
                key, vdir = "ригель_середина", (0, 1)
                gb = (False, False)
            ck = (key, gb[1], b_art(y), b_variant(y))
            if ck not in bnode_cache:
                bnode_cache[ck] = src.node(beam=True, edge=gb[0], glass_below=gb[1], art=ck[2],
                                           variant=ck[3])
            node = bnode_cache[ck]
            mid_b = mid_b or node
            anchors = put(node, R._frame((nose_x, y), (1, 0), vdir))
            if y == ys_show[0] or (len(ys_show) > 2 and y == ys_show[1]):
                groups[y] = (f"А-А/{key}", anchors)   # у всех вертикальных сечений одна раскладка выносок
        if mid_b is None:                       # линия прошла только сквозь стекло
            mid_b = src.node(beam=True, edge=False, art=obj.ригель or card["ригель"]["артикул"])
        inner = nose_x + mid_b.g_in
        glass_y = None
        for gl in fac.glasses:
            if not bay[0] - TOL <= (gl.x0 + gl.x1) / 2 <= bay[1] + TOL:
                continue
            yb, yt = gl.y0, gl.y1
            if lo is not None:
                yb, yt = max(yb, lo), min(yt, hi)
                if yt - yb < 1:
                    continue
            _glass_section(items, yb, yt, inner, +1, obj.формула, False,
                           sc["рамка_глубина"], sc["рамка_от_края"], obj.заполнение)
            if glass_y is None or yb < glass_y:
                glass_y = (yb + yt) / 2
        if glass_y is not None:
            if not groups:
                groups[glass_y] = ("А-А/ригель_середина", [])
            groups[min(groups)][1].append((inner + obj.заполнение / 2, glass_y, glass_name))
        with_layout = [(y, layouts[lay_key], anchors) for y, (lay_key, anchors) in groups.items()
                       if lay_key in layouts]
        loose = [a for y, (lay_key, anchors) in groups.items() if lay_key not in layouts for a in anchors]
        for index, (y, lay, anchors) in enumerate(with_layout):
            pts = {}
            for ax, ay, n in anchors:
                pts.setdefault(n, (ax, ay))
            all_anchors = anchors + (loose if index == 0 else [])
            _leaders_by_layout(items, lay, x0_pos, y, set(pts), glass_name, "", hl, pts,
                               legend=legend, fallback=(all_anchors, x0_pos + 160, y - 120, False))
        if not with_layout and loose:
            _leaders(items, loose, x0_pos + 160, max(groups) + 7 * hl, hl, left=False, legend=legend)
        y_title = max(ys_show) if ys_show else hi
        items.append(Item("text", SECTION_LAYER, x=x0_pos + 30, y=y_title + 250, h=hb_t,
                          text=f"{letter}-{letter}"))
        cx = (bay[0] + bay[1]) / 2 if mark_x is None else mark_x
        if y_range is not None:                     # по концам линии пользователя
            _cut_mark(items, cx, y_range[1], (1, 0), (0, 1), letter, f)
            _cut_mark(items, cx, y_range[0], (1, 0), (0, -1), letter, f)
        else:
            _cut_mark(items, cx, y_top + 100, (1, 0), (0, 1), letter, f)
            _cut_mark(items, cx, floor - 100, (1, 0), (0, -1), letter, f)

    bay = next(((xl, xr) for xl, xr in zip(fac.post_x, fac.post_x[1:]) if (xl, xr) not in fac.floor_bays), None)
    if bay:
        _aa(bay, right + half + sc["отступ_сечения"] + 2 * hb, "А", "А-А")

    # ---------- добавленные сечения: линии команды ДОБАВИТЬСЕЧЕНИЕ (хранятся в чертеже) ----------
    LETTERS = "АБВГДЕЖЗИКЛМНОПРСТУФХЦЧШЩЭЮЯ"      # без Ё, Й, Ъ, Ь, Ы
    step = sc.get("шаг_доп_сечений", 1000)          # шаг размещения В-В, Г-Г… (в карточке можно менять)
    v_i, h_i = 0, 0
    for k, (x1, y1, x2, y2) in enumerate(fac.extra_sections):
        letter = LETTERS[2 + k] if 2 + k < len(LETTERS) else "?"
        if abs(x2 - x1) < abs(y2 - y1):                # вертикальная линия -> вертикальное сечение пролёта
            xc = (x1 + x2) / 2
            bays = [(xl, xr) for xl, xr in zip(fac.post_x, fac.post_x[1:])
                    if xl - 1 <= xc <= xr + 1 and (xl, xr) not in fac.floor_bays]
            if not bays:
                warnings.append(f"Сечение {letter}-{letter}: линия не попала в пролёт без двери - пропуск")
                continue
            # ближайший к линии пролёт; линия точно на стойке - правый пролёт
            bays.sort(key=lambda b: (abs((b[0] + b[1]) / 2 - xc), -b[0]))
            v_i += 1
            _aa(bays[0], right + half + sc["отступ_сечения"] + 2 * hb + v_i * step, letter, letter,
                y_range=(min(y1, y2), max(y1, y2)),
                mark_x=min(max(xc, left), right))
        else:                                          # горизонтальная -> сечение по стойкам на высоте линии
            yc = (y1 + y2) / 2
            h_i += 1
            _bb(y0 - h_i * step, yc if fac.glasses else None, letter, letter,
                x_range=(min(x1, x2), max(x1, x2)), mark_ends=(min(x1, x2), max(x1, x2)))
    items += legend.items(left - 40 * hl, y0 + 16 * hl, hl)     # легенда - слева от первого сечения Б-Б
    return items, warnings


def _sections_catalog(fac: Facade, system: System, obj: ObjectParams, folder: Path
                      ) -> tuple[list[Item], list[str], set[str]]:
    """Старый путь: узлы из базы каталога (лист 03) - если библиотеки узлов нет."""
    card = system.card
    sc, f = card["сечение"], card["оформление"]
    M = f["масштаб"]
    warnings = []
    used_blocks: set[str] = set()
    with open(folder / sc["узлы"], encoding="utf-8") as fp:
        nodes = json.load(fp)
    tpl = nodes["узлы"]
    # узлы из базы каталога (лист 03) для этой толщины и варианта - вместо образца с "Песочного"
    cat_nodes, cat_lib = load_base(folder)
    cat = cat_nodes.get(obj.вариант, {}).get(str(obj.заполнение))
    glass_post = -sc["стойка_стекло_от_базы"]           # кромка стекла от носика стойки, мм (внутрь)
    if cat:
        nose = _riegel_nose(folder / sc["блоки"], tpl["ригель_середина"]["профиль"])
        fillers = [a for a, _, _ in parse_items(fac.perimeter.get("Крайняя стойка", "") + " + " +
                                                fac.perimeter.get("Крайний ригель", ""))]
        new = {}
        old_lib = block_polylines(folder / sc["блоки"])
        # доборные периметра из каталога (стр. 73-79); выбор из пары "Т50-04 (КПС 296)" - по исходным данным
        perim_all = load_perimeter(folder)
        perim = perim_all.get(f"{obj.заполнение}/{obj.вариант_периметра}", {})
        drawn_as = {p["артикул"]: (p["блок"], p["зеркало"]) for sides in perim_all.values()
                    for d in sides.values() for p in d["детали"]}
        pick = lambda art, alt: pick_filler(art, alt, obj.доборные)[0]
        for key, old in tpl.items():
            mid = tpl["ригель_середина"] if key.startswith("ригель") else tpl["стойка_середина"]
            side = perim.get("ригель" if key.startswith("ригель") else "стойка")
            new[key], notes = node_template(key, cat, old, nose, fac.kit, fillers, old_lib, mid, side, pick, drawn_as)
            warnings += [f"Сечение: {n}" for n in notes if f"Сечение: {n}" not in warnings]
        tpl = new
        if cat.get("стекло_внутр") is not None:
            glass_post = -cat["стекло_внутр"]
        used_blocks.update(d["блок"] for t in tpl.values() for d in t["детали"] if d.get("каталог"))
    elif nodes.get("заполнение") != obj.заполнение or nodes.get("вариант") != obj.вариант:
        warnings.append(f"Сечение: в базе нет узла {obj.вариант} / {obj.заполнение} мм - сечение нарисовано по образцу "
                        f"{nodes.get('вариант')} / {nodes.get('заполнение')} мм и НЕВЕРНО")
    lay_file = folder / sc.get("выноски", "-")
    layouts = json.loads(lay_file.read_text(encoding="utf-8"))["узлы"] if lay_file.is_file() else {}
    sh_file = folder / sc.get("контуры", "-")
    shapes = json.loads(sh_file.read_text(encoding="utf-8")) if sh_file.is_file() else {}
    items: list[Item] = []
    g = card["стекло"]["от_оси"]
    half = card["система"]["ширина_профиля"] / 2
    hl = f["высота_выноски"] * f.get("масштаб_сечений", M)
    hb = f["высота_буквы_разреза"] * M
    glass_name = f"Стеклопакет {obj.формула}" if obj.формула else f"Заполнение {obj.заполнение} мм"
    pk = card["подкладки"]
    setting = parse_items(fac.kit.get("Подкладка опорная", ""))
    setting_name = fac.kit.get("Подкладка опорная", "")
    legend = _Legend(_names_map(fac, system))       # номера выносок общие для всех сечений витража
    setting_shape = (shapes.get(f"{setting[0][0]}/{card['ригель']['артикул']}/сечение", {}).get("точки")
                     if setting else None)

    # ---------- Б-Б: горизонтальное сечение, наружу = вниз ----------
    floor = min(lo for lo, hi in fac.post_ends.values())
    y0 = floor - sc["отступ_сечения"] - 2 * hb
    left, right = fac.post_x[0], fac.post_x[-1]
    leader_bb, bb_base = [], None
    for px in fac.post_x:
        if px in (left, right):
            key = "стойка_край"
            base = _orient(tpl[key], (0, -1), (-1, 0) if px == left else (1, 0), px, y0)
        else:
            key = "стойка_середина"
            base = _orient(tpl[key], (0, -1), None, px, y0)
        places = node_places(tpl[key], base)
        items += [Item("insert", SECTION_LAYER, place=p) for p in places]
        if px == left:
            leader_bb += [(p.x, p.y, _label(p.name)) for p in places]
            bb_base = base
    inner = y0 + glass_post                            # наружу = вниз: кромка стекла выше носика
    first_bay = None
    for xl, xr in zip(fac.post_x, fac.post_x[1:]):
        if (xl, xr) in fac.floor_bays:
            continue                                     # дверь до пола - стекла в сечении нет
        first_bay = first_bay or (xl, xr)
        _glass_section(items, xl + g, xr - g, inner, -1, obj.формула, True, sc["рамка_глубина"], sc["рамка_от_края"], obj.заполнение)
    if first_bay:
        leader_bb.append((first_bay[0] + 300, inner - obj.заполнение / 2, glass_name))
    lay = layouts.get("Б-Б/стойка_край")
    if lay and bb_base:
        _leaders_by_layout(items, lay, bb_base.x, bb_base.y, {n for _, _, n in leader_bb}, glass_name, "", hl,
                           legend=legend, fallback=(leader_bb, left - 120, y0 - 120, True))
    elif leader_bb:
        _leaders(items, leader_bb, left - 120, y0 + 150, hl, left=True, legend=legend)
    items.append(Item("text", SECTION_LAYER, x=(left + right) / 2, y=y0 + 250, h=hb, text="Б-Б"))

    # Б-Б на фасаде: середина нижнего ряда стёкол ПО ВСЕМ пролётам (разрез проходит через стекло
    # в каждом пролёте и не попадает на отметки ригелей), взгляд вверх (по образцу пользователя)
    rows = sorted({gl.y0 for gl in fac.glasses})
    if rows:
        low = [gl for gl in fac.glasses if abs(gl.y0 - rows[0]) < TOL]
        cut_y = (rows[0] + min(gl.y1 for gl in low)) / 2
        _cut_mark(items, left - half - 100, cut_y, (0, 1), (-1, 0), "Б", f)
        _cut_mark(items, right + half + 100, cut_y, (0, 1), (1, 0), "Б", f)

    # ---------- А-А: вертикальное сечение по первому пролёту без двери, наружу = вправо ----------
    bay = next(((xl, xr) for xl, xr in zip(fac.post_x, fac.post_x[1:]) if (xl, xr) not in fac.floor_bays), None)
    if bay:
        x0 = right + half + sc["отступ_сечения"] + 2 * hb
        ys = sorted(fac.bays.get(bay, []))
        groups: dict[float, tuple] = {}            # отметка ригеля -> (вид узла, база, точки выносок)
        for y in ys:
            if abs(y - fac.bottom_y) < TOL:
                key, base = "ригель_край", _orient(tpl["ригель_край"], (1, 0), (0, -1), x0, y)
            elif abs(y - fac.top_y) < TOL:
                key, base = "ригель_край", _orient(tpl["ригель_край"], (1, 0), (0, 1), x0, y)
            else:
                key, base = "ригель_середина", _orient(tpl["ригель_середина"], (1, 0), None, x0, y)
            places = node_places(tpl[key], base)
            items += [Item("insert", SECTION_LAYER, place=p) for p in places]
            if y == ys[0] or (len(ys) > 2 and y == ys[1]):
                groups[y] = (f"А-А/{key}", base, [(p.x, p.y, _label(p.name)) for p in places])
        # носик ригеля (в системе блока x = nose) смотрит наружу (вправо): кромка стекла - glass_post внутрь от носика
        inner = x0 - _riegel_nose(folder / sc["блоки"], tpl["ригель_середина"]["профиль"]) - glass_post
        for yb, yt in zip(ys, ys[1:]):
            _glass_section(items, yb + g, yt - g, inner, +1, obj.формула, False, sc["рамка_глубина"], sc["рамка_от_края"], obj.заполнение)
            pad = block_name(setting[0][0]) if setting else ""
            if pad in cat_lib:
                # блок опорной подкладки из каталога: борт - на LIP_FROM_NOSE внутрь от носика ригеля,
                # полка - на толщину резины ниже стекла (снято с вставки пользователя КП45391)
                nose_w = x0 - _riegel_nose(folder / sc["блоки"], tpl["ригель_середина"]["профиль"])
                py = yb + g - RUBBER - bearing_y(cat_lib[pad])
                items.append(Item("insert", SETTING_LAYER, place=Place(pad, nose_w - LIP_FROM_NOSE, py, 0.0, False)))
                used_blocks.add(pad)
            elif setting_shape:
                # контур опорной подкладки снят с правки пользователя (от точки вставки ригеля)
                items.append(Item("poly", SETTING_LAYER, pts=[(x0 + px, yb + py, b) for px, py, b in setting_shape]))
            elif setting:
                # условно: полка под стеклом, борт - вверх вдоль стекла со стороны помещения
                t, lip = pk["толщина_в_сечении"], pk["борт"]
                w = obj.заполнение + 6                     # под всю толщину стекла (+ борт и запас)
                y_top, x_in = yb + g, inner - 3
                items.append(Item("poly", SETTING_LAYER, pts=[
                    (x_in, y_top - t), (x_in + w, y_top - t), (x_in + w, y_top), (x_in + 2, y_top),
                    (x_in + 2, y_top + lip), (x_in, y_top + lip)]))
            if setting and yb in groups:
                groups[yb][2].append((inner + pk["ширина_в_сечении"] / 2, yb + g - 2, setting_name))
        if len(ys) > 1 and ys[0] in groups:
            groups[ys[0]][2].append((inner + obj.заполнение / 2, ys[0] + 300, glass_name))
        with_layout = [(y, nb, layouts[lay_key], anchors) for y, (lay_key, nb, anchors) in groups.items()
                       if lay_key in layouts]
        loose = [a for y, (lay_key, nb, anchors) in groups.items() if lay_key not in layouts for a in anchors]
        for index, (y, nb, lay, anchors) in enumerate(with_layout):
            all_anchors = anchors + (loose if index == 0 else [])
            _leaders_by_layout(items, lay, nb.x, nb.y, {n for _, _, n in anchors}, glass_name,
                               setting_name, hl, legend=legend,
                               fallback=(all_anchors, x0 + 160, y - 120, False))
        if not with_layout and loose:
            _leaders(items, loose, x0 + 160, max(groups) + 7 * hl, hl, left=False, legend=legend)
        items.append(Item("text", SECTION_LAYER, x=x0 + 30, y=max(ys) + 250, h=hb, text="А-А"))
        # А-А на фасаде: посередине пролёта, взгляд вправо (по образцу пользователя)
        cx = (bay[0] + bay[1]) / 2
        y_top = max(hi for lo, hi in fac.post_ends.values())
        _cut_mark(items, cx, y_top + 100, (1, 0), (0, 1), "А", f)
        _cut_mark(items, cx, floor - 100, (1, 0), (0, -1), "А", f)
    items += legend.items(left - 40 * hl, y0 + 16 * hl, hl)       # легенда - слева от сечения Б-Б
    return items, warnings, used_blocks


def build_on_facade(fac: Facade, system: System, obj: ObjectParams, folder: Path,
                    lib=None) -> list[Item]:
    """Закладные ригелей, закладные стоек, платики, подкладки - на фасаде.

    Ригельная закладная - блок своего семейства из библиотеки (точка вставки (0,0) - в плоскости
    стойки по оси ригеля, X - вдоль ригеля; правый конец - зеркалом); если блока нет - контур
    из _контуры.json, затем условный прямоугольник + замечание."""
    card = system.card
    zc = card["закладные_на_фасаде"]
    half = card["система"]["ширина_профиля"] / 2
    f = card["оформление"]
    items: list[Item] = []

    shapes = {}
    sh_file = folder / f"{Path(card['сечение']['узлы']).stem.split('_')[0]}_контуры.json"
    if sh_file.exists():
        shapes = json.loads(sh_file.read_text(encoding="utf-8"))
    L, H = zc["ригель_длина"], zc["ригель_высота"] / 2
    no_shape: set[str] = set()

    def insert_form(code: str, b_art: str):
        """Чем рисовать закладную: ('block', имя) / ('poly', точки) / (None, None) - прямоугольник."""
        family = str(code).split("-")[0].split("_")[0]
        if "-" not in str(code):        # закладная не подобрана - есть отдельное замечание
            return None, None
        if lib is not None and lib.has(family):
            return "block", family
        for key in (f"{family}/{b_art}", f"{family}/{card['ригель']['артикул']}"):
            pts = shapes.get(key, {}).get("точки")
            if pts:
                return "poly", pts
        no_shape.add(f"{family} (ригель {b_art})")
        return None, None

    for y, xl, xr, code, b_art in fac.beam_spans:
        layer = INSERT_LAYER_PREFIX + _layer_safe(code)
        kind, data = insert_form(code, b_art)
        for x_face, d in ((xl + half, 1), (xr - half, -1)):
            if kind == "block":
                items.append(Item("insert", layer, place=Place(data, x_face, y, 0.0, d < 0)))
            elif kind == "poly":
                pts = [(x_face + d * px, y + py, b * d) for px, py, b in data]
                items.append(Item("poly", layer, pts=pts))
            else:
                x1 = x_face + d * L
                pts = _rect(min(x_face, x1), y - H, max(x_face, x1), y + H)
                items.append(Item("poly", layer, pts=pts))
        items.append(Item("text", layer, x=(xl + xr) / 2, y=y - half - 45, h=f["высота_выноски"] * f["масштаб"] * 0.6,
                          text=code))
    if no_shape:
        fac.warnings.append("Закладные: контур не задан - " + "; ".join(sorted(no_shape)) +
                            " - на фасаде условное изображение (прямоугольник); нарисуйте блок закладной"
                            " в библиотеке или запишите контур в _контуры.json ключ «СЕМЕЙСТВО/артикул ригеля»")

    zs = card["закладная_стойки"]
    plat = card["монтаж"]["в_проем_платик"].split(" ")[0]
    for end, Lz in (("низ", obj.закладная_стойки_низ), ("верх", obj.закладная_стойки_верх)):
        Lz = zs["длина_в_проем"] if Lz is None else Lz
        if not Lz:
            continue
        w = zc["стойка_ширина"] / 2
        for px, (lo, hi) in fac.post_ends.items():
            a, b = (lo, lo + Lz) if end == "низ" else (hi - Lz, hi)
            items.append(Item("poly", INSERT_LAYER_PREFIX + _layer_safe(zs["артикул"]), pts=_rect(px - w, a, px + w, b)))
            if obj.монтаж == "в проем":
                t = zc["платик_толщина"]
                a, b = (lo - t, lo) if end == "низ" else (hi, hi + t)
                items.append(Item("poly", INSERT_LAYER_PREFIX + _layer_safe(plat), pts=_rect(px - half, a, px + half, b)))

    # подкладки: опорные под каждым стеклом, фиксирующие на стойках
    pk = card["подкладки"]
    has_fix = bool(fac.kit.get("Подкладка фиксир.", "").strip())
    t = pk["толщина_на_чертеже"]
    for gl in fac.glasses:
        xl, xr = gl.x0 - card["стекло"]["от_оси"] + half, gl.x1 + card["стекло"]["от_оси"] - half   # грани стоек
        for a in (xl + pk["от_стойки"], xr - pk["от_стойки"] - pk["длина"]):
            items.append(Item("poly", FACADE_SETTING_LAYER, pts=_rect(a, gl.y0 - t, a + pk["длина"], gl.y0)))
        if has_fix:
            for x_edge, d in ((gl.x0, -1), (gl.x1, 1)):
                b0 = gl.y0 + pk["от_стойки"]
                items.append(Item("poly", FACADE_SETTING_LAYER,
                                  pts=_rect(min(x_edge, x_edge + d * t), b0, max(x_edge, x_edge + d * t), b0 + pk["длина"])))
    return items


def layer_color(layer: str, card: dict) -> int:
    zc = card["закладные_на_фасаде"]
    if layer.startswith(INSERT_LAYER_PREFIX):
        code = layer[len(INSERT_LAYER_PREFIX):]
        for suf, key in (("-бс", "цвет_бс"), ("-с", "цвет_с"), ("-б", "цвет_б")):
            if code.endswith(suf):
                return zc[key]
        return 4
    return {SECTION_LAYER: 7, GLASS_LAYER: 5, CUT_LAYER: 7, LEADER_LAYER: 8,
            SETTING_LAYER: 30, FACADE_SETTING_LAYER: 30}.get(layer, 7)
