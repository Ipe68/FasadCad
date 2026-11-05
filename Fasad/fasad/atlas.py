"""Атлас базы узлов для проверки глазами: по каждому варианту - все толщины,
слева узел каталога (лист 03), справа узел, собранный из базы (блоки КП50К_детали.dxf по КП50К_узлы_каталог.json).
Без AutoCAD: картинки PNG.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import ezdxf
from ezdxf import bbox

from .catalog_nodes import SCALE, find_nodes, parse_post_node

COLORS = {"крышка": "#1f77b4", "прижим": "#ff7f0e", "термовставка": "#2ca02c", "уплотнитель_прижима": "#d62728",
          "уплотнитель_стойки": "#9467bd", "подкладка_фикс": "#8c564b", "винт": "#e377c2", "лента": "#7f7f7f"}


def _block_polys(lib, name):
    return [([(x, y) for x, y, *_ in e.get_points("xy")], e.closed) for e in lib.blocks.get(name)
            if e.dxftype() == "LWPOLYLINE"]


def render(systems_dir: Path, catalog_dxf: Path, out_dir: Path, system) -> list[Path]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    base = json.loads((systems_dir / "КП50К_узлы_каталог.json").read_text(encoding="utf-8"))["узлы"]
    lib = ezdxf.readfile(str(systems_dir / "КП50К_детали.dxf"))
    msp = ezdxf.readfile(str(catalog_dxf)).modelspace()
    src = {}
    for n in find_nodes(msp):
        src.setdefault((n["серия"], n["толщина"]), []).append(n)
    out_dir.mkdir(parents=True, exist_ok=True)
    pages = []
    for var, nodes in base.items():
        ts = sorted(nodes, key=int)
        cols = 4
        rows = math.ceil(len(ts) / cols)
        fig, axs = plt.subplots(rows, cols * 2, figsize=(cols * 2 * 2.6, rows * 3.2), dpi=80, squeeze=False)
        for ax in axs.flat:
            ax.axis("off")
        for k, t in enumerate(ts):
            node = nodes[t]
            a0, a1 = axs[k // cols][(k % cols) * 2], axs[k // cols][(k % cols) * 2 + 1]
            # каталог: то же окно, что разбирали, в координатах узла
            ser = "стандарт" if var in ("Т50", "Т50-10") else var
            x = float(node["источник"].split("x=")[1])
            cand = [n for n in src.get((ser, int(t)), []) if abs(n["x"] - x) < 2]
            if cand:
                kit = system.kit(int(t), var)
                b, _, _ = parse_post_node(msp, cand[0]["окно"], kit, system.card["уплотнители"])
                x0, y0, x1, y1 = cand[0]["окно"]
                for e in msp.query("LWPOLYLINE"):
                    eb = bbox.extents([e])
                    if not eb.has_data:
                        continue
                    mx, my = (eb.extmin.x + eb.extmax.x) / 2, (eb.extmin.y + eb.extmax.y) / 2
                    if not (x0 <= mx <= x1 and y0 <= my <= y1) or e.dxf.layer in ("Зигзаг",):
                        continue
                    pts = [(px * SCALE - b["ось"], (py * SCALE - b["носик"]) * b["наружу"]) for px, py in e.get_points("xy")]
                    if e.closed:
                        pts.append(pts[0])
                    a0.plot([p[0] for p in pts], [p[1] for p in pts], lw=0.5,
                            color="#4169e1" if e.dxf.layer == "Glass" else "#c00000")
            # база
            for d in node["детали"]:
                m = -1 if d["зеркало"] else 1
                for pts, closed in _block_polys(lib, d["блок"]):
                    xs = [d["x"] + m * p[0] for p in pts]
                    ys = [d["y"] + p[1] for p in pts]
                    if closed:
                        xs.append(xs[0]); ys.append(ys[0])
                    a1.plot(xs, ys, lw=0.6, color=COLORS.get(d["роль"], "black"))
            if node.get("стекло_внутр") is not None:
                a1.axhline(node["стекло_внутр"], color="#4169e1", lw=0.5, ls="--")
            for ax, title in ((a0, f"каталог {t} мм"), (a1, f"база {t} мм")):
                ax.set_xlim(-40, 40)
                ax.set_ylim(-45, int(t) + 30)
                ax.set_aspect("equal")
                ax.set_title(title, fontsize=9, color="#c00000" if node["замечания"] and ax is a1 else "black")
        fig.suptitle(f"{var}: узлы стоек (слева каталог, справа база)", fontsize=12)
        fig.tight_layout()
        p = out_dir / f"атлас {var}.png"
        fig.savefig(p, facecolor="white")
        plt.close(fig)
        pages.append(p)
    return pages
