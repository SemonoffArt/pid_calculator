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

from core.data_loader import ProcessData

# Полоса времени регулирования — доля от величины ступеньки SP
SETTLING_BAND_FRAC = 0.02
# Порог обнаружения ступеньки SP — доля от размаха сигнала
STEP_THRESHOLD = 0.05
# Минимум точек до/после ступеньки для корректного выделения переходного процесса
_MIN_EDGE = 5
# Окно усреднения базовой точки (число точек перед ступенькой)
_BASELINE_WINDOW = 20


def _find_sp_steps(sp: np.ndarray, threshold: float) -> list[int]:
    """
    Индексы точек сразу после каждого скачка SP, превышающего порог.

    Порог — доля от размаха сигнала. Возвращает список индексов (пустой,
    если ступенек нет). Несколько ступенек в записи — признак того, что
    задание менялось несколько раз (например, двойной ступенчатый тест).
    """
    span = float(np.max(sp) - np.min(sp))
    if span <= 0:
        return []
    jumps = np.abs(np.diff(sp))
    th = threshold * span
    return [i + 1 for i, j in enumerate(jumps) if j >= th and j > 0]


def assess_regulation(data: ProcessData, band_frac: float | None = SETTLING_BAND_FRAC,
                      step_threshold: float | None = STEP_THRESHOLD,
                      step_index: int | None = None) -> dict:
    """
    Оценка качества регулирования по реальной записи PV/SP/CV.

    band_frac — полоса установления как доля от величины ступеньки SP
    (по умолчанию 2 %). step_threshold — порог обнаружения ступеньки SP как
    доля от размаха сигнала (по умолчанию 5 %). Если передано None — берётся
    значение по умолчанию.

    step_index — ручной выбор ступеньки (индекс точки после скачка SP).
    Если None — автоматически берётся ПОСЛЕДНЯЯ из обнаруженных ступенек
    (запись обычно заканчивается установившимся режимом именно после неё).
    Если в записи несколько ступенек — добавляется поясняющая note.

    Возвращает словарь с метриками (overshoot, settling_time, iae), контекстом
    (обнаружена ли ступенька, её момент, базовая и целевая точки), списком всех
    обнаруженных ступенек step_indices и источником выбора step_source.
    settling_time измеряется ОТ момента ступеньки (длительность переходного
    процесса), а не от начала записи. Если ступенька не обнаружена — вычисляется
    только IAE по всему участку.
    """
    time, sp, pv, cv = data.time, data.sp, data.pv, data.cv
    if band_frac is None or band_frac <= 0:
        band_frac = SETTLING_BAND_FRAC
    if step_threshold is None or step_threshold <= 0:
        step_threshold = STEP_THRESHOLD

    step_indices = _find_sp_steps(sp, step_threshold)
    step_source = "auto"
    chosen = None
    if step_index is not None and step_index in step_indices:
        chosen, step_source = step_index, "manual"
    elif step_indices:
        chosen = step_indices[-1]   # по умолчанию — последняя ступенька

    base = {
        "step_detected": False,
        "step_index": None,
        "step_source": step_source,
        "step_indices": step_indices,
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
    if chosen is None or chosen < _MIN_EDGE or chosen >= len(time) - _MIN_EDGE:
        base["iae"] = round(float(np.trapezoid(np.abs(sp - pv), time)), 4)
        base["note"] = ("Ступенька задания (SP) не обнаружена или слишком "
                        "близка к краю записи — переходный процесс не выделен, "
                        "IAE рассчитан по всей записи.")
        return base

    # Базовая точка ДО ступеньки (медиана окна перед ней)
    pre = slice(max(0, chosen - _BASELINE_WINDOW), chosen)
    start_pv = float(np.median(pv[pre]))
    # Целевое задание ПОСЛЕ ступеньки (медиана хвоста)
    target_sp = float(np.median(sp[chosen:]))
    delta = target_sp - start_pv

    base["step_detected"] = True
    base["step_index"] = chosen
    base["start_pv"] = round(start_pv, 4)
    base["target_sp"] = round(target_sp, 4)
    base["delta"] = round(delta, 4)

    # Задание практически не изменилось — переходного процесса нет
    if abs(delta) < 1e-12:
        base["iae"] = round(float(np.trapezoid(np.abs(sp - pv), time)), 4)
        base["note"] = ("Задание практически не изменилось — переходный "
                        "процесс отсутствует, IAE рассчитан по всей записи.")
        return base

    # Несколько ступенек — поясняем выбор (только в автоматическом режиме)
    if step_source == "auto" and len(step_indices) > 1:
        times = ", ".join(f"{time[i]:.0f} с" for i in step_indices)
        base["note"] = (f"В записи несколько изменений задания ({times}). "
                        f"Оценка выполнена по последней ступеньке "
                        f"({time[chosen]:.0f} с). Можно выбрать другую ступеньку "
                        "в настройках оценки.")

    t_seg = time[chosen:]
    p_seg = pv[chosen:]
    s_seg = sp[chosen:]

    # --- Перерегулирование ---
    # Отклик в отклонениях от базовой линии, нормированный на величину ступеньки.
    # rel > 1 означает «перелёт за задание» в сторону ступеньки — и для роста,
    # и для снижения SP (в нормировке на delta знак уже учтён).
    rel = (p_seg - start_pv) / delta
    peak = float(np.max(rel))
    overshoot = max(peak - 1.0, 0.0) * 100.0
    base["overshoot"] = round(overshoot, 2)

    # --- Время регулирования: момент входа PV в полосу ±band_frac·|delta| ---
    # и удержания в ней до конца записи. Отсчитывается ОТ МОМЕНТА СТУПЕНЬКИ
    # (время регулирования = длительность переходного процесса, а не время по
    # часам): если ступенька в t=40 с и PV вошёл в полосу в t=72 с, результат
    # равен 72 − 40 = 32 с.
    band = band_frac * abs(delta)
    base["band"] = round(band, 4)
    outside = np.abs(p_seg - target_sp) > band
    settling = float("nan")
    for i in range(len(p_seg)):
        if not np.any(outside[i:]):
            settling = float(t_seg[i] - t_seg[0])
            break
    base["settled"] = bool(np.isfinite(settling))
    base["settling_time"] = round(settling, 3) if base["settled"] else None

    # --- IAE: интеграл модуля ошибки от момента ступеньки до конца записи ---
    base["iae"] = round(float(np.trapezoid(np.abs(s_seg - p_seg), t_seg)), 4)

    return base
