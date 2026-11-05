"""Перевод результата построения в код AutoLISP (entmake) и чтение осей из AutoCAD."""

from __future__ import annotations

import re
from pathlib import Path

from .facade import Axis, Facade, System

AXES_FILE = Path("C:/temp/fasad_axes.txt")
POST_AXIS_LAYER = "OSs"
BEAM_AXIS_LAYER = "OSr"
FALSE_AXIS_LAYER = "OSf"          # фальш-ригель (КПС 009) - за стеклом, стекло не делит
OPENING_LAYER = "проемы"       # метки дверей/окон в ячейках: текст "ДВЕРЬ СТ71"


def _s(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _n(v: float) -> str:
    return f"{float(v):.4f}"


def _layer(name: str, color: int) -> str:
    return (f'(if (not (tblsearch "LAYER" {_s(name)})) (entmake (list (cons 0 "LAYER") '
            f'(cons 100 "AcDbSymbolTableRecord") (cons 100 "AcDbLayerTableRecord") '
            f'(cons 2 {_s(name)}) (cons 70 0) (cons 62 {color}))))')


def _poly(layer: str, points, linetype: str = "", lt_scale: float = 1.0,
          closed: bool = True, width: float = 0.0, widths: tuple | None = None) -> str:
    """Полилиния. Точки (x, y) или (x, y, дуга). widths - ширина первого сегмента (для стрелки)."""
    parts = []
    for k, pt in enumerate(points):
        x, y = pt[0], pt[1]
        s = f"(list 10 {_n(x)} {_n(y)})"
        if widths and k == 0:
            s += f" (cons 40 {_n(widths[0])}) (cons 41 {_n(widths[1])})"
        if len(pt) > 2 and pt[2]:
            s += f" (cons 42 {pt[2]:.8f})"
        parts.append(s)
    # тип линии добавляем, только если он загружен - иначе AutoCAD не создаст объект
    lt = (f'(if (tblsearch "LTYPE" {_s(linetype)}) (list (cons 6 {_s(linetype)}) (cons 48 {_n(lt_scale)})))'
          if linetype else "nil")
    w = f" (cons 43 {_n(width)})" if width and not widths else ""
    return (f'(mk (append (list (cons 0 "LWPOLYLINE") (cons 100 "AcDbEntity") (cons 8 {_s(layer)})) {lt} '
            f'(list (cons 100 "AcDbPolyline") (cons 90 {len(points)}) (cons 70 {1 if closed else 0}){w} '
            f'{" ".join(parts)})))')


def _rect(layer: str, x0, y0, x1, y1, linetype: str = "", lt_scale: float = 1.0) -> str:
    return _poly(layer, ((x0, y0), (x1, y0), (x1, y1), (x0, y1)), linetype, lt_scale)


def _load_linetype(name: str) -> str:
    return (f'(if (not (tblsearch "LTYPE" {_s(name)})) (vl-catch-all-apply (function vla-load) '
            f'(list (vla-get-Linetypes (vla-get-ActiveDocument (vlax-get-acad-object))) {_s(name)} "acadiso.lin")))')


TEXT_STYLE = "ISOCPEUR"


def _style(name: str = TEXT_STYLE, font: str = "isocpeur.ttf") -> str:
    """LISP: создать текстовый стиль (если его нет)."""
    return (f'(if (not (tblsearch "STYLE" {_s(name)})) (entmake (list (cons 0 "STYLE") '
            f'(cons 100 "AcDbSymbolTableRecord") (cons 100 "AcDbTextStyleTableRecord") (cons 2 {_s(name)}) '
            f'(cons 70 0) (cons 40 0.0) (cons 41 1.0) (cons 50 0.0) (cons 71 0) (cons 42 2.5) '
            f'(cons 3 {_s(font)}) (cons 4 ""))))')


def _text(layer: str, x, y, h, text: str, align: str = "center") -> str:
    style = f'(if (tblsearch "STYLE" {_s(TEXT_STYLE)}) (list (cons 7 {_s(TEXT_STYLE)})))'
    if align == "left":
        geo = f"(list (list 10 {_n(x)} {_n(y)} 0.0) (cons 40 {_n(h)}))"
    else:
        geo = (f"(list (list 10 {_n(x)} {_n(y)} 0.0) (list 11 {_n(x)} {_n(y)} 0.0) (cons 40 {_n(h)}) "
               f"(cons 72 1) (cons 73 2))")
    return (f'(mk (append (list (cons 0 "TEXT") (cons 8 {_s(layer)}) (cons 1 {_s(text)})) {style} {geo}))')


def _line(layer: str, x1, y1, x2, y2) -> str:
    return (f'(entmake (list (cons 0 "LINE") (cons 8 {_s(layer)}) '
            f'(list 10 {_n(x1)} {_n(y1)} 0.0) (list 11 {_n(x2)} {_n(y2)} 0.0)))')


def draw_axes_code(posts: list[tuple], beams: list[tuple]) -> str:
    """posts: (x, y1, y2); beams: (y, x1, x2)."""
    out = [_layer(POST_AXIS_LAYER, 10), _layer(BEAM_AXIS_LAYER, 10)]
    out += [_line(POST_AXIS_LAYER, x, y1, x, y2) for x, y1, y2 in posts]
    out += [_line(BEAM_AXIS_LAYER, x1, y, x2, y) for y, x1, x2 in beams]
    return "(progn " + "\n".join(out) + "\n(princ))"


def read_axes_code() -> str:
    """LISP: выгрузить линии осей (L x1 y1 x2 y2 слой) и метки проёмов (T x y текст) в текстовый файл.
    Слои осей - только геометрия: OSs, OSr, OSf (фальш-ригель). Артикулы - в исходных данных витража."""
    return f'''
(progn
  (setq fp (open {_s(AXES_FILE.as_posix())} "w") n 0)
  (if (setq ss (ssget "X" (list (cons 0 "LINE") (cons 8 "{POST_AXIS_LAYER},{BEAM_AXIS_LAYER},{FALSE_AXIS_LAYER}")
                                (cons 410 "Model"))))
    (repeat (setq i (sslength ss))
      (setq e (entget (ssname ss (setq i (1- i)))) p1 (cdr (assoc 10 e)) p2 (cdr (assoc 11 e)) n (1+ n))
      (write-line (strcat "L " (rtos (car p1) 2 4) " " (rtos (cadr p1) 2 4) " "
                          (rtos (car p2) 2 4) " " (rtos (cadr p2) 2 4) " " (cdr (assoc 8 e))) fp)))
  (if (setq ss (ssget "X" (list (cons 0 "TEXT,MTEXT") (cons 8 {_s(OPENING_LAYER)}) (cons 410 "Model"))))
    (repeat (setq i (sslength ss))
      (setq e (entget (ssname ss (setq i (1- i)))) p1 (cdr (assoc 10 e)))
      (write-line (strcat "T " (rtos (car p1) 2 4) " " (rtos (cadr p1) 2 4) " " (cdr (assoc 1 e))) fp)))
  (close fp)
  n)'''


def _axes_lines() -> list[str]:
    raw = AXES_FILE.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("cp1251")
    return [ln for ln in text.splitlines() if ln.strip()]


def parse_axes_file() -> list[Axis]:
    out = []
    for ln in _axes_lines():
        if ln.startswith("L "):
            f = ln.split(" ", 5)
            out.append(Axis(*map(float, f[1:5]), f[5].strip() if len(f) > 5 else ""))
    return out


def parse_markers() -> list[tuple[float, float, str]]:
    """Метки проёмов: (x, y, текст). Из MTEXT убираются коды форматирования."""
    out = []
    for ln in _axes_lines():
        if ln.startswith("T "):
            _, x, y, text = ln.split(" ", 3)
            text = re.sub(r"\\[A-Za-z][^;]*;|[{}]|\\P", " ", text).strip()
            out.append((float(x), float(y), text))
    return out


def draw_marker_code(x: float, y: float, text: str, h: float = 80) -> str:
    """LISP: поставить метку проёма (для опытов; обычно метку ставит пользователь)."""
    return (f'(progn {_layer(OPENING_LAYER, 6)} (entmake (list (cons 0 "TEXT") (cons 8 {_s(OPENING_LAYER)}) '
            f'(list 10 {_n(x)} {_n(y)} 0.0) (cons 40 {_n(h)}) (cons 1 {_s(text)}))) (princ))')


GENERATED_LAYERS = "`#*,MARK_*"   # слои, которые рисует программа (` - экранирует #)


def clear_generated_code() -> str:
    """LISP: стереть только то, что нарисовала программа (слои #_..., ##_..., MARK_...)."""
    return (f'(progn (if (setq ss (ssget "X" (list (cons 8 "{GENERATED_LAYERS}") (cons 410 "Model")))) '
            f'(command "_.ERASE" ss "")) (princ))')


def draw_facade_code(fac: Facade, system: System, text_h: float = 50) -> str:
    # mk - создать объект и посчитать удачи/неудачи; результат: "создано не_создано"
    out = ['(vl-load-com) (setq ok 0 bad 0)',
           '(defun mk (e) (if (entmake e) (setq ok (1+ ok)) (setq bad (1+ bad))))', _style()]
    f = system.card.get("оформление", {})
    text_h = f.get("высота_марки", 2.5) * f.get("масштаб", 20)
    parts = [p for p in fac.parts if p.draw]
    layers: dict[str, int] = {}
    for p in parts:
        layers.setdefault(f"#_{p.article}", system.color(p.article))
    for g in fac.glasses:
        layers.setdefault(f"##_{g.kind}", system.color("стекло"))
    layers["MARK_GLASS"] = 3
    layers["MARK_OPEN"] = 6
    out += [_layer(name, color) for name, color in layers.items()]
    out += [_load_linetype(lt) for lt in sorted({p.linetype for p in parts if p.linetype})]
    lt_scale = system.card.get("чертеж", {}).get("масштаб_линии", 1.0)
    out += [_poly(f"#_{p.article}", p.pts, p.linetype, lt_scale) if p.pts
            else _rect(f"#_{p.article}", p.x0, p.y0, p.x1, p.y1, p.linetype, lt_scale) for p in parts]
    for i, g in enumerate(fac.glasses, 1):
        out.append(_rect(f"##_{g.kind}", g.x0, g.y0, g.x1, g.y1))
        cx, cy = (g.x0 + g.x1) / 2, (g.y0 + g.y1) / 2
        out.append(_text("MARK_GLASS", cx, cy + text_h * 0.8, text_h, g.mark or f"{g.kind}_{i:02d}"))
        out.append(_text("MARK_GLASS", cx, cy - text_h * 0.8, text_h, f"{g.width:g}x{g.height:g}"))
    for text, name, w, h, page in fac.openings:
        frame = [p for p in fac.parts if p.where.startswith(text) and p.pts]
        cx = sum(p.x0 + p.x1 for p in frame) / (2 * len(frame))
        cy = max(p.y1 for p in frame) - h / 2
        out.append(_text("MARK_OPEN", cx, cy + text_h * 0.8, text_h, name))
        out.append(_text("MARK_OPEN", cx, cy - text_h * 0.8, text_h, f"рама {w:g}x{h:g}"))
    return "(progn " + "\n".join(out) + '\n(strcat (itoa ok) " " (itoa bad)))'


def zoom_all_code() -> str:
    return '(progn (command "_.ZOOM" "_E") (command "_.ZOOM" "0.9x") (princ))'


def load_blocks_code(lib_file: Path, probe_block: str) -> str:
    """LISP: загрузить определения блоков из файла-библиотеки (вставить файл и сразу удалить вставку)."""
    return (f'(progn (if (not (tblsearch "BLOCK" {_s(probe_block)})) (progn '
            f'(setq old (getvar "CMDECHO") last0 (entlast)) (setvar "CMDECHO" 0) '
            f'(command "_.-INSERT" {_s(lib_file.as_posix())} "0,0" "1" "1" "0") '
            f'(if (not (equal (entlast) last0)) (entdel (entlast))) (setvar "CMDECHO" old))) '
            f'(if (tblsearch "BLOCK" {_s(probe_block)}) "да" "нет"))')


def _insert(layer: str, name: str, x, y, rot_deg: float, mirror: bool) -> str:
    import math
    sx = -1.0 if mirror else 1.0
    return (f'(mk (list (cons 0 "INSERT") (cons 8 {_s(layer)}) (cons 2 {_s(name)}) (list 10 {_n(x)} {_n(y)} 0.0) '
            f'(cons 41 {_n(sx)}) (cons 42 1.0) (cons 43 1.0) (cons 50 {math.radians(rot_deg):.8f})))')


def draw_items_code(items, layer_colors: dict[str, int]) -> str:
    """LISP: нарисовать элементы сечений/закладных (sections.Item). Результат: "создано не_создано"."""
    out = ['(vl-load-com) (setq ok 0 bad 0)',
           '(defun mk (e) (if (entmake e) (setq ok (1+ ok)) (setq bad (1+ bad))))', _style()]
    for name in sorted({it.layer for it in items}):
        out.append(_layer(name, layer_colors.get(name, 7)))
    for it in items:
        if it.kind == "insert":
            out.append(_insert(it.layer, it.place.name, it.place.x, it.place.y, it.place.rot, it.place.mirror))
        elif it.kind == "poly":
            out.append(_poly(it.layer, it.pts, closed=it.closed, width=it.width, widths=it.widths))
        elif it.kind == "text":
            out.append(_text(it.layer, it.x, it.y, it.h, it.text, it.align))
    return "(progn " + "\n".join(out) + '\n(strcat (itoa ok) " " (itoa bad)))'


def dimstyle_code(name: str, d: dict, f: dict) -> str:
    """LISP: создать размерный стиль СПДС (засечки, ISOCPEUR) - текущие настройки чертежа не меняются."""
    M = f["масштаб"]
    vars_ = [("DIMSCALE", M), ("DIMTXT", d["высота_текста"]), ("DIMASZ", d["засечка"]), ("DIMEXE", d["выступ_выносной"]),
             ("DIMEXO", d["отступ_выносной"]), ("DIMDLE", d["выступ_размерной"]), ("DIMGAP", 1.0), ("DIMTAD", 1),
             ("DIMTIH", 0), ("DIMTOH", 0), ("DIMDEC", 0), ("DIMLUNIT", 2), ("DIMZIN", 8), ("DIMTIX", 0), ("DIMTMOVE", 2)]
    save = " ".join(f'(cons "{k}" (getvar "{k}"))' for k, _ in vars_)
    sets = " ".join(f'(setvar "{k}" {v})' for k, v in vars_)
    return (f'(progn (vl-load-com) (setq doc (vla-get-ActiveDocument (vlax-get-acad-object))) '
            f'(if (not (tblsearch "DIMSTYLE" {_s(name)})) (progn '
            f'(setq old (list {save} (cons "DIMBLK" (getvar "DIMBLK")) (cons "DIMTXSTY" (getvar "DIMTXSTY")))) '
            f'{sets} (setvar "DIMBLK" "_ArchTick") '
            f'(if (tblsearch "STYLE" {_s(TEXT_STYLE)}) (setvar "DIMTXSTY" {_s(TEXT_STYLE)})) '
            f'(setq ds (vla-Add (vla-get-DimStyles doc) {_s(name)})) (vla-CopyFrom ds doc) '
            f'(foreach v old (setvar (car v) (if (and (= (car v) "DIMBLK") (= (cdr v) "")) "." (cdr v)))))) '
            f'(if (tblsearch "DIMSTYLE" {_s(name)}) "да" "нет"))')


def draw_dims_code(items, style: str, layer_colors: dict[str, int]) -> str:
    """LISP: размеры (vla-AddDimRotated) стилем style; прежний активный стиль возвращается."""
    import math
    out = ['(vl-load-com) (setq ok 0 bad 0)',
           '(defun mk (e) (if (entmake e) (setq ok (1+ ok)) (setq bad (1+ bad))))', _style(),
           '(setq doc (vla-get-ActiveDocument (vlax-get-acad-object)) ms (vla-get-ModelSpace doc))',
           '(setq olds (vla-get-ActiveDimStyle doc))',
           f'(vla-put-ActiveDimStyle doc (vla-Item (vla-get-DimStyles doc) {_s(style)}))']
    for name in sorted({it.layer for it in items}):
        out.append(_layer(name, layer_colors.get(name, 7)))
    for it in items:
        if it.kind == "dim":
            (x1, y1), (x2, y2), (lx, ly) = it.pts
            out.append(f'(setq r (vl-catch-all-apply (function vla-AddDimRotated) (list ms '
                       f'(vlax-3d-point {_n(x1)} {_n(y1)} 0.0) (vlax-3d-point {_n(x2)} {_n(y2)} 0.0) '
                       f'(vlax-3d-point {_n(lx)} {_n(ly)} 0.0) {math.radians(it.x):.8f})))')
            out.append(f'(if (vl-catch-all-error-p r) (setq bad (1+ bad)) (progn (vla-put-Layer r {_s(it.layer)}) '
                       f'(setq ok (1+ ok))))')
        elif it.kind == "poly":
            out.append(_poly(it.layer, it.pts, closed=it.closed, width=it.width, widths=it.widths))
        elif it.kind == "text":
            out.append(_text(it.layer, it.x, it.y, it.h, it.text, it.align))
    out.append('(vla-put-ActiveDimStyle doc olds)')
    return "(progn " + "\n".join(out) + '\n(strcat (itoa ok) " " (itoa bad)))'
