"""OpenFHE CryptoContext Initialization and Cryptographic Parameter Management."""

from dataclasses import dataclass
import math
from pathlib import Path
import time
from typing import Dict, Any, Optional
import yaml


def require_openfhe():
    """Imports and returns the official OpenFHE module, failing explicitly if unavailable."""
    try:
        import openfhe as fhe
        return fhe
    except ImportError as exc:
        raise RuntimeError(
            "CRITICAL: OpenFHE library ('openfhe') is not available in the active Python environment. "
            "Run inside the Ubuntu 24.04 Docker environment where openfhe==1.6.1.0.24.4 is installed. "
            "Unencrypted fallback or mock simulation is strictly prohibited."
        ) from exc


@dataclass
class FHEContextBundle:
    """Encapsulates an initialized OpenFHE CryptoContext and its resolved cryptographic parameters."""

    profile_name: str
    scheme: str
    mode: str
    num_parties: int
    crypto_context: Any
    parameters_obj: Any
    ring_dimension: int
    cyclotomic_order: int
    plaintext_modulus: int
    ciphertext_modulus_bits: float
    multiplicative_depth: int
    batch_size: int
    security_level: str
    multiparty_mode: str
    trust_assumption: str
    context_init_time_s: float
    network_transport_enabled: bool

    def to_metadata_dict(self) -> Dict[str, Any]:
        return {
            "fhe_profile": self.profile_name,
            "fhe_scheme": self.scheme,
            "fhe_mode": self.mode,
            "fhe_num_parties": self.num_parties,
            "fhe_ring_dimension": self.ring_dimension,
            "fhe_cyclotomic_order": self.cyclotomic_order,
            "fhe_plaintext_modulus": self.plaintext_modulus,
            "fhe_ciphertext_modulus_bits": round(self.ciphertext_modulus_bits, 2),
            "fhe_multiplicative_depth": self.multiplicative_depth,
            "fhe_batch_size": self.batch_size,
            "fhe_security_level": self.security_level,
            "fhe_multiparty_mode": self.multiparty_mode,
            "fhe_trust_assumption": self.trust_assumption,
            "fhe_context_init_time_s": round(self.context_init_time_s, 6),
        }


