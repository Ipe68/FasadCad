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
from .facade import TOL, Facade, ObjectParams, System, formula_layers, parse_items, pick_filler
from .nodes import Place, node_places
from .profiles import block_polylines

SECTION_LAYER = "MARK_СЕЧЕНИЕ"
GLASS_LAYER = "MARK_СЕЧЕНИЕ_СТЕКЛО"
CUT_LAYER = "MARK_РАЗРЕЗ"
LEADER_LAYER = "MARK_ВЫНОСКИ"
SETTING_LAYER = "MARK_ПОДКЛАДКИ"
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


def _leaders(items, anchors: list[tuple[float, float, str]], col_x: float, top_y: float, h: float, left: bool):
    """Выноски: от точки на детали - к подписи в столбик (без повторов)."""
    seen, row = set(), 0
    for ax, ay, text in sorted(anchors, key=lambda a: -a[1]):
        if text in seen:
            continue
        seen.add(text)
        ty = top_y - row * h * 1.8
        row += 1
        shelf = len(text) * h * 0.7
        x_end = col_x - shelf if left else col_x + shelf
        items.append(Item("poly", LEADER_LAYER, pts=[(ax, ay), (col_x, ty), (x_end, ty)], closed=False))
        items.append(Item("poly", LEADER_LAYER, pts=_rect(ax - h * 0.12, ay - h * 0.12, ax + h * 0.12, ay + h * 0.12)))
        items.append(Item("text", LEADER_LAYER, x=min(col_x, x_end), y=ty + h * 0.35, h=h, text=text, align="left"))


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
    hb = f["высота_буквы_разреза"] * M
    if vx == 0:      # взгляд по вертикали (Б-Б): буква сбоку от хвоста, наружу от витража
        cx, cy = tail[0] + sx * hb * 0.6, tail[1] + hb * 0.3
    else:            # взгляд по горизонтали (А-А): буква за хвостом
        cx, cy = tail[0] - vx * hb * 0.6, tail[1]
    items.append(Item("text", CUT_LAYER, x=cx, y=cy, h=hb, text=letter))


