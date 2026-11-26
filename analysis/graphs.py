"""Academic-Quality Matplotlib Publication Figure Generator (PNG & Vector PDF)."""

from pathlib import Path
from typing import Dict, List, Optional
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from analysis.preprocessing import BenchmarkDataPreprocessor


class AcademicGraphGenerator:
    """Generates IEEE publication-grade comparative graphs from experimental benchmark data."""

    FHE_COLOR = "#1f4e78"
    FHE_WAN_COLOR = "#4b8bbe"
    SMPC_COLOR = "#b8332a"
    SMPC_WAN_COLOR = "#e06d53"
    PLAINTEXT_COLOR = "#4d7c5b"

    def __init__(self, results_dir: Optional[str | Path] = None):
        self.preprocessor = BenchmarkDataPreprocessor(results_dir)
        self.results_dir = self.preprocessor.results_dir
        self.graphs_dir = self.results_dir / "graphs"
        self.graphs_dir.mkdir(parents=True, exist_ok=True)

        plt.rcParams.update({
            "font.family": "serif",
            "font.size": 10,
            "axes.titlesize": 11,
            "axes.labelsize": 10,
            "legend.fontsize": 8.5,
            "xtick.labelsize": 9,
            "ytick.labelsize": 9,
            "figure.dpi": 300,
            "savefig.dpi": 300,
            "axes.grid": True,
            "grid.alpha": 0.3,
            "grid.linestyle": "--",
        })

    def _save_fig(self, fig: plt.Figure, base_name: str) -> Dict[str, Path]:
        png_path = self.graphs_dir / f"{base_name}.png"
        pdf_path = self.graphs_dir / f"{base_name}.pdf"
        fig.savefig(png_path, bbox_inches="tight", dpi=300)
        fig.savefig(pdf_path, bbox_inches="tight")
        plt.close(fig)
        return {"png": png_path, "pdf": pdf_path}

    def generate_graph1_execution_time_vs_dataset_size(
        self, df: pd.DataFrame
    ) -> Dict[str, Path]:
        """Graph 1: Execution Time vs. Dataset Size across Local LAN and WAN profiles."""
        fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.5), sharey=False)
        workloads = [
            ("aggregation", "Workload 1: Multi-Center Cohort Aggregation"),
            ("risk_multiplication", "Workload 2: Collaborative Disease Severity Risk"),
        ]

        for ax, (wl_key, wl_title) in zip(axes, workloads):
            wl_df = df[df["workload"] == wl_key]

            series_specs = [
                ("FHE", "local", "high", "FHE (Local 1 ms, 1 Gbps)", self.FHE_COLOR, "-", "o"),
                ("SMPC", "local", "high", "SMPC (Local 1 ms, 1 Gbps)", self.SMPC_COLOR, "-", "s"),
                ("FHE", "moderate_wan", "restricted", "FHE (WAN 50 ms, 10 Mbps)", self.FHE_WAN_COLOR, "--", "^"),
                ("SMPC", "moderate_wan", "restricted", "SMPC (WAN 50 ms, 10 Mbps)", self.SMPC_WAN_COLOR, "--", "D"),
            ]

            for tech, lat_p, bw_p, label, color, ls, marker in series_specs:
                sub = wl_df[
                    (wl_df["technology"] == tech)
                    & (wl_df["latency_profile"] == lat_p)
                    & (wl_df["bandwidth_profile"] == bw_p)
                ].sort_values("num_transactions")
                if sub.empty:
                    continue
                x = sub["num_transactions"].to_numpy(dtype=float)
                y = sub["total_execution_time_s_mean"].to_numpy(dtype=float)
                yerr = sub["total_execution_time_s_std"].to_numpy(dtype=float)
                ax.errorbar(
                    x,
                    y,
                    yerr=yerr,
                    label=label,
                    color=color,
                    linestyle=ls,
                    marker=marker,
                    markersize=5.5,
                    linewidth=1.8,
                    capsize=3,
                )

            ax.set_title(wl_title, fontweight="bold")
            ax.set_xlabel("Number of Patient Records (N)")
            ax.set_ylabel("Total Execution Time (seconds)")
            ax.set_xscale("log")
            ax.set_yscale("log")
            if ax.get_legend_handles_labels()[0]:
                ax.legend(loc="best", frameon=True)

        fig.suptitle(
            "Graph 1: Execution Time vs. Dataset Size (OpenFHE Threshold BFV vs. MP-SPDZ 3-Party)",
            fontsize=12,
            fontweight="bold",
            y=1.03,
        )
        fig.tight_layout()
        return self._save_fig(fig, "graph1_execution_time_vs_dataset_size")

    def generate_graph2_network_traffic_vs_latency(
        self, df: pd.DataFrame
    ) -> Dict[str, Path]:
        """Graph 2: Network Traffic (MB) vs. Configured Network Latency (ms)."""
        fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.4), sharey=False)
        workloads = [
            ("aggregation", "Workload 1: Multi-Center Cohort Aggregation"),
            ("risk_multiplication", "Workload 2: Collaborative Disease Severity Risk"),
        ]

        ref_ds = "medium" if "medium" in df["dataset_profile"].values else df["dataset_profile"].iloc[0]

        for ax, (wl_key, wl_title) in zip(axes, workloads):
            wl_df = df[
                (df["workload"] == wl_key)
                & (df["dataset_profile"] == ref_ds)
                & (df["bandwidth_profile"] == "high")
            ]

            for tech, color, marker in [
                ("FHE", self.FHE_COLOR, "o"),
                ("SMPC", self.SMPC_COLOR, "s"),
            ]:
                sub = wl_df[wl_df["technology"] == tech].sort_values("configured_latency_ms")
                if sub.empty:
                    continue
                x = sub["configured_latency_ms"].to_numpy(dtype=float)
                y = sub["measured_network_traffic_mb_mean"].to_numpy(dtype=float)
                yerr = sub["measured_network_traffic_mb_std"].to_numpy(dtype=float)
                ax.errorbar(
                    x,
                    y,
                    yerr=yerr,
                    label=f"{tech} Measured Interface Traffic",
                    color=color,
                    marker=marker,
                    markersize=6,
                    linewidth=2.0,
                    capsize=3,
                )

            ax.set_title(f"{wl_title} (N={int(wl_df['num_transactions'].iloc[0]) if not wl_df.empty else 1000})", fontweight="bold")
            ax.set_xlabel("Configured One-Way Network Latency (ms)")
            ax.set_ylabel("Aggregate Network Traffic (MB)")
            ax.set_yscale("log")
            if ax.get_legend_handles_labels()[0]:
                ax.legend(loc="best", frameon=True)

        fig.suptitle(
            "Graph 2: Aggregate Network Traffic vs. Network Latency",
            fontsize=12,
            fontweight="bold",
            y=1.03,
        )
        fig.tight_layout()
        return self._save_fig(fig, "graph2_network_traffic_vs_latency")

    def generate_graph3_execution_time_vs_latency(
        self, df: pd.DataFrame
    ) -> Dict[str, Path]:
        """Graph 3: Total Execution Time vs. Network Latency with Crossover Annotation."""
        fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.5), sharey=False)
        workloads = [
            ("aggregation", "Workload 1: Multi-Center Cohort Aggregation"),
            ("risk_multiplication", "Workload 2: Collaborative Disease Severity Risk"),
        ]
        ref_ds = "medium" if "medium" in df["dataset_profile"].values else df["dataset_profile"].iloc[0]

        for ax, (wl_key, wl_title) in zip(axes, workloads):
            wl_df = df[
                (df["workload"] == wl_key)
                & (df["dataset_profile"] == ref_ds)
                & (df["bandwidth_profile"] == "high")
            ]

            fhe_sub = wl_df[wl_df["technology"] == "FHE"].sort_values("configured_latency_ms")
            smpc_sub = wl_df[wl_df["technology"] == "SMPC"].sort_values("configured_latency_ms")

            for sub, tech, color, marker in [
                (fhe_sub, "FHE (O(1) Rounds)", self.FHE_COLOR, "o"),
                (smpc_sub, "SMPC (Interactive Rounds)", self.SMPC_COLOR, "s"),
            ]:
                if sub.empty:
                    continue
                x = sub["configured_latency_ms"].to_numpy(dtype=float)
                y = sub["total_execution_time_s_mean"].to_numpy(dtype=float)
                yerr = sub["total_execution_time_s_std"].to_numpy(dtype=float)
                ax.errorbar(
                    x,
                    y,
                    yerr=yerr,
                    label=tech,
                    color=color,
                    marker=marker,
                    markersize=6,
                    linewidth=2.0,
                    capsize=3,
                )

            # Check and annotate crossover if present
            if not fhe_sub.empty and not smpc_sub.empty:
                merged = pd.merge(
                    fhe_sub[["configured_latency_ms", "total_execution_time_s_mean"]],
                    smpc_sub[["configured_latency_ms", "total_execution_time_s_mean"]],
                    on="configured_latency_ms",
                    suffixes=("_fhe", "_smpc"),
                ).sort_values("configured_latency_ms")
                xs = merged["configured_latency_ms"].to_numpy(dtype=float)
                diffs = (
                    merged["total_execution_time_s_mean_fhe"]
                    - merged["total_execution_time_s_mean_smpc"]
                ).to_numpy(dtype=float)
                for i in range(len(xs) - 1):
                    if (diffs[i] > 0 and diffs[i + 1] <= 0) or (
                        diffs[i] < 0 and diffs[i + 1] >= 0
                    ):
                        frac = -diffs[i] / (diffs[i + 1] - diffs[i])
                        cx_x = xs[i] + frac * (xs[i + 1] - xs[i])
                        ax.axvline(
                            cx_x,
                            color="#2e7d32",
                            linestyle=":",
                            linewidth=1.6,
                            label=f"Crossover ~ {cx_x:.1f} ms",
                        )
                        break

            n_val = int(wl_df["num_transactions"].iloc[0]) if not wl_df.empty else 1000
            ax.set_title(f"{wl_title} (N={n_val}, 1 Gbps)", fontweight="bold")
            ax.set_xlabel("One-Way Network Latency (ms)")
            ax.set_ylabel("Total Execution Time (seconds)")
            if ax.get_legend_handles_labels()[0]:
                ax.legend(loc="best", frameon=True)

        fig.suptitle(
            "Graph 3: Execution Time vs. Network Latency (Latency Sensitivity & Crossover)",
            fontsize=12,
            fontweight="bold",
            y=1.03,
        )
        fig.tight_layout()
        return self._save_fig(fig, "graph3_execution_time_vs_latency")

    def generate_graph4_execution_time_vs_bandwidth(
        self, df: pd.DataFrame
    ) -> Dict[str, Path]:
        """Graph 4: Execution Time vs. Available Network Bandwidth."""
        fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.5), sharey=False)
        workloads = [
            ("aggregation", "Workload 1: Multi-Center Cohort Aggregation"),
            ("risk_multiplication", "Workload 2: Collaborative Disease Severity Risk"),
        ]
        ref_ds = "small" if "small" in df["dataset_profile"].values else df["dataset_profile"].iloc[0]
        ref_lat = "moderate_wan" if "moderate_wan" in df["latency_profile"].values else "fast_wan"

        for ax, (wl_key, wl_title) in zip(axes, workloads):
            wl_df = df[
                (df["workload"] == wl_key)
                & (df["dataset_profile"] == ref_ds)
                & (df["latency_profile"] == ref_lat)
            ]
            if wl_df.empty:
                wl_df = df[(df["workload"] == wl_key) & (df["dataset_profile"] == ref_ds)]

            fhe_sub = wl_df[wl_df["technology"] == "FHE"].sort_values("configured_bandwidth_mbps")
            smpc_sub = wl_df[wl_df["technology"] == "SMPC"].sort_values("configured_bandwidth_mbps")

            for sub, tech, color, marker in [
                (fhe_sub, "FHE (Large Ciphertext/Key Payload)", self.FHE_COLOR, "o"),
                (smpc_sub, "SMPC (Compact Field Shares)", self.SMPC_COLOR, "s"),
            ]:
                if sub.empty:
                    continue
                x = sub["configured_bandwidth_mbps"].to_numpy(dtype=float)
                y = sub["total_execution_time_s_mean"].to_numpy(dtype=float)
                yerr = sub["total_execution_time_s_std"].to_numpy(dtype=float)
                ax.errorbar(
                    x,
                    y,
                    yerr=yerr,
                    label=tech,
                    color=color,
                    marker=marker,
                    markersize=6,
                    linewidth=2.0,
                    capsize=3,
                )

            if not fhe_sub.empty and not smpc_sub.empty:
                merged = pd.merge(
                    fhe_sub[["configured_bandwidth_mbps", "total_execution_time_s_mean"]],
                    smpc_sub[["configured_bandwidth_mbps", "total_execution_time_s_mean"]],
                    on="configured_bandwidth_mbps",
                    suffixes=("_fhe", "_smpc"),
                ).sort_values("configured_bandwidth_mbps")
                xs = merged["configured_bandwidth_mbps"].to_numpy(dtype=float)
                diffs = (
                    merged["total_execution_time_s_mean_fhe"]
                    - merged["total_execution_time_s_mean_smpc"]
                ).to_numpy(dtype=float)
                for i in range(len(xs) - 1):
                    if (diffs[i] > 0 and diffs[i + 1] <= 0) or (
                        diffs[i] < 0 and diffs[i + 1] >= 0
                    ):
                        frac = -diffs[i] / (diffs[i + 1] - diffs[i])
                        cx_x = xs[i] + frac * (xs[i + 1] - xs[i])
                        ax.axvline(
                            cx_x,
                            color="#2e7d32",
                            linestyle=":",
                            linewidth=1.6,
                            label=f"Crossover ~ {cx_x:.1f} Mbps",
                        )
                        break

            n_val = int(wl_df["num_transactions"].iloc[0]) if not wl_df.empty else 1000
            lat_ms = float(wl_df["configured_latency_ms"].iloc[0]) if not wl_df.empty else 10.0
            ax.set_title(f"{wl_title} (N={n_val}, {lat_ms:g} ms delay)", fontweight="bold")
            ax.set_xlabel("Available Network Bandwidth (Mbps)")
            ax.set_ylabel("Total Execution Time (seconds)")
            ax.set_xscale("log")
            if ax.get_legend_handles_labels()[0]:
                ax.legend(loc="best", frameon=True)

        fig.suptitle(
            "Graph 4: Execution Time vs. Available Network Bandwidth",
            fontsize=12,
            fontweight="bold",
            y=1.03,
        )
        fig.tight_layout()
        return self._save_fig(fig, "graph4_execution_time_vs_bandwidth")

    def generate_graph5_storage_expansion(
        self, df: pd.DataFrame
    ) -> Dict[str, Path]:
        """Graph 5: Ciphertext and Secret-Share Storage Expansion vs. Raw Plaintext Size."""
        fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.5), sharey=False)

        sub = df[
            (df["workload"] == "risk_multiplication")
            & (df["latency_profile"] == "local")
            & (df["bandwidth_profile"] == "high")
        ].copy()
        if sub.empty:
            sub = df.copy()

        fhe_sub = sub[sub["technology"] == "FHE"].sort_values("num_transactions")
        smpc_sub = sub[sub["technology"] == "SMPC"].sort_values("num_transactions")

        merged = pd.merge(
            fhe_sub[
                [
                    "num_transactions",
                    "plaintext_size_bytes_mean",
                    "crypto_payload_size_bytes_mean",
                    "expansion_ratio_mean",
                ]
            ],
            smpc_sub[
                [
                    "num_transactions",
                    "crypto_payload_size_bytes_mean",
                    "expansion_ratio_mean",
                ]
            ],
            on="num_transactions",
            suffixes=("_fhe", "_smpc"),
        ).sort_values("num_transactions")

        ax1, ax2 = axes
        if not merged.empty:
            x_labels = [f"N={int(n):,}" for n in merged["num_transactions"]]
            idx = np.arange(len(x_labels))
            width = 0.26

            pt_kb = merged["plaintext_size_bytes_mean"] / 1024.0
            fhe_kb = merged["crypto_payload_size_bytes_mean_fhe"] / 1024.0
            smpc_kb = merged["crypto_payload_size_bytes_mean_smpc"] / 1024.0

            ax1.bar(idx - width, pt_kb, width, label="Raw Plaintext (64-bit ints)", color=self.PLAINTEXT_COLOR)
            ax1.bar(idx, smpc_kb, width, label="SMPC Secret Shares (3 Parties)", color=self.SMPC_COLOR)
            ax1.bar(idx + width, fhe_kb, width, label="OpenFHE Packed Ciphertexts", color=self.FHE_COLOR)
            ax1.set_xticks(idx)
            ax1.set_xticklabels(x_labels, rotation=15)
            ax1.set_yscale("log")
            ax1.set_ylabel("Representation Size (KB, log scale)")
            ax1.set_title("Cryptographic Payload Size vs. Plaintext", fontweight="bold")
            ax1.legend(loc="best", frameon=True)

            ax2.plot(
                merged["num_transactions"],
                merged["expansion_ratio_mean_fhe"],
                marker="o",
                linewidth=2.0,
                color=self.FHE_COLOR,
                label="FHE Ciphertext Expansion Ratio",
            )
            ax2.plot(
                merged["num_transactions"],
                merged["expansion_ratio_mean_smpc"],
                marker="s",
                linewidth=2.0,
                color=self.SMPC_COLOR,
                label="SMPC 3-Party Share Expansion Ratio (6.0x)",
            )
            ax2.set_xscale("log")
            ax2.set_yscale("log")
            ax2.set_xlabel("Number of Patient Records (N)")
            ax2.set_ylabel("Expansion Ratio (Crypto Bytes / Plaintext Bytes)")
            ax2.set_title("Storage Expansion Ratio Scaling (SIMD Packing Amortization)", fontweight="bold")
            ax2.legend(loc="best", frameon=True)

        fig.suptitle(
            "Graph 5: Ciphertext and Secret-Share Storage Expansion Analysis",
            fontsize=12,
            fontweight="bold",
            y=1.03,
        )
        fig.tight_layout()
        return self._save_fig(fig, "graph5_storage_expansion_comparison")

    def generate_graph6_computation_breakdown(
        self, df: pd.DataFrame
    ) -> Dict[str, Path]:
        """Graph 6: Stacked Phase Breakdown (Setup, Encryption/Sharing, Preprocessing, Online, Decryption)."""
        fig, ax = plt.subplots(figsize=(10.5, 4.8))

        ref_ds = "medium" if "medium" in df["dataset_profile"].values else df["dataset_profile"].iloc[0]
        target_configs = [
            ("FHE", "aggregation", ref_ds, "local", "high", "FHE Agg\n(Local 1ms)"),
            ("SMPC", "aggregation", ref_ds, "local", "high", "SMPC Agg\n(Local 1ms)"),
            ("FHE", "risk_multiplication", ref_ds, "local", "high", "FHE Risk\n(Local 1ms)"),
            ("SMPC", "risk_multiplication", ref_ds, "local", "high", "SMPC Risk\n(Local 1ms)"),
            ("FHE", "risk_multiplication", ref_ds, "slow_wan", "high", "FHE Risk\n(WAN 100ms)"),
            ("SMPC", "risk_multiplication", ref_ds, "slow_wan", "high", "SMPC Risk\n(WAN 100ms)"),
            ("FHE", "risk_multiplication", "small", "moderate_wan", "very_restricted", "FHE Risk\n(1 Mbps BW)"),
            ("SMPC", "risk_multiplication", "small", "moderate_wan", "very_restricted", "SMPC Risk\n(1 Mbps BW)"),
        ]

        labels: List[str] = []
        setup_vals: List[float] = []
        prep_vals: List[float] = []
        input_vals: List[float] = []
        comp_vals: List[float] = []
        output_vals: List[float] = []

        for tech, wl, ds_p, lat_p, bw_p, lbl in target_configs:
            sub = df[
                (df["technology"] == tech)
                & (df["workload"] == wl)
                & (df["dataset_profile"] == ds_p)
                & (df["latency_profile"] == lat_p)
                & (df["bandwidth_profile"] == bw_p)
            ]
            if sub.empty:
                continue
            row = sub.iloc[0]
            labels.append(lbl)
            setup_vals.append(float(row.get("setup_time_s_mean", 0.0)))
            prep_vals.append(float(row.get("preprocessing_time_s_mean", 0.0)))
            input_vals.append(float(row.get("input_protect_time_s_mean", 0.0)))
            comp_vals.append(float(row.get("computation_time_s_mean", 0.0)))
            output_vals.append(float(row.get("output_recover_time_s_mean", 0.0)))

        x = np.arange(len(labels))
        width = 0.52

        b1 = np.array(setup_vals)
        b2 = np.array(prep_vals)
        b3 = np.array(input_vals)
        b4 = np.array(comp_vals)
        b5 = np.array(output_vals)

        ax.bar(x, b1, width, label="Setup / Key Generation", color="#264653")
        ax.bar(x, b2, width, bottom=b1, label="Preprocessing / Beaver Triples", color="#e9c46a")
        ax.bar(x, b3, width, bottom=b1 + b2, label="Encryption / Input Sharing", color="#2a9d8f")
        ax.bar(x, b4, width, bottom=b1 + b2 + b3, label="Online Homomorphic / MPC Evaluation", color="#f4a261")
        ax.bar(x, b5, width, bottom=b1 + b2 + b3 + b4, label="Threshold Decryption / Reconstruction", color="#e76f51")

        ax.set_xticks(x)
        ax.set_xticklabels(labels)
        ax.set_ylabel("Execution Time Breakdown (seconds)")
        ax.set_title(
            f"Graph 6: End-to-End Execution Phase Breakdown (Dataset: {ref_ds.capitalize()})",
            fontweight="bold",
        )
        ax.legend(loc="upper left", bbox_to_anchor=(1.01, 1.0), frameon=True)
        fig.tight_layout()
        return self._save_fig(fig, "graph6_computation_phase_breakdown")

    def generate_all_graphs(self) -> Dict[str, Dict[str, Path]]:
        """Generates and exports all required academic charts in both PNG and PDF formats."""
        summary_df = BenchmarkDataPreprocessor.filter_primary_protocols(
            self.preprocessor.load_summary()
        )
        return {
            "graph1": self.generate_graph1_execution_time_vs_dataset_size(summary_df),
            "graph2": self.generate_graph2_network_traffic_vs_latency(summary_df),
            "graph3": self.generate_graph3_execution_time_vs_latency(summary_df),
            "graph4": self.generate_graph4_execution_time_vs_bandwidth(summary_df),
            "graph5": self.generate_graph5_storage_expansion(summary_df),
            "graph6": self.generate_graph6_computation_breakdown(summary_df),
        }
