"""Построение фасада стойка-ригель по осям.

Вход: оси стоек (вертикальные линии), оси ригелей (горизонтальные линии),
параметры объекта (толщина заполнения, вариант комплектации, выпуск стоек).
Длина нарисованных осей не важна: стойка определяется ригелями, которые
к ней примыкают, а ригель - соседними стойками. Длинную ось ригеля через
несколько пролётов можно рисовать одной линией - она делится по стойкам.
Пока поддерживаются только вертикальные стойки и горизонтальные ригели.
"""

from __future__ import annotations

import csv
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .profiles import chamber_depth

TOL = 1.0  # допуск совпадения координат, мм


@dataclass
class Axis:
    x1: float
    y1: float
    x2: float
    y2: float
    layer: str = ""          # OSs / OSr / OSf - только геометрия; артикулы - в исходных данных витража


def is_false_beam(layer: str) -> bool:
    return layer.upper().startswith("OSF")


@dataclass
class ObjectParams:
    """Параметры конкретного объекта (витража)."""
    заполнение: int                 # толщина заполнения, мм
    вариант: str                    # вариант комплектации из таблицы, например "Т50"
    выпуск_низ: float               # стойка ниже наружной грани нижнего ригеля
    выпуск_верх: float              # стойка выше наружной грани верхнего ригеля
    формула: str = ""               # формула стеклопакета, например "6-16-6-16-6"
    закладная_стойки_низ: float | None = None   # длина, мм; None - по карточке, 0 - не ставить
    закладная_стойки_верх: float | None = None
    монтаж: str = "в проем"                     # "в проем" или "навесной"
    кронштейнов_на_стойку: int = 2              # для навесного фасада
    вариант_периметра: str = "1"
    доборные: str = "как в каталоге"            # из пары "Т50-04 (КПС 296)": как в каталоге / пластик / алюминий
    имя: str = "ВТ-1"                           # марка конструкции
    количество: int = 1                         # сколько таких конструкций
    периметр_лево: bool = True                  # доборные по периметру с этой стороны
    периметр_право: bool = True                 # (нет - витраж примыкает к другому и т.п.)
    периметр_верх: bool = True
    периметр_низ: bool = True
    капельник: bool = True
    отметка_низ: float = 0.0                    # отметка низа витража, м (для знаков отметок)
    # профили ("" - по карточке системы)
    система: str = "КП50К"                      # карточка systems/<система>.toml
    стойка: str = ""
    ригель: str = ""
    фальш_ригель: str = ""                      # оси OSf; "" - по карточке (в системе один, КПС 009)
    # правки из таблицы профилей: марка -> (артикул, (артикул по правилам, длина по правилам))
    профили: dict = field(default_factory=dict)


@dataclass
class Part:
    article: str
    kind: str
    x0: float
    y0: float
    x1: float
    y1: float
    vertical: bool
    where: str = ""          # S1, Y2000 S1-S2 и т.п. - для проверки
    draw: bool = True        # уплотнители в спецификацию, но не рисуем
    note: str = ""           # аналог / примечание из каталога
    linetype: str = ""       # тип линии на чертеже ("" - по слою)
    pts: list | None = None  # контур, если деталь не прямоугольник (срезы 45°); длина - по x0..x1 / y0..y1
    cut: str = "прямой"      # тип реза торцов
    short: float | None = None   # длина по короткой кромке (если торцы косые)
    span: tuple | None = None    # (y, x_лев, x_прав) - пролёт ригеля / фальш-ригеля

    @property
    def length(self) -> float:
        return round((self.y1 - self.y0) if self.vertical else (self.x1 - self.x0), 1)


@dataclass
class Glass:
    kind: str
    x0: float
    y0: float
    x1: float
    y1: float
    weight: float = 0.0      # кг
    mark: str = ""           # марка (сквозная по чертежу), например Ст50_03
    pads: str = ""           # комплект опорных подкладок (по весу: тяжёлые/лёгкие)

    @property
    def width(self) -> float:
        return round(self.x1 - self.x0, 1)

    @property
    def height(self) -> float:
        return round(self.y1 - self.y0, 1)


@dataclass
class Facade:
    parts: list[Part] = field(default_factory=list)
    glasses: list[Glass] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    kit: dict = field(default_factory=dict)
    perimeter: dict = field(default_factory=dict)
    glass_mm: float = 0.0                                   # суммарная толщина стёкол
    fixings: list[tuple] = field(default_factory=list)      # (где, вес кг, способ, крепёж)
    pieces: list[tuple] = field(default_factory=list)       # (артикул, назначение, шт) - штучные
    openings: list[tuple] = field(default_factory=list)     # (метка, название, ширина рамы, высота рамы, стр.)
    # геометрия для сечений и закладных на фасаде
    post_x: list[float] = field(default_factory=list)       # оси стоек
    post_ends: dict = field(default_factory=dict)           # ось стойки -> (низ, верх) стойки
    beam_spans: list[tuple] = field(default_factory=list)   # (y, x_лев, x_прав, шифр закладной, ригель)
    beam_art: dict = field(default_factory=dict)            # (y, x_лев, x_прав) -> артикул ригеля пролёта
    false_spans: list[tuple] = field(default_factory=list)  # фальш-ригели (y, x_лев, x_прав, артикул)
    marks: list[tuple] = field(default_factory=list)        # (y, x_лев, x_прав, марка) - по правилам, до правок таблицы
    profile_rows: list[dict] = field(default_factory=list)  # строки таблицы профилей
    bays: dict = field(default_factory=dict)                # (x_лев, x_прав) -> отметки ригелей
    floor_bays: set = field(default_factory=set)            # пролёты без нижнего ригеля
    bottom_y: float = 0.0
    top_y: float = 0.0


