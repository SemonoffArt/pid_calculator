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
        # 0 — фильтрация отключена; иначе — окно в [MIN, MAX]
        if filter_window != 0:
            filter_window = max(Config.MIN_FILTER_WINDOW,
                                min(Config.MAX_FILTER_WINDOW, filter_window))

        try:
            df = data_loader.load_csv(file)

            # Нормализация PV/SP в 0..100 % шкалы инженерной единицы
            normalize = request.form.get("normalize") == "1"
            norm_scale = None
            if normalize:
                # Масштаб: из Y-max в CSV; иначе — из поля на странице
                norm_scale = df.attrs.get("y_max", {}).get("pv")
                if norm_scale is None:
                    scale_raw = request.form.get("norm_scale", "").strip()
                    if scale_raw:
                        try:
                            norm_scale = float(scale_raw.replace(",", "."))
                        except ValueError:
                            norm_scale = None
                    if norm_scale is None or norm_scale <= 0:
                        flash("Для нормализации укажите максимум шкалы "
                              "(или добавьте строку Y-max в CSV).", "danger")
                        return redirect(url_for("index"))
                df = data_loader.normalize(df, norm_scale)

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
            normalized=bool(normalize),
            norm_scale=norm_scale,
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
            pv_span=data.info.get("pv_span", [None, None]),
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
                "pv_span": state.get("pv_span", []),
                "warnings": state.get("warnings", []),
                "coeffs": state.get("coeffs"),
                "normalized": state.get("normalized", False),
                "norm_scale": state.get("norm_scale"),
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
        tau_c = payload.get("tau_c")
        manual = payload.get("manual")  # {"Kp","Ti","Td"} при ручной коррекции
        # Ручная ступенька задания (в единицах PV) для симуляции
        sp_target = _num(payload.get("sp_target"))
        sp_start = _num(payload.get("sp_start"))
        use_saturation = bool(payload.get("use_saturation", False))
        # Ограничение хода CV (0..100 %). Если выключено — без насыщения
        # (как внешние калькуляторы), для сверки динамики.
        cv_clip = bool(payload.get("cv_clip", True))

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
                                         tau_c=tau_c,
                                         itae_optimizer=itae_opt)
            except ValueError as exc:
                return jsonify({"error": str(exc)}), 400

        save_state(coeffs=coeffs, tuning_method=method, ctype=ctype,
                   sp_target=sp_target, sp_start=sp_start)

        # Время симуляции: покрывает профиль задания из данных
        # плюс запас на переходный процесс
        data_span = float(data.time[-1] - data.time[0])
        sim_time = min(max(2.0 * data_span, 60.0), 3600.0)

        # Если задана только цель SP — стартуем из начальной рабочей точки
        # (первое значение задания из данных)
        if sp_target is not None and sp_start is None:
            sp_start = float(data.sp[0])

        dt_sim = max(data.dt / Config.SIM_SUBSTEPS, 0.01)

        # П3: учёт насыщения — автоматически ограничиваем Kp, если
        # регулятор уходит в упор (флаг «учитывать насыщение»).
        # Важно: референсная ступенька и сама симуляция должны совпадать,
        # иначе Kp ограничивается/проверяется по неверному сценарию.
        if use_saturation and not manual:
            sp0 = float(data.sp[0])
            ref_span = max(abs(float(data.sp[-1]) - sp0), 1.0)
            sim_start_sp = sp_start if sp_start is not None else sp0
            sim_target_sp = sp_target if sp_target is not None \
                else sp0 + ref_span
            coeffs = pid_tuning.limit_kp_by_saturation(
                coeffs, model, ctype,
                sim_start_sp, sim_target_sp,
                dt_sim=dt_sim, sim_time=sim_time,
                max_kp_factor=Config.SATURATION_MAX_KP_FACTOR,
                step_reduction=Config.SATURATION_STEP_REDUCTION,
                warn_sat_frac=Config.SATURATION_WARN_FRAC,
                overshoot_target=Config.SATURATION_OVERSHOOT_TARGET)
            # Используем ту же ступеньку в главной симуляции
            sp_start, sp_target = sim_start_sp, sim_target_sp

        save_state(coeffs=coeffs, cv_clip=cv_clip)

        sim = simulator.simulate_closed_loop(
            model.K, model.T, model.tau, ctype,
            coeffs["Kp"], coeffs["Ti"], coeffs["Td"],
            dt_sim=dt_sim,
            sim_time=sim_time,
            sp_array=data.sp,
            sp_start=sp_start, sp_target=sp_target,
            cv_clip=cv_clip)
        metrics = simulator.quality_metrics(sim[0], sim[1], sim[2], sim[3])

        # П1: нештатные качества настройки — предупреждения для пользователя
        quality_warnings: list[str] = []
        if metrics["overshoot"] > Config.OVERSHOOT_WARN_THRESHOLD:
            quality_warnings.append(
                f"Перерегулирование {metrics['overshoot']:.0f} % — контур "
                "раскачивается. Снизьте Kp или выберите CHR/IMC/ITAE.")
        if metrics["sat_frac"] > Config.SATURATION_WARN_FRAC:
            quality_warnings.append(
                f"Регулятор работает в насыщении "
                f"({metrics['sat_frac'] * 100:.0f} % времени, ход до "
                f"{metrics['cv_max']:.0f} %) — снизьте Kp для линейного режима.")

        # П2: оценка управляемости объекта
        ctrl = pid_tuning.controllability(model)

        # Отклик идентифицированной FOPDT-модели на фактический сигнал CV
        # — для графика «Модель FOPDT и данные». Как и в identify_curve_fit,
        # вход берётся в отклонениях от начальной точки, а к отклику
        # добавляется стартовый уровень PV.
        model_pv = data.pv[0] + identification.fopdt_response(
            data.time, data.cv - data.cv[0], model.K, model.T, model.tau)

        response = {
            "coeffs": coeffs,
            "metrics": metrics,
            "quality_warnings": quality_warnings,
            "controlability": ctrl,
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
