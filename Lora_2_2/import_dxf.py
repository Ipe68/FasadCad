import os
import math
import sys
from PyQt5 import QtWidgets, uic, QtCore, QtGui
from PyQt5.QtWidgets import QGraphicsScene, QGraphicsPolygonItem, QGraphicsPathItem
from PyQt5.QtGui import QPen, QBrush, QColor, QPolygonF, QPainterPath
from PyQt5.QtCore import QPointF


def resource_path(*parts):
    """Абсолютный путь к ресурсу для запуска из исходников и из PyInstaller."""
    if getattr(sys, 'frozen', False):
        base_path = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, *parts)


def fix_ui_file(ui_path):
    """Исправляет несовместимые с PyQt5 перечисления Qt6 в .ui файле"""
    import re
    if not os.path.exists(ui_path):
        return
    with open(ui_path, 'r', encoding='utf-8') as f:
        content = f.read()

    def clean_value(val):
        return val.split('::')[-1]

    def process_tag(match):
        tag = match.group(1)
        content = match.group(2)
        if not content.strip():
            return f'<{tag}>{content}</{tag}>'
        items = content.split('|')
        cleaned_items = []
        for item in items:
            item = item.strip()
            if not item:
                continue
            if 'Qt::' in item:
                cleaned_items.append(f"Qt::{clean_value(item)}")
            elif clean_value(item) in ['Ok', 'Cancel', 'Save', 'Close', 'Discard', 'Apply',
                                        'Reset', 'RestoreDefaults', 'Help', 'NoButton',
                                        'Yes', 'No', 'Abort', 'Retry', 'Ignore']:
                cleaned_items.append(f"QDialogButtonBox::{clean_value(item)}")
            else:
                cleaned_items.append(clean_value(item))
        return f'<{tag}>{"|".join(cleaned_items)}</{tag}>'

    new_content = re.sub(r'<(set|enum)>([^<]*)</\1>', process_tag, content)
    if new_content != content:
        with open(ui_path, 'w', encoding='utf-8') as f:
            f.write(new_content)


def _get_block_names(dxf_path):
    """Возвращает список имён блоков из DXF файла (только вставленные в ModelSpace)."""
    import ezdxf
    doc = ezdxf.readfile(dxf_path)
    names = []
    seen = set()
    for entity in doc.modelspace():
        if entity.dxftype() == 'INSERT':
            name = entity.dxf.name
            if name not in seen and name != "Ramka" and not name.startswith('*'):
                block = doc.blocks.get(name)
                if block:
                    names.append(name)
                    seen.add(name)
    # Если ничего не нашли, пробуем имя файла
    if not names:
        file_base = os.path.splitext(os.path.basename(dxf_path))[0]
        for block in doc.blocks:
            if block.name == file_base:
                names.append(block.name)
                break
    return names


