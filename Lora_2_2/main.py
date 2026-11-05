import sys
import os
import math
import re
from PyQt5 import QtWidgets, uic, QtCore, QtGui
from data_handler import DataHandler
import calculations


def setup_qt_plugin_path():
    """Явно задает путь к Qt platform plugins для стабильного запуска на Windows."""
    qt_dir = os.path.dirname(QtCore.__file__)
    plugins_dir = os.path.join(qt_dir, "Qt5", "plugins")
    platforms_dir = os.path.join(plugins_dir, "platforms")

    if os.path.isdir(plugins_dir):
        QtWidgets.QApplication.addLibraryPath(plugins_dir)
    if os.path.isdir(platforms_dir):
        os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = platforms_dir


def resource_path(*parts):
    """Абсолютный путь к ресурсу для запуска из исходников и из PyInstaller."""
    if getattr(sys, 'frozen', False):
        base_path = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, *parts)

def fix_ui_file(ui_path):
    """Исправляет несовместимые с PyQt5 перечисления Qt6 в .ui файле"""
    if not os.path.exists(ui_path):
        return
    
    with open(ui_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 1. Сначала очищаем теги <set> и <enum> от длинных префиксов Qt6
    # Но для кнопок QDialogButtonBox в PyQt5 НУЖНО оставить префикс QDialogButtonBox::
    # и они НЕ должны быть в Qt::
    
    # Сначала полностью очищаем от всего лишнего
    def clean_value(val):
        return val.split('::')[-1]

    def process_tag(match):
        tag = match.group(1)
        content = match.group(2)
        # Если это пустой тег, возвращаем как есть
        if not content.strip():
            return f'<{tag}>{content}</{tag}>'
            
        items = content.split('|')
        
        cleaned_items = []
        for item in items:
            item = item.strip()
            if not item: continue
            # Если в оригинале было Qt::, сохраняем Qt:: в начале
            if 'Qt::' in item:
                cleaned_items.append(f"Qt::{clean_value(item)}")
            # Если это кнопки (Cancel, Ok и т.д.), добавляем QDialogButtonBox::
            elif clean_value(item) in ['Ok', 'Cancel', 'Save', 'Close', 'Discard', 'Apply', 'Reset', 'RestoreDefaults', 'Help', 'NoButton', 'Yes', 'No', 'Abort', 'Retry', 'Ignore']:
                cleaned_items.append(f"QDialogButtonBox::{clean_value(item)}")
            else:
                cleaned_items.append(clean_value(item))
        
        return f'<{tag}>{"|".join(cleaned_items)}</{tag}>'

    new_content = re.sub(r'<(set|enum)>([^<]*)</\1>', process_tag, content)
    
    if new_content != content:
        with open(ui_path, 'w', encoding='utf-8') as f:
            f.write(new_content)

class SettingsDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, ui_name="dialog.ui"):
        super(SettingsDialog, self).__init__(parent)
        ui_path = resource_path("UI", ui_name)
        fix_ui_file(ui_path)
        uic.loadUi(ui_path, self)
        # Установка шрифта для диалога
        font = QtGui.QFont("Segoe UI", 10)
        self.setFont(font)
        self.setStyleSheet("QWidget { font-family: 'Segoe UI'; font-size: 10pt; }")

