"""Несколько витражей в одном чертеже.

Витраж = связная группа осей (стойки и ригели, которые касаются друг друга).
Исходные данные витража - блок ФАСАД_КОНСТРУКЦИЯ с атрибутами, вставленный рядом с осями витража
(заполняется окном "Исходные данные" при запуске, можно править и в AutoCAD).
Блок относится к витражу, в габарит которого (с запасом) попадает его точка вставки.

Таблица профилей витража - строки-блоки ФАСАД_ПРОФИЛЬ (марка, артикул, длина, кол.) справа от блока
конструкции. Артикул в строке можно поменять - при следующем построении деталь этой марки станет
этим артикулом. Таблицу рисует программа на своём слое ФАСАД_ПРОФИЛИ.
"""

from __future__ import annotations

from dataclasses import fields, replace

from .facade import TOL, Axis, Facade, ObjectParams, _split_axes
from .lisp_draw import AXES_FILE, _layer, _n, _s

BLOCK = "ФАСАД_КОНСТРУКЦИЯ"
BLOCK_LAYER = "ФАСАД_КОНСТРУКЦИИ"     # не MARK_* - программа этот слой не стирает
ROW_BLOCK = "ФАСАД_ПРОФИЛЬ"
TABLE_LAYER = "ФАСАД_ПРОФИЛИ"         # таблицы профилей - программа стирает и рисует заново

# тег, подпись, значение по умолчанию, поле ObjectParams
ATTRS = [
    ("ИМЯ", "Марка конструкции", "ВТ-1", "имя"),
    ("КОЛИЧЕСТВО", "Количество, шт", "1", "количество"),
    ("СИСТЕМА", "Система", "КП50К", "система"),
    ("СТОЙКА", "Стойка", "", "стойка"),
    ("РИГЕЛЬ", "Ригель", "", "ригель"),
    ("ЗАПОЛНЕНИЕ", "Толщина заполнения, мм", "50", "заполнение"),
    ("ФОРМУЛА", "Формула стеклопакета", "6-16-6-16-6", "формула"),
    ("ВАРИАНТ", "Вариант комплектации", "Adifoam 530.121", "вариант"),
    ("ПЕРИМЕТР_ВАРИАНТ", "Вариант доборных периметра", "1", "вариант_периметра"),
    ("ДОБОРНЫЕ", "Доборные: пластик / алюминий", "как в каталоге", "доборные"),
    ("ВЫПУСК_НИЗ", "Выпуск стоек вниз, мм", "20", "выпуск_низ"),
    ("ВЫПУСК_ВЕРХ", "Выпуск стоек вверх, мм", "10", "выпуск_верх"),
    ("МОНТАЖ", "Монтаж (в проем / навесной)", "в проем", "монтаж"),
    ("ЗАКЛАДНАЯ_НИЗ", "Закладная стойки низ, мм (0 - нет)", "150", "закладная_стойки_низ"),
    ("ЗАКЛАДНАЯ_ВЕРХ", "Закладная стойки верх, мм (0 - нет)", "150", "закладная_стойки_верх"),
    ("ПЕРИМЕТР_ЛЕВО", "Доборные слева (да/нет)", "да", "периметр_лево"),
    ("ПЕРИМЕТР_ПРАВО", "Доборные справа (да/нет)", "да", "периметр_право"),
    ("ПЕРИМЕТР_ВЕРХ", "Доборные сверху (да/нет)", "да", "периметр_верх"),
    ("ПЕРИМЕТР_НИЗ", "Доборные снизу (да/нет)", "да", "периметр_низ"),
    ("КАПЕЛЬНИК", "Капельники (да/нет)", "да", "капельник"),
    ("ОТМЕТКА_НИЗ", "Отметка низа витража, м", "0,000", "отметка_низ"),
]

