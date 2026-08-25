"""
Загрузка и предобработка данных технологического процесса.

- Чтение CSV с разделителем ';' и десятичной запятой.
- Проверка обязательных колонок PV, SP, CV; опционально Time.
- Интерполяция на равномерную временную сетку.
- Медианная фильтрация для подавления шума.
- Обнаружение ступенчатого изменения SP.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd


class DataError(ValueError):
    """Ошибка загрузки/валидации данных — понятное сообщение пользователю."""


REQUIRED_COLUMNS = ("PV", "SP", "CV")
OPTIONAL_COLUMNS = ("Time",)


@dataclass
class ProcessData:
    """Предобработанные данные процесса на равномерной сетке."""
    time: np.ndarray                 # равномерная сетка времени (сек)
    pv: np.ndarray                   # регулируемая величина
    sp: np.ndarray                   # задание
    cv: np.ndarray                   # выход регулятора, 0..100 %
    dt: float                        # шаг дискретизации
    step_index: int | None = None    # индекс ступеньки SP (если найдена)
    info: dict = field(default_factory=dict)

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame({"Time": self.time, "PV": self.pv,
                             "SP": self.sp, "CV": self.cv})


def load_csv(file_storage) -> pd.DataFrame:
    """
    Читает CSV из FileStorage, возвращает DataFrame с колонками Time/PV/SP/CV.

    Поддерживаются форматы:
    1. Простой: заголовок с именами Time, PV, SP, CV; числовое время.
    2. SCADA-экспорт: первая колонка — дата-время (например,
       "26.08.2026 3:28:00"), заголовок — теги каналов вместо PV/SP/CV,
       служебные строки Y-Max / Y-Min перед данными. В этом случае
       колонки сопоставляются по порядку: 2-я — PV, 3-я — SP, 4-я — CV
       (если среди тегов нет узнаваемых имён).
    """
    raw = file_storage.read()
    if not raw:
        raise DataError("Файл пустой или не читается.")

    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = raw.decode("cp1251")
        except UnicodeDecodeError:
            raise DataError("Не удалось определить кодировку файла "
                            "(ожидается UTF-8 или CP1251).")

    rows = _parse_rows(text, _detect_delimiter(text))
    header_idx, header = _find_header(rows)
    data = [r for r in rows[header_idx + 1:] if _is_data_row(r)]
    if len(data) < 10:
        raise DataError(f"Слишком мало строк данных (найдено {len(data)}, "
                        "нужно минимум 10). Проверьте формат файла.")

    # --- Сопоставление колонок ---
    col_map = _map_columns(header)
    if col_map is None:
        raise DataError(
            "Не удалось сопоставить колонки PV, SP, CV. Найден заголовок: " +
            ", ".join(c for c in header if c))

    time_idx, pv_idx, sp_idx, cv_idx = col_map

    # --- Время: число или дата-время ---
    times = _parse_time_column([r[time_idx] for r in data])

    def column(idx: int, name: str) -> np.ndarray:
        vals = []
        for r in data:
            v = _try_float(r[idx]) if idx < len(r) else None
            if v is None:
                raise DataError(f"Нечисловое значение в колонке '{name}': "
                                f"'{r[idx] if idx < len(r) else ''}'")
            vals.append(v)
        arr = np.asarray(vals, dtype=float)
        if np.all(np.isnan(arr)):
            raise DataError(f"Колонка '{name}' не содержит числовых данных.")
        return arr

    df = pd.DataFrame({
        "Time": times,
        "PV": column(pv_idx, "PV"),
        "SP": column(sp_idx, "SP"),
        "CV": column(cv_idx, "CV"),
    })

    for col in REQUIRED_COLUMNS:
        if df[col].notna().sum() < 3:
            raise DataError(f"Колонка '{col}' содержит недостаточно данных.")
    if len(df) < 10:
        raise DataError("Слишком мало данных (нужно минимум 10 строк).")
    return df


# ------------------------------------------------------------- helpers

def _try_float(s: str) -> float | None:
    """Парсинг числа с десятичной запятой или точкой; None при неудаче."""
    s = (s or "").strip()
    if not s:
        return None
    try:
        return float(s.replace(",", "."))
    except ValueError:
        return None


def _detect_delimiter(text: str) -> str:
    """
    Определяет разделитель полей по первой строке файла.

    Поддерживаются ';', табуляция и ','. Приоритет ';' — в старом формате
    десятичная запятая внутри чисел не должна путать определение.
    """
    first = text.splitlines()[0] if text.splitlines() else ""
    if ";" in first:
        return ";"
    if "\t" in first:
        return "\t"
    return ","


def _parse_rows(text: str, delimiter: str = ";") -> list[list[str]]:
    """Разбор CSV на строки; пустые ячейки в конце строк удаляются."""
    rows = []
    for row in csv.reader(io.StringIO(text), delimiter=delimiter):
        while row and not row[-1].strip():
            row.pop()
        if any(c.strip() for c in row):
            rows.append(row)
    return rows


def _is_number(s: str) -> bool:
    return _try_float(s) is not None


def _find_header(rows: list[list[str]]) -> tuple[int, list[str]]:
    """
    Ищет строку заголовка: первая строка, где после первой ячейки есть
    минимум 2 непустых нечисловых поля (имена каналов).
    """
    for i, r in enumerate(rows):
        if len(r) < 4:
            continue
        named = sum(1 for c in r[1:] if c.strip() and not _is_number(c))
        if named >= 2:
            return i, [c.strip() for c in r]
    raise DataError("Не найдена строка заголовка с именами колонок.")


def _is_data_row(r: list[str]) -> bool:
    """Строка данных: первое поле — время/число, остальные парсятся как числа."""
    if len(r) < 4 or not r[0].strip():
        return False
    if not (_is_number(r[0]) or _parse_datetime(r[0]) is not None):
        return False
    return all(_try_float(c) is not None for c in r[1:4])


_DT_FORMATS = ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M:%S,%f",
               "%d.%m.%y %H:%M:%S", "%Y-%m-%d %H:%M:%S",
               "%Y/%m/%d %H:%M:%S", "%d/%m/%Y %H:%M:%S")


def _parse_datetime(s: str):
    """Парсинг даты-времени в распространённых форматах экспорта."""
    s = (s or "").strip()
    for fmt in _DT_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


# Алиасы имён каналов (без учёта регистра)
_ALIASES = {
    "PV": {"PV"},
    "SP": {"SP", "SETPOINT", "SET_POINT", "ЗАД", "ЗАДАНИЕ"},
    "CV": {"CV", "OUT", "OUTPUT", "ВЫХ", "ВЫХОД"},
}


def _map_columns(header: list[str]) -> tuple[int, int, int, int] | None:
    """
    Сопоставляет колонки: возвращает (time_idx, pv_idx, sp_idx, cv_idx).

    Сначала ищутся узнаваемые имена (Time/PV/SP/CV и алиасы);
    если их нет и колонок данных ровно три — сопоставление по порядку.
    """
    upper = [c.upper().strip() for c in header]

    def find(name_set: set[str], start: int = 1) -> int | None:
        for i in range(start, len(upper)):
            if upper[i] in name_set:
                return i
        return None

    t_idx = find({"TIME", "ВРЕМЯ", "DATE", "DATETIME"}, start=0)
    pv_idx, sp_idx, cv_idx = find(_ALIASES["PV"]), \
        find(_ALIASES["SP"]), find(_ALIASES["CV"])

    if t_idx is not None and None not in (pv_idx, sp_idx, cv_idx):
        return t_idx, pv_idx, sp_idx, cv_idx

    # Позиционное сопоставление: колонка 0 — время, затем PV, SP, CV
    data_cols = [i for i in range(len(header)) if header[i].strip()]
    if len(data_cols) >= 4 and (t_idx is None or t_idx == 0):
        return 0, 1, 2, 3
    return None


def _parse_time_column(values: list[str]) -> np.ndarray:
    """Колонка времени: числовые секунды либо дата-время → секунды от начала."""
    if all(_is_number(v) for v in values):
        return np.asarray([float(v.replace(",", ".")) for v in values])

    parsed = [_parse_datetime(v) for v in values]
    if any(p is None for p in parsed):
        bad = next(values[i] for i, p in enumerate(parsed) if p is None)
        raise DataError(f"Не удалось распознать значение времени: '{bad}'.")
    t0 = parsed[0]
    return np.asarray([(p - t0).total_seconds() for p in parsed])


def auto_step(time_values: np.ndarray) -> float:
    """Автоматический шаг интерполяции — медианный интервал между отсчётами."""
    diffs = np.diff(np.sort(time_values))
    diffs = diffs[diffs > 0]
    if len(diffs) == 0:
        return 1.0
    return float(np.median(diffs))


def interpolate(df: pd.DataFrame, step: float | None) -> tuple[np.ndarray, ...]:
    """Интерполирует PV/SP/CV на равномерную временную сетку с шагом `step`."""
    if "Time" in df.columns:
        t = df[["Time"]].interpolate().to_numpy().ravel()
    else:
        t = np.arange(len(df), dtype=float)
    order = np.argsort(t)
    t, pv, sp, cv = t[order], df["PV"].to_numpy()[order], \
                    df["SP"].to_numpy()[order], df["CV"].to_numpy()[order]

    # Убираем NaN линейной интерполяцией
    def clean(y: np.ndarray) -> np.ndarray:
        mask = ~np.isnan(y)
        if mask.sum() < 2:
            raise DataError("Слишком много пропусков в данных.")
        return np.interp(t, t[mask], y[mask])

    pv, sp, cv = clean(pv), clean(sp), clean(cv)

    # Равномерная сетка
    if step is None or step <= 0:
        step = auto_step(t)
    grid = np.arange(t[0], t[-1] + step * 0.5, step)
    grid = grid[: min(len(grid), 200_000)]
    if len(grid) < 10:
        raise DataError("После интерполяции осталось слишком мало точек.")
    return grid, np.interp(grid, t, pv), np.interp(grid, t, sp), np.interp(grid, t, cv)


def median_filter(x: np.ndarray, window: int) -> np.ndarray:
    """Медианная фильтрация одномерного сигнала (окно нечётное, края — усечённые)."""
    if window < 3:
        return x.copy()
    if window % 2 == 0:
        window += 1
    half = window // 2
    out = np.empty_like(x)
    for i in range(len(x)):
        lo, hi = max(0, i - half), min(len(x), i + half + 1)
        out[i] = np.median(x[lo:hi])
    return out


def detect_sp_step(sp: np.ndarray, threshold: float = 0.05) -> int | None:
    """Ищет наибольшее ступенчатое изменение сигнала.

    Возвращает индекс точки сразу после ступеньки или None.
    Порог — доля от размаха сигнала.
    """
    span = float(np.max(sp) - np.min(sp))
    if span <= 0:
        return None
    jumps = np.abs(np.diff(sp))
    idx = int(np.argmax(jumps))
    if jumps[idx] >= threshold * span and jumps[idx] > 0:
        return idx + 1
    return None


def preprocess(df: pd.DataFrame, interp_step: float | None = None,
               filter_window: int = 5) -> ProcessData:
    """Полная предобработка: интерполяция + фильтрация + поиск ступеньки."""
    grid, pv, sp, cv = interpolate(df, interp_step)
    pv_f = median_filter(pv, filter_window)
    cv_f = median_filter(cv, filter_window)   # CV тоже может быть шумным
    # Сначала ищем ступеньку в SP (замкнутый контур), затем — в CV
    # (тест в ручном/разомкнутом режиме)
    step_idx = detect_sp_step(sp)
    step_signal = "SP"
    if step_idx is None:
        step_idx = detect_sp_step(cv_f)
        step_signal = "CV"
    return ProcessData(
        time=grid, pv=pv_f, sp=sp, cv=cv_f, dt=float(grid[1] - grid[0]),
        step_index=step_idx,
        info={
            "points": len(grid),
            "dt": round(float(grid[1] - grid[0]), 6),
            "step_detected": step_idx is not None,
            "step_signal": step_signal if step_idx is not None else "—",
            "pv_span": [float(np.min(pv)), float(np.max(pv))],
        },
    )
