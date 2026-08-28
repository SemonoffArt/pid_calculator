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


# --- Проверка поддержки формата SCADA с датой MM/DD/YYYY и строкой Description.
# Имитируем файл в памяти (разделитель ',', даты вида 08/27/2026 05:55:31).
scada_text = (
    '"Time","TAG_PV","TAG_SP","TAG_CV"\n'
    '"Y-max","400","400","100"\n'
    '"Y-min","0","0","0"\n'
    '"Description","Some PV","Some SP","Some CV"\n'
    '08/27/2026 05:55:31,"115.0","114.8","43.8"\n'
    '08/27/2026 05:55:32,"115.5","114.8","43.8"\n'
    '08/27/2026 05:55:33,"116.0","114.8","43.9"\n'
    '08/27/2026 05:55:34,"116.5","114.9","44.0"\n'
    '08/27/2026 05:55:35,"117.0","114.9","44.1"\n'
    '08/27/2026 05:55:36,"117.5","115.0","44.2"\n'
    '08/27/2026 05:55:37,"118.0","115.0","44.3"\n'
    '08/27/2026 05:55:38,"118.5","115.1","44.4"\n'
    '08/27/2026 05:55:39,"119.0","115.1","44.5"\n'
    '08/27/2026 05:55:40,"119.5","115.2","44.6"\n'
)


class FS_SCADA:
    filename = "tst1.csv"

    @staticmethod
    def read():
        return scada_text.encode("utf-8")


df_scada = data_loader.load_csv(FS_SCADA())
check("SCADA (MM/DD + Description) загружен",
      set(("PV", "SP", "CV")).issubset(df_scada.columns))
check("Время распознано как MM/DD/YYYY",
      np.isfinite(df_scada["Time"]).all())
check("Y-max из Scada извлечён", df_scada.attrs["y_max"]["pv"] == 400.0)
check("Строка Description пропущена",
      df_scada.shape[0] == 10 and df_scada["PV"].iloc[-1] == 119.5)

# --- Произвольный порядок колонок по суффиксам SPA(SP)/SPM(CV)/PV.
arbitrary_text = (
    '"Time","A_SPA","PV_DATA","B_SPM"\n'
    '"Y-max","400","400","100"\n'
    '08/27/2026 05:55:31,"114.8","115.0","43.8"\n'
    '08/27/2026 05:55:32,"115.0","116.0","43.9"\n'
    '08/27/2026 05:55:33,"115.1","117.0","44.0"\n'
    '08/27/2026 05:55:34,"115.2","118.0","44.1"\n'
    '08/27/2026 05:55:35,"115.3","119.0","44.2"\n'
    '08/27/2026 05:55:36,"115.4","120.0","44.3"\n'
    '08/27/2026 05:55:37,"115.5","121.0","44.4"\n'
    '08/27/2026 05:55:38,"115.6","122.0","44.5"\n'
    '08/27/2026 05:55:39,"115.7","123.0","44.6"\n'
    '08/27/2026 05:55:40,"115.8","124.0","44.7"\n'
)


class FS_ARB:
    filename = "arbitrary_order.csv"

    @staticmethod
    def read():
        return arbitrary_text.encode("utf-8")


df_arb = data_loader.load_csv(FS_ARB())
check("Произвольный порядок: SPA=SP, SPM=CV, остальное=PV",
      list(df_arb.columns) == ["Time", "PV", "SP", "CV"])
check("Произвольный порядок: значения не перепутаны",
      abs(df_arb["PV"].iloc[0] - 115.0) < 0.01 and
      abs(df_arb["SP"].iloc[0] - 114.8) < 0.01 and
      abs(df_arb["CV"].iloc[0] - 43.8) < 0.01)