def _leaders_by_layout(items, layout: list, bx: float, by: float, names: set, glass_name: str,
                       setting_name: str, h: float, points: dict | None = None) -> set:
    """Выноски по раскладке пользователя (смещения от точки вставки профиля узла).
    Возвращает подписанные тексты; детали, которых нет в раскладке, подписываются отдельно."""
    done = set()
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
        k = (bx + e["перелом"][0], by + e["перелом"][1])
        s_y = by + e["полка"][1]
        if text == e["текст"]:
            # тот же текст, что в образце - полка и надпись ровно как у пользователя
            s_end = (bx + e["полка"][0], s_y)
            tx, ty = bx + e["текст_в"][0], by + e["текст_в"][1]
        else:
            shelf = len(text) * h * 0.7
            to_left = e["полка"][0] < e["перелом"][0]
            s_end = (k[0] - shelf, s_y) if to_left else (k[0] + shelf, s_y)
            tx, ty = min(k[0], s_end[0]), s_y + h * 0.35
        items.append(Item("poly", LEADER_LAYER, pts=[a, (k[0], s_y), s_end], closed=False))
        items.append(Item("poly", LEADER_LAYER, pts=_rect(a[0] - h * 0.12, a[1] - h * 0.12, a[0] + h * 0.12, a[1] + h * 0.12)))
        items.append(Item("text", LEADER_LAYER, x=tx, y=ty, h=h, text=text, align="left"))
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
    glass_name = f"Стеклопакет {obj.формула}" if obj.формула else f"Заполнение {obj.заполнение} мм"

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
    leader_bb = []
    mid = src.node(beam=False, edge=False)
    for px in fac.post_x:
        edge = px in (left, right)
        node = src.node(beam=False, edge=True) if edge else mid
        vdir = (-1, 0) if edge and px == right else (1, 0)          # сторона стекла крайнего - внутрь витража
        anchors = put(node, R._frame((px, y0), (0, -1), vdir))
        if px == left:
            leader_bb = anchors
    g, inner = mid.g_lat, y0 - mid.g_in                 # наружу вниз: внутренняя грань стекла выше носика
    first_bay = None
    for xl, xr in zip(fac.post_x, fac.post_x[1:]):
        if (xl, xr) in fac.floor_bays:
            continue                                     # дверь до пола - стекла в сечении нет
        first_bay = first_bay or (xl, xr)
        _glass_section(items, xl + g, xr - g, inner, -1, obj.формула, True, sc["рамка_глубина"], sc["рамка_от_края"], obj.заполнение)
    if first_bay:
        leader_bb.append((first_bay[0] + 300, inner - obj.заполнение / 2, glass_name))
    lay = layouts.get("Б-Б/стойка_край")
    pts = {}
    for x, y, n in leader_bb:
        pts.setdefault(n, (x, y))
    done = _leaders_by_layout(items, lay, left, y0, set(pts), glass_name, "", hl, pts) if lay else set()
    rest = [a for a in leader_bb if a[2] not in done]
    if rest:
        _leaders(items, rest, left - 120, y0 - 120 if done else y0 + 150, hl, left=True)
    items.append(Item("text", SECTION_LAYER, x=(left + right) / 2, y=y0 + 250, h=hb, text="Б-Б"))
    rows = sorted({gl.y0 for gl in fac.glasses})
    if rows:
        low = [gl for gl in fac.glasses if abs(gl.y0 - rows[0]) < TOL]
        cut_y = (rows[0] + min(gl.y1 for gl in low)) / 2
        _cut_mark(items, left - half - 100, cut_y, (0, 1), (-1, 0), "Б", f)
        _cut_mark(items, right + half + 100, cut_y, (0, 1), (1, 0), "Б", f)

    # ---------- А-А: вертикальное сечение по первому пролёту без двери, наружу = вправо ----------
    bay = next(((xl, xr) for xl, xr in zip(fac.post_x, fac.post_x[1:]) if (xl, xr) not in fac.floor_bays), None)
    if bay:
        x0 = right + half + sc["отступ_сечения"] + 2 * hb              # как раньше - от неё раскладка выносок
        rn = _riegel_nose(folder / sc["блоки"], card["ригель"]["артикул"])
        nose_x = x0 - rn
        ys = sorted(fac.bays.get(bay, []))
        groups: dict[float, tuple] = {}
        mid_b = None
        for y in ys:
            if abs(y - fac.bottom_y) < TOL:
                key, node, vdir = "ригель_край", src.node(beam=True, edge=True), (0, 1)
            elif abs(y - fac.top_y) < TOL:
                key, node, vdir = "ригель_край", src.node(beam=True, edge=True, glass_below=True), (0, -1)
            else:
                key, node, vdir = "ригель_середина", src.node(beam=True, edge=False), (0, 1)
            mid_b = mid_b or node
            anchors = put(node, R._frame((nose_x, y), (1, 0), vdir))
            if y == ys[0] or (len(ys) > 2 and y == ys[1]):
                groups[y] = (f"А-А/{key}", anchors)
        inner = nose_x + mid_b.g_in
        g = mid_b.g_lat
        for yb, yt in zip(ys, ys[1:]):
            _glass_section(items, yb + g, yt - g, inner, +1, obj.формула, False, sc["рамка_глубина"], sc["рамка_от_края"], obj.заполнение)
        if len(ys) > 1 and ys[0] in groups:
            groups[ys[0]][1].append((inner + obj.заполнение / 2, ys[0] + 300, glass_name))
        for y, (lay_key, anchors) in groups.items():
            lay = layouts.get(lay_key)
            pts = {}
            for ax, ay, n in anchors:
                pts.setdefault(n, (ax, ay))
            done = _leaders_by_layout(items, lay, x0, y, set(pts), glass_name, "", hl, pts) if lay else set()
            rest = [a for a in anchors if a[2] not in done]
            if rest:
                _leaders(items, rest, x0 + 160, y - 120 if done else y + 7 * hl, hl, left=False)
        items.append(Item("text", SECTION_LAYER, x=x0 + 30, y=max(ys) + 250, h=hb, text="А-А"))
        cx = (bay[0] + bay[1]) / 2
        y_top = max(hi for lo, hi in fac.post_ends.values())
        _cut_mark(items, cx, y_top + 100, (1, 0), (0, 1), "А", f)
        _cut_mark(items, cx, floor - 100, (1, 0), (0, -1), "А", f)
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
    done = (_leaders_by_layout(items, lay, bb_base.x, bb_base.y, {n for _, _, n in leader_bb}, glass_name, "", hl)
            if lay and bb_base else set())
    rest = [a for a in leader_bb if a[2] not in done]
    if rest:
        _leaders(items, rest, left - 120, y0 - 120 if done else y0 + 150, hl, left=True)
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
        for y, (lay_key, nb, anchors) in groups.items():
            lay = layouts.get(lay_key)
            done = (_leaders_by_layout(items, lay, nb.x, nb.y, {n for _, _, n in anchors}, glass_name, setting_name, hl)
                    if lay else set())
            rest = [a for a in anchors if a[2] not in done]
            if rest:
                _leaders(items, rest, x0 + 160, y - 120 if done else y + 7 * hl, hl, left=False)
        items.append(Item("text", SECTION_LAYER, x=x0 + 30, y=max(ys) + 250, h=hb, text="А-А"))
        # А-А на фасаде: посередине пролёта, взгляд вправо (по образцу пользователя)
        cx = (bay[0] + bay[1]) / 2
        y_top = max(hi for lo, hi in fac.post_ends.values())
        _cut_mark(items, cx, y_top + 100, (1, 0), (0, 1), "А", f)
        _cut_mark(items, cx, floor - 100, (1, 0), (0, -1), "А", f)
    return items, warnings, used_blocks


