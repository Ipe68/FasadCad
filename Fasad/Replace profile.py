"""Замена профиля по маркам - команды AutoCAD ЗАМЕНАСТОЙКИ (REPLACEPOST) и ЗАМЕНАРИГЕЛЯ (REPLACEBEAM).

Команда собирает выбранные полилинии профилей (подсвечивая их), первая строка файла выбора -
"kind:стойка"/"kind:ригель". Для всех выбранных марок считается ОДИН список допустимых профилей
по самой нагруженной: тип, блок в библиотеке, моменты по фоновому расчёту Лоры, для ригеля -
закладная под вес и глубина не больше примыкающей стойки, для стойки - глубина не меньше
примыкающих ригелей. Правка пишется в таблицу профилей, чертёж перестраивается.
"""

import json
import re
import subprocess
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

from fasad.acad_link import Acad, AcadError
from fasad.constructions import (add_profile_row_code, assign_blocks, bbox, define_block_code, params_from_attrs,
                                 parse_blocks, parse_profile_spans, parse_profile_tables, read_blocks_code,
                                 set_profile_code, spans_code, split_axes)
from fasad.dialog import ask_profile, report
from fasad.facade import ObjectParams, build, load_system
from fasad.library import load
from fasad.lisp_draw import _axes_lines, parse_axes_file, parse_markers, read_axes_code
from fasad.lora_link import Statics, beams_with_fixing, fixing_holes, norm_art, to_float
from fasad.run import postroit_vse

HERE = Path(__file__).parent
SEL = Path("C:/temp/fasad_replace.txt")
LORA_CALC = Path(r"C:\cad\Lora_2_2\calc_mark.py")
TOL = 60.0          # допуск попадания выбора в ось/пролёт, мм
PROJECTS = Path(r"C:\cad\Projects")


def read_selection() -> tuple[str, list[tuple[str, float, float, float, float]]]:
    """Первая строка - "kind:стойка"/"kind:ригель", далее строки "слой;minx;miny;maxx;maxy"."""
    kind, out = "стойка", []
    if SEL.exists():
        lines = SEL.read_text(encoding="cp1251").splitlines()
        if lines and lines[0].startswith("kind:"):
            kind = lines[0].split(":", 1)[1].strip() or kind
            lines = lines[1:]
        for ln in lines:
            f = ln.split(";")
            if len(f) == 5:
                try:
                    out.append((f[0], *[float(v) for v in f[1:]]))
                except ValueError:
                    pass
    return kind, out


def weight_map(fac) -> dict:
    """(округлённый y, номер стойки слева, справа) -> вес стекла на ригеле."""
    out = {}
    for where, wt, *_ in fac.fixings:
        try:
            y_s, posts = where.split()
            ia, ib = (int(p[1:]) for p in posts.split("-"))
            out[(round(float(y_s[1:])), ia, ib)] = wt
        except (ValueError, IndexError):
            continue
    return out


def span_weight(fac, y: float, xl: float, xr: float) -> float:
    try:
        ia, ib = fac.post_x.index(xl) + 1, fac.post_x.index(xr) + 1
    except ValueError:
        return 0.0
    return weight_map(fac).get((round(y), ia, ib), 0.0)


def spans_of_mark(fac, mark: str) -> list[tuple]:
    """Все пролёты марки ригеля: [(y, xl, xr)]."""
    return [(y, xl, xr) for y, xl, xr, mk in (fac.marks or []) if mk == mark]


def mark_weight(fac, mark: str, spans) -> float:
    """Вес стекла на марке: максимум по её пролётам (у верхнего ригеля 0)."""
    if mark.startswith("S"):
        return 0.0
    return max((span_weight(fac, *s) for s in spans), default=0.0)


def post_art(fac, n: int) -> str:
    """Текущий артикул стойки S{n} (с учётом правок таблицы профилей)."""
    p = next((p for p in fac.parts if p.where == f"S{n}" and p.kind == "Стойка"), None)
    return p.article if p else ""


