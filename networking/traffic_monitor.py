"""Linux Kernel Network Interface Traffic Monitor (/proc/net/dev & tc stats)."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class InterfaceCounters:
    """Raw cumulative byte and packet counters for a Linux network interface."""

    interface: str
    rx_bytes: int
    rx_packets: int
    tx_bytes: int
    tx_packets: int


@dataclass(frozen=True)
class TrafficDeltaMetrics:
    """
    Measured network traffic delta across a benchmark run.

    Note on avoiding double-counting (Section 11):
      In a closed multi-party network on a shared interface (such as container loopback 'lo'
      or summed across all participant container 'eth0' interfaces), every byte transmitted
      (tx_bytes) by one participant is received (rx_bytes) by the recipient participant(s).
      Summing tx_bytes + rx_bytes would double-count every protocol message.
      Therefore:
        - interface_tx_bytes_delta records total bytes injected onto the network.
        - interface_rx_bytes_delta records total bytes delivered from the network.
        - aggregate_network_traffic_bytes equals interface_tx_bytes_delta (or protocol_network_bytes
          when running on a non-Linux host without /proc/net/dev).
    """

    interface: str
    interface_tx_bytes_delta: int
    interface_rx_bytes_delta: int
    interface_tx_packets_delta: int
    interface_rx_packets_delta: int
    aggregate_network_traffic_bytes: int
    aggregate_network_traffic_mb: float

    def to_metrics_dict(self) -> Dict[str, Any]:
        return {
            "net_interface": self.interface,
            "net_tx_bytes": self.interface_tx_bytes_delta,
            "net_rx_bytes": self.interface_rx_bytes_delta,
            "net_tx_packets": self.interface_tx_packets_delta,
            "net_rx_packets": self.interface_rx_packets_delta,
            "measured_network_traffic_bytes": self.aggregate_network_traffic_bytes,
            "measured_network_traffic_mb": round(self.aggregate_network_traffic_mb, 6),
        }


class NetworkTrafficMonitor:
    """Reads actual kernel-level byte and packet counters from /proc/net/dev."""

    def __init__(self, interface: str = "lo"):
        self.interface = interface
        self._snapshot_before: Optional[InterfaceCounters] = None

    @staticmethod
    def read_interface_counters(interface: str = "lo") -> Optional[InterfaceCounters]:
        proc_net = Path("/proc/net/dev")
        if not proc_net.exists():
            return None

        with open(proc_net, "r", encoding="utf-8") as f:
            lines = f.readlines()

        for line in lines[2:]:
            if ":" not in line:
                continue
            iface_part, stats_part = line.split(":", 1)
            if iface_part.strip() == interface:
                fields = stats_part.split()
                if len(fields) >= 10:
                    return InterfaceCounters(
                        interface=interface,
                        rx_bytes=int(fields[0]),
                        rx_packets=int(fields[1]),
                        tx_bytes=int(fields[8]),
                        tx_packets=int(fields[9]),
                    )
        return None

    def start(self) -> Optional[InterfaceCounters]:
        self._snapshot_before = self.read_interface_counters(self.interface)
        return self._snapshot_before

    def stop(self, fallback_protocol_bytes: int = 0) -> TrafficDeltaMetrics:
        after = self.read_interface_counters(self.interface)
        before = self._snapshot_before

        if before is not None and after is not None:
            tx_b = max(0, after.tx_bytes - before.tx_bytes)
            rx_b = max(0, after.rx_bytes - before.rx_bytes)
            tx_p = max(0, after.tx_packets - before.tx_packets)
            rx_p = max(0, after.rx_packets - before.rx_packets)
            # Use actual kernel interface TX bytes (avoiding TX+RX double-counting)
            agg_bytes = tx_b if tx_b > 0 else fallback_protocol_bytes
        else:
            tx_b = fallback_protocol_bytes
            rx_b = fallback_protocol_bytes
            tx_p = 0
            rx_p = 0
            agg_bytes = fallback_protocol_bytes

        agg_mb = float(agg_bytes) / (1024.0 * 1024.0)
        return TrafficDeltaMetrics(
            interface=self.interface,
            interface_tx_bytes_delta=tx_b,
            interface_rx_bytes_delta=rx_b,
            interface_tx_packets_delta=tx_p,
            interface_rx_packets_delta=rx_p,
            aggregate_network_traffic_bytes=agg_bytes,
            aggregate_network_traffic_mb=agg_mb,
        )
