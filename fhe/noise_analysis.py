"""OpenFHE Noise Growth, RNS Modulus Budget, and Isolated Bootstrapping Overhead Analysis."""

from dataclasses import dataclass
import math
import time
from typing import Any, Dict, Optional
from fhe.context import FHEContextBundle, FHEContextManager, require_openfhe
from fhe.computation import FHEComputationResult


@dataclass
class FHENoiseMetrics:
    """Noise, level, and ciphertext polynomial structure measurements for an OpenFHE evaluation."""

    initial_level: int
    final_level: int
    initial_noise_scale_deg: int
    final_noise_scale_deg: int
    num_ciphertext_polynomials: int
    total_modulus_bits: float
    plaintext_modulus_bits: float
    total_noise_capacity_bits: float
    estimated_noise_consumed_bits: float
    estimated_remaining_noise_budget_bits: float

    def to_metrics_dict(self) -> Dict[str, Any]:
        return {
            "fhe_initial_level": self.initial_level,
            "fhe_final_level": self.final_level,
            "fhe_initial_noise_scale_deg": self.initial_noise_scale_deg,
            "fhe_final_noise_scale_deg": self.final_noise_scale_deg,
            "fhe_num_ciphertext_polynomials": self.num_ciphertext_polynomials,
            "fhe_total_modulus_bits": round(self.total_modulus_bits, 2),
            "fhe_plaintext_modulus_bits": round(self.plaintext_modulus_bits, 2),
            "fhe_total_noise_capacity_bits": round(self.total_noise_capacity_bits, 2),
            "fhe_estimated_noise_consumed_bits": round(self.estimated_noise_consumed_bits, 2),
            "fhe_estimated_remaining_noise_budget_bits": round(
                self.estimated_remaining_noise_budget_bits, 2
            ),
        }


