/*
 * main.js — AJAX-взаимодействие и обновление графиков Plotly.
 *
 * На странице результатов: POST /api/calculate возвращает коэффициенты
 * и данные для графиков; графики обновляются через Plotly.react().
 * На странице корректировки: отправка ручных значений Kp/Ti/Td.
 */
const PIDApp = (() => {
  "use strict";

  const layout = {
    margin: { l: 55, r: 20, t: 10, b: 40 },
    hovermode: "x unified",
    legend: { orientation: "h", y: -0.15 },
  };
  const cfg = { responsive: true, locale: "ru" };

  // Описания методов настройки — для подсказки «Метод настройки»
  const METHOD_HELP = {
    zn_open: "Зиглер–Николс по разомкнутой характеристике (K, T, τ). "
      + "Quarter-decay ratio: каждое следующее перерегулирование в 4 раза "
      + "меньше. Перерегулирование ~25–50 %, агрессивная настройка. "
      + "Для FOPDT с L/T = 0,1…1; не годится, где перерегулирование опасно.",
    zn_closed: "Зиглер–Николс по критическим параметрам Ku/Tu (замкнутый "
      + "контур). Требует записи автоколебаний (релейный тест или модель).",
    cohen: "Cohen–Coon (1953). Улучшение ЗН для больших запаздываний "
      + "(L/T > 0,3): длинные транспортёры, теплообменники с трубопроводами, "
      + "аналитические измерения с лабораторной задержкой.",
    chr_sp0: "CHR servo, 0 % — отслеживание уставки без перерегулирования. "
      + "Бэтч-процессы, смена режимов, где перерегулирование недопустимо.",
    chr_sp20: "CHR servo, 20 % — отслеживание уставки с допущением ~20 % "
      + "перерегулирования ради быстродействия.",
    chr_ds0: "CHR regulator, 0 % — удержание уставки при возмущениях без "
      + "перерегулирования (давление при колебаниях расхода).",
    chr_ds20: "CHR regulator, 20 % — удержание уставки при возмущениях "
      + "с ~20 % перерегулирования ради быстродействия.",
    imc: "Internal Model Control (IMC, 1986). Аналитический метод; параметр λ "
      + "задаёт компромисс скорость/робастность. По умолчанию λ = max(T, τ).",
    simc: "SIMC (Skogestad, 2003). Уточнённый IMC; τc = τ по умолчанию — "
      + "баланс скорости и робастности для широкого диапазона L/T.",
    amigo: "AMIGO (Åström–Hägglund, 2004). Оптимизирован по IAE при Ms ≤ 1,4. "
      + "Робастен к ошибкам модели — для неточной модели или меняющегося объекта.",
    itae: "Оптимизация ITAE — численная минимизация интеграла (Нелдер–Мид от "
      + "старта IMC) по отклику модели. Сводит затянутые колебания к минимуму; "
      + "чувствителен к точности FOPDT.",
  };

  function drawRaw(raw) {
    Plotly.react("plot-raw", [
      { x: raw.time, y: raw.sp, name: "SP", mode: "lines",
        line: { color: "#e74c3c" } },
      { x: raw.time, y: raw.pv, name: "PV", mode: "lines",
        line: { color: "#2c3e50" } },
      { x: raw.time, y: raw.cv, name: "CV, %", mode: "lines",
        line: { color: "#18bc9c", width: 1 }, opacity: 0.6 },
    ], Object.assign({ yaxis: { title: "PV / SP" },
                       xaxis: { title: "Время, с" } }, layout), cfg);
  }

  function drawModel(mr, raw) {
    const traces = [
      { x: raw.time, y: raw.pv, name: "PV (данные)", mode: "lines",
        line: { color: "#2c3e50" } },
      { x: mr.time, y: mr.pv, name: "PV (модель FOPDT)", mode: "lines",
        line: { color: "#18bc9c", dash: "dash", width: 2 } },
    ];
    Plotly.react("plot-model", traces,
                 Object.assign({ yaxis: { title: "PV" },
                                xaxis: { title: "Время, с" } }, layout), cfg);
  }

  function drawSim(sim) {
    Plotly.react("plot-sim", [
      { x: sim.time, y: sim.sp, name: "SP (задание)", mode: "lines",
        line: { color: "#e74c3c" } },
      { x: sim.time, y: sim.pv, name: "PV (модель)", mode: "lines",
        line: { color: "#2c3e50", width: 3 } },
      { x: sim.time, y: sim.cv, name: "CV, %", mode: "lines",
        line: { color: "#18bc9c", width: 1 }, opacity: 0.5 },
    ], Object.assign({ yaxis: { title: "Значение" },
                       xaxis: { title: "Время, с" } }, layout), cfg);
  }

  function fmt(v, d = 4) {
    return v === null || v === undefined ? "—" : Number(v).toFixed(d);
  }

  function updateCoeffs(data) {
    $("#in-kp").val(data.coeffs.Kp != null ? fmt(data.coeffs.Kp) : "");
    $("#in-ti").val(data.coeffs.Ti != null ? fmt(data.coeffs.Ti, 2) : "");
    $("#in-td").val(data.coeffs.Td != null ? fmt(data.coeffs.Td, 2) : "");

    if (data.model) {
      $("#model-K").val(fmt(data.model.K));
      $("#model-T").val(fmt(data.model.T, 2));
      $("#model-tau").val(fmt(data.model.tau, 2));
    }

    // П2: оценка управляемости объекта
    if (data.controlability) {
      const c = data.controlability;
      const badge = c.level === "difficult" ? "#e74c3c"
        : c.level === "moderate" ? "#f39c12" : "#18bc9c";
      $("#model-ctrl").html(
        `<span class="badge" style="background-color:${badge};">${c.label} (τ/T=${c.ratio})</span>`);
      $("#model-ctrl-hint").text(c.hints.join("; "));
      $("#model-ctrl-hint-row").show();
    }
    if (data.coeffs && data.coeffs.saturation_limited) {
      $("#model-ctrl-hint").prepend("Kp ограничен из-за насыщения. ");
    }

    // П1: предупреждения о качестве настройки + метрики
    const m = data.metrics;
    if (m) {
      let satTxt = "";
      if (m.sat_frac != null && m.sat_frac > 0.05) {
        satTxt = `, <span class="text-danger">насыщение ${(m.sat_frac * 100).toFixed(0)} %</span>`;
      }
      $("#metrics-box").html(
        `Перерегулирование: <b>${m.overshoot} %</b>, ` +
        `время регулирования: <b>${fmt(m.settling_time, 1)} с</b>, ` +
        `IAE: <b>${m.iae}</b>${satTxt}`);
    }
    if (data.quality_warnings && data.quality_warnings.length) {
      $("#quality-warnings").empty();
      data.quality_warnings.forEach(w => {
        $("#quality-warnings").append(
          `<div class="alert alert-warning py-1 px-2 mb-1 small">${w}</div>`);
      });
    } else {
      $("#quality-warnings").empty();
    }
  }

  function setBusy(busy) {
    $("#spinner, #sim-spinner").toggleClass("d-none", !busy);
    $("#recalc-btn, #run-sim-btn").prop("disabled", busy);
  }

  function collectManual() {
    return {
      manual: {
        Kp: parseFloat($("#in-kp").val()),
        Ti: parseFloat($("#in-ti").val()) || null,
        Td: parseFloat($("#in-td").val()) || null,
      },
    };
  }

  function recalculate(manual) {
    const payload = manual || {};
    payload.method = $("#method").val();
    payload.ctype = $("#ctype").val();
    payload.lambda = parseFloat($("#lambda-input").val()) || null;
    payload.tau_c = parseFloat($("#tau_c-input").val()) || null;
    payload.use_saturation = $("#use-saturation").is(":checked");
    // Ручное редактирование параметров модели FOPDT (если заданы)
    const mK = parseFloat($("#model-K").val());
    payload.model_k = isFinite(mK) ? mK : null;
    const mT = parseFloat($("#model-T").val());
    payload.model_t = isFinite(mT) ? mT : null;
    const mTau = parseFloat($("#model-tau").val());
    payload.model_tau = isFinite(mTau) ? mTau : null;
    // Диапазон хода CV из карточки «Параметры симуляции»
    const cvLimit = parseFloat($("#cv-limit").val());
    payload.cv_limit = cvLimit > 0 ? cvLimit : null;
    payload.cv_clip = true;

    // Параметры симуляции переходного процесса
    const simTime = parseFloat($("#sim-time").val());
    payload.sim_time = simTime > 0 ? simTime : null;
    const pv0 = parseFloat($("#sim-pv0").val());
    payload.pv0 = isFinite(pv0) ? pv0 : null;
    const cv0 = parseFloat($("#sim-cv0").val());
    payload.cv0 = isFinite(cv0) ? cv0 : null;
    const sp0 = parseFloat($("#sim-sp0").val());
    payload.sp_start = isFinite(sp0) ? sp0 : null;

    // Уставка (SP): приоритет — из карточки параметров, иначе из данных
    const spVal = $("#sim-sp").val();
    payload.sp_target = spVal !== "" && spVal !== null
      ? parseFloat(spVal) : null;
    if (!manual) { delete payload.manual; }

    setBusy(true);
    $.ajax({
      url: "/api/calculate",
      method: "POST",
      contentType: "application/json",
      data: JSON.stringify(payload),
    })
      .done((data) => {
        if (data.error) { alert("Ошибка: " + data.error); return; }
        drawRaw(data.raw);
        drawModel(data.model_response, data.raw);
        drawSim(data.sim);
        updateCoeffs(data);
      })
      .fail((xhr) => {
        const msg = xhr.responseJSON && xhr.responseJSON.error
          ? xhr.responseJSON.error : "Ошибка сервера при пересчёте.";
        alert(msg);
      })
      .always(() => setBusy(false));
  }

  // Обновление подсказки «Метод настройки» под выбранный метод
  function updateMethodTip() {
    const el = document.getElementById("method-tip");
    if (!el) return;
    const m = $("#method").val();
    const text = METHOD_HELP[m] || "";
    el.setAttribute("data-bs-title", text);
    if (window.bootstrap && window.bootstrap.Tooltip) {
      const inst = bootstrap.Tooltip.getInstance(el);
      if (inst) inst.setContent({ ".tooltip-inner": text });
    }
  }

  $(() => {
    // Показ поля λ только для IMC, τc — только для SIMC
    $("#method").on("change", () => {
      const m = $("#method").val();
      $("#lambda-group").toggle(m === "imc");
      $("#tau_c-group").toggle(m === "simc");
      $(".lambda-hint").remove();
      updateMethodTip();
    });

    $("#recalc-btn").on("click", () => recalculate());
    $("#run-sim-btn").on("click", () => recalculate(collectManual()));
    $("#apply-model-btn").on("click", () => recalculate());

    // Подсказки-вопросики у полей (делегирование — работает для всех карточек)
    if (window.bootstrap && window.bootstrap.Tooltip) {
      new bootstrap.Tooltip(document.body, {
        selector: '[data-bs-toggle="tooltip"]',
        html: false,
      });
    }
    updateMethodTip();

  });

  return { recalculate };
})();
