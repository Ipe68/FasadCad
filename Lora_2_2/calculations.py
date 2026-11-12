import math


def _interp_by_table(x, points):
    """Линейная интерполяция по табличным точкам."""
    if not points:
        return 1.0

    points = sorted(points, key=lambda p: p[0])
    if x <= points[0][0]:
        return float(points[0][1])

    for i in range(1, len(points)):
        x0, y0 = points[i - 1]
        x1, y1 = points[i]
        if x <= x1:
            if x1 == x0:
                return float(y1)
            t = (x - x0) / (x1 - x0)
            return float(y0 + t * (y1 - y0))

    return float(points[-1][1])


def calculate_k1_k2(l1, l2, h_m):
    """Коэффициенты k1, k2 прогиба кромки стеклопакета - как в листе 'Формулы и таблицы' СТАТИКИ
    (G36, H36, I36 = k1*k2; таблицы C57:F65 и H56:I65). Применяются ТОЛЬКО к требуемому моменту
    инерции стойки (E39/E40) и только при включённом параметре 'Учитывать коэф. k1 и k2'.
    На нагрузки они не влияют."""
    max_l = max(l1, l2)  # G34 - высота высокого стеклопакета
    max_l_cm = max_l / 10.0
    ratio_l_h = (max_l / h_m) if h_m else 0.0  # H34 = G34/F7

    # k1: B58:B65 - L2 (см); столбцы C (L2/H >= 1), D (1 > L2/H >= 0,75), E (0,75 > L2/H >= 0,66)
    base_lengths_cm = [250, 300, 350, 400, 450, 500, 550, 600]
    k1_col_c = [1.04, 1.24, 1.45, 1.67, 1.87, 2.08, 2.29, 2.49]
    k1_col_d = [1.00, 1.00, 1.00, 1.00, 1.05, 1.17, 1.28, 1.40]
    k1_col_e = [1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.01, 1.11]
    # k2: H56:I65 (после 400 см значение не растёт - как в СТАТИКЕ, I65)
    k2_len_cm = [250, 260, 270, 280, 290, 300, 325, 350, 375, 400]
    k2_val = [1.04, 1.08, 1.12, 1.17, 1.21, 1.25, 1.35, 1.46, 1.56, 1.67]

    if max_l_cm < 250:
        return {'k1': 1.0, 'k2': 1.0}

    # G36 = IF(H34>=1, C66, IF(AND(H34>=0.75, H34<1), D66, IF(AND(H34>=0.66, H34<0.75), E66, 1)))
    if ratio_l_h >= 1.0:
        k1 = _interp_by_table(max_l_cm, list(zip(base_lengths_cm, k1_col_c)))
    elif ratio_l_h >= 0.75:
        k1 = _interp_by_table(max_l_cm, list(zip(base_lengths_cm, k1_col_d)))
    elif ratio_l_h >= 0.66:
        k1 = _interp_by_table(max_l_cm, list(zip(base_lengths_cm, k1_col_e)))
    else:
        k1 = 1.0

    k2 = _interp_by_table(max_l_cm, list(zip(k2_len_cm, k2_val)))

    # В СТАТИКЕ k1 округляется до 0,01 (C66:E66), k2 (I66) не округляется
    return {'k1': round(k1, 2), 'k2': k2}


def get_kz_zeta(ze, terrain):
    """Интерполяция kz и zeta по таблицам из 'ветер СП' листа СТАТИКА .xlsx"""
    # Типы местности A, B, C (из таблицы в 'ветер СП' ячейки G39:I41)
    # alpha (G39:G41), k10 (H39:H41), zeta10 (I39:I41)
    terrain_params = {
        "A": {"alpha": 0.15, "k10": 1.0, "zeta10": 0.76},
        "B": {"alpha": 0.20, "k10": 0.65, "zeta10": 1.06},
        "C": {"alpha": 0.25, "k10": 0.40, "zeta10": 1.78}
    }
    p = terrain_params.get(terrain, terrain_params["B"])
    
    # Формулы из H5 и H6 листа 'ветер СП':
    # H5 (k): ROUND((k10)*((ze/10)^(2*(alpha))),2)
    # H6 (zeta): ROUND((zeta10)*((ze/10)^(-(alpha))),2)
    k_ze = round(p["k10"] * ((ze / 10) ** (2 * p["alpha"])), 2)
    zeta_ze = round(p["zeta10"] * ((ze / 10) ** (-p["alpha"])), 2)
    
    return k_ze, zeta_ze

