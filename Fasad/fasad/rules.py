"""Узлы сечений по привязкам (вместо распознавания листа 03 каталога).

В узле две опорные линии (сняты с образцов пользователя 8/24/50 мм, все совпали):
  носик профиля          - уплотнитель на профиле, опорная подкладка, начало пакета доборных, внутренняя грань стекла;
  наружная грань стекла  - уплотнитель прижима, прижим, крышка, головка винта; термовставка упирается в прижим.
Какие артикулы ставить - таблицы комплектации и периметра; форма - блоки библиотеки пользователя.

Система координат узла (как у catalog_nodes): x - поперёк профиля от оси, y - от носика, наружу +y.
Сторона стекла у крайнего узла - +x (доборные - на -x). У ригеля +x - верх (стекло нижнего ригеля сверху).
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import ezdxf
from ezdxf import bbox

from .catalog_nodes import block_name
from .facade import parse_items
from .nodes import Place, to_local, to_world

TOL = 0.05
THIN = 10                         # заполнение до 10 мм включительно - "тонкое" (кромка стекла 8,5 от оси)
# толщина фиксирующих подкладок, мм (каталог 02, таблица "Подкладки из пластмассы")
PAD_T = {"ТПУ-011": 3.5, "ТПУ-017-01": 1, "ТПУ-017-03": 2, "ТПУ-017-04": 3, "ТПУ-017-05": 2, "ТПУ-017-06": 3,
         "КПП-18-1": 1, "КПП-18-2": 2, "КПП-18-3": 3}


def _art(s: str) -> str:
    """'Adifoam 530.121 (l=37)' -> 'Adifoam 530.121'."""
    return s.split(" (")[0].strip()


def _is_seal(a: str) -> bool:
    return a.upper().startswith(("ТПУ-6", "ТПУ-001"))


def _kind(a: str) -> str:
    return "уплотнитель" if _is_seal(a) else "профиль"


# ---------------------------------------------------------------- библиотека блоков
class Library:
    """Блоки пользователя: габарит в системе блока, поворот/зеркало -> габарит вставки."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.doc = ezdxf.readfile(str(path))
        self._box: dict[str, tuple] = {}
        self.made: dict[str, tuple[str, float]] = {}      # винт, растянутый из другого: имя -> (из чего, на сколько)

    def has(self, name: str) -> bool:
        return name in self.doc.blocks or name in self.made

    def box(self, name: str) -> tuple[float, float, float, float]:
        if name not in self._box:
            if name in self.made:
                src, dl = self.made[name]
                x0, y0, x1, y1 = self.box(src)
                self._box[name] = (x0, y0 - dl, x1, y1)
            else:
                e = bbox.extents(self.doc.blocks.get(name))
                self._box[name] = (e.extmin.x, e.extmin.y, e.extmax.x, e.extmax.y)
        return self._box[name]

    def placed_box(self, p: Place) -> tuple[float, float, float, float]:
        """Габарит вставки p (в системе узла)."""
        x0, y0, x1, y1 = self.box(p.name)
        pts = [to_world(Place("", p.x, p.y, p.rot, p.mirror), Place("", x, y)) for x in (x0, x1) for y in (y0, y1)]
        xs, ys = [q.x for q in pts], [q.y for q in pts]
        return min(xs), min(ys), max(xs), max(ys)

    def screw(self, name: str) -> str | None:
        """Винта нет в библиотеке - растянуть ближайший по длине (головка та же, стержень длиннее/короче)."""
        if self.has(name):
            return name
        m = re.search(r"5[.,]5х(\d+)", name)
        if not m:
            return None
        L = int(m.group(1))
        have = [(int(re.search(r"5[.,]5х(\d+)", b.name).group(1)), b.name) for b in self.doc.blocks
                if b.name.startswith("ВС 5.5х") and re.search(r"5[.,]5х(\d+)", b.name)]
        if not have:
            return None
        L0, src = min(have, key=lambda h: abs(h[0] - L))
        self.made[name] = (src, float(L - L0))
        return name

    def export_blocks(self, out_doc, names):
        """Определения блоков - в другой DXF (атлас); растянутые винты строятся тут же."""
        from ezdxf.addons import Importer
        plain = sorted({n for n in names if n in self.doc.blocks} | {self.made[n][0] for n in names if n in self.made})
        imp = Importer(self.doc, out_doc)
        imp.import_blocks([n for n in plain if n not in out_doc.blocks])
        imp.finalize()
        for n in names:
            if n in self.made and n not in out_doc.blocks:
                src, dl = self.made[n]
                top = self.box(src)[3]
                cut = top - 6.0                         # головка - верхние 6 мм блока винта
                blk = out_doc.blocks.new(n)
                for e in self.doc.blocks.get(src):
                    if e.dxftype() == "LWPOLYLINE":
                        pts = [(x, y - dl if y < cut else y, s, w, b) for x, y, s, w, b in e.get_points("xyseb")]
                        blk.add_lwpolyline(pts, format="xyseb", close=e.closed, dxfattribs={"layer": e.dxf.layer})
                    elif e.dxftype() == "LINE":
                        s, t = e.dxf.start, e.dxf.end
                        blk.add_line((s.x, s.y - dl if s.y < cut else s.y), (t.x, t.y - dl if t.y < cut else t.y),
                                     dxfattribs={"layer": e.dxf.layer})