def _calculate_block(dxf_path, block_name, kx=1.0, ky=1.0, use_k=False, doc=None):
    """Рассчитывает характеристики одного блока из DXF. Возвращает dict или None."""
    import ezdxf
    from ezdxf import path as ezdxf_path
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    if doc is None:
        doc = ezdxf.readfile(dxf_path)
    block = doc.blocks.get(block_name)
    if block is None:
        return None

    DENSITY = 0.0027  # кг/см³

    # Размеры из DIMENSION
    ly_value = None
    bx_value = None
    for entity in block:
        if entity.dxftype() == 'DIMENSION':
            measurement = entity.dxf.get('actual_measurement', None)
            if measurement is not None:
                try:
                    p2 = entity.dxf.defpoint2
                    p3 = entity.dxf.defpoint3
                    dx = abs(p3.x - p2.x)
                    dy = abs(p3.y - p2.y)
                    if dy > dx:
                        if ly_value is None:
                            ly_value = round(measurement, 3)
                    else:
                        if bx_value is None:
                            bx_value = round(measurement, 3)
                except AttributeError:
                    pass

    POLYAMIDE_LAYERS = {'polyamide', 'polyamid', 'poliamid', 'poliamide', 'полиамид', 'пластик'}
    REINFORCEMENT_LAYERS = {'усилитель', 'reinforcement'}

    polylines_metal = []
    polylines_plastic = []
    polylines_reinforcement = []
    for entity in block:
        if entity.dxftype() in ('LWPOLYLINE', 'POLYLINE'):
            p = ezdxf_path.make_path(entity)
            points = [(pt.x, pt.y) for pt in p.flattening(distance=0.01)]
            if len(points) >= 3:
                if points[0] != points[-1]:
                    points.append(points[0])
                poly = Polygon(points)
                layer = entity.dxf.layer.lower().strip()
                if layer in POLYAMIDE_LAYERS:
                    polylines_plastic.append(poly)
                elif layer in REINFORCEMENT_LAYERS:
                    polylines_reinforcement.append(poly)
                else:
                    polylines_metal.append(poly)

    if not polylines_metal and not polylines_plastic:
        return None

    # Площадь пластика
    plastic_area_mm2 = 0
    if polylines_plastic:
        pl = sorted(polylines_plastic, key=lambda p: p.area, reverse=True)
        pl_levels = []
        for i, pi in enumerate(pl):
            level = 0
            for j, pj in enumerate(pl):
                if i != j and pj.contains(pi) and pj.area > pi.area:
                    level += 1
            pl_levels.append(level)
        pl_children = [[] for _ in range(len(pl))]
        for i in range(len(pl)):
            best_parent = None
            best_area = float('inf')
            for j in range(len(pl)):
                if i != j and pl[j].contains(pl[i]) and pl[j].area > pl[i].area:
                    if pl_levels[j] == pl_levels[i] - 1 and pl[j].area < best_area:
                        best_parent = j
                        best_area = pl[j].area
            if best_parent is not None:
                pl_children[best_parent].append(i)
        pl_shapes = []
        for i, p in enumerate(pl):
            if pl_levels[i] % 2 == 0:
                pp = p if p.is_valid else p.buffer(0)
                for ch in pl_children[i]:
                    ch_p = pl[ch] if pl[ch].is_valid else pl[ch].buffer(0)
                    pp = pp.difference(ch_p)
                pl_shapes.append(pp)
        plastic_union = unary_union(pl_shapes)
        plastic_area_mm2 = plastic_union.area

    if not polylines_metal:
        return {
            'article': block_name,
            'jx': 0, 'wx': 0, 'jy': 0, 'wy': 0,
            'area_metal': 0, 'area_plastic': round(plastic_area_mm2 / 100, 3),
            'mass': 0, 'ax': 0, 'ay': 0,
            'ly': ly_value, 'bx': bx_value
        }

    polylines = polylines_metal
    polylines.sort(key=lambda p: p.area, reverse=True)

    # Определяем уровень вложенности каждой полилинии.
    # Чётный уровень (0, 2, ...) = материал (+), нечётный (1, 3, ...) = пустота (-).
    nesting_levels = []
    for i, poly_i in enumerate(polylines):
        level = 0
        for j, poly_j in enumerate(polylines):
            if i != j and poly_j.contains(poly_i) and poly_j.area > poly_i.area:
                level += 1
        nesting_levels.append(level)

    # Строим дерево: каждая полилиния вычитает своих непосредственных детей (level+1),
    # а те в свою очередь добавляют своих детей (level+2) и т.д.
    # Для корректной работы с Shapely: каждый контур чётного уровня — это Polygon
    # с дырками (holes) из непосредственных нечётных детей.
    # Собираем пары (poly_index, direct_children_indices).
    # Для каждой полилинии находим непосредственного родителя (ближайший содержащий с level-1)
    parent_of = [None] * len(polylines)
    children_of = [[] for _ in range(len(polylines))]
    for i, poly_i in enumerate(polylines):
        best_parent = None
        best_area = float('inf')
        for j, poly_j in enumerate(polylines):
            if i != j and poly_j.contains(poly_i) and poly_j.area > poly_i.area:
                if nesting_levels[j] == nesting_levels[i] - 1 and poly_j.area < best_area:
                    best_parent = j
                    best_area = poly_j.area
        parent_of[i] = best_parent
        if best_parent is not None:
            children_of[best_parent].append(i)

    # Собираем итоговую геометрию: для каждого контура чётного уровня без родителя
    # или являющегося корнем — создаём Polygon с дырками.
    shapes_to_combine = []
    for i, poly in enumerate(polylines):
        if nesting_levels[i] % 2 == 0:
            p = poly if poly.is_valid else poly.buffer(0)
            # Вычитаем непосредственных детей (нечётный уровень = дырки)
            for ch in children_of[i]:
                ch_p = polylines[ch] if polylines[ch].is_valid else polylines[ch].buffer(0)
                p = p.difference(ch_p)
            shapes_to_combine.append(p)

    main_shape = unary_union(shapes_to_combine)
    if main_shape.is_empty:
        return None

    def get_all_polygons(geom):
        if geom.geom_type == 'Polygon':
            return [geom]
        elif geom.geom_type == 'MultiPolygon':
            return list(geom.geoms)
        return []

    def polygon_moments(coords):
        coords_list = list(coords.coords)
        n = len(coords_list)
        ix = 0
        iy = 0
        for i in range(n - 1):
            x0, y0 = coords_list[i]
            x1, y1 = coords_list[i + 1]
            cross = x0 * y1 - x1 * y0
            ix += cross * (y0**2 + y0*y1 + y1**2)
            iy += cross * (x0**2 + x0*x1 + x1**2)
        return ix, iy

    def calc_moments_about_origin(geometry):
        total_ix = 0
        total_iy = 0
        for polygon in get_all_polygons(geometry):
            if polygon.is_empty:
                continue
            ix_ext, iy_ext = polygon_moments(polygon.exterior)
            ix = abs(ix_ext)
            iy = abs(iy_ext)
            for interior in polygon.interiors:
                ix_h, iy_h = polygon_moments(interior)
                ix -= abs(ix_h)
                iy -= abs(iy_h)
            total_ix += ix
            total_iy += iy
        return total_ix / 12.0, total_iy / 12.0

    area_mm2 = main_shape.area
    centroid = main_shape.centroid

    ix0, iy0 = calc_moments_about_origin(main_shape)
    jx_mm4 = ix0 - area_mm2 * (centroid.y ** 2)
    jy_mm4 = iy0 - area_mm2 * (centroid.x ** 2)

    bounds = main_shape.bounds
    dx_max = max(abs(bounds[3] - centroid.y), abs(centroid.y - bounds[1]))
    dy_max = max(abs(bounds[2] - centroid.x), abs(centroid.x - bounds[0]))

    wx_mm3 = jx_mm4 / dx_max if dx_max > 0 else 0
    wy_mm3 = jy_mm4 / dy_max if dy_max > 0 else 0

    area_cm2 = area_mm2 / 100
    jx_cm4 = jx_mm4 / 10000
    jy_cm4 = jy_mm4 / 10000
    wx_cm3 = wx_mm3 / 1000
    wy_cm3 = wy_mm3 / 1000

    # Применяем коэффициенты приведения для терморазрыва
    if use_k:
        jx_cm4 *= kx
        jy_cm4 *= ky
        wx_cm3 *= kx
        wy_cm3 *= ky

    mass_kg_m = area_cm2 * DENSITY * 100

    # ay — расстояние от наиболее удалённой точки (maxy) до центра масс
    # т.е. от противоположной стенки (верхней грани) до центра масс
    # ax — аналогично по X (от maxx до центра масс)
    # bounds только по металлу
    ax_mm = bounds[2] - centroid.x  # расстояние от правой грани до центра масс
    ay_mm = bounds[3] - centroid.y  # расстояние от верхней грани до центра масс

    # Высота и ширина профиля из bounds (только металл)
    height_mm = bounds[3] - bounds[1]
    width_mm = bounds[2] - bounds[0]

    result = {
        'article': block_name,
        'jx': round(jx_cm4, 3),
        'wx': round(wx_cm3, 3),
        'jy': round(jy_cm4, 3),
        'wy': round(wy_cm3, 3),
        'area_metal': round(area_cm2, 3),
        'area_plastic': round(plastic_area_mm2 / 100, 3),
        'mass': round(mass_kg_m, 3),
        'ax': round(ax_mm, 3),
        'ay': round(ay_mm, 3),
        'ly': ly_value if ly_value else round(height_mm, 3),
        'bx': bx_value if bx_value else round(width_mm, 3),
        'height': round(height_mm, 3),
        'width': round(width_mm, 3),
    }

    # Расчёт усилителя, если есть полилинии на слое «усилитель»
    if polylines_reinforcement:
        rein_polys = sorted(polylines_reinforcement, key=lambda p: p.area, reverse=True)
        rein_levels = []
        for i, pi in enumerate(rein_polys):
            level = 0
            for j, pj in enumerate(rein_polys):
                if i != j and pj.contains(pi) and pj.area > pi.area:
                    level += 1
            rein_levels.append(level)
        rein_children = [[] for _ in range(len(rein_polys))]
        for i in range(len(rein_polys)):
            best_parent = None
            best_area = float('inf')
            for j in range(len(rein_polys)):
                if i != j and rein_polys[j].contains(rein_polys[i]) and rein_polys[j].area > rein_polys[i].area:
                    if rein_levels[j] == rein_levels[i] - 1 and rein_polys[j].area < best_area:
                        best_parent = j
                        best_area = rein_polys[j].area
            if best_parent is not None:
                rein_children[best_parent].append(i)
        rein_shapes = []
        for i, p in enumerate(rein_polys):
            if rein_levels[i] % 2 == 0:
                pp = p if p.is_valid else p.buffer(0)
                for ch in rein_children[i]:
                    ch_p = rein_polys[ch] if rein_polys[ch].is_valid else rein_polys[ch].buffer(0)
                    pp = pp.difference(ch_p)
                rein_shapes.append(pp)
        rein_shape = unary_union(rein_shapes)
        if not rein_shape.is_empty:
            rein_area_mm2 = rein_shape.area
            rein_centroid = rein_shape.centroid
            rein_ix0, rein_iy0 = calc_moments_about_origin(rein_shape)
            rein_jx_mm4 = rein_ix0 - rein_area_mm2 * (rein_centroid.y ** 2)
            rein_jy_mm4 = rein_iy0 - rein_area_mm2 * (rein_centroid.x ** 2)
            rein_bounds = rein_shape.bounds
            rein_height_mm = rein_bounds[3] - rein_bounds[1]
            rein_width_mm = rein_bounds[2] - rein_bounds[0]
            rein_ax_mm = rein_bounds[2] - rein_centroid.x
            rein_ay_mm = rein_bounds[3] - rein_centroid.y
            rein_area_cm2 = rein_area_mm2 / 100
            rein_jx_cm4 = rein_jx_mm4 / 10000
            rein_jy_cm4 = rein_jy_mm4 / 10000
            rein_mass = rein_area_cm2 * DENSITY * 100
            # Имя усилителя из имени блока (после +)
            rein_name = block_name.split('+')[1].strip() if '+' in block_name else block_name
            result['reinforcement'] = {
                'article': rein_name,
                'jx': round(rein_jx_cm4, 3),
                'jy': round(rein_jy_cm4, 3),
                'area_metal': round(rein_area_cm2, 3),
                'mass': round(rein_mass, 3),
                'ax': round(rein_ax_mm, 3),
                'ay': round(rein_ay_mm, 3),
                'ly': round(rein_height_mm, 3),
                'bx': round(rein_width_mm, 3),
            }
        # Имя основного профиля (до +)
        if '+' in block_name:
            result['article'] = block_name.split('+')[0].strip()

    return result


