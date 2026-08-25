"""
Проверка браузерного сценария: куки больше 4 КБ отклоняются, как это
делают реальные браузеры. Сервер должен работать при таком ограничении.
Запускать при работающем сервере на 127.0.0.1:5000.
"""
import http.cookiejar as cj
import io
import json
import sys
import urllib.request as ur
import uuid

sys.path.insert(0, ".")


class BrowserJar(cj.CookieJar):
    """Имитация браузера: куки больше 4000 байт отклоняются."""

    def extract_cookies(self, response, request):
        for c in self.make_cookies(response, request):
            if len(c.value or "") <= 4000:
                self.set_cookie(c)


jar = BrowserJar()
opener = ur.build_opener(ur.HTTPCookieProcessor(jar))

boundary = "----" + uuid.uuid4().hex
body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
        f'filename="t.csv"\r\nContent-Type: text/csv\r\n\r\n').encode() + \
    open("tmp/test1.csv", "rb").read() + f"\r\n--{boundary}--\r\n".encode()
req = ur.Request("http://127.0.0.1:5000/upload", data=body,
                 headers={"Content-Type":
                          f"multipart/form-data; boundary={boundary}"})
r = opener.open(req)
print("upload ->", r.status)
cookie_size = max((len(c.value) for c in jar), default=0)
print("размер сессионной куки:", cookie_size, "(лимит ~4000)")

html = opener.open("http://127.0.0.1:5000/results").read().decode()
print("results: карточка модели показана:", "FOPDT" in html)

req2 = ur.Request(
    "http://127.0.0.1:5000/api/calculate",
    data=b'{"method":"zn_open","ctype":"PID"}',
    headers={"Content-Type": "application/json"})
resp = opener.open(req2)
d = json.loads(resp.read())
print("api ->", resp.status,
      "коэффициенты:", {k: round(v, 4) if v else v
                        for k, v in d["coeffs"].items()})
print("графики raw/sim не пустые:", len(d["raw"]["time"]), len(d["sim"]["time"]))

# Экспорт тоже должен работать без массивов в сессии
for path in ("/export/pdf", "/export/excel"):
    resp = opener.open("http://127.0.0.1:5000" + path)
    print(path, "->", resp.status, len(resp.read()), "байт")

assert cookie_size <= 4000, "куки превышают лимит!"
assert len(d["raw"]["time"]) > 10 and len(d["sim"]["time"]) > 10
print("\nOK: приложение работает с браузерным лимитом кук.")