@dataclass
class System:
    card: dict
    kits: list[dict]
    perimeters: list[dict]
    inserts: list[dict]
    openings: dict = field(default_factory=dict)
    folder: Path | None = None
    profiles: list[dict] = field(default_factory=list)      # стойки и ригели (каталог стр. 10-11)

    def profile(self, article: str) -> dict | None:
        return next((r for r in self.profiles if r["Шифр"] == article), None)

    def is_post(self, article: str) -> bool:
        r = self.profile(article)
        return bool(r and r["Тип"] == "стойка") or article == self.card["стойка"]["артикул"]

    def pair_error(self, post: str, beam: str) -> str:
        """Каталог стр. 10: у каждой стойки максимальный ригель (по габариту). '' - можно."""
        p, b = self.profile(post), self.profile(beam)
        mx = self.profile(p["Пара"]) if p and p["Пара"] else None
        if not (p and b and mx):
            return ""
        size = lambda r: float(r["Габарит, мм"].split("x")[-1].replace(",", "."))
        if size(b) > size(mx):
            return (f"ригель {beam} ({b['Габарит, мм']} мм) больше допустимого для стойки {post}: "
                    f"максимум {mx['Шифр']} ({mx['Габарит, мм']} мм), каталог стр. 10")
        return ""

    def chamber(self, article: str) -> tuple[float, float, float] | None:
        """Камера профиля по блоку сечения: (глубина во всю ширину, ширина, габарит), мм."""
        if self.folder is None:
            return None
        return chamber_depth(self.folder / self.card["сечение"]["блоки"], article)

    def insert(self, rigel: str, holes: str, purpose: str = "прямая") -> dict | None:
        """Закладная из таблицы применяемости по ригелю и виду отверстий (с / бс / б)."""
        for r in self.inserts:
            rigels = [s.strip() for s in r["Ригель"].split(",")]
            if rigel in rigels and r["Отверстия"] == holes and r["Назначение"] == purpose:
                return r
        return None

    def color(self, article: str) -> int:
        return self.card.get("цвета", {}).get(article, 7)

    def kit(self, thickness: int, variant: str) -> dict:
        rows = [r for r in self.kits if int(r["Толщина"]) == thickness]
        for r in rows:
            if r["Вариант"] == variant:
                return r
        have = ", ".join(f'"{r["Вариант"]}" (стр. {r["Стр."]})' for r in rows) or "нет ни одного"
        raise ValueError(f"Для заполнения {thickness} мм нет варианта \"{variant}\". Есть: {have}")

    def perimeter(self, thickness: int, variant: str) -> dict:
        rows = [r for r in self.perimeters if int(r["Толщина"]) == thickness]
        for r in rows:
            if r["Вариант"] == variant:
                return r
        have = ", ".join(r["Вариант"] for r in rows) or "нет ни одного"
        raise ValueError(f"Для заполнения {thickness} мм нет варианта периметра {variant}. Есть: {have}")


def _read_csv(path: Path) -> list[dict]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f, delimiter=";"))


def load_system(folder: str | Path, name: str) -> System:
    folder = Path(folder)
    with open(folder / f"{name}.toml", "rb") as f:
        card = tomllib.load(f)
    inserts_file = folder / f"{name}_закладные.csv"
    openings = {}
    op_file = folder / f"{name}_встраиваемые.toml"
    if op_file.exists():
        with open(op_file, "rb") as f:
            openings = {k.upper(): v for k, v in tomllib.load(f).items()}
    return System(card, _read_csv(folder / f"{name}_комплектация.csv"),
                  _read_csv(folder / f"{name}_периметр.csv"),
                  _read_csv(inserts_file) if inserts_file.exists() else [], openings, folder,
                  _read_csv(folder / f"{name}_стойки_ригели.csv") if (folder / f"{name}_стойки_ригели.csv").exists()
                  else [])


_ITEM = re.compile(r"^(?P<art>.+?)\s*(?:\((?P<note>[^)]*)\))?\s*(?:[xх×]\s*(?P<qty>\d+))?$")


def parse_items(text: str) -> list[tuple[str, str, int]]:
    """'Т50-04 (КПС 296) x2 + Т50-07' -> [('Т50-04', 'КПС 296', 2), ('Т50-07', '', 1)]"""
    items = []
    for chunk in (text or "").split("+"):
        chunk = chunk.strip()
        if not chunk:
            continue
        m = _ITEM.match(chunk)
        items.append((m["art"].strip(), m["note"] or "", int(m["qty"] or 1)))
    return items


FILLER_MODES = ("как в каталоге", "пластик", "алюминий")


def pick_filler(art: str, alt: str, mode: str) -> tuple[str, str]:
    """Доборный из пары таблицы периметра "прорисован (аналог)" -> (выбранный, другой).
    Алюминиевые - КП.../КПС..., пластиковые - Т50-.../ТПУ-...; "как в каталоге" - прорисованный."""
    if not alt or mode not in ("пластик", "алюминий"):
        return art, alt
    alu = lambda a: a.upper().startswith("КП")
    if alu(art) == alu(alt):
        return art, alt
    want_alu = mode == "алюминий"
    return (art, alt) if alu(art) == want_alu else (alt, art)


FILM = 0.38   # плёнка триплекса, мм


