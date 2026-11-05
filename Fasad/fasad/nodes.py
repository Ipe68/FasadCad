"""Шаблоны узлов сечений: какие блоки и где стоят вокруг стойки/ригеля.

Положение каждой детали хранится в системе координат профиля:
точка (x, y), поворот и признак зеркала. Преобразование = поворот * зеркало по X.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class Place:
    """Вставка блока: точка, поворот (град), зеркало по X."""
    name: str
    x: float
    y: float
    rot: float = 0.0
    mirror: bool = False


def _mat(rot: float, mirror: bool):
    a = math.radians(rot)
    c, s = math.cos(a), math.sin(a)
    m = -1.0 if mirror else 1.0
    # R(a) * diag(m, 1)
    return [[c * m, -s], [s * m, c]]


def _mul(a, b):
    return [[a[0][0] * b[0][0] + a[0][1] * b[1][0], a[0][0] * b[0][1] + a[0][1] * b[1][1]],
            [a[1][0] * b[0][0] + a[1][1] * b[1][0], a[1][0] * b[0][1] + a[1][1] * b[1][1]]]


def _inv(a):
    det = a[0][0] * a[1][1] - a[0][1] * a[1][0]
    return [[a[1][1] / det, -a[0][1] / det], [-a[1][0] / det, a[0][0] / det]]


def _decompose(m) -> tuple[float, bool]:
    det = m[0][0] * m[1][1] - m[0][1] * m[1][0]
    mirror = det < 0
    k = -1.0 if mirror else 1.0
    rot = math.degrees(math.atan2(m[1][0] * k, m[0][0] * k)) % 360
    return round(rot, 4) % 360, mirror


def to_local(base: Place, part: Place) -> Place:
    """Мировая вставка детали -> положение в системе профиля base."""
    mb = _mat(base.rot, base.mirror)
    inv = _inv(mb)
    dx, dy = part.x - base.x, part.y - base.y
    lx = inv[0][0] * dx + inv[0][1] * dy
    ly = inv[1][0] * dx + inv[1][1] * dy
    rot, mirror = _decompose(_mul(inv, _mat(part.rot, part.mirror)))
    return Place(part.name, round(lx, 3), round(ly, 3), rot, mirror)


def to_world(base: Place, part: Place) -> Place:
    """Положение детали в системе профиля -> мировая вставка при профиле base."""
    mb = _mat(base.rot, base.mirror)
    wx = base.x + mb[0][0] * part.x + mb[0][1] * part.y
    wy = base.y + mb[1][0] * part.x + mb[1][1] * part.y
    rot, mirror = _decompose(_mul(mb, _mat(part.rot, part.mirror)))
    return Place(part.name, round(wx, 3), round(wy, 3), rot, mirror)


def node_places(template: dict, base: Place) -> list[Place]:
    """Все вставки узла: сам профиль + детали шаблона."""
    out = [Place(template["профиль"], base.x, base.y, base.rot, base.mirror)]
    for d in template["детали"]:
        out.append(to_world(base, Place(d["блок"], d["x"], d["y"], d["поворот"], d["зеркало"])))
    return out