def calculate_wind_parameters(region, terrain, h_top, h_building, w_building, is_edge, area_mullion):
    """Расчет параметров ветра по СП 20.13330.2016 (по логике СТАТИКА .xlsx)"""
    # w0 (кПа)
    wind_regions_w0 = {
        "Ia": 0.17, "I": 0.23, "II": 0.3, "III": 0.38, 
        "IV": 0.48, "V": 0.6, "VI": 0.73, "VII": 0.85, "VIII": 1.0
    }
    w0 = wind_regions_w0.get(region, 0.23)

    # ze (Эквивалентная высота) - Логика из G13 'ветер СП'
    # G10: ЕСЛИ(D9<=D10; "а"; ЕСЛИ(D9>2*D10;"в"; "б"))
    # D9=h_building, D10=w_building, D12=h_top
    if h_building <= w_building: # случай а
        ze = h_building
    elif h_building > 2 * w_building: # случай в
        if h_top <= w_building: ze = w_building
        elif h_top >= (h_building - w_building): ze = h_building
        else: ze = h_top
    else: # случай б ( h_building > w_building and h_building <= 2*w_building )
        if h_top <= (h_building - w_building): ze = w_building
        else: ze = h_building

    if ze > 150: ze = 150
    if ze < 1: ze = 1 # Минимальная высота по СП

    # k(ze) и zeta(ze)
    k_ze, zeta_ze = get_kz_zeta(ze, terrain)

    # v+ и v- (ню) коэффициенты корреляции пульсаций (ячейки H19 и K19 'ветер СП')
    # v+ (H19) для давления
    if area_mullion <= 2: v_plus = 1.0
    elif area_mullion < 5: v_plus = round(-0.0333 * area_mullion + 1.0667, 2)
    elif area_mullion == 5: v_plus = 0.9
    elif area_mullion < 10: v_plus = round(-0.02 * area_mullion + 1, 2)
    elif area_mullion == 10: v_plus = 0.8
    elif area_mullion < 20: v_plus = round(-0.005 * area_mullion + 0.85, 2)
    else: v_plus = 0.75

    # v- (K19) для отсоса
    if area_mullion <= 2: v_minus = 1.0
    elif area_mullion < 5: v_minus = round(-0.05 * area_mullion + 1.1, 2)
    elif area_mullion == 5: v_minus = 0.85
    elif area_mullion < 10: v_minus = round(-0.02 * area_mullion + 0.95, 2)
    elif area_mullion == 10: v_minus = 0.75
    elif area_mullion < 20: v_minus = round(-0.005 * area_mullion + 0.8, 2)
    else: v_minus = 0.7
    
    # cp (Аэродинамические коэффициенты из ячеек G23, G24, G25 'ветер СП')
    # G23 (Центр давление) = 1.2
    # G24 (Центр отсос) = -1.2
    # G25 (Край отсос) = -2.2
    
    if is_edge:
        # Краевая зона (отсос)
        cp = -2.2
        v = v_minus
    else:
        # Центральная зона (берем давление по умолчанию, как в расчете стоек на прочность)
        cp = 1.2
        v = v_plus

    # Формула для нормативной пиковой нагрузки (формулы F32, F33, F34 в 'ветер СП')
    # W = w0 * k(ze) * (1 + zeta(ze)) * cp * v
    # Используем abs(cp), так как для подбора профиля нам важна величина нагрузки (давление или отсос)
    w_peak = round(w0 * k_ze * (1 + zeta_ze) * abs(cp) * v, 3)

    return {
        'w0': w0, 'ze': ze, 'k_ze': k_ze, 'zeta_ze': zeta_ze, 
        'v': v, 'cp': cp, 'w_peak': w_peak
    }

def calculate_snow_parameters(region, angle_deg):
    """Расчет снеговой нагрузки по СП 20.13330.2016 (по логике СТАТИКА .xlsx)"""
    snow_regions_s0 = {
        "I": 0.5, "II": 1.0, "III": 1.5, "IV": 2.0, "V": 2.5, "VI": 3.0, "VII": 3.5, "VIII": 4.0
    }
    s0 = snow_regions_s0.get(region, 1.0)
    
    # Расчет mu (коэффициент формы) - точная логика из F23 (для СП РФ, K2=3):
    # IF(F8<30,1,IF(AND(30<=F8,F8<=60),1-(F8-30)/30,0))
    if angle_deg < 30:
        mu = 1.0
    elif angle_deg <= 60:
        mu = 1.0 - (angle_deg - 30.0) / 30.0
    else:
        mu = 0.0

    # Без промежуточных округлений (как в Excel)
    s_norm = s0 * mu
    return {'s0': s0, 'mu': mu, 's_norm': s_norm}