def _split_formula(formula: str) -> list[str]:
    """Разбить формулу на слои. ' - ' (с пробелами) - разделитель, если он есть; иначе любой дефис.
    Так дефисы внутри названий стекла ('Vision-50') не ломают разбор."""
    f = formula.replace("–", "-").replace("—", "-").strip()
    parts = re.split(r"\s+-\s+", f) if re.search(r"\s-\s", f) else f.split("-")
    return [p.strip() for p in parts if p.strip()]


def _glass_thickness(token: str) -> float:
    """Толщина стекла по началу записи слоя:
    '6 VisionLight T зак' -> 6; '4.4.1' -> 4+4+1x0,38; '44.2' -> 4+4+2x0,38; '33.1 зак' -> 3+3+0,38; '6,5' -> 6,5."""
    m = re.match(r"\s*(\d+)(?:[.,](\d+))?(?:[.,](\d+))?", token)
    if not m:
        raise ValueError(token)
    a, b, c = m.group(1), m.group(2), m.group(3)
    if c is not None:                                   # 4.4.1
        return float(a) + float(b) + int(c) * FILM
    if b is not None and len(a) == 2 and len(b) == 1:    # 44.2 / 33.1 - триплекс
        return float(a[0]) + float(a[1]) + int(b) * FILM
    if b is not None:                                   # 6,5
        return float(f"{a}.{b}")
    return float(a)


def formula_layers(formula: str) -> list[tuple[str, float, float]]:
    """Слои стеклопакета снаружи внутрь: (вид, полная толщина, толщина стекла).
    'стекло' на нечётных местах, 'рамка' (дистанционная рамка) - на чётных."""
    out = []
    for i, token in enumerate(_split_formula(formula)):
        try:
            if i % 2:
                m = re.match(r"\s*(\d+(?:[.,]\d+)?)", token)
                if not m:
                    raise ValueError(token)
                t = float(m.group(1).replace(",", "."))
                out.append(("рамка", t, 0.0))
            else:
                t = _glass_thickness(token)
                m = re.match(r"\s*(\d+)(?:[.,](\d+))?(?:[.,](\d+))?", token)
                films = (int(m.group(3)) if m.group(3) else
                         int(m.group(2)) if m.group(2) and len(m.group(1)) == 2 and len(m.group(2)) == 1 else 0)
                out.append(("стекло", round(t, 2), round(t - films * FILM, 2)))
        except ValueError:
            raise ValueError(f"Не понимаю слой \"{token}\" в формуле стеклопакета: {formula}")
    if not out or len(out) % 2 == 0:
        raise ValueError(f"Формула стеклопакета должна начинаться и заканчиваться стеклом: {formula}")
    return out


def parse_formula(formula: str) -> tuple[float, float]:
    """Формула -> (полная толщина, сумма стёкол без плёнок), мм.
    '6 VisionLight T зак - 16 CU Аргон - 6 М1 - 16 CU Аргон - 6 М1 зак' -> (50, 18)."""
    layers = formula_layers(formula)
    return round(sum(t for _, t, _ in layers), 1), round(sum(g for _, _, g in layers), 2)


def pick_fillers(gap: float, sizes: dict[str, float]) -> list[str] | None:
    """Набрать толщину gap доборными профилями: наименьшее число штук, крупные вперёд."""
    if gap <= 0.01:
        return []
    items = sorted(sizes.items(), key=lambda kv: -kv[1])
    best: list[str] | None = None

    def go(rest, start, chosen):
        nonlocal best
        if abs(rest) < 0.01:
            if best is None or len(chosen) < len(best):
                best = list(chosen)
            return
        if rest < 0 or len(chosen) >= 4 or (best is not None and len(chosen) + 1 >= len(best)):
            return
        for i in range(start, len(items)):
            chosen.append(items[i][0])
            go(rest - items[i][1], i, chosen)
            chosen.pop()

    go(gap, 0, [])
    return best


def _is_seal(article: str) -> bool:
    return article.upper().startswith("ТПУ")


def _has_heavy_kit(system, thick) -> bool:
    """Есть ли в таблице комплектации тяжёлый вариант для этой толщины заполнения."""
    try:
        t = int(float(str(thick).replace(",", ".")))
    except ValueError:
        return False
    for r in system.kits:
        try:
            rt = int(float(str(r["Толщина"]).replace(",", ".")))
        except (KeyError, ValueError):
            continue
        if rt == t and str(r["Вариант"]).startswith("Тяжелые"):
            return True
    return False


def _pad_heavy(wt: float, card) -> bool:
    """Тяжёлые опорные подкладки нужны, если вес стекла попал в "тяжелый" способ крепления ригеля."""
    rules = sorted(card["крепление_ригеля"], key=lambda r: r["до_кг"])
    rule = next((r for r in rules if wt <= r["до_кг"]), None)
    return True if rule is None else bool(rule.get("тяжелый"))


def _split_axes(axes: list[Axis]):
    """Стойки (x, y1, y2, слой), ригели (y, x1, x2, слой), прочие (наклонные). Фальш-ригели - тоже в ригелях."""
    posts, beams, other = [], [], []
    for a in axes:
        if abs(a.x1 - a.x2) < TOL:
            posts.append((a.x1, min(a.y1, a.y2), max(a.y1, a.y2), a.layer))
        elif abs(a.y1 - a.y2) < TOL:
            beams.append((a.y1, min(a.x1, a.x2), max(a.x1, a.x2), a.layer))
        else:
            other.append(a)
    return posts, beams, other


