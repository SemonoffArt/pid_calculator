"""
Хранение состояния пользователя в Flask-сессии.

Данные (DataFrame) слишком велики для cookie-сессии, поэтому
предобработанные данные сохраняются в CSV-файл в uploads/,
а в сессии хранятся только пути и лёгкие результаты расчётов.
"""
from __future__ import annotations

import os
import uuid

import pandas as pd
from flask import session

SESSION_KEY = "pid_state"


def _state() -> dict:
    return session.setdefault(SESSION_KEY, {})


def save_state(**kwargs) -> None:
    """Обновляет состояние сессии."""
    state = _state()
    state.update(kwargs)
    session.modified = True


def get_state() -> dict:
    """Возвращает копию состояния текущего пользователя."""
    return dict(_state())


def clear_state() -> None:
    """Полностью очищает состояние (например, при загрузке нового файла)."""
    old = _state()
    # Удаляем временные файлы предыдущей сессии
    for key in ("data_path", "upload_name"):
        path = old.get(key)
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass
    session.pop(SESSION_KEY, None)
    session.modified = True


def new_session_id() -> str:
    return uuid.uuid4().hex[:16]


def load_dataframe(upload_folder: str) -> pd.DataFrame:
    """Загружает сохранённый DataFrame из файла, указанного в сессии."""
    path = _state().get("data_path")
    if not path or not os.path.exists(path):
        raise FileNotFoundError("Данные не найдены. Загрузите файл заново.")
    return pd.read_csv(path)


def save_dataframe(df: pd.DataFrame, upload_folder: str,
                   original_name: str) -> str:
    """Сохраняет предобработанные данные и возвращает путь к файлу."""
    os.makedirs(upload_folder, exist_ok=True)
    name = f"{new_session_id()}_data.csv"
    path = os.path.join(upload_folder, name)
    df.to_csv(path, index=False)
    return path
