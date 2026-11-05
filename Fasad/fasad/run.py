"""Построение витражей в открытом AutoCAD: оси -> фасад, закладные, сечения, марки, размеры.

postroit_s_oknom - главный запуск: окно "Исходные данные", все витражи, таблицы профилей, Excel;
postroit_vse     - все витражи чертежа по блокам ФАСАД_КОНСТРУКЦИЯ, без окна (для опытов);
postroit         - один витраж (все оси чертежа - один витраж), для опытов.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Callable

from .acad_link import Acad
from .constructions import (ATTRS, BLOCK_ABOVE, assign_blocks, bbox, clear_tables_code, define_block_code, params_from_attrs,
                            parse_blocks, parse_profile_tables, read_blocks_code, split_axes, table_code,
                            write_block_code)
from .dims import DIM_LAYER, build_dims
from .facade import FILLER_MODES, Facade, ObjectParams, System, build, load_system
from .lisp_draw import (_axes_lines, clear_generated_code, dimstyle_code, draw_dims_code, draw_facade_code,
                        draw_items_code, load_blocks_code, parse_axes_file, parse_markers, read_axes_code,
                        zoom_all_code)
from .marks import BEAM_MARK_LAYER, POST_MARK_LAYER, build_marks
from .sections import build_on_facade, build_sections, layer_color


def _prepare(acad: Acad, system: System, systems_dir: Path) -> list[str]:
    """Один раз на построение: блоки сечений, размерный стиль, блок конструкции."""
    warn = []
    from .library import library_path
    node_lib = library_path(system)
    if not (node_lib and node_lib.exists()):
        # старые блоки профилей - только для запасного пути (сечения по базе каталога);
        # при библиотеке узлов блоки грузятся из неё, иначе в чертеже копились бы "(старый N)"
        lib = systems_dir / system.card["сечение"]["блоки"]
        if acad.run_lisp(load_blocks_code(lib, system.card["стойка"]["артикул"])) != "да":
            warn.append(f"Не удалось загрузить блоки сечений из {lib}")
    d, fm = system.card["размеры"], system.card["оформление"]
    if acad.run_lisp(dimstyle_code(d["стиль"], d, fm)) != "да":
        warn.append(f"Не удалось создать размерный стиль {d['стиль']}")
    acad.run_lisp(define_block_code())
    return warn


def _draw_one(acad: Acad, system: System, obj: ObjectParams, fac: Facade, systems_dir: Path) -> tuple[int, int]:
    """Нарисовать один витраж: фасад, закладные, сечения, марки, размеры. Возвращает (создано, не создано)."""
    made, failed = map(int, acad.run_lisp(draw_facade_code(fac, system)).split())

    def items(it_list):
        nonlocal made, failed
        colors = {it.layer: layer_color(it.layer, system.card) for it in it_list}
        colors.update({POST_MARK_LAYER: 2, BEAM_MARK_LAYER: 2, DIM_LAYER: 7})
        m, f = map(int, acad.run_lisp(draw_items_code(it_list, colors)).split())
        made, failed = made + m, failed + f

    items(build_on_facade(fac, system, obj, systems_dir))
    try:
        sec_items, sec_warn, used_blocks, node_lib = build_sections(fac, system, obj, systems_dir)
        fac.warnings += sec_warn
        if used_blocks and node_lib is not None:           # блоки узлов из библиотеки пользователя
            from .library import export_blocks, load_blocks_code as lib_blocks_code
            import time
            # имя файла - новое на каждое построение: иначе AutoCAD спросит "переопределить блок?" и собьётся
            tmp = Path("C:/temp") / f"fasad_uzly_{int(time.time())}.dxf"
            tmp.parent.mkdir(parents=True, exist_ok=True)
            sig = export_blocks(node_lib, used_blocks, tmp)
            res = acad.run_lisp(lib_blocks_code(tmp, sig), timeout=180) or ""
            tmp.unlink(missing_ok=True)
            if "НЕТ:" in res:
                fac.warnings.append("Сечение: не загрузились блоки " + res.split("НЕТ:")[1].strip())
        elif used_blocks:                                  # детали узлов из базы каталога - определить блоки
            from .catalog_nodes import blocks_code, load_base
            res = acad.run_lisp(blocks_code(used_blocks, load_base(systems_dir)[1]), timeout=120) or ""
            if "НЕ ОПРЕДЕЛЕНЫ" in res:
                fac.warnings.append("Блоки деталей " + res.split("НЕ ОПРЕДЕЛЕНЫ:")[1].strip() +
                                    " не удалось заменить на каталожные - в сечении могут стоять неверно")
        items(sec_items)
    except (ValueError, KeyError, OSError) as e:
        fac.warnings.append(f"Сечения не построены: {e}")
    items(build_marks(fac, system))
    d = system.card["размеры"]
    floor = min(lo for lo, hi in fac.post_ends.values()) if fac.post_ends else 0.0
    zero = floor - obj.отметка_низ * 1000                  # низ витража = отметка_низ
    m, f = map(int, acad.run_lisp(draw_dims_code(build_dims(fac, system, zero), d["стиль"], {DIM_LAYER: 7})).split())
    return made + m, failed + f


def postroit(acad: Acad, system: System, obj: ObjectParams, systems_dir: Path) -> Facade:
    """Один витраж: все оси чертежа."""
    acad.run_lisp(read_axes_code())
    axes, markers = parse_axes_file(), parse_markers()
    print(f"Прочитано осей: {len(axes)}, меток проёмов: {len(markers)}")
    fac = build(axes, system, obj, markers)
    acad.run_lisp(clear_generated_code())
    fac.warnings += _prepare(acad, system, systems_dir)
    made, failed = _draw_one(acad, system, obj, fac, systems_dir)
    acad.run_lisp(zoom_all_code())
    print(f"В AutoCAD создано объектов: {made}" + (f", НЕ СОЗДАНО: {failed}" if failed else ""))
    for w in fac.warnings:
        print("ВНИМАНИЕ:", w)
    return fac




def _read_drawing(acad: Acad, reach: float):
    """Оси, метки, блоки конструкций и таблицы профилей; оси разбиты на витражи."""
    acad.run_lisp(define_block_code())
    acad.run_lisp(read_axes_code())
    acad.run_lisp(read_blocks_code())
    axes, markers = parse_axes_file(), parse_markers()
    lines = _axes_lines()
    blocks = parse_blocks(lines)
    tables = parse_profile_tables(lines)
    groups = split_axes(axes, reach)
    owners = assign_blocks(groups, blocks)
    print(f"Осей: {len(axes)}, витражей: {len(groups)}, блоков конструкций: {len(blocks)}, меток проёмов: {len(markers)}")
    return groups, owners, markers, tables


def _build_all(acad: Acad, system: System, groups, objs, positions, warns, markers, systems_dir: Path):
    """Построить и нарисовать все витражи + таблицы профилей. positions - точки блоков конструкций (или None)."""
    acad.run_lisp(clear_generated_code())
    acad.run_lisp(clear_tables_code())
    common = _prepare(acad, system, systems_dir)
    result, made, failed, glass_no, names = [], 0, 0, 0, set()
    for g, obj, pos, warn in zip(groups, objs, positions, warns):
        x0, y0, x1, y1 = bbox(g)
        if obj.имя in names:
            warn.append(f"Марка {obj.имя} повторяется в чертеже")
        names.add(obj.имя)
        my_markers = [m for m in markers if x0 - 100 < m[0] < x1 + 100 and y0 - 100 < m[1] < y1 + 100]
        try:
            fac = build(g, system, obj, my_markers)
        except ValueError as e:                     # нет варианта комплектации и т.п.
            print(f"{obj.имя}: НЕ ПОСТРОЕН - {e}")
            continue
        for gl in fac.glasses:
            glass_no += 1
            gl.mark = f"{gl.kind}_{glass_no:02d}"
        fac.warnings = warn + fac.warnings
        m, f = _draw_one(acad, system, obj, fac, systems_dir)
        made, failed = made + m, failed + f
        if pos:
            acad.run_lisp(table_code(pos[0], pos[1], obj.имя, fac))
        result.append((obj, fac))
        print(f"  {obj.имя} x{obj.количество}: стоек {len(fac.post_x)}, стёкол {len(fac.glasses)}, "
              f"заполнение {obj.заполнение}, {obj.вариант}")
        for w in fac.warnings:
            print(f"     ВНИМАНИЕ: {w}")
    acad.run_lisp(zoom_all_code())
    print(f"В AutoCAD создано объектов: {made}" + (f", НЕ СОЗДАНО: {failed}" if failed else ""))
    for w in common:
        print("ВНИМАНИЕ:", w)
    return result


def postroit_vse(acad: Acad, system: System, defaults: ObjectParams, systems_dir: Path) -> list[tuple[ObjectParams, Facade]]:
    """Все витражи чертежа по блокам ФАСАД_КОНСТРУКЦИЯ, без окна (для опытов)."""
    groups, owners, markers, tables = _read_drawing(acad, system.card["система"]["ширина_профиля"] + 1)
    objs, positions, warns = [], [], []
    for k, (g, owner) in enumerate(zip(groups, owners), 1):
        if owner:
            obj, warn = params_from_attrs(defaults, owner[2])
        else:
            obj, warn = defaults, [f"Нет блока {'ФАСАД_КОНСТРУКЦИЯ'} рядом - взяты параметры по умолчанию"]
            obj = replace(obj, имя=f"ВТ-{k}?")
        objs.append(replace(obj, профили=tables.get(obj.имя, {})))
        positions.append(owner[:2] if owner else None)
        warns.append(warn)
    return _build_all(acad, system, groups, objs, positions, warns, markers, systems_dir)


def _thick(s) -> int | None:
    try:
        return int(float(str(s).replace(",", ".")))
    except ValueError:
        return None


def _choices(systems_dir: Path, system: System) -> dict:
    """Списки для окна. Стойки - из карточки (основная + "другие"), ригели - из таблицы закладных
    (кроме фальш-ригеля). Варианты комплектации и доборных - для текущей толщины, с расшифровкой."""
    card = system.card

    def profiles(kind, pair_word):
        """Стойки / ригели по каталогу (стр. 10-11) в порядке несущей способности, с расшифровкой."""
        rows = [r for r in system.profiles if r["Тип"] == kind and r["Группа"] != "фальш"]
        if not rows:                                  # нет таблицы - хотя бы основной профиль
            return lambda _: [(card[kind]["артикул"], "")]
        out = [(r["Шифр"], f'{r["Группа"]}, габарит {r["Габарит, мм"]} мм, {r["Масса 1м, кг"]} кг/м'
                           + (f', {pair_word} {r["Пара"]}' if r["Пара"] else "") + f' (стр. {r["Стр."]})')
               for r in rows]
        return lambda _: out

    def kits(thick):
        t = _thick(thick)
        return [(r["Вариант"], f'термовставка {r["Термовставка"] or "нет"}, прижим {r["Прижим"]} (стр. {r["Стр."]})')
                for r in system.kits if _thick(r["Толщина"]) == t]

    def perims(thick):
        t = _thick(thick)
        return [(r["Вариант"], f'стойка: {r["Крайняя стойка"]}; ригель: {r["Крайний ригель"]} (стр. {r["Стр."]})')
                for r in system.perimeters if _thick(r["Толщина"]) == t]

    return {
        "системы": sorted(p.stem for p in systems_dir.glob("*.toml") if "_" not in p.stem),
        "СТОЙКА": profiles("стойка", "ригель до"), "РИГЕЛЬ": profiles("ригель", "стойка от"),
        "ВАРИАНТ": kits, "ПЕРИМЕТР_ВАРИАНТ": perims,
        "МОНТАЖ": ["в проем", "навесной"],
        "ДОБОРНЫЕ": list(FILLER_MODES),
        **{t: ["да", "нет"] for t in ("ПЕРИМЕТР_ЛЕВО", "ПЕРИМЕТР_ПРАВО", "ПЕРИМЕТР_ВЕРХ", "ПЕРИМЕТР_НИЗ", "КАПЕЛЬНИК")},
    }


def _ask_stocks_and_save(result, system: System, name: str, systems_dir: Path, out_dir: Path,
                         title: str) -> Path | None:
    """Окно "Хлысты" по алюминию объекта (последние длины запоминаются) -> раскрой и Excel."""
    from collections import defaultdict
    from .dialog import ask_stocks
    from .spec import _read_csv
    from .spec_vse import save_excel_vse

    alu = {r["Артикул"] for r in _read_csv(systems_dir / f"{name}_алюминий.csv")}
    alu |= {cfg["рама"] for cfg in system.openings.values()}
    total: dict[str, float] = defaultdict(float)
    names: dict[str, str] = {}
    for obj, fac in result:
        for p in fac.parts:
            if p.article in alu:
                total[p.article] += p.length * obj.количество / 1000
                names.setdefault(p.article, p.kind)
    stock_file = out_dir / "хлысты.json"
    remembered = json.loads(stock_file.read_text(encoding="utf-8")) if stock_file.exists() else {}
    default = system.card.get("раскрой", {}).get("хлыст", 6000)
    stocks = ask_stocks([(a, names[a], total[a]) for a in sorted(total)], remembered, default)
    if stocks is None:
        print("Хлысты: отменено - Excel не сделан")
        return None
    stock_file.write_text(json.dumps({**remembered, **stocks}, ensure_ascii=False, indent=1), encoding="utf-8")
    xlsx = save_excel_vse(result, system, out_dir / f"{_file_name(title)}.xlsx", systems_dir,
                          title=title, хлыст=default, хлысты=stocks)
    print(f"Спецификация Excel: {xlsx}")
    return xlsx


def postroit_s_oknom(acad: Acad, systems_dir: Path, out_dir: Path, system_name: str = "КП50К",
                     on_built: Callable[[], None] | None = None) -> Path | None:
    """Главный запуск: прочитать чертёж -> окно "Исходные данные" -> записать блоки -> построить -> Excel."""
    from .dialog import ask

    settings_file = out_dir / "объект.json"
    top = {"объект": "Объект", "система": system_name}
    if settings_file.exists():
        top.update({k: v for k, v in json.loads(settings_file.read_text(encoding="utf-8")).items() if k in top})
    loaded = top["система"]
    system = load_system(systems_dir, loaded)
    card = system.card

    groups, owners, markers, tables = _read_drawing(acad, card["система"]["ширина_профиля"] + 1)
    if not groups:
        print("В чертеже нет осей OSs / OSr - строить нечего")
        return None
    by_card = {"СТОЙКА": card["стойка"]["артикул"], "РИГЕЛЬ": card["ригель"]["артикул"]}
    saved_file = out_dir / "исходные_данные.json"           # ответы окна - на случай, если блок не записался
    saved = json.loads(saved_file.read_text(encoding="utf-8")) if saved_file.exists() else {}
    vitrages = []
    for k, (g, owner) in enumerate(zip(groups, owners), 1):
        x0, y0, x1, y1 = bbox(g)
        label = f"X {x0:.0f}..{x1:.0f}"
        attrs = {tag: default for tag, _, default, _ in ATTRS}
        attrs["ИМЯ"] = f"ВТ-{k}"
        if owner:
            attrs.update({t: v for t, v in owner[2].items() if v != "" or t not in by_card})
        elif label in saved:
            attrs.update(saved[label])
        for t, v in by_card.items():
            attrs[t] = attrs.get(t) or v
        vitrages.append({"подпись": label, "атрибуты": attrs})

    def kit_ok(d: dict) -> str:
        """Проверка витража по каталогу: вариант комплектации есть для толщины, ригель подходит к стойке."""
        try:
            system.kit(int(float(str(d.get("ЗАПОЛНЕНИЕ", "")).replace(",", "."))), d.get("ВАРИАНТ", ""))
        except ValueError as e:
            return str(e)
        return system.pair_error(d.get("СТОЙКА", ""), d.get("РИГЕЛЬ", ""))

    drawing = acad.drawing_path()
    from .lora_link import Statics, available as lora_available, cell_geometry
    statics = Statics(system, out_dir) if lora_available() else None
    statics_geom = [cell_geometry(g) for g in groups] if statics else None
    answer = ask(vitrages, top, _choices(systems_dir, system), kit_ok, statics=statics,
                 statics_geom=statics_geom)
    if answer is None:
        print("Отменено - ничего не построено")
        return None
    acad.wait_ready()
    acad.require_drawing_path(drawing)       # пока было открыто окно, могли переключить чертёж
    data, top = answer
    if top["система"] != loaded:
        system = load_system(systems_dir, top["система"])

    defaults = ObjectParams(заполнение=50, вариант="", выпуск_низ=20, выпуск_верх=10)
    objs, positions, warns = [], [], []
    for g, owner, attrs, v in zip(groups, owners, data, vitrages):
        x0, y0, x1, y1 = bbox(g)
        # блок над витражом; старый блок, если налезает на витраж (стал выше), - поднять
        pos = (owner[0], max(owner[1], y1 + BLOCK_ABOVE)) if owner else (x0, y1 + BLOCK_ABOVE)
        res = acad.run_lisp(write_block_code(pos[0], pos[1], attrs, owner[3] if owner else ""))
        obj, warn = params_from_attrs(defaults, attrs)
        if res != "ок":
            warn.append(f"Блок исходных данных не записан в чертёж ({res}) - построено по данным окна")
            print(f"  {attrs.get('ИМЯ')}: блок не записан - {res}")
        old_name = owner[2].get("ИМЯ", "") if owner else ""
        objs.append(replace(obj, профили=tables.get(old_name, {}) if old_name else {}))
        positions.append(pos)
        warns.append(warn)

    result = _build_all(acad, system, groups, objs, positions, warns, markers, systems_dir)
    from .dialog import report
    if not result:
        report("Фасад - не построено", ["Ни один витраж не построен."], [])
        return None
    out_dir.mkdir(parents=True, exist_ok=True)
    if on_built is not None:
        on_built()
    settings_file.write_text(json.dumps(top, ensure_ascii=False, indent=1), encoding="utf-8")
    saved.update({v["подпись"]: d for v, d in zip(vitrages, data)})
    saved_file.write_text(json.dumps(saved, ensure_ascii=False, indent=1), encoding="utf-8")
    xlsx = _ask_stocks_and_save(result, system, top["система"], systems_dir, out_dir, top["объект"])
    lines = [f"Построено витражей: {len(result)}"] + \
            [f"   {o.имя} x{o.количество}: стоек {len(f.post_x)}, стёкол {len(f.glasses)}, "
             f"заполнение {o.заполнение} мм, {o.вариант}" for o, f in result] + \
            [f"Excel: {xlsx}" if xlsx else "Excel не сделан"]
    report("Фасад - готово", lines, [f"{o.имя}: {w}" for o, f in result for w in f.warnings])
    return xlsx


def _file_name(s: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "-", s).strip() or "Объект"
