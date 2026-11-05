"""Опорные подкладки под стекло из каталога (02 "Комплектующие", раздел "Алюминиевые подкладки", лист 25 PDF).

Каждая подкладка - строка таблицы: артикул слева, контур справа (слой 0). Масштаб - по размерам строки.
Блок подкладки: точка вставки - верх борта (как блок КП45391 пользователя), полка - вправо (наружу).
Ставится в сечении А-А на ригель: (точка ригеля + 3,75 наружу, ось ригеля + 13) - снято с вставки пользователя.
"""

from __future__ import annotations

import re

import ezdxf
from ezdxf import bbox

AREA = (3370, 100, 3620, 280)          # раздел "Алюминиевые подкладки" на листе (единицы листа)
ARTICLE = re.compile(r"^\s*(КП\s?\d+|КПС\s?\d+)\s*$")


def extract_pads(dxf_path) -> tuple[dict, list[str]]:
    """{артикул: [(точки [(x, y, bulge)], замкнут)]} в мм, точка - верх борта. И замечания."""
    doc = ezdxf.readfile(str(dxf_path))
    msp = doc.modelspace()
    x0, y0, x1, y1 = AREA
    labels, polys, dims = [], [], []
    for e in msp:
        try:
            b = bbox.extents([e])
        except Exception:
            continue
        if not b.has_data:
            continue
        mx, my = (b.extmin.x + b.extmax.x) / 2, (b.extmin.y + b.extmax.y) / 2
        if not (x0 <= mx <= x1 and y0 <= my <= y1):
            continue
        t = e.dxftype()
        if t in ("TEXT", "MTEXT"):
            s = e.plain_text() if t == "MTEXT" else e.dxf.text
            if ARTICLE.match(s) and mx < 3400:
                labels.append((my, s.strip()))
        elif t == "LWPOLYLINE" and e.dxf.layer == "0":
            polys.append((my, e))
        elif t == "DIMENSION":
            # раздел начерчен в 1:2 - множитель размеров DIMLFAC стиля (размер "43" на листе длиной 21,5)
            dims.append((my, float(e.get_dim_style().dxf.get("dimlfac", 1.0))))
    notes = []
    out = {}
    for ly, art in labels:
        mine = [e for py, e in polys if min(labels, key=lambda l: abs(l[0] - py))[1] == art]
        if not mine:
            notes.append(f"{art}: контур не найден")
            continue
        k_row = [k for dy, k in dims if min(labels, key=lambda l: abs(l[0] - dy))[1] == art]
        k = sorted(k_row)[len(k_row) // 2] if k_row else 1.0       # масштаб: мм на единицу листа
        # в каталоге борт справа - отражаем, чтобы борт был слева, как в блоке пользователя (дуги меняют знак)
        pts_all = [[(-x, y, -bl) for x, y, _, _, bl in e.get_points("xyseb")] for e in mine]
        ys = [p[1] for pts in pts_all for p in pts]
        xs = [p[0] for pts in pts_all for p in pts]
        top = max(ys)
        # борт - слева сверху: точка вставки - середина верхних вершин левой части
        left = min(xs)
        tops = [p[0] for pts in pts_all for p in pts if p[1] > top - 0.6 / k and p[0] < left + 4 / k]
        bx = (min(tops) + max(tops)) / 2 if tops else left
        out[art.replace("  ", " ")] = [([((x - bx) * k, (y - top) * k, bl) for x, y, bl in pts], bool(e.closed))
                                       for pts, e in zip(pts_all, mine)]
        if abs(k - 1) > 0.02:
            notes.append(f"{art}: масштаб листа {k:.3f}")
    return out, notes


# место подкладки на ригеле - снято с вставки пользователя (КП45391 на КП45550, Т50 32 мм)
LIP_FROM_NOSE = 17.25          # борт (точка вставки) - внутрь от носика ригеля, мм
RUBBER = 2.0                   # резиновая подкладка ТПУ-017-.. на полке: стекло на 2 мм выше полки


def bearing_y(polys) -> float:
    """Уровень полки, на которую ложится стекло: самый длинный горизонтальный участок в верхней половине."""
    best, y_best = 0.0, 0.0
    low = min(p[1] for pts, _ in polys for p in pts)
    for pts, closed in polys:
        seq = pts + pts[:1] if closed else pts
        for (x1, y1, b), (x2, y2, _) in zip(seq, seq[1:]):
            if abs(y1 - y2) < 0.01 and abs(b) < 1e-9 and y1 > low / 2 and abs(x2 - x1) > best:
                best, y_best = abs(x2 - x1), y1
    return y_best
