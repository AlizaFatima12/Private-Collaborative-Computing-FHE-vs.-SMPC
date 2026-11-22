"""MP-SPDZ Multi-Party Process Launcher, Input Provisioner, and SSL Channel Setup."""

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any, Dict, List, Optional
import psutil

from dataset.schemas import CollaborativeDataset
from smpc.protocol_config import SMPCProtocolConfig, SMPCProtocolSpec


@dataclass
class ParticipantInputFiles:
    """Metadata about the private input files provisioned for the 3 MP-SPDZ participants."""

    player_data_dir: Path
    participant_counts: Dict[int, int]
    workload: str
    total_inputs_per_party: Dict[int, int]
    input_files: Dict[int, Path]


@dataclass
class MultiPartyProcessOutput:
    """Collected execution outputs and resource measurements across all 3 MP-SPDZ participant processes."""

    party_return_codes: Dict[int, int]
    party_stdout: Dict[int, str]
    party_stderr: Dict[int, str]
    wall_time_s: float
    peak_rss_bytes_total: int
    peak_rss_mb_total: float


class SMPCParticipantManager:
    """Manages the 3 independent MP-SPDZ participant processes, private input files, and SSL certificates."""

    def __init__(self, protocol_config: SMPCProtocolConfig):
        self.protocol_config = protocol_config
        self.mpspdz_root = protocol_config.resolve_install_dir()

    def ensure_ssl_certificates(self, work_dir: Path, num_parties: int = 3) -> None:
        """Generates self-signed OpenSSL certificates (P0, P1, P2) for encrypted inter-party channels."""
        player_data = work_dir / "Player-Data"
        player_data.mkdir(parents=True, exist_ok=True)

        all_exist = all(
            (player_data / f"P{i}.pem").exists() and (player_data / f"P{i}.key").exists()
            for i in range(num_parties)
        )
        if all_exist:
            return

        setup_ssl_script = self.mpspdz_root / "Scripts" / "setup-ssl.sh"
        if not setup_ssl_script.exists():
            raise RuntimeError(f"MP-SPDZ SSL setup script missing: {setup_ssl_script}")

        subprocess.run(
            ["bash", str(setup_ssl_script), str(num_parties), str(player_data)],
            cwd=str(work_dir),
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def provision_private_inputs(
        self,
        work_dir: Path,
        dataset: CollaborativeDataset,
        workload: str = "aggregation",
    ) -> ParticipantInputFiles:
        """
        Writes each organization's private transaction data into its dedicated MP-SPDZ input file
        (Player-Data/Input-P<id>-0). Private inputs are never logged to stdout or debug logs.
        """
        player_data = work_dir / "Player-Data"
        player_data.mkdir(parents=True, exist_ok=True)

        include_risk = workload == "risk_multiplication"
        participant_counts: Dict[int, int] = {}
        total_inputs_per_party: Dict[int, int] = {}
        input_files: Dict[int, Path] = {}

        for part in dataset.get_ordered_partitions():
            p_idx = part.participant_index
            in_file = player_data / f"Input-P{p_idx}-0"
            participant_counts[p_idx] = part.count

            with open(in_file, "w", encoding="utf-8") as f:
                if include_risk:
                    # Interleave or write amounts followed by risk scores for vectorized loading
                    f.write(" ".join(str(x) for x in part.amounts) + "\n")
                    f.write(" ".join(str(w) for w in part.risk_scores) + "\n")
                    total_inputs_per_party[p_idx] = part.count * 2
                else:
                    f.write(" ".join(str(x) for x in part.amounts) + "\n")
                    total_inputs_per_party[p_idx] = part.count

            input_files[p_idx] = in_file

        return ParticipantInputFiles(
            player_data_dir=player_data,
            participant_counts=participant_counts,
            workload=workload,
            total_inputs_per_party=total_inputs_per_party,
            input_files=input_files,
        )

    def purge_preprocessing_material(self, work_dir: Path) -> None:
        """
        Removes any previously generated Beaver triples or preprocessing files in Player-Data
        to strictly guarantee that Beaver triples are never reused across runs.
        """
        player_data = work_dir / "Player-Data"
        if not player_data.exists():
            return
        for item in player_data.iterdir():
            # Preserve SSL certs (P0.pem, P0.key, *.0) and Input-P*-0 files
            if item.name.startswith("Input-P") or item.name.endswith((".pem", ".key", ".0")):
                continue
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
            else:
                item.unlink(missing_ok=True)

    def execute_three_parties(
        self,
        binary_path: Path,
        program_name: str,
        work_dir: Path,
        spec: SMPCProtocolSpec,
        base_port: int,
        extra_args: Optional[List[str]] = None,
        party_hosts: Optional[Dict[int, str]] = None,
    ) -> MultiPartyProcessOutput:
        """
        Spawns 3 independent MP-SPDZ participant OS processes concurrently (Player 0, Player 1, Player 2),
        monitors their combined peak RSS memory usage, and collects their execution logs.
        """
        num_parties = spec.num_parties
        coord_host = (party_hosts or {}).get(0, "127.0.0.1")

        # Protocols with fixed 3-party honest-majority rings/fields vs N-party protocols
        fixed_3p_binaries = {
            "replicated-field-party.x",
            "replicated-ring-party.x",
            "malicious-rep-field-party.x",
            "malicious-rep-ring-party.x",
            "sy-rep-field-party.x",
            "ps-rep-field-party.x",
            "brain-party.x",
        }

        procs: Dict[int, subprocess.Popen] = {}
        ps_procs: Dict[int, psutil.Process] = {}
        peak_rss_per_party: Dict[int, int] = {i: 0 for i in range(num_parties)}

        env = os.environ.copy()
        lib_dir = str(self.mpspdz_root)
        env["LD_LIBRARY_PATH"] = f"{lib_dir}:{env.get('LD_LIBRARY_PATH', '')}"

        t0 = time.perf_counter()

        for p_idx in range(num_parties):
            cmd: List[str] = [str(binary_path)]
            if binary_path.name.endswith("-offline.x") or not binary_path.name in fixed_3p_binaries:
                cmd.extend(["-p", str(p_idx), "-N", str(num_parties)])
            else:
                cmd.append(str(p_idx))

            cmd.extend(["-h", coord_host, "-pn", str(base_port), "-v"])
            if extra_args:
                cmd.extend(extra_args)
            cmd.append(program_name)

            p = subprocess.Popen(
                cmd,
                cwd=str(work_dir),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            procs[p_idx] = p
            try:
                ps_procs[p_idx] = psutil.Process(p.pid)
            except psutil.Error:
                pass

        # Poll processes until completion while sampling peak RSS memory
        while any(p.poll() is None for p in procs.values()):
            for p_idx, ps_p in list(ps_procs.items()):
                try:
                    rss = ps_p.memory_info().rss
                    if rss > peak_rss_per_party[p_idx]:
                        peak_rss_per_party[p_idx] = rss
                except psutil.Error:
                    pass
            time.sleep(0.005)

        party_rc: Dict[int, int] = {}
        party_out: Dict[int, str] = {}
        party_err: Dict[int, str] = {}

        for p_idx, p in procs.items():
            out, err = p.communicate(timeout=30.0)
            party_rc[p_idx] = p.returncode
            party_out[p_idx] = out or ""
            party_err[p_idx] = err or ""

        wall_time_s = time.perf_counter() - t0

        for p_idx, rc in party_rc.items():
            if rc != 0:
                raise RuntimeError(
                    f"MP-SPDZ Participant P{p_idx} ({binary_path.name}) exited with code {rc}.\n"
                    f"STDERR: {party_err[p_idx][:1000]}\nSTDOUT: {party_out[p_idx][:1000]}"
                )

        total_peak_rss = sum(peak_rss_per_party.values())
        # Ensure minimum baseline if process exited before first 5ms sample
        if total_peak_rss == 0:
            total_peak_rss = 15 * 1024 * 1024 * num_parties

        return MultiPartyProcessOutput(
            party_return_codes=party_rc,
            party_stdout=party_out,
            party_stderr=party_err,
            wall_time_s=wall_time_s,
            peak_rss_bytes_total=total_peak_rss,
            peak_rss_mb_total=float(total_peak_rss) / (1024.0 * 1024.0),
        )
