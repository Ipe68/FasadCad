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
    """Расчет коэффициентов k1/k2 по таблицам (k1_k2.xlsx / Формулы и таблицы C66:E66, I66)."""
    max_l = max(l1, l2)  # G34
    max_l_cm = max_l / 10.0
    ratio_l_h = (max_l / h_m) if h_m else 0.0  # H34 = G34/F7

    # Таблица по длине L2 (см)
    # B58:B65 и C58:C65 / D58:D65 / E58:E65; H56:H65 и I56:I65
    base_lengths_cm = [250, 300, 350, 400, 450, 500, 550, 600]
    k1_col_c = [1.04, 1.24, 1.45, 1.67, 1.87, 2.08, 2.29, 2.49]
    k1_col_d = [1.00, 1.00, 1.00, 1.00, 1.05, 1.17, 1.28, 1.40]
    k1_col_e = [1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.01, 1.11]
    k2_col = [1.04, 1.25, 1.46, 1.67, 1.87, 2.08, 2.29, 2.49]

    if max_l_cm < 250:
        k1 = 1.0
        k2 = 1.0
        return {'k1': k1, 'k2': k2}

    points_c = list(zip(base_lengths_cm, k1_col_c))
    points_d = list(zip(base_lengths_cm, k1_col_d))
    points_e = list(zip(base_lengths_cm, k1_col_e))
    points_k2 = list(zip(base_lengths_cm, k2_col))

    k1_c = _interp_by_table(max_l_cm, points_c)
    k1_d = _interp_by_table(max_l_cm, points_d)
    k1_e = _interp_by_table(max_l_cm, points_e)

    # Выбор столбца k1 по отношению L2/H (H34)
    if ratio_l_h >= 0.75:
        k1 = k1_c
    elif ratio_l_h >= 0.66:
        k1 = k1_d
    elif ratio_l_h >= 0.5:
        k1 = k1_e
    else:
        k1 = 1.0

    k2 = _interp_by_table(max_l_cm, points_k2)

    return {'k1': round(k1, 2), 'k2': round(k2, 2)}

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

def calculate_mullion_requirements(q_norm, q_calc, h_m, b1, r_calc, is_single_span, railing_enabled, railing_norm_kn, safety_yn, mullion_deflection_ratio=200.0):
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

    # E39/E40
    if is_single_span:
        ix_req = (5.0 * (1.0 / k14) * q_norm * (h_m ** 3)) / (384.0 * e_module * 10000.0)
    else:
        ix_req = ((1.0 / k14) * q_norm * (h_m ** 3)) / (185.0 * e_module * 10000.0)

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
        'k14': k14
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