def build(axes: list[Axis], system: System, obj: ObjectParams,
          markers: list[tuple[float, float, str]] = ()) -> Facade:
    """markers - метки проёмов (x, y, текст) со слоя "проемы", например "ДВЕРЬ СТ71".

    Артикулы - по исходным данным витража (стойка, ригель); исключения - таблица профилей
    (например, верхний пояс из стоечного профиля: в строке R1_в вписать артикул стойки).
    Марки считаются по этим правилам и от правок таблицы профилей не меняются: правка в таблице
    ("R1 -> КПС 998") меняет только артикул деталей этой марки. Правка применяется, только пока
    марка совпадает с записанной базой; предупреждение - только о реальной ручной правке
    (артикул в таблице отличен от базы), смена профиля в окне таблицу молча перезаписывает."""
    from .marks import compute_marks

    fac = _build(axes, system, obj, markers, {})
    fac.marks = compute_marks(fac, system)
    base = _mark_spans(fac)                       # марка -> (ключи, длина, артикул) по правилам
    force, warn = {}, []
    for mark, (art, (b_art, b_len)) in obj.профили.items():
        edited = bool(art) and art != b_art       # реальная ручная правка, а не старый расчёт
        if mark not in base:
            if edited:
                warn.append(f"Таблица профилей: марки {mark} больше нет - правка {art} не применена")
            continue
        keys, cur_len, cur_art = base[mark]
        if cur_art != b_art or (b_len and abs(cur_len - b_len) > 1):
            if edited:                            # правка от прошлой конфигурации не переносится
                warn.append(f"Таблица профилей: марка {mark} изменилась (было {b_art} L={b_len:g}, "
                            f"стало {cur_art} L={cur_len:g}) - правка {art} не применена, проверьте")
            continue                              # без правки таблица просто перепишется новым расчётом
        if edited:
            force.update({k: art for k in keys})
    if force:
        marks = fac.marks
        fac = _build(axes, system, obj, markers, force)
        fac.marks = marks
    fac.warnings = warn + fac.warnings
    fac.profile_rows = _profile_rows(fac, base)
    return fac


def _mark_spans(fac: Facade) -> dict[str, tuple[list, float, str]]:
    """марка -> (ключи деталей, длина, артикул по правилам)."""
    out: dict[str, tuple[list, float, str]] = {}
    for n, px in enumerate(fac.post_x, 1):
        p = next((p for p in fac.parts if p.where == f"S{n}" and p.kind == "Стойка"), None)
        if p:
            out[f"S{n}"] = ([("s", px)], p.length, p.article)
    for y, xl, xr, mark in fac.marks:
        p = next((p for p in fac.parts if p.span == (round(y, 3), xl, xr) and p.kind.startswith(("Ригель", "Фальш"))),
                 None)
        if not p:
            continue
        key = ("f" if p.kind == "Фальш-ригель" else "r", round(y, 3), xl, xr)
        keys, L, art = out.get(mark, ([], p.length, p.article))
        out[mark] = (keys + [key], L, art)
    return out


def _profile_rows(fac: Facade, base: dict) -> list[dict]:
    """Строки таблицы профилей: стойки одной длины и артикула - одной строкой, ригели - по маркам.
    "база" - (артикул, длина) по правилам: по ней при следующем построении проверяется, та ли это марка."""
    rows: dict[tuple, dict] = {}
    for mark, (keys, L, art) in _mark_spans(fac).items():
        b = (base[mark][2], base[mark][1]) if mark in base else (art, L)
        k = ("S", art, L, b) if mark.startswith("S") else (mark,)
        r = rows.setdefault(k, {"марки": [], "артикул": art, "база": b, "длина": L, "кол": 0})
        r["марки"].append(mark)
        r["кол"] += len(keys)
    out = list(rows.values())
    for r in out:
        r["марка"] = ", ".join(r.pop("марки"))
    return sorted(out, key=lambda r: (not r["марка"].startswith("S"), r["марка"].startswith("ФР"), r["марка"]))


