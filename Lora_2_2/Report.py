# -*- coding: utf-8 -*-
"""
Report.py — формирование PDF-отчёта по расчёту (Lora).

Модуль не зависит от PyQt и от структур данных программы: main.py собирает
ReportData (разделы, таблицы, формулы, рисунки), а build_pdf() рисует PDF.
Единственная зависимость: reportlab  (pip install reportlab).

Использование:
    from Report import ReportData, Section, TableBlock, FormulaBlock, ImageBlock, build_pdf
    build_pdf(data, r"C:\\path\\otchet.pdf")
"""
import os
import datetime
from dataclasses import dataclass, field
from typing import List, Optional, Union

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (BaseDocTemplate, Frame, PageTemplate, Paragraph,
                                Spacer, Table, TableStyle, Image, KeepTogether,
                                PageBreak)

# --------------------------------------------------------------------------
# Шрифты (нужна кириллица). Ищем Arial в Windows, затем DejaVu.
# --------------------------------------------------------------------------
_FONT_SETS = [
    ("Arial", ["C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf",
               "C:/Windows/Fonts/ariali.ttf", "C:/Windows/Fonts/arialbi.ttf"]),
    ("DejaVu", ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-BoldOblique.ttf"]),
]
FONT = "Helvetica"
FONT_B = "Helvetica-Bold"


def _register_fonts():
    global FONT, FONT_B
    for name, paths in _FONT_SETS:
        if all(os.path.exists(p) for p in paths):
            r, b, i, bi = (f"{name}-{s}" for s in ("R", "B", "I", "BI"))
            for fn, p in zip((r, b, i, bi), paths):
                pdfmetrics.registerFont(TTFont(fn, p))
            pdfmetrics.registerFontFamily(r, normal=r, bold=b, italic=i, boldItalic=bi)
            FONT, FONT_B = r, b
            return
    raise RuntimeError("Не найден шрифт с кириллицей (Arial / DejaVu Sans).")


# --------------------------------------------------------------------------
# Модель данных отчёта
# --------------------------------------------------------------------------
@dataclass
class TableBlock:
    """Таблица. header — заголовки, rows — строки (значения приводятся к str)."""
    title: str
    header: List[str]
    rows: List[list]
    col_widths_mm: Optional[List[float]] = None   # None — поровну
    note: str = ""
    font_size: float = 8.5


@dataclass
class FormulaBlock:
    """
    Формула. formula — строка с разметкой reportlab: <i>, <sub>, <super>, <b>
    и юникодом (σ, φ, γ, ≤, √, ×, ∑ ...). where — расшифровка обозначений.
    ref — ссылка на норматив, например «СП 16.13330.2017, п. 8.1.1».
    """
    formula: str
    where: List[str] = field(default_factory=list)
    ref: str = ""
    numbered: bool = True
    subst: str = ""          # строка с подстановкой чисел (под формулой)


@dataclass
class SubTitle:
    """Подзаголовок внутри раздела (например «Расчёт на прочность»)."""
    text: str


@dataclass
class ListBlock:
    """Строки с переносами без выравнивания по ширине (исходные данные, «где…»)."""
    lines: List[str]


@dataclass
class CheckBlock:
    """Итог проверки: зелёное «выполняется» / красное «НЕ выполняется»."""
    text: str
    ok: bool


@dataclass
class ImageBlock:
    """Рисунок (расчётная схема, эпюры). path — png/jpg. width_mm — ширина на листе."""
    path: str
    caption: str = ""
    width_mm: float = 170


@dataclass
class Section:
    """Раздел. items — строки (абзацы) и блоки TableBlock/FormulaBlock/ImageBlock."""
    title: str
    items: List[Union[str, TableBlock, FormulaBlock, ImageBlock, SubTitle,
                      ListBlock, CheckBlock]] = field(default_factory=list)


@dataclass
class ReportData:
    title: str = "Отчёт о расчёте"
    object_name: str = ""
    organization: str = ""
    author: str = ""
    program: str = "Lora"
    program_version: str = ""
    date: str = field(default_factory=lambda: datetime.date.today().strftime("%d.%m.%Y"))
    sections: List[Section] = field(default_factory=list)
    conclusion: List[str] = field(default_factory=list)
    references: List[str] = field(default_factory=list)   # нормативные документы


# --------------------------------------------------------------------------
# Стили
# --------------------------------------------------------------------------
def _styles():
    base = dict(fontName=FONT, fontSize=10, leading=13.5, alignment=TA_JUSTIFY)
    return {
        "body": ParagraphStyle("body", **base, spaceAfter=4),
        "h1": ParagraphStyle("h1", fontName=FONT_B, fontSize=13, leading=16,
                             spaceBefore=10, spaceAfter=6, keepWithNext=1),
        "title": ParagraphStyle("title", fontName=FONT_B, fontSize=20, leading=25,
                                alignment=TA_CENTER, spaceAfter=10),
        "center": ParagraphStyle("center", fontName=FONT, fontSize=11, leading=15,
                                 alignment=TA_CENTER),
        "formula": ParagraphStyle("formula", fontName=FONT, fontSize=11, leading=19,
                                  alignment=TA_CENTER),
        "where": ParagraphStyle("where", fontName=FONT, fontSize=9, leading=12,
                                leftIndent=12, alignment=TA_JUSTIFY),
        "ref": ParagraphStyle("ref", fontName=FONT, fontSize=8.5, leading=11,
                              leftIndent=12, textColor=colors.HexColor("#444444")),
        "caption": ParagraphStyle("caption", fontName=FONT, fontSize=9, leading=12,
                                  alignment=TA_CENTER, spaceBefore=2, spaceAfter=8),
        "tcap": ParagraphStyle("tcap", fontName=FONT, fontSize=9, leading=12,
                               spaceBefore=6, spaceAfter=3),
        "h2": ParagraphStyle("h2", fontName=FONT_B, fontSize=10.5, leading=14,
                             spaceBefore=8, spaceAfter=3, keepWithNext=1),
        "list": ParagraphStyle("list", fontName=FONT, fontSize=10, leading=13.5,
                               spaceAfter=4),
        "cell": ParagraphStyle("cell", fontName=FONT, fontSize=8.5, leading=10.5),
        "cellh": ParagraphStyle("cellh", fontName=FONT_B, fontSize=8.5, leading=10.5,
                                alignment=TA_CENTER),
    }


def _esc(s) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# --------------------------------------------------------------------------
# Построение
# --------------------------------------------------------------------------
class _Builder:
    def __init__(self, data: ReportData):
        self.d = data
        self.st = _styles()
        self.n_formula = 0
        self.n_table = 0
        self.n_figure = 0
        self.width = A4[0] - 40 * mm   # поля 20 мм

    # ---- блоки
    def table(self, b: TableBlock):
        self.n_table += 1
        st = self.st
        cell = ParagraphStyle("c", parent=st["cell"], fontSize=b.font_size,
                              leading=b.font_size + 2)
        cellh = ParagraphStyle("ch", parent=st["cellh"], fontSize=b.font_size,
                               leading=b.font_size + 2)
        rows = [[Paragraph(str(h), cellh) for h in b.header]]   # заголовки допускают <sub>/<super>
        for r in b.rows:
            rows.append([Paragraph(_esc(c), cell) for c in r])
        n = len(b.header)
        if b.col_widths_mm:
            widths = [w * mm for w in b.col_widths_mm]
        else:
            widths = [self.width / n] * n
        t = Table(rows, colWidths=widths, repeatRows=1)
        t.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#777777")),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E6EAF0")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ]))
        out = []
        if b.title:
            out.append(Paragraph(f"Таблица {self.n_table} — {_esc(b.title)}", st["tcap"]))
        out.append(t)
        if b.note:
            out.append(Paragraph(b.note, st["where"]))   # note допускает <sub>/<super>
        out.append(Spacer(1, 6))
        return out

    def formula(self, b: FormulaBlock):
        st = self.st
        if b.numbered:
            self.n_formula += 1
            num = Paragraph(f"({self.n_formula})", st["formula"])
        else:
            num = ""
        t = Table([["", Paragraph(b.formula, st["formula"]), num]],
                  colWidths=[15 * mm, self.width - 30 * mm, 15 * mm])
        t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                               ("ALIGN", (2, 0), (2, 0), "RIGHT")]))
        out = [Spacer(1, 3), t]
        if b.subst:
            out.append(Paragraph(b.subst, st["formula"]))
            out.append(Spacer(1, 2))
        if b.where:
            out.append(Paragraph("где:<br/>" + "<br/>".join(b.where), st["where"]))
        if b.ref:
            out.append(Paragraph("Источник: " + _esc(b.ref), st["ref"]))
        out.append(Spacer(1, 5))
        return [KeepTogether(out)]

    def image(self, b: ImageBlock):
        if not os.path.exists(b.path):
            return [Paragraph(f"[рисунок не найден: {_esc(b.path)}]", self.st["body"])]
        self.n_figure += 1
        from reportlab.lib.utils import ImageReader
        iw, ih = ImageReader(b.path).getSize()
        w = min(b.width_mm * mm, self.width)
        img = Image(b.path, width=w, height=w * ih / iw)
        cap = f"Рисунок {self.n_figure}" + (f" — {_esc(b.caption)}" if b.caption else "")
        return [KeepTogether([img, Paragraph(cap, self.st["caption"])])]

    # ---- страницы
    def title_page(self):
        d, st = self.d, self.st
        s = [Spacer(1, 50 * mm)]
        if d.organization:
            s.append(Paragraph(_esc(d.organization), st["center"]))
            s.append(Spacer(1, 25 * mm))
        s.append(Paragraph(_esc(d.title), st["title"]))
        if d.object_name:
            s.append(Paragraph(_esc(d.object_name), st["center"]))
        s.append(Spacer(1, 60 * mm))
        if d.author:
            s.append(Paragraph(f"Исполнитель: {_esc(d.author)}", st["center"]))
        prog = d.program + (f" {d.program_version}" if d.program_version else "")
        s.append(Paragraph(f"Программа: {_esc(prog)}", st["center"]))
        s.append(Paragraph(f"Дата: {d.date}", st["center"]))
        s.append(PageBreak())
        return s

    def story(self):
        st = self.st
        s = self.title_page()
        for i, sec in enumerate(self.d.sections, 1):
            s.append(Paragraph(f"{i}. {_esc(sec.title)}", st["h1"]))
            for it in sec.items:
                if isinstance(it, str):
                    s.append(Paragraph(it, st["body"]))      # допускает <b>, <i>, <sub>
                elif isinstance(it, TableBlock):
                    s += self.table(it)
                elif isinstance(it, FormulaBlock):
                    s += self.formula(it)
                elif isinstance(it, ImageBlock):
                    s += self.image(it)
                elif isinstance(it, SubTitle):
                    s.append(Paragraph(it.text, st["h2"]))
                elif isinstance(it, ListBlock):
                    s.append(Paragraph("<br/>".join(it.lines), st["list"]))
                elif isinstance(it, CheckBlock):
                    col = "#1a7f37" if it.ok else "#c62828"
                    s.append(Paragraph(f'<font color="{col}"><b>{it.text}</b></font>', st["body"]))
        k = len(self.d.sections)
        if self.d.conclusion:
            k += 1
            s.append(Paragraph(f"{k}. Заключение", st["h1"]))
            for p in self.d.conclusion:
                s.append(Paragraph(p, st["body"]))
        if self.d.references:
            k += 1
            s.append(Paragraph(f"{k}. Нормативные и справочные документы", st["h1"]))
            for j, r in enumerate(self.d.references, 1):
                s.append(Paragraph(f"[{j}] {_esc(r)}", st["body"]))
        return s


