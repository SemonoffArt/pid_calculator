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
    $("#coef-kp").text(fmt(data.coeffs.Kp));
    $("#coef-ti").text(data.coeffs.Ti ? fmt(data.coeffs.Ti, 2) : "—");
    $("#coef-td").text(data.coeffs.Td ? fmt(data.coeffs.Td, 2) : "—");

    if (data.model) {
      $("#model-K").text(fmt(data.model.K));
      $("#model-T").text(fmt(data.model.T, 2));
      $("#model-tau").text(fmt(data.model.tau, 2));
    }
    const m = data.metrics;
    if (m) {
      $("#metrics-box").html(
        `Перерегулирование: <b>${m.overshoot} %</b>, ` +
        `время регулирования: <b>${fmt(m.settling_time, 1)} с</b>, ` +
        `IAE: <b>${m.iae}</b>`);
    }
  }

  function setBusy(busy) {
    $("#spinner").toggleClass("d-none", !busy);
    $("#recalc-btn").prop("disabled", busy);
  }

  function recalculate(manual) {
    const payload = manual ? manual : {
      method: $("#method").val(),
      ctype: $("#ctype").val(),
      lambda: parseFloat($("#lambda-input").val()) || null,
    };
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

  $(() => {
    // Показ поля λ только для IMC
    $("#method").on("change", () => {
      const isImc = $("#method").val() === "imc";
      $("#lambda-group").toggle(isImc);
      // ZN closed-loop требует автоколебаний; предупредим
      $(".lambda-hint").remove();
    });

    $("#recalc-btn").on("click", () => recalculate());

    // Страница корректировки: отправляем ручные значения и переходим к графикам
    $("#adjust-form").on("submit", (e) => {
      e.preventDefault();
      const manual = {
        manual: {
          Kp: parseFloat($("#kp").val()),
          Ti: parseFloat($("#ti").val()) || null,
          Td: parseFloat($("#td").val()) || null,
        },
      };
      sessionStorage.setItem("pid_manual", JSON.stringify({
        Kp: manual.manual.Kp, Ti: manual.manual.Ti, Td: manual.manual.Td,
      }));
      window.location.href = "/results";
    });

    // Возврат на страницу результатов после корректировки
    if (window.PAGE === "results") {
      const saved = sessionStorage.getItem("pid_manual");
      if (saved) {
        sessionStorage.removeItem("pid_manual");
        recalculate({ manual: JSON.parse(saved) });
      }
    }
  });

  return { recalculate };
})();
