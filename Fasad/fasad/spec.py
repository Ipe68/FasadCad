"""Спецификация витража по каталогу -> Excel.

Алюминий (есть в таблице "Геометрические характеристики" каталога) - хлысты, раскрой, масса.
Пластик, уплотнители, термовставки - погонаж (м.п.), без раскроя и массы.
Стекло - шт., площадь, масса.
Основной лист - по форме спецификации ГОСТ 21.101 (форма 7): Поз. | Обозначение | Наименование | Кол. | Масса ед., кг | Примечание.
"""

from __future__ import annotations

import csv
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .facade import Facade, ObjectParams, System
from .marks import beam_marks

PERIM = "Доборный по периметру"


@dataclass
class Spec:
    title: str
    params: list[tuple[str, str]] = field(default_factory=list)
    aluminium: list[dict] = field(default_factory=list)     # профили: артикул, наим., марки, длина, кол, кг/м
    linear: list[dict] = field(default_factory=list)        # погонаж: артикул, наим., м.п.
    glasses: list[dict] = field(default_factory=list)
    pieces: list[dict] = field(default_factory=list)        # штучные: артикул, наим., шт, масса ед., прим.
    cuts: list[dict] = field(default_factory=list)          # раскрой алюминия
    warnings: list[str] = field(default_factory=list)


def _read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f, delimiter=";"))