def calculate_glass_weight(l1, b2, t_glass):
    """Расчет веса стеклопакета над ригелем (кг) по логике СТАТИКА .xlsx (ячейка F19)"""
    # F3 = b2 (длина ригеля)
    # F4 = l1 (высота стеклопакета НАД ригелем)
    # F16 = t_glass (суммарная толщина)
    # F17 = 2500 (плотность стекла)
    
    # Из карты 'Формулы и таблицы':
    # F18 (Удельный вес) = (F16)*0.01*F17/1000
    # F19 (Вес) = (F3-24)/1000 * (F4-24)/1000 * F18 * 100
    
    f3 = b2
    f4 = l1
    f16 = t_glass
    f17 = 2500.0
    
    f18 = f16 * 0.01 * f17 / 1000.0 # кН/м2
    f19 = (f3 - 24) / 1000.0 * (f4 - 24) / 1000.0 * f18 * 100.0 # кг
    
    return round(f19, 5)

def calculate_mullion_requirements(q_norm, q_calc, h_m, b1, r_calc, is_single_span, railing_enabled, railing_norm_kn, safety_yn, mullion_deflection_ratio=200.0, k_edge=1.0):
    """Требуемые характеристики стойки по формулам E38/E39/E40 листа 'Формулы и таблицы'."""
    e_module = 70000.0  # K13

    # K14: пользовательский предел прогиба стойки (comboBox_8: 1/n)
    # По умолчанию принимаем 1/200 (как в предыдущей версии).
    ratio = mullion_deflection_ratio if mullion_deflection_ratio and mullion_deflection_ratio > 0 else 200.0
    k14 = 1.0 / ratio
    f_allow = h_m * k14

    # K10 (для СП РФ): 1.1
    k10 = 1.1

    # E38: деление на K12, где K12 = K11 / γn -> эквивалентно использованию Rdesign = R / γn
    r_design = (r_calc / safety_yn) if (r_calc and safety_yn) else r_calc
    wx_req = ((q_calc * (h_m / 1000.0) ** 2 / 8.0) / (r_design * 1000.0)) * 1_000_000.0 if r_design else 0.0

    # Добавка от перильной нагрузки (J25) только для однопролетной схемы (F10=1) и при включенной перильной нагрузке (F12=1)
    # J25 = G12*G13*G14*(G16-G14)*G20*G21/(G16*G22*1000)
    # где: G12=qнорм перил, G13=B1, G14=ax(принято 1400 мм, ax < H/2), G16=H, G20=1.2, G21=γn=safety_yn, G22=R
    if is_single_span and railing_enabled:
        g14 = min(1400.0, h_m / 2.0)
        g16 = h_m
        g22 = r_calc
        if g16 != 0 and g22 != 0:
            g20 = 1.2
            g21 = safety_yn
            j25 = railing_norm_kn * b1 * g14 * (g16 - g14) * g20 * g21 / (g16 * g22 * 1000.0)
            wx_req += j25

    # E39/E40: основная часть умножается на k1*k2 (I36) при включённом параметре F14 (k_edge = k1*k2, иначе 1)
    if is_single_span:
        ix_req = (5.0 * (1.0 / k14) * q_norm * (h_m ** 3)) / (384.0 * e_module * 10000.0) * k_edge
    else:
        ix_req = ((1.0 / k14) * q_norm * (h_m ** 3)) / (185.0 * e_module * 10000.0) * k_edge

    # Добавка от перильной нагрузки (J24) только для однопролетной схемы и включенной перильной нагрузки
    # J24 = G12*G13*G14*(3*(G16)^2-4*(G14)^2)*G17/(48*G18*10000*G19)
    if is_single_span and railing_enabled and f_allow != 0:
        g14 = min(1400.0, h_m / 2.0)
        g16 = h_m
        # В Excel (СП РФ) G17 = 0.9
        g17 = 0.9
        g18 = e_module
        g19 = f_allow
        j24 = railing_norm_kn * b1 * g14 * (3.0 * (g16 ** 2) - 4.0 * (g14 ** 2)) * g17 / (48.0 * g18 * 10000.0 * g19)
        ix_req += j24

    return {
        'ix_req': round(ix_req, 2),
        'wx_req': round(wx_req, 2),
        'f_allow': round(f_allow, 1),
        'is_single_span': is_single_span,
        'k10': k10,
        'k14': k14,
        'k_edge': k_edge
    }