def _build(axes: list[Axis], system: System, obj: ObjectParams,
           markers, force: dict) -> Facade:
    """force - артикулы из таблицы профилей: ("s", x) / ("r", y, xl, xr) / ("f", y, xl, xr) -> артикул."""
    card = system.card
    fac = Facade()
    fac.kit = system.kit(obj.заполнение, obj.вариант)
    fac.perimeter = system.perimeter(obj.заполнение, obj.вариант_периметра)
    kit = fac.kit

    w = card["система"]["ширина_профиля"]
    half = w / 2
    reach = w + TOL  # насколько ось может не доходить до соседней оси
    dr = card["чертеж"]
    strip = dr["ширина_периметра"]
    cov_h = dr["ширина_крышки"] / 2
    prz_h = dr["ширина_прижима"] / 2
    prz_lt = dr.get("линия_прижима", "")
    tv_w = dr["ширина_термовставки"]
    thermo = parse_items(kit["Термовставка"])
    n_tv = sum(q for _, _, q in thermo)

    if obj.формула:
        total, fac.glass_mm = parse_formula(obj.формула)
        if abs(total - obj.заполнение) > 1:
            fac.warnings.append(f"Формула {obj.формула} даёт толщину {total:g} мм, а заполнение {obj.заполнение} мм")
    else:
        fac.warnings.append("Не задана формула стеклопакета - вес и крепление ригелей не считаются")

    posts_all, beam_all, other = _split_axes(axes)
    if other:
        fac.warnings.append(f"Наклонные оси пока не поддерживаются, пропущено: {len(other)}")
    posts_all.sort()
    art_post = obj.стойка or card["стойка"]["артикул"]
    art_beam = obj.ригель or card["ригель"]["артикул"]
    post_art = {round(px, 3): force.get(("s", round(px, 3)), art_post) for px, _, _, _ in posts_all}
    posts = [(px, y1, y2) for px, y1, y2, _ in posts_all]
    beam_lines = [b for b in beam_all if not is_false_beam(b[3])]
    false_lines = [b for b in beam_all if is_false_beam(b[3])]

    def split_line(y, bx1, bx2):
        xs = [px for px, py1, py2 in posts if bx1 - reach <= px <= bx2 + reach and py1 - reach <= y <= py2 + reach]
        xs = sorted(set(round(x, 3) for x in xs))
        return list(zip(xs, xs[1:]))

    # 1. Делим оси ригелей на пролёты между стойками.
    spans = []  # (y, x_left, x_right)
    for y, bx1, bx2, lay in beam_lines:
        pairs = split_line(y, bx1, bx2)
        if not pairs:
            fac.warnings.append(f"Ригель на отметке {y:.0f} не опирается на две стойки - пропущен")
            continue
        for xl, xr in pairs:
            spans.append((y, xl, xr))

    if not spans:
        fac.warnings.append("Не найдено ни одного ригеля между стойками")
        return fac

    post_x = sorted(set(round(p[0], 3) for p in posts))
    left_x, right_x = post_x[0], post_x[-1]
    all_y = [s[0] for s in spans]
    bottom_y, top_y = min(all_y), max(all_y)

    # Артикул ригеля пролёта: основной; правки - из таблицы профилей (артикул стойки = ригель из стоечного профиля).
    for y, xl, xr in spans:
        fac.beam_art[(round(y, 3), xl, xr)] = force.get(("r", round(y, 3), xl, xr), art_beam)

    def add(art, kind, x0, y0, x1, y1, vertical, where, note="", lt="", pts=None):
        fac.parts.append(Part(art, kind, x0, y0, x1, y1, vertical, where, not _is_seal(art), note, lt, pts))

    # Пролёты (между соседними стойками) и ригели в них.
    bays: dict[tuple, list[float]] = {}
    for y, xl, xr in spans:
        bays.setdefault((xl, xr), []).append(y)
    for xl, xr in zip(post_x, post_x[1:]):
        bays.setdefault((xl, xr), [])
    # Пролёт без нижнего ригеля = проём до пола (дверь).
    floor_bays = {k for k, ys in bays.items() if not ys or min(ys) > bottom_y + TOL}

    uc = card["уплотнители"]

    def seals(art, kind, c, lo, hi, vertical, where, n=2):
        """Уплотнители - n ниток вдоль детали (в спецификацию, не рисуются)."""
        for _ in range(n):
            if vertical:
                fac.parts.append(Part(art, kind, c, lo, c, hi, True, where, False))
            else:
                fac.parts.append(Part(art, kind, lo, c, hi, c, False, where, False))

    def is_post_profile(art: str) -> bool:
        return art in (art_post, card["стойка"]["артикул"]) or art in post_art.values() or system.is_post(art)

    def add_thermo(c, lo, hi, vertical, where):
        """Термовставки - узкими полосками по центру профиля (c - ось)."""
        k = 0
        for art, note, qty in thermo:
            for _ in range(qty):
                o = c + (k - (n_tv - 1) / 2) * tv_w
                if vertical:
                    add(art, "Термовставка", o - tv_w / 2, lo, o + tv_w / 2, hi, True, where, note)
                else:
                    add(art, "Термовставка", lo, o - tv_w / 2, hi, o + tv_w / 2, False, where, note)
                k += 1

    # 2. Стойки и всё, что идёт вдоль них.
    cover_add = card["крышка_стойки"]["добавка"]
    for n, px in enumerate(post_x, 1):
        ys = [y for y, xl, xr in spans if abs(xl - px) < TOL or abs(xr - px) < TOL]
        if any(abs(xl - px) < TOL or abs(xr - px) < TOL for xl, xr in floor_bays):
            ys.append(bottom_y)          # рядом дверь до пола - стойка идёт до низа витража
        if not ys:
            fac.warnings.append(f"К стойке X={px:.0f} не примыкает ни один ригель - пропущена")
            continue
        face_lo, face_hi = min(ys) - half, max(ys) + half
        s_lo, s_hi = face_lo - obj.выпуск_низ, face_hi + obj.выпуск_верх
        c_lo, c_hi = face_lo - cover_add / 2, face_hi + cover_add / 2
        where = f"S{n}"
        add(post_art.get(px, art_post), card["стойка"]["тип"], px - half, s_lo, px + half, s_hi, True, where)
        fac.post_ends[px] = (s_lo, s_hi)
        add(kit["Крышка стойки"], "Крышка стойки", px - cov_h, c_lo, px + cov_h, c_hi, True, where)
        add(kit["Прижим"], "Прижим", px - prz_h, c_lo, px + prz_h, c_hi, True, where, lt=prz_lt)
        seals(uc["стойки"], "Уплотнитель стойки", px, c_lo, c_hi, True, where)
        seals(uc["прижим"], "Уплотнитель прижима", px, c_lo, c_hi, True, where)
        add_thermo(px, c_lo, c_hi, True, where)
        if (px == left_x and obj.периметр_лево) or (px == right_x and obj.периметр_право):
            k = 0
            for art, note, qty in parse_items(fac.perimeter["Крайняя стойка"]):
                art, note = pick_filler(art, note, obj.доборные)
                for _ in range(qty):
                    x0 = px - half + k * strip if px == left_x else px + half - (k + 1) * strip
                    add(art, "Доборный по периметру", x0, s_lo, x0 + strip, s_hi, True, where, note)
                    k += 1

    # Закладные в стойки: внизу и вверху каждой стойки (крепление в проём).
    zs = card["закладная_стойки"]
    for end, L in (("низ", obj.закладная_стойки_низ), ("верх", obj.закладная_стойки_верх)):
        L = zs["длина_в_проем"] if L is None else L
        n_posts = sum(1 for p in fac.parts if p.kind == card["стойка"]["тип"])
        if L and n_posts:
            fac.pieces.append((f'{zs["артикул"]} L={L:g}',
                               f"Закладная стойки, {end}", n_posts))

    # Опоры стоек - по типу монтажа.
    mt = card["монтаж"]
    n_posts = sum(1 for p in fac.parts if p.kind == card["стойка"]["тип"])
    if obj.монтаж == "в проем":
        fac.pieces.append((mt["в_проем_платик"], "Платик, низ (неподвижный узел)", n_posts))
        fac.pieces.append((mt["в_проем_платик"], "Платик, верх (подвижный узел)", n_posts))
    elif obj.монтаж == "навесной":
        n = n_posts * obj.кронштейнов_на_стойку
        fac.pieces.append((mt["навесной_кронштейн"], "Кронштейн", n))
        for w_ in mt["навесной_шайбы"]:
            fac.pieces.append((w_, "Шайба кронштейна", n))
    else:
        fac.warnings.append(f"Неизвестный тип монтажа \"{obj.монтаж}\" - опоры не посчитаны")

    # 3. Ригели и всё, что идёт вдоль них.
    rc = card["ригель"]
    r_end = half - rc["заход_в_стойку"]
    cov = card["крышка_ригеля"]["от_оси_стойки"]
    prz = card["прижим_ригеля"]["от_оси_стойки"]
    for y, xl, xr in sorted(spans, key=lambda s: (s[1], s[0])):
        where = f"Y{y:.0f} S{post_x.index(xl) + 1}-S{post_x.index(xr) + 1}"
        b_art = fac.beam_art.get((round(y, 3), xl, xr), art_beam)
        from_post = is_post_profile(b_art)
        end = half if from_post else r_end            # стоечный профиль - без фрезеровки, между гранями стоек
        add(b_art, rc["тип"] + (" (из стоечного профиля)" if from_post else ""),
            xl + end, y - half, xr - end, y + half, False, where)
        fac.parts[-1].span = (round(y, 3), xl, xr)
        add(kit["Крышка ригеля"], "Крышка ригеля", xl + cov, y - cov_h, xr - cov, y + cov_h, False, where)
        add(kit["Прижим"], "Прижим", xl + prz, y - prz_h, xr - prz, y + prz_h, False, where, lt=prz_lt)
        add_thermo(y, xl + end, xr - end, False, where)
        seals(uc["стойки"] if from_post else uc["ригели"], "Уплотнитель ригеля", y, xl + half, xr - half, False, where)
        seals(uc["прижим"], "Уплотнитель прижима", y, xl + prz, xr - prz, False, where)
        if (abs(y - bottom_y) < TOL and obj.периметр_низ) or (abs(y - top_y) < TOL and obj.периметр_верх):
            k = 0
            for art, note, qty in parse_items(fac.perimeter["Крайний ригель"]):
                art, note = pick_filler(art, note, obj.доборные)
                for _ in range(qty):
                    y0 = y - half + k * strip if abs(y - bottom_y) < TOL else y + half - (k + 1) * strip
                    add(art, "Доборный по периметру", xl + r_end, y0, xr - r_end, y0 + strip, False, where, note)
                    k += 1

    # Фальш-ригели (слой OSf): за стеклом, стекло не делят; профиль между гранями стоек + 2 уплотнителя по стеклу.
    fc = card.get("фальш_ригель", {})
    for y, bx1, bx2, lay in false_lines:
        pairs = split_line(y, bx1, bx2)
        if not pairs:
            fac.warnings.append(f"Фальш-ригель на отметке {y:.0f} не опирается на две стойки - пропущен")
        for xl, xr in pairs:
            f_art = force.get(("f", round(y, 3), xl, xr), obj.фальш_ригель or fc.get("артикул", "КПС 009"))
            where = f"фальш Y{y:.0f} S{post_x.index(xl) + 1}-S{post_x.index(xr) + 1}"
            add(f_art, "Фальш-ригель", xl + half, y - fc.get("высота", 50) / 2, xr - half, y + fc.get("высота", 50) / 2,
                False, where, lt=fc.get("линия", prz_lt))
            fac.parts[-1].span = (round(y, 3), xl, xr)
            seals(fc.get("уплотнитель", uc["ригели"]), "Уплотнитель фальш-ригеля", y, xl + half, xr - half, False, where)
            fac.false_spans.append((y, xl, xr, f_art))

    fac.post_x, fac.bays, fac.floor_bays = post_x, bays, floor_bays
    fac.bottom_y, fac.top_y = bottom_y, top_y

    # 4. Ячейки: стекло или проём (дверь/окно по метке).
    g = card["стекло"]["от_оси"]
    kind = f"Ст{obj.заполнение}"
    floor_y = bottom_y - half - obj.выпуск_низ          # низ витража = низ стоек
    cells = []                                            # (xl, xr, yb, yt) ; yb=None - до пола
    for (xl, xr), ys in sorted(bays.items()):
        ys = sorted(ys)
        if (xl, xr) in floor_bays and ys:
            cells.append((xl, xr, None, ys[0]))
        cells += [(xl, xr, yb, yt) for yb, yt in zip(ys, ys[1:])]

    used = set()
    for xl, xr, yb, yt in cells:
        lo = floor_y if yb is None else yb
        mark = next((m for m in markers if xl < m[0] < xr and lo < m[1] < yt), None)
        if mark:
            used.add(mark)
            _opening(fac, system, obj, mark[2], xl, xr, yb, yt, floor_y, add)
        elif yb is None:
            fac.warnings.append(f"Пролёт X {xl:.0f}-{xr:.0f}: нет нижнего ригеля, но нет и метки двери - ячейка пустая")
        else:
            gl = Glass(kind, xl + g, yb + g, xr - g, yt - g)
            gl.weight = round(gl.width * gl.height / 1e6 * fac.glass_mm * card["вес"]["стекло_кг_на_м2_на_мм"], 1)
            fac.glasses.append(gl)
    for m in markers:
        if m not in used:
            fac.warnings.append(f"Метка \"{m[2]}\" ({m[0]:.0f}, {m[1]:.0f}) не попала ни в одну ячейку")

    # 5. Крепление ригелей: по весу стекла, которое стоит НА ригеле.
    rules = sorted(card["крепление_ригеля"], key=lambda r: r["до_кг"])
    zk = card["закладная_ригеля"]
    fix_count: dict[str, int] = {}
    ins_count: dict[str, int] = {}
    for y, xl, xr in sorted(spans, key=lambda s: (s[1], s[0])):
        where = f"Y{y:.0f} S{post_x.index(xl) + 1}-S{post_x.index(xr) + 1}"
        above = [gl for gl in fac.glasses if abs(gl.y0 - g - y) < TOL and abs(gl.x0 - g - xl) < TOL]
        wt = max((gl.weight for gl in above), default=0.0)
        rule = next((r for r in rules if wt <= r["до_кг"]), None)
        if rule is None:
            fac.warnings.append(f"Ригель {where}: стекло {wt:g} кг - больше {rules[-1]['до_кг']} кг, "
                                "по каталогу решения нет")
            continue
        b_art = fac.beam_art.get((round(y, 3), xl, xr), art_beam)
        ins = system.insert(b_art, rule["отверстия"])
        code = ins["Шифр"] if ins else None             # для слоя и подписи на фасаде ("=" в имени слоя нельзя)
        if ins:
            ins_name = f'{ins["Шифр"]} (L={ins["L, мм"]}, стр. {ins["Стр."]})'
        else:
            # нет в таблице (стоечный профиль ригелем, свой ригель): закладная того же шифра, что у основного
            # ригеля, длина - камера этого профиля во всю ширину (по блоку сечения) - 1 мм
            ins = system.insert(art_beam, rule["отверстия"]) or system.insert(rc["артикул"], rule["отверстия"])
            ch = system.chamber(b_art)
            what = f"из стоечного профиля {b_art}" if is_post_profile(b_art) else b_art
            if ins and ch:
                L = ch[0] - zk["запас_к_камере"]
                ins_name = f'{ins["Шифр"]} L={L:g} (камера {b_art} {ch[0]:g} - {zk["запас_к_камере"]:g}, стр. {ins["Стр."]})'
                code = f'{ins["Шифр"]}_L{L:g}'
            else:
                ins_name = f"закладная для ригеля {b_art} (ПОДОБРАТЬ)"
                fac.warnings.append(f"Ригель {where} {what}: " +
                                    (f"нет блока сечения {b_art} - не найдена камера" if ins else
                                     f"нет закладной с отверстиями \"{rule['отверстия']}\" ({rule['способ']})") +
                                    " - подберите закладную")
        fac.fixings.append((where, wt, rule["способ"], rule["крепеж"], ins_name))
        fac.beam_spans.append((y, xl, xr, code or ins_name, b_art))
        fix_count[rule["крепеж"]] = fix_count.get(rule["крепеж"], 0) + zk["на_ригель"]
        ins_count[ins_name] = ins_count.get(ins_name, 0) + zk["на_ригель"]
        if rule.get("тяжелый") and not kit["Вариант"].startswith("Тяжелые"):
            if not _has_heavy_kit(system, obj.заполнение):
                fac.warnings.append(f"Ригель {where}: стекло {wt:g} кг - нужны подкладки КПС 846/1126, "
                                    f"а для толщины {obj.заполнение} мм они не предусмотрены - "
                                    f"измените толщину пакета (40-48 мм - КПС 846, 52-58 мм - КПС 1126)")
            else:
                need = "КПС 846 (40-48 мм)" if obj.заполнение <= 48 else "КПС 1126 (52-58 мм)"
                fac.warnings.append(f"Ригель {where}: стекло {wt:g} кг - нужны подкладки {need}, "
                                    f"а выбран вариант \"{kit['Вариант']}\"")
    # опорные подкладки по весу стекла: тяжёлый комплект - только под тяжёлыми пакетами
    light_pads = ""
    light_variant = card.get("подкладки_по_весу", {}).get(str(kit.get("Вариант", "")))
    if light_variant:
        try:
            light_pads = system.kit(obj.заполнение, light_variant).get("Подкладка опорная", "")
        except ValueError:
            fac.warnings.append(f"Подкладки: лёгкий вариант \"{light_variant}\" не найден для толщины "
                                f"{obj.заполнение} мм - под всеми стёклами тяжёлые")
    heavy_pads = kit.get("Подкладка опорная", "")
    for gl in fac.glasses:
        gl.pads = (heavy_pads if _pad_heavy(gl.weight, card) else light_pads) or heavy_pads
    for y, xl, xr, f_art in fac.false_spans:           # фальш-ригель стекло не несёт - под винты
        ins = system.insert(f_art, "с")
        name = f'{ins["Шифр"]} (L={ins["L, мм"]}, стр. {ins["Стр."]})' if ins else f"закладная для {f_art} (УТОЧНИТЬ)"
        if not ins:
            fac.warnings.append(f"Фальш-ригель {f_art}: нет закладной в таблице - подберите")
        ins_count[name] = ins_count.get(name, 0) + zk["на_ригель"]
        fix_count[rules[0]["крепеж"]] = fix_count.get(rules[0]["крепеж"], 0) + zk["на_ригель"]
    for name, n in ins_count.items():
        fac.pieces.append((name, "Закладная ригеля", n))
    for fx, n in fix_count.items():
        fac.pieces.append((fx, "Крепёж закладной (на одну закладную)", n))

    return fac


