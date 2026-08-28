"""
PDF-отчёт (reportlab): титульная часть, таблица параметров и коэффициентов,
графики, встроенные как PNG (рендер через Matplotlib).
"""
from __future__ import annotations

import io

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from flask import session  # noqa: F401 (state передаётся явно)
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (Image, PageBreak, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle)

from core import pid_tuning, simulator

RU_TITLE = "Отчёт по настройке ПИД-регулятора"

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
    from core.identification import fopdt_response
    model_pv = fopdt_response(data.time, data.cv,
                              state["K"], state["T"], state["tau"])
    fig, ax = plt.subplots(figsize=(7.5, 3.0))
    ax.plot(data.time, data.pv, label="PV (данные)", lw=1)
    ax.plot(data.time, model_pv, "--", label="Модель FOPDT", lw=1.4)
    ax.set_xlabel("Время, с"); ax.legend(); ax.grid(alpha=0.3)
    fig.tight_layout()
    return _fig_to_png(fig)


def _plot_sim(state, data) -> io.BytesIO:
    coeffs = state["coeffs"]; ctype = state.get("ctype", "PID")
    sim_time = min(max(2.0 * float(data.time[-1] - data.time[0]), 60.0), 3600.0)
    sim = simulator.simulate_closed_loop(
        state["K"], state["T"], state["tau"], ctype,
        coeffs["Kp"], coeffs.get("Ti"), coeffs.get("Td"),
        dt_sim=max(data.dt / 5.0, 0.01), sim_time=sim_time,
        sp_array=data.sp)
    metrics = simulator.quality_metrics(sim[0], sim[1], sim[2])
    fig, ax = plt.subplots(figsize=(7.5, 3.0))
    ax.plot(sim[0], sim[1], label="SP", lw=1)
    ax.plot(sim[0], sim[2], label="PV (модель)", lw=1.4)
    ax.set_xlabel("Время, с"); ax.legend(); ax.grid(alpha=0.3)
    ax.set_title(f"Перерегулирование: {metrics['overshoot']} %, "
                 f"время регулирования: {metrics['settling_time']} с")
    fig.tight_layout()
    return _fig_to_png(fig)


def build_pdf(state: dict, data) -> io.BytesIO:
    """Собирает PDF в память и возвращает BytesIO."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=2 * cm, rightMargin=2 * cm,
                            topMargin=2 * cm, bottomMargin=2 * cm)

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("RuTitle", parent=styles["Title"],
                                 fontName="Helvetica-Bold")

    story = [
        Paragraph(RU_TITLE, title_style),
        Spacer(1, 12),
        Paragraph(f"Исходный файл: <b>{state.get('upload_name', '—')}</b>",
                  styles["Normal"]),
        Spacer(1, 18),
    ]

    # Таблица параметров модели
    model_rows = [
        ["Параметр модели", "Значение"],
        ["K (коэффициент усиления)", f"{state['K']:.4f}"],
        ["T (постоянная времени), с", f"{state['T']:.2f}"],
        ["τ (запаздывание), с", f"{state['tau']:.2f}"],
    ]
    if state.get("Ku"):
        model_rows += [["Ku (критическое усиление)", f"{state['Ku']:.3f}"],
                       ["Tu (период автоколебаний), с", f"{state['Tu']:.2f}"]]
    mt = Table(model_rows, hAlign="LEFT")
    mt.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), "#e8e8f5"),
        ("GRID", (0, 0), (-1, -1), 0.5, "#999"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
    ]))
    story += [Paragraph("Параметры идентифицированной модели FOPDT",
                        styles["Heading2"]), mt, Spacer(1, 14)]

    # Коэффициенты регулятора
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
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
    ]))
    story += [Paragraph("Коэффициенты ПИД-регулятора", styles["Heading2"]),
              ct, PageBreak()]

    # Графики
    story += [Paragraph("Исходные данные процесса", styles["Heading2"]),
              Image(_plot_raw(data), width=16 * cm, height=6.8 * cm),
              Spacer(1, 10),
              Paragraph("Сравнение данных и модели FOPDT", styles["Heading2"]),
              Image(_plot_model(state, data), width=16 * cm, height=6.4 * cm),
              PageBreak(),
              Paragraph("Симуляция замкнутой системы", styles["Heading2"]),
              Image(_plot_sim(state, data), width=16 * cm, height=6.4 * cm)]

    doc.build(story)
    buf.seek(0)
    return buf
