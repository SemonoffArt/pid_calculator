"""
PDF-отчёт (reportlab): титульная часть, таблица параметров и коэффициентов,
графики, встроенные как PNG (рендер через Matplotlib).

Отчёт включает результаты симуляции по всем методам настройки и сводную
таблицу с коэффициентами и показателями качества.
"""
from __future__ import annotations

import io
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from flask import session  # noqa: F401 (state передаётся явно)
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (Image, PageBreak, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle)

from core import simulator

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

RU_TITLE = "Расчёт коэффициентов ПИД-регулятора"

# Шрифт с поддержкой кириллицы (DejaVuSans поставляется с matplotlib).
# Встроенные шрифты reportlab (Helvetica и т.п.) не содержат глифов
# кириллицы — при экспорте русские буквы выводились бы как «чёрные
# прямоугольники».
_FONTS_REGISTERED = False


def _register_fonts() -> None:
    global _FONTS_REGISTERED
    if _FONTS_REGISTERED:
        return
    fdir = os.path.join(matplotlib.get_data_path(), "fonts", "ttf")
    pdfmetrics.registerFont(
        TTFont("DejaVuSans", os.path.join(fdir, "DejaVuSans.ttf")))
    pdfmetrics.registerFont(
        TTFont("DejaVuSans-Bold", os.path.join(fdir, "DejaVuSans-Bold.ttf")))
    # Соответствие «жирного» в тегах <b>…
    from reportlab.lib.fonts import addMapping
    addMapping("DejaVuSans", 0, 0, "DejaVuSans")
    addMapping("DejaVuSans", 1, 0, "DejaVuSans-Bold")
    _FONTS_REGISTERED = True


METHOD_NAMES = {
    "zn_open": "Зиглер–Николс (разомкнутый контур)",
    "zn_closed": "Зиглер–Николс (замкнутый контур)",
    "cohen": "Cohen–Coon (1953)",
    "chr_sp0": "Чен–Хрон (servo, без перерегулирования)",
    "chr_sp20": "Чен–Хрон (servo, 20 %)",
    "chr_ds0": "Чен–Хрон (regulator, без перерегулирования)",
    "chr_ds20": "Чен–Хрон (regulator, 20 %)",
    "imc": "Внутренняя модель (IMC)",
    "simc": "SIMC (Skogestad IMC, 2003)",
    "amigo": "AMIGO (Åström–Hägglund, 2004)",
    "itae": "Оптимизация ITAE",
}


def _fig_to_png(fig) -> io.BytesIO:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def _plot_raw(data) -> io.BytesIO:
    fig, ax = plt.subplots(figsize=(7.5, 3.2))
    ax.plot(data.time, data.pv, label="PV", lw=1)
    ax.plot(data.time, data.sp, label="SP", lw=1)
    ax2 = ax.twinx()
    ax2.plot(data.time, data.cv, label="CV", lw=0.8, alpha=0.6,
             color="tab:green")
    ax.set_xlabel("Время, с"); ax.set_ylabel("PV / SP"); ax2.set_ylabel("CV, %")
    ax.legend(loc="best"); ax.grid(alpha=0.3)
    fig.tight_layout()
    return _fig_to_png(fig)


def _plot_model(state, data) -> io.BytesIO:
    from core.identification import fopdt_response, ipdt_response
    if state.get("model_type") == "ipdt":
        model_pv = float(data.pv[0]) + float(state.get("m0", 0.0)) * data.time \
            + ipdt_response(data.time, data.cv - data.cv[0],
                            state["Ka"], state["tau"])
        label = "Модель IPDT"
    else:
        # Отклик считаем от приращения CV и привязываем к начальному PV —
        # так же, как на веб-странице «Результаты» (иначе модель «уезжает»).
        model_pv = float(data.pv[0]) + fopdt_response(
            data.time, data.cv - data.cv[0], state["K"], state["T"],
            state["tau"])
        label = "Модель FOPDT"
    fig, ax = plt.subplots(figsize=(7.5, 3.0))
    ax.plot(data.time, data.pv, label="PV (данные)", lw=1)
    ax.plot(data.time, model_pv, "--", label=label, lw=1.4)
    ax.set_xlabel("Время, с"); ax.legend(); ax.grid(alpha=0.3)
    fig.tight_layout()
    return _fig_to_png(fig)


