"""
Симуляция замкнутой системы "ПИД + объект FOPDT".

Регулятор в ISA-стандартной (параллельной) форме с фильтром на D:
    u = Kp*(e + 1/Ti*∫e dt + Td*de/dt/(1 + Td/N·s))
Здесь производная ошибки пропускается через звено первого порядка с
постоянной времени Td/N (N = d_filter_n). Объект интегрируется ТОЧНО
(ZOH-дискретизация звена первого порядка с запаздыванием), что при том же
шаге даёт устойчивый и корректный отклик в отличие от метода Эйлера.

Для предотвращения «раскрутки» интегратора при насыщении выхода
используется anti-windup (back-calculation). Насыщение CV (0..100 %)
может быть отключено (cv_clip=False) — например, для сравнения с
внешними калькуляторами.
"""
from __future__ import annotations

import numpy as np


def _simulate(K: float, T: float, tau: float,
              kp: float, ti: float | None, td: float | None,
              d_filter_n: float, time: np.ndarray, sp: np.ndarray,
              cv_clip: bool = True, cv_min: float = 0.0,
              cv_max: float = 100.0,
              substeps: int = 5,
              pv0: float | None = None,
              cv0: float | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Дискретная симуляция замкнутого контура. Возвращает t, pv, cv.

    Каждый шаг данных делится на подшаги (substeps), внутри которых объект
    интегрируется ТОЧНО (ZOH-дискретизация звена первого порядка). Мелкий
    подшаг важен для устойчивости контуров с усилением, близким к границе.

    pv0/cv0 — начальная рабочая точка: стартовое значение PV и хода CV
    (буфер запаздывания заполняется cv0, чтобы объект не «проседал» в первую
    секунду из-за нулевого начального управления). Если cv0 не задана,
    по умолчанию берётся равновесное значение sp[0]/K (старт без «провала»);
    если pv0 не задано — PV = SP[0].
    """
    n = len(time)
    dt = time[1] - time[0]
    m = max(1, int(substeps))
    h = dt / m
    delay_steps = max(0, int(round(tau / h)))

    y = pv0 if pv0 is not None else sp[0]   # начальное PV
    # Если начальная рабочая точка не задана — стартуем из равновесия:
    # CV = sp[0]/K удерживает PV на начальном задании без «проседания».
    # При sp[0] = 0 cv0 = 0 (совпадает с прежним поведением).
    if cv0 is None and K != 0:
        cv0 = float(sp[0]) / float(K)
    elif cv0 is None:
        cv0 = 0.0

    # Буфер задержки управляющего воздействия (кольцевой массив)
    buf_len = max(delay_steps + m + 2, m + 2)
    ubuf = np.zeros(buf_len)
    ubuf.fill(cv0)

    integ = 0.0                             # интеграл ошибки
    e_prev = sp[0] - y                      # предыдущая ошибка
    dfilt = 0.0                             # состояние D-фильтра
    if kp != 0:
        # Начинаем из равновесия: CV = Kp*(e + integ) при e=0 → integ = cv0/Kp
        integ = cv0 / kp

    # Точная ZOH-дискретизация на подшаге h: y = a*y + b*u_delayed
    if T > 0:
        a = np.exp(-h / T)
        b = K * (1.0 - a)
    else:
        a = 0.0
        b = K

    total = (n - 1) * m
    out_pv = np.empty(n)
    out_cv = np.empty(n)

    for k in range(total):
        i = k // m
        e = sp[i] - y

        if ti and ti > 0:
            integ += h * e / ti

        dterm = 0.0
        if td and td > 0:
            # ISA-стандарт: u = Kp*(e + 1/Ti*∫e dt + Td*de/dt/(1 + Td/N·s))
            # Производная фильтруется звеном с постоянной времени Td/N.
            # Состояние dfilt описывается: d(dfilt)/dt = (N/Td)*(de/dt - dfilt),
            # а D-составляющая равна Td*dfilt.
            d_raw = (e - e_prev) / h
            dfilt += (h * d_filter_n / td) * (d_raw - dfilt)
            dterm = td * dfilt
        e_prev = e

        u_raw = kp * (e + integ + dterm)

        # Насыщение выхода (опционально)
        if cv_clip:
            u = min(max(u_raw, cv_min), cv_max)
        else:
            u = u_raw

        # Anti-windup (back-calculation): при насыщении возвращаем интеграл
        # к значению, согласованному с насыщенным выходом.
        if cv_clip and u != u_raw and kp != 0:
            integ = (u / kp) - e - dterm

        # Запись в буфер задержки (кольцевой сдвиг)
        ubuf = np.roll(ubuf, -1)
        ubuf[-1] = u
        u_delayed = ubuf[0] if delay_steps < len(ubuf) else 0.0

        # Объект FOPDT — точная дискретная модель на подшаге
        y = a * y + b * u_delayed

        if k % m == 0:
            j = k // m
            out_pv[j] = y
            out_cv[j] = u

    out_pv[-1] = y
    out_cv[-1] = u
    return out_pv, out_cv


def simulate_closed_loop(K: float, T: float, tau: float, controller_type: str,
                         kp: float, ti: float | None, td: float | None,
                         dt_sim: float = 0.05, sim_time: float = 60.0,
                         sp_profile: str = "step",
                         sp_array: np.ndarray | None = None,
                         sp_start: float | None = None,
                         sp_target: float | None = None,
                         cv_clip: bool = True, cv_min: float = 0.0,
                         cv_max: float = 100.0,
                         pv0: float | None = None,
                         cv0: float | None = None):
    """
    Симуляция отклика замкнутой системы.

    sp_profile="step" — ступенька задания величиной 10 % диапазона;
    sp_profile="array" — задание из массива данных (интерполированного);
    sp_start/sp_target — ручная ступенька задания от sp_start к sp_target
    (приоритетнее sp_profile/sp_array).
    cv_clip — ограничивать ли выход регулятора диапазоном [cv_min, cv_max].
    pv0/cv0 — начальная рабочая точка (стартовое PV и ход CV); если не
    заданы, PV стартует с SP[0], CV с 0.
    Возвращает (time, sp, pv, cv).
    """
    if sp_start is not None and sp_target is not None:
        time = np.arange(0.0, sim_time + dt_sim * 0.5, dt_sim)
        sp = np.full(len(time), float(sp_start))
        # Ступенька задания подаётся в момент T + τ (постоянная времени +
        # запаздывание объекта): к этому моменту процесс успевает выйти на
        # установившийся режим от начальной рабочей точки.
        step_time = max(T + tau, dt_sim)
        step_at = int(min(round(step_time / dt_sim), len(time) - 2))
        sp[step_at:] = float(sp_target)
    elif sp_profile == "array" and sp_array is not None and len(sp_array) > 1:
        src_t = np.linspace(0.0, sim_time, len(sp_array))
        time = np.arange(0.0, sim_time + dt_sim * 0.5, dt_sim)
        sp = np.interp(time, src_t, sp_array)
    else:
        span = max(abs(sp_array[0] - sp_array[-1]), 1.0) \
            if sp_array is not None and len(sp_array) > 1 else 1.0
        time = np.arange(0.0, sim_time + dt_sim * 0.5, dt_sim)
        sp = np.full(len(time), 0.0)
        step_at = int(min(0.1 * len(time), len(time) - 2))
        sp[step_at:] = span  # ступенька после разгона

    ti_eff = ti if (controller_type in ("PI", "PID") and ti) else None
    td_eff = td if (controller_type in ("PID",) and td) else None
    n_filter = 10.0  # коэффициент фильтра D-составляющей

    # Знак усиления объекта: при K < 0 регулятор должен действовать
    # реверсивно (уменьшать выход при росте ошибки), иначе контур разойдётся.
    # Коэффициенты настройки задаются по модулю усиления.
    kp_eff = np.sign(K) * kp

    pv, cv = _simulate(K, T, tau, kp_eff, ti_eff, td_eff, n_filter, time, sp,
                       cv_clip=cv_clip, cv_min=cv_min, cv_max=cv_max,
                       pv0=pv0, cv0=cv0)
    return time, sp, pv, cv


def _saturation_metrics(cv: np.ndarray, lo: float = 0.0,
                        hi: float = 100.0) -> tuple[float, float]:
    """Доля времени и максимальный ход, когда CV упирается в пределы.

    Возвращает (sat_frac, cv_max): sat_frac — доля точек, где CV на границе
    (±0.5 % от предела), cv_max — максимум CV.
    """
    if cv is None or len(cv) == 0:
        return 0.0, 0.0
    tol = 0.005 * (hi - lo)
    at_bound = np.isclose(cv, lo, atol=tol) | np.isclose(cv, hi, atol=tol)
    sat_frac = float(np.mean(at_bound))
    return sat_frac, float(np.max(cv))


def quality_metrics(time: np.ndarray, sp: np.ndarray, pv: np.ndarray,
                    cv: np.ndarray | None = None,
                    cv_min: float = 0.0, cv_max: float = 100.0) -> dict:
    """Показатели качества переходного процесса.

    cv (опционально) — если передан, дополнительно вычисляются доля времени
    насыщения регулятора и максимальный ход CV. Диапазон CV задаётся
    cv_min..cv_max (по умолчанию 0..100 %).
    """
    target = sp[-1]
    start = pv[0]
    delta = target - start
    if abs(delta) < 1e-12:
        base = {"overshoot": 0.0, "settling_time": 0.0, "iae": 0.0}
        sat_frac, cv_max_v = _saturation_metrics(cv, cv_min, cv_max)
        base["sat_frac"] = round(sat_frac, 4)
        base["cv_max"] = round(cv_max_v, 2)
        return base

    peak = float(np.max((pv - start) / delta)) if delta > 0 else \
        float(np.min((pv - start) / delta))
    overshoot = max(peak - 1.0, 0.0) * 100

    band = 0.02 * abs(delta)
    outside = np.abs(pv - target) > band
    settling = 0.0
    for i in range(len(pv)):
        if not np.any(outside[i:]):
            settling = time[i]
            break
    else:
        settling = float("nan")  # процесс не установился

    iae = float(np.trapezoid(np.abs(sp - pv), time))
    sat_frac, cv_max_v = _saturation_metrics(cv, cv_min, cv_max)
    return {"overshoot": round(float(overshoot), 2),
            "settling_time": round(float(settling), 3),
            "iae": round(iae, 4),
            "sat_frac": round(sat_frac, 4),
            "cv_max": round(cv_max_v, 2)}