def depth_of(system, art: str) -> float:
    r = next((r for r in system.profiles if norm_art(r["Шифр"]) == norm_art(art)), None)
    return to_float(r["Габарит, мм"]) or 0.0 if r else 0.0


def mark_geometry(kind: str, fac, mark: str, span) -> dict:
    """Размеры марки для фонового расчёта требований (схема Лоры)."""
    h_m = max((hi - lo for lo, hi in fac.post_ends.values()), default=0.0)
    if kind == "стойка":
        n = int(mark[1:])
        px = fac.post_x[n - 1]
        left = px - fac.post_x[n - 2] if n > 1 else None
        right = fac.post_x[n] - px if n < len(fac.post_x) else None
        b1 = round((left + right) / 2) if left and right else round(left or right or 0)
        return {"b1": b1, "b2": 0, "l1": 0, "l2": 0, "h_m": round(h_m), "t_glass": fac.glass_mm}
    y, xl, xr = span
    ys = sorted(fac.bays.get((xl, xr), []))
    i = min(range(len(ys)), key=lambda j: abs(ys[j] - y)) if ys else 0
    above = ys[i + 1] - ys[i] if ys and i + 1 < len(ys) else 0.0
    below = ys[i] - ys[i - 1] if 0 < i else 0.0
    k = fac.post_x.index(xl) if xl in fac.post_x else 0
    left = xl - fac.post_x[k - 1] if k > 0 else None
    right = fac.post_x[k + 1] - xl if k + 1 < len(fac.post_x) else None
    b1 = round((left + right) / 2) if left and right else round(left or right or 0)
    return {"b1": b1, "b2": round(xr - xl - 20), "l1": round(max(0.0, above - 24)),
            "l2": round(max(0.0, below - 24)), "h_m": round(h_m), "t_glass": fac.glass_mm}


def mark_requirements(kind: str, fac, mark: str, spans) -> dict | None:
    """Требуемые моменты для марки: Лора считает в фоне по геометрии марки. None - расчёт недоступен."""
    if not LORA_CALC.exists():
        return None
    geoms = [mark_geometry(kind, fac, mark, s) for s in spans] if spans \
        else [mark_geometry(kind, fac, mark, None)]
    reqs: dict[str, float] = {}
    try:
        for gm in geoms:
            with tempfile.TemporaryDirectory() as td:
                inp, outp = Path(td) / "in.json", Path(td) / "out.json"
                inp.write_text(json.dumps(gm, ensure_ascii=False), encoding="utf-8")
                r = subprocess.run([sys.executable, str(LORA_CALC), str(inp), str(outp)],
                                   capture_output=True, timeout=180)
                if r.returncode != 0 or not outp.exists():
                    return None
                d = json.loads(outp.read_text(encoding="utf-8"))["требования"]
            keys = ("стойка_треб_ix_см4", "стойка_треб_wx_см3") if kind == "стойка" \
                else ("ригель_треб_ix_см4", "ригель_треб_iy_см4", "ригель_треб_wx_см3")
            for kname in keys:
                reqs[kname] = max(reqs.get(kname, 0.0), float(d.get(kname) or 0.0))
        return reqs
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def post_min_depth(system, fac, mark: str) -> float:
    """Минимально допустимая глубина стойки: не меньше глубины самого глубокого примыкающего ригеля."""
    n = int(mark[1:])
    px = fac.post_x[n - 1]
    arts = [a for (y, xl, xr), a in fac.beam_art.items() if xl == px or xr == px]
    return max((depth_of(system, a) for a in arts), default=0.0)


def beam_cap(system, fac, mark: str, spans) -> float:
    """Максимально допустимая глубина ригеля: не больше глубины самой мелкой примыкающей стойки."""
    caps = []
    for y, xl, xr in spans:
        try:
            ia, ib = fac.post_x.index(xl) + 1, fac.post_x.index(xr) + 1
        except ValueError:
            continue
        caps.append(min(depth_of(system, post_art(fac, ia)), depth_of(system, post_art(fac, ib)) or 1e9))
    return min(caps, default=0.0)