def build_on_facade(fac: Facade, system: System, obj: ObjectParams, folder: Path) -> list[Item]:
    """Закладные ригелей (живой контур), закладные стоек, платики, подкладки - на фасаде."""
    card = system.card
    zc = card["закладные_на_фасаде"]
    half = card["система"]["ширина_профиля"] / 2
    f = card["оформление"]
    items: list[Item] = []

    shapes = {}
    sh_file = folder / f"{Path(card['сечение']['узлы']).stem.split('_')[0]}_контуры.json"
    if sh_file.exists():
        shapes = json.loads(sh_file.read_text(encoding="utf-8"))
    shape = shapes.get(f"КП1336/{card['ригель']['артикул']}", {}).get("точки")
    L, H = zc["ригель_длина"], zc["ригель_высота"] / 2
    for y, xl, xr, code in fac.beam_spans:
        layer = INSERT_LAYER_PREFIX + _layer_safe(code)
        for x_face, d in ((xl + half, 1), (xr - half, -1)):
            if shape:
                # зеркальный конец: x -> -x, дуги меняют знак; обход в обратную сторону не нужен для замкнутой
                pts = [(x_face + d * px, y + py, b * d) for px, py, b in shape]
            else:
                x1 = x_face + d * L
                pts = _rect(min(x_face, x1), y - H, max(x_face, x1), y + H)
            items.append(Item("poly", layer, pts=pts))
        items.append(Item("text", layer, x=(xl + xr) / 2, y=y - half - 45, h=f["высота_выноски"] * f["масштаб"] * 0.6,
                          text=code))

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
            items.append(Item("poly", SETTING_LAYER, pts=_rect(a, gl.y0 - t, a + pk["длина"], gl.y0)))
        if has_fix:
            for x_edge, d in ((gl.x0, -1), (gl.x1, 1)):
                b0 = gl.y0 + pk["от_стойки"]
                items.append(Item("poly", SETTING_LAYER,
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
    return {SECTION_LAYER: 7, GLASS_LAYER: 5, CUT_LAYER: 7, LEADER_LAYER: 8, SETTING_LAYER: 30}.get(layer, 7)
