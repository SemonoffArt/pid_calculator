"""
Расчёт коэффициентов ПИД-регулятора по классическим методам.

Формулы выдают: Kp [ед. PV/% CV], Ti [сек], Td [сек] в идеальной
(параллельной) форме: u = Kp*(e + 1/Ti*∫e + Td*de/dt).
"""
from __future__ import annotations

from config import Config
from core.identification import FopdtModel

# 9 канонических методов + оптимизация ITAE
METHODS = ("zn_open", "zn_closed", "cohen",
           "chr_sp0", "chr_sp20", "chr_ds0", "chr_ds20",
           "imc", "simc", "amigo", "itae")

CONTROLLER_TYPES = ("P", "PI", "PID")


def _apply_type(kp: float, ti: float, td: float, ctype: str) -> dict:
    """Обнуляет не используемые составляющие в зависимости от типа регулятора."""
    if ctype == "P":
        ti, td = None, None
    elif ctype == "PI":
        td = None
    return {"Kp": float(kp), "Ti": ti and float(ti), "Td": td and float(td)}


def zn_open_loop(model: FopdtModel, ctype: str) -> dict:
    """Зиглер–Николс по параметрам разомкнутого контура (K, T, tau).

    Канонические формулы (quarter-decay ratio ≈ 25 % перерегулирования).
    Формулы используют модуль усиления; знак K (прямое/обратное действие)
    учитывается при симуляции.
    """
    K, T, tau = abs(model.K), model.T, model.tau
    if tau <= 0:
        raise ValueError("Метод Зиглера–Николса требует положительного tau.")
    if ctype == "P":
        kp = 1.0 * T / (K * tau)
        return _apply_type(kp, None, None, ctype)
    if ctype == "PI":
        kp = 0.9 * T / (K * tau)
        return _apply_type(kp, 3.3 * tau, None, ctype)
    kp = 1.2 * T / (K * tau)
    return _apply_type(kp, 2.0 * tau, 0.5 * tau, ctype)


def zn_closed_loop(ku: float, tu: float, ctype: str) -> dict:
    """Зиглер–Николс по критическим параметрам замкнутого контура (Ku, Tu)."""
    if not ku or not tu or ku <= 0 or tu <= 0:
        raise ValueError("Критические параметры Ku/Tu недоступны. "
                         "Используйте данные с автоколебаниями или другой метод.")
    if ctype == "P":
        return _apply_type(0.5 * ku, None, None, ctype)
    if ctype == "PI":
        return _apply_type(0.45 * ku, tu / 1.2, None, ctype)
    return _apply_type(0.6 * ku, tu / 2.0, tu / 8.0, ctype)


def cohen(model: FopdtModel, ctype: str) -> dict:
    """
    Cohen–Coon (1953) для FOPDT с большим запаздыванием.

    r = τ/T. Коэффициенты умножаются на T/(K·τ); Ti, Td — в секундах.
    Чувствителен к соотношению r, лучше Z-N для L/T > 0,3.
    """
    K, T, tau = abs(model.K), model.T, model.tau
    if tau <= 0:
        raise ValueError("Метод Cohen–Coon требует положительного tau.")
    r = tau / T
    base = T / (K * tau)            # = 1/(K·r)
    if ctype == "P":
        kp = base * (1.0 + r / 3.0)
        return _apply_type(kp, None, None, ctype)
    if ctype == "PI":
        kp = base * (0.9 + r / 12.0)
        ti = tau * (30.0 + 3.0 * r) / (9.0 + 20.0 * r)
        return _apply_type(kp, ti, None, ctype)
    kp = base * (4.0 / 3.0 + r / 4.0)
    ti = tau * (32.0 + 6.0 * r) / (13.0 + 8.0 * r)
    td = tau * 4.0 / (11.0 + 2.0 * r)
    return _apply_type(kp, ti, td, ctype)