H, ROW, W1, W2 = 50.0, 80.0, 1300.0, 2000.0         # блок конструкции: высота текста, строка, колонки
VERSION_TAG = "ВЕРСИЯ_4"      # скрытый атрибут: поменять при изменении вида блока - определение пересоберётся
BLOCK_W = W1 + W2
# строка таблицы профилей: тег, x колонки, видимый
ROW_ATTRS = [("МАРКА", 20, True), ("АРТИКУЛ", 520, True), ("ДЛИНА", 1020, True), ("КОЛ", 1320, True),
             ("ВИТРАЖ", 20, False), ("БАЗА", 20, False)]
ROW_W = 1500.0
TABLE_GAP = 200.0                                    # от блока конструкции до таблицы


def _vla_block(name: str, ents: list[str], tags: list[str]) -> str:
    """LISP: определение блока через ActiveX. Если набор атрибутов другой - содержимое пересобирается."""
    want = " ".join(_s(t) for t in sorted(tags))
    return f'''
(progn (vl-load-com)
  (setq blks (vla-get-Blocks (vla-get-ActiveDocument (vlax-get-acad-object))))
  (setq blk (if (tblsearch "BLOCK" {_s(name)}) (vla-item blks {_s(name)}) (vla-Add blks (vlax-3d-point 0 0 0) {_s(name)})))
  (setq tags nil olds nil)
  (vlax-for e blk (setq olds (cons e olds))
    (if (= (vla-get-ObjectName e) "AcDbAttributeDefinition") (setq tags (cons (vla-get-TagString e) tags))))
  (if (not (equal (vl-sort tags (function <)) (list {want})))
    (progn (foreach e olds (vla-Delete e)) {" ".join(ents)} "обновлён") "есть"))'''


def _vla_rect(x0, y0, x1, y1) -> str:
    pts = " ".join(_n(v) for v in (x0, y0, x1, y0, x1, y1, x0, y1))
    return (f'(vla-put-Closed (vla-AddLightWeightPolyline blk (vlax-make-variant (vlax-safearray-fill '
            f'(vlax-make-safearray vlax-vbDouble (cons 0 7)) (list {pts})))) :vlax-true)')


def _vla_text(x, y, h, text) -> str:
    return f'(vla-AddText blk {_s(text)} (vlax-3d-point {_n(x)} {_n(y)} 0) {_n(h)})'


def _vla_attdef(x, y, h, tag, prompt, default, visible=True) -> str:
    return (f'(vla-AddAttribute blk {_n(h)} {0 if visible else 1} {_s(prompt)} (vlax-3d-point {_n(x)} {_n(y)} 0) '
            f'{_s(tag)} {_s(default)})')


def define_block_code() -> str:
    """LISP: блоки ФАСАД_КОНСТРУКЦИЯ (табличка: подпись слева, значение справа) и ФАСАД_ПРОФИЛЬ (строка)."""
    total_h = ROW * (len(ATTRS) + 1)
    ents = [_vla_rect(0, -total_h, BLOCK_W, 0), _vla_text(20, -ROW + 20, H, "Конструкция (ФАСАД)")]
    for k, (tag, label, default, _) in enumerate(ATTRS, 1):
        y = -ROW * (k + 1) + 20
        ents.append(_vla_text(20, y, H * 0.7, label))
        ents.append(_vla_attdef(W1 + 20, y, H * 0.8, tag, label, default))
    ents.append(_vla_attdef(20, -total_h + 20, H * 0.5, VERSION_TAG, "версия блока", "", False))
    row = [_vla_rect(0, -ROW, ROW_W, 0)]
    row += [_vla_attdef(x, -ROW + 20, H * 0.8, tag, tag.capitalize(), "", vis) for tag, x, vis in ROW_ATTRS]
    return (f'(strcat {_vla_block(BLOCK, ents, [a[0] for a in ATTRS] + [VERSION_TAG])} " " '
            f'{_vla_block(ROW_BLOCK, row, [a[0] for a in ROW_ATTRS])})')


def _vla_insert(name: str, layer: str, x: float, y: float, values: dict[str, str]) -> str:
    vals = " ".join(f"(cons {_s(k)} {_s(str(v))})" for k, v in values.items())
    return (f'(progn (setq o (vla-InsertBlock ms (vlax-3d-point {_n(x)} {_n(y)} 0) {_s(name)} 1 1 1 0) '
            f'vals (list {vals})) (vla-put-Layer o {_s(layer)}) '
            f'(foreach a (vlax-invoke o (quote GetAttributes)) '
            f'(if (setq v (assoc (vla-get-TagString a) vals)) (vla-put-TextString a (cdr v)))))')


