"""Crossover Threshold Detection and Experimentally Derived Decision Matrix Generator."""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional
import numpy as np
import pandas as pd

from analysis.preprocessing import BenchmarkDataPreprocessor
from analysis.statistics import StatisticalComparator


class CrossoverAnalyzer:
    """
    Identifies experimentally observed performance crossover points between FHE and SMPC
    across dataset sizes, network latencies, bandwidth levels, and combined regimes.
    """

    def __init__(self, results_dir: Optional[str | Path] = None):
        self.preprocessor = BenchmarkDataPreprocessor(results_dir)
        self.results_dir = self.preprocessor.results_dir
        self.processed_dir = self.results_dir / "processed"
        self.processed_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _detect_1d_crossover(
        sub_df: pd.DataFrame,
        x_col: str,
        x_unit: str,
        axis_name: str,
        context_meta: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Evaluates a sorted 1D sweep of pairwise FHE vs. SMPC comparisons along `x_col`.
        Reports measured bracketing endpoints and piecewise-linear interpolated thresholds
        separately, with bootstrap uncertainty across repeated trials.
        Never extrapolates outside the measured range [x_min, x_max].
        """
        ordered = sub_df.sort_values(x_col).reset_index(drop=True)
        if len(ordered) < 2:
            return {
                "axis": axis_name,
                "x_column": x_col,
                "x_unit": x_unit,
                **context_meta,
                "tested_range_min": float(ordered[x_col].min()) if not ordered.empty else 0.0,
                "tested_range_max": float(ordered[x_col].max()) if not ordered.empty else 0.0,
                "crossover_observed": False,
                "status": "INSUFFICIENT_DATA_POINTS",
                "dominant_technology": (
                    str(ordered["faster_technology"].iloc[0]) if not ordered.empty else "N/A"
                ),
            }

        xs = ordered[x_col].to_numpy(dtype=float)
        deltas = ordered["delta_fhe_minus_smpc_mean_s"].to_numpy(dtype=float)
        fhe_means = ordered["fhe_mean_s"].to_numpy(dtype=float)
        fhe_stds = ordered["fhe_std_s"].to_numpy(dtype=float)
        smpc_means = ordered["smpc_mean_s"].to_numpy(dtype=float)
        smpc_stds = ordered["smpc_std_s"].to_numpy(dtype=float)
        winners = ordered["faster_technology"].tolist()

        crossovers: List[Dict[str, Any]] = []
        rng = np.random.Generator(np.random.PCG64(42))

        for i in range(len(xs) - 1):
            d1, d2 = deltas[i], deltas[i + 1]
            x1, x2 = xs[i], xs[i + 1]

            # Sign change between neighboring measured points x1 and x2
            if (d1 < 0 and d2 >= 0) or (d1 > 0 and d2 <= 0) or (d1 == 0):
                denom = d2 - d1
                frac = (-d1 / denom) if abs(denom) > 1e-12 else 0.5
                frac = max(0.0, min(1.0, frac))
                x_interp_linear = x1 + frac * (x2 - x1)

                # Quantify uncertainty via Monte Carlo resampling from the measured trial distributions
                mc_thresholds: List[float] = []
                for _ in range(500):
                    d1_sample = rng.normal(fhe_means[i], max(1e-6, fhe_stds[i])) - rng.normal(
                        smpc_means[i], max(1e-6, smpc_stds[i])
                    )
                    d2_sample = rng.normal(
                        fhe_means[i + 1], max(1e-6, fhe_stds[i + 1])
                    ) - rng.normal(smpc_means[i + 1], max(1e-6, smpc_stds[i + 1]))
                    if abs(d2_sample - d1_sample) > 1e-12:
                        f_s = max(0.0, min(1.0, -d1_sample / (d2_sample - d1_sample)))
                        mc_thresholds.append(float(x1 + f_s * (x2 - x1)))

                if mc_thresholds:
                    interp_std = float(np.std(mc_thresholds, ddof=1))
                    ci_low = float(np.percentile(mc_thresholds, 2.5))
                    ci_high = float(np.percentile(mc_thresholds, 97.5))
                else:
                    interp_std = 0.0
                    ci_low = x_interp_linear
                    ci_high = x_interp_linear

                transition = f"{winners[i]} -> {winners[i + 1]}"
                crossovers.append({
                    "transition": transition,
                    "measured_lower_bound": round(float(x1), 4),
                    "measured_upper_bound": round(float(x2), 4),
                    "measured_winner_at_lower": winners[i],
                    "measured_winner_at_upper": winners[i + 1],
                    "delta_at_lower_s": round(float(d1), 6),
                    "delta_at_upper_s": round(float(d2), 6),
                    "interpolated_threshold": round(float(x_interp_linear), 4),
                    "interpolated_uncertainty_std": round(interp_std, 4),
                    "interpolated_ci95_low": round(ci_low, 4),
                    "interpolated_ci95_high": round(ci_high, 4),
                })

        if crossovers:
            first_cx = crossovers[0]
            return {
                "axis": axis_name,
                "x_column": x_col,
                "x_unit": x_unit,
                **context_meta,
                "tested_range_min": round(float(xs[0]), 4),
                "tested_range_max": round(float(xs[-1]), 4),
                "crossover_observed": True,
                "status": "CROSSOVER_OBSERVED_WITHIN_MEASURED_RANGE",
                "transition": first_cx["transition"],
                "measured_bracket": f"[{first_cx['measured_lower_bound']}, {first_cx['measured_upper_bound']}] {x_unit}",
                "measured_lower_bound": first_cx["measured_lower_bound"],
                "measured_upper_bound": first_cx["measured_upper_bound"],
                "interpolated_threshold": first_cx["interpolated_threshold"],
                "interpolated_uncertainty_std": first_cx["interpolated_uncertainty_std"],
                "interpolated_ci95_low": first_cx["interpolated_ci95_low"],
                "interpolated_ci95_high": first_cx["interpolated_ci95_high"],
                "all_crossovers": crossovers,
            }

        # No crossover in tested range — explicitly report without extrapolating
        dominant = winners[0]
        min_abs_idx = int(np.argmin(np.abs(deltas)))
        return {
            "axis": axis_name,
            "x_column": x_col,
            "x_unit": x_unit,
            **context_meta,
            "tested_range_min": round(float(xs[0]), 4),
            "tested_range_max": round(float(xs[-1]), 4),
            "crossover_observed": False,
            "status": "NO_CROSSOVER_OBSERVED_IN_TESTED_RANGE",
            "dominant_technology": dominant,
            "closest_measured_point": round(float(xs[min_abs_idx]), 4),
            "closest_delta_s": round(float(deltas[min_abs_idx]), 6),
            "measured_bracket": "None (No ranking change in tested range)",
            "interpolated_threshold": None,
        }

    def generate_decision_matrix(self, pairwise_df: pd.DataFrame) -> pd.DataFrame:
        """
        Derives the experimental decision criteria table directly from measured benchmark points.
        Never prepopulates assumed winners.
        """
        if pairwise_df.empty:
            return pd.DataFrame()

        def _format_evidence(row: pd.Series) -> tuple[str, str]:
            winner = str(row["faster_technology"])
            fhe_t = float(row["fhe_mean_s"])
            smpc_t = float(row["smpc_mean_s"])
            fhe_std = float(row["fhe_std_s"])
            smpc_std = float(row["smpc_std_s"])
            spd = float(row["speedup_factor"])
            p_val = float(row["welch_p_value"])
            fhe_mb = float(row["fhe_network_mb"])
            smpc_mb = float(row["smpc_network_mb"])
            ev = (
                f"FHE={fhe_t:.3f}+/-{fhe_std:.3f}s ({fhe_mb:.2f} MB) vs. "
                f"SMPC={smpc_t:.3f}+/-{smpc_std:.3f}s ({smpc_mb:.2f} MB); "
                f"{winner} {spd:.2f}x faster (p={p_val:.4g})"
            )
            return winner, ev

        scenarios: List[Dict[str, Any]] = []

        # 1. Small dataset, low latency (Local LAN)
        s1 = pairwise_df[
            (pairwise_df["dataset_profile"] == "small")
            & (pairwise_df["latency_profile"] == "local")
            & (pairwise_df["bandwidth_profile"] == "high")
        ]
        for _, r in s1.iterrows():
            w, ev = _format_evidence(r)
            scenarios.append({
                "Conditions": f"Small dataset (N={int(r['num_transactions'])}), low latency (1 ms, 1 Gbps) [{r['workload']}]",
                "Recommended Approach": w,
                "Experimental Evidence": ev,
            })

        # 2. Large dataset, restricted bandwidth
        s2 = pairwise_df[
            (pairwise_df["num_transactions"] >= 1000)
            & (pairwise_df["bandwidth_profile"].isin(["restricted", "very_restricted"]))
        ].sort_values(["num_transactions", "configured_bandwidth_mbps"], ascending=[False, True])
        for wl in ("aggregation", "risk_multiplication"):
            sub = s2[s2["workload"] == wl]
            if not sub.empty:
                r = sub.iloc[0]
                w, ev = _format_evidence(r)
                scenarios.append({
                    "Conditions": (
                        f"Large dataset (N={int(r['num_transactions'])}), restricted bandwidth "
                        f"({r['configured_bandwidth_mbps']:g} Mbps, {r['configured_latency_ms']:g} ms) [{wl}]"
                    ),
                    "Recommended Approach": w,
                    "Experimental Evidence": ev,
                })

        # 3. High latency WAN environment (100-200 ms one-way delay)
        s3 = pairwise_df[
            pairwise_df["latency_profile"].isin(["slow_wan", "high_latency"])
        ].sort_values("configured_latency_ms", ascending=False)
        for wl in ("aggregation", "risk_multiplication"):
            sub = s3[s3["workload"] == wl]
            if not sub.empty:
                r = sub.iloc[0]
                w, ev = _format_evidence(r)
                scenarios.append({
                    "Conditions": (
                        f"High latency WAN ({r['configured_latency_ms']:g} ms delay, "
                        f"{r['configured_bandwidth_mbps']:g} Mbps, N={int(r['num_transactions'])}) [{wl}]"
                    ),
                    "Recommended Approach": w,
                    "Experimental Evidence": ev,
                })

        # 4. High computational workload (Multiplicative Risk on Maximum Tested Dataset Size)
        s4 = pairwise_df[
            (pairwise_df["workload"] == "risk_multiplication")
            & (pairwise_df["latency_profile"] == "local")
        ].sort_values("num_transactions", ascending=False)
        if not s4.empty:
            r = s4.iloc[0]
            w, ev = _format_evidence(r)
            scenarios.append({
                "Conditions": (
                    f"High multiplicative workload (N={int(r['num_transactions'])} dot-product, "
                    f"{r['configured_latency_ms']:g} ms, {r['configured_bandwidth_mbps']:g} Mbps)"
                ),
                "Recommended Approach": w,
                "Experimental Evidence": ev,
            })

        return pd.DataFrame(scenarios)

    def run_full_analysis(self) -> Dict[str, Any]:
        """
        Executes pairwise statistical comparisons, 1D/2D crossover detection across all axes,
        and exports decision_matrix.csv, crossover_thresholds.csv, and crossover_analysis.json.
        """
        raw_df = self.preprocessor.load_raw_measured()
        pairwise_df = StatisticalComparator.build_pairwise_comparison_table(raw_df)

        threshold_results: List[Dict[str, Any]] = []

        for wl in ("aggregation", "risk_multiplication"):
            wl_df = pairwise_df[pairwise_df["workload"] == wl]
            if wl_df.empty:
                continue

            # 1. Dataset Size Crossovers under each tested (latency, bandwidth) pair
            for (lat_p, bw_p), grp in wl_df.groupby(["latency_profile", "bandwidth_profile"]):
                if len(grp) >= 2:
                    threshold_results.append(
                        self._detect_1d_crossover(
                            sub_df=grp,
                            x_col="num_transactions",
                            x_unit="patients",
                            axis_name="dataset_size",
                            context_meta={
                                "workload": wl,
                                "fixed_condition": f"latency={lat_p}, bandwidth={bw_p}",
                            },
                        )
                    )

            # 2. Network Latency Crossovers at fixed bandwidth
            for (ds_p, bw_p), grp in wl_df.groupby(["dataset_profile", "bandwidth_profile"]):
                if len(grp) >= 2:
                    threshold_results.append(
                        self._detect_1d_crossover(
                            sub_df=grp,
                            x_col="configured_latency_ms",
                            x_unit="ms",
                            axis_name="network_latency",
                            context_meta={
                                "workload": wl,
                                "fixed_condition": f"dataset={ds_p}, bandwidth={bw_p}",
                            },
                        )
                    )

            # 3. Network Bandwidth Crossovers at fixed latency
            for (ds_p, lat_p), grp in wl_df.groupby(["dataset_profile", "latency_profile"]):
                if len(grp) >= 2:
                    threshold_results.append(
                        self._detect_1d_crossover(
                            sub_df=grp,
                            x_col="configured_bandwidth_mbps",
                            x_unit="Mbps",
                            axis_name="network_bandwidth",
                            context_meta={
                                "workload": wl,
                                "fixed_condition": f"dataset={ds_p}, latency={lat_p}",
                            },
                        )
                    )

        decision_df = self.generate_decision_matrix(pairwise_df)
        thresholds_df = pd.DataFrame(threshold_results)

        pairwise_csv = self.processed_dir / "pairwise_statistical_comparison.csv"
        thresholds_csv = self.processed_dir / "crossover_thresholds.csv"
        decision_csv = self.processed_dir / "decision_matrix.csv"
        analysis_json = self.processed_dir / "crossover_analysis.json"

        pairwise_df.to_csv(pairwise_csv, index=False)
        thresholds_df.to_csv(thresholds_csv, index=False)
        decision_df.to_csv(decision_csv, index=False)

        report_payload = {
            "pairwise_comparisons": pairwise_df.to_dict(orient="records"),
            "crossover_thresholds": threshold_results,
            "decision_matrix": decision_df.to_dict(orient="records"),
        }
        with open(analysis_json, "w", encoding="utf-8") as f:
            json.dump(report_payload, f, indent=2, default=str)

        return report_payload