def _num(s) -> float | None:
    try:
        return float(str(s).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _kit_items(text: str) -> list[tuple[str, int]]:
    """'КПС 757 + ТПУ-017-04 (2 шт.)' -> [('КПС 757', 1), ('ТПУ-017-04', 2)] - на один комплект."""
    out = []
    for chunk in (text or "").split("+"):
        chunk = chunk.strip()
        if chunk:
            m = re.search(r"\((\d+)\s*шт", chunk)
            out.append((re.sub(r"\s*\(.*\)", "", chunk).strip(), int(m.group(1)) if m else 1))
    return out


def cut_plan(lengths: list[float], stock: float, kerf: float) -> list[list[float]]:
    """Раскрой: первый подходящий хлыст по убыванию длин (каждый рез съедает пропил)."""
    bars: list[list[float]] = []
    for L in sorted(lengths, reverse=True):
        for bar in bars:
            if sum(bar) + L + kerf * (len(bar) + 1) <= stock:
                bar.append(L)
                break
        else:
            bars.append([L])
    return bars


def build_spec(fac: Facade, system: System, obj: ObjectParams, title: str = "Витраж",
               folder: Path | None = None, капельник: bool = True,
               хлыст: float | None = None, хлысты: dict | None = None) -> Spec:
    """хлыст - длина хлыста для объекта (по умолчанию из карточки), хлысты - свои длины по артикулам."""
    card = system.card
    folder = folder or Path(__file__).resolve().parent.parent / "systems"
    sp = Spec(title)
    kit = fac.kit
    alu = {r["Артикул"]: _num(r["Масса, кг/м"]) for r in _read_csv(folder / "КП50К_алюминий.csv")}
    for cfg in system.openings.values():                 # рамы дверей/окон - тоже алюминий
        alu.setdefault(cfg["рама"], cfg.get("масса_1м"))
    ins_rows = _read_csv(folder / "КП50К_закладные.csv")
    ins_mass = {r["Шифр"]: _num(r["Масса, кг"]) for r in ins_rows}
    ins_len = {r["Шифр"]: _num(r["L, мм"]) for r in ins_rows}
    rc = card.get("раскрой", {})
    stock_def = хлыст or rc.get("хлыст", 6000)
    stocks = {**rc.get("хлысты", {}), **(хлысты or {})}
    kerf = rc.get("пропил", 10)

    sp.params = [("Система", card["система"]["название"]), ("Заполнение", f"{obj.заполнение} мм"),
                 ("Формула стеклопакета", obj.формула or "-"),
                 ("Вариант комплектации", f'{kit["Вариант"]} (каталог стр. {kit["Стр."]})'),
                 ("Доборные по периметру", f'вариант {fac.perimeter["Вариант"]} (каталог стр. {fac.perimeter["Стр."]}), '
                                           f'{obj.доборные}'),
                 ("Монтаж", obj.монтаж), ("Хлыст по умолчанию", f"{stock_def:g} мм, пропил {kerf} мм")]

    # ---------- марки ----------
    post_mark = {px: f"S{n}" for n, px in enumerate(fac.post_x, 1)}
    bmarks = beam_marks(fac, system)

    def mark_of(p) -> str:
        if p.kind == card["стойка"]["тип"]:
            return post_mark.get(round((p.x0 + p.x1) / 2, 3), "")
        if (p.kind.startswith(card["ригель"]["тип"]) or p.kind == "Фальш-ригель") and not p.vertical:
            yc = (p.y0 + p.y1) / 2
            return next((m for y, xl, xr, m in bmarks if abs(y - yc) < 1 and xl < p.x0 < xr), "")
        return ""

    # ---------- алюминий и погонаж ----------
    al_groups: dict[tuple, list] = defaultdict(list)
    lin: dict[tuple, float] = defaultdict(float)
    al_lengths: dict[str, list[float]] = defaultdict(list)
    for p in fac.parts:
        if p.article in alu:
            al_groups[(p.article, p.kind, p.length)].append(mark_of(p))
            al_lengths[p.article].append(p.length)
        elif p.draw or p.kind != PERIM:
            lin[(p.article, p.kind)] += p.length
    for (art, kind, L), marks in sorted(al_groups.items(), key=lambda kv: (kv[0][0], -kv[0][2])):
        n = len(marks)
        sp.aluminium.append({"Артикул": art, "Наименование": kind, "Марка": ", ".join(sorted({m for m in marks if m})),
                             "Длина, мм": L, "Кол., шт": n, "Масса 1 м, кг": alu[art],
                             "Масса, кг": round(alu[art] * L * n / 1000, 2) if alu[art] else None})

    # уплотнители (каталог: стойка - ТПУ-6002, ригель - ТПУ-6001, прижим - ТПУ-007ММ) - по две нитки
    # вдоль остекления, уже в деталях фасада (ригель из стоечного профиля - ТПУ-6002, фальш-ригель - свой)
    uc = card["уплотнители"]
    # уплотнители набора периметра уже входят в эти 2 нитки - отдельно не считаем
    for (art, kind), L in list(lin.items()):
        if kind == PERIM and art in (uc["стойки"], uc["ригели"]):
            del lin[(art, kind)]
    for (art, kind), L in sorted(lin.items()):
        sp.linear.append({"Артикул": art, "Наименование": kind, "Кол., м.п.": round(L / 1000, 2)})

    # ---------- раскрой алюминия ----------
    for art, lengths in sorted(al_lengths.items()):
        stock = stocks.get(art, stock_def)
        bars = cut_plan(lengths, stock, kerf)
        long_ = [L for L in lengths if L + kerf > stock]
        if long_:
            sp.warnings.append(f"{art}: деталь {max(long_):g} мм длиннее хлыста {stock:g} мм - нужен спецзаказ")
        sp.cuts.append({"Артикул": art, "Хлыст, мм": stock, "Хлыстов, шт": len(bars),
                        "Отход, %": round(100 * (1 - sum(lengths) / (len(bars) * stock)), 1) if bars else 0,
                        "Масса хлыстов, кг": round(len(bars) * stock / 1000 * alu[art], 2) if alu[art] else None,
                        "Раскладки": [(bar, round(stock - sum(bar) - kerf * len(bar), 1)) for bar in bars]})

    # ---------- стёкла ----------
    for i, g in enumerate(fac.glasses, 1):
        sp.glasses.append({"Марка": g.mark or f"{g.kind}_{i:02d}", "Ширина, мм": g.width, "Высота, мм": g.height,
                           "Площадь, м²": round(g.width * g.height / 1e6, 3), "Масса, кг": g.weight,
                           "Формула": obj.формула})

    # ---------- штучные ----------
    def add_piece(art, name, n, mass=None, note=""):
        sp.pieces.append({"Артикул": art, "Наименование": name, "Кол., шт": n, "Масса ед., кг": mass, "Примечание": note})

    for art, kind, n in fac.pieces:
        code = art.split(" (")[0]
        mass = ins_mass.get(code)
        m = re.match(r"^(\S+ ?\d+) L=(\d+(?:[.,]\d+)?)", art)        # "КПС 714 L=150" - масса по кг/м
        if mass is None and m and m.group(1) in alu:
            mass = round(alu[m.group(1)] * float(m.group(2).replace(",", ".")) / 1000, 3)
        m = re.match(r"^(\S+) L=(\d+(?:[.,]\d+)?)", art)                # "КП1336-96-с L=88" - подрезанная закладная
        if mass is None and m and ins_mass.get(m.group(1)) and ins_len.get(m.group(1)):
            mass = round(ins_mass[m.group(1)] * _num(m.group(2)) / ins_len[m.group(1)], 3)
        add_piece(art, kind, n or None, mass, "" if n else "количество уточнить")
    n_gl = len(fac.glasses)
    for col, name in (("Подкладка опорная", "Подкладка опорная"), ("Подкладка фиксир.", "Подкладка фиксирующая")):
        for art, q in _kit_items(kit.get(col, "")):
            add_piece(art, f"{name}, 2 компл. на стекло", q * 2 * n_gl, None, f"комплект: {kit[col]} (стр. 47)")
    vc = card["винты_прижима"]
    clamps = [p for p in fac.parts if p.kind == "Прижим"]
    if kit.get("Винт прижима"):
        add_piece(kit["Винт прижима"], f"Крепление прижимов, шаг {vc['шаг']} мм",
                  sum(int(p.length // vc["шаг"]) + 1 for p in clamps), None, "каталог стр. 50")
    # дополнительное крепление вертикальных прижимов винтами с буром DIN 7504-K шагом 1 м (стр. 50):
    # обязательно с Т50-09, рекомендуется при заполнении от 24 мм
    need = "Т50-09" in kit.get("Термовставка", "")
    if need or obj.заполнение >= 24:
        art = kit.get("Винт под подкладку") or "ВС 5,5 А2 DIN7504-K (длина - как основного винта)"
        n = sum(int(p.length // vc["шаг_бур"]) + 1 for p in clamps if p.vertical)
        add_piece(art, f"Доп. крепление вертикальных прижимов, шаг {vc['шаг_бур']} мм", n, None,
                  "стр. 50: " + ("обязательно (Т50-09)" if need else "рекомендуется (заполнение от 24 мм)"))
    if капельник:
        # стр. 146: в стойки над каждым разрывом, но не реже чем через 6 м -> цельная стойка: 1 + на каждые 6 м
        kc = card["капельник"]
        n = sum(1 + int((hi - lo - 1) // kc["не_реже_мм"]) for lo, hi in fac.post_ends.values())
        add_piece(kc["артикул"], "Капельник в стойку", n, None,
                  "стр. 146: над каждым разрывом, не реже чем через 6 м; подрезать по толщине заполнения и прижиму")
    sp.warnings += fac.warnings
    return sp


# ======================================================================================
def save_excel(specs: list[Spec], path: Path) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    wb.remove(wb.active)
    thin = Side(style="thin")
    B = Border(left=thin, right=thin, top=thin, bottom=thin)
    F, FB, FT = Font(name="Arial", size=12), Font(name="Arial", size=12, bold=True), Font(name="Arial", size=14, bold=True)
    HEAD = PatternFill("solid", start_color="FFFFCC", end_color="FFFFCC")
    SEC = PatternFill("solid", start_color="EDEDED", end_color="EDEDED")
    WRAP = Alignment(wrap_text=True, vertical="center")
    NOWRAP = Alignment(wrap_text=False, vertical="center")
    CEN = Alignment(horizontal="center", vertical="center", wrap_text=True)

    def put(ws, r, c, v, font=F, fill=None, align=WRAP, border=True):
        cell = ws.cell(r, c, v)
        cell.font, cell.alignment = font, align
        if fill:
            cell.fill = fill
        if border:
            cell.border = B
        return cell

    def table(ws, r, cols, widths, rows):
        for c, (name, w) in enumerate(zip(cols, widths), 1):
            put(ws, r, c, name, FB, HEAD, CEN)
            ws.column_dimensions[get_column_letter(c)].width = w
        for row in rows:
            r += 1
            for c, v in enumerate(row, 1):
                put(ws, r, c, v, align=CEN if isinstance(v, (int, float)) else WRAP)
        return r + 2

    for sp in specs:
        t = sp.title[:24]
        # ---------- Спецификация (ГОСТ 21.101, форма 7) ----------
        ws = wb.create_sheet(f"{t} Спецификация")
        put(ws, 1, 1, f"Спецификация: {sp.title}", FT, align=NOWRAP, border=False)
        cols = ["Поз.", "Обозначение", "Наименование", "Кол.", "Ед.", "Масса ед., кг", "Примечание"]
        widths = [7, 24, 46, 10, 7, 13, 40]
        r = 3
        for c, (name, w) in enumerate(zip(cols, widths), 1):
            put(ws, r, c, name, FB, HEAD, CEN)
            ws.column_dimensions[get_column_letter(c)].width = w
        pos, total_mass = 0, 0.0

        def section(name):
            nonlocal r
            r += 1
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=len(cols))
            put(ws, r, 1, name, FB, SEC, Alignment(horizontal="center"))

        def line(art, name, qty, unit, mass, note):
            nonlocal r, pos
            r += 1
            pos += 1
            for c, v in enumerate([pos, art, name, qty, unit, mass, note], 1):
                put(ws, r, c, v, align=CEN if c in (1, 4, 5, 6) else WRAP)

        section("Профили алюминиевые")
        for row in sp.aluminium:
            kgm = row["Масса 1 м, кг"]
            line(row["Артикул"], f'{row["Наименование"]} L={row["Длина, мм"]:g}' + (f' ({row["Марка"]})' if row["Марка"] else ""),
                 row["Кол., шт"], "шт", round(kgm * row["Длина, мм"] / 1000, 3) if kgm else None, "" if kgm else "масса - по каталогу системы рамы")
            total_mass += row["Масса, кг"] or 0
        section("Заполнение")
        for g in sp.glasses:
            line(g["Марка"], f'Стеклопакет {g["Формула"]} {g["Ширина, мм"]:g}x{g["Высота, мм"]:g}', 1, "шт", g["Масса, кг"],
                 f'{g["Площадь, м²"]} м²')
            total_mass += g["Масса, кг"] or 0
        section("Погонажные изделия")
        for row in sp.linear:
            line(row["Артикул"], row["Наименование"], row["Кол., м.п."], "м.п.", None, "")
        section("Комплектующие")
        for row in sp.pieces:
            line(row["Артикул"], row["Наименование"], row["Кол., шт"], "шт", row["Масса ед., кг"], row["Примечание"])
            total_mass += (row["Масса ед., кг"] or 0) * (row["Кол., шт"] or 0)
        r += 2
        put(ws, r, 3, "Масса витража (алюминий, стекло, закладные), кг", FB)
        put(ws, r, 6, round(total_mass, 1), FB, align=CEN)
        ws.freeze_panes = "A4"

        # ---------- Исходные данные и замечания ----------
        ws = wb.create_sheet(f"{t} Данные")
        put(ws, 1, 1, f"Исходные данные: {sp.title}", FT, align=NOWRAP, border=False)
        r = 3
        for k, v in sp.params:
            put(ws, r, 1, k, FB)
            put(ws, r, 2, v)
            r += 1
        if sp.warnings:
            r += 1
            put(ws, r, 1, "Проверить:", Font(name="Arial", size=12, bold=True, color="C00000"), border=False)
            for w in sp.warnings:
                r += 1
                ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=2)
                put(ws, r, 1, w, Font(name="Arial", size=12, color="C00000"), border=False)
        ws.column_dimensions["A"].width, ws.column_dimensions["B"].width = 30, 80

        # ---------- Раскрой алюминия ----------
        ws = wb.create_sheet(f"{t} Раскрой")
        put(ws, 1, 1, f"Раскрой алюминия: {sp.title}", FT, align=NOWRAP, border=False)
        r = table(ws, 3, ["Артикул", "Хлыст, мм", "Хлыстов, шт", "Отход, %", "Масса хлыстов, кг"], [16, 12, 13, 11, 18],
                  [[c["Артикул"], c["Хлыст, мм"], c["Хлыстов, шт"], c["Отход, %"], c["Масса хлыстов, кг"]] for c in sp.cuts])
        for c in sp.cuts:
            put(ws, r, 1, f'{c["Артикул"]} - хлыст {c["Хлыст, мм"]:g} мм', FB, border=False)
            for k, (bar, rest) in enumerate(c["Раскладки"], 1):
                r += 1
                put(ws, r, 1, f"хлыст {k}")
                ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=4)
                put(ws, r, 2, " + ".join(f"{x:g}" for x in bar))
                put(ws, r, 5, f"остаток {rest:g}", align=CEN)
            r += 2

        # ---------- Стёкла ----------
        ws = wb.create_sheet(f"{t} Стёкла")
        put(ws, 1, 1, f"Ведомость стёкол: {sp.title}", FT, align=NOWRAP, border=False)
        table(ws, 3, ["Марка", "Ширина, мм", "Высота, мм", "Площадь, м²", "Масса, кг", "Формула"], [12, 13, 13, 13, 12, 22],
              [[g["Марка"], g["Ширина, мм"], g["Высота, мм"], g["Площадь, м²"], g["Масса, кг"], g["Формула"]] for g in sp.glasses])

    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path