def chr(model: FopdtModel, ctype: str, variant: str) -> dict:
    """
    Чен–Хрон (CHR, 1952) — четыре варианта:

    variant: "sp0" — отработка уставки, без перерегулирования;
             "sp20" — отработка уставки, ~20 % перерегулирования;
             "ds0" — подавление возмущений, без перерегулирования;
             "ds20" — подавление возмущений, ~20 % перерегулирования.

    Коэффициенты умножаются на T/(K·τ); Ti, Td — в секундах.
    """
    if variant not in ("sp0", "sp20", "ds0", "ds20"):
        raise ValueError(f"Неизвестный вариант CHR: {variant}")
    base = model.T / (abs(model.K) * model.tau)
    tables = {
        "sp0": {  # servo, 0 % OS
            "P": (0.3, None, None),
            "PI": (0.35, 1.2 * model.T, None),
            "PID": (0.6, model.T, 0.5 * model.tau)},
        "sp20": {  # servo, 20 % OS
            "P": (0.7, None, None),
            "PI": (0.6, 1.0 * model.T, None),
            "PID": (0.95, 1.4 * model.T, 0.47 * model.tau)},
        "ds0": {  # regulator, 0 % OS
            "P": (0.3, None, None),
            "PI": (0.7, 2.3 * model.tau, None),
            "PID": (0.95, 2.4 * model.tau, 0.42 * model.tau)},
        "ds20": {  # regulator, 20 % OS
            "P": (0.7, None, None),
            "PI": (0.7, 2.0 * model.tau, None),
            "PID": (1.2, 2.0 * model.tau, 0.42 * model.tau)},
    }
    kp_c, ti_c, td_c = tables[variant][ctype]
    return _apply_type(kp_c * base, ti_c, td_c, ctype)


def imc(model: FopdtModel, ctype: str, lam: float | None) -> dict:
    """
    Метод внутренней модели (IMC) для FOPDT.

    lam — желаемая постоянная времени замкнутой системы λ (сек).
    Рекомендуемое значение по умолчанию: λ = max(T, tau).
    """
    if lam is None or lam <= 0:
        lam = max(model.T, model.tau)
    # Классические IMC-формулы для FOPDT (идеальный PID):
    kp = (model.T + model.tau / 2) / (abs(model.K) * (lam + model.tau / 2))
    ti = model.T + model.tau / 2
    td = model.T * model.tau / (2 * model.T + model.tau)
    return _apply_type(kp, ti, td, ctype)


def simc(model: FopdtModel, ctype: str, tau_c: float | None) -> dict:
    """
    SIMC — Skogestad IMC (2003), уточнённая версия IMC.

    tau_c — желаемая постоянная времени замкнутого контура (сек).
    Рекомендуемое значение по умолчанию: tau_c = τ (запаздывание).
    Малое tau_c — быстрее, но менее робастно; большое — плавнее.
    """
    if tau_c is None or tau_c <= 0:
        tau_c = model.tau
    kp = model.T / (abs(model.K) * (tau_c + model.tau))
    ti = min(model.T, 4.0 * (tau_c + model.tau))
    td = model.tau / 2.0
    return _apply_type(kp, ti, td, ctype)


def amigo(model: FopdtModel, ctype: str) -> dict:
    """
    AMIGO — Åström–Hägglund (2004), современный модельный метод.

    Оптимизирован по интегральной ошибке IAE при ограничении
    максимальной чувствительности Ms ≤ 1,4 (робастные настройки).
    То же ядро Kp/Ti/Td используется для P/PI (без D/без I+D).
    """
    K, T, tau = abs(model.K), model.T, model.tau
    if tau <= 0:
        raise ValueError("Метод AMIGO требует положительного tau.")
    kp = (1.0 / K) * (0.2 + 0.45 * T / tau)
    ti = tau * (0.4 * tau + 0.8 * T) / (tau + 0.1 * T)
    td = 0.5 * tau * T / (0.3 * tau + T)
    return _apply_type(kp, ti, td, ctype)


