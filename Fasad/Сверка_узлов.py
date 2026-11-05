"""Сверка библиотеки узлов (файл образцов пользователя) - без AutoCAD.

Запуск:  py Сверка_узлов.py
1. Читает библиотеку (путь - в карточке системы, ["сечение"]["библиотека"]).
2. Показывает, каких узлов из таблицы комплектации в библиотеке нет (их программа соберёт черновиком по правилам).
3. Ищет возможные описки: деталь стоит иначе, чем в большинстве похожих узлов (от той же опорной линии, с тем же прижимом).
4. Новые наборы доборных (нарисованы, а в таблице периметра нет) - по запросу вносит в таблицу (копия - .csv.bak).
Результат - на экране и в выход\\сверка_узлов.txt.
"""

from collections import Counter
from pathlib import Path

from fasad import library as L
from fasad import rules as R
from fasad.facade import load_system, parse_items

HERE = Path(__file__).parent
СИСТЕМА = "КП50К"


def main():
    s = load_system(HERE / "systems", СИСТЕМА)
    print("Библиотека:", L.library_path(s))
    nodes, _ = L.load(s, HERE / "systems")
    known = L._known(s)
    lines = [f"Узлов в библиотеке: {len(nodes)}"]
    have = {(n.variant, n.thick, n.beam, n.edge) for n in nodes}
    miss = sorted({(r["Вариант"], int(r["Толщина"])) for r in s.kits for b in (False, True) for e in (False, True)
                   if (r["Вариант"], int(r["Толщина"]), b, e) not in have})
    lines.append("Нет в библиотеке (будет черновик по правилам): " +
                 (", ".join(f"{v} {t} мм" for v, t in miss) if miss else "нет"))
    dup = Counter((n.variant, n.thick, n.beam, n.edge, tuple(sorted(L.label(a, known) for a in n.stack))) for n in nodes)
    if any(v > 1 for v in dup.values()):
        lines.append("Одинаковые узлы (берётся тот, что с подписью строки атласа; лишние можно удалить):")
        lines += [f"  {k[0]} {k[1]} мм, {'ригель' if k[2] else 'стойка'} {'крайн.' if k[3] else 'средн.'}"
                  f"{(': ' + ' + '.join(k[4])) if k[4] else ''} - {v} шт." for k, v in dup.items() if v > 1]
    found = L.audit(nodes, s)
    lines.append(f"\nВозможные описки: {len(found)}")
    lines += ["  " + f for f in found]
    text = "\n".join(lines)
    print(text)
    out = HERE / "выход" / "сверка_узлов.txt"
    out.write_text(text, encoding="utf-8")
    print("\nСохранено:", out)
    new_perimeter(nodes, s, known)


def _sets(text):
    """Набор доборных строки таблицы - и как прорисован, и с аналогом из скобок."""
    prim = tuple(sorted(R._art(a) for a, alt, q in parse_items(text) for _ in range(q)))
    alt = tuple(sorted((al.strip() or R._art(a)) for a, al, q in parse_items(text) for _ in range(q)))
    return {prim, alt}


def _join(arts):
    """['Т50-06', 'Т50-06', 'Т50-07'] -> ['Т50-06 x2', 'Т50-07'] (как в таблице, порядок - от носика)."""
    out = []
    for a in arts:
        if out and out[-1][0] == a:
            out[-1][1] += 1
        else:
            out.append([a, 1])
    return [a if q == 1 else f"{a} x{q}" for a, q in out]


def new_perimeter(nodes, s, known):
    """Наборы доборных из библиотеки, которых нет в таблице периметра, - по запросу внести в таблицу."""
    have = {}
    for r in s.perimeters:
        t = int(r["Толщина"])
        have.setdefault((t, False), set()).update(_sets(r["Крайняя стойка"]))
        have.setdefault((t, True), set()).update(_sets(r["Крайний ригель"]))
    found = {}                                  # (толщина, строка атласа) -> {"стойка"/"ригель": набор}
    for n in nodes:
        if not n.edge:
            continue
        st = tuple(L.label(a, known) for a in n.stack)
        if tuple(sorted(st)) in have.get((n.thick, n.beam), set()):
            continue
        row = n.where.split(", ")[0] if n.labeled else n.where
        found.setdefault((n.thick, row), {})["ригель" if n.beam else "стойка"] = " + ".join(_join(st))
    if not found:
        print("Новых наборов доборных нет - таблица периметра полная.")
        return
    path = HERE / "systems" / f"{СИСТЕМА}_периметр.csv"
    for (t, row), d in sorted(found.items()):
        print(f"\nНОВЫЙ НАБОР ДОБОРНЫХ, {t} мм ({row}):")
        print(f"  крайняя стойка: {d.get('стойка', '- (не нарисована)')}")
        print(f"  крайний ригель: {d.get('ригель', '- (не нарисован)')}")
        if input("Внести в таблицу периметра? (y/n): ").strip().lower() not in ("y", "д", "да", "yes"):
            continue
        page = input("Страница каталога (Enter - 'чертёж пользователя'): ").strip() or "чертёж пользователя"
        var = 1 + max([int(r["Вариант"]) for r in s.perimeters if int(r["Толщина"]) == t] or [0])
        line = f"{t};{var};{d.get('стойка', '')};{d.get('ригель', '')};{page}"
        _insert_row(path, t, line)
        s.perimeters.append({"Толщина": str(t), "Вариант": str(var), "Крайняя стойка": d.get("стойка", ""),
                             "Крайний ригель": d.get("ригель", ""), "Стр.": page})
        print(f"  внесено: вариант {var} для {t} мм")


def _insert_row(path: Path, t: int, line: str):
    """Строку - после последней строки той же толщины (по порядку толщин); копия таблицы - .csv.bak."""
    raw = path.read_bytes()
    path.with_suffix(".csv.bak").write_bytes(raw)
    bom = raw.startswith(b"\xef\xbb\xbf")
    rows = raw.decode("utf-8-sig").splitlines()
    pos = len(rows)
    for i, r in enumerate(rows[1:], 1):
        try:
            if int(r.split(";")[0]) <= t:
                pos = i + 1
        except ValueError:
            continue
    rows.insert(pos, line)
    path.write_bytes(("﻿" if bom else "").encode() + ("\r\n".join(rows) + "\r\n").encode("utf-8"))


if __name__ == "__main__":
    main()
