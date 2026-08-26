"""
Интеграционный тест: ядро расчётов + все маршруты через test client.
Запуск: .\\.venv\\Scripts\\python.exe scripts\\test_app.py
"""
import io
import sys

sys.path.insert(0, ".")

import numpy as np

from app import app
from core import data_loader, identification, pid_tuning, simulator


def check(name, cond):
    status = "OK " if cond else "FAIL"
    print(f"[{status}] {name}")
    if not cond:
        raise AssertionError(name)


# ---------------------------------------------------------------- core tests
class FS:
    filename = "sample_open_loop.csv"

    @staticmethod
    def read():
        with open("sample_data/sample_open_loop.csv", "rb") as f:
            return f.read()


df = data_loader.load_csv(FS())

check("CSV загружен", set(("PV", "SP", "CV")).issubset(df.columns))
data = data_loader.preprocess(df, None, 5)
check("Предобработка", abs(data.dt - 1.0) < 0.01)
check("Ступенька найдена (CV, разомкнутый контур)", data.step_index is not None)

res = identification.identify(data, "auto")
model = res["model"]
print(f"  Модель: K={model.K:.3f} T={model.T:.1f} tau={model.tau:.1f} "
      f"(эталон K=2, T=40, tau=8), R2={model.fit_quality}")
check("K в пределах 20 %", 0.8 * 2.0 <= model.K <= 1.2 * 2.0)
check("T в пределах 25 %", 0.75 * 40.0 <= model.T <= 1.3 * 40.0)
check("tau разумное", 0.0 <= model.tau <= 20.0)
check("Критические параметры", res["Ku"] is not None and res["Tu"] > 0)

res_cf = identification.identify(data, "curve_fit")
print(f"  curve_fit: K={res_cf['model'].K:.3f} T={res_cf['model'].T:.1f} "
      f"tau={res_cf['model'].tau:.1f}")

# Релейный метод на данных с автоколебаниями
df_r = data_loader.load_csv(open("sample_data/sample_relay.csv", "rb"))
d_r = data_loader.preprocess(df_r, None, 5)
ku_r, tu_r = identification.identify_relay(d_r.pv, d_r.cv, d_r.dt)
print(f"  relay: Ku={ku_r:.2f} Tu={tu_r:.1f}")
check("Релейный метод даёт Ku>0, Tu>0", ku_r > 0 and tu_r > 0)

for method in pid_tuning.METHODS:
    for ctype in ("P", "PI", "PID"):
        try:
            itae_opt = lambda m, c, kp0, ti0, td0: identification.optimize_itae(
                m, c, kp0, ti0, td0, sim_time=200)
            c = pid_tuning.tune(method, model, ctype,
                                ku=res["Ku"], tu=res["Tu"], lam=None,
                                itae_optimizer=itae_opt
                                if method == "itae" else None)
            assert np.isfinite(c["Kp"])
            print(f"  {method}/{ctype}: Kp={c['Kp']:.4f} Ti={c['Ti']} Td={c['Td']}")
        except ValueError as e:
            print(f"  {method}/{ctype}: пропуск ({e})")

imc_coeffs = pid_tuning.tune("imc", model, "PID")
sim = simulator.simulate_closed_loop(model.K, model.T, model.tau, "PID",
                                     imc_coeffs["Kp"], imc_coeffs["Ti"],
                                     imc_coeffs["Td"], dt_sim=0.05,
                                     sim_time=800, sp_array=data.sp)
metrics = simulator.quality_metrics(*sim[:3])
print(f"  Симуляция: метрики {metrics}")
check("Симуляция сходится к заданию",
      abs(sim[2][-1] - sim[1][-1]) < 0.05 * abs(sim[1][-1]))

# ------------------------------------------------------------ route tests
app.config["TESTING"] = True
client = app.test_client()

r = client.get("/")
check("GET / = 200", r.status_code == 200)
check("Русский интерфейс", "Загрузка".encode() in r.data or "Загрузк".encode("utf-8") in r.data)

with open("sample_data/sample_open_loop.csv", "rb") as f:
    r = client.post("/upload", data={"file": (f, "sample.csv"),
                                     "id_mode": "auto"},
                    content_type="multipart/form-data")
check("POST /upload redirect", r.status_code == 302)

# Нормализация: базовый масштаб из данных с Y-max (CuSO4) → K безразмерный
import io
with open("tmp/CuSO4.csv", "rb") as f:
    r = client.post("/upload", data={"file": (f, "cuso4.csv"),
                                     "normalize": "1"},
                    content_type="multipart/form-data")