# --- Проверка кодировки UTF-16 (BOM) на реальном тренде.
import os
trend_path = r"tmp\Trend on 2026-08-27T06.54.25.csv"
if os.path.exists(trend_path):
    class FS_U16:
        filename = "trend.csv"

        @staticmethod
        def read():
            return open(trend_path, "rb").read()

    df_u16 = data_loader.load_csv(FS_U16())
    check("UTF-16 (BOM) тренд загружен",
          set(("PV", "SP", "CV")).issubset(df_u16.columns))
    check("UTF-16: время линейно по шагу",
          abs((df_u16["Time"].iloc[-1] - 0.0) - (len(df_u16) - 1)) < 1.0)
    check("UTF-16: Y-max извлечён", df_u16.attrs["y_max"]["pv"] == 400.0)
    print(f"  UTF-16 тренд: {len(df_u16)} строк, "
          f"PV {df_u16['PV'].min():.1f}..{df_u16['PV'].max():.1f}")
else:
    print("  (пропуск UTF-16 — файл тренда отсутствует)")




df = data_loader.load_csv(FS())

check("CSV загружен", set(("PV", "SP", "CV")).issubset(df.columns))
data = data_loader.preprocess(df, None, 5)
check("Предобработка", abs(data.dt - 1.0) < 0.01)
check("Ступенька найдена (CV, разомкнутый контур)", data.step_index is not None)

# Окно фильтра = 0 -> без медианной фильтрации (сигнал не изменяется)
data_raw = data_loader.load_csv(FS())
_, raw_pv, _, raw_cv = data_loader.interpolate(data_raw, None)
data_no_filt = data_loader.preprocess(data_raw, None, 0)
check("Окно 0 = без фильтрации", np.array_equal(data_no_filt.pv, raw_pv))
check("Окно 0: CV не отфильтрован",
      np.array_equal(data_no_filt.cv, raw_cv))

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

# --- Новые методы (Cohen-Coon, 4 варианта CHR, SIMC, AMIGO):
# все должны давать положительные Kp/Ti/Td для PID и сходиться.
new_methods = ("cohen", "chr_sp0", "chr_sp20", "chr_ds0", "chr_ds20",
               "simc", "amigo")
for m in new_methods:
    c = pid_tuning.tune(m, model, "PID", ku=res["Ku"], tu=res["Tu"])
    check(f"{m}: Kp/Ti/Td > 0",
          c["Kp"] > 0 and c["Ti"] > 0 and c["Td"] > 0)
    sim_n = simulator.simulate_closed_loop(
        model.K, model.T, model.tau, "PID", c["Kp"], c["Ti"], c["Td"],
        dt_sim=0.05, sim_time=400, sp_start=0.0, sp_target=50.0,
        cv_clip=True)
    check(f"{m}: симуляция сходится к заданию",
          abs(sim_n[2][-1] - sim_n[1][-1]) < 0.05 * abs(sim_n[1][-1]))

# Эталонные формулы Z-N на модели K=1.58, T=7.57, L=2.1
m_ref = identification.FopdtModel(K=1.58, T=7.57, tau=2.1)
zn_p = pid_tuning.tune("zn_open", m_ref, "P")
check("Z-N P формула: Kp=T/(K*L)", abs(zn_p["Kp"] - 7.57 / (1.58 * 2.1)) < 1e-6)
zn = pid_tuning.tune("zn_open", m_ref, "PID")
check("Z-N PID формула: Ti=2L", abs(zn["Ti"] - 4.2) < 1e-6)
check("Z-N PID формула: Td=0.5L", abs(zn["Td"] - 1.05) < 1e-6)
# Cohen-Coon PID: Kp = (1/K)*(T/L)*(4/3 + r/4), r=L/T
r = 2.1 / 7.57
cc_kp = (1 / 1.58) * (7.57 / 2.1) * (4 / 3 + r / 4)
cc = pid_tuning.tune("cohen", m_ref, "PID")
check("Cohen-Coon PID формула: Kp", abs(cc["Kp"] - cc_kp) / cc_kp < 1e-6)
# AMIGO PID: Kp = (1/K)*(0.2 + 0.45*T/L)
am_kp = (1 / 1.58) * (0.2 + 0.45 * 7.57 / 2.1)
am = pid_tuning.tune("amigo", m_ref, "PID")
check("AMIGO PID формула: Kp", abs(am["Kp"] - am_kp) / am_kp < 1e-6)
# SIMC PID при tau_c=L: Kp = (1/K)*T/(L+L) = T/(2*K*L)
sm = pid_tuning.tune("simc", m_ref, "PID", tau_c=2.1)
check("SIMC PID формула (tau_c=L): Kp",
      abs(sm["Kp"] - 7.57 / (2 * 1.58 * 2.1)) / sm["Kp"] < 1e-6)