def _ms() -> str:
    return '(vl-load-com) (setq ms (vla-get-ModelSpace (vla-get-ActiveDocument (vlax-get-acad-object))))'


def write_block_code(x: float, y: float, values: dict[str, str], handle: str = "") -> str:
    """LISP: записать исходные данные витража - старый блок (по handle) удалить, вставить новый со значениями."""
    vals = {tag: values.get(tag, default) for tag, _, default, _ in ATTRS}
    erase = f'(if (setq en (handent {_s(handle)})) (entdel en))' if handle else ""
    # ошибки AutoCAD - перехватить и вернуть текстом: "ок" или "ОШИБКА ..."
    return (f'(progn {_layer(BLOCK_LAYER, 4)} {_ms()} '
            f'(setq r (vl-catch-all-apply (function (lambda () {_vla_insert(BLOCK, BLOCK_LAYER, x, y, vals)})))) '
            f'(if (vl-catch-all-error-p r) (strcat "ОШИБКА вставки: " (vl-catch-all-error-message r)) '
            f'(progn {erase} "ок")))')


def insert_block_code(x: float, y: float, values: dict[str, str]) -> str:
    """LISP: вставить блок конструкции со значениями атрибутов (для опытов)."""
    return write_block_code(x, y, values)


def _read_inserts(block: str, prefix: str) -> str:
    return f'''
  (if (setq ss (ssget "X" (list (cons 0 "INSERT") (cons 2 {_s(block)}) (cons 410 "Model"))))
    (repeat (setq i (sslength ss))
      (setq en (ssname ss (setq i (1- i))) e (entget en) p (cdr (assoc 10 e)) n (1+ n))
      (write-line (strcat "{prefix} " (rtos (car p) 2 4) " " (rtos (cadr p) 2 4) " " (cdr (assoc 5 e))) fp)
      (setq a (entnext en))
      (while (and a (= (cdr (assoc 0 (setq ae (entget a)))) "ATTRIB"))
        (write-line (strcat "A " (cdr (assoc 2 ae)) "=" (cdr (assoc 1 ae))) fp)
        (setq a (entnext a)))))'''


def read_blocks_code() -> str:
    """LISP: дописать в файл осей блоки конструкций ('K x y handle') и строки таблиц профилей ('P x y handle'),
    после каждого - атрибуты 'A ТЕГ=значение'."""
    return f'''
(progn
  (setq fp (open {_s(AXES_FILE.as_posix())} "a") n 0)
  {_read_inserts(BLOCK, "K")}
  {_read_inserts(ROW_BLOCK, "P")}
  (close fp)
  n)'''


def parse_blocks(lines: list[str], prefix: str = "K") -> list[tuple[float, float, dict, str]]:
    """(x, y, атрибуты, handle) блоков с этим префиксом."""
    out, cur = [], None
    for ln in lines:
        head = ln.split(" ", 1)[0]
        if head in ("K", "P"):
            f = ln.split()
            cur = None
            if head == prefix:
                cur = (float(f[1]), float(f[2]), {}, f[3] if len(f) > 3 else "")
                out.append(cur)
        elif head == "A" and cur is not None:
            tag, _, val = ln[2:].partition("=")
            cur[2][tag.strip()] = val.strip()
    return out


def parse_profile_tables(lines: list[str]) -> dict[str, dict[str, tuple[str, tuple[str, float]]]]:
    """Таблицы профилей: витраж -> {марка: (артикул, (артикул по правилам, длина по правилам))}.
    БАЗА в строке - скрытый атрибут "артикул|длина" (по правилам, до правки)."""
    out: dict[str, dict] = {}
    for _, _, a, _ in parse_blocks(lines, "P"):
        b_art, _, b_len = a.get("БАЗА", "").partition("|")
        try:
            L = float(b_len.replace(",", ".") or 0)
        except ValueError:
            L = 0.0
        for mark in a.get("МАРКА", "").split(","):
            if mark.strip():
                out.setdefault(a.get("ВИТРАЖ", ""), {})[mark.strip()] = (a.get("АРТИКУЛ", "").strip(),
                                                                        (b_art.strip(), L))
    return out


