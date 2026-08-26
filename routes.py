"""
Маршруты приложения: загрузка данных, расчёт, ручная корректировка,
AJAX-API и экспорт отчётов (PDF, Excel).
"""
from __future__ import annotations

import math
import os

import numpy as np
from flask import (Flask, flash, jsonify, redirect, render_template, request,
                   send_file, session, url_for)

from config import Config
from core import data_loader, identification, pid_tuning, simulator
from models import (clear_state, get_state, load_dataframe, new_session_id,
                    save_dataframe, save_state)


def _num(value) -> float | None:
    """Безопасное преобразование значения в float (None для пустых)."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _finite(value):
    """
    Рекурсивно заменяет NaN/Infinity на None.

    jsonify сериализует NaN как литерал `NaN`, что невалидно для строгого
    браузерного JSON.parse — из-за этого фронтенд считал ответ ошибкой.
    """
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _finite(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_finite(v) for v in value]
    return value


def register_routes(app: Flask) -> None:
    app.jinja_env.globals["METHODS"] = pid_tuning.METHODS
    app.jinja_env.globals["CONTROLLER_TYPES"] = pid_tuning.CONTROLLER_TYPES

    @app.route("/")
    def index():
        """Главная страница — загрузка файла."""
        return render_template("index.html")

    @app.route("/upload", methods=["POST"])
    def upload():
        """Обработка загруженного CSV-файла."""
        file = request.files.get("file")
        if not file or not file.filename:
            flash("Файл не выбран.", "danger")
            return redirect(url_for("index"))

        ext = file.filename.rsplit(".", 1)[-1].lower()
        if ext not in app.config["ALLOWED_EXTENSIONS"]:
            flash("Допустимы только файлы .csv / .txt", "danger")
            return redirect(url_for("index"))

        # Параметры предобработки из формы
        step_raw = request.form.get("interp_step", "").strip()
        interp_step = None
        if step_raw:
            try:
                interp_step = float(step_raw.replace(",", "."))
                if interp_step <= 0:
                    raise ValueError
            except ValueError:
                flash("Шаг интерполяции должен быть положительным числом.",
                      "warning")
                return redirect(url_for("index"))

        try:
            filter_window = int(request.form.get("filter_window",
                                                 Config.DEFAULT_FILTER_WINDOW))
        except (TypeError, ValueError):
            filter_window = Config.DEFAULT_FILTER_WINDOW
        filter_window = max(Config.MIN_FILTER_WINDOW,
                            min(Config.MAX_FILTER_WINDOW, filter_window))

        try:
            df = data_loader.load_csv(file)
            processed = data_loader.preprocess(df, interp_step, filter_window)
        except data_loader.DataError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("index"))
        except Exception as exc:  # непредвиденные ошибки чтения
            app.logger.exception("Ошибка обработки файла")
            flash(f"Ошибка обработки файла: {exc}", "danger")
            return redirect(url_for("index"))

        # Сохраняем состояние
        clear_state()
        path = save_dataframe(processed.to_frame(),
                              app.config["UPLOAD_FOLDER"], file.filename)
        save_state(
            data_path=path,
            upload_name=file.filename,
            interp_step=interp_step,
            filter_window=filter_window,
            id_mode=request.form.get("id_mode", "auto"),
        )

        # Сразу выполняем первичную идентификацию и расчёт
        try:
            result = _identify_and_store(app)
        except ValueError as exc:
            flash(f"Данные загружены, но идентификация не удалась: {exc}",
                  "warning")
            return redirect(url_for("results_page"))

        flash("Файл успешно обработан. Модель идентифицирована.", "success")
        return redirect(url_for("results_page"))

    def _load_processed() -> data_loader.ProcessData:
        """Восстанавливает предобработанные данные из сессии."""
        state = get_state()
        if not state.get("data_path"):
            raise FileNotFoundError("Данные не найдены.")
        df = load_dataframe(state["data_path"])
        return data_loader.preprocess(df, state.get("interp_step"),
                                      state.get("filter_window", 5))

    def _identify_and_store(app_: Flask) -> dict:
        """Идентификация + сохранение модели в сессию. Возвращает результат."""
        data = _load_processed()
        res = identification.identify(data, get_state().get("id_mode", "auto"))
        model = res["model"]
        # ВНИМАНИЕ: массивы данных в сессию не сохраняем — браузеры
        # отбрасывают куки больше 4 КБ. Данные читаются из uploads/.
        save_state(
            K=model.K, T=model.T, tau=model.tau,
            fit_quality=model.fit_quality, Ku=res.get("Ku"), Tu=res.get("Tu"),
            method_used=model.method, warnings=res.get("warnings", []),
        )
        return res

    # ---------------------------------------------------------------- results
    @app.route("/results")
    def results_page():
        """Страница результатов с графиками."""
        state = get_state()
        if not state.get("K"):
            flash("Сначала загрузите данные процесса.", "warning")
            return redirect(url_for("index"))
        return render_template(
            "results.html",
            state={
                "upload_name": state.get("upload_name"),
                "K": state.get("K"), "T": state.get("T"),
                "tau": state.get("tau"), "Ku": state.get("Ku"),
                "Tu": state.get("Tu"),
                "fit_quality": state.get("fit_quality"),
                "info": state.get("info", {}),
                "warnings": state.get("warnings", []),
                "coeffs": state.get("coeffs"),
            },
        )

    # ------------------------------------------------------------- AJAX API
    @app.route("/api/calculate", methods=["POST"])
    def api_calculate():
        """
        Пересчёт коэффициентов и симуляции.

        Принимает JSON: {method, ctype, lambda}.
        Возвращает JSON с коэффициентами, данными графиков и метриками.
        """
        try:
            return _api_calculate_impl()
        except Exception as exc:  # любая ошибка — понятный JSON вместо HTML
            app.logger.exception("Ошибка /api/calculate")
            return jsonify({"error": f"Внутренняя ошибка: {exc}"}), 500

    def _api_calculate_impl():
        payload = request.get_json(silent=True) or {}
        method = payload.get("method", "zn_open")
        ctype = payload.get("ctype", "PID")
        lam = payload.get("lambda")
        manual = payload.get("manual")  # {"Kp","Ti","Td"} при ручной коррекции

        state = get_state()
        if not state.get("K"):
            return jsonify({"error": "Данные не загружены."}), 400

        try:
            data = _load_processed()
        except (FileNotFoundError, data_loader.DataError):
            return jsonify({"error": "Файл данных не найден. "
                                     "Загрузите файл заново."}), 400
        model = identification.FopdtModel(K=state["K"], T=state["T"],
                                          tau=state["tau"])

        if manual:
            coeffs = {k: _num(manual.get(k)) for k in ("Kp", "Ti", "Td")}
        else:
            try:
                itae_opt = (
                    lambda m, c, kp0, ti0, td0: identification.optimize_itae(
                        m, c, kp0, ti0, td0))
                coeffs = pid_tuning.tune(method, model, ctype,
                                         ku=state.get("Ku"),
                                         tu=state.get("Tu"), lam=lam,
                                         itae_optimizer=itae_opt)
            except ValueError as exc:
                return jsonify({"error": str(exc)}), 400

        save_state(coeffs=coeffs, tuning_method=method, ctype=ctype)

        # Время симуляции: покрывает профиль задания из данных
        # плюс запас на переходный процесс
        data_span = float(data.time[-1] - data.time[0])
        sim_time = min(max(2.0 * data_span, 60.0), 3600.0)

        sim = simulator.simulate_closed_loop(
            model.K, model.T, model.tau, ctype,
            coeffs["Kp"], coeffs["Ti"], coeffs["Td"],
            dt_sim=max(data.dt / Config.SIM_SUBSTEPS, 0.01),
            sim_time=sim_time,
            sp_array=data.sp)
        metrics = simulator.quality_metrics(sim[0], sim[1], sim[2])

        # Отклик идентифицированной FOPDT-модели на фактический сигнал CV
        # — для графика «Модель FOPDT и данные». Как и в identify_curve_fit,
        # вход берётся в отклонениях от начальной точки, а к отклику
        # добавляется стартовый уровень PV.
        model_pv = data.pv[0] + identification.fopdt_response(
            data.time, data.cv - data.cv[0], model.K, model.T, model.tau)

        response = {
            "coeffs": coeffs,
            "metrics": metrics,
            "model": {"K": state["K"], "T": state["T"], "tau": state["tau"],
                      "Ku": state.get("Ku"), "Tu": state.get("Tu")},
            "warnings": state.get("warnings", []),
            "raw": {
                "time": data.time.tolist(), "pv": data.pv.tolist(),
                "sp": data.sp.tolist(), "cv": data.cv.tolist(),
            },
            "model_response": {
                "time": data.time.tolist(),
                "pv": model_pv.tolist(),
            },
            "sim": {
                "time": sim[0].tolist()[::2],   # прореживаем для графика
                "sp": sim[1].tolist()[::2],
                "pv": sim[2].tolist()[::2],
                "cv": sim[3].tolist()[::2],
            },
        }
        return jsonify(_finite(response))

    @app.route("/adjust")
    def adjust_page():
        """Страница ручной корректировки коэффициентов."""
        state = get_state()
        if not state.get("coeffs"):
            flash("Нет рассчитанных коэффициентов.", "warning")
            return redirect(url_for("results_page"))
        return render_template("adjust.html", coeffs=state["coeffs"],
                               ctype=state.get("ctype", "PID"))

    @app.route("/api/reidentify", methods=["POST"])
    def api_reidentify():
        """Повторная идентификация выбранным методом (AJAX)."""
        payload = request.get_json(silent=True) or {}
        mode = payload.get("mode", "auto")
        save_state(id_mode=mode)
        try:
            _identify_and_store(app)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        state = get_state()
        return jsonify({"K": state["K"], "T": state["T"], "tau": state["tau"],
                        "Ku": state.get("Ku"), "Tu": state.get("Tu")})

    # --------------------------------------------------------------- exports
    @app.route("/export/pdf")
    def export_pdf():
        """Генерация PDF-отчёта на лету."""
        state = get_state()
        if not state.get("coeffs"):
            flash("Нет результатов для экспорта.", "warning")
            return redirect(url_for("results_page"))
        from reports.pdf_report import build_pdf
        data = _load_processed()
        buf = build_pdf(state, data)
        return send_file(buf, mimetype="application/pdf", as_attachment=True,
                         download_name="pid_report.pdf")

    @app.route("/export/excel")
    def export_excel():
        """Генерация Excel-отчёта на лету."""
        state = get_state()
        if not state.get("coeffs"):
            flash("Нет результатов для экспорта.", "warning")
            return redirect(url_for("results_page"))
        from reports.excel_report import build_excel
        data = _load_processed()
        buf = build_excel(state, data)
        return send_file(buf,
                         mimetype="application/vnd.openxmlformats-officedocument"
                                  ".spreadsheetml.sheet",
                         as_attachment=True, download_name="pid_report.xlsx")

    @app.errorhandler(413)
    def too_large(_e):
        flash("Файл слишком большой (максимум 16 МБ).", "danger")
        return redirect(url_for("index"))
