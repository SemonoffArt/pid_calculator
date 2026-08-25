"""
Точка входа приложения для настройки ПИД-регуляторов.

Создаёт Flask-приложение и запускает встроенный сервер разработки.
"""
from flask import Flask

from config import Config
from routes import register_routes


def create_app(config_class: type[Config] = Config) -> Flask:
    """Фабрика приложений."""
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Регистрируем маршруты
    register_routes(app)

    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=Config.DEBUG, host="127.0.0.1", port=5000)