# ---------------------------------------------------------------- образцы пользователя
@dataclass
class SampleItem:
    name: str                      # блок или "фикс" (контур фиксирующей подкладки)
    place: Place | None            # в системе узла
    box: tuple                     # габарит в системе узла
    world: tuple = (0.0, 0.0)      # где деталь в файле образцов (для замечаний сверки)


@dataclass
class Sample:
    profile: str
    beam: bool
    thick: int
    edge: bool
    items: list = field(default_factory=list)
    g_in: float = 0.0
    g_out: float = 0.0
    g_lat: float = 0.0
    profile_place: Place | None = None
    where: str = ""
    cx: float = 0.0                # середина профиля в файле образцов
    cy: float = 0.0
    frame: Place | None = None     # система узла в файле


def _frame(origin, udir, vdir) -> Place:
    """Система узла в мире: локальная x -> vdir, y -> udir."""
    a = math.degrees(math.atan2(-udir[0], udir[1])) % 360
    c, s = math.cos(math.radians(a)), math.sin(math.radians(a))
    mirror = abs(c - vdir[0]) > 0.5 or abs(s - vdir[1]) > 0.5
    return Place("", origin[0], origin[1], round(a, 6), mirror)


def read_samples(path: Path, profiles: set[str]) -> tuple[list[Sample], Library]:
    """Узлы образца: профиль + ближние к нему детали и стекло (слои Stek...)."""
    lib = Library(path)
    msp = lib.doc.modelspace()

    def box(e):
        try:
            b = bbox.extents([e])
        except Exception:
            return None
        return (b.extmin.x, b.extmin.y, b.extmax.x, b.extmax.y) if b.has_data else None

    profs, parts, glass = [], [], []
    for e in msp:
        t = e.dxftype()
        if t == "INSERT" and not e.dxf.name.startswith("*"):
            b = box(e)
            if b:
                (profs if e.dxf.name in profiles else parts).append((e, b))
        elif t == "LWPOLYLINE":
            b = box(e)
            if not b or b[2] - b[0] > 100 or b[3] - b[1] > 100:
                continue
            if e.dxf.layer.lower().startswith("stek"):
                glass.append((e, b))
            elif e.dxf.layer == "Подкладки":
                parts.append((e, b))

    def dist(b, c):
        dx = max(c[0] - b[2], b[0] - c[2], 0)
        dy = max(c[1] - b[3], b[1] - c[3], 0)
        return math.hypot(dx, dy)

    owner = lambda b: min(profs, key=lambda q: dist(q[1], b))[1]
    # носик каждого профиля (по стороне стекла): детали принадлежат узлу, к носику которого ближе.
    # По контуру профиля нельзя - у толстых заполнений прижим ригеля ближе к профилю соседнего узла
    noses = {}
    for pe, pb in profs:
        gl = [g for g in glass if owner(g[1]) == pb and dist(pb, g[1]) < 60]
        if not gl:
            continue
        beam = (pb[2] - pb[0]) > (pb[3] - pb[1])
        cu = (lambda b: (b[0] + b[2]) / 2) if beam else (lambda b: (b[1] + b[3]) / 2)
        s = 1 if sum(cu(g[1]) for g in gl) / len(gl) > cu(pb) else -1
        if beam:
            noses[id(pe)] = (pb[2] if s > 0 else pb[0], (pb[1] + pb[3]) / 2, s, 0)
        else:
            noses[id(pe)] = ((pb[0] + pb[2]) / 2, pb[3] if s > 0 else pb[1], 0, s)

    def near(b):
        """Профиль, к носику которого ближе деталь (с запасом наружу - там прижим и крышка)."""
        cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        best, d0 = None, 1e9
        for pe, pb in profs:
            n = noses.get(id(pe))
            if not n:
                continue
            u = (cx - n[0]) * n[2] + (cy - n[1]) * n[3]          # наружу от носика
            v = (cx - n[0]) * n[3] + (cy - n[1]) * n[2]          # поперёк
            d = math.hypot(v, max(0.0, -u - 60) + max(0.0, u - 120)) + 0.05 * abs(u)   # при равенстве - ближний
            if d < d0:
                best, d0 = pb, d
        return best if d0 < 90 else None

    out = []
    for pe, pb in profs:
        if id(pe) not in noses:
            continue
        gl = [g for g in glass if owner(g[1]) == pb and dist(pb, g[1]) < 60]
        mine = [p for p in parts if near(p[1]) == pb]
        beam = (pb[2] - pb[0]) > (pb[3] - pb[1])          # ригель лежит "глубиной" вдоль листа
        cu = (lambda b: (b[0] + b[2]) / 2) if beam else (lambda b: (b[1] + b[3]) / 2)
        cv = (lambda b: (b[1] + b[3]) / 2) if beam else (lambda b: (b[0] + b[2]) / 2)
        s = 1 if sum(cu(g[1]) for g in gl) / len(gl) > cu(pb) else -1
        udir = (s, 0) if beam else (0, s)
        nose = (pb[2] if s > 0 else pb[0]) if beam else (pb[3] if s > 0 else pb[1])
        axis = cv(pb)
        sides = {1 if cv(g[1]) > axis else -1 for g in gl}
        edge = len(sides) == 1
        sv = sides.pop() if edge else 1
        vdir = (0, sv) if beam else (sv, 0)
        org = (nose, axis) if beam else (axis, nose)
        base = _frame(org, udir, vdir)

        def loc_box(b):
            pts = [to_local(base, Place("", x, y)) for x in (b[0], b[2]) for y in (b[1], b[3])]
            xs, ys = [p.x for p in pts], [p.y for p in pts]
            return min(xs), min(ys), max(xs), max(ys)

        def loc_place(e):
            mir = e.dxf.xscale * e.dxf.yscale < 0
            rot = e.dxf.rotation + (180 if e.dxf.yscale < 0 else 0)
            return to_local(base, Place(e.dxf.name, e.dxf.insert.x, e.dxf.insert.y, rot % 360, mir))

        gb = [loc_box(g[1]) for g in gl]
        thick = round(gb[0][3] - gb[0][1])
        smp = Sample(pe.dxf.name, beam, thick, edge, where=f"x={(pb[0]+pb[2])/2:.0f}, y={(pb[1]+pb[3])/2:.0f}",
                     cx=(pb[0] + pb[2]) / 2, cy=(pb[1] + pb[3]) / 2, frame=base,
                     g_in=round(min(b[1] for b in gb), 3), g_out=round(max(b[3] for b in gb), 3),
                     g_lat=round(min(min(abs(b[0]), abs(b[2])) for b in gb), 3),
                     profile_place=loc_place(pe))
        for e, b in mine:
            if e.dxftype() == "INSERT":
                smp.items.append(SampleItem(e.dxf.name, loc_place(e), loc_box(b), (e.dxf.insert.x, e.dxf.insert.y)))
            else:
                smp.items.append(SampleItem("фикс", None, loc_box(b), ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)))
        out.append(smp)
    return out, lib


