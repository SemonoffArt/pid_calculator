"""
Симуляция замкнутой системы "ПИД + объект FOPDT".

Используются дискретные уравнения с шагом dt_sim; для устойчивости
интегрирования каждый шаг данных делится на подшаги (substeps).

Регулятор в идеальной форме:
    u = Kp*(e + 1/Ti*∫e dt + Td*de_f/dt), de_f — производная с фильтром.
Объект: dy/dt = (K*u(t - tau) - y)/T.
"""
from __future__ import annotations

import numpy as np


def _simulate(K: float, T: float, tau: float,
              kp: float, ti: float | None, td: float | None,
              d_filter_n: float, time: np.ndarray, sp: np.ndarray
              ) -> tuple[np.ndarray, np.ndarray]:
    """Дискретная симуляция замкнутого контура. Возвращает t, pv, cv."""
    n = len(time)
    dt = time[1] - time[0]
    m = 5                                   # подшаги внутри шага данных
    h = dt / m
    delay_steps = max(0, int(round(tau / h)))

    # Буфер задержки управляющего воздействия
    buf_len = delay_steps + m * 2
    ubuf = np.zeros(buf_len)
    ubuf[:] = 0.0                           # начальное воздействие

    y = sp[0]                               # начальное PV = начальное SP
    integ = 0.0                             # интеграл ошибки
    e_prev = sp[0] - y                      # предыдущая ошибка
    dfilt = 0.0                             # состояние D-фильтра

    total = (n - 1) * m
    out_t = np.empty(n)
    out_pv = np.empty(n)
    out_cv = np.empty(n)

    a = h / T if T > 0 else 1.0

    for k in range(total):
        i = k // m
        e = sp[i] - y

        if ti and ti > 0:
            integ += h * e / ti

        dterm = 0.0
        if td and td > 0:
            # Реальный дифференциатор с фильтром 1-го порядка:
            # dfilt/dt = N*(de/dt - dfilt), de/dt = (e - e_prev)/h
            d_raw = (e - e_prev) / h
            dfilt += (h * d_filter_n) * (d_raw - dfilt)
            dterm = td * dfilt
        e_prev = e

        u_raw = kp * (e + integ + dterm)

        # Насыщение выхода 0..100 %
        u = min(max(u_raw, 0.0), 100.0)

        # Запись в буфер задержки (кольцевой сдвиг)
        ubuf = np.roll(ubuf, -1)
        ubuf[-1] = u
        u_delayed = ubuf[0] if delay_steps < len(ubuf) else 0.0

        # Объект FOPDT (метод Эйлера)
        for _ in range(m):
            y += a * (K * u_delayed - y)

        if k % m == 0:
            j = k // m
            out_t[j] = time[j]
            out_pv[j] = y
            out_cv[j] = u

    out_t[-1], out_pv[-1], out_cv[-1] = time[-1], y, u
    return out_t, out_pv, out_cv


def simulate_closed_loop(K: float, T: float, tau: float, controller_type: str,
                         kp: float, ti: float | None, td: float | None,
                         dt_sim: float = 0.05, sim_time: float = 60.0,
                         sp_profile: str = "step",
                         sp_array: np.ndarray | None = None,
                         sp_start: float | None = None,
                         sp_target: float | None = None):
    """
    Симуляция отклика замкнутой системы.

    sp_profile="step" — ступенька задания величиной 10 % диапазона;
    sp_profile="array" — задание из массива данных (интерполированного);
    sp_start/sp_target — ручная ступенька задания от sp_start к sp_target
    (приоритетнее sp_profile/sp_array). Возвращает (time, sp, pv, cv).
    """
    if sp_start is not None and sp_target is not None:
        time = np.arange(0.0, sim_time + dt_sim * 0.5, dt_sim)
        sp = np.full(len(time), float(sp_start))
        step_at = int(min(0.1 * len(time), len(time) - 2))
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

    t, pv, cv = _simulate(K, T, tau, kp_eff, ti_eff, td_eff, n_filter, time, sp)
    return t, sp, pv, cv


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
                    cv: np.ndarray | None = None) -> dict:
    """Показатели качества переходного процесса.

    cv (опционально, 0..100 %) — если передан, дополнительно вычисляются
    доля времени насыщения регулятора и максимальный ход CV.
    """
    target = sp[-1]
    start = pv[0]
    delta = target - start
    if abs(delta) < 1e-12:
        base = {"overshoot": 0.0, "settling_time": 0.0, "iae": 0.0}
        sat_frac, cv_max = _saturation_metrics(cv)
        base["sat_frac"] = round(sat_frac, 4)
        base["cv_max"] = round(cv_max, 2)
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
    sat_frac, cv_max = _saturation_metrics(cv)
    return {"overshoot": round(float(overshoot), 2),
            "settling_time": round(float(settling), 3),
            "iae": round(iae, 4),
            "sat_frac": round(sat_frac, 4),
            "cv_max": round(cv_max, 2)}
