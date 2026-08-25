"""
Точка входа приложения для настройки ПИД-регуляторов.

Создаёт Flask-приложение и запускает встроенный сервер разработки.
"""
import logging
import os
from logging.handlers import RotatingFileHandler

from flask import Flask, jsonify, request
from werkzeug.exceptions import HTTPException

from config import BASE_DIR, Config
from routes import register_routes


def create_app(config_class: type[Config] = Config) -> Flask:
    """Фабрика приложений."""
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Лог ошибок в файл — для диагностики проблем пользователей.
    # Файл создаётся при старте; в него пишутся ВСЕ исключения (500).
    log_path = os.path.join(BASE_DIR, "flask.log")
    handler = RotatingFileHandler(log_path, maxBytes=1_000_000, backupCount=2,
                                  encoding="utf-8")
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s"))
    app.logger.addHandler(handler)
    app.logger.setLevel(logging.INFO)
    app.config["VERSION"] = "1.3"
    app.logger.info("Приложение запущено, версия %s, лог: %s",
                    app.config["VERSION"], log_path)

    # Глобальный перехватчик любых исключений: логируем трейсбек и,
    # для AJAX-запросов, возвращаем JSON с текстом ошибки.
    @app.errorhandler(Exception)
    def on_error(exc):
        if isinstance(exc, HTTPException):
            return exc  # штатные 4xx — без трейсбека
        app.logger.exception("Необработанное исключение: %r", exc)
        if request.path.startswith("/api/"):
            return jsonify({"error": f"Внутренняя ошибка: {exc}"}), 500
        return "Внутренняя ошибка сервера. См. flask.log", 500

    # Регистрируем маршруты
    register_routes(app)

    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=Config.DEBUG, host="127.0.0.1", port=5000)
