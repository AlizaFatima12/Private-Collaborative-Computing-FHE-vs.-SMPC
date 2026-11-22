"""Central Benchmark Orchestrator Runner for FHE vs. SMPC Evaluation."""

import argparse
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import time
from typing import Any, Dict, Optional

from dataset.generator import DatasetGenerator
from dataset.schemas import CollaborativeDataset
from fhe.benchmark import FHEBenchmarkEngine
from fhe.noise_analysis import FHENoiseAnalyzer
from smpc.benchmark import SMPCBenchmarkEngine
from networking.network_emulator import NetworkEmulator
from networking.traffic_monitor import NetworkTrafficMonitor
from orchestrator.experiment_manager import ExperimentManager, ExperimentSpec
from orchestrator.metrics_collector import MetricsCollector
from orchestrator.validation import CrossEngineValidator


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
LOGGER = logging.getLogger("orchestrator.runner")


class BenchmarkRunner:
    """Executes the full orchestrated FHE vs. SMPC experimental suite."""

    def __init__(
        self,
        config_dir: Optional[str | Path] = None,
        results_dir: Optional[str | Path] = None,
    ):
        root_dir = Path(__file__).resolve().parent.parent
        self.config_dir = Path(config_dir) if config_dir else (root_dir / "config")
        self.results_dir = Path(results_dir) if results_dir else (root_dir / "results")

        self.exp_manager = ExperimentManager(self.config_dir)
        self.dataset_generator = DatasetGenerator(self.config_dir / "experiment.yaml")
        self.fhe_engine = FHEBenchmarkEngine(self.config_dir / "fhe.yaml")
        self.smpc_engine = SMPCBenchmarkEngine(self.config_dir / "smpc.yaml")
        self.network_emulator = NetworkEmulator(self.config_dir / "network.yaml")
        self.metrics_collector = MetricsCollector(
            results_dir=self.results_dir,
            confidence_level=self.exp_manager.confidence_level,
        )
        self._dataset_cache: Dict[str, CollaborativeDataset] = {}

    def _get_dataset(self, profile_name: str) -> CollaborativeDataset:
        if profile_name not in self._dataset_cache:
            # Materialize full TransactionRecord objects for small/medium/large, arrays for all
            self._dataset_cache[profile_name] = self.dataset_generator.generate(
                profile_or_size=profile_name,
                materialize_records=True,
            )
        return self._dataset_cache[profile_name]

    def run_single_spec(
        self,
        spec: ExperimentSpec,
        warmup_runs: int,
        repetitions: int,
        spec_index: int,
        total_specs: int,
    ) -> None:
        dataset = self._get_dataset(spec.dataset_profile)

        with self.network_emulator.emulate_profile(
            latency_profile=spec.latency_profile,
            bandwidth_profile=spec.bandwidth_profile,
            packet_loss_pct=spec.packet_loss_pct,
        ) as net_state:
            LOGGER.info(
                "[%d/%d] Dim=%s | DS=%s (N=%d) | WL=%s | Net=%s (%gms, RTT=%gms) / %s (%gMbps) | FHE=%s vs SMPC=%s",
                spec_index,
                total_specs,
                spec.dimension,
                spec.dataset_profile,
                dataset.total_transactions,
                spec.workload,
                spec.latency_profile,
                net_state.configured_one_way_delay_ms,
                round(net_state.measured_rtt_ms, 2),
                spec.bandwidth_profile,
                net_state.configured_bandwidth_mbps,
                spec.fhe_profile,
                spec.smpc_protocol,
            )

            total_loops = warmup_runs + repetitions
            for loop_idx in range(total_loops):
                is_warmup = loop_idx < warmup_runs
                rep_num = (loop_idx - warmup_runs + 1) if not is_warmup else 0

                # 1. Execute OpenFHE Pipeline & Measure Actual Kernel Interface Traffic
                fhe_traffic_mon = NetworkTrafficMonitor(self.network_emulator.interface)
                fhe_traffic_mon.start()
                fhe_res = self.fhe_engine.run_pipeline(
                    dataset=dataset,
                    workload=spec.workload,
                    profile_name=spec.fhe_profile,
                    enable_network_transport=True,
                )
                fhe_traffic = fhe_traffic_mon.stop(
                    fallback_protocol_bytes=int(fhe_res["protocol_network_bytes"])
                )
                fhe_res.update(net_state.to_metrics_dict())
                fhe_res.update(fhe_traffic.to_metrics_dict())

                # 2. Execute MP-SPDZ Pipeline & Measure Actual Kernel Interface Traffic
                smpc_traffic_mon = NetworkTrafficMonitor(self.network_emulator.interface)
                smpc_traffic_mon.start()
                smpc_res = self.smpc_engine.run_pipeline(
                    dataset=dataset,
                    workload=spec.workload,
                    protocol_key=spec.smpc_protocol,
                )
                smpc_traffic = smpc_traffic_mon.stop(
                    fallback_protocol_bytes=int(smpc_res["protocol_network_bytes"])
                )
                smpc_res.update(net_state.to_metrics_dict())
                smpc_res.update(smpc_traffic.to_metrics_dict())

                # 3. Validate Numerical Equivalence & Attach Trust Assumption Equivalence Label
                CrossEngineValidator.validate_pair(
                    dataset=dataset,
                    workload=spec.workload,
                    fhe_result=fhe_res,
                    smpc_result=smpc_res,
                )

                # 4. Record Trials
                self.metrics_collector.add_trial_record(
                    record=fhe_res,
                    experiment_dimension=spec.dimension,
                    repetition_index=rep_num,
                    is_warmup=is_warmup,
                )
                self.metrics_collector.add_trial_record(
                    record=smpc_res,
                    experiment_dimension=spec.dimension,
                    repetition_index=rep_num,
                    is_warmup=is_warmup,
                )

    def run_suite(
        self,
        mode: Optional[str] = None,
        override_warmup: Optional[int] = None,
        override_repetitions: Optional[int] = None,
        run_bootstrapping_study: bool = True,
    ) -> Dict[str, Any]:
        active_mode = (
            mode
            or os.environ.get("PCC_EXPERIMENT_MODE")
            or self.exp_manager.default_mode
        )
        warmup, reps = self.exp_manager.get_run_counts(active_mode)
        if override_warmup is not None:
            warmup = override_warmup
        if override_repetitions is not None:
            reps = override_repetitions

        schedule = self.exp_manager.build_schedule(active_mode)
        LOGGER.info(
            "Starting Private Collaborative Computing Benchmark Suite (mode=%s, configs=%d, warmup=%d, repetitions=%d)",
            active_mode,
            len(schedule),
            warmup,
            reps,
        )

        t_suite_start = time.perf_counter()
        for idx, spec in enumerate(schedule, start=1):
            self.run_single_spec(
                spec=spec,
                warmup_runs=warmup,
                repetitions=reps,
                spec_index=idx,
                total_specs=len(schedule),
            )

        boot_metrics: Dict[str, Any] = {}
        if run_bootstrapping_study:
            LOGGER.info("Running isolated OpenFHE CKKS Bootstrapping overhead experiment...")
            boot_metrics = FHENoiseAnalyzer.run_isolated_bootstrapping_benchmark(
                str(self.config_dir / "fhe.yaml")
            )
            LOGGER.info("Isolated Bootstrapping Result: %s", boot_metrics)

        suite_elapsed_s = time.perf_counter() - t_suite_start
        metadata = {
            "experiment_mode": active_mode,
            "num_configurations": len(schedule),
            "warmup_runs_per_config": warmup,
            "measured_repetitions_per_config": reps,
            "total_measured_trials": len(schedule) * reps * 2,
            "suite_wall_time_s": round(suite_elapsed_s, 3),
            "completed_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "random_seed": self.dataset_generator.default_seed,
            "isolated_bootstrapping_study": boot_metrics,
        }

        exported_paths = self.metrics_collector.export_all(extra_metadata=metadata)
        LOGGER.info(
            "Benchmark suite finished in %.2fs. Raw CSV: %s | Summary CSV: %s",
            suite_elapsed_s,
            exported_paths["raw_csv"],
            exported_paths["summary_csv"],
        )
        return {
            "metadata": metadata,
            "paths": exported_paths,
        }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Private Collaborative Computing: FHE vs. SMPC Benchmark Orchestrator"
    )
    parser.add_argument(
        "--mode",
        choices=["quick", "standard", "full"],
        default=os.environ.get("PCC_EXPERIMENT_MODE", "standard"),
        help="Experimental matrix mode to execute.",
    )
    parser.add_argument(
        "--warmup",
        type=int,
        default=None,
        help="Override number of warm-up runs per configuration.",
    )
    parser.add_argument(
        "--repetitions",
        type=int,
        default=None,
        help="Override number of measured repetitions per configuration.",
    )
    parser.add_argument(
        "--skip-analysis",
        action="store_true",
        help="Skip automated crossover analysis, graph generation, and IEEE report compilation.",
    )
    args = parser.parse_args()

    runner = BenchmarkRunner()
    suite_out = runner.run_suite(
        mode=args.mode,
        override_warmup=args.warmup,
        override_repetitions=args.repetitions,
    )

    if not args.skip_analysis:
        from analysis.crossover_analysis import CrossoverAnalyzer
        from analysis.graphs import AcademicGraphGenerator
        from reports.generate_report import IEEEReportGenerator

        LOGGER.info("Running statistical crossover analysis and generating academic graphs...")
        analyzer = CrossoverAnalyzer(results_dir=runner.results_dir)
        crossover_report = analyzer.run_full_analysis()

        graph_gen = AcademicGraphGenerator(results_dir=runner.results_dir)
        graph_paths = graph_gen.generate_all_graphs()
        LOGGER.info("Generated %d graph artifacts.", len(graph_paths))

        report_gen = IEEEReportGenerator(results_dir=runner.results_dir)
        report_paths = report_gen.generate_and_compile(crossover_report=crossover_report)
        LOGGER.info("Generated IEEE Research Report: %s", report_paths)


if __name__ == "__main__":
    main()
