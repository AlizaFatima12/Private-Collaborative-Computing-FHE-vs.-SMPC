"""OpenFHE Threshold Multiparty Decryption and Single-Key Baseline Decryption."""

from dataclasses import dataclass
import time
from typing import Any, Dict, List
from fhe.context import FHEContextBundle, require_openfhe
from fhe.key_generation import FHEKeyMaterial, transmit_payload_over_tcp
from fhe.computation import FHEComputationResult


@dataclass
class FHEDecryptionResult:
    """Holds decrypted computation outputs, partial decryption metrics, and network timings."""

    workload: str
    mode: str
    aggregated_value: int
    total_count: int
    exact_average: float
    integer_average: int
    decryption_cpu_time_s: float
    decryption_network_time_s: float
    total_decryption_time_s: float
    partial_decryption_bytes: int
    decryption_rounds: int

    def to_metrics_dict(self) -> Dict[str, Any]:
        return {
            "fhe_decryption_cpu_time_s": round(self.decryption_cpu_time_s, 6),
            "fhe_decryption_network_time_s": round(self.decryption_network_time_s, 6),
            "fhe_total_decryption_time_s": round(self.total_decryption_time_s, 6),
            "fhe_partial_decryption_bytes": self.partial_decryption_bytes,
            "fhe_decryption_rounds": self.decryption_rounds,
        }


class FHEDecryptor:
    """Performs 3-party threshold distributed decryption or single-key baseline decryption."""

    @staticmethod
    def decrypt(
        ctx_bundle: FHEContextBundle,
        key_material: FHEKeyMaterial,
        comp_result: FHEComputationResult,
        total_count: int,
        enable_network_transport: bool = True,
    ) -> FHEDecryptionResult:
        fhe = require_openfhe()
        cc = ctx_bundle.crypto_context
        res_ct = comp_result.result_ciphertext
        active_slots = comp_result.active_slot_width

        t_total_start = time.perf_counter()

        if ctx_bundle.mode == "threshold_multiparty":
            sk_shares = key_material.secret_key_shares
            if len(sk_shares) != 3:
                raise ValueError(
                    f"Threshold decryption requires 3 secret key shares; got {len(sk_shares)}"
                )

            t_cpu0 = time.perf_counter()
            # Party A computes lead partial decryption (includes noise-flooding term in NOISE_FLOODING_MULTIPARTY)
            part_a = cc.MultipartyDecryptLead([res_ct], sk_shares[0])[0]
            # Party B and Party C compute main partial decryptions
            part_b = cc.MultipartyDecryptMain([res_ct], sk_shares[1])[0]
            part_c = cc.MultipartyDecryptMain([res_ct], sk_shares[2])[0]

            partials: List[Any] = [part_a, part_b, part_c]
            serialized_partials = [fhe.Serialize(p, fhe.BINARY) for p in partials]
            partial_bytes = sum(len(b) for b in serialized_partials)

            # Combine partial decryptions
            pt_fused = cc.MultipartyDecryptFusion(partials)
            pt_fused.SetLength(active_slots)
            slot_values = [int(v) for v in pt_fused.GetPackedValue()[:active_slots]]
            cpu_time_s = time.perf_counter() - t_cpu0

            net_time_s = 0.0
            if enable_network_transport and ctx_bundle.network_transport_enabled:
                _, net_time_s = transmit_payload_over_tcp(serialized_partials)

            rounds = 1

        else:
            sk = key_material.secret_key_shares[0]
            t_cpu0 = time.perf_counter()
            pt_dec = cc.Decrypt(res_ct, sk)
            pt_dec.SetLength(active_slots)
            slot_values = [int(v) for v in pt_dec.GetPackedValue()[:active_slots]]
            cpu_time_s = time.perf_counter() - t_cpu0

            res_blob = fhe.Serialize(res_ct, fhe.BINARY)
            partial_bytes = len(res_blob)
            net_time_s = 0.0
            if enable_network_transport and ctx_bundle.network_transport_enabled:
                _, net_time_s = transmit_payload_over_tcp([res_blob])
            rounds = 1

        total_dec_time_s = time.perf_counter() - t_total_start
        aggregated_val = int(sum(slot_values))
        exact_avg = float(aggregated_val) / float(total_count) if total_count > 0 else 0.0
        int_avg = aggregated_val // total_count if total_count > 0 else 0

        return FHEDecryptionResult(
            workload=comp_result.workload,
            mode=ctx_bundle.mode,
            aggregated_value=aggregated_val,
            total_count=total_count,
            exact_average=exact_avg,
            integer_average=int_avg,
            decryption_cpu_time_s=cpu_time_s,
            decryption_network_time_s=net_time_s,
            total_decryption_time_s=total_dec_time_s,
            partial_decryption_bytes=partial_bytes,
            decryption_rounds=rounds,
        )