class FHENoiseAnalyzer:
    """Analyzes ciphertext noise/level evolution and provides an isolated bootstrapping benchmark."""

    @staticmethod
    def analyze_evaluation(
        ctx_bundle: FHEContextBundle,
        comp_result: FHEComputationResult,
    ) -> FHENoiseMetrics:
        init_ct = comp_result.initial_sample_ciphertext
        res_ct = comp_result.result_ciphertext

        init_level = int(init_ct.GetLevel()) if hasattr(init_ct, "GetLevel") else 0
        final_level = int(res_ct.GetLevel()) if hasattr(res_ct, "GetLevel") else 0
        init_nsd = int(init_ct.GetNoiseScaleDeg()) if hasattr(init_ct, "GetNoiseScaleDeg") else 1
        final_nsd = int(res_ct.GetNoiseScaleDeg()) if hasattr(res_ct, "GetNoiseScaleDeg") else 1
        num_elems = (
            int(res_ct.NumberCiphertextElements())
            if hasattr(res_ct, "NumberCiphertextElements")
            else 2
        )

        log2_q = float(ctx_bundle.ciphertext_modulus_bits)
        log2_p = (
            float(math.log2(ctx_bundle.plaintext_modulus))
            if ctx_bundle.plaintext_modulus > 1
            else 0.0
        )

        # In BFV-RNS, decryption succeeds while ||e||_infty < Q / (2p), giving total capacity log2(Q) - log2(p) - 1
        total_capacity = max(0.0, log2_q - log2_p - 1.0)

        # Fresh RLWE encryption noise with sigma=3.2 and ternary secret key in ring dimension n:
        # Fresh error bound ~ 6 * sigma * sqrt(n) -> ~19-21 bits
        n_ring = max(1, ctx_bundle.ring_dimension)
        fresh_noise_bits = math.log2(6.0 * 3.2 * math.sqrt(n_ring))

        # Additive noise grows logarithmically with number of additions: +0.5 * log2(N_add + 1)
        add_noise_bits = 0.5 * math.log2(comp_result.num_homomorphic_additions + 1)

        # Multiplicative noise in BFV-RNS grows by ~(log2(p) + 0.5*log2(n) + 4) bits per multiplicative depth consumed
        has_mult = 1 if comp_result.num_homomorphic_multiplications > 0 else 0
        mult_noise_bits = has_mult * (log2_p + 0.5 * math.log2(n_ring) + 4.0)

        # Threshold NOISE_FLOODING_MULTIPARTY reserves flooding margin during distributed decryption
        consumed = fresh_noise_bits + add_noise_bits + mult_noise_bits
        remaining = max(0.0, total_capacity - consumed)

        return FHENoiseMetrics(
            initial_level=init_level,
            final_level=final_level,
            initial_noise_scale_deg=init_nsd,
            final_noise_scale_deg=final_nsd,
            num_ciphertext_polynomials=num_elems,
            total_modulus_bits=log2_q,
            plaintext_modulus_bits=log2_p,
            total_noise_capacity_bits=total_capacity,
            estimated_noise_consumed_bits=consumed,
            estimated_remaining_noise_budget_bits=remaining,
        )

    @staticmethod
    def run_isolated_bootstrapping_benchmark(
        config_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Executes an isolated OpenFHE bootstrapping benchmark using the 'bootstrapping_ckks_exp'
        profile to measure bootstrapping key generation and EvalBootstrap execution overhead
        separately from standard BFV evaluation runs.
        """
        fhe = require_openfhe()
        ctx_mgr = FHEContextManager(config_path)
        ctx_bundle = ctx_mgr.create_context("bootstrapping_ckks_exp")
        cc = ctx_bundle.crypto_context

        num_slots = ctx_bundle.batch_size
        t_kg0 = time.perf_counter()
        kp = cc.KeyGen()
        cc.EvalMultKeyGen(kp.secretKey)
        cc.EvalBootstrapKeyGen(kp.secretKey, num_slots)
        boot_keygen_time_s = time.perf_counter() - t_kg0

        vec = [0.25, 0.5, 0.75, 1.0, 2.0, 3.0, 4.0, 5.0][:num_slots]
        pt = cc.MakeCKKSPackedPlaintext(vec, 1, ctx_bundle.multiplicative_depth - 1)
        pt.SetLength(len(vec))
        ct_exhausted = cc.Encrypt(kp.publicKey, pt)

        levels_before = int(ctx_bundle.multiplicative_depth - ct_exhausted.GetLevel())

        t_boot0 = time.perf_counter()
        ct_refreshed = cc.EvalBootstrap(ct_exhausted)
        bootstrap_eval_time_s = time.perf_counter() - t_boot0

        levels_after = int(
            ctx_bundle.multiplicative_depth
            - ct_refreshed.GetLevel()
            - (ct_refreshed.GetNoiseScaleDeg() - 1)
        )

        pt_out = cc.Decrypt(ct_refreshed, kp.secretKey)
        pt_out.SetLength(len(vec))
        decoded = [float(v.real) for v in pt_out.GetCKKSPackedValue()[: len(vec)]]
        max_abs_err = max(abs(a - b) for a, b in zip(vec, decoded))

        ct_bytes = len(fhe.Serialize(ct_refreshed, fhe.BINARY))

        return {
            "profile": "bootstrapping_ckks_exp",
            "ring_dimension": ctx_bundle.ring_dimension,
            "ciphertext_modulus_bits": round(ctx_bundle.ciphertext_modulus_bits, 2),
            "multiplicative_depth_total": ctx_bundle.multiplicative_depth,
            "bootstrap_keygen_time_s": round(boot_keygen_time_s, 6),
            "bootstrap_eval_time_s": round(bootstrap_eval_time_s, 6),
            "levels_remaining_before_bootstrap": levels_before,
            "levels_remaining_after_bootstrap": levels_after,
            "refreshed_ciphertext_bytes": ct_bytes,
            "max_approximation_error": round(max_abs_err, 6),
        }