def tune(method: str, model: FopdtModel, ctype: str,
          ku: float | None = None, tu: float | None = None,
          lam: float | None = None, tau_c: float | None = None,
          itae_optimizer=None) -> dict:
    """Диспетчер методов настройки."""
    if method == "zn_open":
        return zn_open_loop(model, ctype)
    if method == "zn_closed":
        return zn_closed_loop(ku, tu, ctype)
    if method == "cohen":
        return cohen(model, ctype)
    if method == "chr_sp0":
        return chr(model, ctype, "sp0")
    if method == "chr_sp20":
        return chr(model, ctype, "sp20")
    if method == "chr_ds0":
        return chr(model, ctype, "ds0")
    if method == "chr_ds20":
        return chr(model, ctype, "ds20")
    if method == "imc":
        return imc(model, ctype, lam)
    if method == "simc":
        return simc(model, ctype, tau_c)
    if method == "amigo":
        return amigo(model, ctype)
    if method == "itae":
        if itae_optimizer is None:
            raise ValueError("Оптимизатор ITAE недоступен.")
        start = imc(model, ctype, lam)
        kp, ti, td = itae_optimizer(model, ctype, start["Kp"],
                                    start.get("Ti") or max(start["Kp"], 1.0),
                                    start.get("Td") or 0.0)
        return _apply_type(kp, ti, td, ctype)
    raise ValueError(f"Неизвестный метод настройки: {method}")


# ------------------------------------------------------- управляемость (П2)
def controllability(model: FopdtModel) -> dict:
    """
    Оценка управляемости объекта по параметрам FOPDT.

    Основной показатель — отношение чистого запаздывания к постоянной
    времени τ/T. Чем оно больше, тем труднее объект: запаздывание
    «затягивает» реакцию и раскачивает контур. Дополнительно отмечается
    низкая чувствительность (малый |K|).
    """
    ratio = model.tau / model.T if model.T > 0 else float("inf")

    if ratio < Config.CONTROLLABILITY_LOW:
        level, label = "easy", "хорошо управляемый"
    elif ratio <= Config.CONTROLLABILITY_HIGH:
        level, label = "moderate", "умеренно трудный"
    else:
        level, label = "difficult", "трудный (большое запаздывание)"

    weak_gain = abs(model.K) > 0 and abs(model.K) < Config.WEAK_GAIN_THRESHOLD

    hints: list[str] = []
    if ratio >= Config.CONTROLLABILITY_HIGH:
        hints.append("высокое отношение τ/T — реагирует с большой задержкой; "
                     "агрессивные методы (ЗН) дают перерегулирование")
    if weak_gain:
        hints.append("низкий коэффициент усиления |K| — объект слабо "
                     "отвечает на ход заслонки, Kp получается большим")
    if not hints:
        hints.append("объект является обычным: классические методы "
                     "должны дать приемлемый результат")

    return {"ratio": round(ratio, 3), "level": level, "label": label,
            "weak_gain": weak_gain, "hints": hints}


# --------------------------------------------------- учёт насыщения (П3)
def limit_kp_by_saturation(coeffs: dict, model: FopdtModel, ctype: str,
                           sp_start: float, sp_target: float,
                           dt_sim: float = 0.05, sim_time: float = 200.0,
                           max_kp_factor: float = 0.1,
                           step_reduction: float = 0.9,
                           warn_sat_frac: float = 0.05,
                           overshoot_target: float = 30.0) -> dict:
    """
    Смягчает слишком агрессивный Kp, пока отклик не станет приемлемым.

    Итеративно уменьшает Kp (умножая на step_reduction), пока выполняется
    хотя бы одно из условий:
      - регулятор уходит в упор (доля времени насыщения > warn_sat_frac);
      - перерегулирование превышает overshoot_target.
    Ограничение снизу — max_kp_factor доли от исходного Kp (защита от нуля).
    Возвращает коэффициенты с флагом `saturation_limited`.
    """
    from core import simulator

    kp0 = coeffs["Kp"]
    if not kp0 or kp0 <= 0:
        coeffs["saturation_limited"] = False
        return coeffs

    def response(kp: float) -> dict:
        t, sp, pv, cv = simulator.simulate_closed_loop(
            model.K, model.T, model.tau, ctype, kp,
            coeffs.get("Ti"), coeffs.get("Td"),
            dt_sim=dt_sim, sim_time=sim_time,
            sp_start=sp_start, sp_target=sp_target)
        return simulator.quality_metrics(t, sp, pv, cv)

    def acceptable(kp: float) -> bool:
        q = response(kp)
        return (q["sat_frac"] <= warn_sat_frac and
                q["overshoot"] <= overshoot_target)

    result = dict(coeffs)
    limited = False
    kp = kp0
    min_kp = kp0 * max_kp_factor
    while kp > min_kp and not acceptable(kp):
        kp *= step_reduction
        limited = True
    result["Kp"] = kp
    result["saturation_limited"] = limited
    return result
