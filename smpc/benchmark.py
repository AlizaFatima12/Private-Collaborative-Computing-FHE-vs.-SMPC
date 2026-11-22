"""End-to-End MP-SPDZ Multi-Party Computation Pipeline Runner and Metrics Collector."""

from pathlib import Path
import tempfile
import time
from typing import Any, Dict, Optional

from dataset.schemas import CollaborativeDataset
from dataset.validation import DatasetValidator
from smpc.protocol_config import SMPCProtocolConfig
from smpc.participant import SMPCParticipantManager
from smpc.secret_sharing import SecretSharingInspector
from smpc.beaver_multiplication import BeaverTripleAnalyzer
from smpc.computation import SMPCProgramCompiler


class SMPCBenchmarkEngine:
    """Executes and benchmarks 3-party collaborative computation using MP-SPDZ."""

    def __init__(
        self,
        config_path: Optional[str | Path] = None,
        work_dir: Optional[str | Path] = None,
    ):
        self.protocol_config = SMPCProtocolConfig(config_path)
        self.participant_manager = SMPCParticipantManager(self.protocol_config)
        self.compiler = SMPCProgramCompiler(self.protocol_config)
        if work_dir is not None:
            self.work_dir = Path(work_dir)
            self.work_dir.mkdir(parents=True, exist_ok=True)
        else:
            self.work_dir = Path(tempfile.gettempdir()) / "pcc_mpspdz_workdir"
            self.work_dir.mkdir(parents=True, exist_ok=True)

        self._port_offset = 0

    def run_pipeline(
        self,
        dataset: CollaborativeDataset,
        workload: str = "aggregation",
        protocol_key: Optional[str] = None,
        party_hosts: Optional[Dict[int, str]] = None,
    ) -> Dict[str, Any]:
        """
        Executes the 10-step MP-SPDZ 3-party pipeline on the provided dataset and workload,
        validating exact numerical equivalence against the plaintext reference.
        """
        DatasetValidator.validate_dataset(dataset)
        spec = self.protocol_config.get_protocol_spec(protocol_key)
        online_binary = self.protocol_config.resolve_binary_path(spec.binary_online)

        # Step 1: Ensure SSL certificates for encrypted inter-participant channels
        self.participant_manager.ensure_ssl_certificates(
            self.work_dir, num_parties=spec.num_parties
        )

        # Step 2: Purge any prior preprocessing material to strictly forbid Beaver triple reuse
        self.participant_manager.purge_preprocessing_material(self.work_dir)

        # Step 3: Provision private participant inputs (Input-P0-0, Input-P1-0, Input-P2-0)
        self.participant_manager.provision_private_inputs(
            self.work_dir, dataset, workload=workload
        )

        # Step 4: Compile the high-level MP-SPDZ program (.mpc)
        prog_name, compile_time_s = self.compiler.compile_program(
            self.work_dir, dataset, spec, workload=workload
        )

        # Step 5..8: Launch 3 independent participant processes concurrently
        # Rotate base port slightly to avoid TIME_WAIT collisions across back-to-back runs
        active_port = self.protocol_config.base_port + (self._port_offset % 500) * 10
        self._port_offset += 1

        opt_batch = max(100, min(dataset.total_transactions, spec.batch_size))
        extra_args = ["-b", str(opt_batch)]

        t_run0 = time.perf_counter()
        proc_output = self.participant_manager.execute_three_parties(
            binary_path=online_binary,
            program_name=prog_name,
            work_dir=self.work_dir,
            spec=spec,
            base_port=active_port,
            extra_args=extra_args,
            party_hosts=party_hosts,
        )
        wall_time_s = time.perf_counter() - t_run0

        # Step 9: Parse phase timings, network traffic, communication rounds, and Beaver triples
        exec_metrics = SMPCProgramCompiler.parse_execution_metrics(
            party_stdout=proc_output.party_stdout,
            party_stderr=proc_output.party_stderr,
            compile_time_s=compile_time_s,
            wall_time_s=wall_time_s,
            workload=workload,
            total_transactions=dataset.total_transactions,
        )

        all_logs = "\n".join(proc_output.party_stdout.values()) + "\n" + "\n".join(
            proc_output.party_stderr.values()
        )
        beaver_metrics = BeaverTripleAnalyzer.analyze_execution_logs(
            combined_logs=all_logs,
            workload=workload,
            num_transactions=dataset.total_transactions,
            batch_size=spec.batch_size,
            purged_before_run=True,
        )
        share_storage = SecretSharingInspector.compute_storage_metrics(
            dataset=dataset,
            spec=spec,
            workload=workload,
        )

        # Step 10: Validate reconstructed output against plaintext reference calculation
        if workload == "aggregation":
            expected_val = dataset.reference.total_sum
            computed_val = exec_metrics.computed_value
            is_correct = (
                computed_val == expected_val
                and exec_metrics.computed_count == dataset.reference.total_count
                and abs(exec_metrics.computed_average - dataset.reference.exact_average)
                < 1e-9
            )
        elif workload == "risk_multiplication":
            expected_val = dataset.reference.risk_total
            computed_val = exec_metrics.computed_value
            is_correct = computed_val == expected_val
        else:
            raise ValueError(f"Unknown workload '{workload}'")

        if not is_correct:
            raise AssertionError(
                f"SMPC correctness check failed for workload '{workload}'! "
                f"Expected {expected_val}, got {computed_val}"
            )

        result: Dict[str, Any] = {
            "technology": "SMPC",
            "protocol": spec.protocol_key,
            "scheme": spec.secret_sharing_scheme,
            "security_model": spec.security_model,
            "trust_assumption": spec.corruption_threshold,
            "workload": workload,
            "dataset_profile": dataset.profile_name,
            "num_transactions": dataset.total_transactions,
            "num_participants": dataset.num_parties if hasattr(dataset, "num_parties") else dataset.num_participants,
            "setup_time_s": round(exec_metrics.setup_time_s, 6),
            "key_generation_time_s": round(exec_metrics.setup_time_s, 6),
            "preprocessing_time_s": round(exec_metrics.preprocessing_time_s, 6),
            "input_protect_time_s": round(exec_metrics.input_sharing_time_s, 6),
            "encryption_time_s": round(exec_metrics.input_sharing_time_s, 6),
            "addition_time_s": round(exec_metrics.addition_time_s, 6),
            "multiplication_time_s": round(exec_metrics.multiplication_time_s, 6),
            "computation_time_s": round(exec_metrics.online_computation_time_s, 6),
            "output_recover_time_s": round(exec_metrics.output_reconstruction_time_s, 6),
            "decryption_time_s": round(exec_metrics.output_reconstruction_time_s, 6),
            "online_time_s": round(exec_metrics.online_total_time_s, 6),
            "total_execution_time_s": round(exec_metrics.total_execution_time_s, 6),
            "plaintext_size_bytes": share_storage.plaintext_bytes_total,
            "crypto_payload_size_bytes": share_storage.total_share_bytes_all_parties,
            "ciphertext_size_bytes": share_storage.total_share_bytes_all_parties,
            "expansion_ratio": round(
                share_storage.share_expansion_ratio_all_parties, 4
            ),
            "storage_overhead_bytes": share_storage.total_share_bytes_all_parties,
            "protocol_network_bytes": exec_metrics.global_network_bytes_sent,
            "communication_rounds": exec_metrics.communication_rounds_total,
            "beaver_triples_consumed": beaver_metrics.effective_consumed_triples,
            "peak_memory_mb": round(proc_output.peak_rss_mb_total, 3),
            "memory_delta_mb": round(proc_output.peak_rss_mb_total, 3),
            "computed_value": computed_val,
            "expected_value": expected_val,
            "computed_average": round(exec_metrics.computed_average, 6),
            "expected_average": round(dataset.reference.exact_average, 6),
            "correctness_verified": is_correct,
        }
        result.update(spec.to_metadata_dict())
        result.update(exec_metrics.to_metrics_dict())
        result.update(beaver_metrics.to_metrics_dict())
        result.update(share_storage.to_metrics_dict())

        return result
