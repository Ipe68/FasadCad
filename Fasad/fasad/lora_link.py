r"""Расчёт статики через программу Lora (C:\cad\Lora_2_2): запуск, обмен JSON, фильтры.

Фасад пишет в папку проекта файл "статика.json" с блоком "вход" и запускает
Lora_2_2\main.py --фасад <файл>. Lora показывает окно как обычно, по кнопке
"Экспорт в FS" (или при закрытии, если расчёт выполнялся) дописывает блок
"результат". Фасад фильтрует по нему списки стоек, ригелей и вариантов.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

LORA_DIR = Path(r"C:\cad\Lora_2_2")
LORA_MAIN = LORA_DIR / "main.py"


def available() -> bool:
    """Программа Lora на месте (кнопка "Расчёт нагрузок" доступна)."""
    return LORA_MAIN.exists()


def norm_art(art: str) -> str:
    """Артикулы для сравнения: без пробелов, в верхнем регистре ('КПС 344' = 'КПС344')."""
    return str(art).replace(" ", "").upper()


def to_float(v) -> float | None:
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None


def wrap_profiles(orig, min_depth: float, statics: "Statics"):
    """Фильтр списка стоек/ригелей: только профили глубиной не меньше выбранной в Лоре."""
    def f(thick):
        return [(v, d) for v, d in orig(thick)
                if (statics.depth(v) or 0.0) >= min_depth]
    return f


def wrap_kits(orig, statics: "Statics", weight_kg: float):
    """Фильтр вариантов комплектации: тяжёлое стекло -> только варианты 'Тяжелые ...'."""
    def f(thick):
        opts = orig(thick)
        t = to_float(thick)
        if t is None:
            return opts
        rule = statics.heavy_rule(weight_kg)
        if not (rule and rule.get("тяжелый")):
            return opts
        heavy = [o for o in opts if str(o[0]).startswith("Тяжелые")]
        return heavy or opts           # тяжёлого варианта у этой толщины нет - список как есть
    return f


def heavy_thickness_hint(statics: "Statics", thick, weight_kg) -> str | None:
    """Подсказка, если стекло тяжёлое, а у текущей толщины нет тяжёлого варианта комплектации.

    Тяжёлые подкладки КПС 846/1126 не для любой толщины - предлагаем ближайшие подходящие."""
    t, wt = to_float(thick), to_float(weight_kg)
    if t is None or not wt:
        return None
    rule = statics.heavy_rule(wt)
    if not (rule and rule.get("тяжелый")):
        return None
    kits_t = [k for k in statics.system.kits if to_float(k.get("Толщина")) == t]
    if any(str(k["Вариант"]).startswith("Тяжелые") for k in kits_t):
        return None
    heavy_th = sorted({to_float(k["Толщина"]) for k in statics.system.kits
                       if str(k["Вариант"]).startswith("Тяжелые") and to_float(k.get("Толщина"))})
    if not heavy_th:
        return None
    below = [x for x in heavy_th if x < t]
    above = [x for x in heavy_th if x > t]
    opts = []
    if below:
        opts.append(f"{below[-1]:g} мм")
    if above:
        opts.append(f"{above[0]:g} мм")
    return (f"Стекло {wt:g} кг - нужны подкладки КПС 846/1126, а для толщины {t:g} мм они не предусмотрены.\n"
            f"Измените толщину пакета ({' или '.join(opts)}) - подходящий вариант подставится сам;\n"
            f"формулу стеклопакета обновите и повторите расчёт статики.")


def fixing_holes(statics: "Statics", weight_kg: float) -> str | None:
    """Вид отверстий закладной для такого веса стекла ('с' / 'бс' / 'б'; сверх нормы - 'б')."""
    rule = statics.heavy_rule(weight_kg)
    if rule:
        return rule["отверстия"]
    rules = sorted(statics.system.card["крепление_ригеля"], key=lambda r: r["до_кг"])
    return rules[-1]["отверстия"] if rules else None


def beams_with_fixing(system, holes: str) -> set[str]:
    """Ригели, у которых в таблице закладных есть закладная с такими отверстиями (артикулы без пробелов)."""
    out = set()
    for r in system.inserts:
        if r.get("Отверстия") == holes:
            out |= {norm_art(a) for a in str(r.get("Ригель", "")).split(",")}
    return out


def wrap_beams_fixing(orig, allowed: set[str]):
    """Оставить в списке ригелей только те, у которых есть закладная под требуемые отверстия."""
    def f(thick):
        return [(v, d) for v, d in orig(thick) if norm_art(v) in allowed]
    return f


def fixing_warning_text(statics: "Statics", res: dict) -> str | None:
    """Предупреждение, если ригель из Лоры нельзя применить из-за крепления (по прочности проходит)."""
    wt = to_float(res.get("вес_стекла_кг"))
    art = (res.get("ригель") or {}).get("артикул")
    if not wt or not art:
        return None
    holes = fixing_holes(statics, wt)
    if not holes or norm_art(art) in beams_with_fixing(statics.system, holes):
        return None
    way = (statics.heavy_rule(wt) or {}).get("способ", "")
    return (f"Лора подобрала ригель {art} - по прочности он проходит, но применить его нельзя:\n"
            f"для стекла {wt:g} кг крепление требуется \"{way}\" (отверстия \"{holes}\"), а закладной\n"
            f"\"{art} - {holes}\" в таблице нет. Список «Ригель» ограничен профилями с подходящей закладной.")


def cell_geometry(group) -> dict | None:
    """Габариты витража по осям для полей Лоры (схема Shema.png). Все размеры, мм.

    b1 - приведённая ширина грузовой площади стойки (взвешена по высотам ячеек),
    b2 - длина рассчитываемого ригеля (наибольший шаг стоек - 20),
    l1 - высота пакета по большей стороне расчётного ригеля - 24,
    l2 - то же по меньшей стороне - 24 (не меньше 0; у крайнего ригеля 0),
    h_m - высота стойки. Расчётный ригель - несущий самый большой пакет."""
    from .facade import _split_axes
    posts, beams, _other = _split_axes(list(group))
    if not posts or not beams:
        return None
    xs = sorted({round(p[0], 3) for p in posts})
    if len(xs) < 2:
        return None
    spacings = [b - a for a, b in zip(xs, xs[1:])]
    # ячейки пролётов: расстояния между ригелями, покрывающими пролёт
    bays = {}
    for xl, xr in zip(xs, xs[1:]):
        ys = sorted({round(y, 3) for y, x1, x2, _ in beams if x1 <= xl + 1 and x2 >= xr - 1})
        bays[(xl, xr)] = ys
    weighted = [(xr - xl, b - a) for (xl, xr), ys in bays.items()
                for a, b in zip(ys, ys[1:])]
    if weighted:
        b1 = round(sum(s * h for s, h in weighted) / sum(h for _, h in weighted))
    else:
        b1 = round(sum(spacings) / len(spacings))
    # расчётный ригель: максимум большей стороны (при равенстве - с большей меньшей)
    best = None
    for y, x1, x2, _ in beams:
        ry = round(y, 3)
        covered = [(xl, xr) for xl, xr in bays if x1 <= xl + 1 and x2 >= xr - 1]
        if not covered:                       # частичный ригель - ближайший пролёт по центру
            xc = (x1 + x2) / 2
            xl = max(x for x in xs if x <= xc + 1)
            xr = min(x for x in xs if x >= xc - 1)
            covered = [(xl, xr)] if xl < xr else list(bays)
        for xl, xr in covered:
            ys = bays.get((xl, xr), [])
            if len(ys) < 2:
                continue
            ryy = min(ys, key=lambda v: abs(v - ry))
            i = ys.index(ryy)
            above = ys[i + 1] - ryy if i + 1 < len(ys) else 0.0
            below = ryy - ys[i - 1] if i > 0 else 0.0
            pair = (max(above, below), min(above, below))
            if best is None or pair > best:
                best = pair
    out = {"b1": b1, "b2": round(max(spacings) - 20), "h_m": round(max(p[2] - p[1] for p in posts))}
    if best:
        out["l1"] = round(max(0.0, best[0] - 24))
        out["l2"] = round(max(0.0, best[1] - 24))
    return out


class Statics:
    """Обмен с Lora и фильтры по результату статики."""

    def __init__(self, system, project_dir: Path):
        self.system = system
        self.project_dir = Path(project_dir)
        self.path = self.project_dir / "статика.json"
        card = system.card.get("статика", {}) if isinstance(system.card, dict) else {}
        self.main = Path(card.get("путь", LORA_DIR)) / "main.py"
        self.python = card.get("питон") or sys.executable

    # ---------- запуск ----------

    def run(self, obj_name: str, t_glass: float | None, geom: dict | None = None) -> dict | None:
        """Открыть окно Lora и дождаться результата. None - расчёт не передан."""
        if not self.main.exists():
            raise RuntimeError(f"Не найдена программа Lora: {self.main}")
        chk = subprocess.run([self.python, "-c", "import PyQt5, pandas, openpyxl"],
                             capture_output=True, timeout=60)
        if chk.returncode != 0:
            raise RuntimeError(f"Интерпретатор {self.python} не видит PyQt5/pandas/openpyxl - "
                               f"задайте ключ [статика] питон в карточке системы")
        self.project_dir.mkdir(parents=True, exist_ok=True)
        inp = {"название": obj_name}
        if t_glass:
            inp["толщина_стекла_мм"] = t_glass
        sys_lora = self.system.card.get("статика", {}).get("система_лора")
        if sys_lora:
            inp["система"] = sys_lora
        if geom:
            inp["размеры"] = geom
        self.path.write_text(json.dumps({"вход": inp}, ensure_ascii=False, indent=1),
                             encoding="utf-8")
        subprocess.run([self.python, str(self.main), "--фасад", str(self.path)])
        return self.read()

    def read(self) -> dict | None:
        """Сохранённый результат Lora (если есть)."""
        try:
            return json.loads(self.path.read_text(encoding="utf-8")).get("результат")
        except (OSError, ValueError):
            return None

    # ---------- фильтры ----------

    def depth(self, art: str) -> float | None:
        """Габарит (глубина, мм) профиля по шифру из таблицы системы."""
        n = norm_art(art)
        for r in self.system.profiles:
            if norm_art(r["Шифр"]) == n:
                return to_float(r["Габарит, мм"])
        return None

    def heavy_rule(self, weight_kg: float) -> dict | None:
        """Правило крепления ригеля для такого веса стекла (None - по каталогу решения нет)."""
        rules = sorted(self.system.card["крепление_ригеля"], key=lambda r: r["до_кг"])
        return next((r for r in rules if weight_kg <= r["до_кг"]), None)
