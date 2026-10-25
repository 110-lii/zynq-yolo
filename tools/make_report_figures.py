#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 report/figures/ 下的框图与对比图（纯 matplotlib，确定性输出）。

用法::

    python tools/make_report_figures.py

输出：
    report/figures/system_arch.png
    report/figures/software_pipeline.png
    report/figures/optimization.png

数字只来自仓库内已落盘的实测记录；改数字前先改对应日志/JSON。
"""
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "report" / "figures"
OUT.mkdir(parents=True, exist_ok=True)

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\NotoSansSC-VF.ttf",
]
for cand in FONT_CANDIDATES:
    if Path(cand).is_file():
        font_manager.fontManager.addfont(cand)
        plt.rcParams["font.family"] = font_manager.FontProperties(fname=cand).get_name()
        break
plt.rcParams["axes.unicode_minus"] = False

PS = "#dbeafe"
PS_EDGE = "#2563eb"
PL = "#dcfce7"
PL_EDGE = "#16a34a"
PC = "#f3f4f6"
PC_EDGE = "#4b5563"
OUT_C = "#ffedd5"
OUT_EDGE = "#ea580c"


def box(ax, x, y, w, h, text, fc, ec, fs=10, bold=False):
    p = FancyBboxPatch(
        (x - w / 2, y - h / 2), w, h,
        boxstyle="round,pad=0.012,rounding_size=0.02",
        linewidth=1.4, facecolor=fc, edgecolor=ec, zorder=2,
    )
    ax.add_patch(p)
    ax.text(x, y, text, ha="center", va="center", fontsize=fs,
            fontweight="bold" if bold else "normal", zorder=3, wrap=True)


def arrow(ax, x1, y1, x2, y2, color="#374151", lw=1.6, style="-|>"):
    ax.add_patch(FancyArrowPatch(
        (x1, y1), (x2, y2), arrowstyle=style, mutation_scale=14,
        linewidth=lw, color=color, zorder=1, shrinkA=2, shrinkB=2,
    ))


def fig_system_arch():
    fig, ax = plt.subplots(figsize=(11.5, 8.6))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    # lane backgrounds
    ax.add_patch(FancyBboxPatch((0.27, 0.03), 0.46, 0.94,
                                boxstyle="round,pad=0.008,rounding_size=0.02",
                                facecolor="#f8fafc", edgecolor="#cbd5e1", lw=1, zorder=0))
    ax.text(0.50, 0.975, "Zynq-7020", ha="center", va="center", fontsize=13, fontweight="bold")

    rows = [
        (0.895, "PC：视频文件 / 推流\n（tools/stream_sender.py）", PC, PC_EDGE, "PC"),
        (0.775, "PS：以太网接收 → 帧重组 → DDR 帧缓冲", PS, PS_EDGE, "PS"),
        (0.645, "PL：preproc\nBGR888 → 3×320×320 RGB 平面\n最近邻，II=1，18.8 ms/帧", PL, PL_EDGE, "PL"),
        (0.505, "PS：ncnn YOLOv8n-0.25（单类 person）\n320×320，0.386 GMAC，0.95–1.0 s/帧", PS, PS_EDGE, "PS"),
        (0.365, "PS：SORT 跟踪 + 机位运动补偿\n帧间位移中位 0.2 px，34 个 ID", PS, PS_EDGE, "PS"),
        (0.225, "PS：球链路\n白块候选 87.3% → C 分类器 8 ms → 轨迹确认 80%", PS, PS_EDGE, "PS"),
        (0.095, "PS：单应 → 球场坐标 → 事件\n出界 / 进球 / 球权", PS, PS_EDGE, "PS"),
    ]
    for y, text, fc, ec, lane in rows:
        box(ax, 0.50, y, 0.40, 0.095, text, fc, ec, fs=9.2)
        ax.text(0.245, y, lane, ha="center", va="center", fontsize=9,
                fontweight="bold", color=ec)

    # vertical arrows
    ys = [r[0] for r in rows]
    for y1, y2 in zip(ys[:-1], ys[1:]):
        arrow(ax, 0.50, y1 - 0.048, 0.50, y2 + 0.048)

    # outputs on the right
    box(ax, 0.845, 0.155, 0.16, 0.075, "以太网\n推给 PC", OUT_C, OUT_EDGE, fs=9)
    box(ax, 0.845, 0.065, 0.16, 0.075, "SD 卡\n同构 JSON", OUT_C, OUT_EDGE, fs=9)
    arrow(ax, 0.70, 0.095, 0.765, 0.13, color=OUT_EDGE)
    arrow(ax, 0.70, 0.09, 0.765, 0.065, color=OUT_EDGE)

    ax.text(0.50, 0.015, "PL 只承担像素级、规则、可流水化的预处理；检测/跟踪/几何/事件在 PS。",
            ha="center", va="center", fontsize=8.5, color="#475569")
    fig.savefig(OUT / "system_arch.png", dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def fig_software_pipeline():
    fig, ax = plt.subplots(figsize=(12.5, 6.6))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    # main horizontal chain
    stages = [
        (0.085, "帧输入\n每帧", PC, PC_EDGE),
        (0.255, "预处理\nPL / cv2 回退", PL, PL_EDGE),
        (0.425, "检测\n每 every 帧", PS, PS_EDGE),
        (0.595, "跟踪\n每帧预测", PS, PS_EDGE),
        (0.765, "坐标 + 事件\n单应 / 越线 / 球权", PS, PS_EDGE),
        (0.925, "输出\n以太网 + SD", OUT_C, OUT_EDGE),
    ]
    for x, text, fc, ec in stages:
        box(ax, x, 0.66, 0.135, 0.16, text, fc, ec, fs=9.2)
    for (x1, _, _, _), (x2, _, _, _) in zip(stages[:-1], stages[1:]):
        arrow(ax, x1 + 0.068, 0.66, x2 - 0.068, 0.66)

    # ball branch
    box(ax, 0.425, 0.30, 0.20, 0.14,
        "球链路（可独立节奏）\n白块/圆度候选 → C 分类器 → 轨迹确认", PS, PS_EDGE, fs=8.8)
    arrow(ax, 0.425, 0.58, 0.425, 0.375, color="#2563eb", lw=1.3)
    arrow(ax, 0.525, 0.30, 0.765, 0.58, color="#2563eb", lw=1.3, style="-|>")

    # camera compensation
    box(ax, 0.595, 0.30, 0.17, 0.10,
        "机位运动补偿\nphaseCorrelate", PS, PS_EDGE, fs=8.6)
    arrow(ax, 0.595, 0.58, 0.595, 0.355, color="#2563eb", lw=1.2, style="-|>")

    # notes
    ax.text(0.5, 0.87, "PS 侧软件流水线", ha="center", va="center",
            fontsize=14, fontweight="bold")
    ax.text(0.5, 0.10,
            "检测按 --every 稀疏执行；跟踪每帧预测并用相邻帧位移补偿。"
            "球链路用 --ball-every 控制，C 分类器约 8 ms/候选。",
            ha="center", va="center", fontsize=8.8, color="#475569")
    fig.savefig(OUT / "software_pipeline.png", dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def fig_optimization():
    labels = ["conv_engine\n字节访存", "conv_engine\n32-bit 字访存", "PS ncnn\nYOLOv8n-0.25"]
    values = [20.8, 9.63, 0.95]
    colors = ["#fca5a5", "#fdba74", "#86efac"]
    fig, ax = plt.subplots(figsize=(8.6, 5.0))
    bars = ax.bar(labels, values, color=colors, edgecolor="#374151", linewidth=1.0)
    ax.set_yscale("log")
    ax.set_ylabel("单帧耗时（秒，对数坐标）", fontsize=10)
    ax.set_title("检测路线单帧耗时对比（416/YOLOv5n 整网 → 320/YOLOv8n-0.25）",
                 fontsize=11.5, fontweight="bold")
    for b, v in zip(bars, values):
        ax.text(b.get_x() + b.get_width() / 2, v * 1.12, f"{v:g} s",
                ha="center", va="bottom", fontsize=10, fontweight="bold")
    ax.set_ylim(0.5, 40)
    ax.grid(axis="y", which="both", linestyle=":", color="#cbd5e1", linewidth=0.8)
    ax.spines[["top", "right"]].set_visible(False)
    ax.text(0.5, -0.20,
            "出处：board/logs/board_stream_new_20261022.md；ai/sessions/009_ps_pipeline_pl_preproc.md",
            transform=ax.transAxes, ha="center", va="top", fontsize=7.8, color="#64748b")
    fig.savefig(OUT / "optimization.png", dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def main():
    fig_system_arch()
    fig_software_pipeline()
    fig_optimization()
    for name in ("system_arch.png", "software_pipeline.png", "optimization.png"):
        p = OUT / name
        print(f"WROTE {p.relative_to(ROOT).as_posix()} {p.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
