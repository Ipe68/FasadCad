"""Привязки узлов из образцов пользователя + атлас всех толщин (без AutoCAD).

Запуск:  py Привязки.py
1. Читает образцы (узлы 8/24/50 мм из блоков) - ОБРАЗЦЫ ниже.
2. Снимает привязки (что от носика профиля, что от стекла) -> systems\\КП50К_привязки.json.
3. Собирает по ним каждый узел образца и сравнивает с рисунком (должно совпасть).
4. Собирает все толщины таблицы комплектации -> выход\\атлас_привязки\\<вариант>.dxf и .png
   (стойка средняя, стойка крайняя, ригель средний, ригель нижний; доборные - вариант 1 таблицы периметра).
"""

import sys
from pathlib import Path

import ezdxf

from fasad import rules as R
from fasad.facade import load_system, parse_items
from fasad.nodes import Place, to_world

HERE = Path(__file__).parent
ОБРАЗЦЫ = Path(r"C:\cad\Kat sial\Каталог КП50К_ACAD_02_2025\Блоки основные профили КП50Л.dxf")
СИСТЕМА = "КП50К"
ШАГ_X, ШАГ_Y = 220, 260                     # раскладка атласа, мм


def check(samples, lib, rules, s) -> int:
    """Собрать каждый образец по привязкам (артикулы - как в образце) и сравнить."""
    clamps = {R._art(r["Прижим"]) for r in s.kits}
    covers = {R._art(r["Крышка стойки"]) for r in s.kits} | {R._art(r["Крышка ригеля"]) for r in s.kits}
    thermos = {R._art(a) for r in s.kits for a, _, _ in parse_items(r["Термовставка"])}
    setting = {R._art(a) for r in s.kits for a, _, _ in parse_items(r["Подкладка опорная"])}
    bad_total = 0
    for smp in samples:
        mid = lambda i: abs(R._cx(i.box)) < 1
        kit = dict(next(r for r in s.kits if int(r["Толщина"]) == smp.thick))
        kit["Прижим"] = next((i.name for i in smp.items if i.name in clamps), "")
        kit["Крышка ригеля" if smp.beam else "Крышка стойки"] = next((i.name for i in smp.items if i.name in covers), "")
        kit["Термовставка"] = next((i.name for i in smp.items if i.name in thermos and mid(i)), "")
        kit["Винт прижима"] = next((i.name for i in smp.items if i.name.startswith("ВС 5")), "")
        kit["Подкладка опорная"] = next((i.name for i in smp.items if i.name in setting), "")
        stack = None
        if smp.edge:
            st = [i for i in smp.items if i.place and R._cx(i.box) < 0 and not i.name.startswith("ВС")
                  and i.name != s.card["уплотнители"]["прижим"] and i.name not in clamps | covers
                  and not (i.name in thermos and mid(i))]
            stack = [i.name for i in sorted(st, key=lambda i: i.box[1])]
        b = R.build_node(rules, lib, beam=smp.beam, thick=smp.thick, profile=smp.profile, kit=kit, card=s.card,
                         edge_stack=stack)
        built = [(p.name, lib.placed_box(p)) for p in b.places]
        bad, used = [], set()
        for it in smp.items:
            if it.place is None or it.name.startswith("ВС 4"):
                continue
            cand = [(max(abs(u - v) for u, v in zip(bx, it.box)), k) for k, (n, bx) in enumerate(built)
                    if n == it.name and k not in used]
            if not cand:
                bad.append(f"{it.name}: не построен")
                continue
            d, k = min(cand)
            used.add(k)
            if d > 0.2:
                bad.append(f"{it.name}: сдвиг {d:.2f} мм")
        bad += [f"лишний {built[k][0]}" for k in range(1, len(built)) if k not in used]
        bad_total += len(bad)
        print(f"  {'ригель' if smp.beam else 'стойка'} {smp.thick} мм {'крайн.' if smp.edge else 'средн.'} "
              f"({smp.where}): " + ("совпал" if not bad else "; ".join(bad)))
    return bad_total


def edge_stack(text: str) -> list[str]:
    """'КПС 296 (Т50-04) x2 + ТПУ-6002' -> ['КПС 296', 'КПС 296', 'ТПУ-6002'] (без скобок - прорисован)."""
    return [R._art(a) for a, _, q in parse_items(text) for _ in range(q)]


