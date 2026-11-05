"""Доборные периметра из каталога (03 "Основные сечения", раздел "Сечения крайних стоек и ригелей", стр. 73-79).

Ряд раздела - одна толщина (и вариант доборных): слева узел крайней стойки, справа - крайнего ригеля (повернуто).
С одной стороны оси - обычное стекло, с другой - пакет доборных до прижима. Доборные находятся сравнением
сторон: всё, что со стороны края не повторяет зеркально сторону стекла, - доборное (сюда попадает и уплотнитель
стойки, переставленный на доборный). Что со стороны стекла не повторено у края - убрать из узла у края.

Система координат - как у узлов базы (catalog_nodes): x от оси, y от носика, наружу +y; край витража - x < 0.
Артикулы - по таблице периметра (КП50К_периметр.csv) по порядку от стойки к прижиму.
"""

from __future__ import annotations

import re

from .catalog_nodes import SCALE, Shape, _inside, _key, _same, _shapes
from .facade import parse_items

X_FROM, X_TO = 5040, 6550        # раздел на листе 03 (единицы листа)
LABEL_TO_POST = -10.0            # ось стойки от подписи "Заполнение N мм" (единицы листа)
POST_TO_RIEGEL = 54.1            # ось ригеля от оси стойки
NEAR_AXIS = 13.0                 # мм: незамкнутые куски ближе к оси - контур термовставки, доборные дальше (~19)
SKIP_LAYERS ={"Glass", "Зигзаг", "Конструкции", "Dimentions", "Скрытые", "1", "2", "Герлен"}


def _rows(msp) -> list[dict]:
    """Ряды раздела: толщина, вариант (по порядку рядов одной толщины), оси стойки и ригеля, высота ряда."""
    labels = []
    for e in msp.query("TEXT MTEXT"):
        x = e.dxf.insert.x
        if not X_FROM <= x <= X_TO:
            continue
        t = e.plain_text() if e.dxftype() == "MTEXT" else e.dxf.text
        m = re.match(r"\s*Заполнение\s+(\d+)\s*мм", t)
        if m:
            labels.append((x, e.dxf.insert.y, int(m.group(1))))
    axes = []
    for e in msp.query("LINE"):
        s, t = e.dxf.start, e.dxf.end
        if e.dxf.layer == "1" and X_FROM <= s.x <= X_TO and abs(s.x - t.x) < 0.01 and abs(s.y - t.y) > 10:
            axes.append((s.x, min(s.y, t.y), max(s.y, t.y)))
    rows = []
    cols = []                                          # колонки листов (подписи в колонке сдвинуты до 1 ед.)
    for x in sorted(l[0] for l in labels):
        if not cols or x - cols[-1] > 3:
            cols.append(x)
    for lx in cols:
        col = [l for l in labels if abs(l[0] - lx) < 3]
        px = lx + LABEL_TO_POST
        posts = sorted((a for a in axes if abs(a[0] - px) < 2), key=lambda a: -a[2])
        merged = []                                    # ось бывает из двух отрезков - слить
        for a in posts:
            if merged and a[1] < merged[-1][2] and a[2] > merged[-1][1]:
                m = merged[-1]
                merged[-1] = (m[0], min(m[1], a[1]), max(m[2], a[2]))
            else:
                merged.append(a)
        for x, lo, hi in merged:
            above = [l for l in col if l[1] > hi]
            if not above:
                continue
            lab = min(above, key=lambda l: l[1])
            rb = [a for a in axes if abs(a[0] - (x + POST_TO_RIEGEL)) < 2 and a[1] < hi and a[2] > lo]
            rows.append({"толщина": lab[2], "стойка_x": x, "ригель_x": rb[0][0] if rb else x + POST_TO_RIEGEL,
                         "низ": lo, "верх": hi, "колонка": lx})
    rows.sort(key=lambda r: (r["колонка"], -r["верх"]))
    seen: dict[int, int] = {}
    for r in rows:
        seen[r["толщина"]] = seen.get(r["толщина"], 0) + 1
        r["вариант"] = str(seen[r["толщина"]])
    return rows