def merged_options(system, lib, picks) -> tuple[list[tuple[str, str]], bool]:
    """Один список допустимых профилей для всех выбранных марок - по самой нагруженной.
    picks - [(витраж, марка, kind, fac, span)]. Возвращает ([(артикул, расшифровка)], статика_учтена)."""
    kind = picks[0][2]
    rows = [r for r in system.profiles if r["Тип"] == kind and r["Группа"] != "фальш"]
    if lib is not None:
        rows = [r for r in rows if lib.has(r["Шифр"])]
    if kind == "стойка":
        min_d = max((post_min_depth(system, fac, mark) for _v, mark, _k, fac, _s in picks), default=0.0)
        if min_d:
            rows = [r for r in rows if (to_float(r["Габарит, мм"]) or 0.0) >= min_d]
    else:
        cap = min((beam_cap(system, fac, mark, spans_of_mark(fac, mark)) or 1e9
                   for _v, mark, _k, fac, _s in picks), default=1e9)
        if cap < 1e9:
            rows = [r for r in rows if (to_float(r["Габарит, мм"]) or 0.0) <= cap + 1e-6]
        wt = max((mark_weight(fac, mark, spans_of_mark(fac, mark)) for _v, mark, _k, fac, _s in picks),
                 default=0.0)
        holes = fixing_holes(Statics(system, Path(".")), wt)
        allow = beams_with_fixing(system, holes) if holes else set()
        rows = [r for r in rows if norm_art(r["Шифр"]) in allow]
    reqs: dict[str, float] = {}
    stat_ok = True
    for _v, mark, _k, fac, span in picks:
        r = mark_requirements(kind, fac, mark, spans_of_mark(fac, mark) if kind == "ригель" else [])
        if r is None:
            stat_ok = False
            continue
        for k2, v in r.items():
            reqs[k2] = max(reqs.get(k2, 0.0), v)
    if reqs:
        jx_k = "стойка_треб_ix_см4" if kind == "стойка" else "ригель_треб_ix_см4"
        wx_k = "стойка_треб_wx_см3" if kind == "стойка" else "ригель_треб_wx_см3"
        rows = [r for r in rows if (to_float(r["Jx"]) or 0.0) >= reqs[jx_k]
                and (to_float(r["Wx"]) or 0.0) >= reqs[wx_k]]
        if kind == "ригель":
            rows = [r for r in rows if (to_float(r["Jy"]) or 0.0) >= reqs["ригель_треб_iy_см4"]]
    rows.sort(key=lambda r: to_float(r["Габарит, мм"]) or 0.0)
    return [(r["Шифр"], f'{r["Группа"]}, габарит {r["Габарит, мм"]} мм, {r["Масса 1м, кг"]} кг/м, '
                         f'Jx={r["Jx"]}, Jy={r["Jy"]}, Wx={r["Wx"]} (стр. {r["Стр."]})') for r in rows], stat_ok


def current_article(kind: str, fac, mark: str, span) -> str:
    if kind == "стойка":
        return post_art(fac, int(mark[1:]))
    return fac.beam_art.get((round(span[0], 3), span[1], span[2]), "") if span else ""