def calculate_transom_requirements(q_wind_norm, q_wind_calc, b2, l1, l2, angle_deg, t_glass, a_pad, f_allow_weight, r_calc, safety_yn, is_single_span, railing_enabled):
    """Требуемые характеристики ригеля по формулам E50/E51/E52 (+T24/T25) листа 'Формулы и таблицы'."""
    e_module = 70000.0  # K13

    # L15: допустимый прогиб ригеля по ветру (F3/200, но не более 8 мм)
    f_allow_wind = b2 / 200.0
    if f_allow_wind > 8.0:
        f_allow_wind = 8.0

    # E51: требуемый Ix (ветер)
    # q_norm_total = (F18*cos(a)+F24*cos^2(a)/1.6+K27)
    # В вызов функции передаем q_wind_norm = E48 = q_norm_total * F6 / 1000,
    # значит q_norm_total = E48/F6*1000.
    f6 = (b2 / 2.0 if b2 < l1 else l1 / 2.0) + (b2 / 2.0 if b2 < l2 else l2 / 2.0)
    q_norm_total = (q_wind_norm / f6) * 1000.0 if f6 else 0.0
    # Проверка на f_allow_wind во избежание деления на ноль
    if f_allow_wind <= 0:
        f_allow_wind = 0.1 # минимальное значение

    def e51_side_term(li):
        # f3 = b2, f4 = li (l1 или l2)
        # Если ригель длиннее стеклопакета (f3 > f4), то нагрузка приложена не на всю длину.
        # В этом случае грузовая площадь - трапеция или треугольник.
        # Ix_req = (q * L^4) / (E * f_allow) * K
        if b2 < li:
            # Случай когда ригель короче высоты пакета (нагрузка треугольная)
            # В Excel это f3 < f4. Формула: q_norm_total * f3 * 0.5 / 1000 * (f3/10)^4 / (120 * E * f_allow)
            return q_norm_total * b2 * 0.5 / 1000.0 * ((b2 / 10.0) ** 4) / (120.0 * e_module * f_allow_wind)
        
        # Случай когда ригель длиннее высоты пакета (нагрузка трапециевидная)
        # Формула: q_norm_total * f4 * 0.5 / 1000 * ((5*(f3/10)^2 - 4*(f4*0.5/10)^2)^2) / (1920 * E * f_allow)
        return q_norm_total * li * 0.5 / 1000.0 * ((5.0 * (b2 / 10.0) ** 2 - 4.0 * (li * 0.5 / 10.0) ** 2) ** 2) / (1920.0 * e_module * f_allow_wind)

    alpha_rad = math.radians(angle_deg)
    q_ref = 0.02 if b2 < 2000.0 else (0.04 if b2 < 2700.0 else 0.065)
    ix_req = round(
        e51_side_term(l1)
        + e51_side_term(l2)
        + (5.0 * (q_ref * math.cos(alpha_rad)) * (b2 / 10.0) ** 4) / (384.0 * e_module * f_allow_wind),
        2,
    )

    # Добавка от перильной нагрузки (T24) для однопролетной схемы при включенной перильной нагрузке.
    # T24 = 5*Q12*(Q13)^4*Q14/(384*Q15*Q16*10000)
    if is_single_span and railing_enabled and f_allow_wind > 0:
        q12 = 0.8   # нормативная перильная нагрузка на ригель
        q13 = b2
        q14 = 0.9
        q15 = e_module
        q16 = f_allow_wind
        t24 = 5.0 * q12 * (q13 ** 4) * q14 / (384.0 * q15 * q16 * 10000.0)
        ix_req = round(ix_req + t24, 2)

    # E50: требуемый Wx ригеля
    # E50 = (side1 + side2) / (K12*1000) * 1e6 + IF(AND(F10=1,F12=1),T25,0)
    e47_div_f6 = (q_wind_calc / f6) if f6 else 0.0

    def e50_side_term(li):
        if b2 < li:
            return e47_div_f6 * (b2 / 2.0) * ((0.001 * b2) ** 2) / 12.0
        return (
            e47_div_f6 * (li / 2.0) * ((0.001 * b2) ** 2) / 8.0
            - e47_div_f6 * (li / 2.0) * ((0.001 * li / 2.0) ** 2) / 6.0
        )

    wx_req = 0.0
    r_design = (r_calc / safety_yn) if (r_calc and safety_yn) else r_calc
    if r_design:
        wx_req = (e50_side_term(l1) + e50_side_term(l2)) / (r_design * 1000.0) * 1_000_000.0

    # Добавка от перильной нагрузки T25 (для однопролетной схемы при включенной перильной нагрузке)
    # T25 = Q12*(Q13)^2*Q17*Q18/(8*Q19*1000)
    if is_single_span and railing_enabled and r_calc:
        q12 = 0.8
        q13 = b2
        q17 = 1.2
        q18 = 1.0
        q19 = r_calc
        t25 = q12 * (q13 ** 2) * q17 * q18 / (8.0 * q19 * 1000.0)
        wx_req += t25

    # E52 (требуемый Iy по нагрузке от веса стеклопакета)
    # Используем точную структуру формулы с f_allow_weight = IF(F3*K16<L16,F3*K16,L16)
    alpha_rad = math.radians(angle_deg)
    qg_norm = t_glass * 0.025
    f_allow_weight_eff = f_allow_weight if f_allow_weight and f_allow_weight > 0 else 1e-9
    term1 = (
        ((a_pad / 1000.0) * math.sin(alpha_rad) * qg_norm * ((b2 - 25.0) / 1000.0) * ((l1 - 25.0) / 1000.0) / 2.0)
        * (3.0 * (b2 / 1000.0) ** 2 - 4.0 * (a_pad / 1000.0) ** 2)
        / (24.0 * e_module * 1000.0 * f_allow_weight_eff * 0.001)
        * 100000000.0
    )
    term2 = (5.0 * ((q_ref * math.sin(alpha_rad)) * (b2 / 10.0) ** 4)) / (384.0 * e_module * f_allow_weight_eff)
    iy_req = term1 + term2

    return {
        'wx_req': round(wx_req, 2),
        'ix_req': round(ix_req, 2),
        'iy_req': round(iy_req, 2),
        'f_allow_wind': round(f_allow_wind, 2),
        'f_allow_weight': round(f_allow_weight_eff, 2),
    }