def _node_side(msp, ax: float, lo: float, hi: float, edge_right: bool) -> dict:
    """Узел одной стороны ряда (стойка или ригель): доборные, что убрать у края, замечания."""
    shapes, axis, _ = _shapes(msp, ax - 27, lo - 4, ax + 27, hi + 4)
    a = min(((b[0] + b[2]) / 2 for b in axis), key=lambda v: abs(v - ax * SCALE), default=ax * SCALE)
    post = max((s for s in shapes if s.layer == "0" and not s.closed), key=lambda s: len(s.pts), default=None)
    wide = [s for s in shapes if s.layer == "0" and s.closed and s.box[2] - s.box[0] > 30]
    if not post or not wide:
        raise ValueError("не найдены профиль или крышка")
    pc = (post.box[1] + post.box[3]) / 2
    cover = max(wide, key=lambda s: abs((s.box[1] + s.box[3]) / 2 - pc))
    up = 1 if (cover.box[1] + cover.box[3]) > 2 * pc else -1
    tip = post.box[3] if up == 1 else post.box[1]
    k = -1 if edge_right else 1                         # край витража -> x < 0

    def to_node(s: Shape) -> Shape:
        pts = [(k * (x - a), (y - tip) * up, bl * k * up) for x, y, bl in s.pts]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        return Shape(s.layer, pts, s.closed, (min(xs), min(ys), max(xs), max(ys)), s.group)

    # пластиковые Т50-xx начерчены незамкнутой линией со штриховкой; незамкнутые на "0" - линии самого профиля
    cand = [to_node(s) for s in shapes if (s.closed or s.layer != "0") and s.layer not in SKIP_LAYERS]
    # через ось - общие детали узла; куски их контура (термовставка начерчена из кусков) - тоже их
    central = [s.box for s in cand if s.box[0] < -0.3 and s.box[2] > 0.3]
    cand = [s for s in cand if not (s.box[0] < -0.3 and s.box[2] > 0.3)
            and s.box[2] - s.box[0] > 0.5 and s.box[3] - s.box[1] > 0.5        # обрывки линий
            and not any(_inside(s.box, b, 0.5) for b in central)
            and (s.closed or abs(s.box[0] + s.box[2]) / 2 > NEAR_AXIS)]
    c = lambda s: ((s.box[0] + s.box[2]) / 2, (s.box[1] + s.box[3]) / 2)
    left = [s for s in cand if c(s)[0] < 0]
    right = [s for s in cand if c(s)[0] > 0]
    matched_r = set()
    extra = []
    for s in left:
        ks = _key(s.pts, True)
        hit = next((i for i, r in enumerate(right) if i not in matched_r and abs(c(r)[0] + c(s)[0]) < 0.4
                    and abs(c(r)[1] - c(s)[1]) < 0.4 and _same(ks, _key(r.pts))), None)
        if hit is None:
            extra.append(s)
        else:
            matched_r.add(hit)
    gone_r = [r for i, r in enumerate(right) if i not in matched_r]
    gone = [(-c(r)[0], c(r)[1], r.layer) for r in gone_r]
    # пакет доборных - между стойкой и прижимом; дальше прижима - не он
    clamp = sorted(wide, key=lambda s: -abs((s.box[1] + s.box[3]) / 2 - pc))[1:2] or [cover]
    face = to_node(clamp[0]).box[1]
    extra = [s for s in extra if c(s)[1] < face]
    # детали: внешние контуры; отверстия и обрезки (габарит большей частью внутри большего) - к своей детали
    area = lambda b: max(b[2] - b[0], 0) * max(b[3] - b[1], 0)

    def overlap(a, b):
        return area((max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])))

    parts = []
    for s in sorted(extra, key=lambda s: -area(s.box)):
        hosts = [p for p in parts if _inside(s.box, p[0].box) or
                 p[0].layer == s.layer and overlap(s.box, p[0].box) >= 0.6 * area(s.box)]
        # детали пакета вложены друг в друга - отверстие принадлежит самой маленькой из охватывающих
        host = min(hosts, key=lambda p: area(p[0].box), default=None)
        if host:
            host.append(s)
        else:
            parts.append([s])
    # переставленная деталь узла (уплотнитель стойки на доборном): та же форма, что пропала со стороны стекла
    moved = [any(_same(_key(p[0].pts, True), _key(r.pts)) for r in gone_r) for p in parts]
    order = sorted(range(len(parts)), key=lambda i: c(parts[i][0])[1])
    return {"детали": [parts[i] for i in order], "переставлен": [moved[i] for i in order], "убрать": gone}


