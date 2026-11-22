"""Experiment Matrix Planner and Configuration Loader."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
import yaml


@dataclass(frozen=True)
class ExperimentSpec:
    """Single experimental configuration point in the benchmark matrix."""

    dimension: str
    dataset_profile: str
    workload: str
    latency_profile: str
    bandwidth_profile: str
    fhe_profile: str = "threshold_bfv_128"
    smpc_protocol: str = "semi_dishonest_majority_3p"
    packet_loss_pct: float = 0.0


class ExperimentManager:
    """Constructs structured, deduplicated benchmark matrices from config/experiment.yaml."""

    def __init__(self, config_dir: Optional[str | Path] = None):
        if config_dir is not None:
            self.config_dir = Path(config_dir)
        else:
            self.config_dir = Path(__file__).resolve().parent.parent / "config"

        with open(self.config_dir / "experiment.yaml", "r", encoding="utf-8") as f:
            self.exp_yaml: Dict[str, Any] = yaml.safe_load(f) or {}

        self.exec_cfg: Dict[str, Any] = self.exp_yaml.get("execution", {})
        self.default_mode: str = str(self.exec_cfg.get("default_mode", "standard"))
        self.default_warmup: int = int(self.exec_cfg.get("warmup_runs", 1))
        self.default_repetitions: int = int(self.exec_cfg.get("repetitions", 5))
        self.confidence_level: float = float(self.exec_cfg.get("confidence_level", 0.95))

    def get_run_counts(self, mode: Optional[str] = None) -> tuple[int, int]:
        active_mode = (mode or self.default_mode).lower()
        matrices = self.exec_cfg.get("matrices", {})
        m_cfg = matrices.get(active_mode, {})
        warmup = int(m_cfg.get("warmup_runs", self.default_warmup))
        reps = int(m_cfg.get("repetitions", self.default_repetitions))
        return warmup, reps

    def build_schedule(self, mode: Optional[str] = None) -> List[ExperimentSpec]:
        """
        Builds a comprehensive, deduplicated experimental schedule covering:
          1. Dataset size scaling (100 to 100,000 transactions) under Local LAN and WAN profiles.
          2. Independent network latency sweep (1 ms to 200 ms one-way delay) at fixed 1 Gbps bandwidth.
          3. Independent network bandwidth sweep (1000 Mbps down to 1 Mbps) at fixed latency.
          4. Combined WAN conditions and cryptographic protocol / parameter variant comparisons.
        """
        active_mode = (mode or self.default_mode).lower()
        matrices = self.exec_cfg.get("matrices", {})
        m_cfg = matrices.get(active_mode, matrices.get("standard", {}))

        ds_profiles: List[str] = list(
            m_cfg.get(
                "dataset_profiles",
                ["small", "medium", "large", "very_large", "stress_test"],
            )
        )
        workloads: List[str] = list(
            m_cfg.get("workloads", ["aggregation", "risk_multiplication"])
        )
        lat_profiles: List[str] = list(
            m_cfg.get(
                "latency_profiles",
                ["local", "fast_wan", "moderate_wan", "slow_wan", "high_latency"],
            )
        )
        bw_profiles: List[str] = list(
            m_cfg.get(
                "bandwidth_profiles",
                ["high", "medium", "restricted", "very_restricted"],
            )
        )

        schedule: List[ExperimentSpec] = []
        seen_keys: set[tuple[str, str, str, str, str, str]] = set()

        def _add(
            dim: str,
            ds: str,
            wl: str,
            lat: str,
            bw: str,
            fhe_p: str = "threshold_bfv_128",
            smpc_p: str = "semi_dishonest_majority_3p",
        ):
            key = (ds, wl, lat, bw, fhe_p, smpc_p)
            if key not in seen_keys:
                seen_keys.add(key)
                schedule.append(
                    ExperimentSpec(
                        dimension=dim,
                        dataset_profile=ds,
                        workload=wl,
                        latency_profile=lat,
                        bandwidth_profile=bw,
                        fhe_profile=fhe_p,
                        smpc_protocol=smpc_p,
                    )
                )

        # 1. Dataset Scaling Sweep across all configured dataset profiles (100 to 100,000)
        for wl in workloads:
            for ds in ds_profiles:
                _add("dataset_scaling", ds, wl, "local", "high")
            # Evaluate dataset scaling under Moderate WAN (50ms, 10Mbps) for Graph 1 multi-profile comparison
            for ds in [d for d in ds_profiles if d in ("small", "medium", "large")]:
                _add("dataset_scaling_wan", ds, wl, "moderate_wan", "restricted")

        # 2. Independent Network Latency Sweep (1ms to 200ms at fixed 1 Gbps bandwidth)
        lat_ds = "medium" if "medium" in ds_profiles else ds_profiles[0]
        for wl in workloads:
            for lat in lat_profiles:
                _add("latency_sweep", lat_ds, wl, lat, "high")

        # 3. Independent Network Bandwidth Sweep (1000 Mbps down to 1 Mbps at fixed Moderate WAN 50ms)
        for wl in workloads:
            for bw in bw_profiles:
                _add("bandwidth_sweep", "small", wl, "moderate_wan", bw)

        # 4. Trust Model & Cryptographic Parameter Depth Comparison
        ref_ds = "medium" if "medium" in ds_profiles else ds_profiles[0]
        for wl in workloads:
            _add(
                "trust_model_comparison",
                ref_ds,
                wl,
                "local",
                "high",
                fhe_p="single_key_bfv_128",
                smpc_p="shamir_honest_majority_3p",
            )
            _add(
                "parameter_depth_comparison",
                ref_ds,
                wl,
                "local",
                "high",
                fhe_p="deep_bfv_128",
                smpc_p="malicious_shamir_3p",
            )

        return schedule
