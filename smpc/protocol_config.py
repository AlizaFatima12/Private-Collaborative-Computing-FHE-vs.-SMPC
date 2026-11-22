"""MP-SPDZ Protocol Configuration, Security Model Metadata, and Binary Discovery."""

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
import yaml


@dataclass(frozen=True)
class SMPCProtocolSpec:
    """Complete cryptographic specification for a 3-party MP-SPDZ protocol."""

    protocol_key: str
    description: str
    binary_online: str
    binary_offline: str
    domain: str
    compiler_flags: List[str]
    secret_sharing_scheme: str
    multiplication_mechanism: str
    security_model: str
    corruption_threshold: str
    supports_offline_file_mode: bool
    batch_size: int
    num_parties: int
    field_bit_length: int

    def to_metadata_dict(self) -> Dict[str, Any]:
        return {
            "smpc_protocol": self.protocol_key,
            "smpc_binary_online": self.binary_online,
            "smpc_binary_offline": self.binary_offline,
            "smpc_domain": self.domain,
            "smpc_secret_sharing_scheme": self.secret_sharing_scheme,
            "smpc_multiplication_mechanism": self.multiplication_mechanism,
            "smpc_security_model": self.security_model,
            "smpc_corruption_threshold": self.corruption_threshold,
            "smpc_num_parties": self.num_parties,
            "smpc_field_bit_length": self.field_bit_length,
        }


class SMPCProtocolConfig:
    """Loads MP-SPDZ configuration from config/smpc.yaml and locates protocol binaries."""

    def __init__(self, config_path: Optional[str | Path] = None):
        if config_path is not None and Path(config_path).exists():
            cfg_file = Path(config_path)
        else:
            cfg_file = Path(__file__).resolve().parent.parent / "config" / "smpc.yaml"

        with open(cfg_file, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        self.smpc_cfg: Dict[str, Any] = raw.get("smpc", {})
        self.default_protocol: str = str(
            self.smpc_cfg.get("default_protocol", "semi_dishonest_majority_3p")
        )
        self.num_parties: int = int(self.smpc_cfg.get("num_parties", 3))
        self.base_port: int = int(self.smpc_cfg.get("base_port", 15000))
        self.field_bit_length: int = int(self.smpc_cfg.get("field_bit_length", 64))
        self.separate_preprocessing: bool = bool(
            self.smpc_cfg.get("separate_preprocessing_phase", True)
        )
        self.allow_triple_reuse: bool = bool(
            self.smpc_cfg.get("allow_triple_reuse", False)
        )
        if self.allow_triple_reuse:
            raise ValueError(
                "SECURITY VIOLATION: Beaver triple reuse is strictly forbidden in SMPC configuration."
            )

        self.protocols_raw: Dict[str, Dict[str, Any]] = self.smpc_cfg.get(
            "protocols", {}
        )

    def resolve_install_dir(self) -> Path:
        """Resolves and validates the MP-SPDZ installation directory."""
        candidates = [
            os.environ.get("PCC_MPSPDZ_DIR", ""),
            str(self.smpc_cfg.get("install_dir", "/opt/mp-spdz")),
            "/opt/mp-spdz",
            "/opt/mp-spdz-0.4.4",
        ]
        for cand in candidates:
            if not cand:
                continue
            p = Path(cand)
            if p.exists() and (p / "compile.py").exists():
                return p
        raise RuntimeError(
            "CRITICAL: MP-SPDZ cryptographic backend not found in /opt/mp-spdz or PCC_MPSPDZ_DIR. "
            "Run inside the Ubuntu 24.04 Docker container where MP-SPDZ v0.4.4 is installed. "
            "Homemade or unencrypted fallback is strictly prohibited."
        )

    def get_protocol_spec(self, protocol_key: Optional[str] = None) -> SMPCProtocolSpec:
        key = protocol_key or self.default_protocol
        if key not in self.protocols_raw:
            raise ValueError(
                f"Unknown SMPC protocol '{key}'. Available: {list(self.protocols_raw.keys())}"
            )
        p = self.protocols_raw[key]
        return SMPCProtocolSpec(
            protocol_key=key,
            description=str(p.get("description", "")),
            binary_online=str(p["binary_online"]),
            binary_offline=str(p.get("binary_offline", "")),
            domain=str(p.get("domain", "prime_field")),
            compiler_flags=[str(x) for x in p.get("compiler_flags", ["-F", "64"])],
            secret_sharing_scheme=str(p.get("secret_sharing_scheme", "")),
            multiplication_mechanism=str(p.get("multiplication_mechanism", "")),
            security_model=str(p.get("security_model", "Semi-honest")),
            corruption_threshold=str(p.get("corruption_threshold", "")),
            supports_offline_file_mode=bool(p.get("supports_offline_file_mode", False)),
            batch_size=int(p.get("batch_size", 10000)),
            num_parties=self.num_parties,
            field_bit_length=self.field_bit_length,
        )

    def resolve_binary_path(self, binary_name: str) -> Path:
        """Locates an MP-SPDZ virtual machine executable (.x)."""
        root = self.resolve_install_dir()
        direct = root / binary_name
        if direct.exists():
            return direct
        for arch_dir in ("Linux-adx", "Linux-avx2", "Linux-amd64"):
            cand = root / "bin" / arch_dir / binary_name
            if cand.exists():
                return cand
        raise RuntimeError(
            f"MP-SPDZ protocol binary '{binary_name}' not found under {root}"
        )
