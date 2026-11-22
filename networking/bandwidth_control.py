"""Network Bandwidth Rate-Limiting Configuration for Linux tc/netem."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
import yaml


@dataclass(frozen=True)
class BandwidthProfileSpec:
    """Configured bandwidth cap and queue parameters for tc/netem."""

    profile_name: str
    label: str
    rate_mbps: float
    tc_rate: str
    burst_kb: int
    limit_packets: int


class BandwidthController:
    """Loads bandwidth shaping profiles from config/network.yaml and builds tc arguments."""

    def __init__(self, config_path: Optional[str | Path] = None):
        if config_path is not None and Path(config_path).exists():
            cfg_file = Path(config_path)
        else:
            cfg_file = Path(__file__).resolve().parent.parent / "config" / "network.yaml"

        with open(cfg_file, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        self.net_cfg: Dict[str, Any] = raw.get("network", {})
        self.default_profile: str = str(
            self.net_cfg.get("default_bandwidth_profile", "high")
        )
        self.profiles_raw: Dict[str, Dict[str, Any]] = self.net_cfg.get(
            "bandwidth_profiles", {}
        )

    def get_profile(self, profile_name: Optional[str] = None) -> BandwidthProfileSpec:
        name = profile_name or self.default_profile
        if name not in self.profiles_raw:
            raise ValueError(
                f"Unknown bandwidth profile '{name}'. Available: {list(self.profiles_raw.keys())}"
            )
        p = self.profiles_raw[name]
        return BandwidthProfileSpec(
            profile_name=name,
            label=str(p.get("label", name)),
            rate_mbps=float(p["rate_mbps"]),
            tc_rate=str(p["tc_rate"]),
            burst_kb=int(p.get("burst_kb", 128)),
            limit_packets=int(p.get("limit_packets", 10000)),
        )

    @staticmethod
    def build_netem_rate_args(spec: BandwidthProfileSpec) -> List[str]:
        """Builds `tc qdisc ... netem rate <R> limit <P>` arguments."""
        return [
            "rate",
            spec.tc_rate,
            "limit",
            str(spec.limit_packets),
        ]