def _opening(fac: Facade, system: System, obj: ObjectParams, text: str,
             xl: float, xr: float, yb: float | None, yt: float, floor_y: float, add) -> None:
    """Рама двери/окна в ячейке вместо стекла + доборник + уплотнитель."""
    cfg = system.openings.get(" ".join(text.upper().split()))
    if not cfg:
        have = ", ".join(system.openings) or "нет ни одной"
        fac.warnings.append(f"Метка \"{text}\": такой конструкции нет в _встраиваемые.toml. Есть: {have}")
        return
    off, b, art = cfg["от_оси"], cfg["ширина"], cfg["рама"]
    X0, X1, Y1 = xl + off, xr - off, yt - off
    Y0 = floor_y if yb is None else yb + off
    W, H = X1 - X0, Y1 - Y0
    where = f"{text} X{xl:.0f}-{xr:.0f}"
    kind = cfg.get("тип_рамы", "Рама")
    fac.openings.append((text, cfg["название"], W, H, cfg.get("стр", "")))

    # Рама со срезами 45° (у двери до пола низ без среза и без нижней планки).
    def frame(x0, y0, x1, y1, vertical, pts, n45):
        add(art, kind, x0, y0, x1, y1, vertical, where, pts=pts)
        fac.parts[-1].cut = f"45° ({n45} торц{'а' if n45 > 1 else ''})"
        fac.parts[-1].short = round(fac.parts[-1].length - n45 * b, 1)

    frame(X0, Y1 - b, X1, Y1, False, [(X0, Y1), (X1, Y1), (X1 - b, Y1 - b), (X0 + b, Y1 - b)], 2)
    lo_in = Y0 if yb is None else Y0 + b
    n_side = 1 if yb is None else 2
    frame(X0, Y0, X0 + b, Y1, True, [(X0, Y0), (X0 + b, lo_in), (X0 + b, Y1 - b), (X0, Y1)], n_side)
    frame(X1 - b, Y0, X1, Y1, True, [(X1 - b, lo_in), (X1, Y0), (X1, Y1), (X1 - b, Y1 - b)], n_side)
    if yb is not None:
        frame(X0, Y0, X1, Y0 + b, False, [(X0, Y0), (X1, Y0), (X1 - b, Y0 + b), (X0 + b, Y0 + b)], 2)

    # Доборник: толщина заполнения - толщина рамы в стойке.
    gap = obj.заполнение - cfg["толщина_в_стойке"]
    sizes = system.card.get("доборные_толщины", {})
    fillers = pick_fillers(gap, sizes)
    if fillers is None:
        fac.warnings.append(f"{where}: доборник {gap:g} мм не набирается из {', '.join(sizes)} - подберите вручную")
        fillers = []
    strip = system.card["чертеж"]["ширина_периметра"]
    sides = [("верх", X0, Y1 - strip, X1, Y1, False), ("лево", X0, Y0, X0 + strip, Y1, True),
             ("право", X1 - strip, Y0, X1, Y1, True)]
    if yb is not None:
        sides.append(("низ", X0, Y0, X1, Y0 + strip, False))
    for f_art in fillers:
        for _, x0, y0, x1, y1, vert in sides:
            add(f_art, f"Доборник рамы {gap:g} мм", x0, y0, x1, y1, vert, where)
    for _, x0, y0, x1, y1, vert in sides:
        add(cfg["уплотнитель"], "Уплотнитель рамы", x0, y0, x1, y1, vert, where)
    perim = sum((x1 - x0) if not vert else (y1 - y0) for _, x0, y0, x1, y1, vert in sides)
    fac.pieces.append((cfg["винт"], f"{where}: крепление рамы (шаг уточнить), периметр {perim:g} мм", 0))


def summary(fac: Facade) -> list[tuple]:
    """Сводка: (артикул, тип, длина, количество, примечание), как в спецификации."""
    counts: dict[tuple, int] = {}
    for p in fac.parts:
        key = (p.article, p.kind, p.length, p.note)
        counts[key] = counts.get(key, 0) + 1
    return [(a, k, l, n, note) for (a, k, l, note), n in sorted(counts.items())]
