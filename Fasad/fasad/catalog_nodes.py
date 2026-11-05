"""Сборка базы узлов из каталога (03 "Основные сечения", сохранён в DXF) - без AutoCAD.

Узел каталога - "пакет" над стойкой для одной толщины заполнения: крышка, прижим, термовставка,
уплотнители, фиксирующие подкладки, винт; стойка показана с разрывом. Масштаб листа 1:1,6.
Детали узнаются по слою и положению, артикулы - по таблице комплектации (толщина + вариант).

Результат:
  деталь  - контуры 1:1 (внешний + отверстия), точка вставки - низ-середина габарита;
  узел    - список (артикул, dx, dy) от носика стойки на оси (как точка вставки блока стойки),
            наружу = +Y (к прижиму).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import ezdxf
from ezdxf import bbox

SCALE = 1.6                     # 1 единица листа = 1,6 мм (размер "32" на листе = 20)


@dataclass
class Shape:
    layer: str
    pts: list                   # [(x, y, bulge)] в мм, координаты узла
    closed: bool
    box: tuple                  # x0, y0, x1, y1
    group: str = ""             # из одной вставки блока каталога (термовставка Adifoam) - одна деталь


@dataclass
class Part:
    role: str                   # крышка / прижим / термовставка / уплотнитель_прижима / уплотнитель_стойки / ...
    article: str
    shapes: list = field(default_factory=list)

    @property
    def box(self):
        xs = [s.box[0] for s in self.shapes] + [s.box[2] for s in self.shapes]
        ys = [s.box[1] for s in self.shapes] + [s.box[3] for s in self.shapes]
        return min(xs), min(ys), max(xs), max(ys)


def _shapes(msp, x0, y0, x1, y1) -> tuple[list[Shape], list, list]:
    """Полилинии окна (в мм), линии оси, штриховки (габариты)."""
    shapes, axis, hatches = [], [], []

    def add_poly(layer, pts_sheet, closed, group=""):
        pts = [(x * SCALE, y * SCALE, bl) for x, y, bl in pts_sheet]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        shapes.append(Shape(layer, pts, closed, (min(xs), min(ys), max(xs), max(ys)), group))

    for e in msp:
        t = e.dxftype()
        if t not in ("LWPOLYLINE", "LINE", "HATCH", "INSERT") or t == "INSERT" and e.dxf.name.startswith("*D"):
            continue
        try:
            b = bbox.extents([e])
        except Exception:
            continue
        # деталь узла - если середина её габарита в окне (стойка толстых узлов выходит за окно)
        if not b.has_data:
            continue
        mx, my = (b.extmin.x + b.extmax.x) / 2, (b.extmin.y + b.extmax.y) / 2
        if not (x0 <= mx <= x1 and y0 <= my <= y1) or b.size.x > (x1 - x0) * 1.6:
            continue
        box = (b.extmin.x * SCALE, b.extmin.y * SCALE, b.extmax.x * SCALE, b.extmax.y * SCALE)
        if t == "INSERT":
            # деталь, вставленная блоком (термовставка Adifoam 530.121 - блок "8"): раскрыть, сплайны - ломаной
            for v in e.virtual_entities():
                vt = v.dxftype()
                if vt == "LWPOLYLINE":
                    add_poly(v.dxf.layer, [(x, y, bl) for x, y, _, _, bl in v.get_points("xyseb")], bool(v.closed),
                             e.dxf.handle)
                elif vt in ("SPLINE", "ARC"):
                    pts = [(p.x, p.y, 0.0) for p in v.flattening(0.02)]
                    if len(pts) > 1:
                        add_poly(v.dxf.layer, pts, False, e.dxf.handle)
            continue
        if t == "LINE":
            if e.dxf.layer == "1":
                axis.append(box)
        elif t == "HATCH":
            hatches.append(box)
        else:
            add_poly(e.dxf.layer, [(x, y, bl) for x, y, _, _, bl in e.get_points("xyseb")], bool(e.closed))
    return shapes, axis, hatches


def _inside(a, b, tol=0.05) -> bool:
    return a[0] >= b[0] - tol and a[1] >= b[1] - tol and a[2] <= b[2] + tol and a[3] <= b[3] + tol


def _w(b):
    return b[2] - b[0]


def _h(b):
    return b[3] - b[1]


def parse_post_node(msp, region, kit: dict, seals: dict) -> tuple[dict, list[Part], list[str]]:
    """Узел стойки в окне region (координаты листа). Возвращает (база: ось/носик/наружу, детали, замечания)."""
    shapes, axis, hatches = _shapes(msp, *region)
    notes = []
    # стойка: самая "длинная" незамкнутая на слое 0
    post = max((s for s in shapes if s.layer == "0" and not s.closed), key=lambda s: len(s.pts), default=None)
    if not post or not axis:
        raise ValueError("не найдены стойка или ось")
    ax = (axis[0][0] + axis[0][2]) / 2
    # крышка - самая дальняя от стойки широкая замкнутая деталь слоя 0, прижим - следующая
    pc = (post.box[1] + post.box[3]) / 2
    wide = sorted((s for s in shapes if s.layer == "0" and s.closed and _w(s.box) > 30),
                  key=lambda s: -abs((s.box[1] + s.box[3]) / 2 - pc))
    cover, clamp = wide[:1], wide[1:2]
    if not cover:
        raise ValueError("не найдена крышка")
    if not clamp:
        # у толстых узлов прижим чуть выходит за окно - ищем в окне побольше, но только рядом с крышкой
        x0, y0, x1, y1 = region
        more, _, _ = _shapes(msp, x0, y0 - 12, x1, y1 + 12)
        cc = (cover[0].box[1] + cover[0].box[3]) / 2
        near = [s for s in more if s.layer == "0" and s.closed and 30 < _w(s.box) < _w(cover[0].box) - 0.5
                and abs((s.box[1] + s.box[3]) / 2 - cc) < 15 and abs((s.box[0] + s.box[2]) / 2 - ax) < 2]
        if near:
            shapes.extend(near)
            clamp = near[:1]
    # наружу - от стойки к крышке
    up = 1 if (cover[0].box[1] + cover[0].box[3]) > (post.box[1] + post.box[3]) else -1
    tip = post.box[3] if up == 1 else post.box[1]          # носик стойки - край со стороны крышки
    base = {"ось": ax, "носик": tip, "наружу": up}

    def to_node(s: Shape) -> Shape:
        """В систему узла: x от оси, y от носика, наружу = +Y."""
        # при отражении по Y дуги меняют направление - знак bulge меняется
        pts = [((x - ax), (y - tip) if up == 1 else (tip - y), bl if up == 1 else -bl) for x, y, bl in s.pts]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        return Shape(s.layer, pts, s.closed, (min(xs), min(ys), max(xs), max(ys)))

    parts: list[Part] = []
    used = set()

    def take(role, article, outer: Shape):
        p = Part(role, article, [to_node(outer)])
        used.add(id(outer))
        for s in shapes:                 # отверстия - внутри габарита, тот же слой; всё из той же вставки блока
            if id(s) not in used and s is not outer and (
                    outer.group and s.group == outer.group or
                    not outer.group and s.layer == outer.layer and _inside(s.box, outer.box)):
                p.shapes.append(to_node(s))
                used.add(id(s))
        parts.append(p)

    # прижим лежит внутри габарита крышки - берём его первым, чтобы крышка не забрала его как отверстие
    if clamp:
        take("прижим", kit.get("Прижим", ""), max(clamp, key=lambda s: _w(s.box)))
    else:
        notes.append("прижим не найден")
    take("крышка", kit.get("Крышка стойки", ""), cover[0])
    seal_layer = [s for s in shapes if s.layer == "Уплотнители" and s.closed and id(s) not in used]
    # термовставка: Т50-xx - заштрихованная; пенная (Adifoam) - без штриховки, на оси, между стойкой и прижимом
    th_names = [a.strip().split(" (")[0] for a in kit.get("Термовставка", "").split("+") if a.strip()]
    hard = [n for n in th_names if n.startswith("Т50")]
    foam = [n for n in th_names if not n.startswith("Т50")]
    hatched = [s for s in seal_layer if any(_inside(h, s.box, 0.5) and _w(h) > 0.8 * _w(s.box) for h in hatches)]
    cx0 = lambda s: (s.box[0] + s.box[2]) / 2
    foamy = [s for s in shapes if s.layer == "Уплотнители" and id(s) not in used and s not in hatched   # пена бывает
             and not s.group and abs(cx0(s) - ax) < 2 and _w(s.box) * _h(s.box) > 150]          # незамкнутой линией
    # пена, вставленная блоком, - по общему габариту всех кусков вставки (в тонких узлах она из мелких кусков)
    groups: dict[str, list] = {}
    for s in shapes:
        if s.group and s.layer == "Уплотнители" and id(s) not in used:
            groups.setdefault(s.group, []).append(s)
    for g in groups.values():
        gb = (min(s.box[0] for s in g), min(s.box[1] for s in g), max(s.box[2] for s in g), max(s.box[3] for s in g))
        if abs((gb[0] + gb[2]) / 2 - ax) < 2 and _w(gb) * _h(gb) > 150:
            foamy.append(max(g, key=lambda s: _w(s.box) * _h(s.box)))
    free = list(th_names)                  # имена из комплектации - каждое один раз

    def name_for(prefer: list) -> str:
        n = next((n for n in prefer if n in free), None) or (free[0] if free else "термовставка")
        if n in free:
            free.remove(n)
        return n

    for group, prefer in ((hatched, hard), (foamy, foam)):
        for s in sorted(group, key=lambda s: -_w(s.box) * _h(s.box)):
            if id(s) not in used:
                take("термовставка", name_for(prefer), s)
    # уплотнители: у крышки - прижима, у стойки - стойки (по расстоянию до носика)
    for s in sorted(seal_layer, key=lambda s: -_w(s.box) * _h(s.box)):
        if id(s) in used or _w(s.box) * _h(s.box) < 40:
            continue
        mid = (s.box[1] + s.box[3]) / 2
        d_tip, d_cov = abs(mid - tip), abs(mid - (cover[0].box[1] + cover[0].box[3]) / 2)
        take("уплотнитель_стойки" if d_tip < d_cov else "уплотнитель_прижима",
             seals["стойки"] if d_tip < d_cov else seals["прижим"], s)
    for s in shapes:
        if s.layer == "Подкладки" and s.closed and id(s) not in used:
            take("подкладка_фикс", kit.get("Подкладка фиксир.", "").split(" (")[0] or "подкладка", s)
    screw = [s for s in shapes if s.layer == "2" and id(s) not in used]
    if screw:
        take("винт", kit.get("Винт прижима", ""), max(screw, key=lambda s: _h(s.box)))
        for s in screw:                                   # головка / шайба / куски винта с разрывом - к винту
            if id(s) not in used:
                parts[-1].shapes.append(to_node(s))
                used.add(id(s))
    for s in shapes:                                      # лента Герлен (уплотнение у прижима)
        if s.layer == "Герлен" and id(s) not in used:
            take("лента", "Герлен", s)
    for s in shapes:                                      # внутренние линии стойки - часть стойки (её рисует блок стойки)
        if s.layer == "0" and not s.closed and _inside(s.box, post.box, 1):
            used.add(id(s))
    left = [s for s in shapes if id(s) not in used and s is not post and s.layer not in ("Glass", "Зигзаг", "Конструкции")]
    if left:
        notes.append("не разобрано: " + ", ".join(f"{s.layer} {_w(s.box):.1f}x{_h(s.box):.1f}" for s in left))
    glass = [to_node(s) for s in shapes if s.layer == "Glass"]
    base["стекло_внутр"] = round(min(s.box[1] for s in glass), 2) if glass else None
    return base, parts, notes


# ======================= узлы для сечений программы =======================
def load_base(systems_dir) -> tuple[dict, dict]:
    """(узлы по вариантам и толщинам, контуры деталей по имени блока) или ({}, {}) если базы нет."""
    import json
    from pathlib import Path
    systems_dir = Path(systems_dir)
    jf, df = systems_dir / "КП50К_узлы_каталог.json", systems_dir / "КП50К_детали.dxf"
    if not (jf.exists() and df.exists()):
        return {}, {}
    nodes = json.loads(jf.read_text(encoding="utf-8"))["узлы"]
    lib = {}
    for blk in ezdxf.readfile(str(df)).blocks:
        if blk.name.startswith("*"):
            continue
        lib[blk.name] = [([(x, y, bl) for x, y, _, _, bl in e.get_points("xyseb")], bool(e.closed))
                         for e in blk if e.dxftype() == "LWPOLYLINE"]
    return nodes, lib


def _extent(d: dict, old_lib: dict) -> tuple[float, float, float, float]:
    """Габарит детали старого шаблона в системе профиля (блок, повёрнутый и отражённый как вставка)."""
    polys = old_lib.get(d["блок"])
    if not polys:
        return d["x"], d["y"], d["x"], d["y"]
    a = math.radians(d["поворот"])
    m = -1 if d["зеркало"] else 1
    pts = [(d["x"] + m * x * math.cos(a) - y * math.sin(a), d["y"] + m * x * math.sin(a) + y * math.cos(a))
           for p in polys for x, y in p]
    return min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts)


def _center(d: dict, old_lib: dict) -> tuple[float, float]:
    """Центр детали старого шаблона в системе профиля."""
    b = _extent(d, old_lib)
    return (b[0] + b[2]) / 2, (b[1] + b[3]) / 2


def load_perimeter(systems_dir) -> dict:
    """Доборные периметра из базы: {"толщина/вариант": {"стойка"|"ригель": {"детали", "убрать"}}}."""
    import json
    from pathlib import Path
    jf = Path(systems_dir) / "КП50К_узлы_каталог.json"
    return json.loads(jf.read_text(encoding="utf-8")).get("периметр", {}) if jf.exists() else {}


def node_template(key: str, node: dict, old: dict, riegel_nose: float, kit: dict,
                  fillers: list[str] = (), old_lib: dict | None = None, old_middle: dict | None = None,
                  perim: dict | None = None, pick=None, drawn_as: dict | None = None
                  ) -> tuple[dict, list[str]]:
    """Шаблон узла сечения (как в КП50К_узлы.json) из узла базы каталога.

    key - стойка_середина / стойка_край / ригель_середина / ригель_край; old - старый шаблон (с "Песочного")
    этого вида: из него берём то, чего в каталожном узле стойки нет (крышка и уплотнители ригеля, доборные).
    Узел базы: x от оси, y от носика, наружу +y. Стойка - та же система. Ригель: носик в (riegel_nose, 0),
    наружу -X, т.е. поворот на 90 градусов: (x, y) -> (riegel_nose - y, x).
    perim - доборные периметра из каталога для этого вида узла (стойка/ригель) - у крайних вместо старого образца;
    pick(артикул, аналог) -> выбранный артикул; drawn_as - артикул -> (блок, зеркало), как он прорисован у края.
    """
    notes = []
    is_beam = key.startswith("ригель")
    edge = key.endswith("_край")
    det = []
    clamp_new = None
    clamp_face = None                 # наружная грань прижима (ригель: наружу -X) - на ней лежит крышка ригеля
    for d in node["детали"]:
        if is_beam and d["роль"] in ("уплотнитель_стойки", "подкладка_фикс", "крышка"):
            continue
        if is_beam:
            x, y, rot = riegel_nose - d["y"], d["x"], 90.0
        else:
            x, y, rot = d["x"], d["y"], 0.0
        det.append({"блок": d["блок"], "x": round(x, 3), "y": round(y, 3), "поворот": rot, "зеркало": d["зеркало"],
                    "роль": d["роль"], "каталог": True})
        if d["роль"] == "прижим":
            clamp_new = (x, y)
            if d.get("в"):
                clamp_face = x - d["в"] / 2 if is_beam else y + d["в"] / 2
    # из старого шаблона: у ригеля - крышка ригеля (сдвиг вместе с прижимом) и его уплотнители; у края - доборные
    old_lib = old_lib or {}
    # доборные - профили периметра (уплотнители ТПУ из строки доборных - не доборные, они свои у ригеля)
    profiles = [f for f in fillers if not f.upper().startswith("ТПУ")]
    old_fill = [d for d in old["детали"] if d["блок"] in profiles] if edge and not perim else []
    # без доборных уплотнители ригеля лежат прямо на ригеле - как у среднего (в крайнем образце один - на доборном);
    # с доборными из каталога - тоже как у среднего, а лишнее у края убирается по каталогу
    seal_src = old if old_fill or not old_middle else old_middle
    old_clamp = next((d for d in old["детали"] if d["блок"] in ("КПС 758",)), None)
    for d in old["детали"]:
        if is_beam and d["блок"] == kit.get("Крышка ригеля") and old_clamp and clamp_new:
            # крышка лежит на наружной грани прижима: сдвиг крышки = сдвиг этой грани (прижимы разной высоты)
            old_face = _extent(old_clamp, old_lib)[0]
            dx = (clamp_face if clamp_face is not None else clamp_new[0]) - old_face
            det.append({**d, "x": round(d["x"] + dx, 3), "роль": "крышка"})
    if is_beam:
        det += [{**d, "роль": "уплотнитель_ригеля"} for d in seal_src["детали"] if d["блок"].startswith("ТПУ-6001")]
    if edge and perim:
        to_tpl = (lambda x, y: (riegel_nose - y, x)) if is_beam else (lambda x, y: (x, y))
        # что у края заменено пакетом доборных (уплотнитель стойки переставлен на доборный и т.п.) - убрать
        for gx, gy in perim["убрать"]:
            tx, ty = to_tpl(gx, gy)
            for d in list(det):
                cx, cy = (d["x"], d["y"]) if d.get("каталог") else _center(d, old_lib)
                if abs(cx - tx) < 1.0 and abs(cy - ty) < 1.0:
                    det.remove(d)
        for p in perim["детали"]:
            x, y = to_tpl(p["x"], p["y"])
            art = pick(p["артикул"], p["аналог"]) if pick else p["артикул"]
            block, mirror = p["блок"], p["зеркало"]
            if art != p["артикул"]:
                if drawn_as and art in drawn_as:
                    block, mirror = drawn_as[art]
                else:
                    notes.append(f"{art}: в каталоге не начерчен - в сечении нарисован {p['артикул']}")
            det.append({"блок": block, "x": round(x, 3), "y": round(y, 3), "поворот": 90.0 if is_beam else 0.0,
                        "зеркало": mirror, "роль": "доборный", "каталог": True})
    elif edge:
        # доборные периметра: из старого шаблона, если он для этих же доборных (fillers - доборные объекта)
        if old_fill:
            det += [{**d, "роль": "доборный"} for d in old_fill]
        else:
            notes.append("доборные крайних стоек/ригелей для этой толщины в базе пока нет - в сечении не показаны")
    return {"профиль": old["профиль"], "детали": det}, notes


def blocks_code(names, lib: dict) -> str:
    """LISP: определить в чертеже блоки деталей из базы (ActiveX). Блок с тем же именем (например, старый
    с "Песочного" с другой точкой вставки) пересобирается; метка в описании блока - чтобы не делать это каждый раз."""
    def n(v):
        return f"{float(v):.5f}"

    def s(t):
        return '"' + t.replace("\\", "\\\\").replace('"', '\\"') + '"'

    out = ["(vl-load-com) (setq blks (vla-get-Blocks (vla-get-ActiveDocument (vlax-get-acad-object))) done 0)"]
    for name in sorted(names):
        polys = lib.get(name)
        if not polys:
            continue
        sig = f"fasad-база-1 {sum(len(p) for p, _ in polys)}"
        body = []
        for pts, closed in polys:
            if len(pts) < 2:
                continue
            coords = " ".join(f"{n(x)} {n(y)}" for x, y, _ in pts)
            body.append(f'(setq pl (vla-AddLightWeightPolyline blk (vlax-make-variant (vlax-safearray-fill '
                        f'(vlax-make-safearray vlax-vbDouble (cons 0 {2 * len(pts) - 1})) (list {coords})))))')
            body += [f"(vla-SetBulge pl {i} {n(bl)})" for i, (_, _, bl) in enumerate(pts) if abs(bl) > 1e-9]
            if closed:
                body.append("(vla-put-Closed pl :vlax-true)")
        # старый блок с тем же именем (другая точка вставки, бывает динамический - его не очистить) -
        # переименовать в "имя (старый N)" и создать новый; ошибка одного блока не останавливает остальные
        out.append(f'(setq r (vl-catch-all-apply (function (lambda () '
                   f'(setq blk (if (tblsearch "BLOCK" {s(name)}) (vla-item blks {s(name)})))'
                   f'(if (or (not blk) (/= (vla-get-Comments blk) {s(sig)})) (progn '
                   f'(if blk (progn (setq k 1) (while (tblsearch "BLOCK" (strcat {s(name)} " (старый " (itoa k) ")")) '
                   f'(setq k (1+ k))) (vla-put-Name blk (strcat {s(name)} " (старый " (itoa k) ")"))))'
                   f'(setq blk (vla-Add blks (vlax-3d-point 0 0 0) {s(name)})) '
                   f'{" ".join(body)} (vla-put-Comments blk {s(sig)}) (setq done (1+ done))))))))'
                   f'(if (vl-catch-all-error-p r) (setq bad (cons {s(name)} bad)))')
    out[0] += " (setq bad nil)"
    return ("(progn " + "\n".join(out) +
            ' (strcat (itoa done) (if bad (strcat " НЕ ОПРЕДЕЛЕНЫ: " (apply (function strcat) '
            '(mapcar (function (lambda (x) (strcat x "; "))) bad))) "")))')


# ======================= вся база из листа 03 =======================
# серии каталога по положению колонок на листе (x, единицы листа)
SERIES = [(0, 2100, "стандарт"), (2100, 2500, "Тяжелые КПС 846"), (2500, 2950, "Тяжелые КПС 1126"),
          (2950, 3950, "Adifoam 530.121"), (3950, 5000, "Adifoam 530.120")]


def find_nodes(msp) -> list[dict]:
    """Узлы стоек: ось (вертикальная линия слоя 1) + подпись "Заполнение N мм" ближайшая сверху."""
    import re
    labels = []
    for e in msp.query("TEXT MTEXT"):
        t = e.plain_text() if e.dxftype() == "MTEXT" else e.dxf.text
        m = re.match(r"\s*Заполнение\s+(\d+)\s*мм", t)
        if m:
            labels.append((e.dxf.insert.x, e.dxf.insert.y, int(m.group(1))))
    nodes = []
    for e in msp.query("LINE"):
        if e.dxf.layer != "1":
            continue
        s, t = e.dxf.start, e.dxf.end
        if abs(s.x - t.x) > 0.01 or abs(s.y - t.y) < 10:
            continue
        x, top = s.x, max(s.y, t.y)
        cy = (s.y + t.y) / 2
        above = [l for l in labels if l[1] > top and abs(l[0] - x) < 75 and l[1] - top < 40]
        if not above:
            continue
        lab = min(above, key=lambda l: (l[1] - top) + abs(l[0] - x) * 0.3)
        series = next((n for a, b, n in SERIES if a <= x < b), None)
        if series:
            nodes.append({"x": x, "cy": cy, "толщина": lab[2], "серия": series,
                          "окно": (x - 30, cy - 33, x + 30, cy + 33)})
    return nodes


def _variant(series: str, thick: int, x: float, nodes: list, kits: list) -> str | None:
    """Стандартная серия: Т50 или Т50-10 (если оба - левый Т50, правый Т50-10)."""
    have = {r["Вариант"] for r in kits if int(r["Толщина"]) == thick}
    if series != "стандарт":
        return series if series in have else None
    cand = [v for v in ("Т50", "Т50-10") if v in have]
    if len(cand) < 2:
        return cand[0] if cand else None
    same = sorted(n["x"] for n in nodes if n["серия"] == "стандарт" and n["толщина"] == thick)
    return "Т50" if x == same[0] else "Т50-10"


def _key(shape_pts, mirror=False):
    """Форма детали без положения: вершины от центра габарита, для сравнения."""
    xs, ys = [p[0] for p in shape_pts], [p[1] for p in shape_pts]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    k = -1 if mirror else 1
    return sorted((k * (x - cx), y - cy) for x, y, _ in shape_pts)


def _same(a, b, tol=0.25) -> bool:
    """Одна и та же форма: то же число вершин, каждая на месте с точностью tol мм
    (масштаб листа 1:1,6 даёт расхождения в сотые доли)."""
    if len(a) != len(b):
        return False
    used = [False] * len(b)
    for x, y in a:
        for j, (u, v) in enumerate(b):
            if not used[j] and abs(x - u) <= tol and abs(y - v) <= tol:
                used[j] = True
                break
        else:
            return False
    return True


def block_name(article: str) -> str:
    """Имя блока по артикулу: AutoCAD не допускает в именах блоков < > / \\ " : ; ? * | , = `
    ("ВС 5,5х45 А2 DIN7981" -> "ВС 5.5х45 А2 DIN7981"); в спецификации артикул остаётся как есть."""
    import re
    return re.sub(r'[<>/\\":;?*|=`]', "-", article.replace(",", "."))


def _add_form(lib: dict, name: str, outer_pts: list, shapes: list, closed: list) -> tuple[str, bool]:
    """Форма детали в библиотеку (одинаковые формы - один блок, зеркальная - тот же блок с отражением).
    shapes - контуры от центра габарита. Возвращает (имя блока, зеркало)."""
    k0, k1 = _key(outer_pts), _key(outer_pts, True)
    lst = lib.setdefault(name, {"формы": []})["формы"]
    for i, f in enumerate(lst):
        if _same(k0, f["key"]):
            return (name if i == 0 else f"{name} ({i + 1})"), False
        if _same(k1, f["key"]):
            return (name if i == 0 else f"{name} ({i + 1})"), True
    lst.append({"key": k0, "shapes": shapes, "closed": closed})
    i = len(lst) - 1
    return (name if i == 0 else f"{name} ({i + 1})"), False


def build_base(dxf_path, system, out_json, out_dxf, extra_blocks: dict | None = None) -> dict:
    """Разобрать все узлы листа 03 -> библиотека деталей (DXF, блок на артикул) + узлы (JSON).
    extra_blocks - готовые детали {имя: [(точки, замкнут)]} (подкладки из 02 "Комплектующие").
    Доборные периметра - из раздела "Сечения крайних стоек и ригелей" того же листа (catalog_perimeter)."""
    import json
    from .catalog_perimeter import extract_perimeter
    doc = ezdxf.readfile(str(dxf_path))
    msp = doc.modelspace()
    nodes = find_nodes(msp)
    lib: dict[str, dict] = {}                 # артикул -> {"key", "shapes" (от центра габарита)}
    base_out: dict = {}
    report = []
    for n in nodes:
        var = _variant(n["серия"], n["толщина"], n["x"], nodes, system.kits)
        if not var:
            report.append(f'{n["серия"]} {n["толщина"]} мм (x={n["x"]:.0f}): нет такого варианта в таблице комплектации')
            continue
        kit = system.kit(n["толщина"], var)
        try:
            base, parts, notes = parse_post_node(msp, n["окно"], kit, system.card["уплотнители"])
        except ValueError as e:
            report.append(f'{var} {n["толщина"]} мм (x={n["x"]:.0f}): {e}')
            continue
        items = []
        for p in parts:
            outer = p.shapes[0]
            name = block_name(p.article or p.role)
            b = p.box
            cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
            block, mirror = _add_form(lib, name, outer.pts,
                                      [[(x - cx, y - cy, bl) for x, y, bl in s.pts] for s in p.shapes],
                                      [s.closed for s in p.shapes])
            items.append({"роль": p.role, "блок": block, "x": round(cx, 3), "y": round(cy, 3), "зеркало": mirror,
                          "ш": round(b[2] - b[0], 2), "в": round(b[3] - b[1], 2)})   # габарит в системе узла
        base_out.setdefault(var, {})[str(n["толщина"])] = {
            "стекло_внутр": base["стекло_внутр"], "детали": items, "замечания": notes,
            "источник": f'лист 03, x={n["x"]:.0f}'}
    # прижим не найден (начерчен одним контуром с крышкой) - ставим по другим узлам: смещение от крышки
    offs: dict[str, list] = {}
    for var, by_t in base_out.items():
        for node in by_t.values():
            cov = next((d for d in node["детали"] if d["роль"] == "крышка"), None)
            clm = next((d for d in node["детали"] if d["роль"] == "прижим"), None)
            if cov and clm:
                offs.setdefault(clm["блок"], []).append((clm["x"] - cov["x"], clm["y"] - cov["y"]))
    for var, by_t in base_out.items():
        for t, node in by_t.items():
            if any(d["роль"] == "прижим" for d in node["детали"]):
                continue
            art = system.kit(int(t), var).get("Прижим", "")
            cov = next((d for d in node["детали"] if d["роль"] == "крышка"), None)
            if art in offs and cov:
                dx, dy = offs[art][0]
                ref = next((d for n2 in base_out.values() for nd in n2.values() for d in nd["детали"]
                            if d["блок"] == art and d["роль"] == "прижим"), {})
                node["детали"].insert(0, {"роль": "прижим", "блок": art, "x": round(cov["x"] + dx, 3),
                                          "y": round(cov["y"] + dy, 3), "зеркало": False,
                                          "ш": ref.get("ш"), "в": ref.get("в")})
                node["замечания"] = [n for n in node["замечания"] if n != "прижим не найден"] + \
                    ["прижим поставлен по другим узлам (в каталоге начерчен вместе с крышкой)"]
    report += [f"{var} {t} мм: " + "; ".join(node["замечания"])
               for var, by_t in base_out.items() for t, node in by_t.items() if node["замечания"]]
    # доборные периметра: детали - в ту же библиотеку (тот же артикул той же формы - тот же блок)
    perim, notes = extract_perimeter(dxf_path, system.perimeters, system.card.get("доборные_толщины"),
                                     system.card.get("уплотнители"))
    report += [f"периметр {n}" for n in notes]
    perim_out: dict = {}
    for key, sides in perim.items():
        for side, d in sides.items():
            items = []
            for p in d["детали"]:
                block, mirror = _add_form(lib, block_name(p["артикул"]), p["контуры"][0], p["контуры"], p["замкнут"])
                items.append({"роль": "доборный", "артикул": p["артикул"], "аналог": p["аналог"], "блок": block,
                              "x": p["x"], "y": p["y"], "зеркало": mirror, "ш": p["ш"], "в": p["в"]})
            perim_out.setdefault(key, {})[side] = {"детали": items, "убрать": d["убрать"], "источник": d["источник"]}
    # библиотека деталей
    out = ezdxf.new("R2018")
    for name, d in lib.items():
        for i, f in enumerate(d["формы"]):
            blk = out.blocks.new(name if i == 0 else f"{name} ({i + 1})")
            for pts, closed in zip(f["shapes"], f["closed"]):
                blk.add_lwpolyline([(x, y, 0, 0, bl) for x, y, bl in pts], format="xyseb", close=closed)
    for name, polys in (extra_blocks or {}).items():
        name = block_name(name)
        if name in out.blocks:
            continue
        blk = out.blocks.new(name)
        for pts, closed in polys:
            blk.add_lwpolyline([(x, y, 0, 0, bl) for x, y, bl in pts], format="xyseb", close=closed)
    out.saveas(str(out_dxf))
    json.dump({"источник": str(dxf_path), "масштаб_листа": SCALE,
               "система_координат": "x - от оси стойки, y - от носика стойки, наружу +y; точка детали - центр габарита",
               "узлы": base_out,
               "периметр": perim_out}, open(out_json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return {"узлов": sum(len(v) for v in base_out.values()), "деталей": sum(len(d["формы"]) for d in lib.values()),
            "найдено": len(nodes), "периметр": len(perim_out), "замечания": report}