def build_pdf(data: ReportData, path: str) -> str:
    """Собрать PDF и сохранить в path. Возвращает path."""
    _register_fonts()
    b = _Builder(data)

    def _page(canvas, doc):
        canvas.saveState()
        canvas.setFont(FONT, 8)
        canvas.setStrokeColor(colors.HexColor("#999999"))
        canvas.line(20 * mm, 15 * mm, A4[0] - 20 * mm, 15 * mm)
        canvas.drawString(20 * mm, 10 * mm, f"{data.program} — {data.object_name or data.title}")
        canvas.drawRightString(A4[0] - 20 * mm, 10 * mm, f"Лист {doc.page}")
        canvas.restoreState()

    doc = BaseDocTemplate(path, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm,
                          topMargin=18 * mm, bottomMargin=20 * mm,
                          title=data.title, author=data.author or data.program)
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="f")
    doc.addPageTemplates([PageTemplate(id="p", frames=[frame], onPage=_page)])
    doc.build(b.story())
    return path


# --------------------------------------------------------------------------
# Интеграция с PyQt5 (вызывать из main.py)
# --------------------------------------------------------------------------
def export_pdf_dialog(parent, data: ReportData, default_name="Отчёт.pdf") -> Optional[str]:
    """Диалог «Сохранить как…», сборка PDF, сообщение, открытие файла."""
    from PyQt5.QtWidgets import QFileDialog, QMessageBox
    from PyQt5.QtGui import QDesktopServices
    from PyQt5.QtCore import QUrl
    path, _ = QFileDialog.getSaveFileName(parent, "Экспорт отчёта", default_name,
                                          "PDF (*.pdf)")
    if not path:
        return None
    if not path.lower().endswith(".pdf"):
        path += ".pdf"
    try:
        build_pdf(data, path)
    except PermissionError:
        QMessageBox.warning(parent, "Экспорт", "Файл открыт в другой программе. "
                                               "Закройте его и повторите.")
        return None
    except Exception as e:                      # noqa
        QMessageBox.critical(parent, "Экспорт PDF", f"Ошибка: {e}")
        return None
    QDesktopServices.openUrl(QUrl.fromLocalFile(path))
    return path


def grab_widget_png(widget, path: str) -> str:
    """Сохранить вид виджета (схема, эпюры) в PNG для ImageBlock."""
    widget.grab().save(path, "PNG")
    return path


# ==========================================================================
#  Содержание отчёта Lora (стоечно-ригельный фасад)
#  Формулы продублированы 1:1 из calculations.py / main.py (calculate,
#  verify_selected_profiles). Если там меняется методика — менять и здесь.
# ==========================================================================
import math

E_MOD = 70000.0                       # модуль упругости алюминия, МПа
MU_X, MU_Y = 0.725, 0.5               # коэф. расчётной длины стойки
TERRAIN = {"A": (0.15, 1.0, 0.76), "B": (0.20, 0.65, 1.06), "C": (0.25, 0.40, 1.78)}  # alpha, k10, zeta10

REFERENCES = [
    "СП 128.13330.2016 «Алюминиевые конструкции». Актуализированная редакция СНиП 2.03.06-85.",
    "СП 20.13330.2016 «Нагрузки и воздействия». Актуализированная редакция СНиП 2.01.07-85*.",
    "ГОСТ 27751-2014 «Надёжность строительных конструкций и оснований. Основные положения» (коэффициент надёжности по ответственности γn).",
    "ГОСТ 22233 «Профили прессованные из алюминиевых сплавов для светопрозрачных ограждающих конструкций. Технические условия» (расчётное сопротивление сплава 6063 Т6, если оно не приведено в СП 128.13330.2016).",
    "Техническая документация производителя профильной системы (геометрические характеристики профилей, коэффициенты k1, k2).",
]


def n(x, d=2):
    """Число с запятой, фиксированное число знаков."""
    return f"{x:.{d}f}".replace(".", ",")


def g(x):
    """Число с запятой, без лишних нулей."""
    return f"{x:g}".replace(".", ",")


def _sub(a, b):
    return f"{a}<sub>{b}</sub>"


def _profile_table(title, p, kind):
    if p.get("amp"):
        title += " (составное сечение, приведённое к алюминию)"
    head = ["Артикул", "Глубина, мм", "Ix, см<super>4</super>", "Wx, см<super>3</super>",
            "Iy, см<super>4</super>", "Wy, см<super>3</super>", "A, см<super>2</super>"]
    row = [p.get("art", "—"), g(p.get("depth", 0)), n(p.get("ix", 0)), n(p.get("wx", 0)),
           n(p.get("iy", 0)), n(p.get("wy", 0)) if p.get("wy") else "—",
           n(p.get("area", 0), 3) if p.get("area") else "—"]
    return TableBlock(title, head, [row], note="")


def _ze_case(h, b):
    if h <= b:
        return "а) h ≤ b: z<sub>e</sub> = h"
    if h > 2 * b:
        return "в) h > 2b: z<sub>e</sub> определяется по высоте расположения верха конструкции"
    return "б) b < h ≤ 2b: z<sub>e</sub> = b или h в зависимости от высоты расположения конструкции"