def atlas(rules, lib, s, out_dir: Path) -> list[str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    notes = []
    post, beam = s.card["стойка"]["артикул"], s.card["ригель"]["артикул"]
    variants = []
    for r in s.kits:
        if r["Вариант"] not in variants:
            variants.append(r["Вариант"])
    for var in variants:
        rows = sorted((r for r in s.kits if r["Вариант"] == var), key=lambda r: int(r["Толщина"]))
        doc = ezdxf.new("R2018")
        # слои стекла и подкладок - как в файле образцов: исправленный узел можно скопировать туда как образец
        for lay, col in (("Steklopaket", 5), ("Подкладки", 30), ("подписи", 7)):
            doc.layers.add(lay, color=col)
        msp = doc.modelspace()
        used = set()
        for j, r in enumerate(rows):
            t = int(r["Толщина"])
            per = next((p for p in s.perimeters if int(p["Толщина"]) == t and p["Вариант"] == "1"), None)
            cells = [("Стойка средняя", False, None), ("Стойка крайняя", False, edge_stack(per["Крайняя стойка"]) if per else None),
                     ("Ригель средний", True, None), ("Ригель нижний", True, edge_stack(per["Крайний ригель"]) if per else None)]
            y0 = -j * ШАГ_Y
            msp.add_text(f"{var}   {t} мм", height=14, dxfattribs={"layer": "подписи", "insert": (-90, y0 + 150)})
            for i, (title, is_beam, stack) in enumerate(cells):
                if "крайн" in title or "нижний" in title:
                    if not per:
                        continue
                x0 = i * ШАГ_X
                b = R.build_node(rules, lib, beam=is_beam, thick=t, profile=beam if is_beam else post, kit=r,
                                 card=s.card, edge_stack=stack)
                # стойка: наружу вниз, стекло крайней - справа; ригель: наружу вправо, верх - стекло
                base = Place("", x0, y0, 270.0, True) if is_beam else Place("", x0, y0, 180.0, True)
                for p in b.places:
                    w = to_world(base, p)
                    msp.add_blockref(w.name, (w.x, w.y), dxfattribs={
                        "rotation": w.rot, "xscale": -1 if w.mirror else 1})
                    used.add(w.name)
                for lay, (a0, b0, a1, b1) in b.rects:
                    pts = [to_world(base, Place("", x, y)) for x, y in ((a0, b0), (a1, b0), (a1, b1), (a0, b1))]
                    msp.add_lwpolyline([(q.x, q.y) for q in pts], close=True,
                                       dxfattribs={"layer": "Steklopaket" if lay == "стекло" else "Подкладки"})
                label = title + (f": {per['Крайняя стойка' if not is_beam else 'Крайний ригель']}" if stack else "")
                msp.add_text(label, height=6, dxfattribs={"layer": "подписи", "insert": (x0 - 75, y0 + 125)})
                notes += [f"{var} {t} мм, {title.lower()}: {n}" for n in b.notes]
        lib.export_blocks(doc, used)
        dxf = out_dir / f"{var}.dxf"
        doc.saveas(str(dxf))
        try:
            pictures(doc, out_dir / var, [(int(r["Толщина"]), -j * ШАГ_Y) for j, r in enumerate(rows)])
        except ImportError:
            pass
        print(f"  {var}: {len(rows)} толщин -> {dxf.name}")
    return list(dict.fromkeys(notes))


def pictures(doc, folder: Path, rows):
    """PNG на каждую толщину: строка из 4 узлов, крупно, толстыми линиями."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from ezdxf import bbox
    from ezdxf.addons.drawing import Frontend, RenderContext
    from ezdxf.addons.drawing.config import BackgroundPolicy, ColorPolicy, Configuration
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
    folder.mkdir(exist_ok=True)
    for old in folder.glob("*.png"):
        old.unlink()
    msp = doc.modelspace()
    cfg = Configuration(background_policy=BackgroundPolicy.WHITE, color_policy=ColorPolicy.BLACK,
                        min_lineweight=0.5, lineweight_scaling=2.0)
    ents = [(e, bbox.extents([e])) for e in msp]
    for t, y0 in rows:
        x0, x1, ya, yb = -95, 4 * ШАГ_X - 40, y0 - 80, y0 + 165
        sel = [e for e, b in ents if b.has_data and b.extmax.y > ya and b.extmin.y < yb]
        fig = plt.figure(figsize=((x1 - x0) / 25.4, (yb - ya) / 25.4))   # 1:1 при 100% на мониторе ~ в 4 раза крупнее
        ax = fig.add_axes([0, 0, 1, 1])
        Frontend(RenderContext(doc), MatplotlibBackend(ax), config=cfg).draw_entities(sel)
        ax.set_xlim(x0, x1); ax.set_ylim(ya, yb); ax.set_aspect("equal"); ax.axis("off")
        fig.savefig(folder / f"{t:02d} мм.png", dpi=150)
        plt.close(fig)


def main():
    s = load_system(HERE / "systems", СИСТЕМА)
    samples, lib = R.read_samples(ОБРАЗЦЫ, {r["Шифр"] for r in s.profiles})
    rules, notes = R.learn(samples, s.kits, s.perimeters, s.card)
    out = HERE / "systems" / f"{СИСТЕМА}_привязки.json"
    R.save(rules, out, str(ОБРАЗЦЫ))
    print(f"Образцов: {len(samples)}, привязок: {len(rules)} -> {out.name}")
    for n in notes:
        print("  расходятся образцы:", n)
    print("Проверка - собрать образцы по привязкам:")
    bad = check(samples, lib, rules, s)
    print("  ВСЕ СОВПАЛИ" if not bad else f"  НЕ СОВПАЛО: {bad}")
    print("Атлас:")
    out_dir = HERE / "выход" / "атлас_привязки"
    an = atlas(rules, lib, s, out_dir)
    (out_dir / "замечания.txt").write_text("\n".join(an) or "замечаний нет", encoding="utf-8")
    print(f"Замечаний по атласу: {len(an)} (выход\\атлас_привязки\\замечания.txt)")


if __name__ == "__main__":
    sys.exit(main())
