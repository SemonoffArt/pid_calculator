"""
Расчёт коэффициентов ПИД-регулятора по классическим методам.

Формулы выдают: Kp [ед. PV/% CV], Ti [сек], Td [сек] в идеальной
(параллельной) форме: u = Kp*(e + 1/Ti*∫e + Td*de/dt).
"""
from __future__ import annotations

from core.identification import FopdtModel

METHODS = ("zn_open", "zn_closed", "chr_0", "chr_20", "imc", "itae")

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

    Формулы используют модуль усиления; знак K (прямое/обратное действие)
    учитывается при симуляции.
    """
    K, T, tau = abs(model.K), model.T, model.tau
    if tau <= 0:
        raise ValueError("Метод Зиглера–Николса требует положительного tau.")
    if ctype == "P":
        kp = 1.2 * T / (K * tau)
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


def chr(model: FopdtModel, ctype: str, overshoot: str) -> dict:
    """
    Чен–Хрон (CHR) для отработки изменения задания.

    overshoot: "0" — без перерегулирования, "20" — быстрое затухание (20 %).
    Коэффициенты умножаются на T/(K*tau); Ti, Td — в секундах.
    """
    base = model.T / (abs(model.K) * model.tau)
    if overshoot == "0":
        table = {"P": (0.3, None, None),
                 "PI": (0.35, 1.2 * model.T, None),
                 "PID": (0.6, model.T, 0.5 * model.tau)}
    else:
        table = {"P": (0.7, None, None),
                 "PI": (0.7, 2.3 * model.tau, None),
                 "PID": (0.95, 2.4 * model.tau, 0.42 * model.tau)}
    kp_c, ti_c, td_c = table[ctype]
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


def tune(method: str, model: FopdtModel, ctype: str,
         ku: float | None = None, tu: float | None = None,
         lam: float | None = None,
         itae_optimizer=None) -> dict:
    """Диспетчер методов настройки."""
    if method == "zn_open":
        return zn_open_loop(model, ctype)
    if method == "zn_closed":
        return zn_closed_loop(ku, tu, ctype)
    if method == "chr_0":
        return chr(model, ctype, "0")
    if method == "chr_20":
        return chr(model, ctype, "20")
    if method == "imc":
        return imc(model, ctype, lam)
    if method == "itae":
        if itae_optimizer is None:
            raise ValueError("Оптимизатор ITAE недоступен.")
        start = imc(model, ctype, lam)
        kp, ti, td = itae_optimizer(model, ctype, start["Kp"],
                                    start.get("Ti") or max(start["Kp"], 1.0),
                                    start.get("Td") or 0.0)
        return _apply_type(kp, ti, td, ctype)
    raise ValueError(f"Неизвестный метод настройки: {method}")