def set_profile_code(edits: list[tuple[str, str, str]]) -> str:
    """LISP: в таблицах профилей заменить артикул у строк заданных марок.
    edits - [(витраж, марка, новый артикул)]. Результат: "правок N"."""
    def s(t):
        return '"' + t.replace("\\", "\\\\").replace('"', '\\"') + '"'

    cases = " ".join(
        f'((and (= марка {s(mark)}) (= витраж {s(vitrage)})) '
        f'(progn (entmod (subst (cons 1 {s(art)}) (assoc 1 cell) cell)) (setq n (1+ n))))'
        for vitrage, mark, art in edits)
    return (f'(progn (setq n 0) '
            f'(if (setq ss (ssget "_X" (list (cons 0 "INSERT") (cons 8 {_s(TABLE_LAYER)}) (cons 2 {_s(ROW_BLOCK)})))) '
            f'(progn (setq i 0) '
            f'(while (< i (sslength ss)) '
            f'(setq en (ssname ss i) sub (entnext en) марка nil витраж nil cell nil) '
            f'(while (and sub (= "ATTRIB" (cdr (assoc 0 (setq ae (entget sub)))))) '
            f'(cond ((= "МАРКА" (cdr (assoc 2 ae))) (setq марка (cdr (assoc 1 ae)))) '
            f'((= "ВИТРАЖ" (cdr (assoc 2 ae))) (setq витраж (cdr (assoc 1 ae)))) '
            f'((= "АРТИКУЛ" (cdr (assoc 2 ae))) (setq cell ae))) '
            f'(setq sub (entnext sub))) '
            f'(if (and марка витраж cell) (cond {cases} (t nil))) '
            f'(setq i (1+ i))))) '
            f'(strcat "правок " (itoa n)))')


def clear_tables_code() -> str:
    """LISP: стереть таблицы профилей (свой слой программы)."""
    return (f'(progn (if (setq ss (ssget "X" (list (cons 8 {_s(TABLE_LAYER)}) (cons 410 "Model")))) '
            f'(command "_.ERASE" ss "")) (princ))')


def table_code(bx: float, by: float, name: str, fac: Facade) -> str:
    """LISP: таблица профилей витража справа от блока конструкции (bx, by - точка вставки блока)."""
    x0, y = bx + BLOCK_W + TABLE_GAP, by
    out = [_layer(TABLE_LAYER, 4), _ms()]

    def txt(x, yy, h, s):
        out.append(f'(entmake (list (cons 0 "TEXT") (cons 8 {_s(TABLE_LAYER)}) (list 10 {_n(x)} {_n(yy)} 0.0) '
                   f'(cons 40 {_n(h)}) (cons 1 {_s(s)})))')

    txt(x0 + 20, y - ROW + 20, H, f"Профили {name}")
    txt(x0 + 20, y - 2 * ROW + 20, H * 0.6, "артикул можно менять; ригель из стойки - вписать артикул стойки")
    y -= 2 * ROW
    out.append(f'(entmake (list (cons 0 "LWPOLYLINE") (cons 100 "AcDbEntity") (cons 8 {_s(TABLE_LAYER)}) '
               f'(cons 100 "AcDbPolyline") (cons 90 4) (cons 70 1) (list 10 {_n(x0)} {_n(y)}) '
               f'(list 10 {_n(x0 + ROW_W)} {_n(y)}) (list 10 {_n(x0 + ROW_W)} {_n(y - ROW)}) (list 10 {_n(x0)} {_n(y - ROW)})))')
    for tag, x, vis in ROW_ATTRS:
        if vis:
            txt(x0 + x, y - ROW + 20, H * 0.8, {"КОЛ": "Кол."}.get(tag, tag.capitalize()))
    for r in fac.profile_rows:
        y -= ROW
        vals = {"МАРКА": r["марка"], "АРТИКУЛ": r["артикул"], "ДЛИНА": f'{r["длина"]:g}', "КОЛ": str(r["кол"]),
                "ВИТРАЖ": name, "БАЗА": f'{r["база"][0]}|{r["база"][1]:g}'}
        out.append(_vla_insert(ROW_BLOCK, TABLE_LAYER, x0, y, vals))
    return "(progn " + "\n".join(out) + " (princ))"