def _names(text: str) -> list[tuple[str, str]]:
    """'КПС 296 (Т50-04) x2 + Т50-06' -> [('КПС 296','Т50-04'), ('КПС 296','Т50-04'), ('Т50-06','')]."""
    return [(a, n) for a, n, q in parse_items(text) for _ in range(q)]


def extract_perimeter(dxf_path, perimeter_rows: list[dict], sizes: dict[str, float] | None = None,
                      seals: dict | None = None):
    """Все ряды раздела -> ({"толщина/вариант": {"стойка"|"ригель": {...}}}, замечания).

    Деталь: артикул (прорисован), аналог (в скобках), контуры от центра габарита, центр, габарит."""
    import ezdxf
    msp = ezdxf.readfile(str(dxf_path)).modelspace()
    sizes = sizes or {}
    out, notes = {}, []
    table = {(int(r["Толщина"]), r["Вариант"]): r for r in perimeter_rows}
    for row in _rows(msp):
        key = (row["толщина"], row["вариант"])
        tr = table.get(key)
        tag = f'{row["толщина"]} мм, вариант {row["вариант"]}'
        if not tr:
            notes.append(f"{tag}: ряд в каталоге есть, в таблице периметра нет")
            continue
        res = {}
        for side, x, col, right in (("стойка", row["стойка_x"], "Крайняя стойка", False),
                                    ("ригель", row["ригель_x"], "Крайний ригель", True)):
            try:
                node = _node_side(msp, x, row["низ"], row["верх"], right)
            except ValueError as e:
                notes.append(f"{tag}, {side}: {e}")
                continue
            names = _names(tr[col])
            got = node["детали"]
            # переставленный уплотнитель - по уплотнителю ТПУ из таблицы (или обычный уплотнитель узла);
            # уплотнитель ТПУ из таблицы, оставшийся на месте (Спейсер 32 = стекло 32), в узле не ищем
            own_seal = (seals or {}).get("ригели" if side == "ригель" else "стойки", "уплотнитель")
            seal_names = [n for n in names if n[0].upper().startswith("ТПУ")]
            prof_names = [n for n in names if not n[0].upper().startswith("ТПУ")]
            named = []
            for p, mv in zip(got, node["переставлен"]):
                if mv:
                    named.append(seal_names.pop(0) if seal_names else (own_seal, ""))
                elif prof_names:
                    named.append(prof_names.pop(0))
                elif seal_names:
                    named.append(seal_names.pop(0))
                else:
                    named.append((f"доборный {len(named) + 1}", ""))
            if prof_names or len(named) < len(got) or any(a.startswith("доборный ") for a, _ in named):
                notes.append(f"{tag}, {side}: в таблице {tr[col]}, в каталоге найдено {len(got)} шт. - "
                             f"не сошлось: " + ", ".join(a for a, _ in named))
            parts = []
            for i, p in enumerate(got):
                art, alt = named[i]
                b = (min(s.box[0] for s in p), min(s.box[1] for s in p), max(s.box[2] for s in p),
                     max(s.box[3] for s in p))
                cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
                h = b[3] - b[1]                         # толщина - поперёк витража (по y узла)
                if art in sizes and abs(sizes[art] - h) > 3:
                    notes.append(f"{tag}, {side}: {art} по таблице {sizes[art]:g} мм, на чертеже {h:.1f} мм")
                parts.append({"артикул": art, "аналог": alt, "x": round(cx, 3), "y": round(cy, 3),
                              "ш": round(b[2] - b[0], 2), "в": round(h, 2), "слой": p[0].layer,
                              "контуры": [[(x - cx, y - cy, bl) for x, y, bl in s.pts] for s in p],
                              "замкнут": [s.closed for s in p]})
            res[side] = {"детали": parts, "убрать": [[round(gx, 3), round(gy, 3)] for gx, gy, _ in node["убрать"]],
                         "источник": f'лист 03, x={x:.0f}, y={row["низ"]:.0f}..{row["верх"]:.0f}'}
        out[f'{row["толщина"]}/{row["вариант"]}'] = res
    for (t, v), r in table.items():
        if f"{t}/{v}" not in out:
            notes.append(f"{t} мм, вариант {v}: в каталоге (лист 03) ряд не найден")
    return out, notes
