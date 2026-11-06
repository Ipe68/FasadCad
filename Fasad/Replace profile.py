"""Замена профиля по маркам - команда AutoCAD ЗАМЕНАПРОФИЛЯ (REPLACEPROFILE).

Читает выбранные полилинии профилей из C:/temp/fasad_replace.txt (строки "слой;minx;miny;maxx;maxy"),
по положению определяет марки (стойки S{n} / ригели R{k}[_в/_н]), для каждой по очереди показывает
допустимые профиля (жёсткий фильтр: тип, блок в библиотеке, закладная под вес стекла марки,
требуемые моменты - Лора считает в фоне по геометрии марки, сеанс из её последнего сеанса),
вписывает правку в таблицу профилей и перестраивает чертёж.
"""

import json
import subprocess
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

from fasad.acad_link import Acad, AcadError
from fasad.constructions import (assign_blocks, bbox, define_block_code, params_from_attrs,
                                 parse_blocks, parse_profile_tables, read_blocks_code, set_profile_code,
                                 split_axes)
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


def read_selection() -> list[tuple[str, float, float, float, float]]:
    out = []
    if SEL.exists():
        for ln in SEL.read_text(encoding="cp1251").splitlines():
            f = ln.split(";")
            if len(f) == 5:
                try:
                    out.append((f[0], *[float(v) for v in f[1:]]))
                except ValueError:
                    pass
    return out


def weight_map(fac) -> dict:
    """(округлённый y, номер стойки слева, номер справа) -> вес стекла на ригеле."""
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


def locate(sel, facs) -> list[tuple[str, str, str, object, tuple | None]]:
    """Выбранные полилинии -> [(имя витража, марка, вид, fac, span|None)] без повторов."""
    picks, seen = [], set()
    for _layer, x0, y0, x1, y1 in sel:
        xc, yc = (x0 + x1) / 2, (y0 + y1) / 2
        vertical = abs(x1 - x0) <= abs(y1 - y0)
        for _g, obj, fac, _owner in facs:
            gx0, gy0, gx1, gy1 = bbox(_g)
            if not (gx0 - TOL <= xc <= gx1 + TOL and gy0 - TOL <= yc <= gy1 + TOL):
                continue
            if vertical:                                    # стойка
                px = min(fac.post_x, key=lambda p: abs(p - xc))
                if abs(px - xc) < TOL:
                    mark = f"S{fac.post_x.index(px) + 1}"
                    key = (obj.имя, mark)
                    if key not in seen:
                        seen.add(key)
                        picks.append((obj.имя, mark, "стойка", fac, None))
            else:                                           # ригель - самый длинный подходящий пролёт
                spans = [(y, xl, xr, mk) for y, xl, xr, mk in (fac.marks or [])
                         if mk.startswith("R") and abs(y - yc) < TOL and xl - 1 <= xc <= xr + 1]
                if spans:
                    y, xl, xr, mk = max(spans, key=lambda s: s[2] - s[1])
                    key = (obj.имя, mk)
                    if key not in seen:
                        seen.add(key)
                        picks.append((obj.имя, mk, "ригель", fac, (y, xl, xr)))
            break
    return picks


def spans_of_mark(fac, mark: str) -> list[tuple]:
    """Все пролёты марки ригеля: [(y, xl, xr)]."""
    return [(y, xl, xr) for y, xl, xr, mk in (fac.marks or []) if mk == mark]


def mark_weight(fac, mark: str, spans) -> float:
    """Вес стекла на марке: максимум по её пролётам (у верхнего ригеля 0)."""
    if mark.startswith("S"):
        return 0.0
    return max((span_weight(fac, *s) for s in spans), default=0.0)


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
    """Требуемые моменты для марки: Лора считает в фоне по геометрии марки
    (сеанс - её последний). None - расчёт недоступен."""
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
    """Минимально допустимая глубина стойки: не меньше глубины самого глубокого ригеля,
    примыкающего к стойке (слева/справа, по всей её высоте)."""
    n = int(mark[1:])
    px = fac.post_x[n - 1]
    depth = {norm_art(r["Шифр"]): to_float(r["Габарит, мм"]) or 0.0 for r in system.profiles}
    arts = [a for (y, xl, xr), a in fac.beam_art.items() if xl == px or xr == px]
    return max((depth.get(norm_art(a), 0.0) for a in arts), default=0.0)


