from __future__ import annotations

from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


mpl.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["DejaVu Sans", "Arial", "Helvetica", "sans-serif"],
    "font.size": 7,
    "axes.linewidth": 0.7,
    "axes.spines.right": False,
    "axes.spines.top": False,
    "legend.frameon": False,
    "pdf.fonttype": 42,
    "svg.fonttype": "none",
})

WEAK = "#9AA3AD"
RICH = "#2E6F9E"
PAIR = "#CDD3D9"
FAIL = "#B84A4A"


def _paired_panel(axis, frame: pd.DataFrame, metric: str, ylabel: str, panel: str) -> None:
    wide = frame.pivot(index="seed", columns="condition", values=metric)
    for _, row in wide.iterrows():
        axis.plot([0, 1], [row["weak"], row["rich"]], color=PAIR, linewidth=0.6, zorder=1)
    axis.scatter(np.zeros(len(wide)), wide["weak"], color=WEAK, s=13, alpha=0.75,
                 edgecolors="white", linewidths=0.25, zorder=2)
    axis.scatter(np.ones(len(wide)), wide["rich"], color=RICH, s=13, alpha=0.75,
                 edgecolors="white", linewidths=0.25, zorder=2)
    for position, condition, color in [(0, "weak", WEAK), (1, "rich", RICH)]:
        values = wide[condition]
        median = float(values.median())
        q25, q75 = np.percentile(values, [25, 75])
        axis.errorbar(position, median, yerr=[[median - q25], [q75 - median]],
                      fmt="D", color=color, markeredgecolor="black", markeredgewidth=0.4,
                      markersize=5, capsize=3, linewidth=1.3, zorder=3)
    axis.set_xticks([0, 1], ["Weak", "Rich"])
    axis.set_xlim(-0.35, 1.35)
    axis.set_ylabel(ylabel)
    axis.grid(axis="y", color="#E0E4E8", linewidth=0.5)
    axis.text(-0.17, 1.04, panel, transform=axis.transAxes, fontsize=9, fontweight="bold")


def make_figure(project_root: Path) -> tuple[Path, ...]:
    frame = pd.read_csv(
        project_root / "outputs/tables/excitation_identifiability_repetitions_v1.csv"
    )
    if len(frame) != 60 or frame["seed"].nunique() != 30:
        raise ValueError("The frozen paired figure requires 30 seeds and 60 rows")
    fig, axes = plt.subplots(1, 3, figsize=(7.1, 2.65), constrained_layout=True)

    _paired_panel(
        axes[0], frame, "condition_number", "Standardized condition number", "a",
    )
    axes[0].set_title("Conditioning gate failed", loc="left", fontweight="bold")
    lower, upper = axes[0].get_ylim()
    span = upper - lower
    axes[0].set_ylim(lower - 0.15 * span, upper)
    axes[0].text(0.5, lower - 0.10 * span, "0/30 pairs improved",
                 ha="center", color=FAIL, fontweight="bold")

    _paired_panel(
        axes[1], frame, "normalized_parameter_rmse", "Normalized parameter RMSE", "b",
    )
    axes[1].set_title("Parameter recovery", loc="left", fontweight="bold")
    lower, upper = axes[1].get_ylim()
    span = upper - lower
    axes[1].set_ylim(lower, upper + 0.15 * span)
    axes[1].text(0.5, upper + 0.08 * span, "30/30 pairs improved",
                 ha="center", color=RICH, fontweight="bold")

    _paired_panel(
        axes[2], frame, "probe_rmse_mean", "Common-probe RMSE (m³ m⁻³)", "c",
    )
    axes[2].set_title("Out-of-design prediction", loc="left", fontweight="bold")
    lower, upper = axes[2].get_ylim()
    span = upper - lower
    axes[2].set_ylim(lower, upper + 0.15 * span)
    axes[2].text(0.5, upper + 0.08 * span, "26/30 pairs improved",
                 ha="center", color=RICH, fontweight="bold")

    fig.suptitle(
        "Rich excitation improves recovery but fails the frozen conditioning gate\n"
        "Known-parameter synthetic study; diamonds show median and interquartile range",
        fontsize=9.5, fontweight="bold",
    )
    output_base = project_root / "outputs/figures/excitation_identifiability_v1"
    output_base.parent.mkdir(parents=True, exist_ok=True)
    svg_path = output_base.with_suffix(".svg")
    pdf_path = output_base.with_suffix(".pdf")
    tiff_path = output_base.with_suffix(".tiff")
    png_path = output_base.with_suffix(".png")
    fig.savefig(svg_path, bbox_inches="tight")
    fig.savefig(pdf_path, bbox_inches="tight")
    fig.savefig(tiff_path, dpi=600, bbox_inches="tight")
    fig.savefig(png_path, dpi=300, bbox_inches="tight")
    plt.close(fig)
    return svg_path, pdf_path, tiff_path, png_path


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    for output in make_figure(root):
        print(output)
