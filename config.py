"""Конфигурация приложения: секретный ключ, пути, ограничения загрузки."""
import os
import secrets

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


class Config:
    # Секретный ключ: из переменной окружения или генерируется на лету
    SECRET_KEY = os.environ.get("SECRET_KEY", secrets.token_hex(32))

    DEBUG = os.environ.get("FLASK_DEBUG", "1") == "1"

    # Каталоги для временных файлов
    UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
    EXPORT_FOLDER = os.path.join(BASE_DIR, "exports")

    # Ограничения загрузки
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16 МБ
    ALLOWED_EXTENSIONS = {"csv", "txt"}

    # Значения по умолчанию для предобработки
    DEFAULT_FILTER_WINDOW = 5        # окно медианного фильтра (точки)
    MIN_FILTER_WINDOW = 3
    MAX_FILTER_WINDOW = 15

    # Порог обнаружения ступеньки SP — доля от размаха сигнала
    STEP_DETECT_THRESHOLD = 0.05

    # Значения по умолчанию для идентификации / симуляции
    IMC_LAMBDA_FACTOR = 1.0          # λ = фактор * max(T, τ)
    SIM_SUBSTEPS = 5                 # число подшагов дискретизации симуляции

    # Ограничения по размеру данных
    MAX_POINTS = 200_000             # максимум точек после интерполяции
