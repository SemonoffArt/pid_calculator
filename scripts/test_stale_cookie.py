"""
Проверка сценария с устаревшей кукой от предыдущей версии приложения
(состояние указывает на несуществующий файл данных).
Сервер должен отвечать понятной JSON-ошибкой, а не HTML 500.
Запускать при работающем сервере на 127.0.0.1:5000.
"""
import http.cookiejar as cj
import json
import sys
import urllib.error
import urllib.request as ur

sys.path.insert(0, ".")

from app import app  # noqa: E402
from flask.sessions import SecureCookieSessionInterface  # noqa: E402

STALE_STATE = {
    "K": 2.0, "T": 40.0, "tau": 8.0,
    "coeffs": {"Kp": 1.0, "Ti": 20.0, "Td": 5.0},
    "data_path": "uploads/deleted_file.csv",  # файл удалён
}

serializer = SecureCookieSessionInterface().get_signing_serializer(app)
cookie_val = serializer.dumps(dict(pid_state=STALE_STATE))

jar = cj.CookieJar()
c = cj.Cookie(0, "session", cookie_val, None, False, "127.0.0.1", False,
              False, "/", False, False, None, False, None, None, {})
jar.set_cookie(c)
opener = ur.build_opener(ur.HTTPCookieProcessor(jar))

html = opener.open("http://127.0.0.1:5000/results").read().decode()
print("results с устаревшей кукой: карточка модели показана:",
      "FOPDT" in html)

req = ur.Request(
    "http://127.0.0.1:5000/api/calculate",
    data=b'{"method":"zn_open","ctype":"PID"}',
    headers={"Content-Type": "application/json"})
try:
    resp = opener.open(req)
    d = json.loads(resp.read())
    print("api ->", resp.status, d.get("error", "OK, расчёт выполнен"))
except urllib.error.HTTPError as e:
    body = json.loads(e.read())
    print("api ->", e.code, "JSON-ошибка:", body.get("error"))
    assert e.code != 500, "сервер вернул 500 вместо понятной ошибки"
    assert "Внутренняя ошибка" not in body.get("error", ""), \
        "необработанное исключение на сервере"
print("\nOK: устаревшее состояние обрабатывается корректно.")