# ---------------------------------------------------------------------------
# Проверка прочности по формулам примера отчёта (СП 128.13330.2016)
#   стойка  : (N/A + Mx/Wx) / (Ryn * gamma_c) <= 1   (сжато-изгибаемый элемент)
#   ригель  : (Mx/Wx + My/Wy) / (Ryn * gamma_c) <= 1 (изгиб в двух плоскостях)
# ---------------------------------------------------------------------------
G_ACC = 9.81          # ускорение свободного падения, м/с2
GAMMA_F_SELF = 1.1    # коэф. надёжности по нагрузке от собственного веса профиля
GAMMA_C = 1.0         # коэф. условий работы (табл. 6 СП 128.13330.2016, как в примере)
GAMMA_F_RAIL = 1.2    # коэф. надёжности для перильной нагрузки


def _transom_self_weight_kn_m(profile, b2):
    """Погонный вес ригеля, кН/м: по массе профиля, иначе приближённо как в расчёте прогибов."""
    mass = profile.get('mass')
    if mass:
        return mass * G_ACC / 1000.0, True
    return (0.02 if b2 < 2000.0 else (0.04 if b2 < 2700.0 else 0.065)), False


def mullion_strength(profile, q_calc, h_m, b1, r_calc, safety_yn, angle_deg, qG_norm,
                     k_glass, is_single_span, railing_enabled, railing_norm_kn=0.8,
                     gamma_c=GAMMA_C):
    """Прочность стойки. q_calc - расчётная погонная нагрузка (кН/м), h_m, b1 - мм, qG_norm - кН/м2.
    Возвращает словарь с промежуточными величинами и коэффициентом использования 'util'."""
    sin_a = math.sin(math.radians(angle_deg))
    wx = profile.get('wx') or 0.0
    area = profile.get('area') or 0.0
    mass = profile.get('mass')
    r_design = (r_calc / safety_yn) if (r_calc and safety_yn) else r_calc

    # Продольная сила: собственный вес стойки + вес заполнения с грузовой площади
    n_self = (mass or 0.0) * (h_m / 1000.0) * G_ACC * GAMMA_F_SELF * sin_a                 # Н
    area_load = (b1 / 1000.0) * (h_m / 1000.0)                                             # м2
    n_fill = qG_norm * k_glass * area_load * 1000.0 * sin_a                                # Н
    n_tot = n_self + n_fill

    # Изгибающий момент
    m_wind = q_calc * (h_m / 1000.0) ** 2 / 8.0                                            # кН*м
    m_rail = 0.0
    ax = 0.0
    if is_single_span and railing_enabled and h_m:
        ax = min(1400.0, h_m / 2.0)
        p_calc = railing_norm_kn * (b1 / 1000.0) * GAMMA_F_RAIL                            # кН
        m_rail = p_calc * ax * (h_m - ax) / h_m / 1000.0                                   # кН*м
    m_tot = m_wind + m_rail

    sigma_n = n_tot / (area * 100.0) if area else 0.0                                      # МПа
    sigma_m = m_tot * 1000.0 / wx if wx else float('inf')                                  # МПа
    util = (sigma_n + sigma_m) / (r_design * gamma_c) if r_design else float('inf')
    return {
        'n_self': n_self, 'n_fill': n_fill, 'n': n_tot, 'area_load': area_load,
        'm_wind': m_wind, 'm_rail': m_rail, 'm': m_tot, 'ax': ax,
        'sigma_n': sigma_n, 'sigma_m': sigma_m, 'r_design': r_design, 'gamma_c': gamma_c,
        'util': util, 'ok': util <= 1.0, 'mass_known': bool(mass), 'area_known': bool(area),
    }


