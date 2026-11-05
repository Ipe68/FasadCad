"""Опыт 1: рисуем оси пробного витража в открытом AutoCAD, читаем их обратно
и строим по ним фасад КП50К.

Витраж: 3 пролёта по 1200, оси ригелей на 0, 2000 и 3000,
в среднем пролёте дополнительный ригель на 900.

Запуск:  py опыт_1_витраж.py           - нарисовать оси и построить фасад
         py опыт_1_витраж.py --по-осям - только построить по уже нарисованным осям
"""

import sys
from pathlib import Path

from fasad.acad_link import Acad
from fasad.facade import ObjectParams, load_system, summary
from fasad.lisp_draw import BEAM_AXIS_LAYER, OPENING_LAYER, POST_AXIS_LAYER, draw_axes_code
from fasad.run import postroit
from fasad.spec import build_spec, save_excel

HERE = Path(__file__).parent

# Программа рисует и стирает ТОЛЬКО в этом чертеже. Другие открытые файлы не трогает.
РАБОЧИЙ_ЧЕРТЕЖ = "Чертеж1.dwg"

# Размеры - по осям от оси левой стойки и оси нижнего ригеля.
_POSTS = [(0, 0, 3000), (1200, 0, 3000), (2400, 0, 3000), (3600, 0, 3000)]
_BEAMS = [(0, 0, 3600), (2000, 0, 3600), (3000, 0, 3600), (900, 1200, 2400)]

OBJECT = ObjectParams(
    заполнение=50,
    вариант="Adifoam 530.121",
    формула="6-16-6-16-6",
    выпуск_низ=20,
    выпуск_верх=10,
    вариант_периметра="1",
)

# Привязка по умолчанию (отметка 0,000): наружная грань левой стойки - x=0, низ стоек - y=0.
X0 = 25                                  # половина ширины стойки
Y0 = OBJECT.выпуск_низ + 25              # выпуск стойки + половина ригеля
POSTS = [(x + X0, y1 + Y0, y2 + Y0) for x, y1, y2 in _POSTS]
BEAMS = [(y + Y0, x1 + X0, x2 + X0) for y, x1, x2 in _BEAMS]


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
        print("Пробные оси нарисованы.")

    fac = postroit(acad, system, OBJECT, HERE / "systems")
    sp = build_spec(fac, system, OBJECT, "ВТ-1 опыт")
    xlsx = save_excel([sp], HERE / "выход" / "ВТ-1 опыт.xlsx")
    print(f"Спецификация Excel: {xlsx}")

    k, p = fac.kit, fac.perimeter
    print(f"\nЗаполнение {OBJECT.заполнение} мм, вариант \"{k['Вариант']}\" (каталог стр. {k['Стр.']}),"
          f" периметр вариант {p['Вариант']} (стр. {p['Стр.']})")
    print(f"  Подкладка опорная:  {k['Подкладка опорная'] or '-'}")
    print(f"  Подкладка фиксир.:  {k['Подкладка фиксир.'] or '-'}")
    print(f"  Винт прижима:       {k['Винт прижима'] or '-'}")
    if k["Винт под подкладку"]:
        print(f"  Винт под подкладку: {k['Винт под подкладку']}")

    print(f"\n{'Артикул':<16} {'Тип':<15} {'Длина':>7} {'Кол.':>5}  Примечание")
    for art, kind, length, cnt, note in summary(fac):
        print(f"{art:<16} {kind:<15} {length:>7g} {cnt:>5}  {note}")
    print("\nШтучные:")
    for art, kind, cnt in fac.pieces:
        print(f"  {art:<55} {cnt:>3} шт.  {kind}")
    print(f"\nСтёкла (формула {OBJECT.формула}, стекла {fac.glass_mm:g} мм):")
    for i, g in enumerate(fac.glasses, 1):
        print(f"  {g.kind}_{i:02d}  {g.width:g} x {g.height:g}   {g.weight:g} кг")
    print("\nКрепление ригелей (по весу стекла на ригеле):")
    for where, wt, how, _, ins in fac.fixings:
        print(f"  {where:<14} {wt:>6g} кг  {how:<20} {ins}")


if __name__ == "__main__":
    main()