def _simulate(state, data, coeffs, ctype, ctx: dict) -> tuple:
    """Запускает симуляцию по контексту (общему для всех методов)."""
    mt = state.get("model_type", "fopdt")
    is_ipdt = mt == "ipdt"
    return simulator.simulate_closed_loop(
        (state.get("K", 0.0) if not is_ipdt else 0.0),
        (state.get("T", 0.0) if not is_ipdt else 0.0),
        state.get("tau", 0.0), ctype,
        coeffs["Kp"], coeffs.get("Ti"), coeffs.get("Td"),
        dt_sim=ctx.get("dt_sim", max(data.dt / 5.0, 0.01)),
        sim_time=ctx.get("sim_time",
                         min(max(2.0 * float(data.time[-1] - data.time[0]),
                                 60.0), 3600.0)),
        sp_array=data.sp,
        sp_start=ctx.get("sp_start"), sp_target=ctx.get("sp_target"),
        cv_clip=ctx.get("cv_clip", True), cv_min=ctx.get("cv_min", 0.0),
        cv_max=ctx.get("cv_max", 100.0),
        pv0=ctx.get("pv0"), cv0=ctx.get("cv0"),
        model_type=mt, Ka=state.get("Ka"), balance=state.get("balance"))


def _plot_sim(sim, metrics=None, title: str = "") -> io.BytesIO:
    fig, ax = plt.subplots(figsize=(7.5, 3.0))
    ax.plot(sim[0], sim[1], label="SP", lw=1)
    ax.plot(sim[0], sim[2], label="PV (модель)", lw=1.4)
    ax.set_xlabel("Время, с"); ax.legend(); ax.grid(alpha=0.3)
    if title:
        ax.set_title(title)
    fig.tight_layout()
    return _fig_to_png(fig)


def _fmt(value, ndigits: int) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):.{ndigits}f}"
    except (TypeError, ValueError):
        return str(value)


def _comparison_table(methods: list) -> Table:
    """Сводная таблица коэффициентов и качества по всем методам."""
    head = ["Метод", "Kp", "Ti, с", "Td, с", "Перерег., %",
            "Время регул., с", "IAE"]
    rows = [head]
    for m in methods:
        if not m.get("coeffs"):
            name = METHOD_NAMES.get(m["method"], m["method"])
            rows.append([name, "—", "—", "—", "—", "—", "—"])
            continue
        c = m["coeffs"]; mm = m.get("metrics") or {}
        rows.append([
            METHOD_NAMES.get(m["method"], m["method"]),
            _fmt(c.get("Kp"), 4),
            _fmt(c.get("Ti"), 2) if c.get("Ti") is not None else "—",
            _fmt(c.get("Td"), 2) if c.get("Td") is not None else "—",
            _fmt(mm.get("overshoot"), 1),
            _fmt(mm.get("settling_time"), 1),
            _fmt(mm.get("iae"), 3),
        ])
    t = Table(rows, repeatRows=1, hAlign="LEFT")
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), "#2c3e50"),
        ("TEXTCOLOR", (0, 0), (-1, 0), "#ffffff"),
        ("GRID", (0, 0), (-1, -1), 0.5, "#999"),
        ("FONTNAME", (0, 0), (-1, -1), "DejaVuSans"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         ["#ffffff", "#f0f7f5"]),
    ]))
    return t