def _composite_blocks(prof, role):
    """Раздел отчёта про усилитель и составное сечение (если профиль выбран с усилителем)."""
    if not prof.get("amp"):
        return []
    amp, base, comp = prof["amp"], prof["base"], prof["comp"]
    art_b = str(prof.get("art", "")).split(" + ")[0]
    n_ = comp["n"]
    steel = abs(n_ - 1.0) > 1e-9
    pos = amp.get("pos", "") or "—"
    mode_txt = ("рассчитаны по геометрии сечения" if amp.get("mode") == "geom"
                else "приняты по данным производителя (введены вручную)")
    items = [
        SubTitle(f"Составное сечение {role}: профиль + усилитель"),
        f"Профиль {art_b} усилен элементом «{amp['desc']}». Материал усилителя — {amp['material']}; "
        f"расположение — {pos}. Характеристики усилителя {mode_txt}. "
        "Итоговые характеристики составного сечения определены методом приведённого сечения: "
        "сечение усилителя приводится к алюминию умножением его характеристик на коэффициент приведения n; "
        "положение общего центра тяжести и момент инерции — по теореме Штейнера.",
    ]
    fmt_w = lambda v: n(v) if v else "—"
    items.append(TableBlock("Характеристики составного сечения",
        ["Элемент", "Ix, см<super>4</super>", "Iy, см<super>4</super>", "Wx, см<super>3</super>",
         "Wy, см<super>3</super>", "A, см<super>2</super>", "m, кг/м"],
        [[f"Профиль {art_b}", n(base["ix"]), n(base["iy"]), fmt_w(base["wx"]), fmt_w(base["wy"]), n(base["area"], 3), n(base["mass"], 3)],
         [f"Усилитель: {amp['desc']}", n(amp.get("ix", 0)), n(amp.get("iy", 0)), fmt_w(amp.get("wx")), fmt_w(amp.get("wy")),
          n(amp.get("area", 0), 3), n(amp["mass"], 3)],
         ["Составное сечение (приведённое к алюминию)", n(prof["ix"]), n(prof["iy"]), n(prof["wx"]), n(prof["wy"]),
          n(prof["area"], 3), n(prof["mass"], 3)]],
        col_widths_mm=[62, 18, 18, 18, 18, 18, 18],
        note=f"Масса профиля — {base['mass_src']}; масса усилителя m = A · ρ, ρ = {'7850' if steel else '2700'} кг/м³. "
             "Площадь A в строке «Составное сечение» — приведённая: A<sub>пр</sub> = A<sub>п</sub> + n · A<sub>у</sub>."))

    if amp.get("mode") == "geom":
        shp = str(amp.get("shape", "")).lower()
        bx, by, t_ = amp.get("bx", 0), amp.get("by", 0), amp.get("t", 0)
        if "труб" in shp:
            items.append(FormulaBlock(
                "A<sub>у</sub> = [b·h − (b − 2t)(h − 2t)] / 100<br/>I<sub>x,у</sub> = [b·h<super>3</super> − (b − 2t)(h − 2t)<super>3</super>] / 12 / 10<super>4</super><br/>"
                "I<sub>y,у</sub> = [h·b<super>3</super> − (h − 2t)(b − 2t)<super>3</super>] / 12 / 10<super>4</super>",
                where=[f"прямоугольная труба: b = {g(bx)} мм — ширина; h = {g(by)} мм — высота (в плоскости изгиба); t = {g(t_)} мм — толщина стенки"],
                ref="Справочные формулы геометрических характеристик сечений (сопротивление материалов)",
                subst=f"A<sub>у</sub> = {n(amp.get('area',0),3)} см²;   I<sub>x,у</sub> = {n(amp.get('ix',0))} см⁴;   I<sub>y,у</sub> = {n(amp.get('iy',0))} см⁴;   "
                      f"W<sub>x,у</sub> = I<sub>x,у</sub> / (h/20) = {n(amp.get('wx',0))} см³;   W<sub>y,у</sub> = I<sub>y,у</sub> / (b/20) = {n(amp.get('wy',0))} см³"))
        else:
            items.append(FormulaBlock(
                "A<sub>у</sub> = [b·t + 2·t·(h − t)] / 100;   I<sub>x,у</sub>, I<sub>y,у</sub> — по теореме Штейнера для полки и двух стенок относительно центра тяжести",
                where=[f"{amp.get('shape','швеллер')}: b = {g(bx)} мм — ширина; h = {g(by)} мм — высота; t = {g(t_)} мм — толщина"],
                ref="Справочные формулы геометрических характеристик сечений (сопротивление материалов)",
                subst=f"A<sub>у</sub> = {n(amp.get('area',0),3)} см²;   I<sub>x,у</sub> = {n(amp.get('ix',0))} см⁴;   I<sub>y,у</sub> = {n(amp.get('iy',0))} см⁴"))

    items.append(FormulaBlock(
        "n = E<sub>у</sub> / E<sub>ал</sub>;    A<sub>пр</sub> = A<sub>п</sub> + n · A<sub>у</sub>",
        where=["n — коэффициент приведения материала усилителя к алюминию (для стали принято n = 3: E = 210 000 МПа / 70 000 МПа; для алюминия n = 1)",
               "A<sub>п</sub>, A<sub>у</sub> — площади сечения профиля и усилителя"],
        ref="СП 128.13330.2016 (приведённое сечение при совместной работе материалов)",
        subst=(f"n = {g(comp['n'])}" + (" (сталь)" if steel else " (алюминий)") +
               f";   A<sub>пр</sub> = {n(base['area'],3)} + {g(comp['n'])} · {n(amp.get('area',0),3)} = <b>{n(comp['a_red'],3)} см²</b>")))

    if comp["steiner"]:
        br = comp["branch"]
        if br in ("thermal_offset", "outside_ay"):
            e_txt = ("e = a<sub>y,п</sub> + a<sub>y,у</sub>   (усилитель со стороны помещения)" if "помещен" in comp["pos"]
                     else "e = (h − a<sub>y,п</sub>) + a<sub>y,у</sub>   (усилитель со стороны улицы)")
            e_sub = (f"e = {n(amp['ay_main'],1)} + {n(amp['ay_rein'],1)}" if "помещен" in comp["pos"]
                     else f"e = ({n(base['depth'],1)} − {n(amp['ay_main'],1)}) + {n(amp['ay_rein'],1)}") + f" = {n(comp['dist_ct'],1)} мм"
            e_where = [f"a<sub>y,п</sub> = {n(amp['ay_main'],1)} мм, a<sub>y,у</sub> = {n(amp['ay_rein'],1)} мм — расстояния от нижней грани до центра тяжести профиля и усилителя",
                       f"h = {n(base['depth'],1)} мм — глубина профиля"]
        elif br == "inside_offset":
            e_txt = "e — расстояние между центрами тяжести профиля и усилителя (усилитель внутри камеры профиля)"
            e_sub = f"e = {n(comp['dist_ct'],1)} мм (задано расчётчиком)"
            e_where = []
        else:  # outside_geom
            e_txt = "e = h/2 + h<sub>у</sub>/2   (центры тяжести приняты в геометрических центрах)"
            e_sub = f"e = {n(base['depth'],1)}/2 + {n(amp.get('by',0),1)}/2 = {n(comp['dist_ct'],1)} мм"
            e_where = [f"h<sub>у</sub> = {n(amp.get('by',0),1)} мм — высота усилителя"]
        items.append(FormulaBlock(e_txt, where=e_where, subst=e_sub, numbered=False))
        items.append(FormulaBlock(
            "y<sub>c</sub> = n · A<sub>у</sub> · e / (A<sub>п</sub> + n · A<sub>у</sub>);   d<sub>1</sub> = y<sub>c</sub>;   d<sub>2</sub> = e − y<sub>c</sub>",
            where=["y<sub>c</sub> — положение общего центра тяжести приведённого сечения от центра тяжести профиля",
                   "d<sub>1</sub>, d<sub>2</sub> — расстояния от общего центра тяжести до центров тяжести профиля и усилителя"],
            ref="Сопротивление материалов, составные сечения",
            subst=f"y<sub>c</sub> = {g(comp['n'])} · {n(amp.get('area',0),3)} · {n(comp['dist_ct'],1)} / {n(comp['a_red'],3)} = {n(comp['y_c'],2)} мм;   "
                  f"d<sub>1</sub> = {n(comp['d1']*10,2)} мм;   d<sub>2</sub> = {n(comp['d2']*10,2)} мм"))
        items.append(FormulaBlock(
            "I<sub>x,пр</sub> = (I<sub>x,п</sub> + A<sub>п</sub> · d<sub>1</sub><super>2</super>) + n · (I<sub>x,у</sub> + A<sub>у</sub> · d<sub>2</sub><super>2</super>)",
            ref="Теорема Штейнера (сопротивление материалов)",
            subst=f"I<sub>x,пр</sub> = ({n(base['ix'])} + {n(base['area'],3)} · {n(comp['d1'],3)}<super>2</super>) + {g(comp['n'])} · "
                  f"({n(amp.get('ix',0))} + {n(amp.get('area',0),3)} · {n(comp['d2'],3)}<super>2</super>) = <b>{n(comp['ix_tot'])} см⁴</b>"))
        items.append(FormulaBlock(
            "W<sub>x,пр</sub> = I<sub>x,пр</sub> / c<sub>max</sub>,   c<sub>max</sub> = max(|y<sub>max</sub> − y<sub>c</sub>|; |y<sub>c</sub> − y<sub>min</sub>|)",
            where=["y<sub>min</sub>, y<sub>max</sub> — координаты крайних волокон составного сечения (от центра тяжести профиля)"],
            subst=f"y<sub>min</sub> = {n(comp['y_min'],1)} мм; y<sub>max</sub> = {n(comp['y_max'],1)} мм; c<sub>max</sub> = max({n(comp['c_top'],2)}; {n(comp['c_bot'],2)}) = {n(comp['c_max'],2)} см;   "
                  f"W<sub>x,пр</sub> = {n(comp['ix_tot'])} / {n(comp['c_max'],2)} = <b>{n(comp['wx_tot'])} см³</b>"))
    else:
        items.append(FormulaBlock(
            "I<sub>x,пр</sub> = I<sub>x,п</sub> + n · I<sub>x,у</sub>;    W<sub>x,пр</sub> = I<sub>x,пр</sub> / c<sub>max</sub>",
            where=["центры тяжести профиля и усилителя приняты совпадающими (усилитель внутри камеры профиля, смещение не задано)",
                   "c<sub>max</sub> — расстояние до наиболее удалённого волокна: max(a<sub>y</sub>; h − a<sub>y</sub>), при a<sub>y</sub> не задано — h/2"],
            subst=f"I<sub>x,пр</sub> = {n(base['ix'])} + {g(comp['n'])} · {n(amp.get('ix',0))} = <b>{n(comp['ix_tot'])} см⁴</b>;   "
                  f"c<sub>max</sub> = {n(comp['c_max'],2)} см;   W<sub>x,пр</sub> = <b>{n(comp['wx_tot'])} см³</b>"))
    items.append(FormulaBlock(
        "I<sub>y,пр</sub> = I<sub>y,п</sub> + n · I<sub>y,у</sub>;    W<sub>y,пр</sub> = I<sub>y,пр</sub> / c<sub>x</sub>",
        where=["c<sub>x</sub> — расстояние до наиболее удалённого волокна по ширине сечения (с учётом положения центров тяжести a<sub>x</sub>, если заданы)"],
        subst=f"I<sub>y,пр</sub> = {n(base['iy'])} + {g(comp['n'])} · {n(amp.get('iy',0))} = <b>{n(comp['iy_tot'])} см⁴</b>;   "
              f"c<sub>x</sub> = {n(comp['cx'],2)} см;   W<sub>y,пр</sub> = <b>{n(comp['wy_tot'])} см³</b>"))
    items.append("Примечание: характеристики составного сечения приведены к алюминию; напряжения в сечении определяются "
                 "в алюминиевом эквиваленте (напряжение в волокне стального усилителя равно эквивалентному, умноженному на n).")
    return items

