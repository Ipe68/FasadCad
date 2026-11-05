import os
import pandas as pd

class DataHandler:
    def __init__(self, profiles_path):
        self.profiles_path = profiles_path
        self.profiles_data = {}
        self.system_names = []

    def load_profiles(self):
        """Загрузка данных из profiles.xlsx"""
        try:
            if not os.path.exists(self.profiles_path):
                return False, f"Файл не найден: {self.profiles_path}"
            
            xl = pd.ExcelFile(self.profiles_path)
            self.system_names = xl.sheet_names
            for sheet_name in self.system_names:
                df = xl.parse(sheet_name)
                self.profiles_data[sheet_name] = df
            return True, "Успешно"
        except Exception as e:
            return False, str(e)

    def get_mullions(self, system_name):
        """Парсинг стоек и стоек усиленных для выбранной системы"""
        if system_name not in self.profiles_data:
            return []

        df = self.profiles_data[system_name]
        mullions = []
        
        # Стойки (обычные) - ищем заголовок 'артикул' в самом начале (до 'ригели')
        header_row = -1
        for i, row in df.iterrows():
            val = str(row.iloc[0]).lower()
            if 'ригели' in val:
                break # Стойки закончились
            if 'артикул' in val:
                header_row = i
                break
        
        if header_row != -1:
            for i in range(header_row + 1, len(df)):
                row = df.iloc[i]
                art = str(row.iloc[0])
                if not art or art == 'nan' or 'ригели' in art.lower():
                    break
                try:
                    depth = float(row.iloc[1])
                    ix = float(row.iloc[2])
                    wx = float(row.iloc[3])
                    iy = float(row.iloc[4])
                    area = float(row.iloc[6])
                    wy = float(row.iloc[5])
                    ay = float(row.iloc[8]) if len(row) > 8 else 0.0
                    ax = float(row.iloc[9]) if len(row) > 9 else 0.0
                    mullions.append({'art': art, 'depth': depth, 'ix': ix, 'wx': wx, 'iy': iy, 'wy': wy, 'area': area, 'ay': ay, 'ax': ax})
                except (ValueError, TypeError):
                    continue

        # Стойки усиленные - ищем заголовок 'артикул' в колонке 8 (или 'стойки усиленные')
        header_row_rein = -1
        for i, row in df.iterrows():
            if len(row) > 8:
                val = str(row.iloc[8]).lower()
                if 'ригели' in val:
                    break
                if 'артикул' in val:
                    header_row_rein = i
                    break
        
        if header_row_rein != -1:
            # Характеристики усиленных стоек в столбцах 8 (арт), 9 (глубина), 10 (Ix), 11 (Wx)
            for i in range(header_row_rein + 1, len(df)):
                row = df.iloc[i]
                if len(row) <= 11: break
                art = str(row.iloc[8])
                if not art or art == 'nan' or 'ригели' in art.lower():
                    break
                try:
                    depth = float(row.iloc[9])
                    ix = float(row.iloc[10])
                    wx = float(row.iloc[11])
                    iy = float(row.iloc[12])
                    area = float(row.iloc[14])
                    wy = float(row.iloc[15])
                    ay = float(row.iloc[18]) if len(row) > 18 else 0.0
                    ax = float(row.iloc[19]) if len(row) > 19 else 0.0
                    mullions.append({'art': art, 'depth': depth, 'ix': ix, 'wx': wx, 'iy': iy, 'wy': wy, 'area': area, 'ay': ay, 'ax': ax})
                except (ValueError, TypeError):
                    continue

        return mullions

    def get_transoms(self, system_name):
        """Парсинг ригелей и ригелей усиленных для выбранной системы"""
        if system_name not in self.profiles_data:
            return []

        df = self.profiles_data[system_name]
        transoms = []
        
        # 1. Секция ОБЫЧНЫХ РИГЕЛЕЙ (Колонка 0)
        # Ищем заголовок 'ригели' в первом столбце
        start_row = -1
        for i, row in df.iterrows():
            if 'ригели' in str(row.iloc[0]).lower():
                start_row = i
                break
        
        if start_row != -1:
            header_row = -1
            # Ищем 'артикул' в первых колонках после 'ригели'
            for i in range(start_row, min(start_row + 10, len(df))):
                if 'артикул' in str(df.iloc[i, 0]).lower():
                    header_row = i
                    break
            
            if header_row != -1:
                for i in range(header_row + 1, len(df)):
                    row = df.iloc[i]
                    art = str(row.iloc[0])
                    # Останавливаемся если пустая строка или встретили 'стойки' (конец секции)
                    if not art or art == 'nan' or 'стойки' in art.lower():
                        break
                    try:
                        depth = float(row.iloc[1])
                        ix = float(row.iloc[2])
                        wx = float(row.iloc[3])
                        iy = float(row.iloc[4])
                        wy = float(row.iloc[5])
                        area = float(row.iloc[6]) if len(row) > 6 else 0.0
                        ay = float(row.iloc[8]) if len(row) > 8 else 0.0
                        ax = float(row.iloc[9]) if len(row) > 9 else 0.0
                        transoms.append({'art': art, 'depth': depth, 'ix': ix, 'wx': wx, 'iy': iy, 'wy': wy, 'area': area, 'ay': ay, 'ax': ax})
                    except (ValueError, TypeError):
                        continue

        # 2. Секция УСИЛЕННЫХ РИГЕЛЕЙ (Колонка 8)
        # В этой колонке заголовок 'ригели усиленные' на строке 18 (в pandas индексе)
        # Данные начинаются на строке 22 (в pandas индексе)
        header_row_rein = -1
        for i in range(start_row if start_row != -1 else 0, len(df)):
            if len(df.iloc[i]) > 8:
                val = str(df.iloc[i, 8]).lower()
                if 'ригели усиленные' in val:
                    header_row_rein = i
                    break
        
        if header_row_rein != -1:
            # После 'ригели усиленные' идут строки заголовков (глубина, профиля, мм)
            # Мы начинаем искать артикулы (они содержат '+')
            for i in range(header_row_rein + 1, len(df)):
                row = df.iloc[i]
                if len(row) <= 12: break
                art = str(row.iloc[8])
                
                # Если артикул содержит '+' — это усиленный ригель
                if '+' in art:
                    try:
                        depth = float(row.iloc[9])
                        ix = float(row.iloc[10])
                        wx = float(row.iloc[11])
                        iy = float(row.iloc[12])
                        wy = float(row.iloc[13])
                        area = float(row.iloc[14]) if len(row) > 14 else 0.0
                        ay = float(row.iloc[18]) if len(row) > 18 else 0.0
                        ax = float(row.iloc[19]) if len(row) > 19 else 0.0
                        transoms.append({'art': art, 'depth': depth, 'ix': ix, 'wx': wx, 'iy': iy, 'wy': wy, 'area': area, 'ay': ay, 'ax': ax})
                    except (ValueError, TypeError):
                        continue
                elif art and art != 'nan' and ('глубина' in art.lower() or 'мм' in art.lower() or 'профиля' in art.lower()):
                    continue # Пропускаем заголовочные строки
                elif not art or art == 'nan' or 'стойки' in art.lower():
                    # Прекращаем парсинг усиленных ригелей при встрече пустой ячейки или конца секции
                    if i > header_row_rein + 5: # Даем запас на заголовочные строки
                        break
        
        return transoms
