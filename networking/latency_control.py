"""Network Latency, Jitter, Packet Loss Configuration, and Live RTT Probe Verification."""

from dataclasses import dataclass
from pathlib import Path
import socket
import statistics
import threading
import time
from typing import Any, Dict, List, Optional
import yaml


@dataclass(frozen=True)
class LatencyProfileSpec:
    """Configured one-way delay, expected RTT, jitter, and packet loss specification."""

    profile_name: str
    label: str
    one_way_delay_ms: float
    expected_rtt_ms: float
    jitter_ms: float
    packet_loss_pct: float
    symmetry: str


class LatencyController:
    """Manages netem latency/loss parameters and measures actual live TCP round-trip time."""

    def __init__(self, config_path: Optional[str | Path] = None):
        if config_path is not None and Path(config_path).exists():
            cfg_file = Path(config_path)
        else:
            cfg_file = Path(__file__).resolve().parent.parent / "config" / "network.yaml"

        with open(cfg_file, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        self.net_cfg: Dict[str, Any] = raw.get("network", {})
        self.default_profile: str = str(self.net_cfg.get("default_latency_profile", "local"))
        self.default_loss_pct: float = float(self.net_cfg.get("default_packet_loss_pct", 0.0))
        self.delay_symmetry: str = str(self.net_cfg.get("delay_symmetry", "symmetric"))
        self.profiles_raw: Dict[str, Dict[str, Any]] = self.net_cfg.get("latency_profiles", {})

    def get_profile(
        self,
        profile_name: Optional[str] = None,
        packet_loss_pct: Optional[float] = None,
    ) -> LatencyProfileSpec:
        name = profile_name or self.default_profile
        if name not in self.profiles_raw:
            raise ValueError(
                f"Unknown latency profile '{name}'. Available: {list(self.profiles_raw.keys())}"
            )
        p = self.profiles_raw[name]
        loss = self.default_loss_pct if packet_loss_pct is None else float(packet_loss_pct)
        return LatencyProfileSpec(
            profile_name=name,
            label=str(p.get("label", name)),
            one_way_delay_ms=float(p["one_way_delay_ms"]),
            expected_rtt_ms=float(p.get("expected_rtt_ms", float(p["one_way_delay_ms"]) * 2.0)),
            jitter_ms=float(p.get("jitter_ms", 0.0)),
            packet_loss_pct=loss,
            symmetry=self.delay_symmetry,
        )

    @staticmethod
    def build_netem_delay_args(spec: LatencyProfileSpec) -> List[str]:
        """Builds the `tc qdisc ... netem` arguments for delay and optional packet loss."""
        args: List[str] = []
        if spec.one_way_delay_ms > 0:
            args.extend(["delay", f"{spec.one_way_delay_ms:.2f}ms"])
            if spec.jitter_ms > 0:
                args.append(f"{spec.jitter_ms:.2f}ms")
        if spec.packet_loss_pct > 0:
            args.extend(["loss", f"{spec.packet_loss_pct:.2f}%"])
        return args

    @staticmethod
    def measure_actual_rtt_ms(
        target_host: str = "127.0.0.1",
        num_probes: int = 3,
    ) -> float:
        """
        Measures actual application-level Round-Trip Time (RTT) in milliseconds over a real
        TCP_NODELAY socket on the shaped interface (where request + response each traverse
        the interface's netem egress qdisc once, yielding actual RTT ~ 2 * one_way_delay).
        """
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((target_host, 0))
        _, port = server.getsockname()
        server.listen(1)

        def _echo_worker():
            try:
                conn, _ = server.accept()
                conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                with conn:
                    for _ in range(num_probes):
                        data = conn.recv(64)
                        if not data:
                            break
                        conn.sendall(data)
            finally:
                server.close()

        t = threading.Thread(target=_echo_worker, daemon=True)
        t.start()

        rtt_samples_ms: List[float] = []
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
            client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            client.connect((target_host, port))
            payload = b"PCC_RTT_PROBE_64B" + b"0" * 47
            for _ in range(num_probes):
                t0 = time.perf_counter()
                client.sendall(payload)
                _ = client.recv(64)
                rtt_samples_ms.append((time.perf_counter() - t0) * 1000.0)

        t.join(timeout=10.0)
        return float(statistics.median(rtt_samples_ms)) if rtt_samples_ms else 0.0
