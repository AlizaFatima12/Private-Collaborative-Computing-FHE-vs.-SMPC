"""Parametric and Non-Parametric Statistical Hypothesis Testing for FHE vs. SMPC."""

import math
from typing import Any, Dict, List
import numpy as np
import pandas as pd
from scipy import stats


class StatisticalComparator:
    """Computes pairwise statistical significance, confidence intervals, and speedup ratios."""

    @staticmethod
    def compare_trials(
        fhe_samples: List[float] | np.ndarray,
        smpc_samples: List[float] | np.ndarray,
        alpha: float = 0.05,
    ) -> Dict[str, Any]:
        """
        Compares repeated trial measurements of FHE and SMPC at a single experimental point.
        Returns mean/median differences, speedup factors, Welch's t-test, Mann-Whitney U test,
        and 95% confidence interval on the difference (T_FHE - T_SMPC).
        """
        fhe_arr = np.asarray(fhe_samples, dtype=float)
        smpc_arr = np.asarray(smpc_samples, dtype=float)

        if len(fhe_arr) == 0 or len(smpc_arr) == 0:
            raise ValueError("Cannot compare empty trial sample arrays.")

        fhe_mean = float(np.mean(fhe_arr))
        fhe_med = float(np.median(fhe_arr))
        fhe_std = float(np.std(fhe_arr, ddof=1)) if len(fhe_arr) > 1 else 0.0

        smpc_mean = float(np.mean(smpc_arr))
        smpc_med = float(np.median(smpc_arr))
        smpc_std = float(np.std(smpc_arr, ddof=1)) if len(smpc_arr) > 1 else 0.0

        # Difference delta = T_FHE - T_SMPC (negative => FHE is faster; positive => SMPC is faster)
        diff_mean = fhe_mean - smpc_mean
        diff_med = fhe_med - smpc_med

        if len(fhe_arr) > 1 and len(smpc_arr) > 1 and (fhe_std > 0 or smpc_std > 0):
            t_res = stats.ttest_ind(fhe_arr, smpc_arr, equal_var=False)
            t_stat = float(t_res.statistic)
            p_val_t = float(t_res.pvalue)

            u_res = stats.mannwhitneyu(fhe_arr, smpc_arr, alternative="two-sided")
            p_val_u = float(u_res.pvalue)

            se_diff = math.sqrt((fhe_std ** 2) / len(fhe_arr) + (smpc_std ** 2) / len(smpc_arr))
            df_welch_num = ((fhe_std ** 2) / len(fhe_arr) + (smpc_std ** 2) / len(smpc_arr)) ** 2
            df_welch_den = (
                (((fhe_std ** 2) / len(fhe_arr)) ** 2) / max(1, len(fhe_arr) - 1)
                + (((smpc_std ** 2) / len(smpc_arr)) ** 2) / max(1, len(smpc_arr) - 1)
            )
            df_eff = df_welch_num / df_welch_den if df_welch_den > 0 else (len(fhe_arr) + len(smpc_arr) - 2)
            t_crit = float(stats.t.ppf(1.0 - alpha / 2.0, df=max(1.0, df_eff)))
            ci_margin = t_crit * se_diff
        else:
            t_stat = 0.0
            p_val_t = 1.0 if abs(diff_mean) < 1e-12 else 0.0
            p_val_u = p_val_t
            ci_margin = 0.0

        ci_low = diff_mean - ci_margin
        ci_high = diff_mean + ci_margin
        statistically_significant = bool(p_val_t < alpha)

        if diff_mean < 0:
            faster_tech = "FHE"
            speedup = smpc_mean / fhe_mean if fhe_mean > 0 else 1.0
        elif diff_mean > 0:
            faster_tech = "SMPC"
            speedup = fhe_mean / smpc_mean if smpc_mean > 0 else 1.0
        else:
            faster_tech = "TIE"
            speedup = 1.0

        return {
            "fhe_mean_s": round(fhe_mean, 6),
            "fhe_median_s": round(fhe_med, 6),
            "fhe_std_s": round(fhe_std, 6),
            "smpc_mean_s": round(smpc_mean, 6),
            "smpc_median_s": round(smpc_med, 6),
            "smpc_std_s": round(smpc_std, 6),
            "delta_fhe_minus_smpc_mean_s": round(diff_mean, 6),
            "delta_fhe_minus_smpc_median_s": round(diff_med, 6),
            "delta_ci95_low_s": round(ci_low, 6),
            "delta_ci95_high_s": round(ci_high, 6),
            "welch_t_statistic": round(t_stat, 4),
            "welch_p_value": round(p_val_t, 6),
            "mann_whitney_p_value": round(p_val_u, 6),
            "statistically_significant": statistically_significant,
            "faster_technology": faster_tech,
            "speedup_factor": round(speedup, 3),
        }

    @classmethod
    def build_pairwise_comparison_table(cls, raw_df: pd.DataFrame) -> pd.DataFrame:
        """Builds a complete table comparing FHE vs. SMPC at every matching experimental condition."""
        if raw_df.empty:
            return pd.DataFrame()

        primary_df = raw_df[
            raw_df["protocol"].isin(["threshold_bfv_128", "semi_dishonest_majority_3p"])
        ].copy()
        if primary_df.empty:
            primary_df = raw_df.copy()

        cond_cols = [
            "workload",
            "dataset_profile",
            "num_transactions",
            "latency_profile",
            "configured_latency_ms",
            "bandwidth_profile",
            "configured_bandwidth_mbps",
        ]
        rows: List[Dict[str, Any]] = []

        for cond_vals, group in primary_df.groupby(cond_cols, dropna=False, sort=True):
            fhe_sub = group[group["technology"] == "FHE"]["total_execution_time_s"].dropna().to_numpy()
            smpc_sub = group[group["technology"] == "SMPC"]["total_execution_time_s"].dropna().to_numpy()
            if len(fhe_sub) == 0 or len(smpc_sub) == 0:
                continue

            row = dict(zip(cond_cols, cond_vals))
            comp = cls.compare_trials(fhe_sub, smpc_sub)
            row.update(comp)

            # Also compare network traffic
            fhe_net = float(group[group["technology"] == "FHE"]["measured_network_traffic_mb"].mean())
            smpc_net = float(group[group["technology"] == "SMPC"]["measured_network_traffic_mb"].mean())
            row["fhe_network_mb"] = round(fhe_net, 4)
            row["smpc_network_mb"] = round(smpc_net, 4)
            row["network_traffic_ratio_fhe_over_smpc"] = (
                round(fhe_net / smpc_net, 3) if smpc_net > 0 else 0.0
            )
            rows.append(row)

        return pd.DataFrame(rows)