def build_lora_report(c: dict) -> ReportData:
    """
    c — словарь, который собирает main.py (см. LoraApp._collect_report_ctx):
      data, wind, snow, k1k2, use_k, qG_norm, s_norm, gamma_f_wind, k_glass, gamma_snow,
      q_m_norm, q_m_calc, q_t_norm, q_t_calc, f6, weight_kg, r_calc, is_single, railing,
      strength_m, strength_t (calculations.mullion_strength / transom_strength),
      m_req, t_req, f_allow_weight, system, address, object_name, program_version,
      mullion, transom (профили: art, depth, ix, wx, iy, wy, area), schema_path
    """
    d, w, sn = c["data"], c["wind"], c["snow"]
    k1, k2 = c["k1k2"]["k1"], c["k1k2"]["k2"]
    m, t = c["mullion"], c["transom"]
    mr, tr = c["m_req"], c["t_req"]
    H, B1, B2, L1, L2 = d["h_m"], d["b1"], d["b2"], d["l1"], d["l2"]
    ang = d["angle_deg"]
    ca, sa = math.cos(math.radians(ang)), math.sin(math.radians(ang))
    R, gn = c["r_calc"], d["safety_yn"]
    Rd = R / gn if gn else R
    single = c["is_single"]
    railing = c["railing"]
    qG, s_n = c["qG_norm"], sn["s_norm"]
    gf_w, k_gl, gf_s = c["gamma_f_wind"], c["k_glass"], c["gamma_snow"]
    zone = "краевая (отсос)" if d["is_edge"] else "центральная (давление)"
    checks = []   # (описание, ok)

    rep = ReportData(title="Расчёт элементов стоечно-ригельной системы светопрозрачных конструкций",
                     object_name=c.get("object_name", ""), program="Lora",
                     program_version=c.get("program_version", ""))

    # ------------------------------------------------------------------ 1
    s1 = Section("Исходные данные", [])
    s1.items += [
        "Расчёт выполнен на основании нормативной документации:",
        ListBlock(["- СП 128.13330.2016 «Алюминиевые конструкции» (СНиП 2.03.06-85);",
                   "- СП 20.13330.2016 «Нагрузки и воздействия» (СНиП 2.01.07-85*)."]),
        "<b>Исходными данными для расчёта</b> светопрозрачных конструкций являются:",
        ListBlock([
            f"- адрес объекта — {c.get('address') or 'не указан'};",
            f"- ветровой район — {d['wind_region']} (нормативное значение ветрового давления w<sub>0</sub> = {n(w['w0'])} кПа);",
            f"- снеговой район — {d['snow_region']} (вес снегового покрова s<sub>0</sub> = {n(sn['s0'])} кПа);",
            f"- тип местности — {d['terrain_type']};",
            f"- коэффициент надёжности по ветровой нагрузке γ<sub>f</sub> = {g(gf_w)};",
            f"- коэффициент надёжности по ответственности здания γ<sub>n</sub> = {g(gn)};",
            f"- высота здания — {g(d['h_building'])} м; ширина здания (рассматриваемый фасад) — {g(d['w_building'])} м;",
            f"- высота расположения верха конструкции — {g(d['h_top'])} м;",
            f"- эквивалентная высота для элемента z<sub>e</sub> = {g(w['ze'])} м (п. 11.1.5 СП 20.13330.2016);",
            f"- зона фасада — {zone};",
            f"- угол наклона витража к горизонту — {g(ang)}°;",
            f"- расчётная схема стойки — {'однопролётная' if single else 'двухпролётная'} балка; перильная нагрузка — {'учитывается' if railing else 'отсутствует'};",
            f"- рассчитываемая профильная система — {c.get('system', '—')};",
            f"- материал профилей — алюминиевый сплав: {d['alum_mark']}; расчётное сопротивление R = {g(R)} МПа; модуль упругости E = {g(E_MOD)} МПа;",
            f"- суммарная толщина стеклопакета — {g(d['t_glass'])} мм.",
        ]),
        TableBlock("Геометрия фрагмента фасада", ["Обозначение", "Описание", "Значение, мм"], [
            ["H", "длина стойки (расстояние между точками крепления)", g(H)],
            ["B1", "ширина грузовой площади стойки", g(B1)],
            ["B2", "длина ригеля (расстояние между стойками)", g(B2)],
            ["L1", "высота стеклопакета над ригелем", g(L1)],
            ["L2", "высота стеклопакета под ригелем", g(L2)],
            ["a", "расположение подкладки стеклопакета от стойки", g(d["a_pad"])],
        ], col_widths_mm=[25, 110, 35]),
    ]
    if c.get("schema_path") and os.path.exists(c["schema_path"]):
        s1.items.append(ImageBlock(c["schema_path"], "Схема витража (обозначения размеров)", width_mm=150))
    rep.sections.append(s1)

    # ------------------------------------------------------------------ 2
    alpha, k10, z10 = TERRAIN.get(d["terrain_type"], TERRAIN["B"])
    A_m = B1 * H / 1e6
    s2 = Section("Нормативная пиковая ветровая нагрузка", [
        "Для элементов ограждения и узлов их крепления учитываются пиковые положительные W<sub>+</sub> и "
        "отрицательные W<sub>−</sub> воздействия ветровой нагрузки. Нормативное значение определяется по формуле:",
        FormulaBlock("W = w<sub>0</sub> · k(z<sub>e</sub>) · [1 + ζ(z<sub>e</sub>)] · |c<sub>p</sub>| · ν",
                     where=[f"w<sub>0</sub> = {n(w['w0'])} кПа — нормативное значение ветрового давления (п. 11.1.4 СП 20.13330.2016)",
                            f"k(z<sub>e</sub>) = {n(w['k_ze'])} — коэффициент, учитывающий изменение ветрового давления (п. 11.1.6)",
                            f"ζ(z<sub>e</sub>) = {n(w['zeta_ze'])} — коэффициент пульсации давления ветра (п. 11.1.8)",
                            f"c<sub>p</sub> = {n(abs(w['cp']))} — аэродинамический коэффициент, {zone} (прил. В СП 20.13330.2016)",
                            f"ν = {n(w['v'])} — коэффициент пространственной корреляции пульсаций при площади A = {n(A_m)} м² (табл. 11.8)"],
                     ref="СП 20.13330.2016, п. 11.1.1, формула (11.1)",
                     subst=f"W = {n(w['w0'])} · {n(w['k_ze'])} · (1 + {n(w['zeta_ze'])}) · {n(abs(w['cp']))} · {n(w['v'])} = <b>{n(w['w_peak'], 3)} кПа</b>"),
        SubTitle("Эквивалентная высота и коэффициенты"),
        f"Эквивалентная высота: {_ze_case(d['h_building'], d['w_building'])}; принято z<sub>e</sub> = {g(w['ze'])} м (п. 11.1.5).",
        FormulaBlock("k(z<sub>e</sub>) = k<sub>10</sub> · (z<sub>e</sub>/10)<super>2α</super>;   ζ(z<sub>e</sub>) = ζ<sub>10</sub> · (z<sub>e</sub>/10)<super>−α</super>",
                     where=[f"тип местности {d['terrain_type']}: α = {n(alpha)}, k<sub>10</sub> = {n(k10)}, ζ<sub>10</sub> = {n(z10)} (табл. 11.3 СП 20.13330.2016)"],
                     ref="СП 20.13330.2016, п. 11.1.6, п. 11.1.8",
                     subst=f"k = {n(k10)} · ({g(w['ze'])}/10)<super>{n(2*alpha)}</super> = {n(w['k_ze'])};   ζ = {n(z10)} · ({g(w['ze'])}/10)<super>−{n(alpha)}</super> = {n(w['zeta_ze'])}"),
    ])
    rep.sections.append(s2)

    # ------------------------------------------------------------------ 3
    mu_txt = ("μ = 1 при α &lt; 30°; μ = 1 − (α − 30°)/30° при 30° ≤ α ≤ 60°; μ = 0 при α &gt; 60°")
    s3 = Section("Снеговая нагрузка, вес заполнения, нагрузки на стойку и ригель", [
        FormulaBlock("s = s<sub>0</sub> · μ",
                     where=[f"s<sub>0</sub> = {n(sn['s0'])} кПа — вес снегового покрова на 1 м² горизонтальной поверхности земли",
                            f"μ = {n(sn['mu'])} — коэффициент перехода от веса снегового покрова земли к снеговой нагрузке на покрытие ({mu_txt}; прил. Б СП 20.13330.2016)"],
                     ref="СП 20.13330.2016, п. 10.1",
                     subst=f"s = {n(sn['s0'])} · {n(sn['mu'])} = <b>{n(s_n, 3)} кПа</b>"),
        FormulaBlock("q<sub>G</sub> = t · γ<sub>ст</sub>",
                     where=[f"t = {g(d['t_glass'])} мм — суммарная толщина стёкол стеклопакета",
                            "γ<sub>ст</sub> = 25 кН/м³ (плотность стекла 2500 кг/м³) — удельный вес стекла"],
                     ref="СП 20.13330.2016, п. 5.1 (собственный вес конструкций)",
                     subst=f"q<sub>G</sub> = {g(d['t_glass'])} · 0,025 = <b>{n(qG, 3)} кН/м²</b>"),
        f"Вес стеклопакета над ригелем (с учётом зазоров по периметру 12 мм): P = {n(c['weight_kg'], 1)} кг.",
        SubTitle("Линейные нагрузки"),
        "Нагрузки собираются с грузовой площади элемента. Нормативная (для расчёта прогибов) и расчётная "
        "(для расчёта прочности) погонные нагрузки определяются по формулам:",
        FormulaBlock("q<sub>н</sub> = (q<sub>G</sub>·cosα + s·cos<super>2</super>α + W) · b",
                     subst=f"q<sub>н,ст</sub> = ({n(qG,3)}·{n(ca,3)} + {n(s_n,3)}·{n(ca*ca,3)} + {n(w['w_peak'],3)}) · {g(B1)}/1000 = <b>{n(c['q_m_norm'],3)} кН/м</b>"),
        FormulaBlock("q<sub>р</sub> = (q<sub>G</sub>·cosα·γ<sub>f,ст</sub> + s·cos<super>2</super>α·γ<sub>f,s</sub> + W·γ<sub>f,w</sub>) · b",
                     where=[f"b — ширина грузовой площади (для стойки b = B1 = {g(B1)} мм; для ригеля b = {n(c['f6'],1)} мм — "
                            "сумма полуширин по грузовым площадям над и под ригелем)",
                            f"γ<sub>f,ст</sub> = {g(k_gl)}; γ<sub>f,s</sub> = {g(gf_s)}; γ<sub>f,w</sub> = {g(gf_w)} — коэффициенты надёжности по нагрузке (разд. 7, 10, 11 СП 20.13330.2016)"],
                     ref="СП 20.13330.2016, п. 6.2 (сочетания нагрузок)",
                     subst=f"q<sub>р,ст</sub> = ({n(qG,3)}·{n(ca,3)}·{g(k_gl)} + {n(s_n,3)}·{n(ca*ca,3)}·{g(gf_s)} + {n(w['w_peak'],3)}·{g(gf_w)}) · {g(B1)}/1000 = <b>{n(c['q_m_calc'],3)} кН/м</b>"),
        TableBlock("Нагрузки на элементы", ["Элемент", "q<sub>н</sub>, кН/м", "q<sub>р</sub>, кН/м"], [
            ["Стойка", n(c["q_m_norm"], 3), n(c["q_m_calc"], 3)],
            ["Ригель", n(c["q_t_norm"], 3), n(c["q_t_calc"], 3)]], col_widths_mm=[60, 55, 55]),
    ]) 
    if railing:
        s3.items.append(f"Перильная нагрузка: нормативное линейное значение 0,8 кН/м, γ<sub>f</sub> = 1,2 (п. 8.2.4 СП 20.13330.2016); "
                        "учитывается в расчёте однопролётной стойки и ригеля.")
    rep.sections.append(s3)

    # ------------------------------------------------------------------ 4  СТОЙКА
    Ix, Wx, Iy, A = m.get("ix", 0), m.get("wx", 0), m.get("iy"), m.get("area")
    sm = c["strength_m"]
    s4 = Section("Расчёт фасадной стойки", [
        f"В качестве стойки принимается профиль {m.get('art', '—')} со следующими характеристиками:",
        _profile_table("Характеристики стойки", m, "стойка"),
        SubTitle("4.1 Расчёт на прочность"),
        "Расчёт на прочность сплошностенчатых сжато-изгибаемых элементов выполняется по формуле:",
        FormulaBlock("(N / A + M<sub>x</sub> / W<sub>x</sub>) / (R<sub>yn</sub> · γ<sub>c</sub>) ≤ 1",
                     where=["N — сила сжатия, снятая с грузовой площади: N = m<sub>с</sub> + m<sub>з</sub>",
                            "m<sub>с</sub> — собственный вес стойки; m<sub>з</sub> — вес заполнения (стеклопакета) с грузовой площади",
                            "A — площадь сечения профиля; W<sub>x</sub> — момент сопротивления профиля",
                            "M<sub>x</sub> — максимальный изгибающий момент от ветровой, снеговой нагрузок и веса заполнения"
                            + (" и перильной нагрузки" if sm["m_rail"] else ""),
                            f"R<sub>yn</sub> = R / γ<sub>n</sub> = {g(R)} / {g(gn)} = {n(Rd)} МПа — расчётное сопротивление с учётом ответственности здания "
                            "(для сплава 6063 Т6 значение принимается по ГОСТ 22233, если оно не приведено в табл. 4 СП 128.13330.2016)",
                            f"γ<sub>c</sub> = {g(sm['gamma_c'])} — коэффициент условий работы (табл. 6 СП 128.13330.2016)"],
                     ref="СП 128.13330.2016, расчёт на прочность элементов при действии осевой силы и изгиба"),
        FormulaBlock("m<sub>с</sub> = m · H · g · γ<sub>f</sub> · sinα;      m<sub>з</sub> = q<sub>G</sub> · γ<sub>f,ст</sub> · A<sub>гр</sub> · sinα",
                     where=[f"m = {n(m.get('mass') or 0, 3)} кг/м — масса погонного метра профиля" + (f" (профиль {n(m['base']['mass'],3)} + усилитель {n(m['amp']['mass'],3)})" if m.get("amp") else "") + ("" if sm['mass_known'] else " (в таблице профилей не задана, принята 0)"),
                            f"H = {n(H/1000, 3)} м; g = 9,81 м/с²; γ<sub>f</sub> = 1,1 — коэффициент надёжности по нагрузке от собственного веса",
                            f"q<sub>G</sub> = {n(qG,3)} кН/м²; γ<sub>f,ст</sub> = {g(k_gl)}; A<sub>гр</sub> = B1 · H = {n(B1/1000,3)} · {n(H/1000,3)} = {n(sm['area_load'],3)} м² — грузовая площадь"],
                     subst=f"m<sub>с</sub> = {n(m.get('mass') or 0,3)} · {n(H/1000,3)} · 9,81 · 1,1 = {n(sm['n_self'],1)} Н;    "
                           f"m<sub>з</sub> = {n(qG,3)} · {g(k_gl)} · {n(sm['area_load'],3)} · 1000 = {n(sm['n_fill'],1)} Н;    "
                           f"N = {n(sm['n'],1)} Н"),
        FormulaBlock("M<sub>x</sub> = q<sub>р</sub> · H<super>2</super> / 8" + (" + Q<sub>пер</sub> · B1 · a<sub>x</sub> · (H − a<sub>x</sub>) · γ<sub>f</sub> / H" if sm["m_rail"] else ""),
                     where=[f"q<sub>р</sub> = {n(c['q_m_calc'],3)} кН/м — расчётная нагрузка на стойку от ветра, веса заполнения и снега", f"H = {g(H)} мм — длина стойки (расстояние между точками крепления)"]
                           + ([f"Q<sub>пер</sub> = 0,8 кН/м — нормативная перильная нагрузка; a<sub>x</sub> = {g(sm['ax'])} мм — плечо приложения; γ<sub>f</sub> = 1,2"] if sm["m_rail"] else [])
                           + (["для двухпролётной схемы в программе принят тот же расчётный момент q·H²/8"] if not single else []),
                     ref="Строительная механика, однопролётная шарнирно опёртая балка",
                     subst=f"M<sub>x</sub> = {n(c['q_m_calc'],3)} · {n(H/1000,3)}<super>2</super> / 8" + (f" + {n(sm['m_rail'],3)}" if sm["m_rail"] else "") + f" = {n(sm['m'],3)} кН·м = {n(sm['m']*1e6,0)} Н·мм"),
    ])
    s4.items[2:2] = _composite_blocks(m, "стойки")
    ok = sm["ok"]
    checks.append(("стойка — прочность", ok))
    s4.items.append(CheckBlock(
        f"({n(sm['n'],1)} / {n(A*100 if A else 0,1)} + {n(sm['m']*1e6,0)} / {n(Wx*1000,0)}) / ({n(Rd)} · {g(sm['gamma_c'])}) = {n(sm['util'],2)} "
        f"{'≤' if ok else '&gt;'} 1, условие {'выполняется' if ok else 'НЕ выполняется'}", ok))

    # прогиб
    n_m = 1.0 / mr["k14"]
    f_wind = 5.0 * c["q_m_norm"] * ((H / 10.0) ** 4) / (384.0 * E_MOD * Ix) if single else (1.0 / 185.0) * c["q_m_norm"] * ((H / 10.0) ** 4) / E_MOD / Ix
    f_rail = 0.0
    if single and railing:
        g14 = min(1400.0, H / 2.0)
        f_rail = 0.8 * B1 * g14 * (3.0 * H ** 2 - 4.0 * g14 ** 2) * 0.9 / (48.0 * E_MOD * 10000.0 * Ix)
    f_fact = round(f_wind + f_rail, 1)
    ok = f_fact <= mr["f_allow"]
    checks.append(("стойка — прогиб", ok))
    s4.items += [
        SubTitle("4.2 Расчёт прогиба"),
        "Фактический прогиб стойки от нормативных нагрузок не должен превышать допустимого:",
        FormulaBlock("f<sub>факт</sub> = " + ("5 · q<sub>н</sub> · H<super>4</super> / (384 · E · I<sub>x</sub>)" if single
                                              else "q<sub>н</sub> · H<super>4</super> / (185 · E · I<sub>x</sub>)") + " ≤ f<sub>доп</sub> = H / n",
                     where=[f"q<sub>н</sub> = {n(c['q_m_norm'],3)} кН/м — нормативная нагрузка на стойку",
                            f"E = {g(E_MOD)} МПа; I<sub>x</sub> = {n(Ix)} см<super>4</super>",
                            f"f<sub>доп</sub> = H/{g(n_m)} = {n(mr['f_allow'],1)} мм — допустимый прогиб стойки"],
                     ref="СП 20.13330.2016, прил. Е (предельные прогибы); балочные формулы — строительная механика",
                     subst=(f"f = 5 · {n(c['q_m_norm'],3)} · {g(H)}<super>4</super> / (384 · {g(E_MOD)} · {n(Ix)} · 10<super>4</super>) = {n(f_wind,1)} мм" if single
                            else f"f = {n(c['q_m_norm'],3)} · {g(H)}<super>4</super> / (185 · {g(E_MOD)} · {n(Ix)} · 10<super>4</super>) = {n(f_wind,1)} мм")),
    ]
    if f_rail:
        s4.items.append(f"Добавка от перильной нагрузки: f<sub>пер</sub> = {n(f_rail,1)} мм.")
    s4.items.append(CheckBlock(f"f<sub>факт</sub> = {n(f_fact,1)} мм {'≤' if ok else '&gt;'} f<sub>доп</sub> = {n(mr['f_allow'],1)} мм: условие {'выполняется' if ok else 'НЕ выполняется'}", ok))
    k_edge = mr.get("k_edge", 1.0)
    ix_base = (5.0 if single else 1.0) * n_m * c["q_m_norm"] * H ** 3 / ((384.0 if single else 185.0) * E_MOD * 1e4)
    s4.items += [
        FormulaBlock("I<sub>x,тр</sub> = " + ("5" if single else "") + " · n · q<sub>н</sub> · H<super>3</super> / (" + ("384" if single else "185") + " · E)"
                     + (" · k<sub>1</sub> · k<sub>2</sub>" if k_edge != 1.0 else ""),
                     where=[f"n = {g(n_m)} — отношение H / f<sub>доп</sub> (допустимый прогиб стойки H/{g(n_m)})"]
                           + ([f"k<sub>1</sub> = {n(k1)}, k<sub>2</sub> = {n(k2,4)} — коэффициенты прогиба кромки стеклопакета, зависят от высоты стеклопакета "
                               f"L = {g(max(L1, L2))} мм и отношения L/H = {n(max(L1, L2)/H,2)} (методика производителя профильной системы); "
                               "на нагрузки не умножаются, а увеличивают только требуемый момент инерции стойки"]
                              if k_edge != 1.0 else ["коэффициенты k<sub>1</sub>, k<sub>2</sub> в расчёте не учитываются (отключены в настройках)"]),
                     ref="Методика производителя профильной системы (расчёт прогиба кромки стеклопакета)",
                     subst=f"I<sub>x,тр</sub> = {n(ix_base)}" + (f" · {n(k1)} · {n(k2,4)}" if k_edge != 1.0 else "") + f" = <b>{n(mr['ix_req'])} см⁴</b>"),
    ]
    ok_ix = Ix >= mr["ix_req"]
    checks.append(("стойка — требуемый момент инерции", ok_ix))
    s4.items.append(CheckBlock(f"I<sub>x</sub> = {n(Ix)} см⁴ {'≥' if ok_ix else '&lt;'} I<sub>x,тр</sub> = {n(mr['ix_req'])} см⁴: условие {'выполняется' if ok_ix else 'НЕ выполняется'}", ok_ix))

    # гибкость
    if A and A > 0 and Ix > 0:
        ix_r = math.sqrt(Ix / A)
        lx = (H / 10.0 * MU_X) / ix_r
        lim_x = 70.0 if d["is_edge"] else 100.0
        okx = lx < lim_x
        checks.append(("стойка — гибкость из плоскости витража", okx))
        s4.items += [
            SubTitle("4.3 Расчёт гибкости"),
            FormulaBlock("λ<sub>x</sub> = μ<sub>x</sub> · H / i<sub>x</sub>,    i<sub>x</sub> = √(I<sub>x</sub> / A)",
                         where=[f"μ<sub>x</sub> = {n(MU_X,3)} — коэффициент расчётной длины стойки (табл. 31 СП 128.13330.2016)",
                                f"i<sub>x</sub> = √({n(Ix)} / {n(A,3)}) = {n(ix_r)} см — радиус инерции",
                                f"[λ] = {int(lim_x)} — предельная гибкость, принятая в программе ({'краевая' if d['is_edge'] else 'центральная'} зона фасада; СП 128.13330.2016, раздел «Предельные гибкости»)"],
                         ref="СП 128.13330.2016, п. 10.4, табл. 31, табл. 34",
                         subst=f"λ<sub>x</sub> = ({n(H/10,1)} · {n(MU_X,3)}) / {n(ix_r)} = <b>{n(lx,1)}</b>"),
            CheckBlock(f"λ<sub>x</sub> = {n(lx,1)} {'&lt;' if okx else '≥'} [λ] = {int(lim_x)}: условие {'выполняется' if okx else 'НЕ выполняется'}", okx),
        ]
        if Iy and Iy > 0:
            iy_r = math.sqrt(Iy / A)
            Lmax = max(L1, L2)
            ly = (Lmax / 10.0 * MU_Y) / iy_r
            oky = ly < 100.0
            checks.append(("стойка — гибкость в плоскости витража", oky))
            s4.items += [
                FormulaBlock("λ<sub>y</sub> = μ<sub>y</sub> · L<sub>max</sub> / i<sub>y</sub>,    i<sub>y</sub> = √(I<sub>y</sub> / A)",
                             where=[f"μ<sub>y</sub> = {n(MU_Y,3)} (табл. 31 СП 128.13330.2016); L<sub>max</sub> = {g(Lmax)} мм — наибольшее расстояние между ригелями",
                                    f"i<sub>y</sub> = √({n(Iy)} / {n(A,3)}) = {n(iy_r)} см; [λ] = 100"],
                             ref="СП 128.13330.2016, табл. 31, табл. 34",
                             subst=f"λ<sub>y</sub> = ({n(Lmax/10,1)} · {n(MU_Y,3)}) / {n(iy_r)} = <b>{n(ly,1)}</b>"),
                CheckBlock(f"λ<sub>y</sub> = {n(ly,1)} {'&lt;' if oky else '≥'} 100: условие {'выполняется' if oky else 'НЕ выполняется'}", oky),
            ]
    # кромка
    if f_fact > 0:
        Lmax = max(L1, L2)
        Rr = (H ** 2 + 4.0 * f_fact ** 2) / (8.0 * f_fact)
        term = 4.0 * Rr ** 2 - Lmax ** 2
        if term >= 0:
            f_edge = round(Rr - 0.5 * math.sqrt(term), 1)
            oke = f_edge < 8.0
            checks.append(("стойка — прогиб кромки стеклопакета", oke))
            s4.items += [
                SubTitle("4.4 Прогиб кромки стойки в пределах стеклопакета"),
                "Изогнутая ось стойки принимается дугой окружности радиуса r<sub>к</sub> = (H² + 4f²) / (8f); "
                "прогиб кромки на участке L<sub>max</sub> — стрела этой дуги:",
                FormulaBlock("f<sub>кр</sub> = r<sub>к</sub> − 0,5 · √(4r<sub>к</sub><super>2</super> − L<sub>max</sub><super>2</super>) &lt; 8 мм",
                             subst=f"r<sub>к</sub> = {n(Rr,0)} мм;  f<sub>кр</sub> = <b>{n(f_edge,1)} мм</b>",
                             ref="Ограничение 8 мм принято в программе (требования к стеклопакетам / системе)"),
                CheckBlock(f"f<sub>кр</sub> = {n(f_edge,1)} мм {'&lt;' if oke else '≥'} 8 мм: условие {'выполняется' if oke else 'НЕ выполняется'}", oke),
            ]
    rep.sections.append(s4)

    # ------------------------------------------------------------------ 5  РИГЕЛЬ
    tIx, tIy, tWx = t.get("ix", 0), t.get("iy", 0), t.get("wx", 0)
    q_tot = (c["q_t_norm"] / c["f6"]) * 1000.0 if c["f6"] else 0.0      # кН/м² (qG cosα + s cos²α + W)
    q_ref = 0.02 if B2 < 2000.0 else (0.04 if B2 < 2700.0 else 0.065)  # собств. вес ригеля, кН/м

    def side(li):
        if B2 < li:
            return q_tot * B2 * 0.5 / 1000.0 * ((B2 / 10.0) ** 4) / (120.0 * E_MOD * tIx)
        return q_tot * li * 0.5 / 1000.0 * ((5.0 * (B2 / 10.0) ** 2 - 4.0 * (li * 0.5 / 10.0) ** 2) ** 2) / (1920.0 * E_MOD * tIx)

    fx1, fx2 = side(L1), side(L2)
    fx3 = 5.0 * (q_ref * ca) * (B2 / 10.0) ** 4 / (384.0 * E_MOD * tIx)
    fx4 = 0.0
    if single and railing and tr["f_allow_wind"] > 0:
        fx4 = round(5.0 * 0.8 * B2 ** 4 * 0.9 / (384.0 * E_MOD * 10000.0 * tIx), 2)
    f_fx = round(fx1 + fx2 + fx3, 2) + fx4
    a_p = d["a_pad"]
    term1 = ((a_p / 1000.0) * sa * qG * ((B2 - 25.0) / 1000.0) * ((L1 - 25.0) / 1000.0) / 2.0) \
        * (3.0 * (B2 / 1000.0) ** 2 - 4.0 * (a_p / 1000.0) ** 2) / (24.0 * E_MOD * 1000.0 * tIy * 0.001) * 1e8
    term2 = 5.0 * ((q_ref * sa) * (B2 / 10.0) ** 4) / (384.0 * E_MOD * tIy)
    f_fy = round(term1 + term2, 2)
    Fglass = sa * qG * ((B2 - 25.0) / 1000.0) * ((L1 - 25.0) / 1000.0) / 2.0

    st_ = c["strength_t"]
    s5 = Section("Расчёт фасадного ригеля", [
        f"В качестве ригеля принимается профиль {t.get('art', '—')} со следующими характеристиками:",
        _profile_table("Характеристики ригеля", t, "ригель"),
        SubTitle("5.1 Расчёт на прочность"),
        "Расчёт на прочность ригеля от ветровой нагрузки, собственного веса, снега и веса заполнения (изгиб в двух плоскостях) "
        "выполняется по формуле:",
        FormulaBlock("(M<sub>x</sub> / W<sub>x</sub> + M<sub>y</sub> / W<sub>y</sub>) / (R<sub>yn</sub> · γ<sub>c</sub>) ≤ 1",
                     where=["M<sub>x</sub> — максимальный изгибающий момент перпендикулярно фасаду (от ветра, снега и стекла) — сумма по грузовым площадям над и под ригелем"
                            + (" и перильной нагрузки" if st_["mx_rail"] else ""),
                            "M<sub>y</sub> — максимальный изгибающий момент в плоскости фасада: M<sub>y</sub> = F · a + q<sub>св,y</sub> · B2<super>2</super> / 8",
                            f"F = {n(st_['f_calc'],3)} кН — расчётная нагрузка на одну подкладку (от половины веса заполнения над ригелем: F = q<sub>G</sub> · sinα · (B2−25)(L1−25)/2 · γ<sub>f,ст</sub>)",
                            f"a = {g(st_['a'])} мм — расстояние от подкладки стеклопакета до стойки",
                            f"q<sub>св,y</sub> = {n(st_['q_self_y']*1000,1)} Н/м — проекция собственного веса ригеля на плоскость фасада (γ<sub>f</sub> = 1,1)"
                            + ("" if st_["mass_known"] else "; масса профиля в таблице не задана, погонный вес принят приближённо"),
                            f"W<sub>x</sub> = {n(tWx)} см³; W<sub>y</sub> = {n(t.get('wy') or 0)} см³ — моменты сопротивления профиля",
                            f"R<sub>yn</sub> = R / γ<sub>n</sub> = {g(R)} / {g(gn)} = {n(Rd)} МПа; γ<sub>c</sub> = {g(st_['gamma_c'])} (табл. 6 СП 128.13330.2016)"],
                     ref="СП 128.13330.2016, расчёт на прочность изгибаемых элементов",
                     subst=f"M<sub>x</sub> = {n(st_['mx']*1e6,0)} Н·мм;    M<sub>y</sub> = {n(st_['m_f']*1e6,0)} + {n(st_['m_q']*1e6,0)} = {n(st_['my']*1e6,0)} Н·мм"),
    ])
    s5.items[2:2] = _composite_blocks(t, "ригеля")
    ok = st_["ok"]
    checks.append(("ригель — прочность", ok))
    s5.items.append(CheckBlock(
        f"({n(st_['mx']*1e6,0)} / {n(tWx*1000,0)} + {n(st_['my']*1e6,0)} / {n((t.get('wy') or 0)*1000,0)}) / ({n(Rd)} · {g(st_['gamma_c'])}) = {n(st_['util'],2)} "
        f"{'≤' if ok else '&gt;'} 1, условие {'выполняется' if ok else 'НЕ выполняется'}", ok))
    okx = f_fx <= tr["f_allow_wind"]
    checks.append(("ригель — прогиб из плоскости фасада", okx))
    s5.items += [
        SubTitle("5.2 Прогиб из плоскости фасада"),
        FormulaBlock("f<sub>x</sub> = f<sub>x1</sub> + f<sub>x2</sub> + f<sub>x3</sub> + f<sub>x4</sub> ≤ f<sub>x,доп</sub> = min(B2/200; 8 мм)",
                     where=["f<sub>x1</sub>, f<sub>x2</sub> — прогиб от распределённой нагрузки над и под ригелем (стекло, снег, ветер):",
                            "трапециевидная эпюра (B2 ≥ L<sub>i</sub>): f = q · (L<sub>i</sub>/2) · (5B2² − 4(L<sub>i</sub>/2)²)² / (1920 · E · I<sub>x</sub>)",
                            "треугольная эпюра (B2 &lt; L<sub>i</sub>): f = q · (B2/2) · B2<super>4</super> / (120 · E · I<sub>x</sub>)",
                            f"f<sub>x3</sub> = 5 · q<sub>св</sub> · cosα · B2<super>4</super> / (384 · E · I<sub>x</sub>) — собственный вес ригеля (q<sub>св</sub> = {n(q_ref*1000,0)} Н/м, принят по длине ригеля)",
                            "f<sub>x4</sub> — прогиб от перильной нагрузки (только однопролётная схема с перильной нагрузкой)",
                            f"q = {n(q_tot,3)} кН/м² = q<sub>G</sub>·cosα + s·cos²α + W; E = {g(E_MOD)} МПа; I<sub>x</sub> = {n(tIx)} см⁴"],
                     ref="СП 20.13330.2016, прил. Е; балочные формулы — строительная механика",
                     subst=f"f<sub>x1</sub> = {n(fx1,2)} мм;  f<sub>x2</sub> = {n(fx2,2)} мм;  f<sub>x3</sub> = {n(fx3,2)} мм;  f<sub>x4</sub> = {n(fx4,2)} мм;  f<sub>x</sub> = <b>{n(f_fx,2)} мм</b>"),
        CheckBlock(f"f<sub>x</sub> = {n(f_fx,2)} мм {'≤' if okx else '&gt;'} f<sub>x,доп</sub> = {n(tr['f_allow_wind'],2)} мм: условие {'выполняется' if okx else 'НЕ выполняется'}", okx),
    ]
    oky = f_fy <= tr["f_allow_weight"]
    checks.append(("ригель — прогиб в плоскости фасада", oky))
    s5.items += [
        SubTitle("5.3 Прогиб в плоскости фасада (от веса заполнения)"),
        FormulaBlock("f<sub>y</sub> = f<sub>y1</sub> + f<sub>y2</sub> ≤ f<sub>y,доп</sub>",
                     where=[f"f<sub>y1</sub> = F · a · (3·B2² − 4·a²) / (24 · E · I<sub>y</sub>) — прогиб от веса стеклопакета, опирающегося на подкладку на расстоянии a = {g(a_p)} мм от стойки; "
                            f"F = q<sub>G</sub> · sinα · (B2−25)(L1−25)/2 = {n(Fglass,3)} кН — доля веса заполнения над ригелем, приходящаяся на ригель",
                            f"f<sub>y2</sub> = 5 · q<sub>св</sub> · sinα · B2<super>4</super> / (384 · E · I<sub>y</sub>) — собственный вес ригеля в плоскости фасада",
                            f"I<sub>y</sub> = {n(tIy)} см⁴; f<sub>y,доп</sub> = {n(tr['f_allow_weight'],2)} мм — наименьшее из B2/{g(d['transom_deflection_ratio'])} и предельного значения, заданного в программе"],
                     ref="СП 20.13330.2016, прил. Е",
                     subst=f"f<sub>y</sub> = <b>{n(f_fy,2)} мм</b>"),
        CheckBlock(f"f<sub>y</sub> = {n(f_fy,2)} мм {'≤' if oky else '&gt;'} f<sub>y,доп</sub> = {n(tr['f_allow_weight'],2)} мм: условие {'выполняется' if oky else 'НЕ выполняется'}", oky),
    ]
    rep.sections.append(s5)

    # ------------------------------------------------------------------ 6 Вывод
    all_ok = all(ok for _, ok in checks)
    rows = [[desc.replace("<sub>", "").replace("</sub>", ""), "выполняется" if ok else "НЕ выполняется"] for desc, ok in checks]
    rows = [[__import__("re").sub(r"</?(sub|super)>", "", r[0]), r[1]] for r in rows]
    rep.conclusion = [
        "Сводка проверок:",
    ]
    rep.sections.append(Section("Сводка проверок", [
        TableBlock("Результаты проверок", ["Проверка", "Результат"], rows, col_widths_mm=[120, 50])]))
    if all_ok:
        rep.conclusion = [
            f"Принятый в качестве стойки профиль {m.get('art', '—')} обеспечивает необходимые прочность, гибкость и жёсткость "
            "(в соответствии с расчётами по 1-й и 2-й группам предельных состояний).",
            f"Принятый в качестве ригеля профиль {t.get('art', '—')} обеспечивает необходимые прочность и жёсткость "
            "(в соответствии с расчётами по 1-й и 2-й группам предельных состояний).",
        ]
    else:
        bad = "; ".join(__import__("re").sub(r"</?(sub|super)>", "", desc) for desc, ok in checks if not ok)
        rep.conclusion = [f"<font color='#c62828'><b>Принятые профили не удовлетворяют требованиям расчёта:</b></font> {bad}. "
                          "Необходимо выбрать профиль большего сечения или усилить его."]
    rep.references = REFERENCES
    return rep