def locate(sel, facs, kind: str) -> tuple[list, int]:
    """Выбранные полилинии -> [(имя витража, марка, kind, fac, span)] своего вида; пропуски - число."""
    picks, skipped, seen = [], 0, set()
    for _layer, x0, y0, x1, y1 in sel:
        xc, yc = (x0 + x1) / 2, (y0 + y1) / 2
        vertical = abs(x1 - x0) <= abs(y1 - y0)
        if (kind == "стойка") != vertical:
            skipped += 1                      # не тот вид - пропуск
            continue
        for _g, obj, fac, _owner in facs:
            gx0, gy0, gx1, gy1 = bbox(_g)
            if not (gx0 - TOL <= xc <= gx1 + TOL and gy0 - TOL <= yc <= gy1 + TOL):
                continue
            if vertical:
                px = min(fac.post_x, key=lambda p: abs(p - xc))
                if abs(px - xc) < TOL:
                    mark = f"S{fac.post_x.index(px) + 1}"
                    key = (obj.имя, mark)
                    if key not in seen:
                        seen.add(key)
                        picks.append((obj.имя, mark, "стойка", fac, None))
            else:
                spans = [(y, xl, xr, mk) for y, xl, xr, mk in (fac.marks or [])
                         if mk.startswith("R") and abs(y - yc) < TOL and xl - 1 <= xc <= xr + 1]
                if spans:
                    y, xl, xr, mk = max(spans, key=lambda s: s[2] - s[1])
                    key = (obj.имя, mk, y, xl, xr)
                    if key not in seen:
                        seen.add(key)
                        picks.append((obj.имя, mk, "ригель", fac, (y, xl, xr)))
            break
    return picks, skipped


def plan_edits(picks) -> tuple[list, list]:
    """Полная марка меняет артикул; при выборе части её пролётов создаётся новая марка."""
    grouped = {}
    for v, mark, _kind, fac, span in picks:
        grouped.setdefault((v, mark), (fac, []))
        if span not in grouped[(v, mark)][1]:
            grouped[(v, mark)][1].append(span)
    used = {m for _v, _m, _k, fac, _s in picks for *_coords, m in fac.marks}
    edits, additions = [], []
    for (v, mark), (fac, selected) in grouped.items():
        all_spans = spans_of_mark(fac, mark)
        if selected[0] is None or len(selected) == len(all_spans):
            edits.append((v, mark))
            continue
        suffix = re.match(r"R\d+(.*)$", mark).group(1)
        n = 2
        while f"R{n}{suffix}" in used:
            n += 1
        new_mark = f"R{n}{suffix}"
        used.add(new_mark)
        additions.append((v, mark, new_mark, selected))
    return edits, additions