class LoraApp(QtWidgets.QMainWindow):
    def __init__(self, fasad_path=None):
        super(LoraApp, self).__init__()
        # Параметры по умолчанию
        self.settings = {
            'use_alutech': True,
            'address': ""
        }

        # Режим программы Фасад: путь к JSON-файлу обмена (None - обычный запуск)
        self.fasad_path = fasad_path
        self._fasad_input = {}
        self._fasad_written = False
        self._calculated_once = False
        self._last_calc = {}
        
        # Установка глобального шрифта Segoe UI 10
        font = QtGui.QFont("Segoe UI", 10)
        QtWidgets.QApplication.setFont(font)
        # Принудительное применение шрифта ко всему приложению через StyleSheet
        self.setStyleSheet("QWidget { font-family: 'Segoe UI'; font-size: 10pt; }")
        
        # Загрузка интерфейса
        ui_path = resource_path("UI", "Lora2.ui")
        fix_ui_file(ui_path)
        uic.loadUi(ui_path, self)
        
        # Ограничения размеров и полосы прокрутки
        #self.setMinimumSize(600, 600)
        #self.setMaximumSize(1340, 1000)
        original_central = self.centralWidget()
        if original_central:
            #original_central.setMinimumSize(1320, 960)
            scroll = QtWidgets.QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
            scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
            scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
            scroll.setWidget(original_central)
            self.setCentralWidget(scroll)
        
        # Инициализация обработчика данных
        self.data_handler = DataHandler(resource_path("data", "profiles.xlsx"))
        success, msg = self.data_handler.load_profiles()
        if not success:
            QtWidgets.QMessageBox.warning(self, "Ошибка", f"Ошибка загрузки профилей: {msg}")
        
        self.setup_ui()
        self.manual_profile_data = None
        self.update_profile_lists() # Принудительное заполнение списков при старте
        self.connect_signals()

        # Кнопка "Экспорт в FS" - только при запуске из программы Фасад
        if hasattr(self, 'pushButton_8'):
            self.pushButton_8.setVisible(bool(self.fasad_path))
        self._load_session()                   # прошлый сеанс (поля ввода)
        if self.fasad_path:
            self._load_fasad_input()

    def setup_ui(self):
        """Настройка начальных значений элементов UI"""
        # Заполняем только те списки, которые зависят от внешних данных
        # comboBox_11 (Система)
        if self.data_handler.system_names:
            self.comboBox_11.clear()
            self.comboBox_11.addItems(self.data_handler.system_names)
        
        # Остальные comboBox (ветровые районы и т.д.) уже заполнены в Lora2.ui
        # Поэтому мы их НЕ ТРОГАЕМ по просьбе пользователя.
        self.setup_numeric_validators()

    def setup_numeric_validators(self):
        """Ограничиваем ввод в расчетные поля только числовыми значениями."""
        number_validator = QtGui.QRegularExpressionValidator(
            QtCore.QRegularExpression(r"^\d*(?:[\.,]\d*)?$")
        )

        numeric_input_fields = [
            "lineEdit", "lineEdit_2", "lineEdit_3", "lineEdit_4", "lineEdit_5",
            "lineEdit_6", "lineEdit_7", "lineEdit_8", "lineEdit_9", "lineEdit_10",
            "lineEdit_11", "lineEdit_12"
        ]

        for field_name in numeric_input_fields:
            widget = getattr(self, field_name, None)
            if isinstance(widget, QtWidgets.QLineEdit):
                widget.setValidator(number_validator)

    def connect_signals(self):
        """Привязка сигналов к слотам"""
        self.pushButton_6.clicked.connect(self.calculate)
        self.pushButton_7.clicked.connect(self.show_settings)
        self.pushButton_10.clicked.connect(self.show_schema)
        # Пересчет при смене профиля вручную
        self.comboBox_9.currentIndexChanged.connect(self.recalculate_verification)
        self.comboBox_10.currentIndexChanged.connect(self.recalculate_verification)
        # Обновление списков профилей при смене системы
        self.comboBox_11.currentIndexChanged.connect(self.update_profile_lists)
        self.comboBox_11.currentIndexChanged.connect(self.calculate)
        self.comboBox_13.currentIndexChanged.connect(self.calculate)
        
        # Кнопка ручного ввода характеристик профиля
        if hasattr(self, 'toolButton'):
            self.toolButton.clicked.connect(self.show_new_profile_dialog)

        # Передача результата в программу Фасад
        if hasattr(self, 'pushButton_8'):
            self.pushButton_8.clicked.connect(self.export_to_fasad)

        # Перильная нагрузка только для однопролетной балки
        self.comboBox_6.currentIndexChanged.connect(self.update_railing_availability)
        self.update_railing_availability()

    def update_railing_availability(self):
        """Для двухпролетной балки отключаем перильную нагрузку (только 'Нет')."""
        beam_text = self.comboBox_6.currentText().strip().lower()
        is_two_span = "двухпролет" in beam_text

        if is_two_span:
            idx_no = self.comboBox_7.findText("Нет")
            if idx_no >= 0:
                self.comboBox_7.setCurrentIndex(idx_no)
            self.comboBox_7.setEnabled(False)
        else:
            self.comboBox_7.setEnabled(True)

    def update_profile_lists(self):
        """Обновление comboBox_9 и comboBox_10 при смене системы"""
        system = self.comboBox_11.currentText()
        
        # Если переключаемся с "Ручной ввод" на реальную систему,
        # сбрасываем сохраненные данные ручного ввода
        if system != "Ручной ввод" and hasattr(self, 'manual_profile_data'):
            self.manual_profile_data = None
        
        # --- Логика из ТЗ: выбор марки алюминия и настроек в зависимости от системы ---
        if system.upper().startswith("ALT"):
            # Марка алюминия для ALT
            target_mark = "6063 T6 (Алютех) R=122 МПа"
            idx = self.comboBox_13.findText(target_mark)
            if idx >= 0:
                self.comboBox_13.setCurrentIndex(idx)
            else:
                # Если такой марки нет в списке, добавляем её
                self.comboBox_13.addItem(target_mark)
                self.comboBox_13.setCurrentIndex(self.comboBox_13.count() - 1)
            
            # Учитывать коэффициенты k1. K2. (checkBox1 в setting2.ui)
            self.settings['use_alutech'] = True
        else:
            # Марка алюминия для остальных
            target_mark = "6063 / АД33 (T нет) R=120 МПа"
            idx = self.comboBox_13.findText(target_mark)
            if idx >= 0:
                self.comboBox_13.setCurrentIndex(idx)
            else:
                self.comboBox_13.addItem(target_mark)
                self.comboBox_13.setCurrentIndex(self.comboBox_13.count() - 1)
            
            # НЕ учитывать коэффициенты k1. K2.
            self.settings['use_alutech'] = False

        mullions = self.data_handler.get_mullions(system)
        transoms = self.data_handler.get_transoms(system)

        def normalize_art(art):
            return str(art).replace(" ", "").strip()
        
        self.comboBox_9.clear()
        for m in mullions:
            # Форматирование до 2 знаков после запятой
            self.comboBox_9.addItem(
                f"{m['art']} | {m['depth']} | Ix={m['ix']:.2f} | Wx={m['wx']:.2f}",
                normalize_art(m['art'])
            )
            
        self.comboBox_10.clear()
        for t in transoms:
            # Форматирование до 2 знаков после запятой
            self.comboBox_10.addItem(
                f"{t['art']} | {t['depth']} | Ix={t['ix']:.2f} | Iy={t['iy']:.2f} | Wx={t['wx']:.2f}",
                normalize_art(t['art'])
            )

    def recalculate_verification(self):
        """Пересчет только проверочной части"""
        if not hasattr(self, '_is_calculating') or not self._is_calculating:
            # Чтобы избежать бесконечного цикла при изменении индекса comboBox в calculate
            self.calculate(auto_select=False)

    def show_schema(self):
        """Показ схемы Shema.png"""
        schema_path = resource_path("images", "Shema.png")
        if os.path.exists(schema_path):
            os.startfile(schema_path)
        else:
            QtWidgets.QMessageBox.warning(self, "Ошибка", f"Файл не найден: {schema_path}")

    def show_settings(self):
        """Окно параметров программы"""
        # Приоритет UI\setting2.ui, затем UI\setting.ui, затем UI\dialog.ui
        ui_name = "dialog.ui"
        if os.path.exists(resource_path("UI", "setting2.ui")):
            ui_name = "setting2.ui"
        elif os.path.exists(resource_path("UI", "setting.ui")):
            ui_name = "setting.ui"
            
        dialog = SettingsDialog(self, ui_name=ui_name)
        
        # Чекбокс может называться checkBox1 или checkBox
        cb = getattr(dialog, "checkBox1", None) or getattr(dialog, "checkBox", None)
        # Поле адреса может быть QTextEdit (textEdit) или QLineEdit (lineEdit)
        addr_field = getattr(dialog, "textEdit", None) or getattr(dialog, "lineEdit", None)
        
        if cb:
            cb.setChecked(self.settings['use_alutech'])
            
        if addr_field:
            if isinstance(addr_field, QtWidgets.QTextEdit):
                addr_field.setPlainText(self.settings['address'])
            else:
                addr_field.setText(self.settings['address'])
        
        if dialog.exec_() == QtWidgets.QDialog.Accepted:
            if cb:
                self.settings['use_alutech'] = cb.isChecked()
            
            if addr_field:
                if isinstance(addr_field, QtWidgets.QTextEdit):
                    self.settings['address'] = addr_field.toPlainText()
                else:
                    self.settings['address'] = addr_field.text()
                    
            # Пересчитываем при изменении настроек
            self.calculate()

    def show_new_profile_dialog(self):
        """Открывает диалог ручного ввода характеристик профиля"""
        from new_profile_dialog import NewProfileDialog
        
        # Получаем текущие выбранные профили
        mullion_art_full = self.comboBox_9.itemData(self.comboBox_9.currentIndex())
        transom_art_full = self.comboBox_10.itemData(self.comboBox_10.currentIndex())
        
        # Очищаем артикулы от усиления перед поиском в базе
        mullion_art = mullion_art_full.split('+')[0].strip() if mullion_art_full else ""
        transom_art = transom_art_full.split('+')[0].strip() if transom_art_full else ""
        
        system = self.comboBox_11.currentText()
        if system == "Ручной ввод":
            # Если уже выбран "Ручной ввод", пробуем взять артикулы из сохраненных данных
            res = getattr(self, 'manual_profile_data', None)
            if res:
                mullion_art = res['mullion']['art']
                transom_art = res['transom']['art']
            # Также пробуем определить систему (допустим, по умолчанию или из настроек)
            # В данном контексте лучше оставить system как есть, или брать из настроек.
        
        mullions = self.data_handler.get_mullions(system)
        transoms = self.data_handler.get_transoms(system)
        
        mullion = next((m for m in mullions if str(m['art']).replace(" ", "").strip() == mullion_art), None)
        transom = next((t for t in transoms if str(t['art']).replace(" ", "").strip() == transom_art), None)
        
        # Создаем и открываем диалог
        manual_data = getattr(self, 'manual_profile_data', None)
        dialog = NewProfileDialog(self, mullion, transom, manual_data)
        if dialog.exec_() == QtWidgets.QDialog.Accepted:
            result = dialog.get_result()
            # Сохраняем результат ручного ввода
            self.manual_profile_data = result
            
            # Обновляем UI в Lora2.ui
            # 1. Стойка (comboBox_9)
            art_m = result['mullion']['art']
            if result['mullion'].get('rein_active'):
                rein_art = result['mullion'].get('rein_art', "")
                if not rein_art: rein_art = "усилитель"
                art_m = f"{art_m}+{rein_art}"
            
            # Формируем расширенную строку с характеристиками для стойки: Ix, Wx, Mass
            ix_m = result['mullion'].get('ix_tot', 0)
            wx_m = result['mullion'].get('wx_tot', 0)
            depth_m = result['mullion'].get('depth', 0)
            display_m = f"{art_m} | {depth_m} | Ix={ix_m:.2f} | Wx={wx_m:.2f}"

            self.comboBox_9.blockSignals(True)
            self.comboBox_9.clear()
            self.comboBox_9.addItem(display_m, art_m)
            self.comboBox_9.setCurrentIndex(0)
            self.comboBox_9.blockSignals(False)
            
            # 2. Ригель (comboBox_10)
            art_t = result['transom']['art']
            if result['transom'].get('rein_active'):
                rein_art_t = result['transom'].get('rein_art', "")
                if not rein_art_t: rein_art_t = "усилитель"
                art_t = f"{art_t}+{rein_art_t}"
            
            # Формируем расширенную строку с характеристиками для ригеля: Ix, Iy, Wx, Mass
            ix_t = result['transom'].get('ix_tot', 0)
            iy_t = result['transom'].get('iy_tot', 0)
            wx_t = result['transom'].get('wx_tot', 0)
            depth_t = result['transom'].get('depth', 0)
            display_t = f"{art_t} | {depth_t} | Ix={ix_t:.2f} | Iy={iy_t:.2f} | Wx={wx_t:.2f}"

            self.comboBox_10.blockSignals(True)
            self.comboBox_10.clear()
            self.comboBox_10.addItem(display_t, art_t)
            self.comboBox_10.setCurrentIndex(0)
            self.comboBox_10.blockSignals(False)
            
            # 3. Добавляем "Ручной ввод" в comboBox_11, если его там нет
            index_manual = self.comboBox_11.findText("Ручной ввод")
            if index_manual == -1:
                self.comboBox_11.addItem("Ручной ввод")
                index_manual = self.comboBox_11.count() - 1
            
            self.comboBox_11.blockSignals(True)
            self.comboBox_11.setCurrentIndex(index_manual)
            self.comboBox_11.blockSignals(False)
            
            # Принудительно вызываем пересчет
            self.calculate()

    def calculate(self, checked=False, auto_select=True):
        """Основная функция расчета"""
        if getattr(self, '_restoring', False):
            return
        if hasattr(self, '_is_calculating') and self._is_calculating:
            return
        
        self._is_calculating = True
        try:
            # Сброс стилей
            self.reset_styles()

            # 1. Извлечение исходных данных
            data = self.get_input_data()
            
            # --- РАСЧЕТ ВЕТРОВОЙ НАГРУЗКИ ---
            area_mullion = (data['b1'] * data['h_m']) / 1_000_000
            wind = calculations.calculate_wind_parameters(
                data['wind_region'], data['terrain_type'], data['h_top'], 
                data['h_building'], data['w_building'], data['is_edge'], area_mullion
            )
            
            self.update_wind_ui(wind)

            # --- РАСЧЕТ СНЕГОВОЙ НАГРУЗКИ И ВЕСА ---
            snow = calculations.calculate_snow_parameters(data['snow_region'], data['angle_deg'])
            
            if self.settings.get('use_alutech', True):
                k1k2 = calculations.calculate_k1_k2(data['l1'], data['l2'], data['h_m'])
            else:
                k1k2 = {'k1': 1.0, 'k2': 1.0}
                
            # Вес стеклопакета НАД ригелем (для расчета ригеля)
            weight_kg = calculations.calculate_glass_weight(data['l1'], data['b2'], data['t_glass'])
            self.update_snow_ui(snow, weight_kg)
            self.lineEdit_28.setText(f"{k1k2['k1']:.2f}")
            self.lineEdit_29.setText(f"{k1k2['k2']:.2f}")
            
            # --- РЕЗУЛЬТИРУЮЩИЕ НАГРУЗКИ (по листу 'Формулы и таблицы' E35-E48) ---
            # qG_norm = F18 (кН/м2), s0_norm = F24 (кН/м2), w_norm = wind['w_peak'] (кН/м2)
            # alpha = data['angle_deg']
            # K10 = k_safety_glass (1.1 для СП РФ, 1.35 для EN)
            # gamma_f_wind = 1.4
            
            qG_norm = data['t_glass'] * 0.025 # F18 в кН/м2
            # В Excel E35/E36/E47/E48 используется F24 (а не F22 напрямую):
            # F24 = F22 * F23 * 1.6 (для СП РФ, если нет ручного ввода в Расчет!L9)
            # snow['s_norm'] = F22*F23, поэтому домножаем на 1.6 для эквивалента F24.
            s0_norm = snow['s_norm'] * 1.6 # F24 в кН/м2
            w_norm = abs(wind['w_peak']) # K27 в кН/м2
            
            alpha_rad = math.radians(data['angle_deg'])
            cos_alpha = math.cos(alpha_rad)
            cos_alpha2 = cos_alpha ** 2
            
            k_safety_glass = 1.1 # K10 для СП РФ
            gamma_f_wind = 1.4 # Коэффициент для ветра
            
            # 1. Стойка (ячейки E35, E36)
            # F2 = B1 (мм), в формуле E35/E36 далее деление на 1000
            f2 = data['b1']
            
            # E36 (Нормативная): (qG*cos(a) + s0*cos2(a)/1.6 + w_norm) * F2
            k1 = k1k2['k1']
            k2 = k1k2['k2']
            q_mullion_norm = (qG_norm * cos_alpha + (s0_norm * cos_alpha2 / 1.6) + w_norm) * f2 / 1000.0 * k1
            # E35 (Расчетная): (qG*cos(a)*K10 + s0*cos2(a) + w_norm*gamma_f) * F2
            q_mullion_calc = (qG_norm * cos_alpha * k_safety_glass + (s0_norm * cos_alpha2) + (w_norm * gamma_f_wind)) * f2 / 1000.0 * k1
            
            self.lineEdit_33.setText(f"{q_mullion_norm:.3f}")
            self.lineEdit_30.setText(f"{q_mullion_calc:.3f}")

            # 2. Ригель (ячейки E47, E48)
            # F6 = IF(F3<F4,F3/2,F4/2)+IF(F3<F5,F3/2,F5/2), где:
            # F3=B2, F4=L1, F5=L2 (в мм)
            f3 = data['b2']
            f4 = data['l1']
            f5 = data['l2']
            f6 = (f3 / 2.0 if f3 < f4 else f4 / 2.0) + (f3 / 2.0 if f3 < f5 else f5 / 2.0)
            
            # E48 (Нормативная)
            q_transom_norm = (qG_norm * cos_alpha + (s0_norm * cos_alpha2 / 1.6) + w_norm) * f6 / 1000.0 * k2
            # E47 (Расчетная)
            q_transom_calc = (qG_norm * cos_alpha * k_safety_glass + (s0_norm * cos_alpha2) + (w_norm * gamma_f_wind)) * f6 / 1000.0 * k2
            
            self.lineEdit_32.setText(f"{q_transom_norm:.3f}")
            self.lineEdit_31.setText(f"{q_transom_calc:.3f}")

            # --- ТРЕБУЕМЫЕ ХАРАКТЕРИСТИКИ ---
            # Расчетное сопротивление алюминия (K11) берем из выбранной марки в UI (R=... МПа)
            r_match = re.search(r"R\s*=\s*(\d+(?:[\.,]\d+)?)", data['alum_mark'])
            if not r_match:
                r_match = re.search(r"Ry\s*=\s*(\d+(?:[\.,]\d+)?)", data['alum_mark'])
            r_calc = float(r_match.group(1).replace(',', '.')) if r_match else 122.0
            
            beam_text = self.comboBox_6.currentText().strip().lower()
            is_single_span = "однопролет" in beam_text

            railing_text = self.comboBox_7.currentText().strip().lower()
            railing_enabled = (
                is_single_span and (
                    "есть" in railing_text
                    or railing_text == "да"
                    or railing_text.startswith("да ")
                    or " да" in railing_text
                )
            )
            railing_norm_kn = 0.8

            m_req = calculations.calculate_mullion_requirements(
                q_mullion_norm,
                q_mullion_calc,
                data['h_m'],
                data['b1'],
                r_calc,
                is_single_span,
                railing_enabled,
                railing_norm_kn,
                data['safety_yn'],
                data['mullion_deflection_ratio']
            )
            self.update_mullion_req_ui(m_req)

            # E52: допустимый прогиб по весу = IF(F3*K16<L16,F3*K16,L16)
            # K16 задается в comboBox_12 (1/n), L16 — lineEdit_10.
            l16_user = float(self.lineEdit_10.text() or 3)
            ratio_k16 = data['transom_deflection_ratio'] if data['transom_deflection_ratio'] > 0 else 200.0
            k16 = 1.0 / ratio_k16
            f_allow_transom_weight = min(data['b2'] * k16, l16_user)

            # Отображение ограничений прогиба ригеля (блок Iy):
            # lineEdit_56 = 1/K16, lineEdit_55 = L16 max
            if abs(ratio_k16 - round(ratio_k16)) < 1e-9:
                ratio_text = str(int(round(ratio_k16)))
            else:
                ratio_text = f"{ratio_k16:.3f}".rstrip("0").rstrip(".")
            self.lineEdit_56.setText(f"1/{ratio_text}")
            self.lineEdit_55.setText(f"{l16_user:g} max")

            p_glass_norm = (weight_kg * 9.81) / 1000.0 # Вес в кН
            
            t_req = calculations.calculate_transom_requirements(
                q_transom_norm,
                q_transom_calc,
                data['b2'],
                data['l1'],
                data['l2'],
                data['angle_deg'],
                data['t_glass'],
                data['a_pad'],
                f_allow_transom_weight,
                r_calc,
                data['safety_yn'],
                is_single_span,
                railing_enabled,
            )
            self.update_transom_req_ui(t_req)

            # Перильная нагрузка (выводим после update_transom_req_ui,
            # чтобы значения в lineEdit_47 не перезаписывались)
            self.handle_railing_load(data)

            # --- ПОДБОР И ПРОВЕРКА ---
            if auto_select:
                self.auto_select_profiles(m_req, t_req)
            
            # Если есть ручной ввод, мы должны учитывать его характеристики
            manual_mullion = None
            manual_transom = None
            if self.comboBox_11.currentText() == "Ручной ввод" and hasattr(self, 'manual_profile_data') and self.manual_profile_data:
                manual_mullion = self.manual_profile_data['mullion']
                manual_transom = self.manual_profile_data['transom']

            self.verify_selected_profiles(q_mullion_norm, q_transom_norm, p_glass_norm, data, m_req, t_req, t_req['f_allow_wind'], f_allow_transom_weight, manual_mullion, manual_transom)

            # Запоминаем результат для передачи в программу Фасад
            self._calculated_once = True
            self._last_calc = {
                'вес_стекла_кг': weight_kg,
                'толщина_стекла_мм': data['t_glass'],
                'q_стойка_норм_кн_м': q_mullion_norm,
                'q_стойка_расч_кн_м': q_mullion_calc,
                'q_ригель_норм_кн_м': q_transom_norm,
                'q_ригель_расч_кн_м': q_transom_calc,
                'прогиб_стойка_доп_мм': m_req['f_allow'],
                'прогиб_ригель_ветер_доп_мм': t_req['f_allow_wind'],
                'прогиб_ригель_вес_доп_мм': t_req['f_allow_weight'],
                'стойка_треб_ix_см4': m_req['ix_req'],
                'стойка_треб_wx_см3': m_req['wx_req'],
                'ригель_треб_ix_см4': t_req['ix_req'],
                'ригель_треб_iy_см4': t_req['iy_req'],
                'ригель_треб_wx_см3': t_req['wx_req'],
            }

        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Ошибка расчета", f"Ошибка: {str(e)}")
        finally:
            self._is_calculating = False

    def get_input_data(self):
        """Сбор всех входных данных из UI"""
        mullion_ratio_text = self.comboBox_8.currentText().strip()
        ratio_match = re.search(r"(\d+(?:[\.,]\d+)?)\s*$", mullion_ratio_text)
        mullion_deflection_ratio = float(ratio_match.group(1).replace(",", ".")) if ratio_match else 200.0

        transom_ratio_text = self.comboBox_12.currentText().strip()
        transom_ratio_match = re.search(r"(\d+(?:[\.,]\d+)?)\s*$", transom_ratio_text)
        transom_deflection_ratio = float(transom_ratio_match.group(1).replace(",", ".")) if transom_ratio_match else 200.0

        return {
            'wind_region': self.comboBox.currentText(),
            'snow_region': self.comboBox_2.currentText(),
            'terrain_type': self.comboBox_3.currentText(),
            'safety_yn': float(self.comboBox_14.currentText().replace(",", ".")),
            'h_top': float(self.lineEdit.text() or 0),
            'h_building': float(self.lineEdit_2.text() or 0),
            'w_building': float(self.lineEdit_3.text() or 0),
            'is_edge': "Край" in self.comboBox_4.currentText() or "Краевая" in self.comboBox_4.currentText(),
            'b1': float(self.lineEdit_4.text() or 0),
            'b2': float(self.lineEdit_5.text() or 0),
            'l1': float(self.lineEdit_6.text() or 0),
            'l2': float(self.lineEdit_7.text() or 0),
            'h_m': float(self.lineEdit_8.text() or 0),
            'angle_deg': float(self.lineEdit_9.text() or 0),
            't_glass': float(self.lineEdit_12.text() or 0),
            'alum_mark': self.comboBox_13.currentText(),
            'a_pad': float(self.lineEdit_11.text() or 100),
            'mullion_deflection_ratio': mullion_deflection_ratio,
            'transom_deflection_ratio': transom_deflection_ratio
        }

    # ---------- обмен с программой Фасад (режим --фасад <файл.json>) ----------

    def _session_file(self):
        return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            'save_data', 'последний_сеанс.json')

    def _save_session(self):
        """Запомнить введённые данные (все поля ввода/списки) для следующего запуска."""
        import json
        data = {'line': {}, 'combo': {}}
        for w in self.findChildren(QtWidgets.QLineEdit):
            if w.objectName():
                data['line'][w.objectName()] = w.text()
        for w in self.findChildren(QtWidgets.QComboBox):
            if w.objectName():
                data['combo'][w.objectName()] = w.currentIndex()
        try:
            os.makedirs(os.path.dirname(self._session_file()), exist_ok=True)
            with open(self._session_file(), 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
        except OSError:
            pass

    def _load_session(self):
        """Восстановить поля прошлого сеанса (если сохранялся)."""
        import json
        try:
            with open(self._session_file(), 'r', encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, ValueError):
            return
        self._restoring = True
        try:
            for name, text in data.get('line', {}).items():
                w = self.findChild(QtWidgets.QLineEdit, name)
                if w is not None:
                    w.setText(text)
            for name, idx in data.get('combo', {}).items():
                w = self.findChild(QtWidgets.QComboBox, name)
                if w is not None and isinstance(idx, int) and 0 <= idx < w.count():
                    w.setCurrentIndex(idx)
        finally:
            self._restoring = False
        self.calculate()

    def _load_fasad_input(self):
        """Читает блок 'вход' из JSON программы Фасад: предзаполнение и заголовок."""
        import json
        try:
            with open(self.fasad_path, 'r', encoding='utf-8') as f:
                self._fasad_input = json.load(f).get('вход', {}) or {}
        except Exception:
            self._fasad_input = {}
        name = self._fasad_input.get('название')
        if name:
            self.setWindowTitle(f"Lora 2.2 — {name}")
        sys_name = self._fasad_input.get('система')
        if sys_name:
            idx = self.comboBox_11.findText(str(sys_name))
            if idx >= 0:
                self.comboBox_11.setCurrentIndex(idx)
        t = self._fasad_input.get('толщина_стекла_мм')
        if t:
            try:
                self.lineEdit_12.setText(f"{float(t):g}")
            except (ValueError, TypeError):
                pass
        dims = self._fasad_input.get('размеры') or {}
        for key, field in (('b1', 'lineEdit_4'), ('b2', 'lineEdit_5'), ('l1', 'lineEdit_6'),
                           ('l2', 'lineEdit_7'), ('h_m', 'lineEdit_8')):
            v = dims.get(key)
            if v is None:
                continue
            try:
                getattr(self, field).setText(f"{float(v):g}")
            except (ValueError, TypeError, AttributeError):
                pass

    def _current_profile(self, combo, kind):
        """Выбранный в comboBox профиль из data_handler: артикул, глубина, моменты."""
        if combo.count() == 0 or combo.currentIndex() < 0:
            return None
        art = combo.itemData(combo.currentIndex())
        if art is None:
            text = combo.currentText()
            art = text.split('|')[0] if '|' in text else text
        norm = str(art).replace(' ', '').strip()
        system = self.comboBox_11.currentText()
        if kind == 'стойка':
            rows = self.data_handler.get_mullions(system)
        else:
            rows = self.data_handler.get_transoms(system)
        for p in rows:
            if str(p['art']).replace(' ', '').strip() == norm:
                out = {'артикул': p['art'], 'глубина': p['depth']}
                out.update({k: p[k] for k in ('ix', 'wx', 'iy') if k in p})
                return out
        return {'артикул': art}

    def export_to_fasad(self, silent=False):
        """Писать результат расчёта в JSON программы Фасад (кнопка "Экспорт в FS")."""
        import json
        from datetime import datetime
        if not self.fasad_path:
            return
        lc = self._last_calc or {}
        st = self._current_profile(self.comboBox_9, 'стойка')
        rg = self._current_profile(self.comboBox_10, 'ригель')
        if st and 'ix' in st:
            st['ok'] = bool(lc) and st['ix'] >= lc.get('стойка_треб_ix_см4', 0) \
                and st.get('wx', 0) >= lc.get('стойка_треб_wx_см3', 0)
        if rg and 'ix' in rg:
            rg['ok'] = bool(lc) and rg['ix'] >= lc.get('ригель_треб_ix_см4', 0) \
                and rg.get('iy', 0) >= lc.get('ригель_треб_iy_см4', 0)
        result = {
            'программа': 'Lora 2.2',
            'дата': datetime.now().isoformat(timespec='seconds'),
            'система': self.comboBox_11.currentText(),
            'стойка': st,
            'ригель': rg,
        }
        result.update(lc)
        try:
            with open(self.fasad_path, 'w', encoding='utf-8') as f:
                json.dump({'вход': self._fasad_input, 'результат': result}, f,
                          ensure_ascii=False, indent=1)
            self._fasad_written = True
        except OSError as e:
            QtWidgets.QMessageBox.critical(self, 'Экспорт в FS',
                                           f'Не удалось записать файл:\n{e}')
            return
        if not silent:
            QtWidgets.QMessageBox.information(self, 'Экспорт в FS',
                                              'Результат передан в Фасад.')
            self.close()

    def closeEvent(self, event):
        """Перед закрытием запоминаем сеанс; для Фасада отдаём результат, если был расчёт."""
        self._save_session()
        if self.fasad_path and self._calculated_once and not self._fasad_written:
            self.export_to_fasad(silent=True)
        event.accept()

    def reset_styles(self):
        for widget in self.findChildren(QtWidgets.QLineEdit):
            widget.setStyleSheet("")
        for widget in self.findChildren(QtWidgets.QLabel):
            if widget.objectName() in ["label_66", "label_70", "label_71", "label_72", "label_73", "label_74", "label_78", "label_80", "label_81", "label_82", "label_64", "label_75"]:
                widget.setStyleSheet("")

    def update_wind_ui(self, wind):
        self.lineEdit_13.setText(str(wind['w0']))
        self.lineEdit_14.setText(f"{wind['ze']:.2f}")
        self.lineEdit_15.setText(f"{wind['k_ze']:.3f}")
        self.lineEdit_16.setText(f"{wind['zeta_ze']:.3f}")
        self.lineEdit_21.setText(f"{wind['v']:.3f}")
        self.lineEdit_23.setText(str(wind['cp']))
        # LineEdit_22 Зона: сокращаем текст (Центр/Край)
        zone_text = self.comboBox_4.currentText()
        if "Центр" in zone_text:
            self.lineEdit_22.setText("Центр")
        elif "Край" in zone_text:
            self.lineEdit_22.setText("Край")
        else:
            self.lineEdit_22.setText(zone_text)
            
        self.lineEdit_24.setText(f"{wind['w_peak']:.3f}")

    def update_snow_ui(self, snow, weight_kg):
        self.lineEdit_25.setText(f"{snow['s0']:.2f}")
        self.lineEdit_26.setText(f"{snow['mu']:.2f}")
        self.lineEdit_27.setText(f"{weight_kg:.1f}")

    def handle_railing_load(self, data):
        """Расчет перильной нагрузки по СП 20.13330.2016 (ячейки J26-K27 карты Расчет)"""
        # Перильная нагрузка учитывается только для однопролетной балки.
        beam_text = self.comboBox_6.currentText().strip().lower()
        if "двухпролет" in beam_text:
            for le in [self.lineEdit_34, self.lineEdit_35, self.lineEdit_47, self.lineEdit_44]:
                le.setText("-")
            return

        railing_text = self.comboBox_7.currentText().strip().lower()
        is_railing_enabled = (
            "есть" in railing_text
            or railing_text == "да"
            or railing_text.startswith("да ")
            or " да" in railing_text
        )

        if is_railing_enabled:
            # По логике Расчет!J26/J27/K26/K27:
            # J27 = G12*G13/1000
            # J26 = G12*G13*G20*G21/1000
            # K27 = Q12
            # K26 = Q12*Q17*Q18
            # Для текущей версии СП РФ принимаем норматив q_p = 0.8 кН/м (по активной методике книги).
            p_norm_lin = 0.8
            gamma_f = 1.2  # G20/Q17
            gamma_n = data['safety_yn']  # G21/Q18

            # На стойку (кН)
            p_mullion_norm = p_norm_lin * data['b1'] / 1000.0
            p_mullion_calc = p_norm_lin * data['b1'] * gamma_f * gamma_n / 1000.0

            # На ригель (в книге в кН/м, в UI подпись кН)
            p_transom_norm = p_norm_lin
            p_transom_calc = p_norm_lin * gamma_f * gamma_n

            self.lineEdit_34.setText(f"{p_mullion_norm:.3f}")
            self.lineEdit_35.setText(f"{p_mullion_calc:.3f}")
            self.lineEdit_47.setText(f"{p_transom_norm:.3f}")
            self.lineEdit_44.setText(f"{p_transom_calc:.3f}")
        else:
            for le in [self.lineEdit_34, self.lineEdit_35, self.lineEdit_47, self.lineEdit_44]:
                le.setText("-")

    def update_mullion_req_ui(self, m_req):
        self.lineEdit_39.setText(f"{m_req['f_allow']:.1f}")
        self.lineEdit_48.setText(f"{m_req['ix_req']:.2f}")
        self.lineEdit_37.setText(f"{m_req['wx_req']:.2f}")

    def update_transom_req_ui(self, t_req):
        # Допустимый прогиб ригеля, Ix выводим в поле (lineEdit_47), Iy в (lineEdit_55)
        self.lineEdit_47.setText(f"{t_req['f_allow_wind']:.1f}")
        self.lineEdit_55.setText(f"{t_req['f_allow_weight']:.1f}")
        # Требуемые характеристики (Ix=49, Iy=50, Wx=45)
        self.lineEdit_45.setText(f"{t_req['wx_req']:.2f}")
        self.lineEdit_49.setText(f"{t_req['ix_req']:.2f}")
        self.lineEdit_50.setText(f"{t_req['iy_req']:.2f}")

    def auto_select_profiles(self, m_req, t_req):
        """Автоподбор минимального профиля (приоритет - без усиления)"""
        system = self.comboBox_11.currentText()
        mullions = self.data_handler.get_mullions(system)
        transoms = self.data_handler.get_transoms(system)

        def normalize_art(art):
            return str(art).replace(" ", "").strip()
        
        # Универсальная защитная установка индекса по условиям.
        # Нужна на случай, если в UI по какой-то причине остался неверный текущий элемент.
        def force_combo_selection(combo, profiles, is_ok, strength_tuple):
            if combo.count() == 0 or not profiles:
                return None

            by_art = {normalize_art(p['art']): p for p in profiles}

            def profile_at(i):
                art = combo.itemData(i)
                if art is None:
                    text = combo.itemText(i)
                    art = normalize_art(text.split('|')[0]) if '|' in text else normalize_art(text)
                return by_art.get(normalize_art(art))

            # 1) Проверяем текущий выбранный элемент: если проходит — не трогаем.
            cur_i = combo.currentIndex()
            if 0 <= cur_i < combo.count():
                cur_p = profile_at(cur_i)
                if cur_p and is_ok(cur_p):
                    return cur_p

            # 2) Ищем минимальный подходящий без усиления.
            for i in range(combo.count()):
                p = profile_at(i)
                if p and ('+' not in p['art']) and is_ok(p):
                    combo.setCurrentIndex(i)
                    return p

            # 3) Если не найден, ищем среди усиленных.
            for i in range(combo.count()):
                p = profile_at(i)
                if p and ('+' in p['art']) and is_ok(p):
                    combo.setCurrentIndex(i)
                    return p

            # 4) Если ничего не прошло — ставим самый "сильный" из доступных.
            best_idx = None
            best_profile = None
            for i in range(combo.count()):
                p = profile_at(i)
                if p is None:
                    continue
                if best_profile is None or strength_tuple(p) > strength_tuple(best_profile):
                    best_profile = p
                    best_idx = i
            if best_idx is not None:
                combo.setCurrentIndex(best_idx)
            return best_profile

        # Стойка: Сначала без усилителя, потом с усилителем
        mullions.sort(key=lambda x: x['depth'])
        m_simple = [m for m in mullions if "+" not in m['art']]
        m_reinforced = [m for m in mullions if "+" in m['art']]
        
        selected_m = next((m for m in m_simple if m['ix'] >= m_req['ix_req'] and m['wx'] >= m_req['wx_req']), None)
        if not selected_m:
            selected_m = next((m for m in m_reinforced if m['ix'] >= m_req['ix_req'] and m['wx'] >= m_req['wx_req']), None)

        # Если подходящей стойки нет, выбираем самую "сильную", чтобы не оставаться на первом (12 мм) элементе.
        if not selected_m and mullions:
            selected_m = max(mullions, key=lambda x: (x['ix'], x['wx'], x['depth']))
            
        if selected_m:
            target_art = normalize_art(selected_m['art'])
            for i in range(self.comboBox_9.count()):
                item_art = self.comboBox_9.itemData(i)
                if item_art == target_art:
                    self.comboBox_9.setCurrentIndex(i)
                    break

        selected_m = force_combo_selection(
            self.comboBox_9,
            mullions,
            lambda p: p['ix'] >= m_req['ix_req'] and p['wx'] >= m_req['wx_req'],
            lambda p: (p['ix'], p['wx'], p['depth'])
        )

        # Ригель
        transoms.sort(key=lambda x: x['depth'])
        t_simple = [t for t in transoms if "+" not in t['art']]
        t_reinforced = [t for t in transoms if "+" in t['art']]
        
        selected_t = next((t for t in t_simple if t['ix'] >= t_req['ix_req'] and t['iy'] >= t_req['iy_req']), None)
        if not selected_t:
            selected_t = next((t for t in t_reinforced if t['ix'] >= t_req['ix_req'] and t['iy'] >= t_req['iy_req']), None)

        # Если подходящего ригеля нет, выбираем самый "сильный".
        if not selected_t and transoms:
            selected_t = max(transoms, key=lambda x: (x['ix'], x['iy'], x['depth']))
            
        if selected_t:
            target_art = normalize_art(selected_t['art'])
            for i in range(self.comboBox_10.count()):
                item_art = self.comboBox_10.itemData(i)
                if item_art == target_art:
                    self.comboBox_10.setCurrentIndex(i)
                    break

        selected_t = force_combo_selection(
            self.comboBox_10,
            transoms,
            lambda p: p['ix'] >= t_req['ix_req'] and p['iy'] >= t_req['iy_req'],
            lambda p: (p['ix'], p['iy'], p['depth'])
        )
        
        # Проверка глубины (желтый цвет)
        if selected_m and selected_t:
            if (selected_m['depth'] + 5) < selected_t['depth']:
                self.label_64.setStyleSheet("color: #f5dc06; font-weight: bold;")
                self.label_75.setStyleSheet("color: #f5dc06; font-weight: bold;")
            else:
                self.label_64.setStyleSheet("")
                self.label_75.setStyleSheet("")

    def verify_selected_profiles(self, q_wind_m_norm, q_wind_t_norm, p_glass_norm, data, m_req, t_req, f_all_t_wind, f_all_t_weight, manual_mullion=None, manual_transom=None):
        e_module = 70000.0
        # Стойка
        if manual_mullion:
            mullion_art = manual_mullion['art']
            m_depth = manual_mullion['depth']
            m_ix = manual_mullion['ix_tot'] # Используем суммарный Ix
            m_iy = manual_mullion['iy_tot'] # Используем суммарный Iy
            m_area = manual_mullion.get('area_tot', manual_mullion.get('area'))
            # В режиме ручного ввода мы НЕ перезаписываем текст в comboBox_9 здесь, 
            # так как он уже установлен в show_new_profile_dialog
        else:
            m_text = self.comboBox_9.currentText()
            m_depth = 0
            if "|" in m_text:
                p = m_text.split("|")
                mullion_art = p[0].strip()
                m_depth = float(p[1].strip())
                m_ix = float(p[2].split("=")[1].strip())
                m_iy = None
                m_area = None
                system_name = self.comboBox_11.currentText()
                mullions = self.data_handler.get_mullions(system_name)
                selected_m = next((x for x in mullions if str(x['art']).replace(" ", "").strip() == str(mullion_art).replace(" ", "").strip()), None)
                if selected_m:
                    m_iy = selected_m.get('iy')
                    m_area = selected_m.get('area')
            elif manual_mullion is None and self.comboBox_11.currentText() == "Ручной ввод":
                # Если выбран "Ручной ввод", но manual_mullion не передан (например, прямой вызов calculate)
                # Пытаемся взять данные из сохраненных
                res = getattr(self, 'manual_profile_data', None)
                if res:
                    mullion_art = res['mullion']['art']
                    m_depth = res['mullion']['depth']
                    m_ix = res['mullion']['ix_tot'] # Используем суммарный Ix
                    m_iy = res['mullion']['iy_tot'] # Используем суммарный Iy
                    m_area = res['mullion'].get('area_tot', res['mullion'].get('area'))
                else:
                    return
            else:
                return # Нет выбранного профиля и нет ручного ввода

        if True: # Обертка для сохранения структуры отступов
            if m_req.get('is_single_span', True):
                # I39: ROUND((5*E36*(F7/10)^4)/(384*K13*Ix),1)
                f_wind = (5.0 * q_wind_m_norm * ((data['h_m'] / 10.0) ** 4)) / (384.0 * e_module * m_ix)

                # Добавка от перильной нагрузки для фактического прогиба стойки
                # (по логике листа "Перильная нагрузка СП (РФ)" / J24, но с фактическим Ix выбранного профиля)
                beam_text = self.comboBox_6.currentText().strip().lower()
                railing_text = self.comboBox_7.currentText().strip().lower()
                railing_enabled = (
                    "однопролет" in beam_text and (
                        "есть" in railing_text
                        or railing_text == "да"
                        or railing_text.startswith("да ")
                        or " да" in railing_text
                    )
                )

                f_rail = 0.0
                if railing_enabled:
                    g12 = 0.8  # нормативная перильная нагрузка, кН/м
                    g13 = data['b1']
                    g14 = min(1400.0, data['h_m'] / 2.0)
                    g16 = data['h_m']
                    g17 = 0.9
                    f_rail = (g12 * g13 * g14 * (3.0 * (g16 ** 2) - 4.0 * (g14 ** 2)) * g17) / (48.0 * e_module * 10000.0 * m_ix)

                f_fact = round(f_wind + f_rail, 1)
            else:
                # I40: ROUND((1/185)*E36*(F7/10)^4/K13/Ix,1)
                f_fact = round((1.0 / 185.0) * q_wind_m_norm * ((data['h_m'] / 10.0) ** 4) / e_module / m_ix, 1)

            self.lineEdit_38.setText(f"{f_fact:.1f}")
            self.lineEdit_36.setText(str(m_depth))
            self.lineEdit_39.setText(f"{m_req['f_allow']:.1f}")

            if m_area and m_area > 0 and m_ix > 0:
                # E42 -> I72: λx = (F7/10*0.725)/sqrt(Ix/S)
                lambda_x = (data['h_m'] / 10.0 * 0.725) / ((m_ix / m_area) ** 0.5)
                self.lineEdit_41.setText(f"{lambda_x:.1f}")

                # Пределы гибкости по формулам G42/G43 (СП Россия):
                # G42 = IF(K4=1,100,70), G43 = 100
                lambda_x_limit = 70.0 if data['is_edge'] else 100.0
                lambda_y_limit = 100.0
                self.label_72.setText(f"<{int(lambda_x_limit)}")
                self.label_73.setText(f"<{int(lambda_y_limit)}")

                if lambda_x >= lambda_x_limit:
                    self.label_72.setText(f">{int(lambda_x_limit)}")
                    self.lineEdit_41.setStyleSheet("color: red; font-weight: bold;")
                    self.label_72.setStyleSheet("color: red;")

                if m_iy and m_iy > 0:
                    # E43 -> I71: λy = (max(F4,F5)/10*0.5)/sqrt(Iy/S)
                    lambda_y = (max(data['l1'], data['l2']) / 10.0 * 0.5) / ((m_iy / m_area) ** 0.5)
                    self.lineEdit_42.setText(f"{lambda_y:.1f}")
                    if lambda_y >= lambda_y_limit:
                        self.label_73.setText(f">{int(lambda_y_limit)}")
                        self.lineEdit_42.setStyleSheet("color: red; font-weight: bold;")
                        self.label_73.setStyleSheet("color: red;")

            # G45/E44: прогиб кромки через стрелу прогиба стойки (I39/I40)
            max_l = max(data['l1'], data['l2'])
            f_arc = (data['h_m'] ** 2 + 4.0 * (f_fact ** 2)) / (8.0 * f_fact) if f_fact != 0 else 0
            edge_term = 4.0 * (f_arc ** 2) - (max_l ** 2)
            if edge_term >= 0 and f_arc > 0:
                f_edge = round(f_arc - 0.5 * (edge_term ** 0.5), 1)
                self.lineEdit_40.setText(f"{f_edge:.1f}")
                if f_edge > 8.0:
                    self.label_71.setText(">8")
                    self.lineEdit_40.setStyleSheet("color: red; font-weight: bold;")
                    self.label_71.setStyleSheet("color: red;")
                else:
                    self.label_71.setText("<8")
            else:
                self.lineEdit_40.setText("-")
                self.label_71.setText("<8")

            if f_fact > m_req['f_allow']:
                self.lineEdit_38.setStyleSheet("color: red; font-weight: bold;")
                self.label_78.setStyleSheet("color: red;")

        # Ригель
        if manual_transom:
            transom_art = manual_transom['art']
            t_depth = manual_transom['depth']
            t_ix = manual_transom['ix_tot'] # Используем суммарный Ix
            t_iy = manual_transom['iy_tot'] # Используем суммарный Iy
            t_area = manual_transom.get('area_tot', manual_transom.get('area'))
        else:
            t_text = self.comboBox_10.currentText()
            t_depth = 0
            t_ix = 0
            t_iy = 0
            t_area = None
            if "|" in t_text:
                p = t_text.split("|")
                transom_art = p[0].strip()
                t_depth = float(p[1].strip())
                t_ix = float(p[2].split("=")[1].strip())
                t_iy = float(p[3].split("=")[1].strip())
                
                system_name = self.comboBox_11.currentText()
                transoms = self.data_handler.get_transoms(system_name)
                selected_t = next((x for x in transoms if str(x['art']).replace(" ", "").strip() == str(transom_art).replace(" ", "").strip()), None)
                if selected_t:
                    t_area = selected_t.get('area')
            elif manual_transom is None and self.comboBox_11.currentText() == "Ручной ввод":
                res = getattr(self, 'manual_profile_data', None)
                if res:
                    transom_art = res['transom']['art']
                    t_depth = res['transom']['depth']
                    t_ix = res['transom']['ix_tot'] # Используем суммарный Ix
                    t_iy = res['transom']['iy_tot'] # Используем суммарный Iy
                    t_area = res['transom'].get('area_tot', res['transom'].get('area'))
                else:
                    return
            else:
                return # Нет выбранного профиля и нет ручного ввода

        if True: # Обертка для сохранения структуры отступов
            # Точные формулы по логике E51/E52 (СП РФ) для фактических прогибов ригеля.
            f3 = data['b2']
            f4 = data['l1']
            f5 = data['l2']
            f6 = (f3 / 2.0 if f3 < f4 else f4 / 2.0) + (f3 / 2.0 if f3 < f5 else f5 / 2.0)
            q_norm_total = (q_wind_t_norm / f6) * 1000.0 if f6 else 0.0

            def side_deflection(li):
                if f3 < li:
                    return q_norm_total * f3 * 0.5 / 1000.0 * ((f3 / 10.0) ** 4) / (120.0 * e_module * t_ix)
                return q_norm_total * li * 0.5 / 1000.0 * ((5.0 * (f3 / 10.0) ** 2 - 4.0 * (li * 0.5 / 10.0) ** 2) ** 2) / (1920.0 * e_module * t_ix)

            alpha_rad = math.radians(data['angle_deg'])
            q_ref = 0.02 if f3 < 2000.0 else (0.04 if f3 < 2700.0 else 0.065)

            f_fact_ix = round(
                side_deflection(f4)
                + side_deflection(f5)
                + (5.0 * (q_ref * math.cos(alpha_rad)) * (f3 / 10.0) ** 4) / (384.0 * e_module * t_ix),
                2,
            )

            # Добавка от перильной нагрузки (аналог ветки +T24) для однопролетной схемы.
            beam_text = self.comboBox_6.currentText().strip().lower()
            railing_text = self.comboBox_7.currentText().strip().lower()
            is_railing_enabled = (
                "однопролет" in beam_text and (
                    "есть" in railing_text
                    or railing_text == "да"
                    or railing_text.startswith("да ")
                    or " да" in railing_text
                )
            )
            if is_railing_enabled and f_all_t_wind > 0:
                q12 = 0.8 # нормативная перильная нагрузка на ригель кН/м
                q13 = f3 # длина ригеля мм
                q14 = 0.9 # коэффициент
                q15 = e_module
                q16 = f_all_t_wind # допустимый прогиб
                # Формула добавки к прогибу от перильной нагрузки:
                # delta_f = (5 * q * L^4) / (384 * E * I)
                # Отсюда добавочный прогиб при текущем Ix:
                f_rail_t = (5.0 * q12 * (q13 ** 4) * q14) / (384.0 * q15 * 10000.0 * t_ix)
                f_fact_ix += round(f_rail_t, 2)

            # Фактический прогиб Iy: ветка E52 с фактическим Iy выбранного ригеля.
            qg_norm = data['t_glass'] * 0.025
            a_pad = data['a_pad']
            term1 = (
                ((a_pad / 1000.0) * math.sin(alpha_rad) * qg_norm * ((f3 - 25.0) / 1000.0) * ((f4 - 25.0) / 1000.0) / 2.0)
                * (3.0 * (f3 / 1000.0) ** 2 - 4.0 * (a_pad / 1000.0) ** 2)
                / (24.0 * e_module * 1000.0 * t_iy * 0.001)
                * 100000000.0
            )
            term2 = (5.0 * ((q_ref * math.sin(alpha_rad)) * (f3 / 10.0) ** 4)) / (384.0 * e_module * t_iy)
            f_fact_iy = round(term1 + term2, 2)
            
            self.lineEdit_46.setText(f"{f_fact_ix:.1f}")
            self.lineEdit_51.setText(f"{f_fact_iy:.1f}")
            self.lineEdit_43.setText(str(t_depth))
            self.lineEdit_47.setText(f"{t_req['f_allow_wind']:.1f}")
            self.lineEdit_55.setText(f"{t_req['f_allow_weight']:.1f}")

            # Гибкость ригеля (по ТЗ отдельные поля не предусмотрены, убираем вывод в 49/50, чтобы не затирать требуемые Ix/Iy)
            # if t_area and t_area > 0:
            #    lambda_x_t = (f3 / 10.0 * 0.725) / ((t_ix / t_area) ** 0.5) if t_ix > 0 else 0
            #    lambda_y_t = (f3 / 10.0 * 0.5) / ((t_iy / t_area) ** 0.5) if t_iy > 0 else 0
            
            if f_fact_ix > f_all_t_wind:
                self.lineEdit_46.setStyleSheet("color: red; font-weight: bold;")
                self.label_81.setStyleSheet("color: red;")
            if f_fact_iy > f_all_t_weight:
                self.lineEdit_51.setStyleSheet("color: red; font-weight: bold;")
                self.label_82.setStyleSheet("color: red;")
            
            if (m_depth + 5) < t_depth:
                self.label_64.setStyleSheet("color: #f5dc06; font-weight: bold;")
                self.label_75.setStyleSheet("color: #f5dc06; font-weight: bold;")

if __name__ == "__main__":
    setup_qt_plugin_path()
    app = QtWidgets.QApplication(sys.argv)
    fasad_path = None
    if '--фасад' in sys.argv:
        try:
            fasad_path = sys.argv[sys.argv.index('--фасад') + 1]
        except IndexError:
            pass
    window = LoraApp(fasad_path=fasad_path)
    window.show()
    sys.exit(app.exec_())