def _get_block_geometry(dxf_path, block_name, doc=None):
    """Возвращает список полилиний блока для предпросмотра: [(points, layer), ...]"""
    import ezdxf
    from ezdxf import path as ezdxf_path

    if doc is None:
        doc = ezdxf.readfile(dxf_path)
    block = doc.blocks.get(block_name)
    if block is None:
        return []

    result = []
    for entity in block:
        if entity.dxftype() in ('LWPOLYLINE', 'POLYLINE'):
            p = ezdxf_path.make_path(entity)
            points = [(pt.x, pt.y) for pt in p.flattening(distance=0.05)]
            if len(points) >= 3:
                layer = entity.dxf.layer.lower().strip()
                result.append((points, layer))
    return result


class ImportDxfDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super(ImportDxfDialog, self).__init__(parent)
        ui_path = resource_path("UI", "import_dxf.ui")
        fix_ui_file(ui_path)
        uic.loadUi(ui_path, self)

        font = QtGui.QFont("Segoe UI", 10)
        self.setFont(font)

        self._dxf_path = None
        self._dxf_doc = None
        self._result = None
        self._scene = QGraphicsScene()
        self.graphicsView.setScene(self._scene)
        self.graphicsView.setRenderHint(QtGui.QPainter.Antialiasing, True)

        # Сохраняем оригинальный resizeEvent и переопределяем для корректного fitInView
        _orig_resize = self.graphicsView.resizeEvent
        def _gv_resize(event, orig=_orig_resize):
            orig(event)
            if self._scene.items():
                self.graphicsView.fitInView(self._scene.sceneRect(), QtCore.Qt.KeepAspectRatio)
        self.graphicsView.resizeEvent = _gv_resize

        # По умолчанию коэффициенты неактивны
        self.lineEdit_kx.setEnabled(False)
        self.lineEdit_ky.setEnabled(False)
        self.checkBox_k.setChecked(False)

        # Сигналы
        self.pushButton_add.clicked.connect(self._browse_file)
        self.comboBox_blok.currentIndexChanged.connect(self._on_block_changed)
        self.checkBox_k.toggled.connect(self._on_checkbox_k_toggled)

    def _on_checkbox_k_toggled(self, checked):
        self.lineEdit_kx.setEnabled(checked)
        self.lineEdit_ky.setEnabled(checked)

    def _browse_file(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Выберите DXF файл", "", "DXF файлы (*.dxf);;Все файлы (*)")
        if not path:
            return
        self._dxf_path = path
        self._dxf_doc = None
        try:
            import ezdxf
            self._dxf_doc = ezdxf.readfile(path)
            names = []
            seen = set()
            for entity in self._dxf_doc.modelspace():
                if entity.dxftype() == 'INSERT':
                    name = entity.dxf.name
                    if name not in seen and name != "Ramka" and not name.startswith('*'):
                        block = self._dxf_doc.blocks.get(name)
                        if block:
                            names.append(name)
                            seen.add(name)
            if not names:
                file_base = os.path.splitext(os.path.basename(path))[0]
                for block in self._dxf_doc.blocks:
                    if block.name == file_base:
                        names.append(block.name)
                        break
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Ошибка", f"Не удалось прочитать DXF:\n{e}")
            return
        self.comboBox_blok.blockSignals(True)
        self.comboBox_blok.clear()
        self.comboBox_blok.addItems(names)
        self.comboBox_blok.blockSignals(False)
        if names:
            self.comboBox_blok.setCurrentIndex(0)
            self._on_block_changed(0)

    def _on_block_changed(self, index):
        if self._dxf_path is None or index < 0:
            return
        block_name = self.comboBox_blok.currentText()
        if not block_name:
            return
        self._draw_preview(block_name)
        # Обновить label_ly и label_bx с размерами блока
        try:
            result = _calculate_block(self._dxf_path, block_name, doc=self._dxf_doc)
            if result:
                ly = result.get('ly', result.get('height', 0))
                bx = result.get('bx', result.get('width', 0))
                self.label_ly.setText(f"Ly={ly:.0f}")
                self.label_bx.setText(f"bx={bx:.0f}")
            else:
                self.label_ly.setText("Ly=")
                self.label_bx.setText("bx=")
        except Exception:
            self.label_ly.setText("Ly=")
            self.label_bx.setText("bx=")

    def _draw_preview(self, block_name):
        self._scene.clear()
        self._scene.setSceneRect(0, 0, 0, 0)
        self.graphicsView.resetTransform()
        try:
            geom = _get_block_geometry(self._dxf_path, block_name, doc=self._dxf_doc)
        except Exception:
            return
        if not geom:
            return

        POLYAMIDE_LAYERS = {'polyamide', 'polyamid', 'полиамид', 'пластик'}
        REINFORCEMENT_LAYERS = {'усилитель', 'reinforcement'}
        pen_metal = QPen(QColor(0, 0, 180), 0)
        brush_no = QBrush(QtCore.Qt.NoBrush)
        pen_plastic = QPen(QColor(200, 0, 0), 0)
        pen_reinforcement = QPen(QColor(200, 0, 0), 0)

        for points, layer in geom:
            polygon = QPolygonF([QPointF(x, -y) for x, y in points])  # flip Y
            if layer in POLYAMIDE_LAYERS:
                self._scene.addPolygon(polygon, pen_plastic, brush_no)
            elif layer in REINFORCEMENT_LAYERS:
                self._scene.addPolygon(polygon, pen_reinforcement, brush_no)
            else:
                self._scene.addPolygon(polygon, pen_metal, brush_no)

        rect = self._scene.itemsBoundingRect()
        self._scene.setSceneRect(rect)
        self.graphicsView.fitInView(rect, QtCore.Qt.KeepAspectRatio)

    def accept(self):
        if self._dxf_path is None or self.comboBox_blok.count() == 0:
            QtWidgets.QMessageBox.warning(self, "Ошибка", "Не выбран блок профиля.")
            return

        block_name = self.comboBox_blok.currentText()
        use_k = self.checkBox_k.isChecked()
        try:
            kx = float(self.lineEdit_kx.text().replace(',', '.')) if use_k else 1.0
        except ValueError:
            kx = 0.75
        try:
            ky = float(self.lineEdit_ky.text().replace(',', '.')) if use_k else 1.0
        except ValueError:
            ky = 1.0

        try:
            result = _calculate_block(self._dxf_path, block_name, kx, ky, use_k, doc=self._dxf_doc)
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Ошибка", f"Ошибка расчёта:\n{e}")
            return

        if result is None:
            QtWidgets.QMessageBox.warning(self, "Ошибка", "Не удалось рассчитать характеристики блока.")
            return

        self._result = result
        # Тип профиля: 0=стойка, 1=ригель, 2=усилитель стойка, 3=усилитель ригель
        self._result['profile_type'] = self.comboBox_1.currentIndex()
        super().accept()

    def get_result(self):
        return self._result