def transom_strength(profile, q_calc, f6, b2, l1, l2, angle_deg, qG_norm, k_glass, a_pad,
                     r_calc, safety_yn, is_single_span, railing_enabled, gamma_c=GAMMA_C):
    """Прочность ригеля при изгибе в двух плоскостях.
    Mx - от ветра/снега/стекла перпендикулярно фасаду (по грузовым площадям над и под ригелем),
    My - в плоскости фасада от веса стеклопакета (две подкладки на расстоянии a от стоек) и собственного веса."""
    sin_a = math.sin(math.radians(angle_deg))
    wx = profile.get('wx') or 0.0
    wy = profile.get('wy') or 0.0
    r_design = (r_calc / safety_yn) if (r_calc and safety_yn) else r_calc

    q_surf = (q_calc / f6) if f6 else 0.0            # кН/м2, делённое на 1000 (как в calculate_transom_requirements)

    def side(li):
        if b2 < li:
            return q_surf * (b2 / 2.0) * ((0.001 * b2) ** 2) / 12.0
        return (q_surf * (li / 2.0) * ((0.001 * b2) ** 2) / 8.0
                - q_surf * (li / 2.0) * ((0.001 * li / 2.0) ** 2) / 6.0)

    mx_load = side(l1) + side(l2)                                                            # кН*м
    mx_rail = 0.0
    if is_single_span and railing_enabled:
        mx_rail = 0.8 * (b2 / 1000.0) ** 2 * GAMMA_F_RAIL / 8.0                              # кН*м
    mx = mx_load + mx_rail

    f_norm = sin_a * qG_norm * ((b2 - 25.0) / 1000.0) * ((l1 - 25.0) / 1000.0) / 2.0         # кН, на одну подкладку
    f_calc = f_norm * k_glass
    m_f = f_calc * a_pad / 1000.0                                                            # кН*м
    q_self, mass_known = _transom_self_weight_kn_m(profile, b2)
    q_self_y = q_self * GAMMA_F_SELF * sin_a                                                 # кН/м (в плоскости фасада)
    m_q = q_self_y * (b2 / 1000.0) ** 2 / 8.0
    my = m_f + m_q

    sigma_x = mx * 1000.0 / wx if wx else float('inf')
    sigma_y = my * 1000.0 / wy if wy else float('inf')
    util = (sigma_x + sigma_y) / (r_design * gamma_c) if r_design else float('inf')
    return {
        'mx_load': mx_load, 'mx_rail': mx_rail, 'mx': mx,
        'f_calc': f_calc, 'a': a_pad, 'm_f': m_f, 'q_self_y': q_self_y, 'm_q': m_q, 'my': my,
        'sigma_x': sigma_x, 'sigma_y': sigma_y, 'r_design': r_design, 'gamma_c': gamma_c,
        'util': util, 'ok': util <= 1.0, 'mass_known': mass_known,
    }


# ---------------------------------------------------------------------------
# Составное сечение «профиль + усилитель» (метод приведённого сечения).
# Логика 1:1 повторяет new_profile_dialog.py (_update_totals_mullion / _update_totals_transom);
# нужна, чтобы в отчёте показать промежуточные шаги расчёта итоговых характеристик.
# ---------------------------------------------------------------------------
E_ALUM = 70000.0      # модуль упругости алюминия, МПа
E_STEEL = 210000.0    # модуль упругости стали, МПа
N_STEEL = 3.0         # коэффициент приведения стали к алюминию, принятый в программе (E_ст/E_ал)
RHO_ALUM = 2700.0     # плотность алюминия, кг/м3
RHO_STEEL = 7850.0    # плотность стали, кг/м3


def mass_per_m(area_cm2, material):
    """Масса погонного метра по площади сечения, кг/м (A[см2] * rho[кг/м3] * 1e-4)."""
    rho = RHO_STEEL if 'сталь' in str(material).lower() else RHO_ALUM
    return (area_cm2 or 0.0) * rho * 1e-4


def _wy_composite(iy_tot, b_b, bx_rein, ax_main, ax_rein):
    max_bx = max(b_b, bx_rein) if bx_rein > 0 else b_b
    if ax_main > 0 or ax_rein > 0:
        cx_main = ax_main if ax_main > 0 else b_b / 2.0
        cx_rein = ax_rein if ax_rein > 0 else (bx_rein / 2.0 if bx_rein > 0 else b_b / 2.0)
        cx_max = max(cx_main, b_b - cx_main, cx_rein, bx_rein - cx_rein) if max_bx > 0 else 1.0
        return (iy_tot / (cx_max / 10.0) if cx_max > 0 else 0.0), cx_max / 10.0
    return (iy_tot / (max_bx / 20.0) if max_bx > 0 else 0.0), max_bx / 20.0