class FHEContextManager:
    """Configures and initializes OpenFHE CryptoContexts from config/fhe.yaml."""

    def __init__(self, config_path: Optional[str | Path] = None):
        if config_path is not None and Path(config_path).exists():
            cfg_file = Path(config_path)
        else:
            cfg_file = Path(__file__).resolve().parent.parent / "config" / "fhe.yaml"

        with open(cfg_file, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        self.fhe_config: Dict[str, Any] = raw.get("fhe", {})
        self.default_profile: str = str(
            self.fhe_config.get("default_profile", "threshold_bfv_128")
        )
        self.network_transport_enabled: bool = bool(
            self.fhe_config.get("network_transport_enabled", True)
        )
        self.profiles: Dict[str, Dict[str, Any]] = self.fhe_config.get("profiles", {})

    def create_context(
        self,
        profile_name: Optional[str] = None,
        override_params: Optional[Dict[str, Any]] = None,
    ) -> FHEContextBundle:
        """
        Initializes an OpenFHE CryptoContext for the specified parameter profile.
        """
        fhe = require_openfhe()
        active_profile = profile_name or self.default_profile
        if active_profile not in self.profiles:
            raise ValueError(
                f"Unknown FHE profile '{active_profile}'. Available: {list(self.profiles.keys())}"
            )

        prof = dict(self.profiles[active_profile])
        if override_params:
            prof.update(override_params)

        scheme = str(prof.get("scheme", "BFVRNS")).upper()
        mode = str(prof.get("mode", "threshold_multiparty"))
        num_parties = int(prof.get("num_parties", 3))
        mult_depth = int(prof.get("multiplicative_depth", 2))
        batch_size = int(prof.get("batch_size", 4096))
        sec_level_str = str(prof.get("security_level", "HEStd_128_classic"))
        trust_assumption = str(prof.get("trust_assumption", ""))
        mp_mode_str = str(prof.get("multiparty_mode", "NOISE_FLOODING_MULTIPARTY"))

        # Clear any lingering static keys/contexts before creating a fresh context
        fhe.ClearEvalMultKeys()

        t0 = time.perf_counter()

        sec_level_map = {
            "HEStd_128_classic": fhe.SecurityLevel.HEStd_128_classic,
            "HEStd_192_classic": fhe.SecurityLevel.HEStd_192_classic,
            "HEStd_256_classic": fhe.SecurityLevel.HEStd_256_classic,
            "HEStd_NotSet": fhe.SecurityLevel.HEStd_NotSet,
        }
        sec_enum = sec_level_map.get(sec_level_str, fhe.SecurityLevel.HEStd_128_classic)

        if scheme == "BFVRNS":
            params = fhe.CCParamsBFVRNS()
            pt_modulus = int(prof.get("plaintext_modulus", 536903681))
            params.SetPlaintextModulus(pt_modulus)
            params.SetMultiplicativeDepth(mult_depth)
            params.SetSecurityLevel(sec_enum)
            params.SetBatchSize(batch_size)
            params.SetStandardDeviation(float(prof.get("standard_deviation", 3.2)))
            params.SetSecretKeyDist(fhe.UNIFORM_TERNARY)
            params.SetScalingModSize(int(prof.get("scaling_mod_size", 60)))
            params.SetDigitSize(int(prof.get("digit_size", 30)))

            if str(prof.get("multiplication_technique", "HPSPOVERQLEVELED")) == "HPSPOVERQLEVELED":
                params.SetMultiplicationTechnique(fhe.HPSPOVERQLEVELED)

            if mode == "threshold_multiparty":
                params.SetThresholdNumOfParties(num_parties)
                if mp_mode_str == "NOISE_FLOODING_MULTIPARTY":
                    params.SetMultipartyMode(fhe.NOISE_FLOODING_MULTIPARTY)
                else:
                    params.SetMultipartyMode(fhe.FIXED_NOISE_MULTIPARTY)

            cc = fhe.GenCryptoContext(params)
            cc.Enable(fhe.PKESchemeFeature.PKE)
            cc.Enable(fhe.PKESchemeFeature.KEYSWITCH)
            cc.Enable(fhe.PKESchemeFeature.LEVELEDSHE)
            cc.Enable(fhe.PKESchemeFeature.ADVANCEDSHE)
            if mode == "threshold_multiparty":
                cc.Enable(fhe.PKESchemeFeature.MULTIPARTY)

        elif scheme == "CKKSRNS":
            params = fhe.CCParamsCKKSRNS()
            params.SetSecretKeyDist(fhe.SecretKeyDist.UNIFORM_TERNARY)
            params.SetSecurityLevel(sec_enum)
            if sec_enum == fhe.SecurityLevel.HEStd_NotSet and "ring_dimension" in prof:
                params.SetRingDim(int(prof["ring_dimension"]))
            params.SetScalingModSize(int(prof.get("scaling_mod_size", 59)))
            params.SetFirstModSize(int(prof.get("first_mod_size", 60)))
            params.SetScalingTechnique(fhe.ScalingTechnique.FLEXIBLEAUTO)
            params.SetBatchSize(batch_size)

            level_budget = list(prof.get("level_budget", [4, 4]))
            levels_after = int(prof.get("levels_after_bootstrap", 10))
            if mode == "bootstrapping_experiment":
                mult_depth = levels_after + int(
                    fhe.CryptoContext.GetBootstrapDepth(
                        level_budget, fhe.SecretKeyDist.UNIFORM_TERNARY
                    )
                )
            params.SetMultiplicativeDepth(mult_depth)

            cc = fhe.GenCryptoContext(params)
            cc.Enable(fhe.PKESchemeFeature.PKE)
            cc.Enable(fhe.PKESchemeFeature.KEYSWITCH)
            cc.Enable(fhe.PKESchemeFeature.LEVELEDSHE)
            cc.Enable(fhe.PKESchemeFeature.ADVANCEDSHE)
            if mode == "bootstrapping_experiment":
                cc.Enable(fhe.PKESchemeFeature.FHE)
                cc.EvalBootstrapSetup(level_budget, [0, 0], batch_size)
            pt_modulus = 0
        else:
            raise ValueError(f"Unsupported FHE scheme: {scheme}")

        init_time_s = time.perf_counter() - t0

        cyc_order = int(cc.GetCyclotomicOrder())
        ring_dim = int(cc.GetRingDimension()) if hasattr(cc, "GetRingDimension") else (cyc_order // 2)
        actual_pt_mod = int(cc.GetPlaintextModulus()) if scheme == "BFVRNS" else pt_modulus
        q_mod = cc.GetModulus()
        log2_q = float(math.log2(q_mod)) if q_mod > 0 else 0.0

        return FHEContextBundle(
            profile_name=active_profile,
            scheme=scheme,
            mode=mode,
            num_parties=num_parties,
            crypto_context=cc,
            parameters_obj=params,
            ring_dimension=ring_dim,
            cyclotomic_order=cyc_order,
            plaintext_modulus=actual_pt_mod,
            ciphertext_modulus_bits=log2_q,
            multiplicative_depth=mult_depth,
            batch_size=batch_size,
            security_level=sec_level_str,
            multiparty_mode=mp_mode_str if mode == "threshold_multiparty" else "SINGLE_KEY",
            trust_assumption=trust_assumption,
            context_init_time_s=init_time_s,
            network_transport_enabled=self.network_transport_enabled,
        )
