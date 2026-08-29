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

    def _resolve_sim_context(payload: dict) -> dict:
        """Разбирает общий контекст (модель + параметры симуляции) из payload."""
        state = get_state()
        if not state.get("K"):
            raise ValueError("Данные не загружены.")

        sp_target = _num(payload.get("sp_target"))
        sp_start = _num(payload.get("sp_start"))
        pv0 = _num(payload.get("pv0"))
        cv0 = _num(payload.get("cv0"))
        use_saturation = bool(payload.get("use_saturation", False))
        sim_time_inp = _num(payload.get("sim_time"))
        cv_limit = _num(payload.get("cv_limit"))
        if cv_limit is not None and cv_limit > 0:
            cv_clip = True
            cv_min, cv_max = 0.0, cv_limit
        else:
            cv_clip = bool(payload.get("cv_clip", True))
            cv_min, cv_max = 0.0, 100.0

        # Ручное редактирование параметров модели FOPDT (опционально)
        model_k = _num(payload.get("model_k"))
        model_t = _num(payload.get("model_t"))
        model_tau = _num(payload.get("model_tau"))
        if model_k is not None:
            state["K"] = model_k
        if model_t is not None:
            state["T"] = model_t
        if model_tau is not None:
            state["tau"] = model_tau
        save_state(K=state["K"], T=state["T"], tau=state["tau"])

        data = _load_processed()
        model = identification.FopdtModel(K=state["K"], T=state["T"],
                                          tau=state["tau"])

        data_span = float(data.time[-1] - data.time[0])
        sim_time = min(max(2.0 * data_span, 60.0), 3600.0)
        if sim_time_inp is not None and sim_time_inp > 0:
            sim_time = sim_time_inp
        if sp_target is not None and sp_start is None:
            sp_start = float(data.sp[0])
        dt_sim = max(data.dt / Config.SIM_SUBSTEPS, 0.01)

        return {
            "state": state, "data": data, "model": model,
            "cv_clip": cv_clip, "cv_min": cv_min, "cv_max": cv_max,
            "sim_time": sim_time, "sp_start": sp_start,
            "sp_target": sp_target, "dt_sim": dt_sim,
            "pv0": pv0, "cv0": cv0, "use_saturation": use_saturation,
        }

    def _saturation_limited(coeffs, ctx, ctype) -> dict:
        """Применяет П3 (учёт насыщения) к коэффициентам, если включён.

        Также обновляет ctx["sp_start"]/ctx["sp_target"] на референсную
        ступеньку, чтобы основная симуляция совпадала с референсом.
        """
        if not ctx["use_saturation"]:
            return coeffs
        sp0 = float(ctx["data"].sp[0])
        ref_span = max(abs(float(ctx["data"].sp[-1]) - sp0), 1.0)
        sim_start_sp = ctx["sp_start"] if ctx["sp_start"] is not None else sp0
        sim_target_sp = ctx["sp_target"] if ctx["sp_target"] is not None \
            else sp0 + ref_span
        limited = pid_tuning.limit_kp_by_saturation(
            coeffs, ctx["model"], ctype,
            sim_start_sp, sim_target_sp,
            dt_sim=ctx["dt_sim"], sim_time=ctx["sim_time"],
            max_kp_factor=Config.SATURATION_MAX_KP_FACTOR,
            step_reduction=Config.SATURATION_STEP_REDUCTION,
            warn_sat_frac=Config.SATURATION_WARN_FRAC,
            overshoot_target=Config.SATURATION_OVERSHOOT_TARGET,
            sp_array=ctx["data"].sp)
        # Референсная ступенька теперь используется и в основной симуляции
        ctx["sp_start"], ctx["sp_target"] = sim_start_sp, sim_target_sp
        return limited

    def _sim_run(ctx, ctype, coeffs) -> tuple:
        """Запускает симуляцию + метрики для заданных коэффициентов."""
        sim = simulator.simulate_closed_loop(
            ctx["model"].K, ctx["model"].T, ctx["model"].tau, ctype,
            coeffs["Kp"], coeffs["Ti"], coeffs["Td"],
            dt_sim=ctx["dt_sim"], sim_time=ctx["sim_time"],
            sp_array=ctx["data"].sp,
            sp_start=ctx["sp_start"], sp_target=ctx["sp_target"],
            cv_clip=ctx["cv_clip"], cv_min=ctx["cv_min"], cv_max=ctx["cv_max"],
            pv0=ctx["pv0"], cv0=ctx["cv0"])
        metrics = simulator.quality_metrics(sim[0], sim[1], sim[2], sim[3],
                                            cv_min=ctx["cv_min"],
                                            cv_max=ctx["cv_max"])
        return sim, metrics

    def _api_calculate_impl():
        payload = request.get_json(silent=True) or {}
        method = payload.get("method", "zn_open")
        ctype = payload.get("ctype", "PID")
        lam = payload.get("lambda")
        tau_c = payload.get("tau_c")
        manual = payload.get("manual")  # {"Kp","Ti","Td"} при ручной коррекции

        try:
            ctx = _resolve_sim_context(payload)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        state, data, model = ctx["state"], ctx["data"], ctx["model"]

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
                   sp_target=ctx["sp_target"], sp_start=ctx["sp_start"])

        # П3: учёт насыщения
        if not manual:
            coeffs = _saturation_limited(coeffs, ctx, ctype)

        save_state(coeffs=coeffs, cv_clip=ctx["cv_clip"])

        sim, metrics = _sim_run(ctx, ctype, coeffs)

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

    @app.route("/api/simulate_all", methods=["POST"])
    def api_simulate_all():
        """
        Расчёт симуляции для всех методов настройки (кроме ITAE).

        Принимает тот же JSON, что и /api/calculate (метод, ctype, параметры
        симуляции, правки модели). Возвращает список методов с
        коэффициентами, метриками и данными графиков, а также общий контекст
        (модель, исходные данные, отклик модели, управляемость).
        """
        try:
            return _api_simulate_all_impl()
        except Exception as exc:
            app.logger.exception("Ошибка /api/simulate_all")
            return jsonify({"error": f"Внутренняя ошибка: {exc}"}), 500

    def _api_simulate_all_impl():
        payload = request.get_json(silent=True) or {}
        ctype = payload.get("ctype", "PID")
        lam = payload.get("lambda")
        tau_c = payload.get("tau_c")
        selected = payload.get("method", "imc")
        manual = payload.get("manual")  # ручные коэффициенты для выбранного метода

        try:
            ctx = _resolve_sim_context(payload)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        state, data, model = ctx["state"], ctx["data"], ctx["model"]

        itae_opt = (
            lambda m, c, kp0, ti0, td0: identification.optimize_itae(
                m, c, kp0, ti0, td0))

        methods_out = []
        for method in pid_tuning.METHODS:
            if method == "itae":
                continue
            # Ручные коэффициенты применимы только к выбранному методу
            use_manual = bool(manual) and method == selected
            if use_manual:
                coeffs = {k: _num(manual.get(k)) for k in ("Kp", "Ti", "Td")}
            else:
                try:
                    coeffs = pid_tuning.tune(method, model, ctype,
                                             ku=state.get("Ku"),
                                             tu=state.get("Tu"), lam=lam,
                                             tau_c=tau_c,
                                             itae_optimizer=itae_opt)
                except ValueError as exc:
                    methods_out.append({"method": method, "error": str(exc)})
                    continue
            if not use_manual:
                coeffs = _saturation_limited(coeffs, ctx, ctype)
            sim, metrics = _sim_run(ctx, ctype, coeffs)
            methods_out.append({
                "method": method,
                "coeffs": coeffs,
                "metrics": metrics,
                "sim": {
                    "time": sim[0].tolist()[::2],
                    "sp": sim[1].tolist()[::2],
                    "pv": sim[2].tolist()[::2],
                    "cv": sim[3].tolist()[::2],
                },
            })

        # Сохраняем результаты по всем методам (для экспорта PDF/Excel).
        # Храним только коэффициенты, метрики и контекст симуляции — массивы
        # графиков в сессию не пишем (куки ограничены ~4 КБ), графики
        # пересчитываются на лету при экспорте из этого контекста.
        save_state(
            sim_all={
                "ctype": ctype,
                "ctx": {
                    "sim_time": ctx["sim_time"],
                    "dt_sim": ctx["dt_sim"],
                    "sp_start": ctx["sp_start"],
                    "sp_target": ctx["sp_target"],
                    "cv_clip": ctx["cv_clip"],
                    "cv_min": ctx["cv_min"],
                    "cv_max": ctx["cv_max"],
                    "pv0": ctx["pv0"],
                    "cv0": ctx["cv0"],
                },
                "methods": [
                    {
                        "method": m["method"],
                        "coeffs": m.get("coeffs"),
                        "metrics": m.get("metrics"),
                        "error": m.get("error"),
                    }
                    for m in methods_out
                ],
            },
        )

        # Сохраняем состояние выбранного метода (для левой панели)
        sel = next((m for m in methods_out if m["method"] == selected), None)
        quality_warnings = []
        if sel and "coeffs" in sel:
            save_state(coeffs=sel["coeffs"], tuning_method=selected,
                       ctype=ctype, cv_clip=ctx["cv_clip"],
                       sp_target=ctx["sp_target"], sp_start=ctx["sp_start"])
            qm = sel["metrics"]
            if qm["overshoot"] > Config.OVERSHOOT_WARN_THRESHOLD:
                quality_warnings.append(
                    f"Перерегулирование {qm['overshoot']:.0f} % — контур "
                    "раскачивается. Снизьте Kp или выберите CHR/IMC/ITAE.")
            if qm["sat_frac"] > Config.SATURATION_WARN_FRAC:
                quality_warnings.append(
                    f"Регулятор работает в насыщении "
                    f"({qm['sat_frac'] * 100:.0f} % времени, ход до "
                    f"{qm['cv_max']:.0f} %) — снизьте Kp для линейного режима.")

        # Общий контекст (модель, сырые данные, отклик модели, управляемость)
        ctrl = pid_tuning.controllability(model)
        model_pv = data.pv[0] + identification.fopdt_response(
            data.time, data.cv - data.cv[0], model.K, model.T, model.tau)

        response = {
            "method": selected,
            "ctype": ctype,
            "controlability": ctrl,
            "model": {"K": state["K"], "T": state["T"], "tau": state["tau"],
                      "Ku": state.get("Ku"), "Tu": state.get("Tu")},
            "warnings": state.get("warnings", []),
            "quality_warnings": quality_warnings,
            "raw": {
                "time": data.time.tolist(), "pv": data.pv.tolist(),
                "sp": data.sp.tolist(), "cv": data.cv.tolist(),
            },
            "model_response": {
                "time": data.time.tolist(),
                "pv": model_pv.tolist(),
            },
            "methods": methods_out,
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
