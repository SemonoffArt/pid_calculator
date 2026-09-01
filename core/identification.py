"""
Идентификация модели объекта управления FOPDT:

    G(s) = K * exp(-tau*s) / (T*s + 1)

Методы:
1. По переходной характеристике (метод двух точек 28.3 % / 63.2 %).
2. Аппроксимация произвольного сигнала МНК (scipy.optimize.curve_fit).
3. Релейный метод — оценка критических параметров Ku, Tu по автоколебаниям.

Также оценивает критические параметры замкнутого контура (Ku, Tu) из FOPDT.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import curve_fit, minimize


def _safe_median(arr: np.ndarray) -> float:
    """Медиана без RuntimeWarning для пустого среза."""
    if arr.size == 0:
        return float("nan")
    return float(np.median(arr))


@dataclass
class FopdtModel:
    """Параметры модели первого порядка с запаздыванием."""
    K: float      # коэффициент усиления (ед. PV / % CV); может быть отрицательным
    T: float      # постоянная времени, сек
    tau: float    # чистое запаздывание, сек
    method: str = ""   # способ идентификации
    fit_quality: float = float("nan")  # R^2 аппроксимации


@dataclass
class IpdtModel:
    """
    Параметры интегрирующего звена с запаздыванием:

        G(s) = Ka * exp(-tau*s) / s

    Ka — интегральный коэффициент усиления (скорость нарастания PV на
    единицу изменения CV): [ед. PV / (% CV * сек)]. Может быть отрицательным
    (обратное действие). tau — чистое запаздывание, сек.
    """
    Ka: float     # интегральный коэффициент усиления, ед. PV / (%CV * с)
    tau: float    # чистое запаздывание, сек
    method: str = ""
    fit_quality: float = float("nan")
    m0: float = 0.0   # наклон базы до ступеньки (дрейф уровня), ед. PV/с
    balance: float = 0.0  # балансный ход CV, при котором уровень «стоит»


# Минимальный допустимый R² аппроксимации; ниже — данные непригодны
MIN_FIT_R2 = 0.15


def fopdt_response(time: np.ndarray, u: np.ndarray, K: float, T: float,
                   tau: float) -> np.ndarray:
    """Отклик FOPDT-звена на входной сигнал u(t) (дискретная Эйлерова модель)."""
    dt = time[1] - time[0]
    n = len(time)
    delay_samples = max(0, int(round(tau / dt)))
    u_delayed = np.empty_like(u)
    if delay_samples >= n:
        u_delayed[:] = u[0]
    else:
        u_delayed[delay_samples:] = u[: n - delay_samples]
        u_delayed[:delay_samples] = u[0]

    # Начальное значение выхода — первое измерение (для статики)
    y = np.empty(n)
    y0 = u_delayed[0] * K
    y[0] = y0
    a = dt / T if T > 0 else 1.0
    # Переполнения неизбежны при пробных параметрах в curve_fit
    # (экстремально малые T) — подавляем, некорректные кандидаты
    # отбрасываются по величине остатка.
    with np.errstate(over="ignore", invalid="ignore"):
        for i in range(1, n):
            y[i] = y[i - 1] + a * (K * u_delayed[i - 1] - y[i - 1])
    return y


def ipdt_response(time: np.ndarray, u: np.ndarray, Ka: float,
                  tau: float, y0: float = 0.0) -> np.ndarray:
    """
    Отклик интегрирующего звена с запаздыванием на входной сигнал u(t).

        y[k+1] = y[k] + dt * Ka * u(t - tau)

    Дискретная модель точная (ZOH-интегрирование). Начальное значение —
    y0 (обычно 0 — отклик в приращениях).
    """
    dt = time[1] - time[0]
    n = len(time)
    delay_samples = max(0, int(round(tau / dt)))
    u_delayed = np.empty_like(u)
    if delay_samples >= n:
        u_delayed[:] = u[0]
    else:
        u_delayed[delay_samples:] = u[: n - delay_samples]
        u_delayed[:delay_samples] = u[0]

    y = np.empty(n)
    y[0] = y0
    with np.errstate(over="ignore", invalid="ignore"):
        for i in range(1, n):
            y[i] = y[i - 1] + dt * Ka * u_delayed[i - 1]
    return y


# ---------------------------------------------------------------- step response
def _next_event_index(cv: np.ndarray, start: int, threshold: float) -> int:
    """Индекс следующей крупной ступеньки CV после `start` (или конец записи)."""
    for i in range(start + 2, len(cv)):
        if abs(cv[i] - cv[i - 2]) > threshold * (np.max(cv) - np.min(cv)):
            return i
    return len(cv)


def identify_step_response(time: np.ndarray, cv: np.ndarray, pv: np.ndarray,
                           step_index: int | None) -> FopdtModel:
    """
    Метод двух точек по переходной характеристике.

    Анализируется участок от ступеньки CV до следующего возмущения;
    по моментам достижения 28.3 % и 63.2 % от установившегося изменения PV
    вычисляются T и tau; K — по отношению установившихся изменений.
    """
    if step_index is None or step_index >= len(cv) - 3:
        raise ValueError("В данных не обнаружена ступенька для метода "
                         "переходной характеристики.")

    seg_end = _next_event_index(cv, step_index, 0.05)

    pre = slice(max(0, step_index - 20), step_index)
    y0 = _safe_median(pv[pre])
    u0 = _safe_median(cv[pre])

    # Установившиеся значения — медиана последней четверти участка
    q_start = step_index + max(3, int(0.75 * (seg_end - step_index)))
    yss = _safe_median(pv[q_start:seg_end])
    uss = _safe_median(cv[q_start:seg_end])

    du = uss - u0
    dy = yss - y0
    if not np.isfinite(du) or not np.isfinite(dy):
        raise ValueError("Недостаточно данных для определения установившихся "
                         "режимов до и после ступеньки.")
    if abs(du) < 1e-9:
        raise ValueError("Не удалось определить величину изменения CV.")
    if abs(dy) < 1e-12:
        raise ValueError("Выходная величина не изменилась после ступеньки.")
    K = dy / du

    target28 = y0 + 0.283 * dy
    target63 = y0 + 0.632 * dy
    seg_t = time[step_index:seg_end]
    seg_pv = pv[step_index:seg_end]

    def crossing(level: float) -> float:
        above = seg_pv >= level if dy > 0 else seg_pv <= level
        idx = np.argmax(above)
        if not above[idx]:
            raise ValueError("Переходная характеристика не достигла "
                             f"уровня {level:.3g} — данных недостаточно.")
        if idx == 0:
            return float(seg_t[0])
        t1, t2, y1, y2 = seg_t[idx - 1], seg_t[idx], seg_pv[idx - 1], seg_pv[idx]
        frac = 0.5 if y2 == y1 else (level - y1) / (y2 - y1)
        return float(t1 + frac * (t2 - t1))

    t28 = crossing(target28)
    t63 = crossing(target63)

    # Момент ступеньки относительно начала отсчёта кривой разгона
    t0 = float(seg_t[0])
    T = 1.5 * (t63 - t28)
    tau = max((t63 - t0) - T, 0.0)
    if T <= 0:
        raise ValueError("Оценка постоянной времени неположительна — "
                         "проверьте качество данных.")
    fitted = fopdt_response(time, cv, K, T, tau)
    ss_tot = float(np.sum((pv - np.mean(pv)) ** 2))
    ss_res = float(np.sum((pv - fitted) ** 2))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else float("-inf")
    if r2 < MIN_FIT_R2:
        # Переходная характеристика плохо описывается FOPDT
        raise ValueError(f"Аппроксимация по переходной характеристике плохая "
                         f"(R²={r2:.2f}). Попробуйте метод МНК.")
    return FopdtModel(K=K, T=T, tau=tau, method="step", fit_quality=r2)


# ---------------------------------------------------------- IPDT step response
def identify_ipdt_step_response(time: np.ndarray, cv: np.ndarray,
                                pv: np.ndarray, step_index: int | None
                                ) -> IpdtModel:
    """
    Идентификация интегрирующего звена с запаздыванием (IPDT) по переходной
    характеристике — методом «изменения наклона».

    Интегратор (dy/dt = Ka·CV) при постоянном ненулевом CV непрерывно
    дрейфует, поэтому ровная база — лишь частный случай. По наклонам
    ДО (m0) и ПОСЛЕ (m1) ступеньки CV определяется
        Ka = (m1 − m0) / du,
    а запаздывание τ — по абсциссе пересечения двух линий разгона
    (минус момент ступеньки). При m0 ≈ 0 формула сводится к классическому
    случаю ровной базы.
    """
    if step_index is None or step_index >= len(cv) - 3:
        raise ValueError("В данных не обнаружена ступенька для метода "
                         "переходной характеристики.")

    seg_end = _next_event_index(cv, step_index, 0.05)

    u0 = _safe_median(cv[max(0, step_index - 20):step_index])
    q_start = step_index + max(3, int(0.5 * (seg_end - step_index)))
    uss = _safe_median(cv[q_start:seg_end])
    du = uss - u0
    if not np.isfinite(du):
        raise ValueError("Недостаточно данных для определения режимов "
                         "до и после ступеньки.")
    if abs(du) < 1e-9:
        raise ValueError("Не удалось определить величину изменения CV.")

    def _slope(tt: np.ndarray, yy: np.ndarray) -> tuple[float, float]:
        """Наклон и сдвиг по МНК с центрированием (численно устойчиво)."""
        xm = float(np.mean(tt))
        ym = float(np.mean(yy))
        denom = float(np.sum((tt - xm) ** 2))
        if denom < 1e-12:
            raise ValueError("Слишком короткий участок для оценки наклона.")
        slope = float(np.sum((tt - xm) * (yy - ym)) / denom)
        return slope, float(ym - slope * xm)

    # --- Наклон ДО ступеньки (уровень может дрейфовать с «начальным углом») ---
    m0 = 0.0
    a0 = 0.0
    pre_avail = step_index
    if pre_avail >= 6:
        pre_len = min(pre_avail, max(5, int(0.5 * pre_avail)))
        pre_t = time[step_index - pre_len:step_index]
        pre_pv = pv[step_index - pre_len:step_index]
        m0, a0 = _slope(pre_t, pre_pv)
        if not np.isfinite(m0):
            raise ValueError("Некорректный наклон участка до ступеньки.")
        if abs(m0) < 1e-12:
            # Уровень до ступеньки стоял: горизонтальная линия на уровне y0
            m0 = 0.0
            a0 = float(np.mean(pre_pv))
    else:
        # Мало данных до ступеньки — считаем базу горизонтальной на уровне
        # доступных точек до ступеньки (частный случай без дрейфа).
        pre_pv = pv[max(0, step_index - pre_avail):step_index]
        a0 = float(_safe_median(pre_pv)) if pre_pv.size else float(pv[0])

    # --- Наклон ПОСЛЕ ступеньки по устойчивому участку рампы (последние 60 %) ---
    t_seg = time[step_index:seg_end]
    pv_seg = pv[step_index:seg_end]
    if len(t_seg) < 6:
        raise ValueError("Слишком короткий участок после ступеньки.")
    req_len = max(5, int(0.6 * len(t_seg)))
    m1, a1 = _slope(t_seg[-req_len:], pv_seg[-req_len:])
    if not np.isfinite(m1) or abs(m1) < 1e-12:
        raise ValueError("Наклон ПВ после ступеньки слишком мал — "
                         "процесс не интегрирующий.")

    # --- Ka по изменению наклона ---
    dslope = m1 - m0
    if abs(dslope) < 1e-12:
        raise ValueError("Скорость изменения PV после ступеньки не "
                         "изменилась — ступенька CV не отработала.")
    Ka = dslope / du

    # Балансный ход CV (при котором уровень «стоит») из уравнения дрейфа:
    # m0 = Ka*(u0 - balance)  =>  balance = u0 - m0/Ka
    balance = float(u0) - m0 / Ka if abs(Ka) > 1e-12 else float(u0)

    # --- Запаздывание: пересечение двух линий разгона ---
    t_step = float(time[step_index])
    if abs(m0 - m1) < 1e-12:
        t_int = t_step
    else:
        # a0 + m0*t = a1 + m1*t  =>  t = (a1 - a0)/(m0 - m1)
        t_int = float((a1 - a0) / (m0 - m1))
    tau = max(t_int - t_step, 0.0)

    # R^2 по всему отклику. Модель = базовый дрейф (a0 + m0*t) + интеграл
    # ОТКЛОНЕНИЯ CV от начального значения (cv - cv[0]). Так модель
    # воспроизводит и ровную базу, и дрейфующий уровень, и рампу после
    # ступеньки; интеграл по абсолютному CV ошибочен, если у объекта есть
    # балансный ход CV (уровень «стоит» при ненулевом CV).
    fitted = float(pv[0]) + m0 * time + ipdt_response(
        time, cv - cv[0], Ka, tau)
    ss_tot = float(np.sum((pv - np.mean(pv)) ** 2))
    ss_res = float(np.sum((pv - fitted) ** 2))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else float("-inf")
    if not np.isfinite(r2) or r2 < MIN_FIT_R2:
        raise ValueError(f"Аппроксимация IPDT неудовлетворительна "
                         f"(R²={r2:.2f}). Проверьте данные.")
    return IpdtModel(Ka=Ka, tau=tau, method="step", fit_quality=r2, m0=m0,
                     balance=balance)


# ------------------------------------------------------------ least squares fit
def identify_curve_fit(time: np.ndarray, cv: np.ndarray, pv: np.ndarray) -> FopdtModel:
    """Аппроксимация FOPDT методом наименьших квадратов по всему сигналу."""
    span_cv = float(np.max(cv) - np.min(cv))
    if span_cv < 1e-9:
        raise ValueError("Сигнал CV не изменяется — идентификация невозможна.")

    # Начальные приближения: статический коэффициент (обе знаки —
    # у реальных объектов бывает отрицательное усиление),
    # T ~ 25 % длины записи
    span_pv = float(np.max(pv) - np.min(pv))
    K0 = abs((float(np.mean(pv[-max(3, len(pv)//10):])) -
              float(np.mean(pv[:max(3, len(pv)//10)])))) / max(span_cv, 1e-9)
    K0 = float(np.clip(K0, 1e-9, None))

    # Работаем в отклонениях от начальной точки; свободный параметр c
    # компенсирует смещение нуля измерений.
    cv_dev = cv - float(cv[0])
    duration = float(time[-1] - time[0])

    def model(t, K, T, tau, c):
        # fopdt_response в отклонениях: выход начинается с нуля
        return c + fopdt_response(t, cv_dev, K, abs(T), abs(tau))

    # Мультистарт: перебираем начальные приближения (включая знак K).
    # Нижняя граница tau — полшага дискретизации: меньшее запаздывание
    # принципиально не разрешимо по данным и делает формулы
    # Зиглера–Николса вырожденными.
    min_tau = max(0.5 * (time[1] - time[0]), 1e-6)
    candidates: list[tuple[np.ndarray, float]] = []
    for k_start in (K0, -K0):
        for t_start in (duration / 10, duration / 4, duration / 2):
            for tau_start in (min_tau, max(duration / 50, time[1] - time[0])):
                try:
                    popt, _ = curve_fit(
                        model, time, pv,
                        p0=[k_start, t_start, tau_start, float(np.mean(pv[:3]))],
                        bounds=([-np.inf, 1e-4, min_tau, -np.inf],
                                [np.inf, duration * 2, duration, np.inf]),
                        maxfev=6000,
                    )
                except (RuntimeError, ValueError):
                    continue
                resid = float(np.sum((pv - model(time, *popt)) ** 2))
                candidates.append((popt, resid))

    if not candidates:
        raise ValueError("Не удалось аппроксимировать модель FOPDT "
                         "(МНК не сошёлся).")

    # Итоговая модель — лучший остаток среди кандидатов мультистарта
    popt = min(candidates, key=lambda c: c[1])[0]

    K, T, tau, _c = popt
    ss_res = float(np.sum((pv - model(time, *popt)) ** 2))
    ss_tot = float(np.sum((pv - np.mean(pv)) ** 2))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    if not np.isfinite(r2) or r2 < MIN_FIT_R2:
        raise ValueError(f"Аппроксимация FOPDT неудовлетворительна "
                         f"(R²={r2:.2f}). Проверьте данные.")
    return FopdtModel(K=float(K), T=float(T), tau=float(tau),
                      method="curve_fit", fit_quality=r2)


# ------------------------------------------------------------------ relay test
def identify_relay(pv: np.ndarray, cv: np.ndarray, dt: float
                   ) -> tuple[float, float]:
    """
    Релейный метод: оценка критического усиления Ku и периода автоколебаний Tu.

    Предполагается, что в данных есть автоколебания PV амплитудой `a`
    и CV колеблется с амплитудой `d` (релейный уровень):
        Ku = 4*d / (pi*a),  Tu = период колебаний.
    """
    pv_ac = pv - np.median(pv)
    cv_ac = cv - np.median(cv)

    # Период по пересечениям нуля детрендированного PV
    amp = float(np.percentile(pv_ac, 97.5) - np.percentile(pv_ac, 2.5)) / 2
    if amp < 1e-9:
        raise ValueError("Автоколебания не обнаружены.")

    # Пересечения нуля со знаком; минимальное расстояние отсекает шумовые
    signs = pv_ac >= 0
    crossings = []
    for i in range(1, len(signs)):
        if signs[i] != signs[i - 1]:
            # отбрасываем дребезг: пересечение ближе 5 точек к предыдущему
            if not crossings or i - crossings[-1] > 5:
                crossings.append(i)
    if len(crossings) < 3:
        raise ValueError("Недостаточно колебаний для оценки периода.")

    # Период автоколебаний: расстояние между соседними пересечениями
    # равно половине периода
    gaps = np.diff(crossings) * dt
    tu = float(2 * np.median(gaps))
    if tu <= 0:
        raise ValueError("Некорректная оценка периода автоколебаний.")

    d = float(np.percentile(cv_ac, 97.5) - np.percentile(cv_ac, 2.5)) / 2
    a = float(np.percentile(pv_ac, 97.5) - np.percentile(pv_ac, 2.5)) / 2
    if a <= 0 or d <= 0:
        raise ValueError("Не удалось оценить амплитуды автоколебаний.")
    ku = 4.0 * d / (np.pi * a)
    return float(ku), tu


# --------------------------------------------- critical params from FOPDT model
def critical_from_fopdt(model: FopdtModel) -> tuple[float, float]:
    """
    Оценка критических параметров замкнутого контура из FOPDT-модели.

    Фазовая характеристика на частоте omega_u равна -pi:
        omega_u*tau + atan(omega_u*T) = pi
    Тогда Ku = sqrt(1 + (omega_u*T)^2) / K, Tu = 2*pi/omega_u.
    """
    if model.tau <= 0 and model.T <= 0:
        raise ValueError("Некорректная модель для расчёта критических параметров.")

    def phase(w: float) -> float:
        return w * model.tau + np.arctan(w * model.T) - np.pi

    lo, hi = 1e-9, np.pi / max(model.tau, 1e-9)
    # Расширяем диапазон до смены знака фазовой характеристики
    tries = 0
    while phase(hi) < 0 and tries < 60:
        hi *= 2
        tries += 1
    if phase(hi) < 0:
        raise ValueError("Не удалось найти частоту фазы -180° "
                         "(слишком малое запаздывание).")
    for _ in range(200):
        mid = (lo + hi) / 2
        if phase(mid) > 0:
            hi = mid
        else:
            lo = mid
    wu = (lo + hi) / 2
    ku = float(np.sqrt(1 + (wu * model.T) ** 2) / abs(model.K))
    tu = float(2 * np.pi / wu)
    return ku, tu


def critical_from_ipdt(model: IpdtModel) -> tuple[float, float]:
    """
    Оценка критических параметров замкнутого контура из IPDT-модели.

    Для G(s) = Ka*s^-1*exp(-tau*s) фазовый сдвиг -180°: omega_u*tau = pi/2,
    откуда Ku = omega_u/Ka = pi/(2*tau*Ka), Tu = 2*pi/omega_u = 4*tau.
    """
    if model.tau <= 0 or model.Ka == 0:
        raise ValueError("Некорректная модель IPDT для расчёта "
                         "критических параметров.")
    wu = np.pi / (2 * model.tau)
    ku = wu / abs(model.Ka)
    tu = 2 * np.pi / wu
    return float(ku), float(tu)


def _identify_ipdt(data, method: str, results: dict) -> dict:
    """Идентификация интегрирующего звена с запаздыванием (IPDT)."""
    errors: list[str] = []

    if method in ("step", "curve_fit", "auto"):
        # Для IPDT доступна единственная аппроксимация — по переходной
        # характеристике; режим "curve_fit" (FOPDT-МНК) сводится к ней же.
        try:
            results["model"] = identify_ipdt_step_response(
                data.time, data.cv, data.pv, data.step_index)
        except ValueError as exc:
            errors.append(str(exc))

    if results["model"] is None and method == "auto":
        # Возможен только один способ для IPDT — по переходной характеристике;
        # при повторном неудачном проходе ошибка уже сформирована.
        pass

    if method == "relay":
        ku, tu = identify_relay(data.pv, data.cv, data.dt)
        results["Ku"], results["Tu"] = ku, tu
        results["relay"] = True
        if results["model"] is None:
            results["model_error"] = "; ".join(errors) if errors else ""
        return results

    if method not in ("step", "curve_fit", "auto"):
        raise ValueError(f"Неизвестный метод идентификации: {method}")

    if results["model"] is None:
        raise ValueError("Идентификация IPDT не удалась. " +
                         ("; ".join(errors) if errors else
                          "Нужна ступенька CV в данных."))

    try:
        results["Ku"], results["Tu"] = critical_from_ipdt(results["model"])
    except ValueError:
        pass

    warnings: list[str] = []
    r2 = results["model"].fit_quality
    if np.isfinite(r2) and r2 < 0.5:
        warnings.append(
            f"Низкое качество аппроксимации модели IPDT (R²={r2:.2f}). "
            "Возможные причины: короткий участок разгона, шум PV, "
            "преобладание возмущений. Результатам доверять осторожно.")
    results["warnings"] = warnings
    results["errors"] = errors
    return results


def identify(data, method: str = "auto", model_type: str = "fopdt") -> dict:
    """
    Главная функция идентификации. Возвращает словарь с параметрами модели
    и (при возможности) критическими параметрами контура.

    model_type: "fopdt" — модель первого порядка с запаздыванием;
                "ipdt"  — интегрирующее звено с запаздыванием.
    """
    from core.data_loader import ProcessData  # локальный импорт против цикла

    assert isinstance(data, ProcessData)
    results: dict = {"model": None, "Ku": None, "Tu": None,
                     "model_type": model_type}

    if model_type == "ipdt":
        return _identify_ipdt(data, method, results)

    errors: list[str] = []

    if method in ("step", "auto"):
        try:
            results["model"] = identify_step_response(
                data.time, data.cv, data.pv, data.step_index)
        except ValueError as exc:
            errors.append(str(exc))

    if results["model"] is None and method in ("curve_fit", "auto"):
        try:
            results["model"] = identify_curve_fit(data.time, data.cv, data.pv)
        except ValueError as exc:
            errors.append(str(exc))

    if method == "relay":
        ku, tu = identify_relay(data.pv, data.cv, data.dt)
        results["Ku"], results["Tu"] = ku, tu
        results["relay"] = True
        if results["model"] is None:
            # Модель не нужна для ZN closed-loop, но полезна для симуляции
            results["model_error"] = "; ".join(errors) if errors else ""
        return results

    if method not in ("step", "curve_fit", "auto"):
        raise ValueError(f"Неизвестный метод идентификации: {method}")

    if results["model"] is None:
        raise ValueError("Идентификация не удалась. " +
                         ("; ".join(errors) if errors else
                          "Проверьте качество данных."))

    try:
        results["Ku"], results["Tu"] = critical_from_fopdt(results["model"])
    except ValueError:
        pass

    # Предупреждения о качестве (модель возвращается, но пользователю
    # сообщается о низкой достоверности)
    warnings: list[str] = []
    r2 = results["model"].fit_quality
    if np.isfinite(r2) and r2 < 0.5:
        warnings.append(
            f"Низкое качество аппроксимации модели (R²={r2:.2f}). "
            "Возможные причины: слабое изменение выхода регулятора CV, "
            "высокий шум PV, преобладание возмущений. Результатам "
            "доверять осторожно.")
    results["warnings"] = warnings
    results["errors"] = errors
    return results


# ------------------------------------------------------- ITAE optimization util
def optimize_itae(model, controller_type: str, kp0: float,
                  ti0: float, td0: float, dt_sim: float = 0.05,
                  sim_time: float | None = None,
                  model_type: str = "fopdt") -> tuple[float, float, float]:
    """
    Оптимизация коэффициентов PID минимизацией ITAE при отклике на ступеньку SP.
    Используется дискретный симулятор из core.simulator.
    """
    from core.simulator import simulate_closed_loop

    if isinstance(model, IpdtModel):
        model_type = "ipdt"
    if sim_time is None:
        if model_type == "ipdt":
            sim_time = min(max(20 * model.tau, 30), 600)
        else:
            sim_time = min(max(8 * model.T + 4 * model.tau, 30), 600)

    def cost(x):
        kp, ti, td = x
        max_ti = max(20.0 * sim_time, 10.0)
        ti = min(max(ti, 1e-3), max_ti) if ti > 0 else 1e-3
        td = max(td, 0.0)
        try:
            if model_type == "ipdt":
                t, sp, pv, _ = simulate_closed_loop(
                    0.0, 0.0, model.tau, controller_type,
                    kp, ti, td, dt_sim, sim_time, model_type="ipdt",
                    Ka=model.Ka, balance=getattr(model, "balance", None))
            else:
                t, sp, pv, _ = simulate_closed_loop(
                    model.K, model.T, model.tau, controller_type,
                    kp, ti, td, dt_sim, sim_time)
        except Exception:
            return 1e12
        e = sp - pv
        itae = float(np.trapezoid(np.maximum(t, 1e-6) * np.abs(e), t))
        if not np.isfinite(itae):
            return 1e12
        return itae

    x0 = np.array([max(kp0, 1e-6), max(ti0, 1e-3), max(td0, 0.0)])
    res = minimize(cost, x0, method="Nelder-Mead",
                   options={"maxiter": 400, "xatol": 1e-4, "fatol": 1e-4})
    kp, ti, td = res.x
    max_ti = max(20.0 * sim_time, 10.0)
    return float(kp), float(min(max(ti, 1e-3), max_ti)), float(max(td, 0.0))