# CHR servo 0% PID: Kp=0.6*T/(K*L)
chr0 = pid_tuning.tune("chr_sp0", m_ref, "PID")
check("CHR servo 0% PID формула: Kp",
      abs(chr0["Kp"] - 0.6 * 7.57 / (1.58 * 2.1)) / chr0["Kp"] < 1e-6)

# --- ISA-стандартный ПИД: D-составляющая (с фильтром Td/N) должна
# подавлять перерегулирование по сравнению с чистым PI.
k_isa, t_isa, tau_isa = 1.58, 7.57, 2.1
_, _, pv_pid, _ = simulator.simulate_closed_loop(
    k_isa, t_isa, tau_isa, "PID", 2.05, 7.0, 1.0,
    dt_sim=0.05, sim_time=100, sp_start=0.0, sp_target=50.0, cv_clip=True)
_, _, pv_pi, _ = simulator.simulate_closed_loop(
    k_isa, t_isa, tau_isa, "PI", 2.05, 7.0, None,
    dt_sim=0.05, sim_time=100, sp_start=0.0, sp_target=50.0, cv_clip=True)
ov_pid = simulator.quality_metrics(
    np.arange(0, 100 + 0.05 / 2, 0.05), np.full(len(pv_pid), 50.0),
    pv_pid)["overshoot"]
ov_pi = simulator.quality_metrics(
    np.arange(0, 100 + 0.05 / 2, 0.05), np.full(len(pv_pi), 50.0),
    pv_pi)["overshoot"]
check("ISA-ПИД: D-составляющая снижает перерегулирование", ov_pid < ov_pi)
print(f"  ISA-ПИД: OV PID={ov_pid:.1f}% vs PI={ov_pi:.1f}%")

# --- Проверка соответствия внешнему референсу (ЗН, PI, K=1.58/T=7.57/tau=2.1,
# ступенька SP 0->50): подтверждает корректность дискретизации и anti-windup.
ref_model = identification.FopdtModel(K=1.58, T=7.57, tau=2.1)
ref_coeffs = pid_tuning.tune("zn_open", ref_model, "PI")
check("ЗН-PI даёт референсные Kp/Ti",
      abs(ref_coeffs["Kp"] - 2.0533) < 0.01 and abs(ref_coeffs["Ti"] - 7.0) < 0.1)
for clip, ref_ov in ((True, 13.7), (False, 46.5)):
    t, sp, pv, cv = simulator.simulate_closed_loop(
        ref_model.K, ref_model.T, ref_model.tau, "PI",
        ref_coeffs["Kp"], ref_coeffs["Ti"], None,
        dt_sim=0.05, sim_time=100, sp_start=0.0, sp_target=50.0,
        cv_clip=clip)
    q = simulator.quality_metrics(t, sp, pv, cv)
    check(f"Референс (clip={clip}) OV≈{ref_ov}%",
          abs(q["overshoot"] - ref_ov) < 3.0)
    print(f"  Референс clip={clip}: OV={q['overshoot']:.1f}% "
          f"(ожид. {ref_ov}%), IAE={q['iae']:.1f}")

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

r = client.get("/export/pdf")
check("PDF экспорт", r.status_code == 200 and r.data[:4] == b"%PDF")
print(f"  PDF размер: {len(r.data)} байт")

r = client.get("/export/excel")
check("Excel экспорт", r.status_code == 200 and r.data[:2] == b"PK")
print(f"  Excel размер: {len(r.data)} байт")

print("\nВсе тесты пройдены успешно.")