def main():
    kind, sel = read_selection()
    if not sel:
        print("Ничего не выбрано.")
        return
    acad = Acad()
    systems_dir = HERE / "systems"
    acad.run_lisp(define_block_code())
    acad.run_lisp(read_axes_code())
    acad.run_lisp(read_blocks_code())
    axes, markers = parse_axes_file(), parse_markers()
    lines = _axes_lines()
    blocks = parse_blocks(lines)
    sysname = next((a.get("СИСТЕМА", "").strip() for *_xy, a, _h in blocks if a.get("СИСТЕМА", "").strip()),
                   "КП50К")
    system = load_system(systems_dir, sysname or "КП50К")
    groups = split_axes(axes, system.card["система"]["ширина_профиля"] + 1)
    owners = assign_blocks(groups, blocks)
    tables = parse_profile_tables(lines)
    spans = parse_profile_spans(lines)

    defaults = ObjectParams(заполнение=50, вариант="", выпуск_низ=20, выпуск_верх=10)
    facs, glass_no = [], 0
    for g, owner in zip(groups, owners):
        attrs = owner[2] if owner else {}
        obj, _warn = params_from_attrs(defaults, attrs)
        obj = replace(obj, имя=attrs.get("ИМЯ", f"ВТ-{len(facs) + 1}?"),
                      профили=tables.get(obj.имя, {}), пролеты=spans.get(obj.имя, {}))
        gx0, gy0, gx1, gy1 = bbox(g)
        mk = [m for m in markers if gx0 - 100 < m[0] < gx1 + 100 and gy0 - 100 < m[1] < gy1 + 100]
        fac = build(g, system, obj, mk)
        for gl in fac.glasses:
            glass_no += 1
            gl.mark = f"{gl.kind}_{glass_no:02d}"
        facs.append((g, obj, fac, owner))

    _, lib = load(system, systems_dir)
    picks, skipped = locate(sel, facs, kind)
    if not picks:
        print(f"Полилинии не попали ни в одну марку ({'стойки' if kind == 'стойка' else 'ригели'}"
              f"{', пропущено чужого вида: ' + str(skipped) if skipped else ''}).")
        return
    if skipped:
        print(f"Пропущено полилиний другого вида: {skipped}")
    print(f"Марки ({kind}):", ", ".join(f"{v}:{m}" for v, m, *_ in picks))

    opts, stat_ok = merged_options(system, lib, picks)
    if not opts:
        from tkinter import messagebox
        geo = ("глубина в пределах примыкающих стоек / закладная под вес" if kind == "ригель"
               else "глубина не меньше примыкающих ригелей")
        why = f"блок в библиотеке / {geo} / моменты по расчёту Лоры" if stat_ok \
            else f"блок в библиотеке / {geo} (Лора недоступна - моменты не проверены)"
        messagebox.showwarning("Замена профиля",
                               f"Нет допустимых профилей {kind} для выбранных марок\n({why})")
        return
    cur = current_article(kind, picks[0][3], picks[0][1], picks[0][4])
    wt_txt = ""
    if kind == "ригель":
        wt = max(mark_weight(fac, mark, spans_of_mark(fac, mark)) for _v, mark, _k, fac, _s in picks)
        wt_txt = f"   вес до {wt:g} кг"
    title = f"Замена {kind}: {', '.join(m for _, m, *_ in picks)}{wt_txt}"
    if not stat_ok:
        title += "   (статика не учтена - Лора недоступна)"
    choice = ask_profile(title, opts, cur)
    if not choice:
        print("Замена отменена - чертёж не менялся.")
        return
    edit_keys, additions = plan_edits(picks)
    edits = [(v, m, choice) for v, m in edit_keys]
    print("  ->", choice)
    targets = {(v, m) for v, m in edit_keys} | {(v, old) for v, old, _new, _spans in additions}
    legacy = False
    for _x, _y, attrs, _handle in parse_blocks(lines, "P"):
        marks = [m.strip() for m in attrs.get("МАРКА", "").split(",") if m.strip()]
        if len(marks) > 1 and any((attrs.get("ВИТРАЖ", ""), m) in targets for m in marks):
            legacy = True
    if legacy:
        print("Обновление старой таблицы: отдельная строка для каждой марки")
        postroit_vse(acad, system, defaults, systems_dir)
    for v, old, new, selected in additions:
        source = next((row for row in parse_blocks(lines, "P")
                       if row[2].get("ВИТРАЖ") == v
                       and old in [m.strip() for m in row[2].get("МАРКА", "").split(",")]), None)
        if source is None:
            report(f"Замена {kind} - не выполнена", [f"Не найдена строка {old} для разделения на {new}"], [])
            return
        x, y, attrs, _handle = source
        values = dict(attrs)
        values.update({"МАРКА": new, "АРТИКУЛ": choice, "КОЛ": str(len(selected)),
                       "ПРОЛЕТ": spans_code(selected)})
        print(f"  {old} -> {new}: пролётов {len(selected)}")
        res = acad.run_lisp(add_profile_row_code(x, y, values), timeout=60)
        if res != "ок":
            report(f"Замена {kind} - не выполнена", [f"Новая строка {new}: {res}"], [])
            return
    if edits:
        res = acad.run_lisp(set_profile_code(edits), timeout=60)
        print("Таблица профилей:", res)
        if res != f"правок {len(edits)}":
            report(f"Замена {kind} - не выполнена",
                   [f"Таблица профилей: {res}, ожидалось правок: {len(edits)}",
                    "Чертёж не перестраивался. Проверьте таблицу профилей."], [])
            return
    result = postroit_vse(acad, system, defaults, systems_dir)
    warns = [f"{o.имя}: {w}" for o, f in result for w in f.warnings]
    report_edits = edits + [(v, new, choice) for v, _old, new, _selected in additions]
    report(f"Замена {kind} - готово",
           [f"Марок заменено: {len(report_edits)} ({kind})"] + [f"{v}, {m} -> {choice}" for v, m, _ in report_edits], warns)


if __name__ == "__main__":
    try:
        main()
    except (AcadError, OSError) as e:
        print("ОШИБКА:", e)
