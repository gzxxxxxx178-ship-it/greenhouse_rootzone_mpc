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

CONDITIONS = ["weak", "moderate", "rich"]
LABELS = ["Weak", "Moderate", "Rich"]
COLORS = {"weak": "#9AA3AD", "moderate": "#D58B3A", "rich": "#2E6F9E"}
PAIR = "#D7DCE1"


def _ordered_panel(axis, frame: pd.DataFrame, metric: str, ylabel: str, panel: str) -> None:
    wide = frame.pivot(index="seed", columns="condition", values=metric)
    for _, row in wide.iterrows():
        axis.plot(range(3), row[CONDITIONS], color=PAIR, linewidth=0.55, zorder=1)
    for position, condition in enumerate(CONDITIONS):
        values = wide[condition]
        axis.scatter(
            np.full(len(values), position), values, color=COLORS[condition], s=11,
            alpha=0.65, edgecolors="white", linewidths=0.2, zorder=2,
        )
        median = float(values.median())
        q25, q75 = np.percentile(values, [25, 75])
        axis.errorbar(
            position, median, yerr=[[median - q25], [q75 - median]], fmt="D",
            color=COLORS[condition], markeredgecolor="black", markeredgewidth=0.4,
            markersize=4.7, capsize=3, linewidth=1.2, zorder=3,
        )
    axis.set_xticks(range(3), LABELS)
    axis.set_xlim(-0.35, 2.35)
    axis.set_ylabel(ylabel)
    axis.grid(axis="y", color="#E0E4E8", linewidth=0.5)
    axis.text(-0.14, 1.03, panel, transform=axis.transAxes, fontsize=9, fontweight="bold")


def make_figure(project_root: Path) -> tuple[Path, ...]:
    frame = pd.read_csv(
        project_root / "outputs/tables/information_identifiability_repetitions_v1.csv"
    )
    if len(frame) != 90 or frame["seed"].nunique() != 30:
        raise ValueError("The frozen figure requires 30 seeds and 90 rows")
    fig, axes = plt.subplots(2, 2, figsize=(7.1, 5.25), constrained_layout=True)

    _ordered_panel(
        axes[0, 0], frame, "information_min_singular_snr",
        "Minimum singular value (signal/noise)", "a",
    )
    axes[0, 0].set_title("Amplitude-sensitive information", loc="left", fontweight="bold")

    _ordered_panel(
        axes[0, 1], frame, "predicted_normalized_se_rms",
        "Predicted normalized SE (RMS)", "b",
    )
    axes[0, 1].set_title("Analytical uncertainty", loc="left", fontweight="bold")

    _ordered_panel(
        axes[1, 0], frame, "normalized_parameter_rmse",
        "Normalized parameter RMSE", "c",
    )
    axes[1, 0].set_title("Actual recovery error", loc="left", fontweight="bold")

    for condition in CONDITIONS:
        selected = frame[frame["condition"] == condition]
        axes[1, 1].scatter(
            selected["predicted_normalized_se_rms"],
            selected["normalized_parameter_rmse"],
            label=condition.capitalize(), color=COLORS[condition], s=18, alpha=0.75,
            edgecolors="white", linewidths=0.25,
        )
    axes[1, 1].set_xlabel("Predicted normalized SE (RMS)")
    axes[1, 1].set_ylabel("Normalized parameter RMSE")
    axes[1, 1].set_title("Uncertainty tracks error", loc="left", fontweight="bold")
    axes[1, 1].grid(color="#E0E4E8", linewidth=0.5)
    axes[1, 1].legend(title="Excitation", loc="upper left")
    axes[1, 1].text(0.97, 0.06, "Spearman ρ = 0.826", transform=axes[1, 1].transAxes,
                    ha="right", fontweight="bold", color="#263746")
    axes[1, 1].text(-0.14, 1.03, "d", transform=axes[1, 1].transAxes,
                    fontsize=9, fontweight="bold")

    fig.suptitle(
        "Input information predicts parameter recovery across three excitation levels\n"
        "Known-structure synthetic study; n = 30 paired noise seeds",
        fontsize=9.5, fontweight="bold",
    )
    output_base = project_root / "outputs/figures/information_identifiability_v1"
    output_base.parent.mkdir(parents=True, exist_ok=True)
    paths = tuple(output_base.with_suffix(suffix) for suffix in [".svg", ".pdf", ".tiff", ".png"])
    fig.savefig(paths[0], bbox_inches="tight")
    fig.savefig(paths[1], bbox_inches="tight")
    fig.savefig(paths[2], dpi=600, bbox_inches="tight")
    fig.savefig(paths[3], dpi=300, bbox_inches="tight")
    plt.close(fig)
    return paths


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    for output in make_figure(root):
        print(output)
