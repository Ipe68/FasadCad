"""Перестроить чертёж с учётом добавленных сечений - команда AutoCAD ДОБАВИТЬСЕЧЕНИЕ.

Линия сечения уже нарисована командой на слое FASAD_СЕЧЕНИЯ; здесь чертёж перестраивается,
и добавленные сечения (В-В, Г-Г...) строятся вместе с А-А/Б-Б.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from fasad.acad_link import Acad, AcadError
from fasad.constructions import define_block_code, parse_blocks, read_blocks_code
from fasad.dialog import report
from fasad.facade import ObjectParams, load_system
from fasad.lisp_draw import _axes_lines, read_axes_code
from fasad.run import postroit_vse

HERE = Path(__file__).parent


def main():
    acad = Acad()
    systems_dir = HERE / "systems"
    acad.run_lisp(define_block_code())
    acad.run_lisp(read_axes_code())
    acad.run_lisp(read_blocks_code())
    blocks = parse_blocks(_axes_lines())
    sysname = next((a.get("СИСТЕМА", "").strip() for *_xy, a, _h in blocks if a.get("СИСТЕМА", "").strip()),
                   "КП50К")
    system = load_system(systems_dir, sysname or "КП50К")
    defaults = ObjectParams(заполнение=50, вариант="", выпуск_низ=20, выпуск_верх=10)
    result = postroit_vse(acad, system, defaults, systems_dir)
    warns = [f"{o.имя}: {w}" for o, f in result for w in f.warnings]
    report("Добавить сечение - готово", [f"Построено витражей: {len(result)}"], warns)


if __name__ == "__main__":
    try:
        main()
    except (AcadError, OSError) as e:
        print("ОШИБКА:", e)
