import os
import json
import math
import sys
from PyQt5 import QtWidgets, uic, QtCore, QtGui
from import_dxf import ImportDxfDialog


def resource_path(*parts):
    """Абсолютный путь к ресурсу для запуска из исходников и из PyInstaller."""
    if getattr(sys, 'frozen', False):
        base_path = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, *parts)

def fix_ui_file(ui_path):
    """Приводит перечисления Qt6 в .ui к виду, понятному PyQt5.

    Qt6 Designer пишет, например, QFrame::Shape::NoFrame или
    Qt::AlignmentFlag::AlignLeft. PyQt5 понимает только Класс::Значение,
    поэтому из цепочки оставляем первый и последний элементы.
    Префикс класса (QFrame, QSizePolicy, QDialogButtonBox, Qt ...) НЕ удаляем.
    """
    import re
    if not os.path.exists(ui_path):
        return

    with open(ui_path, 'r', encoding='utf-8') as f:
        content = f.read()

    def clean_item(item):
        item = item.strip()
        if '::' not in item:
            return item
        parts = item.split('::')
        return f"{parts[0]}::{parts[-1]}"

    def process_tag(match):
        tag = match.group(1)
        body = match.group(2)
        if not body.strip():
            return match.group(0)
        items = [clean_item(i) for i in body.split('|') if i.strip()]
        return f'<{tag}>{"|".join(items)}</{tag}>'

    new_content = re.sub(r'<(set|enum)>([^<]*)</\1>', process_tag, content)

    if new_content != content:
        with open(ui_path, 'w', encoding='utf-8') as f:
            f.write(new_content)

class NewProfileDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, mullion=None, transom=None, manual_data=None):
        super(NewProfileDialog, self).__init__(parent)
        ui_path = resource_path("UI", "new_profil.ui")
        fix_ui_file(ui_path)
        uic.loadUi(ui_path, self)
        
        # Полоса прокрутки уже есть в самом .ui (scrollArea), обёртка не нужна.
        # Размер окна подгоняем под содержимое, но не больше экрана.
        self.setMinimumSize(600, 600)
        hint = self.scrollArea.widget().sizeHint()
        avail = QtWidgets.QApplication.primaryScreen().availableGeometry()
        self.resize(min(hint.width() + 6, avail.width()),
                    min(hint.height() + 70, avail.height()))

        self.mullion = mullion
        self.transom = transom
        self.manual_data = manual_data
        
        self._area_tot_m = 0.0
        self._area_tot_t = 0.0
        
        self._setup_initial_values()
        self._setup_numeric_validators()
        self._connect_signals()
        self._update_all_states()
        self._update_pixmaps()
        self._calculate_all()

    def _setup_numeric_validators(self):
        number_validator = QtGui.QRegularExpressionValidator(
            QtCore.QRegularExpression(r"^\d*(?:[\.,]\d*)?$")
        )

        numeric_fields = [
            "lineEdit_ay_main", "lineEdit_ay_rein", "lineEdit_4", "lineEdit_2", "lineEdit_5", "lineEdit_3",
            "lineEdit_6", "lineEdit_7", "lineEdit_57", "lineEdit_9", "lineEdit_10", "lineEdit_13",
            "lineEdit_15", "lineEdit_16", "lineEdit_17", "lineEdit_55", "lineEdit_59", "lineEdit_ax_main_2",
            "lineEdit_ax_rein_2", "lineEdit_ayay_st", "lineEdit_ay_main_t", "lineEdit_ay_rein_t", "lineEdit_31",
            "lineEdit_32", "lineEdit_33", "lineEdit_34", "lineEdit_35", "lineEdit_36", "lineEdit_58",
            "lineEdit_18", "lineEdit_19", "lineEdit_23", "lineEdit_27", "lineEdit_28", "lineEdit_29",
            "lineEdit_56", "lineEdit_60", "lineEdit_ax_main_3", "lineEdit_ax_rein_3", "lineEdit_ayay_rig",
            "lineEdit_37", "lineEdit_38", "lineEdit_39", "lineEdit_40", "lineEdit_41", "lineEdit_47",
            "lineEdit_48", "lineEdit_49", "lineEdit_50", "lineEdit_51"
        ]

        for field_name in numeric_fields:
            widget = getattr(self, field_name, None)
            if isinstance(widget, QtWidgets.QLineEdit):
                widget.setValidator(number_validator)

    def _setup_initial_values(self):
        # 1. Стойка (Mullion)
        m_data = self.manual_data.get('mullion', {}) if self.manual_data else {}
        
        # Если есть manual_data, восстанавливаем из неё в приоритете
        if m_data:
            self.lineEdit.setText(m_data.get('art', ""))
            self.checkBox.setChecked(m_data.get('active', False))
            sys_type = m_data.get('sys_type', 0)
            # Обратная совместимость: если thermal_active=True, это вариант 2 (терморазрыв)
            if m_data.get('thermal_active', False) and sys_type == 0:
                sys_type = 2
            self.comboBox_sys.setCurrentIndex(sys_type)
            self.lineEdit_ay_main.setText(f"{m_data.get('ay_main', 0.0):.2f}")
            self.lineEdit_ay_rein.setText(f"{m_data.get('ay_rein', 0.0):.2f}")
            self.checkBox_2.setChecked(m_data.get('rein_active_char', False))
            self.checkBox_5.setChecked(m_data.get('rein_active_geom', False))
            
            self.lineEdit_4.setText(f"{m_data.get('ix', 0):.2f}")
            self.lineEdit_2.setText(f"{m_data.get('iy', 0):.2f}")
            self.lineEdit_5.setText(f"{m_data.get('wx', 0):.2f}")
            self.lineEdit_3.setText(f"{m_data.get('wy', 0):.2f}")
            self.lineEdit_6.setText(f"{m_data.get('area', 0):.2f}")
            self.lineEdit_7.setText(f"{m_data.get('depth', 0):.2f}")
            self.lineEdit_57.setText(f"{m_data.get('width', 50.0):.2f}")
            
            self.lineEdit_8.setText(m_data.get('rein_art', ""))
            self.comboBox_4.setCurrentText(m_data.get('rein_material', "алюминий"))
            self.comboBox_7.setCurrentText(m_data.get('rein_pos', "внутри"))
            
            self.lineEdit_9.setText(f"{m_data.get('rein_ix', 0):.2f}")
            self.lineEdit_10.setText(f"{m_data.get('rein_iy', 0):.2f}")
            self.lineEdit_13.setText(f"{m_data.get('rein_area', 0):.2f}")
            
            self.comboBox_8.setCurrentText(m_data.get('rein_shape', "Труба"))
            self.lineEdit_15.setText(f"{m_data.get('rein_bx', 0):.2f}")
            self.lineEdit_16.setText(f"{m_data.get('rein_by', 0):.2f}")
            self.lineEdit_17.setText(f"{m_data.get('rein_t', 0):.2f}")
            self.lineEdit_55.setText(f"{m_data.get('rein_by_char', 0):.2f}")
            self.lineEdit_59.setText(f"{m_data.get('rein_bx_char', 50.0):.2f}")
        elif self.mullion:
            art = self.mullion.get('art', '')
            clean_art = art.split('+')[0]
            self.lineEdit.setText(clean_art)
            
            if '+' in art:
                self.checkBox_2.setChecked(True)
                rein_art = art.split('+')[1]
                self.lineEdit_8.setText(rein_art)
                
            self.lineEdit_4.setText(f"{self.mullion.get('ix', 0):.2f}")
            self.lineEdit_2.setText(f"{self.mullion.get('iy', 0):.2f}")
            self.lineEdit_5.setText(f"{self.mullion.get('wx', 0):.2f}")
            self.lineEdit_3.setText(f"{self.mullion.get('wy', 0):.2f}")
            self.lineEdit_6.setText(f"{self.mullion.get('area', 0):.2f}")
            self.lineEdit_7.setText(f"{self.mullion.get('depth', 0):.2f}")
            self.lineEdit_57.setText(f"{self.mullion.get('width', 50.0):.2f}")
            # ay = Ly(таблица) - ay(таблица), ax = ax(таблица)
            ay_table = self.mullion.get('ay', 0.0)
            depth_table = self.mullion.get('depth', 0.0)
            ay_val = depth_table - ay_table if ay_table else 0.0
            ax_val = self.mullion.get('ax', 0.0)
            self.lineEdit_ay_main.setText(f"{ay_val:.2f}")
            self.lineEdit_ax_main_2.setText(f"{ax_val:.2f}")
            # Не заполняем поля усилителя по умолчанию данными стойки
            self.lineEdit_55.setText(f"0.00")
            self.lineEdit_59.setText(f"0.00")

        # 2. Ригель (Transom)
        t_data = self.manual_data.get('transom', {}) if self.manual_data else {}
        
        if t_data:
            self.lineEdit_30.setText(t_data.get('art', ""))
            self.checkBox_4.setChecked(t_data.get('active', False))
            self.checkBox_3.setChecked(t_data.get('rein_active_char', False))
            self.checkBox_6.setChecked(t_data.get('rein_active_geom', False))
            
            self.lineEdit_31.setText(f"{t_data.get('ix', 0):.2f}")
            self.lineEdit_32.setText(f"{t_data.get('iy', 0):.2f}")
            self.lineEdit_33.setText(f"{t_data.get('wx', 0):.2f}")
            self.lineEdit_34.setText(f"{t_data.get('wy', 0):.2f}")
            self.lineEdit_35.setText(f"{t_data.get('area', 0):.2f}")
            self.lineEdit_36.setText(f"{t_data.get('depth', 0):.2f}")
            self.lineEdit_58.setText(f"{t_data.get('width', 50.0):.2f}")
            self.lineEdit_ay_main_t.setText(f"{t_data.get('ay_main_t', 0.0):.2f}")
            self.lineEdit_ay_rein_t.setText(f"{t_data.get('ay_rein_t', 0.0):.2f}")
            
            if hasattr(self, 'lineEdit_26'): self.lineEdit_26.setText(t_data.get('rein_art', ""))
            self.comboBox_5.setCurrentText(t_data.get('rein_material', "алюминий"))
            self.comboBox_6.setCurrentText(t_data.get('rein_pos', "внутри"))
            
            self.lineEdit_18.setText(f"{t_data.get('rein_ix', 0):.2f}")
            self.lineEdit_19.setText(f"{t_data.get('rein_iy', 0):.2f}")
            self.lineEdit_23.setText(f"{t_data.get('rein_area', 0):.2f}")
            
            self.comboBox_9.setCurrentText(t_data.get('rein_shape', "Труба"))
            self.lineEdit_27.setText(f"{t_data.get('rein_bx', 0):.2f}")
            self.lineEdit_28.setText(f"{t_data.get('rein_by', 0):.2f}")
            self.lineEdit_29.setText(f"{t_data.get('rein_t', 0):.2f}")
            self.lineEdit_56.setText(f"{t_data.get('rein_by_char', 0):.2f}")
            self.lineEdit_60.setText(f"{t_data.get('rein_bx_char', 50.0):.2f}")
        elif self.transom:
            art = self.transom.get('art', '')
            clean_art = art.split('+')[0]
            self.lineEdit_30.setText(clean_art)
            
            if '+' in art:
                self.checkBox_3.setChecked(True)
                rein_art = art.split('+')[1]
                if hasattr(self, 'lineEdit_26'): self.lineEdit_26.setText(rein_art)
                
            self.lineEdit_31.setText(f"{self.transom.get('ix', 0):.2f}")
            self.lineEdit_32.setText(f"{self.transom.get('iy', 0):.2f}")
            self.lineEdit_33.setText(f"{self.transom.get('wx', 0):.2f}")
            self.lineEdit_34.setText(f"{self.transom.get('wy', 0):.2f}")
            self.lineEdit_35.setText(f"{self.transom.get('area', 0):.2f}")
            self.lineEdit_36.setText(f"{self.transom.get('depth', 0):.2f}")
            self.lineEdit_58.setText(f"{self.transom.get('width', 50.0):.2f}")
            # ay = Ly(таблица) - ay(таблица), ax = ax(таблица)
            ay_table_t = self.transom.get('ay', 0.0)
            depth_table_t = self.transom.get('depth', 0.0)
            ay_val_t = depth_table_t - ay_table_t if ay_table_t else 0.0
            ax_val_t = self.transom.get('ax', 0.0)
            self.lineEdit_ay_main_t.setText(f"{ay_val_t:.2f}")
            self.lineEdit_ax_main_3.setText(f"{ax_val_t:.2f}")
            # Не заполняем поля усилителя по умолчанию данными ригеля
            self.lineEdit_56.setText(f"0.00")
            self.lineEdit_60.setText(f"0.00")

    def _connect_signals(self):
        # Checkboxes
        self.checkBox.toggled.connect(self._update_all_states)
        self.checkBox.toggled.connect(lambda: self._update_pixmaps())
        self.checkBox_4.toggled.connect(lambda: self._update_pixmaps())
        self.comboBox_sys.currentIndexChanged.connect(self._on_sys_changed)
        self.lineEdit_ay_main.textChanged.connect(self._calculate_all)
        self.lineEdit_ay_rein.textChanged.connect(self._calculate_all)
        self.lineEdit_ay_main_t.textChanged.connect(self._calculate_all)
        self.lineEdit_ay_rein_t.textChanged.connect(self._calculate_all)
        self.lineEdit_ayay_st.textChanged.connect(self._calculate_all)
        self.lineEdit_ayay_rig.textChanged.connect(self._calculate_all)
        self.comboBox_7.currentIndexChanged.connect(self._update_all_states)
        self.comboBox_6.currentIndexChanged.connect(self._update_all_states)
        self.lineEdit_ax_main_2.textChanged.connect(self._calculate_all)
        self.lineEdit_ax_rein_2.textChanged.connect(self._calculate_all)
        self.lineEdit_ax_main_3.textChanged.connect(self._calculate_all)
        self.lineEdit_ax_rein_3.textChanged.connect(self._calculate_all)
        self.checkBox_2.toggled.connect(self._on_rein_m_char_toggled)
        self.checkBox_5.toggled.connect(self._on_rein_m_geom_toggled)
        
        self.checkBox_4.toggled.connect(self._update_all_states)
        self.checkBox_3.toggled.connect(self._on_rein_t_char_toggled)
        self.checkBox_6.toggled.connect(self._on_rein_t_geom_toggled)
        
        # Inputs for calculation
        # Mullion Reinforcement Geometry
        for w in [self.lineEdit_15, self.lineEdit_16, self.lineEdit_17, self.comboBox_8, self.lineEdit_37, self.lineEdit_38, self.lineEdit_39, self.lineEdit_40, self.lineEdit_41, self.comboBox_7]:
            if isinstance(w, QtWidgets.QLineEdit):
                w.textChanged.connect(self._calculate_all)
            elif isinstance(w, QtWidgets.QComboBox):
                w.currentIndexChanged.connect(self._calculate_all)
        
        # Transom Reinforcement Geometry
        for w in [self.lineEdit_27, self.lineEdit_28, self.lineEdit_29, self.comboBox_9, self.lineEdit_51, self.lineEdit_47, self.lineEdit_48, self.lineEdit_49, self.lineEdit_50, self.comboBox_6]:
            if isinstance(w, QtWidgets.QLineEdit):
                w.textChanged.connect(self._calculate_all)
            elif isinstance(w, QtWidgets.QComboBox):
                w.currentIndexChanged.connect(self._calculate_all)
                
        # Reinforcement characteristics (if manual)
        for w in [self.lineEdit_8, self.lineEdit_9, self.lineEdit_10, self.lineEdit_13, self.lineEdit_55]:
            if isinstance(w, QtWidgets.QLineEdit):
                w.textChanged.connect(self._calculate_all)
            elif isinstance(w, QtWidgets.QComboBox):
                w.currentIndexChanged.connect(self._calculate_all)

        for w in [self.lineEdit_26, self.lineEdit_18, self.lineEdit_19, self.lineEdit_23, self.lineEdit_56]:
            if isinstance(w, QtWidgets.QLineEdit):
                w.textChanged.connect(self._calculate_all)
            elif isinstance(w, QtWidgets.QComboBox):
                w.currentIndexChanged.connect(self._calculate_all)

        # Force connect comboBox_6 and comboBox_7 specifically to be sure
        self.comboBox_7.currentIndexChanged.connect(self._calculate_all)
        self.comboBox_6.currentIndexChanged.connect(self._calculate_all)
        self.comboBox_4.currentIndexChanged.connect(self._calculate_all)
        self.comboBox_5.currentIndexChanged.connect(self._calculate_all)

        # Checkbox toggled should also trigger calculation
        self.checkBox_2.toggled.connect(self._calculate_all)
        self.checkBox_5.toggled.connect(self._calculate_all)
        self.checkBox_3.toggled.connect(self._calculate_all)
        self.checkBox_6.toggled.connect(self._calculate_all)
        self.checkBox.toggled.connect(self._calculate_all)
        self.checkBox_4.toggled.connect(self._calculate_all)

        # Mullion and Transom characteristics (manual input)
        for w in [self.lineEdit_4, self.lineEdit_2, self.lineEdit_5, self.lineEdit_3, self.lineEdit_6, self.lineEdit_7, self.lineEdit_57,
                 self.lineEdit_31, self.lineEdit_32, self.lineEdit_33, self.lineEdit_34, self.lineEdit_35, self.lineEdit_36, self.lineEdit_58,
                 self.lineEdit_59, self.lineEdit_60]:
            w.textChanged.connect(self._calculate_all)

        # Кнопки сохранения/загрузки профилей
        self.pushButton_3.clicked.connect(self._save_mullion)
        self.pushButton_4.clicked.connect(self._load_mullion)
        self.pushButton_2.clicked.connect(self._save_transom)
        self.pushButton.clicked.connect(self._load_transom)
        # Импорт из DXF
        self.pushButton_5.clicked.connect(self._import_from_dxf)

    def _on_sys_changed(self, index):
        self._update_all_states()
        self._update_pixmaps()
        if index == 2:
            QtWidgets.QMessageBox.information(
                self, "Терморазрыв",
                "Внимание! В режиме терморазрыва в поле «Площадь сечения» "
                "необходимо вводить площадь только алюминиевой части профиля "
                "(без полиамида).\n\n"
                "Каталожная площадь обычно включает полиамид, что приводит "
                "к завышению момента инерции в теореме Штейнера."
            )
        self._calculate_all()

    def _update_pixmaps(self):
        """Обновление картинок в зависимости от типа системы и состояния checkBox/checkBox_4."""
        idx = self.comboBox_sys.currentIndex()
        ui_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "UI")
        # Стойка (label, label_2) — зависит от checkBox (ручной ввод стойки)
        if self.checkBox.isChecked():
            if idx == 1:  # Штапиковый профиль
                self.label.setPixmap(self._load_pixmap(ui_dir, "Impost.png"))
                self.label_2.setPixmap(self._load_pixmap(ui_dir, "SRU_i.png"))
            elif idx == 2:  # Штапиковый профиль терморазрыв
                self.label.setPixmap(self._load_pixmap(ui_dir, "Impost_termo.png"))
                self.label_2.setPixmap(self._load_pixmap(ui_dir, "SRU_termo.png"))
            else:  # Фасадный профиль
                self.label.setPixmap(self._load_pixmap(ui_dir, "Stoika.png"))
                self.label_2.setPixmap(self._load_pixmap(ui_dir, "SU.png"))
        # Ригель (label_4, label_3) — зависит от checkBox_4 (ручной ввод ригеля)
        if self.checkBox_4.isChecked():
            if idx == 1:  # Штапиковый профиль
                self.label_4.setPixmap(self._load_pixmap(ui_dir, "Impost.png"))
                self.label_3.setPixmap(self._load_pixmap(ui_dir, "SRU_i.png"))
            elif idx == 2:  # Штапиковый профиль терморазрыв
                self.label_4.setPixmap(self._load_pixmap(ui_dir, "Impost_termo.png"))
                self.label_3.setPixmap(self._load_pixmap(ui_dir, "SRU_termo.png"))
            else:  # Фасадный профиль
                self.label_4.setPixmap(self._load_pixmap(ui_dir, "Rigel.png"))
                self.label_3.setPixmap(self._load_pixmap(ui_dir, "RU.png"))

    def _load_pixmap(self, ui_dir, filename):
        """Загрузка pixmap из папки UI."""
        from PyQt5.QtGui import QPixmap
        path = os.path.join(ui_dir, filename)
        return QPixmap(path)

    def _on_rein_m_char_toggled(self, checked):
        if checked:
            self.checkBox_5.setChecked(False)
        self._update_all_states()
        self._calculate_all()

    def _on_rein_m_geom_toggled(self, checked):
        if checked:
            self.checkBox_2.setChecked(False)
        self._update_all_states()
        self._calculate_all()

    def _on_rein_t_char_toggled(self, checked):
        if checked:
            self.checkBox_6.setChecked(False)
        self._update_all_states()
        self._calculate_all()

    def _on_rein_t_geom_toggled(self, checked):
        if checked:
            self.checkBox_3.setChecked(False)
        self._update_all_states()
        self._calculate_all()

    def _update_all_states(self):
        # Стойка
        m_manual = self.checkBox.isChecked()
        for w in [self.lineEdit, self.lineEdit_4, self.lineEdit_2, self.lineEdit_5, self.lineEdit_3, self.lineEdit_6, self.lineEdit_7]:
            w.setReadOnly(not m_manual)
        self.comboBox_sys.setEnabled(m_manual)
            
        # Усилитель стойка характеристики
        m_rein_char = self.checkBox_2.isChecked()
        for w in [self.lineEdit_8, self.lineEdit_9, self.lineEdit_10, self.lineEdit_13, self.lineEdit_55, self.lineEdit_59, self.comboBox_4]:
            w.setEnabled(m_rein_char)
            
        # Усилитель стойка геометрия
        m_rein_geom = self.checkBox_5.isChecked()
        # lineEdit_15-17, 37-41, comboBox_8, lineEdit_8 (артикул)
        for w in [self.comboBox_8, self.lineEdit_15, self.lineEdit_16, self.lineEdit_17, self.lineEdit_37, self.lineEdit_38, self.lineEdit_39, self.lineEdit_40, self.lineEdit_41]:
            w.setEnabled(m_rein_geom)
            if isinstance(w, QtWidgets.QLineEdit) and w.objectName() in ["lineEdit_37", "lineEdit_38", "lineEdit_39", "lineEdit_40", "lineEdit_41"]:
                w.setReadOnly(True)

        # Артикул усилителя стойки (lineEdit_8) активен при любом способе ввода усилителя
        self.lineEdit_8.setEnabled(m_rein_char or m_rein_geom)

        # Расположение усилителя стойки (comboBox_7) активируется при любом способе ввода усилителя
        self.comboBox_7.setEnabled(m_rein_char or m_rein_geom)
        self.comboBox_4.setEnabled(m_rein_char or m_rein_geom)

        # Поля bx, ax, ay стойки активны при ручном вводе или при выбранном усилителе
        m_has_rein = m_rein_char or m_rein_geom
        self.lineEdit_57.setEnabled(m_manual or m_has_rein)       # bx стойки
        self.lineEdit_ax_main_2.setEnabled(m_manual or m_has_rein) # ax стойки
        self.lineEdit_ay_main.setEnabled(m_manual or m_has_rein)   # ay стойки
        self.lineEdit_ay_rein.setEnabled(m_has_rein)   # ay усилителя стойки
        self.lineEdit_ay_rein.setReadOnly(m_rein_geom)  # только чтение при геометрии
        self.lineEdit_ax_rein_2.setEnabled(m_has_rein)  # ax усилителя стойки
        self.lineEdit_ax_rein_2.setReadOnly(m_rein_geom)  # только чтение при геометрии
        # lineEdit_ayay_st активен только при "внутри камеры" + усилитель активен
        m_pos_inside = "внутри" in self.comboBox_7.currentText().strip().lower() if m_has_rein else False
        self.lineEdit_ayay_st.setEnabled(m_has_rein and m_pos_inside)
        # Wx, Wy стойки неактивны при выбранном усилителе (пересчитываются автоматически)
        self.lineEdit_5.setReadOnly(m_has_rein)        # Wx стойки
        self.lineEdit_3.setReadOnly(m_has_rein)        # Wy стойки

        # Ригель
        t_manual = self.checkBox_4.isChecked()
        for w in [self.lineEdit_30, self.lineEdit_31, self.lineEdit_32, self.lineEdit_33, self.lineEdit_34, self.lineEdit_35, self.lineEdit_36]:
            w.setReadOnly(not t_manual)
            
        # Усилитель ригель характеристики
        t_rein_char = self.checkBox_3.isChecked()
        for w in [self.lineEdit_26, self.lineEdit_18, self.lineEdit_19, self.lineEdit_23, self.lineEdit_56, self.lineEdit_60, self.comboBox_5]:
            w.setEnabled(t_rein_char)
            
        # Усилитель ригель геометрия
        t_rein_geom = self.checkBox_6.isChecked()
        for w in [self.comboBox_9, self.lineEdit_27, self.lineEdit_28, self.lineEdit_29, self.lineEdit_51, self.lineEdit_47, self.lineEdit_48, self.lineEdit_49, self.lineEdit_50]:
            w.setEnabled(t_rein_geom)
            if isinstance(w, QtWidgets.QLineEdit) and w.objectName() in ["lineEdit_51", "lineEdit_47", "lineEdit_48", "lineEdit_49", "lineEdit_50"]:
                w.setReadOnly(True)

        # Артикул усилителя ригеля (lineEdit_26) активен при любом способе ввода усилителя
        self.lineEdit_26.setEnabled(t_rein_char or t_rein_geom)
        
        # Расположение и материал усилителя ригеля активны при любом способе ввода усилителя
        self.comboBox_6.setEnabled(t_rein_char or t_rein_geom)
        self.comboBox_5.setEnabled(t_rein_char or t_rein_geom)

        # Поля bx, ax, ay ригеля активны при ручном вводе или при выбранном усилителе
        t_has_rein = t_rein_char or t_rein_geom
        self.lineEdit_58.setEnabled(t_manual or t_has_rein)        # bx ригеля
        self.lineEdit_ax_main_3.setEnabled(t_manual or t_has_rein)  # ax ригеля
        self.lineEdit_ay_main_t.setEnabled(t_manual or t_has_rein)  # ay ригеля
        self.lineEdit_ay_rein_t.setEnabled(t_has_rein)  # ay усилителя ригеля
        self.lineEdit_ay_rein_t.setReadOnly(t_rein_geom)  # только чтение при геометрии
        self.lineEdit_ax_rein_3.setEnabled(t_has_rein)  # ax усилителя ригеля
        self.lineEdit_ax_rein_3.setReadOnly(t_rein_geom)  # только чтение при геометрии
        # lineEdit_ayay_rig активен только при "внутри камеры" + усилитель активен
        t_pos_inside = "внутри" in self.comboBox_6.currentText().strip().lower() if t_has_rein else False
        self.lineEdit_ayay_rig.setEnabled(t_has_rein and t_pos_inside)
        # Wx, Wy ригеля неактивны при выбранном усилителе (пересчитываются автоматически)
        self.lineEdit_33.setReadOnly(t_has_rein)        # Wx ригеля
        self.lineEdit_34.setReadOnly(t_has_rein)        # Wy ригеля

    def _calc_wx_simple(self, ix_tot, h, ay_widget):
        """Расчёт Wx с учётом ay. Если ay>0, крайнее волокно = max(ay, h-ay), иначе h/2."""
        if h <= 0:
            return 0
        ay = self._parse_float(ay_widget.text()) if ay_widget else 0.0
        if ay > 0:
            c_max = max(ay, h - ay) / 10.0  # мм -> см
        else:
            c_max = h / 20.0  # h/2 в см
        return ix_tot / c_max if c_max > 0 else 0

    def _calc_wy(self, iy_tot, b_m, bx_rein, ax_main_widget, ax_rein_widget):
        """Расчёт Wy с учётом ax. Если ax>0, используем ax, иначе ширина/2."""
        max_bx = max(b_m, bx_rein) if bx_rein > 0 else b_m
        ax_main = self._parse_float(ax_main_widget.text())
        ax_rein = self._parse_float(ax_rein_widget.text())
        if ax_main > 0 or ax_rein > 0:
            cx_main = ax_main if ax_main > 0 else b_m / 2.0
            cx_rein = ax_rein if ax_rein > 0 else bx_rein / 2.0 if bx_rein > 0 else b_m / 2.0
            cx_max = max(cx_main, b_m - cx_main, cx_rein, bx_rein - cx_rein) if max_bx > 0 else 1.0
            return iy_tot / (cx_max / 10.0) if cx_max > 0 else 0
        else:
            return iy_tot / (max_bx / 20.0) if max_bx > 0 else 0

    def _parse_float(self, text):
        try:
            return float(text.replace(',', '.'))
        except:
            return 0.0

    def _calculate_geometry(self, shape_type, bx, by, t):
        # bx - ширина по X, by - высота по Y (глубина), t - толщина
        area = 0.0
        ix = 0.0 # Относительно горизонтальной оси X (изгиб по высоте by)
        iy = 0.0 # Относительно вертикальной оси Y (изгиб по ширине bx)
        wx = 0.0
        wy = 0.0
        
        if t <= 0: return area, ix, iy, wx, wy, by / 2.0 if by > 0 else 0.0, bx / 2.0 if bx > 0 else 0.0
        
        # ay - расстояние от нижней грани до ЦТ (мм), ax - от левой грани до ЦТ (мм)
        ay = by / 2.0  # по умолчанию симметрия
        ax = bx / 2.0
        
        if "Труба" in shape_type:
            # Прямоугольная труба (симметричное сечение)
            area = (bx * by - (bx - 2*t) * (by - 2*t)) / 100.0 # см2
            # Jx = (bx * by^3 - (bx-2t)*(by-2t)^3) / 12
            ix = (bx * (by**3) - (bx - 2*t) * ((by - 2*t)**3)) / 12.0 / 10000.0 # см4
            # Jy = (by * bx^3 - (by-2t)*(bx-2t)^3) / 12
            iy = (by * (bx**3) - (by - 2*t) * ((bx - 2*t)**3)) / 12.0 / 10000.0 # см4
            wx = ix / (by / 20.0) # см3 (по наиболее удаленному волокну по Y)
            wy = iy / (bx / 20.0) # см3 (по наиболее удаленному волокну по X)
            # Труба симметрична: ay = by/2, ax = bx/2 (уже установлено)
        elif "Швеллер" in shape_type:
            # Швеллер (П-образное сечение): одна полка (нижняя) + две стенки (боковые)
            # Полка: bx × t (нижняя), стенки: t × (by - t) каждая (левая и правая)
            h_w = by - t  # высота стенки (мм)
            a_flange = bx * t          # площадь полки (мм²)
            a_wall = t * h_w           # площадь одной стенки (мм²)
            a_total = a_flange + 2 * a_wall  # общая площадь (мм²)
            area = a_total / 100.0     # см²
            # ay — ЦТ смещён к полке (несимметричен по оси Y)
            if a_total > 0:
                ay = (a_flange * (t / 2.0) + 2 * a_wall * (t + h_w / 2.0)) / a_total
            # ax = bx/2 (симметричен по оси X — стенки слева и справа)
            # Ix — момент инерции относительно горизонтальной оси через ЦТ (теорема Штейнера)
            ix_fl = bx * t**3 / 12.0 + a_flange * (ay - t / 2.0)**2
            ix_w = t * h_w**3 / 12.0 + a_wall * (t + h_w / 2.0 - ay)**2
            ix = (ix_fl + 2 * ix_w) / 10000.0  # см⁴
            # Iy — момент инерции относительно вертикальной оси через ЦТ
            iy_fl = t * bx**3 / 12.0
            iy_w = h_w * t**3 / 12.0 + a_wall * ((bx - t) / 2.0)**2
            iy = (iy_fl + 2 * iy_w) / 10000.0  # см⁴
            # Wx — несимметричен по Y, делим на max расстояние от ЦТ до края
            cy_max = max(ay, by - ay) if by > 0 else 1.0
            wx = ix / (cy_max / 10.0) if cy_max > 0 else 0
            # Wy — симметричен по X, делим на bx/2
            wy = iy / (bx / 20.0) if bx > 0 else 0
            
        return area, ix, iy, wx, wy, ay, ax

    def _calculate_all(self):
        # 1. Стойка Усилитель Геометрия
        if self.checkBox_5.isChecked():
            shape = self.comboBox_8.currentText()
            bx = self._parse_float(self.lineEdit_15.text()) # ширина (по оси X)
            by = self._parse_float(self.lineEdit_16.text()) # высота/глубина (по оси Y)
            t = self._parse_float(self.lineEdit_17.text())
            # ix - момент инерции относительно горизонтальной оси (X), проходящей через ЦТ.
            # Для прямоугольной трубы bx * by^3 / 12 ...
            # Но в нашем расчете ix - это изгиб из плоскости (ветер), Iy - в плоскости (вес).
            # В фасадных системах: 
            # Ix (глубина) - изгиб от ветра. Плечо - глубина.
            # Iy (ширина) - изгиб от веса. Плечо - ширина.
            # В _calculate_geometry сейчас:
            # ix = (by * (bx**3) - ...) / 12  <-- Это Iy (относительно вертикальной оси)
            # iy = (bx * (by**3) - ...) / 12  <-- Это Ix (относительно горизонтальной оси)
            area, ix_calc, iy_calc, wx_calc, wy_calc, ay_calc, ax_calc = self._calculate_geometry(shape, bx, by, t)
            self.lineEdit_37.setText(f"{ix_calc:.2f}")
            self.lineEdit_38.setText(f"{iy_calc:.2f}")
            self.lineEdit_39.setText(f"{wx_calc:.2f}")
            self.lineEdit_40.setText(f"{wy_calc:.2f}")
            self.lineEdit_41.setText(f"{area:.2f}")
            # Автозаполнение ay_rein и ax_rein для усилителя стойки
            self.lineEdit_ay_rein.setText(f"{ay_calc:.2f}")
            self.lineEdit_ax_rein_2.setText(f"{ax_calc:.2f}")

        # 2. Ригель Усилитель Геометрия
        if self.checkBox_6.isChecked():
            shape = self.comboBox_9.currentText()
            bx = self._parse_float(self.lineEdit_27.text()) # ширина (по оси X)
            by = self._parse_float(self.lineEdit_28.text()) # высота/глубина (по оси Y)
            t = self._parse_float(self.lineEdit_29.text())
            area, ix_calc, iy_calc, wx_calc, wy_calc, ay_calc, ax_calc = self._calculate_geometry(shape, bx, by, t)
            self.lineEdit_51.setText(f"{ix_calc:.2f}")
            self.lineEdit_47.setText(f"{iy_calc:.2f}")
            self.lineEdit_48.setText(f"{wx_calc:.2f}")
            self.lineEdit_49.setText(f"{wy_calc:.2f}")
            self.lineEdit_50.setText(f"{area:.2f}")
            # Автозаполнение ay_rein и ax_rein для усилителя ригеля
            self.lineEdit_ay_rein_t.setText(f"{ay_calc:.2f}")
            self.lineEdit_ax_rein_3.setText(f"{ax_calc:.2f}")

        # Суммарные характеристики Стойка
        self._update_totals_mullion()
        # 4. Суммарные характеристики Ригель
        self._update_totals_transom()

    def _update_totals_mullion(self):
        # Стойка
        ix_m = self._parse_float(self.lineEdit_4.text())
        iy_m = self._parse_float(self.lineEdit_2.text())
        wx_m = self._parse_float(self.lineEdit_5.text())
        wy_m = self._parse_float(self.lineEdit_3.text())
        h_m = self._parse_float(self.lineEdit_7.text())  # глубина (мм)
        b_m = self._parse_float(self.lineEdit_57.text())  # ширина (мм)
        area_m = self._parse_float(self.lineEdit_6.text()) # площадь (см2)
        
        ix_r, iy_r, wx_r, wy_r, area_r = 0.0, 0.0, 0.0, 0.0, 0.0
        bx_rein = b_m  # ширина усилителя по умолчанию равна ширине профиля
        by_rein = 0.0  # глубина усилителя
        mat = "алюминий"
        pos = "внутри камеры профиля"
        
        if self.comboBox_sys.currentIndex() == 2:
            # Режим терморазрыва (штапиковый профиль)
            # Вторая алюминиевая часть берётся из блока усилителя
            if self.checkBox_2.isChecked():
                ix_r = self._parse_float(self.lineEdit_9.text())
                iy_r = self._parse_float(self.lineEdit_10.text())
                area_r = self._parse_float(self.lineEdit_13.text())
                bx_rein = self._parse_float(self.lineEdit_55.text())
                by_rein = self._parse_float(self.lineEdit_59.text())
                mat = self.comboBox_4.currentText().strip().lower()
                pos = self.comboBox_7.currentText().strip().lower()
            elif self.checkBox_5.isChecked():
                ix_r = self._parse_float(self.lineEdit_37.text())
                iy_r = self._parse_float(self.lineEdit_38.text())
                area_r = self._parse_float(self.lineEdit_41.text())
                bx_rein = self._parse_float(self.lineEdit_15.text())
                by_rein = self._parse_float(self.lineEdit_16.text())
                mat = self.comboBox_4.currentText().strip().lower()
                pos = self.comboBox_7.currentText().strip().lower()
            
            # Вычисляем dist_ct из ay_main, ay_rein и позиции усилителя
            ay_main = self._parse_float(self.lineEdit_ay_main.text())
            ay_rein = self._parse_float(self.lineEdit_ay_rein.text())
            if "помещен" in pos:
                # Усилитель снаружи, со стороны помещения
                dist_ct = ay_main + ay_rein
            elif "улиц" in pos:
                # Усилитель снаружи, со стороны улицы
                dist_ct = (h_m - ay_main) + ay_rein
            else:
                # Внутри камеры профиля — центры масс совпадают
                dist_ct = 0.0
            
            # Коэффициент приведения материала усилителя
            n = 3.0 if "сталь" in mat else 1.0
            
            if area_r > 0 and area_m > 0:
                # Теорема Штейнера: Ix_tot = Ix1 + A1*d1² + n*(Ix2 + A2*d2²)
                # dist_ct - расстояние между ц.т. частей (мм)
                # d1, d2 - расстояния от общего ц.т. до ц.т. каждой части
                
                if dist_ct > 0:
                    # Общий центр тяжести (от ц.т. первой части)
                    # y1 = 0, y2 = dist_ct (мм)
                    a_priv = area_m + n * area_r
                    y_c = (n * area_r * dist_ct) / a_priv  # мм от ц.т. первой части
                    
                    d1 = y_c / 10.0  # см (смещение первой части от общего ц.т.)
                    d2 = (dist_ct - y_c) / 10.0  # см (смещение второй части)
                    
                    ix_tot = (ix_m + area_m * d1**2) + n * (ix_r + area_r * d2**2)
                    iy_tot = iy_m + n * iy_r  # по оси Y симметрия, перенос не нужен
                    
                    # Крайние волокна (0 = ц.т. первой части)
                    # Первая часть: от -ay_main до (h_m - ay_main)
                    # Вторая часть: от (dist_ct - ay_rein) до (dist_ct + by_rein - ay_rein)
                    if ay_main > 0 and ay_rein > 0:
                        y_min = min(-ay_main, dist_ct - ay_rein)
                        y_max = max(h_m - ay_main, dist_ct + by_rein - ay_rein)
                    else:
                        # Fallback на геометрические центры
                        y_min = min(-h_m / 2.0, dist_ct - by_rein / 2.0)
                        y_max = max(h_m / 2.0, dist_ct + by_rein / 2.0)
                    
                    # Расстояния от общего ц.т. до крайних волокон (см)
                    c_top = abs(y_max - y_c) / 10.0
                    c_bot = abs(y_c - y_min) / 10.0
                    c_max = max(c_top, c_bot)
                    
                    wx_tot = ix_tot / c_max if c_max > 0 else 0
                else:
                    # dist_ct = 0: просто складываем (консервативно, как раньше)
                    ix_tot = ix_m + n * ix_r
                    iy_tot = iy_m + n * iy_r
                    wx_tot = self._calc_wx_simple(ix_tot, h_m, self.lineEdit_ay_main)
                
                wy_tot = self._calc_wy(iy_tot, b_m, bx_rein, self.lineEdit_ax_main_2, self.lineEdit_ax_rein_2)
                
                self.lineEdit_42.setText(f"{ix_tot:.2f}")
                self.lineEdit_43.setText(f"{iy_tot:.2f}")
                self.lineEdit_44.setText(f"{wx_tot:.2f}")
                self.lineEdit_45.setText(f"{wy_tot:.2f}")
                self._area_tot_m = area_m + n * area_r
                return

        # Стандартный расчет (усилитель)
        if self.checkBox_2.isChecked():
            # Характеристики введены вручную
            ix_r = self._parse_float(self.lineEdit_9.text())
            iy_r = self._parse_float(self.lineEdit_10.text())
            area_r = self._parse_float(self.lineEdit_13.text())
            bx_rein = self._parse_float(self.lineEdit_55.text())
            by_rein = self._parse_float(self.lineEdit_59.text())
            mat = self.comboBox_4.currentText().strip().lower()
            pos = self.comboBox_7.currentText().strip().lower()
        elif self.checkBox_5.isChecked():
            # Геометрия рассчитана автоматически
            ix_r = self._parse_float(self.lineEdit_37.text())
            iy_r = self._parse_float(self.lineEdit_38.text())
            wx_r = self._parse_float(self.lineEdit_39.text())
            wy_r = self._parse_float(self.lineEdit_40.text())
            area_r = self._parse_float(self.lineEdit_41.text())
            bx_rein = self._parse_float(self.lineEdit_15.text())
            by_rein = self._parse_float(self.lineEdit_16.text())
            mat = self.comboBox_4.currentText().strip().lower()
            pos = self.comboBox_7.currentText().strip().lower()
        else:
            # Усилитель не выбран — просто дублируем введённые значения
            self.lineEdit_42.setText(f"{ix_m:.2f}")
            self.lineEdit_43.setText(f"{iy_m:.2f}")
            self.lineEdit_44.setText(f"{wx_m:.2f}")
            self.lineEdit_45.setText(f"{wy_m:.2f}")
            return

        # Коэффициент приведения (сталь в 3 раза жестче алюминия)
        n = 3.0 if "сталь" in mat else 1.0
        
        # Площадь стойки (см2)
        area_m = self._parse_float(self.lineEdit_6.text())
        
        # Приведенная площадь для гибкости
        area_tot_m = area_m + n * area_r

        # Базовая проверка
        if area_r <= 0 or area_m <= 0:
            ix_tot = ix_m + n * ix_r
            iy_tot = iy_m + n * iy_r
            wx_tot = self._calc_wx_simple(ix_tot, h_m, self.lineEdit_ay_main)
            wy_tot = self._calc_wy(iy_tot, b_m, bx_rein, self.lineEdit_ax_main_2, self.lineEdit_ax_rein_2)
            self.lineEdit_42.setText(f"{ix_tot:.2f}")
            self.lineEdit_43.setText(f"{iy_tot:.2f}")
            self.lineEdit_44.setText(f"{wx_tot:.2f}")
            self.lineEdit_45.setText(f"{wy_tot:.2f}")
            # Сохраняем приведенную площадь для get_result
            self._area_tot_m = area_tot_m
            return
        
        self._area_tot_m = area_tot_m
        
        # Расчёт в зависимости от положения усилителя
        if pos == "внутри камеры профиля":
            # Расстояние между ЦТ стойки и усилителя (мм), введённое пользователем
            dist_ct = self._parse_float(self.lineEdit_ayay_st.text())
            if dist_ct > 0 and area_m > 0 and area_r > 0:
                a_priv = area_m + n * area_r
                y_c = (n * area_r * dist_ct) / a_priv  # мм от ЦТ профиля
                d1 = y_c / 10.0  # см
                d2 = (dist_ct - y_c) / 10.0  # см
                ix_tot = (ix_m + area_m * d1**2) + n * (ix_r + area_r * d2**2)
                iy_tot = iy_m + n * iy_r
                # Крайние волокна
                ay_main = self._parse_float(self.lineEdit_ay_main.text())
                ay_rein = self._parse_float(self.lineEdit_ay_rein.text())
                if ay_main > 0 and ay_rein > 0:
                    y_min = min(-ay_main, dist_ct - ay_rein)
                    y_max = max(h_m - ay_main, dist_ct + by_rein - ay_rein)
                else:
                    y_min = min(-h_m / 2.0, dist_ct - by_rein / 2.0)
                    y_max = max(h_m / 2.0, dist_ct + by_rein / 2.0)
                c_top = abs(y_max - y_c) / 10.0
                c_bot = abs(y_c - y_min) / 10.0
                c_max = max(c_top, c_bot)
                wx_tot = ix_tot / c_max if c_max > 0 else 0
            else:
                # dist_ct = 0: центры масс совпадают, простое сложение
                ix_tot = ix_m + n * ix_r
                iy_tot = iy_m + n * iy_r
                wx_tot = self._calc_wx_simple(ix_tot, h_m, self.lineEdit_ay_main)
            wy_tot = self._calc_wy(iy_tot, b_m, bx_rein, self.lineEdit_ax_main_2, self.lineEdit_ax_rein_2)
            
        elif pos == "снаружи, улица" or pos == "снаружи, помещение":
            # Усилитель снаружи (улица или помещение)
            if by_rein <= 0:
                by_rein = h_m if h_m > 0 else 1.0
            
            # Проверяем, заданы ли ay (центры масс)
            ay_main = self._parse_float(self.lineEdit_ay_main.text())
            ay_rein = self._parse_float(self.lineEdit_ay_rein.text())
            
            if ay_main > 0 and ay_rein > 0:
                # Используем реальные центры масс
                if "помещен" in pos:
                    dist_ct = ay_main + ay_rein
                else:  # улица
                    dist_ct = (h_m - ay_main) + ay_rein
                
                a_priv = area_m + n * area_r
                y_c = (n * area_r * dist_ct) / a_priv  # от ц.т. профиля
                
                d_al = y_c / 10.0  # см
                d_st = (dist_ct - y_c) / 10.0  # см
                
                ix_tot = (ix_m + area_m * d_al**2) + n * (ix_r + area_r * d_st**2)
                iy_tot = iy_m + n * iy_r
                
                # Крайние волокна (0 = ц.т. профиля, dist_ct = ц.т. усилителя)
                # Грани профиля: от -ay_main до (h_m - ay_main)
                # Грани усилителя: от (dist_ct - ay_rein) до (dist_ct + by_rein - ay_rein)
                y_min_abs = min(-ay_main, dist_ct - ay_rein)
                y_max_abs = max(h_m - ay_main, dist_ct + by_rein - ay_rein)
                
                c_top = abs(y_max_abs - y_c) / 10.0
                c_bot = abs(y_c - y_min_abs) / 10.0
                c_max = max(c_top, c_bot)
                
                wx_tot = ix_tot / c_max if c_max > 0 else 0
            else:
                # Fallback: геометрические центры
                if "помещен" in pos:
                    y_st = h_m / 2.0 + by_rein / 2.0
                else:  # улица
                    y_st = -(h_m / 2.0 + by_rein / 2.0)
                
                y_al = 0.0
                a_priv = area_m + n * area_r
                y_c = (area_m * y_al + n * area_r * y_st) / a_priv
                
                d_al = (y_c - y_al) / 10.0
                d_st = (y_st - y_c) / 10.0
                
                ix_tot = (ix_m + area_m * d_al**2) + n * (ix_r + area_r * d_st**2)
                iy_tot = iy_m + n * iy_r
                
                if "помещен" in pos:
                    y_min = -h_m / 2.0
                    y_max = h_m / 2.0 + by_rein
                else:
                    y_min = -(h_m / 2.0 + by_rein)
                    y_max = h_m / 2.0
                
                c_front = abs(y_c - y_min) / 10.0
                c_back = abs(y_max - y_c) / 10.0
                wx_tot = ix_tot / max(c_front, c_back) if max(c_front, c_back) > 0 else 0
            
            iy_tot = iy_m + n * iy_r
            wy_tot = self._calc_wy(iy_tot, b_m, bx_rein, self.lineEdit_ax_main_2, self.lineEdit_ax_rein_2)
            
        else:
            # На случай пустых или других строк - считаем как внутри камеры
            ix_tot = ix_m + n * ix_r
            iy_tot = iy_m + n * iy_r
            wx_tot = self._calc_wx_simple(ix_tot, h_m, self.lineEdit_ay_main)
            wy_tot = self._calc_wy(iy_tot, b_m, bx_rein, self.lineEdit_ax_main_2, self.lineEdit_ax_rein_2)
        
        self.lineEdit_42.setText(f"{ix_tot:.2f}")
        self.lineEdit_43.setText(f"{iy_tot:.2f}")
        self.lineEdit_44.setText(f"{wx_tot:.2f}")
        self.lineEdit_45.setText(f"{wy_tot:.2f}")


    def _update_totals_transom(self):
        # Ригель
        ix_t = self._parse_float(self.lineEdit_31.text())
        iy_t = self._parse_float(self.lineEdit_32.text())
        wx_t = self._parse_float(self.lineEdit_33.text())
        wy_t = self._parse_float(self.lineEdit_34.text())
        h_t = self._parse_float(self.lineEdit_36.text())  # глубина (мм)
        b_t = self._parse_float(self.lineEdit_58.text())  # ширина (мм)
        area_t = self._parse_float(self.lineEdit_35.text()) # площадь (см2)
        
        ix_r, iy_r, wx_r, wy_r, area_r = 0.0, 0.0, 0.0, 0.0, 0.0
        bx_rein = b_t
        by_rein = 0.0
        mat = "алюминий"
        pos = "внутри камеры профиля"
        
        if self.comboBox_sys.currentIndex() == 2:
            # Режим терморазрыва (штапиковый профиль)
            if self.checkBox_3.isChecked():
                ix_r = self._parse_float(self.lineEdit_18.text())
                iy_r = self._parse_float(self.lineEdit_19.text())
                area_r = self._parse_float(self.lineEdit_23.text())
                bx_rein = self._parse_float(self.lineEdit_56.text())
                by_rein = self._parse_float(self.lineEdit_60.text())
                mat = self.comboBox_5.currentText().strip().lower()
                pos = self.comboBox_6.currentText().strip().lower()
            elif self.checkBox_6.isChecked():
                ix_r = self._parse_float(self.lineEdit_51.text())
                iy_r = self._parse_float(self.lineEdit_47.text())
                area_r = self._parse_float(self.lineEdit_50.text())
                bx_rein = self._parse_float(self.lineEdit_27.text())
                by_rein = self._parse_float(self.lineEdit_28.text())
                mat = self.comboBox_5.currentText().strip().lower()
                pos = self.comboBox_6.currentText().strip().lower()
            
            # Вычисляем dist_ct из ay_main_t, ay_rein_t и позиции усилителя
            ay_main = self._parse_float(self.lineEdit_ay_main_t.text())
            ay_rein = self._parse_float(self.lineEdit_ay_rein_t.text())
            if "помещен" in pos:
                # Усилитель снаружи, со стороны помещения
                dist_ct = ay_main + ay_rein
            elif "улиц" in pos:
                # Усилитель снаружи, со стороны улицы
                dist_ct = (h_t - ay_main) + ay_rein
            else:
                # Внутри камеры профиля — центры масс совпадают
                dist_ct = 0.0
            
            # Коэффициент приведения материала усилителя
            n = 3.0 if "сталь" in mat else 1.0
            
            if area_r > 0 and area_t > 0:
                if dist_ct > 0:
                    a_priv = area_t + n * area_r
                    y_c = (n * area_r * dist_ct) / a_priv
                    
                    d1 = y_c / 10.0
                    d2 = (dist_ct - y_c) / 10.0
                    
                    ix_tot = (ix_t + area_t * d1**2) + n * (ix_r + area_r * d2**2)
                    iy_tot = iy_t + n * iy_r
                    
                    # Крайние волокна (0 = ц.т. первой части)
                    if ay_main > 0 and ay_rein > 0:
                        y_min = min(-ay_main, dist_ct - ay_rein)
                        y_max = max(h_t - ay_main, dist_ct + by_rein - ay_rein)
                    else:
                        y_min = min(-h_t / 2.0, dist_ct - by_rein / 2.0)
                        y_max = max(h_t / 2.0, dist_ct + by_rein / 2.0)
                    
                    c_top = abs(y_max - y_c) / 10.0
                    c_bot = abs(y_c - y_min) / 10.0
                    c_max = max(c_top, c_bot)
                    
                    wx_tot = ix_tot / c_max if c_max > 0 else 0
                else:
                    ix_tot = ix_t + n * ix_r
                    iy_tot = iy_t + n * iy_r
                    wx_tot = self._calc_wx_simple(ix_tot, h_t, self.lineEdit_ay_main_t)
                
                wy_tot = self._calc_wy(iy_tot, b_t, bx_rein, self.lineEdit_ax_main_3, self.lineEdit_ax_rein_3)
                
                self.lineEdit_46.setText(f"{ix_tot:.2f}")
                self.lineEdit_52.setText(f"{iy_tot:.2f}")
                self.lineEdit_53.setText(f"{wx_tot:.2f}")
                self.lineEdit_54.setText(f"{wy_tot:.2f}")
                self._area_tot_t = area_t + n * area_r
                return

        if self.checkBox_3.isChecked():
            # Характеристики введены вручную
            ix_r = self._parse_float(self.lineEdit_18.text())
            iy_r = self._parse_float(self.lineEdit_19.text())
            area_r = self._parse_float(self.lineEdit_23.text())
            bx_rein = self._parse_float(self.lineEdit_56.text())
            by_rein = self._parse_float(self.lineEdit_60.text())
            mat = self.comboBox_5.currentText().strip().lower()
            pos = self.comboBox_6.currentText().strip().lower()
        elif self.checkBox_6.isChecked():
            # Геометрия рассчитана автоматически
            ix_r = self._parse_float(self.lineEdit_51.text())
            iy_r = self._parse_float(self.lineEdit_47.text())
            wx_r = self._parse_float(self.lineEdit_48.text())
            wy_r = self._parse_float(self.lineEdit_49.text())
            area_r = self._parse_float(self.lineEdit_50.text())
            bx_rein = self._parse_float(self.lineEdit_27.text())
            by_rein = self._parse_float(self.lineEdit_28.text())
            mat = self.comboBox_5.currentText().strip().lower()
            pos = self.comboBox_6.currentText().strip().lower()
        else:
            # Усилитель не выбран — просто дублируем введённые значения
            self.lineEdit_46.setText(f"{ix_t:.2f}")
            self.lineEdit_52.setText(f"{iy_t:.2f}")
            self.lineEdit_53.setText(f"{wx_t:.2f}")
            self.lineEdit_54.setText(f"{wy_t:.2f}")
            return
        
        # Коэффициент приведения
        n = 3.0 if "сталь" in mat else 1.0
        
        # Площадь ригеля (см2)
        area_t_base = self._parse_float(self.lineEdit_35.text())
        
        # Приведенная площадь для гибкости
        area_tot_t = area_t_base + n * area_r

        # Базовая проверка
        if area_r <= 0 or area_t_base <= 0:
            ix_tot = ix_t + n * ix_r
            iy_tot = iy_t + n * iy_r
            wx_tot = self._calc_wx_simple(ix_tot, h_t, self.lineEdit_ay_main_t)
            wy_tot = self._calc_wy(iy_tot, b_t, bx_rein, self.lineEdit_ax_main_3, self.lineEdit_ax_rein_3)
            self.lineEdit_46.setText(f"{ix_tot:.2f}")
            self.lineEdit_52.setText(f"{iy_tot:.2f}")
            self.lineEdit_53.setText(f"{wx_tot:.2f}")
            self.lineEdit_54.setText(f"{wy_tot:.2f}")
            # Сохраняем приведенную площадь
            self._area_tot_t = area_tot_t
            return
        
        self._area_tot_t = area_tot_t
        
        # Расчёт в зависимости от положения усилителя
        if pos == "внутри камеры профиля":
            # Расстояние между ЦТ ригеля и усилителя (мм), введённое пользователем
            dist_ct = self._parse_float(self.lineEdit_ayay_rig.text())
            if dist_ct > 0 and area_t_base > 0 and area_r > 0:
                a_priv = area_t_base + n * area_r
                y_c = (n * area_r * dist_ct) / a_priv
                d1 = y_c / 10.0
                d2 = (dist_ct - y_c) / 10.0
                ix_tot = (ix_t + area_t_base * d1**2) + n * (ix_r + area_r * d2**2)
                iy_tot = iy_t + n * iy_r
                ay_main = self._parse_float(self.lineEdit_ay_main_t.text())
                ay_rein = self._parse_float(self.lineEdit_ay_rein_t.text())
                if ay_main > 0 and ay_rein > 0:
                    y_min = min(-ay_main, dist_ct - ay_rein)
                    y_max = max(h_t - ay_main, dist_ct + by_rein - ay_rein)
                else:
                    y_min = min(-h_t / 2.0, dist_ct - by_rein / 2.0)
                    y_max = max(h_t / 2.0, dist_ct + by_rein / 2.0)
                c_top = abs(y_max - y_c) / 10.0
                c_bot = abs(y_c - y_min) / 10.0
                c_max = max(c_top, c_bot)
                wx_tot = ix_tot / c_max if c_max > 0 else 0
            else:
                ix_tot = ix_t + n * ix_r
                iy_tot = iy_t + n * iy_r
                wx_tot = self._calc_wx_simple(ix_tot, h_t, self.lineEdit_ay_main_t)
            wy_tot = self._calc_wy(iy_tot, b_t, bx_rein, self.lineEdit_ax_main_3, self.lineEdit_ax_rein_3)
            
        elif pos == "снаружи, улица" or pos == "снаружи, помещение":
            if by_rein <= 0:
                by_rein = h_t if h_t > 0 else 1.0
            
            ay_main = self._parse_float(self.lineEdit_ay_main_t.text())
            ay_rein = self._parse_float(self.lineEdit_ay_rein_t.text())
            
            if ay_main > 0 and ay_rein > 0:
                if "помещен" in pos:
                    dist_ct = ay_main + ay_rein
                else:
                    dist_ct = (h_t - ay_main) + ay_rein
                
                a_priv = area_t_base + n * area_r
                y_c = (n * area_r * dist_ct) / a_priv
                
                d_al = y_c / 10.0
                d_st = (dist_ct - y_c) / 10.0
                
                ix_tot = (ix_t + area_t_base * d_al**2) + n * (ix_r + area_r * d_st**2)
                iy_tot = iy_t + n * iy_r
                
                y_min_abs = min(-ay_main, dist_ct - ay_rein)
                y_max_abs = max(h_t - ay_main, dist_ct + by_rein - ay_rein)
                
                c_top = abs(y_max_abs - y_c) / 10.0
                c_bot = abs(y_c - y_min_abs) / 10.0
                c_max = max(c_top, c_bot)
                
                wx_tot = ix_tot / c_max if c_max > 0 else 0
            else:
                if "помещен" in pos:
                    y_st = h_t / 2.0 + by_rein / 2.0
                else:
                    y_st = -(h_t / 2.0 + by_rein / 2.0)
                
                y_al = 0.0
                a_priv = area_t_base + n * area_r
                y_c = (area_t_base * y_al + n * area_r * y_st) / a_priv
                
                d_al = (y_c - y_al) / 10.0
                d_st = (y_st - y_c) / 10.0
                
                ix_tot = (ix_t + area_t_base * d_al**2) + n * (ix_r + area_r * d_st**2)
                iy_tot = iy_t + n * iy_r
                
                if "помещен" in pos:
                    y_min = -h_t / 2.0
                    y_max = h_t / 2.0 + by_rein
                else:
                    y_min = -(h_t / 2.0 + by_rein)
                    y_max = h_t / 2.0
                
                c_front = abs(y_c - y_min) / 10.0
                c_back = abs(y_max - y_c) / 10.0
                wx_tot = ix_tot / max(c_front, c_back) if max(c_front, c_back) > 0 else 0
            
            iy_tot = iy_t + n * iy_r
            wy_tot = self._calc_wy(iy_tot, b_t, bx_rein, self.lineEdit_ax_main_3, self.lineEdit_ax_rein_3)
            
        else:
            ix_tot = ix_t + n * ix_r
            iy_tot = iy_t + n * iy_r
            wx_tot = self._calc_wx_simple(ix_tot, h_t, self.lineEdit_ay_main_t)
            wy_tot = self._calc_wy(iy_tot, b_t, bx_rein, self.lineEdit_ax_main_3, self.lineEdit_ax_rein_3)
        
        self.lineEdit_46.setText(f"{ix_tot:.2f}")
        self.lineEdit_52.setText(f"{iy_tot:.2f}")
        self.lineEdit_53.setText(f"{wx_tot:.2f}")
        self.lineEdit_54.setText(f"{wy_tot:.2f}")

    def _get_save_dir(self):
        save_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "save_data")
        os.makedirs(save_dir, exist_ok=True)
        return save_dir

    def _collect_mullion_data(self):
        return {
            'art': self.lineEdit.text(),
            'active': self.checkBox.isChecked(),
            'sys_type': self.comboBox_sys.currentIndex(),
            'ay_main': self._parse_float(self.lineEdit_ay_main.text()),
            'ay_rein': self._parse_float(self.lineEdit_ay_rein.text()),
            'ix': self._parse_float(self.lineEdit_4.text()),
            'iy': self._parse_float(self.lineEdit_2.text()),
            'wx': self._parse_float(self.lineEdit_5.text()),
            'wy': self._parse_float(self.lineEdit_3.text()),
            'area': self._parse_float(self.lineEdit_6.text()),
            'depth': self._parse_float(self.lineEdit_7.text()),
            'width': self._parse_float(self.lineEdit_57.text()),
            'rein_active_char': self.checkBox_2.isChecked(),
            'rein_active_geom': self.checkBox_5.isChecked(),
            'rein_art': self.lineEdit_8.text(),
            'rein_material': self.comboBox_4.currentText(),
            'rein_pos': self.comboBox_7.currentText(),
            'rein_ix': self._parse_float(self.lineEdit_9.text()),
            'rein_iy': self._parse_float(self.lineEdit_10.text()),
            'rein_area': self._parse_float(self.lineEdit_13.text()),
            'rein_shape': self.comboBox_8.currentText(),
            'rein_bx': self._parse_float(self.lineEdit_15.text()),
            'rein_by': self._parse_float(self.lineEdit_16.text()),
            'rein_t': self._parse_float(self.lineEdit_17.text()),
            'rein_by_char': self._parse_float(self.lineEdit_55.text()),
            'rein_bx_char': self._parse_float(self.lineEdit_59.text()),
            'ax_main': self._parse_float(self.lineEdit_ax_main_2.text()),
            'ax_rein': self._parse_float(self.lineEdit_ax_rein_2.text()),
            'ayay_st': self._parse_float(self.lineEdit_ayay_st.text()),
        }

    def _apply_mullion_data(self, data):
        self.lineEdit.setText(data.get('art', ''))
        self.checkBox.setChecked(data.get('active', False))
        sys_type = data.get('sys_type', 0)
        if data.get('thermal_active', False) and sys_type == 0:
            sys_type = 2
        self.comboBox_sys.setCurrentIndex(sys_type)
        self.lineEdit_ay_main.setText(f"{data.get('ay_main', 0.0):.2f}")
        self.lineEdit_ay_rein.setText(f"{data.get('ay_rein', 0.0):.2f}")
        self.checkBox_2.setChecked(data.get('rein_active_char', False))
        self.checkBox_5.setChecked(data.get('rein_active_geom', False))
        self.lineEdit_4.setText(f"{data.get('ix', 0):.2f}")
        self.lineEdit_2.setText(f"{data.get('iy', 0):.2f}")
        self.lineEdit_5.setText(f"{data.get('wx', 0):.2f}")
        self.lineEdit_3.setText(f"{data.get('wy', 0):.2f}")
        self.lineEdit_6.setText(f"{data.get('area', 0):.2f}")
        self.lineEdit_7.setText(f"{data.get('depth', 0):.2f}")
        self.lineEdit_57.setText(f"{data.get('width', 50.0):.2f}")
        self.lineEdit_8.setText(data.get('rein_art', ''))
        self.comboBox_4.setCurrentText(data.get('rein_material', 'алюминий'))
        self.comboBox_7.setCurrentText(data.get('rein_pos', 'внутри'))
        self.lineEdit_9.setText(f"{data.get('rein_ix', 0):.2f}")
        self.lineEdit_10.setText(f"{data.get('rein_iy', 0):.2f}")
        self.lineEdit_13.setText(f"{data.get('rein_area', 0):.2f}")
        self.comboBox_8.setCurrentText(data.get('rein_shape', 'Труба'))
        self.lineEdit_15.setText(f"{data.get('rein_bx', 0):.2f}")
        self.lineEdit_16.setText(f"{data.get('rein_by', 0):.2f}")
        self.lineEdit_17.setText(f"{data.get('rein_t', 0):.2f}")
        self.lineEdit_55.setText(f"{data.get('rein_by_char', 0):.2f}")
        self.lineEdit_59.setText(f"{data.get('rein_bx_char', 50.0):.2f}")
        self.lineEdit_ax_main_2.setText(f"{data.get('ax_main', 0.0):.2f}")
        self.lineEdit_ax_rein_2.setText(f"{data.get('ax_rein', 0.0):.2f}")
        self.lineEdit_ayay_st.setText(f"{data.get('ayay_st', 0.0):.2f}")
        self._update_all_states()
        self._calculate_all()

    def _collect_transom_data(self):
        return {
            'art': self.lineEdit_30.text(),
            'active': self.checkBox_4.isChecked(),
            'sys_type': self.comboBox_sys.currentIndex(),
            'ay_main_t': self._parse_float(self.lineEdit_ay_main_t.text()),
            'ay_rein_t': self._parse_float(self.lineEdit_ay_rein_t.text()),
            'ix': self._parse_float(self.lineEdit_31.text()),
            'iy': self._parse_float(self.lineEdit_32.text()),
            'wx': self._parse_float(self.lineEdit_33.text()),
            'wy': self._parse_float(self.lineEdit_34.text()),
            'area': self._parse_float(self.lineEdit_35.text()),
            'depth': self._parse_float(self.lineEdit_36.text()),
            'width': self._parse_float(self.lineEdit_58.text()),
            'rein_active_char': self.checkBox_3.isChecked(),
            'rein_active_geom': self.checkBox_6.isChecked(),
            'rein_art': self.lineEdit_26.text(),
            'rein_material': self.comboBox_5.currentText(),
            'rein_pos': self.comboBox_6.currentText(),
            'rein_ix': self._parse_float(self.lineEdit_18.text()),
            'rein_iy': self._parse_float(self.lineEdit_19.text()),
            'rein_area': self._parse_float(self.lineEdit_23.text()),
            'rein_shape': self.comboBox_9.currentText(),
            'rein_bx': self._parse_float(self.lineEdit_27.text()),
            'rein_by': self._parse_float(self.lineEdit_28.text()),
            'rein_t': self._parse_float(self.lineEdit_29.text()),
            'rein_by_char': self._parse_float(self.lineEdit_56.text()),
            'rein_bx_char': self._parse_float(self.lineEdit_60.text()),
            'ax_main': self._parse_float(self.lineEdit_ax_main_3.text()),
            'ax_rein': self._parse_float(self.lineEdit_ax_rein_3.text()),
            'ayay_rig': self._parse_float(self.lineEdit_ayay_rig.text()),
        }

    def _apply_transom_data(self, data):
        self.lineEdit_30.setText(data.get('art', ''))
        self.checkBox_4.setChecked(data.get('active', False))
        sys_type = data.get('sys_type', 0)
        if data.get('thermal_active', False) and sys_type == 0:
            sys_type = 2
        self.comboBox_sys.setCurrentIndex(sys_type)
        self.lineEdit_ay_main_t.setText(f"{data.get('ay_main_t', 0.0):.2f}")
        self.lineEdit_ay_rein_t.setText(f"{data.get('ay_rein_t', 0.0):.2f}")
        self.checkBox_3.setChecked(data.get('rein_active_char', False))
        self.checkBox_6.setChecked(data.get('rein_active_geom', False))
        self.lineEdit_31.setText(f"{data.get('ix', 0):.2f}")
        self.lineEdit_32.setText(f"{data.get('iy', 0):.2f}")
        self.lineEdit_33.setText(f"{data.get('wx', 0):.2f}")
        self.lineEdit_34.setText(f"{data.get('wy', 0):.2f}")
        self.lineEdit_35.setText(f"{data.get('area', 0):.2f}")
        self.lineEdit_36.setText(f"{data.get('depth', 0):.2f}")
        self.lineEdit_58.setText(f"{data.get('width', 50.0):.2f}")
        self.lineEdit_26.setText(data.get('rein_art', ''))
        self.comboBox_5.setCurrentText(data.get('rein_material', 'алюминий'))
        self.comboBox_6.setCurrentText(data.get('rein_pos', 'внутри'))
        self.lineEdit_18.setText(f"{data.get('rein_ix', 0):.2f}")
        self.lineEdit_19.setText(f"{data.get('rein_iy', 0):.2f}")
        self.lineEdit_23.setText(f"{data.get('rein_area', 0):.2f}")
        self.comboBox_9.setCurrentText(data.get('rein_shape', 'Труба'))
        self.lineEdit_27.setText(f"{data.get('rein_bx', 0):.2f}")
        self.lineEdit_28.setText(f"{data.get('rein_by', 0):.2f}")
        self.lineEdit_29.setText(f"{data.get('rein_t', 0):.2f}")
        self.lineEdit_56.setText(f"{data.get('rein_by_char', 0):.2f}")
        self.lineEdit_60.setText(f"{data.get('rein_bx_char', 50.0):.2f}")
        self.lineEdit_ax_main_3.setText(f"{data.get('ax_main', 0.0):.2f}")
        self.lineEdit_ax_rein_3.setText(f"{data.get('ax_rein', 0.0):.2f}")
        self.lineEdit_ayay_rig.setText(f"{data.get('ayay_rig', 0.0):.2f}")
        self._update_all_states()
        self._calculate_all()

    def _save_mullion(self):
        data = self._collect_mullion_data()
        art = self.lineEdit.text().strip()
        if not art:
            QtWidgets.QMessageBox.warning(self, "Ошибка", "Введите артикул стойки.")
            return
        rein_active = self.checkBox_2.isChecked() or self.checkBox_5.isChecked()
        if rein_active:
            rein_art = self.lineEdit_8.text().strip()
            if rein_art:
                filename = f"{art}+{rein_art}.json"
            else:
                filename = f"{art}.json"
        else:
            filename = f"{art}.json"
        save_dir = self._get_save_dir()
        filepath = os.path.join(save_dir, filename)
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump({'type': 'mullion', 'data': data}, f, ensure_ascii=False, indent=2)
            QtWidgets.QMessageBox.information(self, "Сохранено", f"Профиль стойки сохранён:\n{filename}")
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить:\n{e}")

    def _load_mullion(self):
        save_dir = self._get_save_dir()
        filepath, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Загрузить профиль стойки", save_dir, "JSON файлы (*.json)")
        if not filepath:
            return
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = json.load(f)
            data = content.get('data', content)
            self._apply_mullion_data(data)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Ошибка", f"Не удалось загрузить:\n{e}")

    def _save_transom(self):
        data = self._collect_transom_data()
        art = self.lineEdit_30.text().strip()
        if not art:
            QtWidgets.QMessageBox.warning(self, "Ошибка", "Введите артикул ригеля.")
            return
        rein_active = self.checkBox_3.isChecked() or self.checkBox_6.isChecked()
        if rein_active:
            rein_art = self.lineEdit_26.text().strip()
            if rein_art:
                filename = f"{art}+{rein_art}.json"
            else:
                filename = f"{art}.json"
        else:
            filename = f"{art}.json"
        save_dir = self._get_save_dir()
        filepath = os.path.join(save_dir, filename)
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump({'type': 'transom', 'data': data}, f, ensure_ascii=False, indent=2)
            QtWidgets.QMessageBox.information(self, "Сохранено", f"Профиль ригеля сохранён:\n{filename}")
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить:\n{e}")

    def _load_transom(self):
        save_dir = self._get_save_dir()
        filepath, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Загрузить профиль ригеля", save_dir, "JSON файлы (*.json)")
        if not filepath:
            return
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = json.load(f)
            data = content.get('data', content)
            self._apply_transom_data(data)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Ошибка", f"Не удалось загрузить:\n{e}")

    def _import_from_dxf(self):
        """Открывает диалог импорта из DXF и заполняет поля профиля."""
        dlg = ImportDxfDialog(self)
        if dlg.exec_() != QtWidgets.QDialog.Accepted:
            return
        r = dlg.get_result()
        if r is None:
            return

        profile_type = r.get('profile_type', 0)

        rein = r.get('reinforcement')

        if profile_type == 0:
            # Стойка
            self.checkBox.setChecked(True)
            self.lineEdit.setText(r['article'])
            self.lineEdit_4.setText(f"{r['jx']:.2f}")
            self.lineEdit_2.setText(f"{r['jy']:.2f}")
            self.lineEdit_5.setText(f"{r['wx']:.2f}")
            self.lineEdit_3.setText(f"{r['wy']:.2f}")
            self.lineEdit_6.setText(f"{r['area_metal']:.2f}")
            self.lineEdit_7.setText(f"{r.get('ly', r.get('height', 0)):.2f}")
            self.lineEdit_57.setText(f"{r.get('bx', r.get('width', 0)):.2f}")
            self.lineEdit_ay_main.setText(f"{r.get('ay', 0):.2f}")
            self.lineEdit_ax_main_2.setText(f"{r.get('ax', 0):.2f}")
            # Если в блоке есть усилитель — заполнить усилитель стойки
            if rein:
                self.checkBox_2.setChecked(True)
                self.lineEdit_8.setText(rein['article'])
                self.lineEdit_9.setText(f"{rein['jx']:.2f}")
                self.lineEdit_10.setText(f"{rein['jy']:.2f}")
                self.lineEdit_13.setText(f"{rein['area_metal']:.2f}")
                self.lineEdit_55.setText(f"{rein.get('ly', 0):.2f}")
                self.lineEdit_59.setText(f"{rein.get('bx', 0):.2f}")
                self.lineEdit_ay_rein.setText(f"{rein.get('ay', 0):.2f}")
                self.lineEdit_ax_rein_2.setText(f"{rein.get('ax', 0):.2f}")
        elif profile_type == 1:
            # Ригель
            self.checkBox_4.setChecked(True)
            self.lineEdit_30.setText(r['article'])
            self.lineEdit_31.setText(f"{r['jx']:.2f}")
            self.lineEdit_32.setText(f"{r['jy']:.2f}")
            self.lineEdit_33.setText(f"{r['wx']:.2f}")
            self.lineEdit_34.setText(f"{r['wy']:.2f}")
            self.lineEdit_35.setText(f"{r['area_metal']:.2f}")
            self.lineEdit_36.setText(f"{r.get('ly', r.get('height', 0)):.2f}")
            self.lineEdit_58.setText(f"{r.get('bx', r.get('width', 0)):.2f}")
            self.lineEdit_ay_main_t.setText(f"{r.get('ay', 0):.2f}")
            self.lineEdit_ax_main_3.setText(f"{r.get('ax', 0):.2f}")
            # Если в блоке есть усилитель — заполнить усилитель ригеля
            if rein:
                self.checkBox_3.setChecked(True)
                self.lineEdit_26.setText(rein['article'])
                self.lineEdit_18.setText(f"{rein['jx']:.2f}")
                self.lineEdit_19.setText(f"{rein['jy']:.2f}")
                self.lineEdit_23.setText(f"{rein['area_metal']:.2f}")
                self.lineEdit_56.setText(f"{rein.get('ly', 0):.2f}")
                self.lineEdit_60.setText(f"{rein.get('bx', 0):.2f}")
                self.lineEdit_ay_rein_t.setText(f"{rein.get('ay', 0):.2f}")
                self.lineEdit_ax_rein_3.setText(f"{rein.get('ax', 0):.2f}")
        elif profile_type == 2:
            # Усилитель стойки — если блок составной, берём данные усилителя
            src = rein if rein else r
            self.checkBox_2.setChecked(True)
            self.lineEdit_8.setText(src['article'])
            self.lineEdit_9.setText(f"{src['jx']:.2f}")
            self.lineEdit_10.setText(f"{src['jy']:.2f}")
            self.lineEdit_13.setText(f"{src['area_metal']:.2f}")
            self.lineEdit_55.setText(f"{src.get('ly', src.get('height', 0)):.2f}")
            self.lineEdit_59.setText(f"{src.get('bx', src.get('width', 0)):.2f}")
            self.lineEdit_ay_rein.setText(f"{src.get('ay', 0):.2f}")
            self.lineEdit_ax_rein_2.setText(f"{src.get('ax', 0):.2f}")
        elif profile_type == 3:
            # Усилитель ригеля — если блок составной, берём данные усилителя
            src = rein if rein else r
            self.checkBox_3.setChecked(True)
            self.lineEdit_26.setText(src['article'])
            self.lineEdit_18.setText(f"{src['jx']:.2f}")
            self.lineEdit_19.setText(f"{src['jy']:.2f}")
            self.lineEdit_23.setText(f"{src['area_metal']:.2f}")
            self.lineEdit_56.setText(f"{src.get('ly', src.get('height', 0)):.2f}")
            self.lineEdit_60.setText(f"{src.get('bx', src.get('width', 0)):.2f}")
            self.lineEdit_ay_rein_t.setText(f"{src.get('ay', 0):.2f}")
            self.lineEdit_ax_rein_3.setText(f"{src.get('ax', 0):.2f}")

        self._calculate_all()

    def get_result(self):
        m_rein_active = self.checkBox_2.isChecked() or self.checkBox_5.isChecked()
        t_rein_active = self.checkBox_3.isChecked() or self.checkBox_6.isChecked()
        
        # Получаем актуальные значения area_tot (расчеты уже были в _calculate_all)
        # Для стойки
        mat_m = self.comboBox_4.currentText().strip().lower()
        n_m = 3.0 if "сталь" in mat_m else 1.0
        area_m = self._parse_float(self.lineEdit_6.text())
        if self.checkBox_2.isChecked():
            area_r_m = self._parse_float(self.lineEdit_13.text())
        elif self.checkBox_5.isChecked():
            area_r_m = self._parse_float(self.lineEdit_41.text())
        else:
            area_r_m = 0
        
        # Если усилитель не выбран, площадь равна площади основной стойки
        if not m_rein_active:
            area_tot_m = area_m
        else:
            area_tot_m = area_m + n_m * area_r_m

        # Для ригеля
        mat_t = self.comboBox_5.currentText().strip().lower()
        n_t = 3.0 if "сталь" in mat_t else 1.0
        area_t = self._parse_float(self.lineEdit_35.text())
        if self.checkBox_3.isChecked():
            area_r_t = self._parse_float(self.lineEdit_23.text())
        elif self.checkBox_6.isChecked():
            area_r_t = self._parse_float(self.lineEdit_50.text())
        else:
            area_r_t = 0
        
        if not t_rein_active:
            area_tot_t = area_t
        else:
            area_tot_t = area_t + n_t * area_r_t
        
        m_rein_art = ""
        if self.checkBox_2.isChecked():
            m_rein_art = self.lineEdit_8.text()
        elif self.checkBox_5.isChecked():
            # Если геометрия, формируем описание (например: труба 40х40)
            shape = self.comboBox_8.currentText().lower()
            bx = self.lineEdit_15.text()
            by = self.lineEdit_16.text()
            m_rein_art = f"{shape} {bx}х{by}"

        t_rein_art = ""
        if self.checkBox_3.isChecked():
            t_rein_art = self.lineEdit_26.text()
        elif self.checkBox_6.isChecked():
            shape = self.comboBox_9.currentText().lower()
            bx = self.lineEdit_27.text()
            by = self.lineEdit_28.text()
            t_rein_art = f"{shape} {bx}х{by}"

        return {
            'mullion': {
                'art': self.lineEdit.text(),
                'ix': self._parse_float(self.lineEdit_4.text()),
                'iy': self._parse_float(self.lineEdit_2.text()),
                'wx': self._parse_float(self.lineEdit_5.text()),
                'wy': self._parse_float(self.lineEdit_3.text()),
                'ix_tot': self._parse_float(self.lineEdit_42.text()),
                'iy_tot': self._parse_float(self.lineEdit_43.text()),
                'wx_tot': self._parse_float(self.lineEdit_44.text()),
                'wy_tot': self._parse_float(self.lineEdit_45.text()),
                'area': area_m,
                'area_tot': area_tot_m,
                'depth': self._parse_float(self.lineEdit_7.text()),
                'width': self._parse_float(self.lineEdit_57.text()),
                'active': self.checkBox.isChecked(),
                'sys_type': self.comboBox_sys.currentIndex(),
                'ay_main': self._parse_float(self.lineEdit_ay_main.text()),
                'ay_rein': self._parse_float(self.lineEdit_ay_rein.text()),
                'manual': self.checkBox.isChecked() or m_rein_active,
                'rein_active': m_rein_active,
                'rein_active_char': self.checkBox_2.isChecked(),
                'rein_active_geom': self.checkBox_5.isChecked(),
                'rein_art': m_rein_art,
                'rein_material': self.comboBox_4.currentText(),
                'rein_pos': self.comboBox_7.currentText(),
                'rein_ix': self._parse_float(self.lineEdit_9.text()),
                'rein_iy': self._parse_float(self.lineEdit_10.text()),
                'rein_area': self._parse_float(self.lineEdit_13.text()),
                'rein_shape': self.comboBox_8.currentText(),
                'rein_bx': self._parse_float(self.lineEdit_15.text()),
                'rein_by': self._parse_float(self.lineEdit_16.text()),
                'rein_t': self._parse_float(self.lineEdit_17.text()),
                'rein_by_char': self._parse_float(self.lineEdit_55.text()),
                'rein_bx_char': self._parse_float(self.lineEdit_59.text()),
                'by_m': self._parse_float(self.lineEdit_7.text()),
                'bx_m': self._parse_float(self.lineEdit_57.text()),
            },
            'transom': {
                'art': self.lineEdit_30.text(),
                'ix': self._parse_float(self.lineEdit_31.text()),
                'iy': self._parse_float(self.lineEdit_32.text()),
                'wx': self._parse_float(self.lineEdit_33.text()),
                'wy': self._parse_float(self.lineEdit_34.text()),
                'ix_tot': self._parse_float(self.lineEdit_46.text()),
                'iy_tot': self._parse_float(self.lineEdit_52.text()),
                'wx_tot': self._parse_float(self.lineEdit_53.text()),
                'wy_tot': self._parse_float(self.lineEdit_54.text()),
                'area': area_t,
                'area_tot': area_tot_t,
                'depth': self._parse_float(self.lineEdit_36.text()),
                'width': self._parse_float(self.lineEdit_58.text()),
                'ay_main_t': self._parse_float(self.lineEdit_ay_main_t.text()),
                'ay_rein_t': self._parse_float(self.lineEdit_ay_rein_t.text()),
                'active': self.checkBox_4.isChecked(),
                'manual': self.checkBox_4.isChecked() or t_rein_active,
                'rein_active': t_rein_active,
                'rein_active_char': self.checkBox_3.isChecked(),
                'rein_active_geom': self.checkBox_6.isChecked(),
                'rein_art': t_rein_art,
                'rein_material': self.comboBox_5.currentText(),
                'rein_pos': self.comboBox_6.currentText(),
                'rein_ix': self._parse_float(self.lineEdit_18.text()),
                'rein_iy': self._parse_float(self.lineEdit_19.text()),
                'rein_area': self._parse_float(self.lineEdit_23.text()),
                'rein_shape': self.comboBox_9.currentText(),
                'rein_bx': self._parse_float(self.lineEdit_27.text()),
                'rein_by': self._parse_float(self.lineEdit_28.text()),
                'rein_t': self._parse_float(self.lineEdit_29.text()),
                'rein_by_char': self._parse_float(self.lineEdit_56.text()),
                'rein_bx_char': self._parse_float(self.lineEdit_60.text()),
                'by_t': self._parse_float(self.lineEdit_36.text()),
                'bx_t': self._parse_float(self.lineEdit_58.text()),
            }
        }