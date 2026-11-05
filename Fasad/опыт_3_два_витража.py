"""Опыт 3: два витража в одном чертеже, у каждого свой блок ФАСАД_КОНСТРУКЦИЯ.

ВТ-1 - 3 пролёта по 1200, ригели 0 / 2000 / 3000, в среднем пролёте ещё 900. 1 шт.
ВТ-2 - 3 пролёта по 1200, в среднем дверь СТ71 до пола (ригель над дверью 2400). 2 шт.

Запуск:  py опыт_3_два_витража.py           - нарисовать пробные оси, метки, блоки и построить
         py опыт_3_два_витража.py --по-осям - построить по тому, что уже нарисовано в чертеже
"""

import sys
from pathlib import Path

from fasad.acad_link import Acad
from fasad.constructions import BLOCK_LAYER, define_block_code, insert_block_code
from fasad.facade import ObjectParams, load_system
from fasad.lisp_draw import BEAM_AXIS_LAYER, OPENING_LAYER, POST_AXIS_LAYER, draw_axes_code, draw_marker_code
from fasad.run import postroit_vse
from fasad.spec_vse import save_excel_vse

HERE = Path(__file__).parent
РАБОЧИЙ_ЧЕРТЕЖ = "Чертеж1.dwg"

# параметры по умолчанию - если у витража нет блока или в блоке не заполнен атрибут
DEFAULTS = ObjectParams(заполнение=50, вариант="Adifoam 530.121", формула="6-16-6-16-6",
                        выпуск_низ=20, выпуск_верх=10)

# Хлысты - на весь объект (закупка профиля), а не на витраж:
ХЛЫСТ = None                       # None - из карточки системы (КП50К.toml, [раскрой])
ХЛЫСТЫ = {}                        # свои длины по артикулам для этого объекта (поверх карточки)

X0, Y0 = 25, 45            # ось левой стойки и ось нижнего ригеля при низе стоек в 0
SHIFT = 7500               # второй витраж правее


def vt1(dx):
    posts = [(x + X0 + dx, Y0, 3000 + Y0) for x in (0, 1200, 2400, 3600)]
    beams = [(y + Y0, X0 + dx, 3600 + X0 + dx) for y in (0, 2000, 3000)] + [(900 + Y0, 1200 + X0 + dx, 2400 + X0 + dx)]
    return posts, beams


def vt2(dx):
    posts = [(x + X0 + dx, Y0, 3000 + Y0) for x in (0, 1200, 2400, 3600)]
    a, b = X0 + dx, X0 + dx
    beams = [(Y0, a, a + 1200), (Y0, a + 2400, a + 3600), (2000 + Y0, a, a + 1200), (2000 + Y0, a + 2400, a + 3600),
             (2400 + Y0, a + 1200, a + 2400), (3000 + Y0, a, a + 3600)]
    return posts, beams


def main():
    system = load_system(HERE / "systems", "КП50К")
    acad = Acad()
    acad.require_drawing(РАБОЧИЙ_ЧЕРТЕЖ)
    print("AutoCAD:", acad.title)

    if "--по-осям" not in sys.argv:
        layers = f"{POST_AXIS_LAYER},{BEAM_AXIS_LAYER},{OPENING_LAYER},{BLOCK_LAYER}"
        acad.run_lisp(f'(progn (if (setq ss (ssget "X" (list (cons 8 "{layers}") (cons 410 "Model")))) '
                      f'(command "_.ERASE" ss "")) (princ))')
        p1, b1 = vt1(0)
        p2, b2 = vt2(SHIFT)
        acad.run_lisp(draw_axes_code(p1 + p2, b1 + b2))
        acad.run_lisp(draw_marker_code(SHIFT + 1500, 1000, "ДВЕРЬ СТ71"))
        # в пробном чертеже - определение блока заново (могло остаться старое, с другим набором атрибутов)
        acad.run_lisp('(progn (command "_.-PURGE" "_B" "ФАСАД_КОНСТРУКЦИЯ" "_N") (princ))')
        acad.run_lisp(define_block_code())
        acad.run_lisp(insert_block_code(0, 3080 + 2000, {"ИМЯ": "ВТ-1", "КОЛИЧЕСТВО": "1"}))
        acad.run_lisp(insert_block_code(SHIFT, 3080 + 2000, {"ИМЯ": "ВТ-2", "КОЛИЧЕСТВО": "2"}))
        print("Пробные оси, метка двери и блоки конструкций нарисованы.")

    results = postroit_vse(acad, system, DEFAULTS, HERE / "systems")
    xlsx = save_excel_vse(results, system, HERE / "выход" / "Опыт 3 - два витража.xlsx", HERE / "systems",
                          title="Опыт 3 - два витража", хлыст=ХЛЫСТ, хлысты=ХЛЫСТЫ)
    print(f"Спецификация Excel: {xlsx}")


if __name__ == "__main__":
    main()
