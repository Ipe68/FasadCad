"""Собрать базу узлов из каталога СИАЛ (без AutoCAD).

Когда запускать: поправили КП50К_комплектация.csv / КП50К_периметр.csv / КП50К.toml (уплотнители,
доборные_толщины) или лист каталога 03 "Основные сечения" (DXF).
Запуск:  py Собрать_базу.py

Что делает:
1. Читает лист 03 (узлы и доборные периметра) и лист 02 (опорные подкладки) каталога.
2. Старую базу копирует в systems\\архив_базы\\<дата время>.
3. Записывает systems\\КП50К_узлы_каталог.json и systems\\КП50К_детали.dxf.
4. Рисует атлас для проверки глазами: выход\\атлас (слева каталог, справа база).
Замечания (что не разобрано, что не сошлось с таблицами) - на экране и в выход\\атлас\\замечания.txt.
"""

import shutil
import sys
from datetime import datetime
from pathlib import Path

from fasad.catalog_nodes import build_base
from fasad.catalog_pads import extract_pads
from fasad.facade import load_system

HERE = Path(__file__).parent
СИСТЕМА = "КП50К"
КАТАЛОГ = Path(r"C:\cad\Kat sial\Каталог КП50К_ACAD_02_2025")
ЛИСТ_СЕЧЕНИЯ = КАТАЛОГ / "03 Основные сечения КР50К_2024.dxf"
ЛИСТ_КОМПЛЕКТУЮЩИЕ = КАТАЛОГ / "02 Комплектующие КП50К_2024.dxf"


def main(out_dir: Path | None = None):
    systems = HERE / "systems"
    out_dir = out_dir or systems
    for f in (ЛИСТ_СЕЧЕНИЯ, ЛИСТ_КОМПЛЕКТУЮЩИЕ):
        if not f.exists():
            print("ОШИБКА: нет файла каталога", f)
            return
    system = load_system(systems, СИСТЕМА)
    pads, pad_notes = extract_pads(ЛИСТ_КОМПЛЕКТУЮЩИЕ)

    out_json = out_dir / f"{СИСТЕМА}_узлы_каталог.json"
    out_dxf = out_dir / f"{СИСТЕМА}_детали.dxf"
    old = [f for f in (out_json, out_dxf) if f.exists()]
    if old:
        arch = systems / "архив_базы" / datetime.now().strftime("%Y-%m-%d %H-%M")
        arch.mkdir(parents=True, exist_ok=True)
        for f in old:
            shutil.copy2(f, arch / f.name)
        print("Старая база сохранена:", arch)

    res = build_base(ЛИСТ_СЕЧЕНИЯ, system, out_json, out_dxf, extra_blocks=pads)
    notes = [f"подкладки: {n}" for n in pad_notes] + res["замечания"]
    print(f"Узлов в каталоге найдено: {res['найдено']}, в базу записано: {res['узлов']}")
    print(f"Деталей (блоков): {res['деталей']}, подкладок: {len(pads)}, рядов периметра: {res['периметр']}")
    print("Записано:", out_json.name, ",", out_dxf.name)

    atlas_dir = HERE / "выход" / "атлас"
    try:
        from fasad.atlas import render
        pages = render(out_dir, ЛИСТ_СЕЧЕНИЯ, atlas_dir, system)
        print(f"Атлас для проверки: {atlas_dir} ({len(pages)} листов)")
    except ImportError as e:
        print("Атлас не нарисован (нет библиотеки):", e)
    atlas_dir.mkdir(parents=True, exist_ok=True)
    (atlas_dir / "замечания.txt").write_text("\n".join(notes) or "замечаний нет", encoding="utf-8")
    print(f"\nЗамечания ({len(notes)}):" if notes else "\nЗамечаний нет.")
    for n in notes:
        print(" -", n)


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else None)