def allowed_profiles(system, lib, fac, mark: str, kind: str, spans) -> tuple[list[tuple[str, str]], bool]:
    """Допустимые профиля (жёстко): тип, блок в библиотеке, для ригеля - закладная под вес марки,
    для стойки - глубина не меньше примыкающих ригелей, моменты не ниже требуемых (Лора, фон).
    Возвращает ([(артикул, расшифровка)], статика_учтена)."""
    rows = [r for r in system.profiles if r["Тип"] == kind and r["Группа"] != "фальш"]
    if lib is not None:
        rows = [r for r in rows if lib.has(r["Шифр"])]
    if kind == "стойка":
        min_d = post_min_depth(system, fac, mark)
        if min_d:
            rows = [r for r in rows if (to_float(r["Габарит, мм"]) or 0.0) >= min_d]
    if kind == "ригель":
        wt = mark_weight(fac, mark, spans)
        holes = fixing_holes(Statics(system, Path(".")), wt)
        allow = beams_with_fixing(system, holes) if holes else set()
        rows = [r for r in rows if norm_art(r["Шифр"]) in allow]
    reqs = mark_requirements(kind, fac, mark, spans)
    if reqs:
        jx_k = "стойка_треб_ix_см4" if kind == "стойка" else "ригель_треб_ix_см4"
        wx_k = "стойка_треб_wx_см3" if kind == "стойка" else "ригель_треб_wx_см3"
        rows = [r for r in rows if (to_float(r["Jx"]) or 0.0) >= reqs[jx_k]
                and (to_float(r["Wx"]) or 0.0) >= reqs[wx_k]]
        if kind == "ригель":
            rows = [r for r in rows if (to_float(r["Jy"]) or 0.0) >= reqs["ригель_треб_iy_см4"]]
    rows.sort(key=lambda r: to_float(r["Габарит, мм"]) or 0.0)
    return [(r["Шифр"], f'{r["Группа"]}, габарит {r["Габарит, мм"]} мм, {r["Масса 1м, кг"]} кг/м, '
                         f'Jx={r["Jx"]}, Jy={r["Jy"]}, Wx={r["Wx"]} (стр. {r["Стр."]})') for r in rows], bool(reqs)


def current_article(kind: str, fac, mark: str, span) -> str:
    if kind == "стойка":
        p = next((p for p in fac.parts if p.where == mark and p.kind == "Стойка"), None)
        return p.article if p else ""
    return fac.beam_art.get((round(span[0], 3), span[1], span[2]), "") if span else ""


def main():
    sel = read_selection()
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

    defaults = ObjectParams(заполнение=50, вариант="", выпуск_низ=20, выпуск_верх=10)
    facs = []
    for g, owner in zip(groups, owners):
        attrs = owner[2] if owner else {}
        obj, _warn = params_from_attrs(defaults, attrs)
        obj = replace(obj, имя=attrs.get("ИМЯ", f"ВТ-{len(facs) + 1}?"))
        gx0, gy0, gx1, gy1 = bbox(g)
        mk = [m for m in markers if gx0 - 100 < m[0] < gx1 + 100 and gy0 - 100 < m[1] < gy1 + 100]
        fac = build(g, system, obj, mk)
        facs.append((g, replace(obj, профили=tables.get(obj.имя, {})), fac, owner))
    # build с правками (для current_article)
    facs = [(g, obj, build(g, system, obj, [m for m in markers
            if bbox(g)[0] - 100 < m[0] < bbox(g)[2] + 100 and bbox(g)[1] - 100 < m[1] < bbox(g)[3] + 100]), ow)
            for g, obj, _f0, ow in facs]

    _, lib = load(system, systems_dir)

    picks = locate(sel, facs)
    if not picks:
        print("Полилинии не попали ни в одну марку - ничего не заменено.")
        return
    print("Марки:", ", ".join(f"{v}: {m}" for v, m, *_ in picks))

    edits = []
    for vname, mark, kind, fac, span in picks:
        spans = spans_of_mark(fac, mark) if kind == "ригель" else []
        opts, stat_ok = allowed_profiles(system, lib, fac, mark, kind, spans)
        if not opts:
            from tkinter import messagebox
            geo = "глубина не меньше примыкающих ригелей" if kind == "стойка" else "закладная под вес"
            why = f"блок в библиотеке / {geo} / моменты по расчёту Лоры" if stat_ok \
                else f"блок в библиотеке / {geo} (Лора недоступна - моменты не проверены)"
            messagebox.showwarning("Замена профиля",
                                   f"{vname}, марка {mark}: нет допустимых профилей {kind}\n({why}) - пропуск")
            continue
        title = f"Замена профиля: {mark}  ({kind}, {vname})"
        if kind == "ригель":
            title += f"   вес на марке {mark_weight(fac, mark, spans):g} кг"
        if not stat_ok:
            title += "   (статика не учтена - Лора недоступна)"
        choice = ask_profile(title, opts, current_article(kind, fac, mark, span))
        if choice:
            edits.append((vname, mark, choice))
            print(f"  {mark} -> {choice}")

    if not edits:
        print("Замен не выбрано - чертёж не менялся.")
        return
    res = acad.run_lisp(set_profile_code(edits), timeout=60)
    print("Таблица профилей:", res)
    result = postroit_vse(acad, system, defaults, systems_dir)
    warns = [f"{o.имя}: {w}" for o, f in result for w in f.warnings]
    report("Замена профиля - готово",
           [f"Заменено: {len(edits)}"] + [f"{v}, {m} -> {a}" for v, m, a in edits], warns)


if __name__ == "__main__":
    try:
        main()
    except (AcadError, OSError) as e:
        print("ОШИБКА:", e)
