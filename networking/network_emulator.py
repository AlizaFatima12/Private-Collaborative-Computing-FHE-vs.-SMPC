"""Linux Traffic Control (tc / netem) Network Emulator Engine."""

from contextlib import contextmanager
from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any, Dict, Iterator, Optional
import yaml

from networking.latency_control import LatencyController, LatencyProfileSpec
from networking.bandwidth_control import BandwidthController, BandwidthProfileSpec
from networking.cleanup import NetworkCleanupManager


@dataclass(frozen=True)
class ActiveNetworkState:
    """Verified network shaping configuration and live measured RTT for an experiment run."""

    interface: str
    latency_profile: str
    bandwidth_profile: str
    configured_one_way_delay_ms: float
    expected_rtt_ms: float
    measured_rtt_ms: float
    configured_bandwidth_mbps: float
    configured_packet_loss_pct: float
    tc_shaping_active: bool
    tc_qdisc_summary: str

    def to_metrics_dict(self) -> Dict[str, Any]:
        return {
            "network_interface": self.interface,
            "latency_profile": self.latency_profile,
            "bandwidth_profile": self.bandwidth_profile,
            "configured_latency_ms": round(self.configured_one_way_delay_ms, 3),
            "expected_rtt_ms": round(self.expected_rtt_ms, 3),
            "measured_rtt_ms": round(self.measured_rtt_ms, 3),
            "configured_bandwidth_mbps": round(self.configured_bandwidth_mbps, 3),
            "configured_packet_loss_pct": round(self.configured_packet_loss_pct, 3),
            "tc_shaping_active": self.tc_shaping_active,
        }


class NetworkEmulator:
    """Applies, validates, and cleans up Linux tc/netem latency and bandwidth profiles."""

    def __init__(
        self,
        config_path: Optional[str | Path] = None,
        interface: Optional[str] = None,
    ):
        if config_path is not None and Path(config_path).exists():
            cfg_file = Path(config_path)
        else:
            cfg_file = Path(__file__).resolve().parent.parent / "config" / "network.yaml"

        with open(cfg_file, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        self.net_cfg: Dict[str, Any] = raw.get("network", {})
        self.interface: str = (
            interface
            or os.environ.get("PCC_NETWORK_INTERFACE")
            or str(self.net_cfg.get("default_interface", "lo"))
        )
        self.verify_rtt: bool = bool(self.net_cfg.get("verify_rtt_before_benchmark", True))
        self.rtt_probe_count: int = int(self.net_cfg.get("rtt_probe_count", 3))
        self.latency_ctrl = LatencyController(cfg_file)
        self.bandwidth_ctrl = BandwidthController(cfg_file)

    def configure_shaping(
        self,
        latency_profile: str = "local",
        bandwidth_profile: str = "high",
        packet_loss_pct: Optional[float] = None,
    ) -> ActiveNetworkState:
        """
        Applies tc/netem shaping rules to the active Linux network interface,
        verifies that the qdisc is installed, and measures actual TCP RTT.
        """
        lat_spec: LatencyProfileSpec = self.latency_ctrl.get_profile(
            latency_profile, packet_loss_pct=packet_loss_pct
        )
        bw_spec: BandwidthProfileSpec = self.bandwidth_ctrl.get_profile(bandwidth_profile)

        tc_bin = shutil.which("tc") or ("/usr/sbin/tc" if Path("/usr/sbin/tc").exists() else None)
        tc_active = False
        qdisc_summary = "tc_unavailable"

        # Reset any prior rule first
        NetworkCleanupManager.reset_interface(self.interface)

        if tc_bin is not None:
            cmd = (
                [tc_bin, "qdisc", "replace", "dev", self.interface, "root", "netem"]
                + LatencyController.build_netem_delay_args(lat_spec)
                + BandwidthController.build_netem_rate_args(bw_spec)
            )
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            if proc.returncode == 0:
                NetworkCleanupManager.register_shaped_interface(self.interface)
                show_proc = subprocess.run(
                    [tc_bin, "qdisc", "show", "dev", self.interface],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    check=False,
                )
                qdisc_summary = (show_proc.stdout or "").strip()
                tc_active = "netem" in qdisc_summary
            else:
                qdisc_summary = f"tc_error: {(proc.stderr or '').strip()}"

        measured_rtt = (
            LatencyController.measure_actual_rtt_ms(num_probes=self.rtt_probe_count)
            if self.verify_rtt
            else lat_spec.expected_rtt_ms
        )

        return ActiveNetworkState(
            interface=self.interface,
            latency_profile=lat_spec.profile_name,
            bandwidth_profile=bw_spec.profile_name,
            configured_one_way_delay_ms=lat_spec.one_way_delay_ms,
            expected_rtt_ms=lat_spec.expected_rtt_ms,
            measured_rtt_ms=measured_rtt,
            configured_bandwidth_mbps=bw_spec.rate_mbps,
            configured_packet_loss_pct=lat_spec.packet_loss_pct,
            tc_shaping_active=tc_active,
            tc_qdisc_summary=qdisc_summary,
        )

    def cleanup(self) -> None:
        NetworkCleanupManager.reset_interface(self.interface)

    @contextmanager
    def emulate_profile(
        self,
        latency_profile: str = "local",
        bandwidth_profile: str = "high",
        packet_loss_pct: Optional[float] = None,
    ) -> Iterator[ActiveNetworkState]:
        """Context manager that applies tc/netem shaping and restores settings upon exit."""
        state = self.configure_shaping(
            latency_profile=latency_profile,
            bandwidth_profile=bandwidth_profile,
            packet_loss_pct=packet_loss_pct,
        )
        try:
            yield state
        finally:
            self.cleanup()
