import os
import pandas as pd

# Смещения колонок внутри блока профилей (от колонки с артикулом):
# артикул, глубина, Jx, Wx, Jy, Wy, S (площадь), m (масса м.п.), ay, ax
_OFFS = dict(depth=1, ix=2, wx=3, iy=4, wy=5, area=6, mass=7, ay=8, ax=9)


class DataHandler:
    def __init__(self, profiles_path):
        self.profiles_path = profiles_path
        self.profiles_data = {}
        self.system_names = []

    def load_profiles(self):
        """Загрузка данных из profiles.xlsx (листы = профильные системы)."""
        try:
            if not os.path.exists(self.profiles_path):
                return False, f"Файл не найден: {self.profiles_path}"

            xl = pd.ExcelFile(self.profiles_path)
            self.system_names = xl.sheet_names
            for sheet_name in self.system_names:
                # header=None: первая строка листа ("стойки", "стойки усиленные", ...)
                # нужна для поиска блоков, а не как заголовок столбцов.
                self.profiles_data[sheet_name] = xl.parse(sheet_name, header=None)
            return True, "Успешно"
        except Exception as e:
            return False, str(e)

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _cell(df, r, c):
        if r < 0 or c < 0 or r >= len(df) or c >= df.shape[1]:
            return None
        v = df.iat[r, c]
        return None if pd.isna(v) else v

    @staticmethod
    def _num(v, default=None):
        try:
            return float(str(v).replace(',', '.'))
        except (TypeError, ValueError):
            return default

    def _find_label(self, df, text, col=None, exact=False, rows=None):
        """Первая ячейка с текстом. Возвращает (строка, колонка) или None."""
        rows = rows if rows is not None else range(len(df))
        for r in rows:
            cols = [col] if col is not None else range(df.shape[1])
            for c in cols:
                v = self._cell(df, r, c)
                if v is None:
                    continue
                s = str(v).strip().lower()
                if (s == text) if exact else (text in s):
                    return r, c
        return None

    def _parse_block(self, df, r0, c0, r_end=None, only_plus=False):
        """Строки профилей блока: от строки r0, артикул в колонке c0.
        Заголовочные строки пропускаются; чтение останавливается на первой
        пустой/нечисловой строке после начала данных."""
        out = []
        r_end = r_end if r_end is not None else len(df)
        for r in range(r0, min(r_end, len(df))):
            art_raw = self._cell(df, r, c0)
            if art_raw is None:
                if out:
                    break
                continue
            art = str(art_raw).strip()
            vals = {k: self._num(self._cell(df, r, c0 + o)) for k, o in _OFFS.items()}
            if any(vals[k] is None for k in ('depth', 'ix', 'wx', 'iy', 'wy', 'area')):
                if out:
                    break
                continue
            if only_plus and '+' not in art:
                continue
            out.append({
                'art': art, 'depth': vals['depth'], 'ix': vals['ix'], 'wx': vals['wx'],
                'iy': vals['iy'], 'wy': vals['wy'], 'area': vals['area'],
                'ay': vals['ay'] or 0.0, 'ax': vals['ax'] or 0.0,
                'mass': vals['mass'],          # масса погонного метра, кг/м (None - не задана)
            })
        return out

    def _sections(self, df):
        """Положение блоков: строка с 'ригели' (начало раздела ригелей)."""
        pos = self._find_label(df, 'ригели', col=0)
        return pos[0] if pos else len(df)

    # ------------------------------------------------------------------ API
    def get_mullions(self, system_name):
        """Стойки и стойки усиленные выбранной системы."""
        if system_name not in self.profiles_data:
            return []
        df = self.profiles_data[system_name]
        r_trans = self._sections(df)
        mullions = self._parse_block(df, 1, 0, r_end=r_trans)

        pos = self._find_label(df, 'стойки усиленные', rows=range(0, min(3, len(df))))
        if pos:
            mullions += self._parse_block(df, pos[0] + 1, pos[1], r_end=r_trans)
        return mullions

    def get_transoms(self, system_name):
        """Ригели и ригели усиленные выбранной системы."""
        if system_name not in self.profiles_data:
            return []
        df = self.profiles_data[system_name]
        r_trans = self._sections(df)
        if r_trans >= len(df):
            return []
        transoms = self._parse_block(df, r_trans + 1, 0)

        pos = self._find_label(df, 'ригели усиленные', rows=range(r_trans, r_trans + 3))
        if pos:
            # в усиленных ригелях берём только составные профили (артикул содержит '+')
            transoms += self._parse_block(df, pos[0] + 1, pos[1], only_plus=True)
        return transoms