def build_pdf(state: dict, data) -> io.BytesIO:
    """Собирает PDF в память и возвращает BytesIO."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=2 * cm, rightMargin=2 * cm,
                            topMargin=2 * cm, bottomMargin=2 * cm)

    _register_fonts()
    styles = getSampleStyleSheet()
    styles["Normal"].fontName = "DejaVuSans"
    styles["Heading2"].fontName = "DejaVuSans-Bold"
    title_style = ParagraphStyle("RuTitle", parent=styles["Title"],
                                 fontName="DejaVuSans-Bold")

    # Заголовок и логотип на одной строке; имя файла — под заголовком
    logo_path = os.path.join(BASE_DIR, "static", "images", "manky.png")
    _logo = Image(logo_path, width=2.4 * cm, height=2.4 * cm)
    header = Table(
        [[Paragraph(RU_TITLE, title_style), _logo],
         [Paragraph(f"Исходный файл: "
                    f"<b>{state.get('upload_name', '—')}</b>",
                    styles["Normal"]), None]],
        colWidths=[13.5 * cm, 4.5 * cm],
        rowHeights=[1.3 * cm, 1.1 * cm],
        style=TableStyle([
            ("VALIGN", (0, 0), (0, 0), "MIDDLE"),
            ("VALIGN", (1, 0), (1, 0), "MIDDLE"),
            ("ALIGN", (0, 0), (-1, -1), "LEFT"),
            ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ]),
        hAlign="LEFT")

    story = [
        header,
        Spacer(1, 18),
    ]

    # Таблица параметров модели
    if state.get("model_type") == "ipdt":
        model_rows = [
            ["Параметр модели", "Значение"],
            ["Ka (интегральное усиление), 1/с", f"{state['Ka']:.4f}"],
            ["τ (запаздывание), с", f"{state['tau']:.2f}"],
            ["Тип модели", "IPDT — интегрирующее звено с запаздыванием"],
        ]
        model_head = "Параметры идентифицированной модели IPDT"
    else:
        model_rows = [
            ["Параметр модели", "Значение"],
            ["K (коэффициент усиления)", f"{state['K']:.4f}"],
            ["T (постоянная времени), с", f"{state['T']:.2f}"],
            ["τ (запаздывание), с", f"{state['tau']:.2f}"],
        ]
        model_head = "Параметры идентифицированной модели FOPDT"
    if state.get("Ku"):
        model_rows += [["Ku (критическое усиление)", f"{state['Ku']:.3f}"],
                       ["Tu (период автоколебаний), с", f"{state['Tu']:.2f}"]]
    mt = Table(model_rows, hAlign="LEFT")
    mt.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), "#e8e8f5"),
        ("GRID", (0, 0), (-1, -1), 0.5, "#999"),
        ("FONTNAME", (0, 0), (-1, -1), "DejaVuSans"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
    ]))
    story += [Paragraph(model_head, styles["Heading2"]), mt, Spacer(1, 14)]

    # Коэффициенты регулятора (выбранный метод)
    coeffs = state["coeffs"]
    method_name = METHOD_NAMES.get(state.get("tuning_method"), "—")
    c_rows = [
        ["Коэффициент", "Значение"],
        ["Kp", f"{coeffs['Kp']:.4f}"],
        ["Ti, с", f"{coeffs['Ti']:.2f}" if coeffs.get("Ti") else "—"],
        ["Td, с", f"{coeffs['Td']:.2f}" if coeffs.get("Td") else "—"],
        ["Тип регулятора", state.get("ctype", "PID")],
        ["Метод настройки", method_name],
    ]
    ct = Table(c_rows, hAlign="LEFT")
    ct.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), "#e8f5e8"),
        ("GRID", (0, 0), (-1, -1), 0.5, "#999"),
        ("FONTNAME", (0, 0), (-1, -1), "DejaVuSans"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
    ]))
    story += [Paragraph("Коэффициенты ПИД-регулятора (выбранный метод)",
                        styles["Heading2"]), ct, PageBreak()]

    # Контекст симуляции и список методов (для таблицы сравнения и графиков)
    sim_all = state.get("sim_all")
    ctype = (sim_all.get("ctype") if sim_all else None) \
        or state.get("ctype", "PID")
    ctx = (sim_all.get("ctx") if sim_all else None) or {}
    methods = list(sim_all["methods"]) if (sim_all and sim_all.get("methods")) \
        else []

    # Графики исходных данных и модели
    story += [Paragraph("Исходные данные процесса", styles["Heading2"]),
              Image(_plot_raw(data), width=16 * cm, height=6.8 * cm),
              Spacer(1, 10),
              Paragraph("Сравнение данных и модели", styles["Heading2"]),
              Image(_plot_model(state, data), width=16 * cm, height=6.4 * cm)]

    # Сводная таблица сравнения — сразу после сравнения данных и модели
    if methods:
        story.append(Spacer(1, 10))
        story.append(Paragraph("Сравнение результатов симуляции",
                               styles["Heading2"]))
        story.append(_comparison_table(methods))
    story.append(PageBreak())

    # Результаты симуляции по всем методам (графики)
    if methods:
        story.append(Paragraph("Результаты симуляции по методам настройки",
                               styles["Heading2"]))
        for m in methods:
            name = METHOD_NAMES.get(m["method"], m["method"])
            story.append(Spacer(1, 6))
            story.append(Paragraph(f"<b>{name}</b>", styles["Normal"]))
            if m.get("error"):
                story.append(Paragraph(f"Ошибка расчёта: {m['error']}",
                                       styles["Normal"]))
                continue
            if m.get("coeffs"):
                sim = _simulate(state, data, m["coeffs"], ctype, ctx)
                mm = m.get("metrics")
                title_parts = []
                if mm:
                    overshoot = mm.get("overshoot")
                    settling = mm.get("settling_time")
                    iae = mm.get("iae")
                    if overshoot is not None:
                        title_parts.append(f"Перерегулирование: {overshoot} %")
                    if settling is not None:
                        title_parts.append(f"время регулирования: "
                                           f"{_fmt(settling, 1)} с")
                    if iae is not None:
                        title_parts.append(f"IAE: {_fmt(iae, 3)}")
                story.append(Image(_plot_sim(sim, mm, "; ".join(title_parts)),
                                   width=15.5 * cm, height=5.8 * cm))
                story.append(Spacer(1, 4))
    else:
        # Фоллбэк: один выбранный метод
        story += [Paragraph("Симуляция замкнутой системы", styles["Heading2"]),
                  Image(_plot_sim(_simulate(state, data, coeffs, ctype, ctx)),
                        width=16 * cm, height=6.4 * cm)]

    doc.build(story)
    buf.seek(0)
    return buf