def _bool(v: str) -> bool:
    return v.strip().lower() not in ("нет", "no", "0", "-", "false", "")


def _num(v: str) -> float:
    return float(v.replace(",", ".").replace("±", "").replace("+", "").strip() or 0)


def params_from_attrs(defaults: ObjectParams, attrs: dict[str, str]) -> tuple[ObjectParams, list[str]]:
    """Атрибуты блока -> параметры объекта. Неверные значения - предупреждение и значение по умолчанию."""
    types = {f.name: f.type for f in fields(ObjectParams)}
    changes, warn = {}, []
    for tag, label, _, field_name in ATTRS:
        if tag not in attrs:
            continue
        v = attrs[tag]
        try:
            ftype = str(types[field_name])
            if "bool" in ftype:
                changes[field_name] = _bool(v)
            elif field_name in ("количество", "заполнение"):
                changes[field_name] = int(_num(v))
            elif "float" in ftype or "int" in ftype:
                changes[field_name] = _num(v)
            else:
                changes[field_name] = v
        except ValueError:
            warn.append(f"Атрибут {tag}=\"{v}\" не понят - взято значение по умолчанию")
    return replace(defaults, **changes), warn


def split_axes(axes: list[Axis], reach: float) -> list[list[Axis]]:
    """Разбить оси на витражи: связные группы касающихся стоек и ригелей."""
    items = [("s", a) for a in axes if abs(a.x1 - a.x2) < TOL] + [("r", a) for a in axes if abs(a.y1 - a.y2) < TOL]
    parent = list(range(len(items)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, (ki, a) in enumerate(items):
        if ki != "r":
            continue
        y, bx1, bx2 = a.y1, min(a.x1, a.x2), max(a.x1, a.x2)
        for j, (kj, b) in enumerate(items):
            if kj != "s":
                continue
            px, py1, py2 = b.x1, min(b.y1, b.y2), max(b.y1, b.y2)
            if bx1 - reach <= px <= bx2 + reach and py1 - reach <= y <= py2 + reach:
                parent[find(i)] = find(j)
    groups: dict[int, list[Axis]] = {}
    for i, (_, a) in enumerate(items):
        groups.setdefault(find(i), []).append(a)
    # слева направо, снизу вверх
    return sorted(groups.values(), key=lambda g: (min(min(a.x1, a.x2) for a in g), min(min(a.y1, a.y2) for a in g)))


def bbox(group: list[Axis]) -> tuple[float, float, float, float]:
    xs = [v for a in group for v in (a.x1, a.x2)]
    ys = [v for a in group for v in (a.y1, a.y2)]
    return min(xs), min(ys), max(xs), max(ys)


BLOCK_H = ROW * (len(ATTRS) + 1)
BLOCK_ABOVE = BLOCK_H + 600          # точка блока над верхней осью: низ блока на 600 выше витража


def assign_blocks(groups: list[list[Axis]], blocks: list[tuple], margin: float = 2500):
    """Каждому витражу - блок, точка вставки которого ближе всего (в пределах габарита + запас;
    сверху запас больше - там стоит блок конструкции)."""
    result = []
    for g in groups:
        x0, y0, x1, y1 = bbox(g)
        cands = [b for b in blocks if x0 - margin <= b[0] <= x1 + margin
                 and y0 - margin <= b[1] <= y1 + BLOCK_ABOVE + margin]
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        cands.sort(key=lambda b: (b[0] - cx) ** 2 + (b[1] - cy) ** 2)
        result.append(cands[0] if cands else None)
    return result