# ---------------------------------------------------------------- привязки
def _cx(b):
    return (b[0] + b[2]) / 2


def _mirror_x(p: Place) -> Place:
    """Отражение вставки относительно оси узла (x -> -x)."""
    return to_world(Place("", 0, 0, 0, True), p)


def _orient(p: Place) -> dict:
    return {"поворот": p.rot, "зеркало": p.mirror}


def learn(samples: list[Sample], kits: list[dict], perimeters: list[dict], card: dict) -> tuple[dict, list[str]]:
    """Привязки из образцов. Возвращает (правила, замечания: где образцы расходятся между собой)."""
    notes = []
    clamps = {_art(r["Прижим"]) for r in kits}
    covers = {_art(r["Крышка стойки"]) for r in kits} | {_art(r["Крышка ригеля"]) for r in kits}
    thermos = {_art(a) for r in kits for a, _, _ in parse_items(r["Термовставка"])}
    setting = {_art(a) for r in kits for a, _, _ in parse_items(r["Подкладка опорная"])}
    seal_in = {False: card["уплотнители"]["стойки"], True: card["уплотнители"]["ригели"]}
    seal_out = card["уплотнители"]["прижим"]
    vals: dict[tuple, list] = {}

    def put(key, value, where):
        vals.setdefault(key, []).append((value, where))

    for s in samples:
        kind = "ригель" if s.beam else "стойка"
        tag = f"{kind} {s.thick} мм ({s.where})"
        thin = "тонкое" if s.thick <= THIN else "толстое"
        put(("стекло_внутр",), s.g_in, tag)
        put(("кромка", thin), s.g_lat, tag)
        put(("профиль", kind), _orient(s.profile_place), tag)
        clamp = next((i for i in s.items if i.name in clamps), None)
        stack = []
        for it in s.items:
            n, b = it.name, it.box
            right = _cx(b) > 0
            p = it.place if (right or it.place is None) else _mirror_x(it.place)
            if n == seal_out:
                put(("нар_уплотнитель",), (round(p.x, 2), round(p.y - s.g_out, 2), p.rot, p.mirror), tag)
            elif n == seal_in[s.beam] and right and b[1] < s.g_in:
                put(("внутр_уплотнитель", kind), (round(p.x, 2), round(p.y, 2), p.rot, p.mirror), tag)
            elif n in clamps:
                put(("прижим_нар", kind), round(b[3] - s.g_out, 2), tag)
                put(("ориент", n, kind), _orient(it.place), tag)
            elif n in covers:
                put(("крышка_внутр", kind), round(b[1] - s.g_out, 2), tag)
                put(("ориент", n, kind), _orient(it.place), tag)
            elif n.startswith("ВС 5") and abs(_cx(b)) < 1:
                # головка лежит в пазу прижима - у каждого прижима своя глубина
                put(("винт_головка", clamp.name if clamp else "?"), round(b[3] - s.g_out, 2), tag)
                put(("ориент", "винт", kind), _orient(it.place), tag)
            elif n in thermos and abs(_cx(b)) < 1:
                put(("термо_до_прижима", n), round(b[3] - clamp.box[1], 2) if clamp else 0.0, tag)
                put(("ориент", n, kind), _orient(it.place), tag)
            elif n in setting and s.beam:
                put(("опорная_от_носика",), round(b[1], 2), tag)
                put(("опорная_от_оси",), round(b[0], 2), tag)       # внутренний край - в пазу ригеля
                put(("ориент", n, kind), _orient(it.place), tag)
            elif n == "фикс":
                if right:
                    put(("фикс_низ", thin), round(b[1] - s.g_in, 2), tag)
            elif not right and s.edge and not n.startswith("ВС"):    # винт спейсера - не доборный
                stack.append(it)
        # пакет доборных: по порядку от носика (в таблице периметра порядок не всегда от носика - запомнить)
        stack.sort(key=lambda i: i.box[1])
        if stack:
            put(("пакет_порядок", kind, "+".join(sorted(i.name for i in stack))), tuple(i.name for i in stack), tag)
        for k, it in enumerate(stack):
            if k == 0:
                put(("пакет_начало", kind, _kind(it.name)), round(it.box[1], 2), tag)
            else:
                # нахлёст - по паре соседей: профиль на профиль, уплотнитель на профиль...
                put(("нахлёст", _kind(stack[k - 1].name), _kind(it.name)), round(stack[k - 1].box[3] - it.box[1], 2), tag)
            put(("пакет_от_оси", it.name), round(-_cx(it.box), 2), tag)
            put(("ориент_доборный", it.name, kind), _orient(it.place), tag)

    rules: dict = {}
    for key, lst in vals.items():
        uniq = []
        for v, w in lst:
            if not any(_close(v, u) for u, _ in uniq):
                uniq.append((v, w))
        v0 = uniq[0][0]
        if len(uniq) > 1:
            notes.append(" / ".join(map(str, key)) + ": " + "; ".join(f"{_fmt(v)} ({w})" for v, w in uniq))
            if all(isinstance(v, (int, float)) for v, _ in uniq):
                v0 = round(sorted(v for v, _ in lst)[len(lst) // 2], 2)     # медиана
        rules[" / ".join(map(str, key))] = v0
    return rules, notes


def _close(a, b, tol=0.15) -> bool:
    if isinstance(a, dict) and isinstance(b, dict):
        return abs((a["поворот"] - b["поворот"] + 180) % 360 - 180) < 0.01 and a["зеркало"] == b["зеркало"]
    if isinstance(a, tuple) and isinstance(b, tuple):
        return all(_close(x, y, tol) for x, y in zip(a, b))
    if isinstance(a, (bool, str)) or isinstance(b, (bool, str)):
        return a == b
    return abs(a - b) <= tol


def _fmt(v):
    if isinstance(v, dict):
        return f"поворот {v['поворот']:g}{' зеркально' if v['зеркало'] else ''}"
    return str(v)


# ---------------------------------------------------------------- сборка узла
@dataclass
class Built:
    places: list                 # [Place] - блоки
    rects: list                  # [(слой, (x0, y0, x1, y1))] - стекло, фиксирующие подкладки
    notes: list


def build_node(rules: dict, lib: Library, *, beam: bool, thick: int, profile: str, kit: dict, card: dict,
               edge_stack: list[str] | None = None, glass_len: float = 30.0) -> Built:
    """Узел стойки/ригеля в системе узла. edge_stack - доборные крайнего узла (от носика наружу), None - средний."""
    R = rules
    kind = "ригель" if beam else "стойка"
    notes, places, rects = [], [], []
    edge = edge_stack is not None
    g_in = R["стекло_внутр"]
    g_out = g_in + thick
    g_lat = R["кромка / " + ("тонкое" if thick <= THIN else "толстое")]

    def orient(name: str, *keys) -> dict | None:
        for k in keys:
            if k in R:
                return R[k]
        return None

    def place_by(name: str, o: dict, *, y_lo=None, y_hi=None, x_mid=None, x_lo=None) -> Place | None:
        """Вставка блока name с ориентацией o: низ/верх габарита по y, середина/левый край по x."""
        if not lib.has(name):
            notes.append(f"{name}: нет блока в библиотеке - не нарисован")
            return None
        p = Place(name, 0.0, 0.0, o["поворот"], o["зеркало"])
        b = lib.placed_box(p)
        dx = (x_mid - _cx(b)) if x_mid is not None else (x_lo - b[0]) if x_lo is not None else 0.0
        dy = (y_lo - b[1]) if y_lo is not None else (y_hi - b[3])
        return Place(name, round(dx, 3), round(dy, 3), p.rot, p.mirror)

    def both(p: Place, sides=(1, -1)):
        for sgn in sides:
            places.append(p if sgn > 0 else _mirror_x(p))

    # профиль: носик в (0, 0)
    o = R.get(f"профиль / {kind}")
    pp = place_by(profile, o, y_hi=0.0, x_mid=0.0)
    if pp:
        places.append(pp)
    glass_sides = (1,) if edge else (1, -1)
    # стекло
    for sgn in glass_sides:
        x0, x1 = sorted((sgn * g_lat, sgn * (g_lat + glass_len)))
        rects.append(("стекло", (x0, g_in, x1, g_out)))
    # уплотнитель на профиле - со стороны стекла
    key = f"внутр_уплотнитель / {kind}"
    if key in R:
        x, y, rot, mir = R[key]
        both(Place(card["уплотнители"]["ригели" if beam else "стойки"], x, y, rot, mir), glass_sides)
    # прижим, крышка, винт, термовставка
    clamp_art = _art(kit.get("Прижим", ""))
    clamp = None
    if clamp_art:
        o = orient(clamp_art, f"ориент / {clamp_art} / {kind}", f"ориент / {clamp_art} / стойка",
                   f"ориент / {clamp_art} / ригель") or _any(R, "ориент / ", clamps_of(R), kind)
        clamp = place_by(clamp_art, o, y_hi=g_out + R[f"прижим_нар / {kind}"], x_mid=0.0) if o else None
        if clamp:
            places.append(clamp)
    x, y, rot, mir = R["нар_уплотнитель"]
    both(Place(card["уплотнители"]["прижим"], x, y + g_out, rot, mir))
    cover_art = _art(kit.get("Крышка ригеля" if beam else "Крышка стойки", ""))
    if cover_art:
        o = orient(cover_art, f"ориент / {cover_art} / {kind}", f"ориент / {cover_art} / стойка",
                   f"ориент / {cover_art} / ригель")
        if o is None:
            notes.append(f"{cover_art}: в образцах не встречается - не знаю, как повернуть")
        else:
            c = place_by(cover_art, o, y_lo=g_out + R[f"крышка_внутр / {kind}"], x_mid=0.0)
            if c:
                places.append(c)
    screw = kit.get("Винт прижима", "")
    if screw:
        name = lib.screw(block_name(screw))
        if name is None:
            notes.append(f"{screw}: нет блока винта")
        else:
            if name in lib.made:
                notes.append(f"{screw}: блока нет - растянут из {lib.made[name][0]}")
            head = R.get(f"винт_головка / {clamp_art}")
            if head is None:
                head = _median([v for k, v in R.items() if k.startswith("винт_головка / ")])
                notes.append(f"{clamp_art}: винт с этим прижимом в образцах не встречается - головка по среднему")
            s = place_by(name, R[f"ориент / винт / {kind}"], y_hi=g_out + head, x_mid=0.0)
            if s:
                places.append(s)
    top = clamp and lib.placed_box(clamp)[1]
    for a, _, q in parse_items(kit.get("Термовставка", "")):
        a = _art(a)
        o = orient(a, f"ориент / {a} / {kind}", f"ориент / {a} / стойка", f"ориент / {a} / ригель") \
            or R.get("ориент / Т50-01 / стойка")
        if o is None or top is None:
            continue
        if f"ориент / {a} / {kind}" not in R and f"ориент / {a} / стойка" not in R:
            notes.append(f"{a}: в образцах нет - повёрнут как Т50-01")
        t = place_by(a, o, y_hi=top + R.get(f"термо_до_прижима / {a}", 0.0), x_mid=0.0)
        if t:
            places.append(t)
            top = lib.placed_box(t)[1]                    # следующая часть составной термовставки - ниже
    # опорная подкладка - только ригель, под стеклом (сторона +x)
    if beam:
        for a, _, q in parse_items(kit.get("Подкладка опорная", "")):
            a = _art(a)
            if a.startswith("ТПУ-017") or a.startswith("КПП-"):
                continue                                   # резина на полке подкладки - в сечении не рисуем
            o = orient(a, f"ориент / {a} / ригель") or _any(R, "ориент / ", setting_of(R), "ригель")
            if o is None:
                notes.append(f"{a}: не знаю, как повернуть опорную подкладку")
                continue
            p = place_by(a, o, y_lo=R["опорная_от_носика"], x_lo=R["опорная_от_оси"])
            if p:
                places.append(p)
    # фиксирующие подкладки - только стойка, у кромки стекла, стопкой к оси
    if not beam:
        low = g_in + R["фикс_низ / " + ("тонкое" if thick <= THIN else "толстое")]
        for sgn in glass_sides:
            x = g_lat
            for a, _, q in parse_items(kit.get("Подкладка фиксир.", "")):
                t = PAD_T.get(_art(a))
                if t is None:
                    notes.append(f"{a}: неизвестна толщина фиксирующей подкладки")
                    continue
                for _ in range(q):
                    x0, x1 = sorted((sgn * x, sgn * (x - t)))
                    rects.append(("подкладка", (x0, low, x1, g_out)))
                    x -= t
    # доборные крайнего узла - стопкой от носика на стороне -x
    if edge_stack:
        order = R.get(f"пакет_порядок / {kind} / " + "+".join(sorted(edge_stack)))
        if order:
            edge_stack = list(order)                       # как у пользователя в образце
        prev, prev_kind = None, None
        for a in edge_stack:
            k = _kind(a)
            if prev is None:
                y_lo = R.get(f"пакет_начало / {kind} / {k}")
                if y_lo is None:                           # уплотнитель первым - на место уплотнителя профиля
                    y_lo = R[f"пакет_начало / {kind} / профиль"] - 1
                    notes.append(f"{a}: первым в пакете в образцах не встречается - поставлен условно")
            else:
                ov = R.get(f"нахлёст / {prev_kind} / {k}")
                if ov is None:
                    ov = _median([v for kk, v in R.items() if kk.startswith("нахлёст / ")])
                    notes.append(f"нахлёст {prev_kind} -> {k} в образцах нет - взят средний")
                y_lo = prev - ov
            o = orient(a, f"ориент_доборный / {a} / {kind}", f"ориент_доборный / {a} / стойка",
                       f"ориент_доборный / {a} / ригель")
            if o is None:
                o = R.get(f"ориент_доборный / Т50-07 / {kind}")
                notes.append(f"{a}: в образцах нет - повёрнут как Т50-07, проверить")
            off = R.get(f"пакет_от_оси / {a}")
            if off is None:
                off = _median([v for kk, v in R.items() if kk.startswith("пакет_от_оси / ")])
            p = place_by(a, o, y_lo=y_lo, x_mid=-off)
            if p:
                places.append(p)
                prev, prev_kind = lib.placed_box(p)[3], k
    return Built(places, rects, notes)


def _median(v):
    v = sorted(v)
    return v[len(v) // 2] if v else 0.0


def clamps_of(R):
    return {k.split(" / ")[1] for k in R if k.startswith("ориент / ") and k.split(" / ")[1].startswith(("КПС 758", "КП45313"))}


def setting_of(R):
    return {k.split(" / ")[1] for k in R if k.startswith("ориент / ") and k.split(" / ")[1] in ("КП45109", "КПС 954", "ТПУ-011")}


def _any(R, prefix, names, kind):
    for n in sorted(names):
        k = f"{prefix}{n} / {kind}"
        if k in R:
            return R[k]
    return None


def save(rules: dict, path: Path, source: str):
    path.write_text(json.dumps({"источник": source, "привязки": rules}, ensure_ascii=False, indent=1), encoding="utf-8")


def load(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))["привязки"]
