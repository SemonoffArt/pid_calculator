"""
Генератор синтетических данных для тестирования.

Создаёт два файла:
1. sample_data/sample_open_loop.csv — тест в разомкнутом контуре:
   ступеньки CV (25 % -> 40 % -> 30 %) при постоянном SP.
2. sample_data/sample_closed_loop.csv — замкнутый контур:
   объект FOPDT (K=2, T=40 c, tau=8 c) под PI-регулятором,
   две ступеньки задания SP.

Формат: разделитель ';', десятичная запятая.
"""
import csv
import os

import numpy as np

K_TRUE, T_TRUE, TAU_TRUE = 2.0, 40.0, 8.0


def fopdt(u: np.ndarray, dt: float, y_base: float = 50.0) -> np.ndarray:
    """Отклик FOPDT на входной сигнал u (% хода)."""
    delay_n = int(round(TAU_TRUE / dt))
    ubuf = np.zeros(len(u) + delay_n)
    ubuf[:delay_n] = u[0]
    ubuf[delay_n:] = u
    y = np.empty(len(u))
    y_val = y_base + K_TRUE * (u[0] - 25.0)
    a = dt / T_TRUE
    for k in range(len(u)):
        y_val += a * (K_TRUE * ubuf[k] - (y_val - y_base) - K_TRUE * 25.0)
        # эквивалент: dy/dt = (K*u - (y - y_base))/T относительно базы
        y[k] = y_val
    return y


def generate_open_loop(path: str, dt: float = 1.0) -> None:
    """Разомкнутый контур: ступеньки CV при постоянном задании."""
    rng = np.random.default_rng(42)
    t = np.arange(0.0, 600.0 + dt, dt)
    n = len(t)

    sp = np.full(n, 50.0)
    cv = np.full(n, 25.0)
    cv[100:] = 40.0     # ступенька +15 % на t=100 c
    cv[400:] = 30.0     # возврат на t=400 c

    pv = fopdt(cv, dt) + rng.normal(0, 0.08, n)
    cv_noisy = np.clip(cv + rng.normal(0, 0.15, n), 0, 100)

    _write_csv(path, t, sp, pv, cv_noisy)


def generate_closed_loop(path: str, dt: float = 1.0) -> None:
    """Замкнутый контур: PI-регулятор отрабатывает ступеньки задания."""
    rng = np.random.default_rng(7)
    t = np.arange(0.0, 600.0 + dt, dt)
    n = len(t)

    sp = np.full(n, 50.0)
    sp[120:] = 60.0
    sp[420:] = 55.0

    kp, ti = 1.5, 35.0
    delay_n = int(round(TAU_TRUE / dt))
    u_buf = np.full(n + delay_n, 25.0)

    pv = np.empty(n)
    y = 50.0            # начальное PV совпадает с базой при u=25 %
    integ = 25.0 / kp   # инициализация интегратора для баланса в начале
    for k in range(n):
        e = sp[k] - y
        integ += dt * e / ti
        u_raw = kp * (e + integ)
        u = float(np.clip(u_raw, 0.0, 100.0))
        u_buf[k + delay_n] = u
        ud = u_buf[k]
        # Абсолютная форма FOPDT: стремится к y_base + K*(ud - 25)
        y += (dt / T_TRUE) * ((50.0 + K_TRUE * (ud - 25.0)) - y)
        pv[k] = y

    pv += rng.normal(0, 0.06, n)
    cv_noisy = np.clip(u_buf[:n] + rng.normal(0, 0.25, n), 0, 100)

    _write_csv(path, t, sp, pv, cv_noisy)


def _write_csv(path: str, t, sp, pv, cv) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Time", "SP", "PV", "CV"])
        for i in range(len(t)):
            w.writerow([f"{t[i]:.1f}",
                        f"{sp[i]:.2f}".replace(".", ","),
                        f"{pv[i]:.3f}".replace(".", ","),
                        f"{cv[i]:.2f}".replace(".", ",")])
    print(f"Файл создан: {path} ({len(t)} строк)")


def generate_relay(path: str, dt: float = 1.0) -> None:
    """Релейное регулирование: автоколебания PV и релейный выход CV."""
    rng = np.random.default_rng(11)
    t = np.arange(0.0, 600.0 + dt, dt)
    n = len(t)

    sp = np.full(n, 50.0)
    sp[50:] = 58.0

    # Релейный регулятор с гистерезисом: выход 20 % или 60 %
    u_low, u_high = 20.0, 60.0
    hyst = 0.5
    delay_n = int(round(TAU_TRUE / dt))
    u_buf = np.full(n + delay_n, 25.0)

    pv = np.empty(n)
    y = 50.0
    u = u_low
    for k in range(n):
        e = sp[k] - y
        # Переключение реле с гистерезисом
        if e > hyst:
            u = u_high
        elif e < -hyst:
            u = u_low
        u_buf[k + delay_n] = u
        ud = u_buf[k]
        y += (dt / T_TRUE) * ((50.0 + K_TRUE * (ud - 25.0)) - y)
        pv[k] = y

    pv += rng.normal(0, 0.05, n)
    cv_noisy = np.clip(u_buf[:n] + rng.normal(0, 0.2, n), 0, 100)
    _write_csv(path, t, sp, pv, cv_noisy)


if __name__ == "__main__":
    generate_open_loop("sample_data/sample_open_loop.csv")
    generate_closed_loop("sample_data/sample_closed_loop.csv")
    generate_relay("sample_data/sample_relay.csv")
