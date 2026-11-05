"""Спецификация по всем витражам чертежа -> один Excel (по образцу пользователя
C:\\cad\\Клод_Витражи_Песочный_стойки_ригели.xlsx).

Листы:
  Спецификация  - сводная, форма ГОСТ 21.101 (поз., обозначение, наименование, кол., ед., масса ед., примечание)
  Конструкции   - перечень витражей
  <профиль>     - по каждому алюминиевому профилю: конструкция, марка, кол-во, длина по длинной / короткой
                  кромке, тип реза, сумма длин и вес - живыми формулами; итоги по конструкциям
  Раскрой <...> - общий по всем витражам, длина хлыста и пропил в ячейках; таблица "хлыст -> конструкция, марка"
  Стёкла, Погонаж, Комплектующие - с учётом количества одинаковых конструкций
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from .facade import Facade, ObjectParams, System
from .spec import _num, _read_csv, build_spec, cut_plan
from .marks import beam_marks


def _part_rows(fac: Facade, system: System, alu: dict) -> list[dict]:
    """Алюминиевые детали витража с марками и данными реза."""
    card = system.card
    post_mark = {px: f"S{n}" for n, px in enumerate(fac.post_x, 1)}
    bmarks = beam_marks(fac, system)
    rows = []
    for p in fac.parts:
        if p.article not in alu:
            continue
        mark = ""
        if p.kind == card["стойка"]["тип"]:
            mark = post_mark.get(round((p.x0 + p.x1) / 2, 3), "")
        elif (p.kind.startswith(card["ригель"]["тип"]) or p.kind == "Фальш-ригель") and not p.vertical:
            yc = (p.y0 + p.y1) / 2
            mark = next((m for y, xl, xr, m in bmarks if abs(y - yc) < 1 and xl < p.x0 < xr), "")
        rows.append({"art": p.article, "kind": p.kind, "mark": mark, "long": p.length,
                     "short": p.short if p.short is not None else p.length, "cut": p.cut})
    return rows


def _sheet_name(s: str) -> str:
    return re.sub(r"[\\/*?:\[\]]", "-", s)[:31]


def save_excel_vse(results: list[tuple[ObjectParams, Facade]], system: System, path: Path,
                   folder: Path | None = None, title: str = "Объект",
                   хлыст: float | None = None, хлысты: dict | None = None) -> Path:
    """хлыст - длина хлыста на весь объект (по умолчанию из карточки), хлысты - свои длины по артикулам."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    folder = folder or Path(__file__).resolve().parent.parent / "systems"
    card = system.card
    alu = {r["Артикул"]: _num(r["Масса, кг/м"]) for r in _read_csv(folder / "КП50К_алюминий.csv")}
    for cfg in system.openings.values():                 # рамы дверей/окон - тоже алюминий (масса - из их каталога)
        alu.setdefault(cfg["рама"], cfg.get("масса_1м"))
    rc = card.get("раскрой", {})
    kerf = rc.get("пропил", 10)

    stock_def = хлыст or rc.get("хлыст", 6000)
    stocks_by_art = {**rc.get("хлысты", {}), **(хлысты or {})}
    specs = [(obj, fac, build_spec(fac, system, obj, obj.имя, folder, obj.капельник, stock_def, stocks_by_art))
             for obj, fac in results]
    parts = [(obj, _part_rows(fac, system, alu)) for obj, fac in results]

    wb = Workbook()
    wb.remove(wb.active)
    thin = Side(style="thin")
    B = Border(left=thin, right=thin, top=thin, bottom=thin)
    F, FB, FT = Font(name="Arial", size=12), Font(name="Arial", size=12, bold=True), Font(name="Arial", size=14, bold=True)
    FR = Font(name="Arial", size=12, color="C00000")
    HEAD = PatternFill("solid", start_color="FFFFCC", end_color="FFFFCC")
    SEC = PatternFill("solid", start_color="EDEDED", end_color="EDEDED")
    WRAP = Alignment(wrap_text=True, vertical="center")
    NOWRAP = Alignment(wrap_text=False, vertical="center")
    CEN = Alignment(horizontal="center", vertical="center", wrap_text=True)

    def put(ws, r, c, v, font=F, fill=None, align=None, border=True, fmt=None):
        cell = ws.cell(r, c, v)
        cell.font = font
        cell.alignment = align or (CEN if isinstance(v, (int, float)) or (isinstance(v, str) and v.startswith("="))
                                   else WRAP)
        if fill:
            cell.fill = fill
        if border:
            cell.border = B
        if fmt:
            cell.number_format = fmt
        return cell

    def heads(ws, r, cols, widths):
        for c, (name, w) in enumerate(zip(cols, widths), 1):
            put(ws, r, c, name, FB, HEAD, CEN)
            ws.column_dimensions[get_column_letter(c)].width = w

    # ===================== Спецификация (ГОСТ 21.101, форма 7) =====================
    ws = wb.create_sheet("Спецификация")
    put(ws, 1, 1, f"Спецификация: {title}", FT, align=NOWRAP, border=False)
    put(ws, 2, 1, f"Конструкции: " + ", ".join(f"{o.имя}×{o.количество}" for o, _ in results), F, align=NOWRAP,
        border=False)
    cols = ["Поз.", "Обозначение", "Наименование", "Кол.", "Ед.", "Масса ед., кг", "Примечание"]
    heads(ws, 4, cols, [7, 26, 50, 11, 7, 13, 44])
    r, pos = 4, 0

    def section(name):
        nonlocal r
        r += 1
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=len(cols))
        put(ws, r, 1, name, FB, SEC, Alignment(horizontal="center"))

    def line(*vals):
        nonlocal r, pos
        r += 1
        pos += 1
        for c, v in enumerate((pos, *vals), 1):
            put(ws, r, c, v, align=CEN if c in (1, 4, 5, 6) else WRAP)

    al_tot: dict[str, float] = defaultdict(float)
    for obj, rows in parts:
        for p in rows:
            al_tot[p["art"]] += p["long"] * obj.количество
    section("Профили алюминиевые (по длине деталей; хлысты - на листах раскроя)")
    names: dict[str, str] = {}
    for _, rows in parts:
        for p in rows:
            names.setdefault(p["art"], p["kind"])      # стоечный профиль ригелем - всё равно "Стойка"
    for art in sorted(al_tot):
        line(art, names[art], round(al_tot[art] / 1000, 2), "м", alu.get(art), "масса - кг на 1 м")
    section("Заполнение")
    glass_tot: dict[tuple, list] = defaultdict(list)
    for obj, fac, sp in specs:
        for g in sp.glasses:
            glass_tot[(g["Формула"], g["Ширина, мм"], g["Высота, мм"], g["Масса, кг"])] += [g["Марка"]] * obj.количество
    for (formula, w, h, m), marks in sorted(glass_tot.items(), key=lambda kv: kv[1][0]):
        line(", ".join(sorted(set(marks))), f"Стеклопакет {formula} {w:g}x{h:g}", len(marks), "шт", m,
             f"{w * h / 1e6:.3f} м²")
    section("Погонажные изделия")
    lin_tot: dict[tuple, float] = defaultdict(float)
    for obj, fac, sp in specs:
        for row in sp.linear:
            lin_tot[(row["Артикул"], row["Наименование"])] += row["Кол., м.п."] * obj.количество
    for (art, name), v in sorted(lin_tot.items()):
        line(art, name, round(v, 2), "м.п.", None, "")
    section("Комплектующие")
    pc_tot: dict[tuple, list] = {}
    for obj, fac, sp in specs:
        for row in sp.pieces:
            key = (row["Артикул"], row["Наименование"])
            rec = pc_tot.setdefault(key, [0, row["Масса ед., кг"], row["Примечание"]])
            rec[0] += (row["Кол., шт"] or 0) * obj.количество
    for (art, name), (n, m, note) in sorted(pc_tot.items()):
        line(art, name, n or None, "шт", m, note)
    ws.freeze_panes = "A5"

    # ===================== Конструкции =====================
    ws = wb.create_sheet("Конструкции")
    put(ws, 1, 1, "Перечень конструкций", FT, align=NOWRAP, border=False)
    heads(ws, 3, ["Марка", "Кол., шт", "Ширина, мм", "Высота, мм", "Заполнение", "Формула", "Вариант", "Монтаж",
                  "Проверить"], [10, 9, 12, 12, 12, 18, 22, 12, 70])
    r = 3
    for obj, fac, sp in specs:
        xs = [v for p in fac.parts for v in (p.x0, p.x1) if p.kind == card["стойка"]["тип"]]
        ys = [v for lo_hi in fac.post_ends.values() for v in lo_hi]
        r += 1
        vals = [obj.имя, obj.количество, round(max(xs) - min(xs)) if xs else None, round(max(ys) - min(ys)) if ys else None,
                obj.заполнение, obj.формула, obj.вариант, obj.монтаж, "; ".join(sp.warnings) or "-"]
        for c, v in enumerate(vals, 1):
            put(ws, r, c, v, FR if c == 9 and sp.warnings else F)

    # ===================== Листы по профилям + раскрой =====================
    by_art: dict[str, list] = defaultdict(list)
    for obj, rows in parts:
        for p in rows:
            by_art[p["art"]].append((obj, p))
    order = [card["стойка"]["артикул"], card["ригель"]["артикул"]]
    arts = order + sorted(a for a in by_art if a not in order)
    warn_stock = []
    for art in arts:
        if art not in by_art:
            continue
        items = by_art[art]
        kgm = alu.get(art)
        ws = wb.create_sheet(_sheet_name(f"{art} {items[0][1]['kind']}"))
        put(ws, 1, 1, f"Спецификация: {items[0][1]['kind'].lower()} {art} по конструкциям", FT, align=NOWRAP, border=False)
        put(ws, 2, 1, "Профиль", FB)
        put(ws, 2, 2, art, FB)
        put(ws, 3, 1, "Масса, кг/м", FB, align=NOWRAP)
        put(ws, 3, 2, kgm if kgm is not None else "нет данных", F if kgm is not None else FR)
        put(ws, 3, 3, "каталог, «Геометрические характеристики»", F, align=NOWRAP, border=False)
        heads(ws, 5, ["Конструкция", "Марка", "Кол-во, шт", "Длина по длинной кромке, мм",
                      "Длина по короткой кромке, мм", "Тип реза", "Σ длина, м", "Масса, кг"],
              [14, 12, 11, 16, 16, 16, 12, 12])
        grp: dict[tuple, int] = defaultdict(int)
        for obj, p in items:
            grp[(obj.имя, p["mark"], p["long"], p["short"], p["cut"])] += obj.количество
        r = 5
        first = r + 1
        for (name, mark, lg, sh, cut), n in sorted(grp.items(), key=lambda kv: (kv[0][0], kv[0][1], -kv[0][2])):
            r += 1
            for c, v in enumerate([name, mark, n, lg, sh, cut, f"=C{r}*D{r}/1000",
                                   f"=G{r}*$B$3" if kgm is not None else None], 1):
                put(ws, r, c, v, fmt="0.00" if c in (7, 8) else None)
        last = r
        r += 2
        put(ws, r, 1, "Итого по конструкциям", FB, align=NOWRAP, border=False)
        r += 1
        heads(ws, r, ["Конструкция", "", "Кол-во, шт", "", "", "", "Σ длина, м", "Масса, кг"], [14, 12, 11, 16, 16, 16, 12, 12])
        for name in sorted({k[0] for k in grp}):
            r += 1
            put(ws, r, 1, name)
            put(ws, r, 3, f'=SUMIF($A${first}:$A${last},A{r},$C${first}:$C${last})')
            put(ws, r, 7, f'=SUMIF($A${first}:$A${last},A{r},$G${first}:$G${last})', fmt="0.00")
            put(ws, r, 8, f'=SUMIF($A${first}:$A${last},A{r},$H${first}:$H${last})' if kgm is not None else None, fmt="0.00")
        r += 1
        put(ws, r, 1, "ВСЕГО", FB)
        put(ws, r, 3, f"=SUM(C{first}:C{last})", FB)
        put(ws, r, 7, f"=SUM(G{first}:G{last})", FB, fmt="0.00")
        put(ws, r, 8, f"=SUM(H{first}:H{last})" if kgm is not None else None, FB, fmt="0.00")

        # ---------- раскрой ----------
        stock = stocks_by_art.get(art, stock_def)
        pieces = []
        for obj, p in items:
            pieces += [(p["long"], obj.имя, p["mark"] or p["kind"])] * obj.количество
        bars = cut_plan([x[0] for x in pieces], stock, kerf)
        pool = sorted(pieces, key=lambda x: -x[0])
        mapping = []
        for k, bar in enumerate(bars, 1):
            for L in bar:
                i = next(j for j, x in enumerate(pool) if x[0] == L)
                mapping.append((k, pool[i][1], pool[i][2], L))
                pool.pop(i)
        ws = wb.create_sheet(_sheet_name(f"Раскрой {art}"))
        put(ws, 1, 1, f"Раскрой {items[0][1]['kind'].lower()} ({art})", FT, align=NOWRAP, border=False)
        put(ws, 2, 1, "Длина хлыста, мм", FB, align=NOWRAP)
        put(ws, 2, 2, stock)
        put(ws, 2, 3, "Пропил учтён на каждую деталь (длина + пропил).", F, align=NOWRAP, border=False)
        put(ws, 3, 1, "Пропил, мм", FB, align=NOWRAP)
        put(ws, 3, 2, kerf)
        heads(ws, 5, ["Хлыст №", "Состав раскроя", "Деталей, шт", "Сумма деталей, мм", "Пропилы, мм", "Остаток, мм",
                      "Остаток, %"], [20, 46, 12, 16, 13, 13, 12])
        for c, (name, w) in enumerate(zip(["Хлыст №", "Конструкция", "Марка", "Длина, мм"], [10, 14, 14, 12]), 10):
            put(ws, 5, c, name, FB, HEAD, CEN)
            ws.column_dimensions[get_column_letter(c)].width = w
        m_first, m_last = 6, 5 + len(mapping)
        for i, (k, name, mark, L) in enumerate(mapping):
            for c, v in enumerate([k, name, mark, L], 10):
                put(ws, 6 + i, c, v)
        r = 5
        for k, bar in enumerate(bars, 1):
            r += 1
            for c, v in enumerate([k, " + ".join(f"{x:g}" for x in bar),
                                   f"=COUNTIF($J${m_first}:$J${m_last},A{r})",
                                   f"=SUMIF($J${m_first}:$J${m_last},A{r},$M${m_first}:$M${m_last})",
                                   f"=C{r}*$B$3", f"=$B$2-D{r}-E{r}", f"=F{r}/$B$2"], 1):
                put(ws, r, c, v, fmt="0.0%" if c == 7 else None)
        b_first, b_last = 6, r
        r += 1
        for c, v in enumerate(["ИТОГО", None, f"=SUM(C{b_first}:C{b_last})", f"=SUM(D{b_first}:D{b_last})",
                               f"=SUM(E{b_first}:E{b_last})", f"=SUM(F{b_first}:F{b_last})",
                               f"=F{r}/(COUNT(A{b_first}:A{b_last})*$B$2)"], 1):
            put(ws, r, c, v, FB, fmt="0.0%" if c == 7 else None)
        r += 2
        put(ws, r, 1, "Хлыстов, шт", FB, align=NOWRAP)
        put(ws, r, 3, f"=COUNT(A{b_first}:A{b_last})", FB)
        put(ws, r + 1, 1, "Расход профиля, м", FB, align=NOWRAP)
        put(ws, r + 1, 3, f"=C{r}*$B$2/1000", FB, fmt="0.00")
        put(ws, r + 2, 1, "Масса, кг", FB, align=NOWRAP)
        put(ws, r + 2, 3, f"=C{r + 1}*{kgm}" if kgm is not None else "нет массы", FB, fmt="0.00")
        long_ = [x for x in pieces if x[0] + kerf > stock]
        note = ("Примечание: раскрой общий по всем конструкциям с учётом количества. Длина детали - по длинной кромке. "
                "Состав раскроя рассчитан программой: при смене длины хлыста или пропила постройте заново.")
        if long_:
            note += f" ВНИМАНИЕ: деталь {max(x[0] for x in long_):g} мм длиннее хлыста - спецзаказ."
        ws.merge_cells(start_row=r + 4, start_column=1, end_row=r + 6, end_column=7)
        put(ws, r + 4, 1, note, FR if long_ else F, align=Alignment(wrap_text=True, vertical="top"), border=False)

    # ===================== Стёкла, погонаж, комплектующие по конструкциям =====================
    ws = wb.create_sheet("Стёкла")
    put(ws, 1, 1, "Ведомость стёкол", FT, align=NOWRAP, border=False)
    heads(ws, 3, ["Конструкция", "Марка", "Кол-во, шт", "Ширина, мм", "Высота, мм", "Площадь 1 шт, м²", "Масса 1 шт, кг",
                  "Формула", "Σ площадь, м²", "Σ масса, кг"], [14, 12, 11, 12, 12, 15, 15, 20, 14, 13])
    r = 3
    for obj, fac, sp in specs:
        for g in sp.glasses:
            r += 1
            for c, v in enumerate([obj.имя, g["Марка"], obj.количество, g["Ширина, мм"], g["Высота, мм"], g["Площадь, м²"],
                                   g["Масса, кг"], g["Формула"], f"=C{r}*F{r}", f"=C{r}*G{r}"], 1):
                put(ws, r, c, v, fmt="0.00" if c in (9, 10) else None)
    r += 1
    put(ws, r, 1, "ВСЕГО", FB)
    put(ws, r, 3, f"=SUM(C4:C{r - 1})", FB)
    put(ws, r, 9, f"=SUM(I4:I{r - 1})", FB, fmt="0.00")
    put(ws, r, 10, f"=SUM(J4:J{r - 1})", FB, fmt="0.00")

    for sheet, key, cols, widths, unit in (
            ("Погонаж", "linear", ["Конструкция", "Кол-во констр.", "Артикул", "Наименование", "На 1 констр., м.п.", "Всего, м.п."],
             [14, 13, 18, 50, 16, 13], "Кол., м.п."),
            ("Комплектующие", "pieces", ["Конструкция", "Кол-во констр.", "Артикул", "Наименование", "На 1 констр., шт", "Всего, шт",
                                         "Примечание"], [14, 13, 30, 46, 15, 11, 44], "Кол., шт")):
        ws = wb.create_sheet(sheet)
        put(ws, 1, 1, sheet, FT, align=NOWRAP, border=False)
        heads(ws, 3, cols, widths)
        r = 3
        for obj, fac, sp in specs:
            for row in getattr(sp, key):
                r += 1
                vals = [obj.имя, obj.количество, row["Артикул"], row["Наименование"], row[unit], f"=B{r}*E{r}"]
                if key == "pieces":
                    vals.append(row["Примечание"])
                for c, v in enumerate(vals, 1):
                    put(ws, r, c, v, fmt="0.00" if key == "linear" and c in (5, 6) else None)

    if warn_stock:
        ws = wb["Конструкции"]
        rr = ws.max_row + 2
        for w in warn_stock:
            put(ws, rr, 1, w, FR, align=NOWRAP, border=False)
            rr += 1

    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path