def _wx_simple(ix_tot, h, ay):
    if h <= 0:
        return 0.0, 0.0
    c_max = (max(ay, h - ay) / 10.0) if ay > 0 else h / 20.0
    return (ix_tot / c_max if c_max > 0 else 0.0), c_max


def composite_section(ix_b, iy_b, area_b, h_b, b_b, ay_main, ax_main,
                      ix_r, iy_r, area_r, bx_rein, by_rein, ay_rein, ax_rein,
                      material, pos, thermal=False, dist_user=0.0):
    """Характеристики составного сечения. Единицы: см4, см2, мм (глубина h, ширина b, ay, ax, расстояния).
    Возвращает словарь: итоговые ix_tot, iy_tot, wx_tot, wy_tot, area_tot и промежуточные величины
    (n, dist_ct, y_c, d1, d2, y_min, y_max, c_top, c_bot, c_max, branch...)."""
    mat = str(material).strip().lower()
    pos = str(pos).strip().lower()
    n = N_STEEL if "сталь" in mat else 1.0
    r = {'n': n, 'pos': pos, 'mat': mat, 'dist_ct': 0.0, 'y_c': 0.0, 'd1': 0.0, 'd2': 0.0,
         'y_min': 0.0, 'y_max': 0.0, 'c_top': 0.0, 'c_bot': 0.0, 'c_max': 0.0,
         'a_red': area_b + n * area_r, 'steiner': False, 'branch': ''}

    def finish(ix_tot, wx_tot, branch, steiner):
        iy_tot = iy_b + n * iy_r
        wy_tot, cx = _wy_composite(iy_tot, b_b, bx_rein, ax_main, ax_rein)
        r.update(ix_tot=ix_tot, iy_tot=iy_tot, wx_tot=wx_tot, wy_tot=wy_tot, cx=cx,
                 area_tot=area_b + n * area_r, branch=branch, steiner=steiner)
        return r

    def steiner(dist_ct, y_c, with_ay=True):
        d1, d2 = y_c / 10.0, (dist_ct - y_c) / 10.0
        r.update(dist_ct=dist_ct, y_c=y_c, d1=d1, d2=d2)
        return (ix_b + area_b * d1 ** 2) + n * (ix_r + area_r * d2 ** 2)

    def fibers(y_min, y_max, y_c):
        c_top, c_bot = abs(y_max - y_c) / 10.0, abs(y_c - y_min) / 10.0
        r.update(y_min=y_min, y_max=y_max, c_top=c_top, c_bot=c_bot, c_max=max(c_top, c_bot))
        return max(c_top, c_bot)

    def simple(branch):
        ix_tot = ix_b + n * ix_r
        wx_tot, c = _wx_simple(ix_tot, h_b, ay_main)
        r['c_max'] = c
        return finish(ix_tot, wx_tot, branch, False)

    # --- режим терморазрыва: положение усилителя берётся из ay и расположения
    if thermal and area_r > 0 and area_b > 0:
        if "помещен" in pos:
            dist_ct = ay_main + ay_rein
        elif "улиц" in pos:
            dist_ct = (h_b - ay_main) + ay_rein
        else:
            dist_ct = 0.0
        if dist_ct > 0:
            y_c = (n * area_r * dist_ct) / (area_b + n * area_r)
            ix_tot = steiner(dist_ct, y_c)
            if ay_main > 0 and ay_rein > 0:
                y_min, y_max = min(-ay_main, dist_ct - ay_rein), max(h_b - ay_main, dist_ct + by_rein - ay_rein)
            else:
                y_min, y_max = min(-h_b / 2.0, dist_ct - by_rein / 2.0), max(h_b / 2.0, dist_ct + by_rein / 2.0)
            c_max = fibers(y_min, y_max, y_c)
            return finish(ix_tot, ix_tot / c_max if c_max > 0 else 0.0, 'thermal_offset', True)
        return simple('thermal_coincident')

    if area_r <= 0 or area_b <= 0:
        return simple('no_area')

    if pos == "внутри камеры профиля":
        dist_ct = dist_user
        if dist_ct > 0:
            y_c = (n * area_r * dist_ct) / (area_b + n * area_r)
            ix_tot = steiner(dist_ct, y_c)
            if ay_main > 0 and ay_rein > 0:
                y_min, y_max = min(-ay_main, dist_ct - ay_rein), max(h_b - ay_main, dist_ct + by_rein - ay_rein)
            else:
                y_min, y_max = min(-h_b / 2.0, dist_ct - by_rein / 2.0), max(h_b / 2.0, dist_ct + by_rein / 2.0)
            c_max = fibers(y_min, y_max, y_c)
            return finish(ix_tot, ix_tot / c_max if c_max > 0 else 0.0, 'inside_offset', True)
        return simple('inside_coincident')

    if pos in ("снаружи, улица", "снаружи, помещение"):
        if by_rein <= 0:
            by_rein = h_b if h_b > 0 else 1.0
        if ay_main > 0 and ay_rein > 0:
            dist_ct = (ay_main + ay_rein) if "помещен" in pos else ((h_b - ay_main) + ay_rein)
            y_c = (n * area_r * dist_ct) / (area_b + n * area_r)
            ix_tot = steiner(dist_ct, y_c)
            y_min, y_max = min(-ay_main, dist_ct - ay_rein), max(h_b - ay_main, dist_ct + by_rein - ay_rein)
            c_max = fibers(y_min, y_max, y_c)
            return finish(ix_tot, ix_tot / c_max if c_max > 0 else 0.0, 'outside_ay', True)
        # запасной вариант: геометрические центры
        y_st = (h_b / 2.0 + by_rein / 2.0) if "помещен" in pos else -(h_b / 2.0 + by_rein / 2.0)
        y_c = (n * area_r * y_st) / (area_b + n * area_r)
        d_al, d_st = y_c / 10.0, (y_st - y_c) / 10.0
        r.update(dist_ct=abs(y_st), y_c=y_c, d1=d_al, d2=d_st)
        ix_tot = (ix_b + area_b * d_al ** 2) + n * (ix_r + area_r * d_st ** 2)
        if "помещен" in pos:
            y_min, y_max = -h_b / 2.0, h_b / 2.0 + by_rein
        else:
            y_min, y_max = -(h_b / 2.0 + by_rein), h_b / 2.0
        c_front, c_back = abs(y_c - y_min) / 10.0, abs(y_max - y_c) / 10.0
        r.update(y_min=y_min, y_max=y_max, c_top=c_back, c_bot=c_front, c_max=max(c_front, c_back))
        c_max = max(c_front, c_back)
        return finish(ix_tot, ix_tot / c_max if c_max > 0 else 0.0, 'outside_geom', True)

    return simple('other_pos')


