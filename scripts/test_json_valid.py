"""
Диагностика: содержит ли ответ /api/calculate невалидные для браузера
значения (NaN/Infinity)? Они делают JSON непарсируемым для JSON.parse.
Запускать при работающем сервере на 127.0.0.1:5000.
"""
import http.cookiejar as cj
import json
import uuid
import urllib.request as ur

jar = cj.CookieJar()
op = ur.build_opener(ur.HTTPCookieProcessor(jar))
b = "----" + uuid.uuid4().hex
body = (f'--{b}\r\nContent-Disposition: form-data; name="file"; '
        f'filename="t.csv"\r\nContent-Type: text/csv\r\n\r\n').encode() + \
       open("tmp/test1.csv", "rb").read() + f"\r\n--{b}--\r\n".encode()
op.open(ur.Request("http://127.0.0.1:5000/upload", data=body,
                   headers={"Content-Type":
                            f"multipart/form-data; boundary={b}"}))
op.open("http://127.0.0.1:5000/results").read()
resp = op.open(ur.Request("http://127.0.0.1:5000/api/calculate",
                          data=b'{"method":"zn_open","ctype":"PID"}',
                          headers={"Content-Type": "application/json"}))
raw = resp.read().decode()
print("длина ответа:", len(raw))
print("содержит NaN:", "NaN" in raw, "| Infinity:", "Infinity" in raw)
try:
    json.loads(raw)
    print("строгий JSON.parse: OK")
except Exception as e:
    print("строгий JSON.parse: ОШИБКА ->", e)
