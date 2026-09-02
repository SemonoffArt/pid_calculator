"""
Оценка качества регулирования по фактическим данным процесса.

Анализирует реальную запись PV/SP/CV из загруженного CSV-файла: находит
ступеньку задания (SP), выделяет переходный процесс после неё и рассчитывает
классические показатели качества управления:
  - перерегулирование (overshoot), %;
  - время регулирования (settling time), с;
  - IAE — интеграл модуля ошибки (integral of absolute error).

В отличие от симуляции по модели (core.simulator.quality_metrics), здесь
оценивается то, как регулятор ОТРАБОТАЛ фактическую запись.
"""
from __future__ import annotations

import numpy as np

from core.data_loader import ProcessData, detect_sp_step

# Полоса времени регулирования — доля от величины ступеньки SP
SETTLING_BAND_FRAC = 0.02


def assess_regulation(data: ProcessData, band_frac: float = SETTLING_BAND_FRAC) -> dict:
    """
    Оценка качества регулирования по реальной записи PV/SP/CV.

    Возвращает словарь с метриками (overshoot, settling_time, iae) и контекстом
    (обнаружена ли ступенька SP, её момент, базовая и целевая точки).
    Если ступенька не обнаружена — вычисляется только IAE по всему участку.
    """
    time, sp, pv, cv = data.time, data.sp, data.pv, data.cv
    step_index = detect_sp_step(sp)

    base = {
        "step_detected": False,
        "step_index": None,
        "start_pv": None,
        "target_sp": None,
        "delta": None,
        "overshoot": 0.0,
        "settling_time": None,
        "settled": False,
        "band": None,
        "iae": 0.0,
        "duration": float(time[-1] - time[0]),
        "note": None,
    }

    # Недостаточно данных до/после ступеньки — оцениваем участок целиком
    if step_index is None or step_index < 5 or step_index >= len(time) - 5:
        base["iae"] = round(float(np.trapezoid(np.abs(sp - pv), time)), 4)
        base["note"] = ("Ступенька задания (SP) не обнаружена или слишком "
                        "близка к краю записи — переходный процесс не выделен, "
                        "IAE рассчитан по всей записи.")
        return base

    # Базовая точка ДО ступеньки (медиана окна перед ней)
    pre = slice(max(0, step_index - 20), step_index)
    start_pv = float(np.median(pv[pre]))
    # Целевое задание ПОСЛЕ ступеньки (медиана хвоста)
    target_sp = float(np.median(sp[step_index:]))
    delta = target_sp - start_pv

    base["step_detected"] = True
    base["step_index"] = step_index
    base["start_pv"] = round(start_pv, 4)
    base["target_sp"] = round(target_sp, 4)
    base["delta"] = round(delta, 4)

    # Задание практически не изменилось — переходного процесса нет
    if abs(delta) < 1e-12:
        base["iae"] = round(float(np.trapezoid(np.abs(sp - pv), time)), 4)
        base["note"] = ("Задание практически не изменилось — переходный "
                        "процесс отсутствует, IAE рассчитан по всей записи.")
        return base

    t_seg = time[step_index:]
    p_seg = pv[step_index:]
    s_seg = sp[step_index:]

    # --- Перерегулирование ---
    # Отклик в отклонениях от базовой линии, нормированный на величину ступеньки
    rel = (p_seg - start_pv) / delta
    peak = float(np.max(rel)) if delta > 0 else float(np.min(rel))
    overshoot = max(peak - 1.0, 0.0) * 100.0
    base["overshoot"] = round(overshoot, 2)

    # --- Время регулирования: момент входа PV в полосу ±band_frac·|delta| ---
    # и удержания в ней до конца записи
    band = band_frac * abs(delta)
    base["band"] = round(band, 4)
    outside = np.abs(p_seg - target_sp) > band
    settling = float("nan")
    for i in range(len(p_seg)):
        if not np.any(outside[i:]):
            settling = float(t_seg[i])
            break
    base["settled"] = bool(np.isfinite(settling))
    base["settling_time"] = round(settling, 3) if base["settled"] else None

    # --- IAE: интеграл модуля ошибки от момента ступеньки до конца записи ---
    base["iae"] = round(float(np.trapezoid(np.abs(s_seg - p_seg), t_seg)), 4)

    return base
