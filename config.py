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

    # --- Качество настройки / предупреждения ---
    # Порог перерегулирования (%), выше которого появляется предупреждение
    OVERSHOOT_WARN_THRESHOLD = 50.0
    # Доля времени насыщения CV, при которой считается, что регулятор
    # работает на пределе (в упоре)
    SATURATION_WARN_FRAC = 0.05     # >5 % времени в упоре — предупреждение

    # --- Пошаговая оценка управляемости (τ/T) ---
    # τ/T < LOW — хорошо управляемый; < HIGH — умеренно трудный; иначе трудный
    CONTROLLABILITY_LOW = 0.2
    CONTROLLABILITY_HIGH = 0.6
    # Порог «слабого» усиления: |K| меньше этого — низкочувствительный объект
    WEAK_GAIN_THRESHOLD = 0.2

    # --- Учёт насыщения при расчёте Kp (П3) ---
    SATURATION_AWARE = False         # флаг по умолчанию
    SATURATION_MAX_KP_FACTOR = 0.1   # итоговый Kp не меньше доли от расчётного
    SATURATION_STEP_REDUCTION = 0.9  # шаг понижения Kp при насыщении
    # Целевое перерегулирование (%), до которого снижаем Kp в режиме
    # «учитывать насыщение»
    SATURATION_OVERSHOOT_TARGET = 30.0

    # Версия приложения (отображается в подвале)
    VERSION = "0.10.1"
