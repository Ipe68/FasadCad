"""Фоновый расчёт требований к профилю марки для программы Фасад (команда ЗАМЕНАПРОФИЛЯ).

Вход  (argv[1]): JSON {"b1": мм, "b2": мм, "l1": мм, "l2": мм, "h_m": мм, "t_glass": мм}
Выход (argv[2]): JSON {"требования": {...}} - ключи _last_calc (стойка_треб_ix/wx, ригель_треб_ix/iy/wx).
Сеанс (ветровой/снеговой районы, высоты здания, коэффициенты) восстанавливается
из save_data/последний_сеанс.json - считается как в окне Лоры.
"""
import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.chdir(HERE)

from PyQt5 import QtWidgets          # noqa: E402
import main as M                     # noqa: E402


def main():
    inp, outp = sys.argv[1], sys.argv[2]
    with open(inp, encoding="utf-8") as f:
        dims = json.load(f)
    app = QtWidgets.QApplication([])          # noqa: F841
    w = M.LoraApp(fasad_path=None)            # сеанс восстановится сам
    for field, key in (("lineEdit_4", "b1"), ("lineEdit_5", "b2"), ("lineEdit_6", "l1"),
                       ("lineEdit_7", "l2"), ("lineEdit_8", "h_m"), ("lineEdit_12", "t_glass")):
        if dims.get(key) is not None:
            getattr(w, field).setText(f"{float(dims[key]):g}")
    w.calculate(auto_select=False)
    with open(outp, "w", encoding="utf-8") as f:
        json.dump({"требования": w._last_calc}, f, ensure_ascii=False)


if __name__ == "__main__":
    main()
