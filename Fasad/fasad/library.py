"""Библиотека узлов пользователя: готовые сечения стоек и ригелей, нарисованные из блоков.

Источник - файл образцов (карточка системы, ["сечение"]["библиотека"]). Узел хранится в системе узла
(rules: x - поперёк профиля от оси, y - от носика, наружу +y, сторона стекла крайнего узла +x).
При построении узел ставится на ось стойки/ригеля, зеркалится, профиль меняется на выбранный.
Если нужного узла нет - черновик по привязкам (rules) с замечанием.

Вариант узла определяется по деталям (термовставка, прижим, винт, подкладка - из таблицы комплектации),
набор доборных крайнего узла - сравнением с таблицей периметра. Чтение файла кэшируется в JSON.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from . import rules as R
from .facade import parse_items, pick_filler
from .nodes import Place, to_world

CACHE_VERSION = 6
ROW = re.compile(r"^\s*(.+?)\s+(\d+)\s*мм\s*$")


@dataclass
class LibNode:
    variant: str
    thick: int
    beam: bool
    edge: bool
    profile: str
    profile_place: Place
    items: list                      # [(имя блока, Place)] - без профиля
    pads: list                       # [(x0, y0, x1, y1)] - фиксирующие подкладки (контуры)
    stack: tuple                     # доборные крайнего узла от носика
    g_in: float
    g_lat: float
    where: str                       # подпись + координаты в файле - для замечаний
    labeled: bool = False            # из исправленного атласа (подпись "вариант NN мм") - свежее


@dataclass
class Picked:
    places: list                     # [Place] в системе узла, профиль первым
    pads: list
    g_in: float
    g_lat: float
    notes: list = field(default_factory=list)
    from_library: bool = True


def label(name: str, known: set[str]) -> str:
    """Подпись детали по имени блока: 'Adifoam 530.121 40' (укорочен по перфорации) -> 'Adifoam 530.121',
    'Т50-04 (2)' -> 'Т50-04'."""
    name = re.sub(r" \(\d+\)$", "", name)
    if name not in known:
        head = name.rsplit(" ", 1)[0]
        if head in known:
            return head
    return name


def _known(system) -> set[str]:
    out = set()
    for r in system.kits:
        for col in ("Термовставка", "Прижим", "Крышка стойки", "Крышка ригеля", "Подкладка опорная",
                    "Подкладка фиксир.", "Винт прижима", "Винт под подкладку"):
            out |= {R._art(a) for a, _, _ in parse_items(r.get(col, "") or "")}
    for r in system.perimeters:
        for col in ("Крайняя стойка", "Крайний ригель"):
            for a, alt, _ in parse_items(r[col]):
                out |= {R._art(a), alt.strip()} - {""}
    out |= set(system.card["уплотнители"].values())
    return out


# ------------------------------------------------------------------ чтение файла
def _labels(doc) -> tuple[list, list]:
    rows, cells = [], []
    for e in doc.modelspace().query("TEXT MTEXT"):
        t = (e.plain_text() if e.dxftype() == "MTEXT" else e.dxf.text).replace("\n", " ").strip()
        x, y = e.dxf.insert.x, e.dxf.insert.y
        m = ROW.match(t)
        if m and not t.lower().startswith(("стойка", "ригель")):
            rows.append((x, y, m.group(1).strip(), int(m.group(2))))
        elif t.lower().startswith(("стойка", "ригель")):
            cells.append((x, y, t))
    return rows, cells


def _variant(names: set[str], thick: int, beam: bool, system, row_label: str | None) -> tuple[str, int]:
    """Вариант комплектации: по подписи строки атласа (стойки "тяжёлых" не отличить от Т50-10 по деталям),
    без подписи - больше всего совпавших артикулов."""
    if row_label and any(r["Вариант"] == row_label and int(r["Толщина"]) == thick for r in system.kits):
        return row_label, 99
    best, score = None, -1
    for r in system.kits:
        if int(r["Толщина"]) != thick:
            continue
        want = {R._art(a) for col in ("Термовставка", "Прижим", "Винт прижима") for a, _, _ in parse_items(r[col])}
        if beam:
            want |= {R._art(a) for a, _, _ in parse_items(r["Подкладка опорная"]) if not a.startswith(("ТПУ-017", "КПП"))}
        want = {R.block_name(a) for a in want if a}
        s = len(want & names) - 0.5 * len(want - names)
        if s > score or s == score and row_label == r["Вариант"]:
            best, score = r["Вариант"], s
    return best or (row_label or "?"), score


def read_library(path: Path, system) -> tuple[list[LibNode], R.Library, list[str]]:
    samples, lib = R.read_samples(path, {r["Шифр"] for r in system.profiles})
    rows, cells = _labels(lib.doc)
    known = _known(system)
    clamps = {R._art(r["Прижим"]) for r in system.kits}
    covers = {R._art(r["Крышка стойки"]) for r in system.kits} | {R._art(r["Крышка ригеля"]) for r in system.kits}
    seal_out = system.card["уплотнители"]["прижим"]
    nodes, notes = [], []
    for s in samples:
        # подписи атласа: строка "вариант NN мм" выше-левее узла, ячейка "Стойка ..."/"Ригель ..." над узлом
        row = min((r for r in rows if 0 < r[1] - s.cy < 220 and r[0] < s.cx + 60),
                  key=lambda r: (r[1] - s.cy) + 0.2 * abs(r[0] - s.cx), default=None)
        cell = min((c for c in cells if -10 < c[1] - s.cy < 200 and abs(c[0] - s.cx) < 140),
                   key=lambda c: abs(c[1] - s.cy) + abs(c[0] - s.cx), default=None)
        names = {i.name for i in s.items} | {label(i.name, known) for i in s.items}
        var, _ = _variant(names, s.thick, s.beam, system, row[2] if row and row[3] == s.thick else None)
        mid = lambda i: abs(R._cx(i.box)) < 1
        stack = ()
        if s.edge:
            st = [i for i in s.items if i.place and R._cx(i.box) < 0 and not i.name.startswith("ВС")
                  and i.name != seal_out and label(i.name, known) not in clamps | covers and not mid(i)]
            stack = tuple(i.name for i in sorted(st, key=lambda i: i.box[1]))
        where = (f"{(row[2] + ' ' + str(row[3]) + ' мм, ') if row else ''}"
                 f"{(cell[2] if cell else ('ригель' if s.beam else 'стойка'))[:60]} (x={s.cx:.0f}, y={s.cy:.0f})")
        nodes.append(LibNode(var, s.thick, s.beam, s.edge, s.profile, s.profile_place,
                             [(i.name, i.place) for i in s.items if i.place],
                             [i.box for i in s.items if i.place is None], stack, s.g_in, s.g_lat, where,
                             labeled=bool(row)))
    return nodes, lib, notes


def _to_json(n: LibNode) -> dict:
    pl = lambda p: [p.name, p.x, p.y, p.rot, p.mirror]
    return {"вариант": n.variant, "толщина": n.thick, "ригель": n.beam, "крайний": n.edge, "профиль": n.profile,
            "профиль_вставка": pl(n.profile_place), "детали": [pl(p) for _, p in n.items], "подкладки": n.pads,
            "доборные": list(n.stack), "стекло_внутр": n.g_in, "кромка": n.g_lat, "где": n.where, "атлас": n.labeled}


def _from_json(d: dict) -> LibNode:
    pl = lambda a: Place(a[0], a[1], a[2], a[3], a[4])
    return LibNode(d["вариант"], d["толщина"], d["ригель"], d["крайний"], d["профиль"], pl(d["профиль_вставка"]),
                   [(a[0], pl(a)) for a in d["детали"]], [tuple(b) for b in d["подкладки"]], tuple(d["доборные"]),
                   d["стекло_внутр"], d["кромка"], d["где"], d["атлас"])


def library_path(system) -> Path | None:
    p = system.card.get("сечение", {}).get("библиотека")
    return Path(p) if p else None


_LIBS: dict[str, tuple[int, "R.Library"]] = {}        # путь -> (mtime, библиотека): DXF читаем один раз


def _library(path: Path) -> "R.Library":
    st = Path(path).stat()
    key = str(path)
    if key not in _LIBS or _LIBS[key][0] != st.st_mtime_ns:
        _LIBS[key] = (st.st_mtime_ns, R.Library(path))
    return _LIBS[key][1]


def load(system, systems_dir: Path) -> tuple[list[LibNode], R.Library | None]:
    """Узлы библиотеки (из кэша, если файл образцов не менялся) и сама библиотека блоков."""
    path = library_path(system)
    if not path or not path.exists():
        return [], None
    st = path.stat()
    stamp = f"{CACHE_VERSION}|{st.st_size}|{st.st_mtime_ns}"
    cache = Path(systems_dir) / f"{system.card['система']['название'].split()[-1]}_библиотека.json"
    if cache.exists():
        d = json.loads(cache.read_text(encoding="utf-8"))
        if d.get("метка") == stamp:
            return [_from_json(x) for x in d["узлы"]], _library(path)
    nodes, lib, _ = read_library(path, system)
    cache.write_text(json.dumps({"источник": str(path), "метка": stamp, "узлы": [_to_json(n) for n in nodes]},
                                ensure_ascii=False, indent=0), encoding="utf-8")
    return nodes, lib


# ------------------------------------------------------------------ выбор узла
def wanted_stack(text: str, mode: str) -> list[str]:
    """Доборные из строки таблицы периметра с выбором из пары "А (Б)" по исходным данным."""
    out = []
    for a, alt, q in parse_items(text):
        art = pick_filler(R._art(a), alt.strip(), mode)[0] if alt else R._art(a)
        out += [art] * q
    return out


class Source:
    """Узлы для сечений одного объекта: из библиотеки или черновик по привязкам."""

    def __init__(self, system, systems_dir: Path, obj, kit: dict, perimeter: dict):
        self.system, self.obj, self.kit, self.card = system, obj, kit, system.card
        self.nodes, self.lib = load(system, systems_dir)
        self.known = _known(system)
        rf = Path(systems_dir) / "КП50К_привязки.json"
        self.rules = R.load(rf) if rf.exists() else None
        self.perimeter = perimeter                 # {"Крайняя стойка": "...", "Крайний ригель": "..."}
        self.used: set[str] = set()

    def ready(self) -> bool:
        return bool(self.nodes) and self.lib is not None

    def _profile(self, beam: bool, ref: Place, art: str | None = None) -> Place | None:
        """Профиль в узле: выбранный артикул (art) или из исходных данных."""
        if art is None:
            art = (self.obj.ригель or self.card["ригель"]["артикул"]) if beam \
                else (self.obj.стойка or self.card["стойка"]["артикул"])
        if not self.lib.has(art):
            return None
        p = Place(art, 0.0, 0.0, ref.rot, ref.mirror)
        b = self.lib.placed_box(p)
        return Place(art, round(-R._cx(b), 3), round(-b[3], 3), ref.rot, ref.mirror)

    def node(self, beam: bool, edge: bool, glass_below: bool = False, art: str | None = None,
             variant: str | None = None) -> Picked:
        """Узел в системе узла. glass_below - верхний ригель: опорная подкладка не нужна (она под стеклом).
        art - профиль для сечения (по умолчанию из исходных данных).
        variant - вариант комплектации узла (по умолчанию из исходных данных; в А-А под лёгкими
        пакетами берётся обычный вариант по карте "подкладки_по_весу")."""
        o = self.obj
        kind = "ригель" if beam else "стойка"
        if art is None:
            art = (o.ригель or self.card["ригель"]["артикул"]) if beam \
                else (o.стойка or self.card["стойка"]["артикул"])
        if variant is None:
            variant = o.вариант
        notes = []
        want = None
        if edge:
            want = wanted_stack(self.perimeter.get("Крайний ригель" if beam else "Крайняя стойка", ""), o.доборные)
        cand = [n for n in self.nodes if n.variant == variant and n.thick == o.заполнение and n.beam == beam
                and n.edge == edge]
        if edge:
            same = [n for n in cand if Counter(label(a, self.known) for a in n.stack) == Counter(want)]
            if not same and want:
                # нарисован аналог из пары "А (Б)" - взять, предупредить
                alt = [n for n in cand if len(n.stack) == len(want)]
                if alt:
                    notes.append(f"{kind} крайн.: в библиотеке нет доборных {' + '.join(want)} - взят узел с "
                                 f"{' + '.join(label(a, self.known) for a in alt[0].stack)}")
                same = alt
            cand = same
        if cand:
            n = sorted(cand, key=lambda n: not n.labeled)[0]
            prof = self._profile(beam, n.profile_place, art)
            if prof is None:
                notes.append(f"{kind}: в библиотеке нет блока «{art}» - в сечении оставлен "
                             f"{n.profile} из узла; переименуйте/дорисуйте блок и повторите сверку")
                prof = n.profile_place
            places = [prof] + [p for _, p in n.items]
            if glass_below:
                setting = {R.block_name(R._art(a)) for r in self.system.kits for a, _, _ in parse_items(r["Подкладка опорная"])}
                places = [p for p in places if label(p.name, self.known) not in setting]
            self.used |= {p.name for p in places}
            return Picked(places, list(n.pads), n.g_in, n.g_lat, notes)
        # черновик по привязкам
        notes.append(f"{kind} {'крайн.' if edge else 'средн.'} {o.вариант} {o.заполнение} мм: в библиотеке нет - "
                     f"собран по правилам, ПРОВЕРИТЬ")
        if not self.rules:
            raise ValueError("нет ни узла в библиотеке, ни привязок (py Привязки.py)")
        b = R.build_node(self.rules, self.lib, beam=beam, thick=o.заполнение, profile=art, kit=self.kit, card=self.card,
                         edge_stack=want if edge else None)
        places = b.places
        if glass_below:
            setting = {R.block_name(R._art(a)) for a, _, _ in parse_items(self.kit.get("Подкладка опорная", ""))}
            places = [p for p in places if p.name not in setting]
        self.used |= {p.name for p in places}
        g_in = self.rules["стекло_внутр"]
        g_lat = self.rules["кромка / " + ("тонкое" if o.заполнение <= R.THIN else "толстое")]
        return Picked(places, [r for lay, r in b.rects if lay == "подкладка"], g_in, g_lat, notes + b.notes, False)


# ------------------------------------------------------------------ блоки в чертёж
def export_blocks(lib: R.Library, names: set[str], out: Path) -> dict[str, str]:
    """Определения блоков узлов - в отдельный DXF (для вставки в чертёж). Возвращает {имя: подпись версии}."""
    import ezdxf
    doc = ezdxf.new("R2018")
    lib.export_blocks(doc, names)
    sig = {}
    for name in names:
        if name not in doc.blocks:
            continue
        blk = doc.blocks.get(name)
        h = hashlib.md5()
        for e in blk:
            h.update(e.dxftype().encode())
            if e.dxftype() == "LWPOLYLINE":
                h.update(repr([tuple(round(v, 3) for v in p[:2]) for p in e.get_points("xy")]).encode())
            elif e.dxftype() == "LINE":
                h.update(repr((round(e.dxf.start.x, 3), round(e.dxf.start.y, 3), round(e.dxf.end.x, 3),
                               round(e.dxf.end.y, 3))).encode())
            elif e.dxftype() == "ARC":
                h.update(repr((round(e.dxf.center.x, 3), round(e.dxf.center.y, 3),
                               round(e.dxf.radius, 3), round(e.dxf.start_angle, 2),
                               round(e.dxf.end_angle, 2))).encode())
        sig[name] = "fasad-узел " + h.hexdigest()[:10]
        blk.block.dxf.description = sig[name]
    doc.saveas(str(out))
    return sig


def load_blocks_code(dxf: Path, sig: dict[str, str]) -> str:
    """LISP: одноимённые блоки другой версии переименовать в "имя (старый N)", вставить файл с блоками и убрать вставку.
    Результат: "загружено N" или ошибка."""
    def s(t):
        return '"' + t.replace("\\", "\\\\").replace('"', '\\"') + '"'
    fname = dxf.stem
    out = ["(vl-load-com) (setq blks (vla-get-Blocks (vla-get-ActiveDocument (vlax-get-acad-object))) ren 0)"]
    for name, sg in sorted(sig.items()):
        out.append(f'(vl-catch-all-apply (function (lambda () '
                   f'(if (tblsearch "BLOCK" {s(name)}) (progn (setq blk (vla-item blks {s(name)})) '
                   f'(if (/= (vla-get-Comments blk) {s(sg)}) (progn (setq k 1) '
                   f'(while (tblsearch "BLOCK" (strcat {s(name)} " (старый " (itoa k) ")")) (setq k (1+ k))) '
                   f'(vla-put-Name blk (strcat {s(name)} " (старый " (itoa k) ")")) (setq ren (1+ ren)))))))))')
    out.append(f'(setq old (getvar "CMDECHO") last0 (entlast)) (setvar "CMDECHO" 0) '
               f'(command "_.-INSERT" {s(dxf.as_posix())} "0,0" "1" "1" "0") '
               f'(if (not (equal (entlast) last0)) (entdel (entlast))) (setvar "CMDECHO" old) '
               f'(vl-catch-all-apply (function (lambda () (vla-delete (vla-item blks {s(fname)})))))')
    names = " ".join(s(n) for n in sorted(sig))
    out.append(f'(setq miss (vl-remove-if (function (lambda (n) (tblsearch "BLOCK" n))) (list {names})))')
    return ("(progn " + "\n".join(out) +
            ' (strcat "переименовано " (itoa ren) (if miss (strcat " НЕТ: " (apply (function strcat) '
            '(mapcar (function (lambda (x) (strcat x "; "))) miss))) "")))')


# ------------------------------------------------------------------ сверка
def audit(nodes: list[LibNode], system) -> list[str]:
    """Описки: деталь стоит иначе, чем в большинстве узлов того же вида.
    Сравнение - от своей опорной линии (носик профиля или наружная грань стекла), с учётом прижима."""
    known = _known(system)
    clamps = {R._art(r["Прижим"]) for r in system.kits}
    groups: dict[tuple, list] = {}
    for n in nodes:
        g_out = n.g_in + n.thick
        clamp = next((p.name for _, p in n.items if label(p.name, known) in clamps), "")
        for name, p in n.items:
            if p.x < -1 and n.edge:                    # сторона доборных - у каждого набора своя
                continue
            q = p if p.x >= -0.5 else R._mirror_x(p)
            if name.startswith("ВС 4"):
                continue
            rot = (round(q.rot) % 360, q.mirror)
            key = (name, n.beam, clamp if not name.startswith("ТПУ") else "", rot)
            groups.setdefault(key, []).append((n, round(q.x, 1), round(q.y, 1), round(q.y - g_out, 1), p))
    out = []
    for (name, beam, clamp, rot), lst in groups.items():
        if len(lst) < 4:
            continue
        # опорная линия - та, от которой положение одинаково у большинства
        best = None
        for idx in (2, 3):
            c = Counter((v[1], v[idx]) for v in lst)
            top, cnt = c.most_common(1)[0]
            if best is None or cnt > best[1]:
                best = (idx, cnt, top)
        idx, cnt, (bx, by) = best
        if cnt < 0.6 * len(lst):
            continue                                   # закономерности нет - не судим
        for n, x, y_n, y_g, p in lst:
            y = y_n if idx == 2 else y_g
            d = max(abs(x - bx), abs(y - by))
            if 0.25 < d < 15:
                out.append(f"{name} ({'ригель' if beam else 'стойка'}, {n.variant} {n.thick} мм): сдвинут на {d:.1f} мм "
                           f"относительно {cnt} других узлов - {n.where}")
    return sorted(set(out))