# ---------------------------------------------------------------------------
# Прогиб стойки и прогиб кромки стеклопакета (СТАТИКА: I39/I40, G45, E44).
# Формулы 1:1 как в verify_selected_profiles (main.py); используются и при подборе профиля.
# ---------------------------------------------------------------------------
EDGE_DEFLECTION_LIMIT = 8.0   # мм; в СТАТИКЕ условие строгое: E44 < 8


def mullion_edge_deflection(ix_cm4, h_m, q_norm, is_single_span, railing_enabled, b1, max_l,
                            railing_norm_kn=0.8, e_module=70000.0):
    """Прогиб стойки f_fact (мм, округлён до 0,1) и прогиб кромки стеклопакета f_edge (мм).
    ix_cm4 - момент инерции стойки; q_norm - нормативная нагрузка на стойку, кН/м;
    max_l - высота высокого стеклопакета max(L1, L2), мм.
    Возвращает {'f_fact', 'f_edge', 'ok'}; f_edge = None, если расчёт кромки невозможен."""
    if not ix_cm4 or ix_cm4 <= 0:
        return {'f_fact': None, 'f_edge': None, 'ok': False}
    if is_single_span:
        f_wind = (5.0 * q_norm * ((h_m / 10.0) ** 4)) / (384.0 * e_module * ix_cm4)
        f_rail = 0.0
        if railing_enabled:
            g14 = min(1400.0, h_m / 2.0)
            f_rail = (railing_norm_kn * b1 * g14 * (3.0 * (h_m ** 2) - 4.0 * (g14 ** 2)) * 0.9) / (48.0 * e_module * 10000.0 * ix_cm4)
        f_fact = round(f_wind + f_rail, 1)
    else:
        f_fact = round((1.0 / 185.0) * q_norm * ((h_m / 10.0) ** 4) / e_module / ix_cm4, 1)

    f_arc = (h_m ** 2 + 4.0 * (f_fact ** 2)) / (8.0 * f_fact) if f_fact != 0 else 0.0
    edge_term = 4.0 * (f_arc ** 2) - (max_l ** 2)
    if edge_term >= 0 and f_arc > 0:
        f_edge = round(f_arc - 0.5 * (edge_term ** 0.5), 1)
        return {'f_fact': f_fact, 'f_edge': f_edge, 'ok': f_edge < EDGE_DEFLECTION_LIMIT}
    # стрела прогиба слишком мала для заданной высоты стеклопакета - прогиб кромки пренебрежимо мал
    return {'f_fact': f_fact, 'f_edge': None, 'ok': True}