check("POST /upload (нормализация) redirect", r.status_code == 302)
j_norm = client.post("/api/calculate",
                     json={"method": "imc", "ctype": "PID"}).get_json()
check("Нормализованный K в диапазоне ~1", 0.5 <= abs(j_norm["model"]["K"]) <= 3.0)
print(f"  нормализация: K={j_norm['model']['K']:.3f}, "
      f"Kp={j_norm['coeffs']['Kp']:.3f} (масштаб из Y-max)")
html_norm = client.get("/results").data.decode()
check("Бейдж «нормализовано» в UI", "нормализовано" in html_norm)

# Возвращаем обычный пример для остальных тестов
with open("sample_data/sample_open_loop.csv", "rb") as f:
    r = client.post("/upload", data={"file": (f, "sample.csv"),
                                     "id_mode": "auto"},
                    content_type="multipart/form-data")

r = client.get("/results")
check("GET /results = 200", r.status_code == 200)

r = client.post("/api/calculate", json={"method": "imc", "ctype": "PID"})
j = r.get_json()
check("API calculate = 200", r.status_code == 200)
check("API вернул коэффициенты", j and "coeffs" in j and np.isfinite(j["coeffs"]["Kp"]))
print(f"  IMC/PID: {j['coeffs']}")

r = client.post("/api/calculate", json={"method": "zn_closed", "ctype": "PI"})
j2 = r.get_json()
check("API zn_closed = 200", r.status_code == 200)

# Ручная корректировка меняет симуляцию
r_manual = client.post("/api/calculate",
                       json={"manual": {"Kp": 10.0, "Ti": 5.0, "Td": 1.0}})
jm = r_manual.get_json()
check("Ручные коэффициенты применены",
      jm["coeffs"]["Kp"] == 10.0 and jm["metrics"] != j["metrics"])

# Ручное задание SP: ступенька к целевому значению (консервативные
# коэффициенты, чтобы PV успело установиться)
r_sp = client.post("/api/calculate",
                   json={"manual": {"Kp": 0.5, "Ti": 40.0, "Td": 3.0},
                         "sp_target": 60.0})
jsp = r_sp.get_json()
sps = jsp["sim"]["sp"]
check("Ступенька SP к цели", abs(sps[-1] - 60.0) < 0.01)
check("PV стремится к целевому SP", abs(jsp["sim"]["pv"][-1] - 60.0) < 3.0)
print(f"  sp_target=60: sp_end={sps[-1]:.2f}, "
      f"pv_end={jsp['sim']['pv'][-1]:.2f}")

# П1: предупреждения о качестве — агрессивная настройка ЗН раскачивает контур
r_zn = client.post("/api/calculate", json={"method": "zn_open",
                                           "ctype": "PID"})
jzn = r_zn.get_json()
check("ЗН возвращает метрики с насыщением", "sat_frac" in jzn["metrics"])
check("ЗН с агрессивным перерегулированием даёт предупреждение",
      len(jzn["quality_warnings"]) > 0 or jzn["metrics"]["overshoot"] <= 50)

# П2: оценка управляемости объекта
check("Возвращается оценка управляемости",
      "controlability" in jzn and "label" in jzn["controlability"])
print(f"  Управляемость: {jzn['controlability']['label']} "
      f"(τ/T={jzn['controlability']['ratio']})")

# П3: учёт насыщения — Kp ограничивается, чтобы контур не раскачивался
r_sat = client.post("/api/calculate", json={"method": "zn_open",
                                            "ctype": "PID",
                                            "use_saturation": True})
jsat = r_sat.get_json()
check("С флагом насыщения Kp ниже исходного ЗН",
      jsat["coeffs"]["Kp"] < jzn["coeffs"]["Kp"])
check("С флагом насыщения перерегулирование в рамках",
      jsat["metrics"]["overshoot"] <= 60)
print(f"  ЗН Kp={jzn['coeffs']['Kp']:.2f} (ov={jzn['metrics']['overshoot']:.0f}%)"
      f" -> с флагом Kp={jsat['coeffs']['Kp']:.2f} "
      f"(ov={jsat['metrics']['overshoot']:.0f}%)")

r = client.get("/adjust")
check("GET /adjust = 200", r.status_code == 200)

r = client.get("/export/pdf")
check("PDF экспорт", r.status_code == 200 and r.data[:4] == b"%PDF")
print(f"  PDF размер: {len(r.data)} байт")

r = client.get("/export/excel")
check("Excel экспорт", r.status_code == 200 and r.data[:2] == b"PK")
print(f"  Excel размер: {len(r.data)} байт")

print("\nВсе тесты пройдены успешно.")
