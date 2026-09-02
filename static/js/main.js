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

  // Текущий тип модели (FOPDT / IPDT) — переключается на странице результатов
  let currentModelType = "fopdt";

  // Описания методов настройки — для подсказки «Метод настройки»
  const METHOD_HELP = {
    zn_open: "Зиглер–Николс по разомкнутой характеристике (K, T, τ). "
      + "Quarter-decay ratio: каждое следующее перерегулирование в 4 раза "
      + "меньше. Перерегулирование ~25–50 %, агрессивная настройка. "
      + "Для FOPDT с τ/T = 0,1…1; не годится, где перерегулирование опасно.",
    zn_closed: "Зиглер–Николс по критическим параметрам Ku/Tu (замкнутый "
      + "контур). Требует записи автоколебаний (релейный тест или модель).",
    cohen: "Cohen–Coon (1953). Улучшение ЗН для больших запаздываний "
      + "(τ/T > 0,3): длинные транспортёры, теплообменники с трубопроводами, "
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
      + "баланс скорости и робастности для широкого диапазона τ/T.",
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

  // Данные последней оценки — для кнопки «Скопировать результаты»
  let lastAssessmentData = null;

  // График и метрики «Оценки регулирования» по фактическим данным
  function drawAssessment(assess, raw) {
    if (!assess || !$("#plot-assessment").length) return;
    lastAssessmentData = { assess, raw };
    Plotly.react("plot-assessment", [
      { x: raw.time, y: raw.sp, name: "SP (задание)", mode: "lines",
        line: { color: "#e74c3c" } },
      { x: raw.time, y: raw.pv, name: "PV (факт)", mode: "lines",
        line: { color: "#2c3e50", width: 3 } },
      { x: raw.time, y: raw.cv, name: "CV, %", mode: "lines",
        line: { color: "#18bc9c", width: 1 }, opacity: 0.5 },
    ], Object.assign({ yaxis: { title: "Значение" },
                       xaxis: { title: "Время, с" } }, layout), cfg);

    let html = "";
    if (assess.step_detected) {
      const settle = assess.settled
        ? `${fmt(assess.settling_time, 1)} с`
        : `<span class="text-warning">не достигнуто</span>`;
      html = `<span class="me-3">Перерегулирование: <b>${fmt(assess.overshoot, 2)} %</b></span>` +
        `<span class="me-3">Время регулирования: <b>${settle}</b></span>` +
        `<span>IAE: <b>${fmt(assess.iae, 2)}</b></span>`;
      if (assess.step_index != null && assess.step_index >= 0
          && raw.time && raw.time[assess.step_index] != null) {
        html += `<div class="text-muted mt-1">Ступенька SP в ` +
          `${fmt(raw.time[assess.step_index], 1)} с; PV: ` +
          `${fmt(assess.start_pv, 2)} → ${fmt(assess.target_sp, 2)}` +
          ` (Δ = ${fmt(assess.delta, 2)}), полоса ± ${fmt(assess.band, 2)}</div>`;
      }
    } else {
      html = `<span class="text-warning">Ступенька задания не обнаружена.</span> ` +
        `<span class="me-3">IAE всего участка: <b>${fmt(assess.iae, 2)}</b></span>`;
      if (assess.note) html += `<div class="text-muted mt-1">${assess.note}</div>`;
    }
    $("#assessment-metrics").html(html);
    renderAssessmentDetails(assess, raw);
  }

  // Качественная интерпретация полученных результатов
  function buildInterpretation(a) {
    const b = (cls, txt) =>
      `<div class="alert alert-${cls} py-2 px-3 mb-2 small">${txt}</div>`;
    if (!a.step_detected) {
      return b("warning", "Ступенька задания не обнаружена или слишком близка к "
        + "краю записи — переходный процесс не выделен, поэтому "
        + "перерегулирование и время регулирования не определены. Рассчитан IAE "
        + "по всей записи." + (a.note ? " " + a.note : ""));
    }
    if (!a.settled) {
      return b("warning", `Процесс не вышел на установившийся режим за время `
        + `записи: PV не вошёл в полосу ±${fmt(a.band, 2)} и не удержался в ней. `
        + `Время регулирования не определено. Перерегулирование: `
        + `${fmt(a.overshoot, 2)} %.`);
    }
    let cls, txt;
    if (a.overshoot <= 2) {
      cls = "success";
      txt = "Отличное качество: перерегулирование ≤ 2 % — контур быстро и " +
        "плавно выходит на задание без заметного выброса.";
    } else if (a.overshoot <= 10) {
      cls = "info";
      txt = `Хорошее качество: умеренное перерегулирование `
        + `${fmt(a.overshoot, 2)} % — допустимо для большинства процессов.`;
    } else if (a.overshoot <= 25) {
      cls = "warning";
      txt = `Заметное перерегулирование ${fmt(a.overshoot, 2)} % — контур `
        + "чувствительный. Рассмотрите снижение Kp или более консервативный "
        + "метод настройки (IMC, CHR-0).";
    } else {
      cls = "danger";
      txt = `Высокое перерегулирование ${fmt(a.overshoot, 2)} % — контур может `
        + "раскачиваться. Снизьте Kp или перейдите на методы без "
        + "перерегулирования (CHR-0, IMC с большим λ).";
    }
    return b(cls, txt);
  }

  // Таблица «Все полученные результаты»
  function renderAssessmentDetails(assess, raw) {
    const el = $("#assessment-details");
    if (!el.length) return;
    const rows = [];
    const add = (k, v) => rows.push(`<tr><th>${k}</th><td>${v}</td></tr>`);

    add("Длительность записи", fmt(assess.duration, 1) + " с");
    add("Ступенька задания (SP)", assess.step_detected
      ? "обнаружена" : "не обнаружена");
    if (assess.step_detected) {
      if (assess.step_index != null && raw.time
          && raw.time[assess.step_index] != null) {
        add("Момент ступеньки", fmt(raw.time[assess.step_index], 1) + " с");
      }
      add("PV до ступеньки (базовая)", fmt(assess.start_pv, 2));
      add("Задание после ступеньки (SP)", fmt(assess.target_sp, 2));
      add("Изменение Δ", fmt(assess.delta, 2));
      add("Полоса установления ±", fmt(assess.band, 2) + " (2 % от Δ)");
      add("Перерегулирование", fmt(assess.overshoot, 2) + " %");
      add("Время регулирования", assess.settled
        ? fmt(assess.settling_time, 1) + " с" : "не достигнуто");
    }
    add("IAE", fmt(assess.iae, 2));

    el.html(buildInterpretation(assess)
      + `<div class="table-responsive"><table class="table table-sm table-striped mb-0">`
      + `<tbody>${rows.join("")}</tbody></table></div>`);
  }

  // Загрузка данных и метрик для отдельной страницы «Оценка регулирования»
  function loadAssessment() {
    if (!window.PAGE || window.PAGE !== "assessment") return;
    const spinner = $("#assessment-spinner");
    const metrics = $("#assessment-metrics");
    spinner.removeClass("d-none");
    metrics.html("");
    $.ajax({
      url: "/api/assessment",
      method: "GET",
    })
      .done((data) => {
        if (data.error) {
          $("#assessment-error").html(
            `<div class="alert alert-warning">${data.error}</div>`);
          return;
        }
        $("#assessment-error").empty();
        drawAssessment(data.assessment, data.raw);
      })
      .fail((xhr) => {
        const msg = xhr.responseJSON && xhr.responseJSON.error
          ? xhr.responseJSON.error : "Ошибка при загрузке данных.";
        $("#assessment-error").html(
          `<div class="alert alert-warning">${msg}</div>`);
      })
      .always(() => spinner.addClass("d-none"));
  }

  // Текстовое представление оценки — для буфера обмена
  function buildAssessmentText() {
    if (!lastAssessmentData) return "";
    const { assess, raw } = lastAssessmentData;
    const file = $("#copy-result-btn").data("file") || "";
    const lines = ["Оценка регулирования", "-------------------------"];
    if (file) lines.push("Файл: " + file);
    if (assess.step_detected) {
      let stepTxt = "";
      if (assess.step_index != null && assess.step_index >= 0
          && raw.time && raw.time[assess.step_index] != null) {
        stepTxt = " в " + fmt(raw.time[assess.step_index], 1) + " с";
      }
      lines.push("Ступенька SP" + stepTxt);
      if (assess.start_pv != null) {
        lines.push("PV: " + fmt(assess.start_pv, 2) + " -> "
          + fmt(assess.target_sp, 2) + " (Δ = " + fmt(assess.delta, 2) + ")");
      }
      lines.push("Перерегулирование: " + fmt(assess.overshoot, 2) + " %");
      lines.push("Время регулирования: "
        + (assess.settled ? fmt(assess.settling_time, 1) + " с" : "не достигнуто"));
      lines.push("IAE: " + fmt(assess.iae, 2));
    } else {
      lines.push("Ступенька задания не обнаружена");
      lines.push("IAE (вся запись): " + fmt(assess.iae, 2));
    }
    return lines.join("\n");
  }

  // Резервное копирование через скрытый textarea (старые браузеры / http)
  function fallbackCopy(text, done) {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand("copy"); } catch (e) { /* игнорируем */ }
    document.body.removeChild(ta);
    if (done) done();
  }

  function copyResults() {
    const text = buildAssessmentText();
    if (!text) return;
    const btn = $("#copy-result-btn");
    const done = () => {
      const original = btn.text();
      btn.text("Скопировано ✓")
        .removeClass("btn-outline-primary").addClass("btn-success");
      setTimeout(() => {
        btn.text(original).addClass("btn-outline-primary").removeClass("btn-success");
      }, 2000);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done).catch(() => fallbackCopy(text, done));
    } else {
      fallbackCopy(text, done);
    }
  }

  function drawModel(mr, raw) {
    const label = currentModelType === "ipdt"
      ? "PV (модель IPDT)" : "PV (модель FOPDT)";
    const traces = [
      { x: raw.time, y: raw.pv, name: "PV (данные)", mode: "lines",
        line: { color: "#2c3e50" } },
      { x: mr.time, y: mr.pv, name: label, mode: "lines",
        line: { color: "#18bc9c", dash: "dash", width: 2 } },
    ];
    Plotly.react("plot-model", traces,
                 Object.assign({ yaxis: { title: "PV" },
                                xaxis: { title: "Время, с" } }, layout), cfg);
  }

  function drawSimTo(divId, sim) {
    Plotly.react(divId, [
      { x: sim.time, y: sim.sp, name: "SP (задание)", mode: "lines",
        line: { color: "#e74c3c" } },
      { x: sim.time, y: sim.pv, name: "PV (модель)", mode: "lines",
        line: { color: "#2c3e50", width: 3 } },
      { x: sim.time, y: sim.cv, name: "CV, %", mode: "lines",
        line: { color: "#18bc9c", width: 1 }, opacity: 0.5 },
    ], Object.assign({ yaxis: { title: "Значение" },
                       xaxis: { title: "Время, с" } }, layout), cfg);
  }

  // Названия методов для заголовков карточек и таблицы
  const METHOD_NAMES = {
    zn_open: "Зиглер–Николс (разомкнутый)",
    zn_closed: "Зиглер–Николс (замкнутый)",
    cohen: "Cohen–Coon (1953)",
    chr_sp0: "Чен–Хрон: уставка, 0 %",
    chr_sp20: "Чен–Хрон: уставка, 20 %",
    chr_ds0: "Чен–Хрон: возмущение, 0 %",
    chr_ds20: "Чен–Хрон: возмущение, 20 %",
    imc: "Внутренняя модель (IMC)",
    simc: "SIMC (Skogestad, 2003)",
    amigo: "AMIGO (Åström–Hägglund)",
    itae: "Оптимизация ITAE",
  };

  function metricsLine(m) {
    let satTxt = "";
    if (m.sat_frac != null && m.sat_frac > 0.05) {
      satTxt = `, <span class="text-danger">насыщение ${(m.sat_frac * 100).toFixed(0)} %</span>`;
    }
    return `Перерегулирование: <b>${m.overshoot} %</b>, ` +
      `время регулирования: <b>${fmt(m.settling_time, 1)} с</b>, ` +
      `IAE: <b>${m.iae}</b>${satTxt}`;
  }

  function coeffsLine(c) {
    let s = `Kp = <b>${fmt(c.Kp)}</b>`;
    if (c.Ti != null) s += `, Ti = <b>${fmt(c.Ti, 2)} с</b>`;
    if (c.Td != null) s += `, Td = <b>${fmt(c.Td, 2)} с</b>`;
    return s;
  }

  // Значок-подсказка (?) с описанием метода настройки
  function methodHelpIcon(method) {
    const text = METHOD_HELP[method] || "";
    const esc = text
      .replace(/&/g, "&amp;").replace(/"/g, "&quot;")
      .replace(/</g, "&lt;").replace(/>/g, "&gt;");
    return `<span class="help-q" data-bs-toggle="tooltip" tabindex="0"
      data-bs-title="${esc}">?</span>`;
  }

  // Основная плавающая карточка (выбранный метод) + карточки остальных
  function renderAllSims(data) {
    const methods = data.methods || [];
    const primary = methods.find(m => m.method === data.method) || methods[0];
    const others = methods.filter(m => m !== primary);

    // -- основная карточка
    const primaryDiv = $("#plot-sim-primary");
    if (primary && primary.sim) {
      const name = METHOD_NAMES[primary.method] || primary.method;
      $("#sim-primary-title").html(`${name}${methodHelpIcon(primary.method)}`);
      if (primary.error) {
        primaryDiv.empty();
        primaryDiv.html(`<div class="alert alert-warning">${primary.error}</div>`);
      } else {
        drawSimTo("plot-sim-primary", primary.sim);
        $("#sim-primary-metrics").html(metricsLine(primary.metrics));
        $("#sim-primary-coeffs").html(coeffsLine(primary.coeffs));
      }
    }

    // -- карточки остальных методов
    const container = $("#sim-others");
    container.empty();
    // Создаём контейнеры Plotly заранее, затем заполняем
    others.forEach((m, idx) => {
      const divId = `plot-algo-${idx}`;
      const card = $(`
        <div class="card shadow-sm p-3 mb-3 sim-algo-card">
          <h5 class="mb-3">${METHOD_NAMES[m.method] || m.method}${methodHelpIcon(m.method)}</h5>
          <div id="${divId}" style="height:300px;"></div>
          <div class="sim-algo-metrics small text-muted mt-2"></div>
          <div class="sim-algo-coeffs small mt-1 text-muted"></div>
        </div>`);
      container.append(card);
      if (m.error) {
        $(`#${divId}`).html(`<div class="alert alert-warning">${m.error}</div>`);
      } else {
        drawSimTo(divId, m.sim);
        card.find(".sim-algo-metrics").html(metricsLine(m.metrics));
        card.find(".sim-algo-coeffs").html(coeffsLine(m.coeffs));
      }
    });

    // -- сравнительная таблица
    renderCompareTable(methods);
  }

  // Состояние сортировки таблицы сравнения
  let sortKey = null;        // "iae" | "overshoot" | ...
  let sortDir = 1;           // 1 - по возрастанию, -1 - по убыванию
  let tableMethods = [];

  function renderCompareTable(methods) {
    tableMethods = methods.filter(m => !m.error);
    const tbody = $("#sim-compare-table tbody");
    tbody.empty();
    const rows = sortKey ? sortRows(tableMethods) : tableMethods;
    rows.forEach(m => {
      const mm = m.metrics;
      tbody.append(
        `<tr>
          <td>${METHOD_NAMES[m.method] || m.method}</td>
          <td>${fmt(m.coeffs.Kp)}</td>
          <td>${m.coeffs.Ti != null ? fmt(m.coeffs.Ti, 2) : "—"}</td>
          <td>${m.coeffs.Td != null ? fmt(m.coeffs.Td, 2) : "—"}</td>
          <td>${mm.overshoot}</td>
          <td>${fmt(mm.settling_time, 1)}</td>
          <td>${mm.iae}</td>
        </tr>`);
    });
    updateSortArrows();
  }

  function sortRows(methods) {
    return methods.slice().sort((a, b) => {
      const av = a.metrics[sortKey];
      const bv = b.metrics[sortKey];
      if (av === bv) return 0;
      if (av == null) return 1;    // null/undefined — в конец
      if (bv == null) return -1;
      return (av - bv) * sortDir;
    });
  }

  function updateSortArrows() {
    $(".sortable .sort-arrow").text("");
    if (sortKey) {
      const el = $(`.sortable[data-key="${sortKey}"] .sort-arrow`);
      el.text(sortDir === 1 ? "▲" : "▼");
    }
  }

  function fmt(v, d = 4) {
    return v === null || v === undefined ? "—" : Number(v).toFixed(d);
  }

  function updateCoeffs(data) {
    // Выбранный метод: берём из списка methods
    const sel = (data.methods || []).find(m => m.method === data.method)
      || (data.methods && data.methods[0]);
    const coeffs = sel && sel.coeffs ? sel.coeffs : null;
    const metrics = sel && sel.metrics ? sel.metrics : null;

    if (coeffs) {
      $("#in-kp").val(coeffs.Kp != null ? fmt(coeffs.Kp) : "");
      $("#in-ti").val(coeffs.Ti != null ? fmt(coeffs.Ti, 2) : "");
      $("#in-td").val(coeffs.Td != null ? fmt(coeffs.Td, 2) : "");
    }

    if (data.model) {
      currentModelType = data.model.type || currentModelType;
      $("#model-chart-title").text(currentModelType === "ipdt" ? "IPDT" : "FOPDT");
      $("#model-K").val(fmt(data.model.K));
      $("#model-T").val(fmt(data.model.T, 2));
      $("#model-Ka").val(fmt(data.model.Ka));
      $("#model-tau").val(fmt(data.model.tau, 2));
      setModelType(currentModelType);
    }

    // П2: оценка управляемости объекта
    if (data.controlability) {
      const c = data.controlability;
      const badge = c.level === "difficult" ? "#e74c3c"
        : c.level === "moderate" ? "#f39c12" : "#18bc9c";
      const ratioLabel = currentModelType === "ipdt"
        ? `(τ=${c.ratio} с)` : `(τ/T=${c.ratio})`;
      $("#model-ctrl").html(
        `<span class="badge" style="background-color:${badge};">${c.label} ${ratioLabel}</span>`);
      $("#model-ctrl-hint").text(c.hints.join("; "));
      $("#model-ctrl-hint-row").show();
    }
    if (coeffs && coeffs.saturation_limited) {
      $("#model-ctrl-hint").prepend("Kp ограничен из-за насыщения. ");
    }

    // П1: метрики выбранного метода + предупреждения
    if (metrics) {
      $("#metrics-box").html(metricsLine(metrics));
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

  // Переключение типа модели: поля, методы, заголовки графиков
  function setModelType(mt) {
    currentModelType = mt === "ipdt" ? "ipdt" : "fopdt";
    // Поля параметров модели
    $("#fopdt-K-row, #fopdt-T-row").toggle(currentModelType === "fopdt");
    $("#ipdt-Ka-row").toggle(currentModelType === "ipdt");
    // Фильтрация списка методов по применимости
    $("#method option").each(function () {
      const applicable = currentModelType === "ipdt"
        ? (this.dataset.ipdt === "1") : true;
      this.hidden = !applicable;
      this.style.display = applicable ? "" : "none";
    });
    // Если выбранный метод недоступен — выбираем первый доступный
    const sel = $("#method").val();
    const selOk = $("#method option").filter(function () {
      return this.value === sel && this.style.display !== "none";
    }).length > 0;
    if (!selOk) {
      const first = $("#method option").filter(function () {
        return this.style.display !== "none";
      }).first();
      if (first.length) $("#method").val(first.val());
    }
    $("#method").trigger("change");
    $("#model-chart-title").text(currentModelType === "ipdt" ? "IPDT" : "FOPDT");
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
    payload.model_type = currentModelType;
    // Ручное редактирование параметров модели (в зависимости от типа)
    if (currentModelType === "ipdt") {
      const mKa = parseFloat($("#model-Ka").val());
      payload.model_ka = isFinite(mKa) ? mKa : null;
      delete payload.model_t;
      delete payload.model_k;
    } else {
      const mK = parseFloat($("#model-K").val());
      payload.model_k = isFinite(mK) ? mK : null;
      const mT = parseFloat($("#model-T").val());
      payload.model_t = isFinite(mT) ? mT : null;
      delete payload.model_ka;
    }
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
      url: "/api/simulate_all",
      method: "POST",
      contentType: "application/json",
      data: JSON.stringify(payload),
    })
      .done((data) => {
        if (data.error) { alert("Ошибка: " + data.error); return; }
        drawRaw(data.raw);
        drawModel(data.model_response, data.raw);
        renderAllSims(data);
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
    // Смена типа модели: повторная идентификация + пересчёт
    $("#model-type").on("change", function () {
      const mt = $(this).val() === "ipdt" ? "ipdt" : "fopdt";
      setBusy(true);
      $.ajax({
        url: "/api/reidentify",
        method: "POST",
        contentType: "application/json",
        data: JSON.stringify({ model_type: mt, mode: "auto" }),
      })
        .done((r) => {
          if (r.error) { alert("Ошибка: " + r.error); setBusy(false); return; }
          setModelType(mt);
          // Обновляем поля идентифицированными параметрами
          if (r.model_type === "ipdt") {
            $("#model-Ka").val(fmt(r.Ka));
            $("#sim-cv0").val(fmt(r.balance, 2));   // балансный ход CV
          } else {
            $("#model-K").val(fmt(r.K));
            $("#model-T").val(fmt(r.T, 2));
            $("#sim-cv0").val("0");
          }
          $("#model-tau").val(fmt(r.tau, 2));
          recalculate();
        })
        .fail((xhr) => {
          const msg = xhr.responseJSON && xhr.responseJSON.error
            ? xhr.responseJSON.error : "Ошибка идентификации.";
          alert(msg);
          setBusy(false);
        });
    });
    // Enter в полях коэффициентов запускает симуляцию (как кнопка run-sim)
    $("#in-kp, #in-ti, #in-td").on("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        recalculate(collectManual());
      }
    });
    $("#apply-model-btn").on("click", () => recalculate());

    // Быстрый переход к началу/концу страницы
    $("#scroll-top").on("click", () => $("html, body").animate({ scrollTop: 0 }, 300));
    $("#scroll-bottom").on("click", () =>
      $("html, body").animate({ scrollTop: $(document).height() }, 300));

    // Сортировка таблицы сравнения по колонкам IAE / Перерегулирование
    $("#sim-compare-table").on("click", "th.sortable", function () {
      const key = $(this).data("key");
      if (sortKey === key) {
        sortDir = -sortDir;
      } else {
        sortKey = key;
        sortDir = 1;   // по возрастанию (лучшее значение сверху)
      }
      renderCompareTable(tableMethods);
    });

    // Подсказки-вопросики у полей (делегирование — работает для всех карточек)
    if (window.bootstrap && window.bootstrap.Tooltip) {
      new bootstrap.Tooltip(document.body, {
        selector: '[data-bs-toggle="tooltip"]',
        html: false,
      });
    }
    updateMethodTip();

    // Инициализация типа модели из серверного состояния (только результаты)
    if ($("#model-type").length) {
      setModelType($("#model-type").val());
    }
    // Отдельная страница «Оценка регулирования»
    if (window.PAGE === "assessment") {
      loadAssessment();
    }
    // Кнопка «Скопировать результаты» (страница оценки)
    if ($("#copy-result-btn").length) {
      $("#copy-result-btn").on("click", copyResults);
    }
  });

  return { recalculate, loadAssessment };
})();
