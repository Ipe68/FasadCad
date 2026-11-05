"""Опыт 2: витраж КП50К с дверью СТ71 до пола в среднем пролёте.

3 пролёта по 1200, высота по осям 3000. В крайних пролётах ригели на 0, 2000, 3000.
В среднем пролёте нижнего ригеля нет (дверь до пола), над дверью ригель на 2400.
Метка "ДВЕРЬ СТ71" на слое "проемы" внутри ячейки двери.

Запуск:  py опыт_2_дверь.py           - заменить оси и метку пробными и построить
         py опыт_2_дверь.py --по-осям - построить по тому, что уже нарисовано
"""

import sys
from pathlib import Path

from fasad.acad_link import Acad
from fasad.facade import ObjectParams, load_system, summary
from fasad.lisp_draw import BEAM_AXIS_LAYER, OPENING_LAYER, POST_AXIS_LAYER, draw_axes_code, draw_marker_code
from fasad.run import postroit
from fasad.spec import build_spec, save_excel

HERE = Path(__file__).parent

# Программа рисует и стирает ТОЛЬКО в этом чертеже. Другие открытые файлы не трогает.
РАБОЧИЙ_ЧЕРТЕЖ = "Чертеж1.dwg"

POSTS = [(0, 0, 3000), (1200, 0, 3000), (2400, 0, 3000), (3600, 0, 3000)]
BEAMS = [(0, 0, 1200), (0, 2400, 3600),
         (2000, 0, 1200), (2000, 2400, 3600),
         (2400, 1200, 2400),
         (3000, 0, 3600)]
MARKERS = [(1500, 1000, "ДВЕРЬ СТ71")]

OBJECT = ObjectParams(
    заполнение=50,
    вариант="Adifoam 530.121",
    формула="6-16-6-16-6",
    выпуск_низ=20,
    выпуск_верх=10,
    вариант_периметра="1",
)


def main():
    system = load_system(HERE / "systems", "КП50К")
    acad = Acad()
    acad.require_drawing(РАБОЧИЙ_ЧЕРТЕЖ)
    print("AutoCAD:", acad.title)

    if "--по-осям" not in sys.argv:
        layers = f"{POST_AXIS_LAYER},{BEAM_AXIS_LAYER},{OPENING_LAYER}"
        acad.run_lisp(f'(progn (if (setq ss (ssget "X" (list (cons 8 "{layers}") (cons 410 "Model")))) '
                      f'(command "_.ERASE" ss "")) (princ))')
        acad.run_lisp(draw_axes_code(POSTS, BEAMS))
        for x, y, text in MARKERS:
            acad.run_lisp(draw_marker_code(x, y, text))
        print("Пробные оси и метка нарисованы.")

    fac = postroit(acad, system, OBJECT, HERE / "systems")
    sp = build_spec(fac, system, OBJECT, "ВТ-2 опыт с дверью")
    xlsx = save_excel([sp], HERE / "выход" / "ВТ-2 опыт с дверью.xlsx")
    print(f"Спецификация Excel: {xlsx}")

    print("\nПроёмы:")
    for text, name, w, h, page in fac.openings:
        print(f"  {name}: рама {w:g} x {h:g} (каталог КП50К стр. {page})")

    print(f"\n{'Артикул':<16} {'Тип':<26} {'Длина':>7} {'Кол.':>5}  Примечание")
    for art, kind, length, cnt, note in summary(fac):
        print(f"{art:<16} {kind:<26} {length:>7g} {cnt:>5}  {note}")
    print("\nШтучные:")
    for art, kind, cnt in fac.pieces:
        print(f"  {art:<40} {cnt if cnt else '':>3} {'шт.' if cnt else '   '}  {kind}")
    print("\nСтёкла:")
    for i, g in enumerate(fac.glasses, 1):
        print(f"  {g.kind}_{i:02d}  {g.width:g} x {g.height:g}   {g.weight:g} кг")


if __name__ == "__main__":
    main()
