"""End-to-End OpenFHE Pipeline Runner and Metrics Collector."""

import os
from pathlib import Path
import time
from typing import Any, Dict, Optional
import psutil

from dataset.schemas import CollaborativeDataset
from dataset.validation import DatasetValidator
from fhe.context import FHEContextManager
from fhe.key_generation import FHEKeyGenerator
from fhe.encryption import FHEEncryptor
from fhe.computation import FHEEvaluator
from fhe.decryption import FHEDecryptor
from fhe.noise_analysis import FHENoiseAnalyzer


class FHEBenchmarkEngine:
    """Executes and benchmarks the complete OpenFHE collaborative computation pipeline."""

    def __init__(self, config_path: Optional[str | Path] = None):
        self.context_manager = FHEContextManager(config_path)

    def run_pipeline(
        self,
        dataset: CollaborativeDataset,
        workload: str = "aggregation",
        profile_name: Optional[str] = None,
        enable_network_transport: bool = True,
    ) -> Dict[str, Any]:
        """
        Runs the 10-step OpenFHE pipeline on the provided 3-party dataset and workload,
        validating exact numerical equivalence against the plaintext reference.
        """
        proc = psutil.Process(os.getpid())
        mem_before = proc.memory_info().rss
        peak_rss = mem_before

        def _sample_mem():
            nonlocal peak_rss
            cur = proc.memory_info().rss
            if cur > peak_rss:
                peak_rss = cur

        t_pipeline_start = time.perf_counter()

        # Step 1 & 2: Initialize FHE Context & Configure Cryptographic Parameters
        ctx_bundle = self.context_manager.create_context(profile_name=profile_name)
        DatasetValidator.validate_dataset(
            dataset, plaintext_modulus=ctx_bundle.plaintext_modulus
        )
        _sample_mem()

        # Step 3: Collective Key Generation (or Single-Key Baseline)
        need_mult_key = workload == "risk_multiplication"
        key_material = FHEKeyGenerator.generate_keys(
            ctx_bundle,
            generate_eval_mult_key=need_mult_key,
            enable_network_transport=False,
        )
        _sample_mem()

        # Step 4 & 5: Encrypt Participant Datasets & Transmit Ciphertexts
        encrypted_bundle = FHEEncryptor.encrypt_dataset(
            ctx_bundle=ctx_bundle,
            key_material=key_material,
            dataset=dataset,
            workload=workload,
            enable_network_transport=enable_network_transport,
        )
        _sample_mem()

        # Step 6 & 7: Perform Homomorphic Computation on Ciphertexts
        comp_result = FHEEvaluator.evaluate(
            ctx_bundle=ctx_bundle,
            encrypted_bundle=encrypted_bundle,
        )
        _sample_mem()

        # Step 8: Threshold Distributed Decryption (or Single-Key Decryption)
        dec_result = FHEDecryptor.decrypt(
            ctx_bundle=ctx_bundle,
            key_material=key_material,
            comp_result=comp_result,
            total_count=dataset.total_transactions,
            enable_network_transport=enable_network_transport,
        )
        _sample_mem()

        # Step 9: Analyze Ciphertext Noise & Level Growth
        noise_metrics = FHENoiseAnalyzer.analyze_evaluation(
            ctx_bundle=ctx_bundle,
            comp_result=comp_result,
        )

        total_pipeline_time_s = time.perf_counter() - t_pipeline_start

        # Step 10: Validate Result Against Plaintext Reference Calculation
        if workload == "aggregation":
            expected_val = dataset.reference.total_sum
            computed_val = dec_result.aggregated_value
            is_correct = (
                computed_val == expected_val
                and dec_result.total_count == dataset.reference.total_count
                and abs(dec_result.exact_average - dataset.reference.exact_average) < 1e-9
            )
        elif workload == "risk_multiplication":
            expected_val = dataset.reference.risk_total
            computed_val = dec_result.aggregated_value
            is_correct = computed_val == expected_val
        else:
            raise ValueError(f"Unknown workload '{workload}'")

        if not is_correct:
            raise AssertionError(
                f"FHE correctness check failed for workload '{workload}'! "
                f"Expected {expected_val}, got {computed_val}"
            )

        setup_time_s = ctx_bundle.context_init_time_s + key_material.total_keygen_time_s
        online_time_s = (
            encrypted_bundle.total_encryption_time_s
            + comp_result.evaluation_time_s
            + dec_result.total_decryption_time_s
        )
        total_network_bytes = (
            encrypted_bundle.total_ciphertext_bytes
            + dec_result.partial_decryption_bytes
        )
        total_rounds = (
            1  # Ciphertext upload round from 3 participants
            + dec_result.decryption_rounds
        )
        storage_overhead_bytes = (
            encrypted_bundle.total_ciphertext_bytes
            + key_material.public_key_size_bytes
            + key_material.eval_mult_key_size_bytes
        )
        peak_memory_mb = float(peak_rss) / (1024.0 * 1024.0)
        memory_delta_mb = float(max(0, peak_rss - mem_before)) / (1024.0 * 1024.0)

        result: Dict[str, Any] = {
            "technology": "FHE",
            "protocol": ctx_bundle.profile_name,
            "scheme": ctx_bundle.scheme,
            "security_model": ctx_bundle.mode,
            "trust_assumption": ctx_bundle.trust_assumption,
            "workload": workload,
            "dataset_profile": dataset.profile_name,
            "num_transactions": dataset.total_transactions,
            "num_participants": dataset.num_participants,
            "setup_time_s": round(setup_time_s, 6),
            "key_generation_time_s": round(key_material.total_keygen_time_s, 6),
            "preprocessing_time_s": 0.0,
            "input_protect_time_s": round(encrypted_bundle.total_encryption_time_s, 6),
            "encryption_time_s": round(encrypted_bundle.total_encryption_time_s, 6),
            "addition_time_s": round(comp_result.homomorphic_addition_time_s, 6),
            "multiplication_time_s": round(
                comp_result.homomorphic_multiplication_time_s, 6
            ),
            "computation_time_s": round(comp_result.evaluation_time_s, 6),
            "output_recover_time_s": round(dec_result.total_decryption_time_s, 6),
            "decryption_time_s": round(dec_result.total_decryption_time_s, 6),
            "online_time_s": round(online_time_s, 6),
            "total_execution_time_s": round(total_pipeline_time_s, 6),
            "plaintext_size_bytes": encrypted_bundle.total_plaintext_bytes,
            "crypto_payload_size_bytes": encrypted_bundle.total_ciphertext_bytes,
            "ciphertext_size_bytes": encrypted_bundle.total_ciphertext_bytes,
            "expansion_ratio": round(encrypted_bundle.ciphertext_expansion_ratio, 4),
            "storage_overhead_bytes": storage_overhead_bytes,
            "protocol_network_bytes": total_network_bytes,
            "communication_rounds": total_rounds,
            "beaver_triples_consumed": 0,
            "peak_memory_mb": round(peak_memory_mb, 3),
            "memory_delta_mb": round(memory_delta_mb, 3),
            "computed_value": computed_val,
            "expected_value": expected_val,
            "computed_average": round(dec_result.exact_average, 6),
            "expected_average": round(dataset.reference.exact_average, 6),
            "correctness_verified": is_correct,
        }
        result.update(ctx_bundle.to_metadata_dict())
        result.update(key_material.to_metrics_dict())
        result.update(encrypted_bundle.to_metrics_dict())
        result.update(comp_result.to_metrics_dict())
        result.update(dec_result.to_metrics_dict())
        result.update(noise_metrics.to_metrics_dict())

        return result
