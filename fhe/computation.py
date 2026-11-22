"""OpenFHE Homomorphic Evaluation for Aggregation and Multiplicative Risk Workloads."""

from dataclasses import dataclass
import time
from typing import Any, Dict, List
from fhe.context import FHEContextBundle
from fhe.encryption import EncryptedDatasetBundle


@dataclass
class FHEComputationResult:
    """Holds the evaluated result ciphertext and fine-grained homomorphic operation timings."""

    workload: str
    result_ciphertext: Any
    initial_sample_ciphertext: Any
    homomorphic_addition_time_s: float
    homomorphic_multiplication_time_s: float
    evaluation_time_s: float
    num_homomorphic_additions: int
    num_homomorphic_multiplications: int
    active_slot_width: int

    def to_metrics_dict(self) -> Dict[str, Any]:
        return {
            "fhe_homomorphic_addition_time_s": round(self.homomorphic_addition_time_s, 6),
            "fhe_homomorphic_multiplication_time_s": round(
                self.homomorphic_multiplication_time_s, 6
            ),
            "fhe_evaluation_time_s": round(self.evaluation_time_s, 6),
            "fhe_num_homomorphic_additions": self.num_homomorphic_additions,
            "fhe_num_homomorphic_multiplications": self.num_homomorphic_multiplications,
        }


class FHEEvaluator:
    """Executes homomorphic circuits directly on OpenFHE ciphertexts without secret key access."""

    @staticmethod
    def evaluate(
        ctx_bundle: FHEContextBundle,
        encrypted_bundle: EncryptedDatasetBundle,
    ) -> FHEComputationResult:
        cc = ctx_bundle.crypto_context
        workload = encrypted_bundle.workload

        t_eval_start = time.perf_counter()
        add_time_s = 0.0
        mult_time_s = 0.0
        num_adds = 0
        num_mults = 0
        acc_ct = None
        initial_sample_ct = None

        if workload == "aggregation":
            all_amt_cts: List[Any] = []
            for p_ct in encrypted_bundle.participant_ciphertexts.values():
                all_amt_cts.extend(p_ct.amount_ciphertexts)

            if not all_amt_cts:
                raise ValueError("No encrypted amount ciphertexts provided for aggregation.")

            initial_sample_ct = all_amt_cts[0]
            acc_ct = all_amt_cts[0]
            for ct in all_amt_cts[1:]:
                t_a0 = time.perf_counter()
                acc_ct = cc.EvalAdd(acc_ct, ct)
                add_time_s += time.perf_counter() - t_a0
                num_adds += 1

        elif workload == "risk_multiplication":
            product_cts: List[Any] = []
            for p_ct in encrypted_bundle.participant_ciphertexts.values():
                if len(p_ct.amount_ciphertexts) != len(p_ct.risk_ciphertexts):
                    raise ValueError(
                        f"Mismatched amount and risk ciphertext counts for {p_ct.participant_id}"
                    )
                for ct_x, ct_w in zip(p_ct.amount_ciphertexts, p_ct.risk_ciphertexts):
                    if initial_sample_ct is None:
                        initial_sample_ct = ct_x
                    t_m0 = time.perf_counter()
                    prod_ct = cc.EvalMult(ct_x, ct_w)
                    mult_time_s += time.perf_counter() - t_m0
                    num_mults += 1
                    product_cts.append(prod_ct)

            if not product_cts:
                raise ValueError("No product ciphertexts computed for risk_multiplication.")

            acc_ct = product_cts[0]
            for prod_ct in product_cts[1:]:
                t_a0 = time.perf_counter()
                acc_ct = cc.EvalAdd(acc_ct, prod_ct)
                add_time_s += time.perf_counter() - t_a0
                num_adds += 1

        else:
            raise ValueError(f"Unsupported FHE workload: '{workload}'")

        eval_time_s = time.perf_counter() - t_eval_start

        return FHEComputationResult(
            workload=workload,
            result_ciphertext=acc_ct,
            initial_sample_ciphertext=initial_sample_ct,
            homomorphic_addition_time_s=add_time_s,
            homomorphic_multiplication_time_s=mult_time_s,
            evaluation_time_s=eval_time_s,
            num_homomorphic_additions=num_adds,
            num_homomorphic_multiplications=num_mults,
            active_slot_width=encrypted_bundle.active_slot_width,
        )
