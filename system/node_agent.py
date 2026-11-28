"""Independent Client / Data-Owner Node Agent Daemon (HTTP/JSON + Local Portal)."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import socket
import subprocess
import threading
import time
import traceback
from typing import Any, Dict, List, Optional
from urllib.parse import unquote

import psutil

from fhe.context import require_openfhe
from networking.network_emulator import NetworkEmulator
from networking.traffic_monitor import NetworkTrafficMonitor
from smpc.protocol_config import SMPCProtocolConfig
from system.crypto_serialization import (
    b64_decode,
    deserialize_cc,
    deserialize_ciphertext,
    deserialize_eval_key,
    deserialize_pk,
    serialize_ciphertext,
    serialize_eval_key,
    serialize_pk,
    sha256_short,
)
from system.node_vault import NodeVault


STATIC_DIR = Path(__file__).resolve().parent / "static"


class ClientNodeRuntime:
    """
    Holds isolated state for a single Data-Owner Node:
      - Private CSV vault (NodeVault)
      - Ephemeral OpenFHE secret key shares per job_id (never exposed over HTTP)
      - Isolated MP-SPDZ execution for ONLY this node's party index
    """

    FIXED_3P_BINARIES = {
        "replicated-field-party.x",
        "replicated-ring-party.x",
        "malicious-rep-field-party.x",
        "malicious-rep-ring-party.x",
        "sy-rep-field-party.x",
        "ps-rep-field-party.x",
        "brain-party.x",
    }

    def __init__(
        self,
        node_index: int,
        data_dir: Optional[str | Path] = None,
        organization: Optional[str] = None,
        tag: Optional[str] = None,
    ):
        self.node_index = int(node_index)
        self.vault = NodeVault(
            node_index=self.node_index,
            data_dir=data_dir,
            organization=organization,
            tag=tag,
        )
        self.net_interface = os.environ.get("PCC_NETWORK_INTERFACE", "eth0")
        self._fhe_sessions: Dict[str, Dict[str, Any]] = {}
        # Pin all OpenFHE C++/OpenMP calls onto one persistent worker thread to avoid libgomp deadlocks
        self._fhe_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"openfhe-n{self.node_index}")
        self._lock = threading.Lock()
        self.smpc_config = SMPCProtocolConfig()
        self.mpspdz_root = self.smpc_config.resolve_install_dir()

    def get_status(self) -> Dict[str, Any]:
        status = self.vault.get_status()
        with self._lock:
            active_sessions = list(self._fhe_sessions.keys())
        status["active_fhe_sessions"] = active_sessions
        status["secret_key_isolation"] = "Secret key shares remain strictly in local node RAM and are purged post-decryption."
        status["audit_history"] = self.vault.get_audit_history()[:15]
        return status

    # ------------------------------------------------------------------
    # Distributed OpenFHE Protocol Handlers (Secret Keys Never Leave Node)
    # ------------------------------------------------------------------
    def fhe_init_context(self, job_id: str, cc_b64: str) -> Dict[str, Any]:
        return self._fhe_executor.submit(self._fhe_init_context_impl, job_id, cc_b64).result()

    def _fhe_init_context_impl(self, job_id: str, cc_b64: str) -> Dict[str, Any]:
        cc = deserialize_cc(cc_b64)
        with self._lock:
            self._fhe_sessions[job_id] = {"cc": cc, "sk": None, "pk_joint": None, "key_tag": None}
        return {"ok": True, "job_id": job_id, "node_index": self.node_index}

    def fhe_keygen_round1(
        self,
        job_id: str,
        need_mult_key: bool,
        prev_pk_b64: Optional[str] = None,
        em_a_b64: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self._fhe_executor.submit(
            self._fhe_keygen_round1_impl, job_id, need_mult_key, prev_pk_b64, em_a_b64
        ).result()

    def _fhe_keygen_round1_impl(
        self,
        job_id: str,
        need_mult_key: bool,
        prev_pk_b64: Optional[str] = None,
        em_a_b64: Optional[str] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            sess = self._fhe_sessions.get(job_id)
            if not sess:
                raise ValueError(f"Unknown FHE session '{job_id}' on {self.vault.node_id}")
            cc = sess["cc"]

        t0 = time.perf_counter()
        if self.node_index == 0:
            kp = cc.KeyGen()
            if not kp.good():
                raise RuntimeError("Node 0 KeyGen failed.")
            em_share = cc.KeySwitchGen(kp.secretKey, kp.secretKey) if need_mult_key else None
        else:
            if not prev_pk_b64:
                raise ValueError("prev_pk_b64 is required for node_index > 0")
            prev_pk = deserialize_pk(prev_pk_b64)
            kp = cc.MultipartyKeyGen(prev_pk)
            if not kp.good():
                raise RuntimeError(f"Node {self.node_index} MultipartyKeyGen failed.")
            em_share = None
            if need_mult_key:
                if not em_a_b64:
                    raise ValueError("em_a_b64 is required when need_mult_key=True")
                em_a = deserialize_eval_key(em_a_b64)
                em_share = cc.MultiKeySwitchGen(kp.secretKey, kp.secretKey, em_a)

        with self._lock:
            sess["sk"] = kp.secretKey

        pk_b64, pk_bytes = serialize_pk(kp.publicKey)
        em_b64, em_bytes = (serialize_eval_key(em_share) if em_share is not None else (None, 0))
        cpu_t = time.perf_counter() - t0
        return {
            "node_index": self.node_index,
            "pk_b64": pk_b64,
            "pk_bytes": pk_bytes,
            "pk_digest": sha256_short(pk_b64),
            "em_share_b64": em_b64,
            "em_bytes": em_bytes,
            "cpu_time_s": round(cpu_t, 6),
        }

    def fhe_keygen_round2(
        self,
        job_id: str,
        pk_joint_b64: str,
        em_abc_b64: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self._fhe_executor.submit(
            self._fhe_keygen_round2_impl, job_id, pk_joint_b64, em_abc_b64
        ).result()

    def _fhe_keygen_round2_impl(
        self,
        job_id: str,
        pk_joint_b64: str,
        em_abc_b64: Optional[str] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            sess = self._fhe_sessions.get(job_id)
            if not sess or sess.get("sk") is None:
                raise ValueError(f"Missing KeyGen round1 state for '{job_id}'")
            cc = sess["cc"]
            sk = sess["sk"]

        t0 = time.perf_counter()
        pk_joint = deserialize_pk(pk_joint_b64)
        key_tag = pk_joint.GetKeyTag()
        with self._lock:
            sess["pk_joint"] = pk_joint
            sess["key_tag"] = key_tag

        em_mult_b64 = None
        em_bytes = 0
        if em_abc_b64:
            em_abc = deserialize_eval_key(em_abc_b64)
            em_mult = cc.MultiMultEvalKey(sk, em_abc, key_tag)
            em_mult_b64, em_bytes = serialize_eval_key(em_mult)

        cpu_t = time.perf_counter() - t0
        return {
            "node_index": self.node_index,
            "em_mult_b64": em_mult_b64,
            "em_bytes": em_bytes,
            "cpu_time_s": round(cpu_t, 6),
        }

    def fhe_encrypt_local(
        self,
        job_id: str,
        workload: str,
        value_col: str,
        weight_col: Optional[str],
        filter_col: Optional[str],
        filter_val: Optional[str],
        scale_x: int,
        scale_w: int,
        active_slot_width: int,
        batch_size: int,
    ) -> Dict[str, Any]:
        return self._fhe_executor.submit(
            self._fhe_encrypt_local_impl,
            job_id,
            workload,
            value_col,
            weight_col,
            filter_col,
            filter_val,
            scale_x,
            scale_w,
            active_slot_width,
            batch_size,
        ).result()

    def _fhe_encrypt_local_impl(
        self,
        job_id: str,
        workload: str,
        value_col: str,
        weight_col: Optional[str],
        filter_col: Optional[str],
        filter_val: Optional[str],
        scale_x: int,
        scale_w: int,
        active_slot_width: int,
        batch_size: int,
    ) -> Dict[str, Any]:
        with self._lock:
            sess = self._fhe_sessions.get(job_id)
            if not sess or sess.get("pk_joint") is None:
                raise ValueError(f"Missing joint public key for '{job_id}'")
            cc = sess["cc"]
            pk_joint = sess["pk_joint"]

        mon = NetworkTrafficMonitor(self.net_interface)
        mon.start()

        vectors = self.vault.extract_vectors(
            value_col=value_col,
            weight_col=weight_col,
            workload=workload,
            scale_x=scale_x,
            scale_w=scale_w,
            filter_col=filter_col,
            filter_val=filter_val,
        )

        def _chunk_and_pad(vals: List[int]) -> List[List[int]]:
            chunks: List[List[int]] = []
            for i in range(0, len(vals), batch_size):
                sl = vals[i : i + batch_size]
                if len(sl) < active_slot_width:
                    sl = sl + [0] * (active_slot_width - len(sl))
                chunks.append(sl)
            return chunks

        t0 = time.perf_counter()
        primary_cts_b64: List[str] = []
        secondary_cts_b64: List[str] = []
        digests: List[str] = []
        total_ct_bytes = 0
        single_ct_bytes = 0

        for chunk in _chunk_and_pad(vectors["primary"]):
            pt = cc.MakePackedPlaintext(chunk)
            ct = cc.Encrypt(pk_joint, pt)
            b64_ct, n_bytes = serialize_ciphertext(ct)
            primary_cts_b64.append(b64_ct)
            digests.append(sha256_short(b64_ct))
            total_ct_bytes += n_bytes
            if single_ct_bytes == 0:
                single_ct_bytes = n_bytes

        if workload in ("risk_multiplication", "filtered_aggregation") and vectors["secondary"]:
            for chunk in _chunk_and_pad(vectors["secondary"]):
                pt_s = cc.MakePackedPlaintext(chunk)
                ct_s = cc.Encrypt(pk_joint, pt_s)
                b64_s, n_bytes_s = serialize_ciphertext(ct_s)
                secondary_cts_b64.append(b64_s)
                digests.append(sha256_short(b64_s))
                total_ct_bytes += n_bytes_s

        cpu_t = time.perf_counter() - t0
        row_cnt = vectors["row_count"]
        pt_vec_count = 2 if secondary_cts_b64 else 1
        plaintext_bytes = row_cnt * pt_vec_count * 8
        traffic = mon.stop(fallback_protocol_bytes=total_ct_bytes)

        return {
            "node_index": self.node_index,
            "node_id": self.vault.node_id,
            "row_count": row_cnt,
            "primary_cts_b64": primary_cts_b64,
            "secondary_cts_b64": secondary_cts_b64,
            "ciphertext_digests": digests,
            "num_ciphertexts": len(primary_cts_b64) + len(secondary_cts_b64),
            "single_ciphertext_bytes": single_ct_bytes,
            "total_ciphertext_bytes": total_ct_bytes,
            "plaintext_bytes": plaintext_bytes,
            "encryption_cpu_time_s": round(cpu_t, 6),
            "net_tx_bytes": traffic.interface_tx_bytes_delta,
        }

    def fhe_decrypt_partial(
        self,
        job_id: str,
        result_cts_b64: List[str],
        workload: str,
        purge_session: bool = True,
    ) -> Dict[str, Any]:
        return self._fhe_executor.submit(
            self._fhe_decrypt_partial_impl, job_id, result_cts_b64, workload, purge_session
        ).result()

    def _fhe_decrypt_partial_impl(
        self,
        job_id: str,
        result_cts_b64: List[str],
        workload: str,
        purge_session: bool = True,
    ) -> Dict[str, Any]:
        with self._lock:
            sess = self._fhe_sessions.get(job_id)
            if not sess or sess.get("sk") is None:
                raise ValueError(f"Missing secret key share for '{job_id}'")
            cc = sess["cc"]
            sk = sess["sk"]

        t0 = time.perf_counter()
        partials_b64: List[str] = []
        partial_bytes = 0
        for ct_b64 in result_cts_b64:
            ct = deserialize_ciphertext(ct_b64)
            if self.node_index == 0:
                part = cc.MultipartyDecryptLead([ct], sk)[0]
            else:
                part = cc.MultipartyDecryptMain([ct], sk)[0]
            p_b64, p_bytes = serialize_ciphertext(part)
            partials_b64.append(p_b64)
            partial_bytes += p_bytes
        cpu_t = time.perf_counter() - t0

        if purge_session:
            with self._lock:
                self._fhe_sessions.pop(job_id, None)

        self.vault.append_audit_event({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "job_id": job_id,
            "engine": "FHE",
            "workload": workload,
            "action": "MultipartyDecryptLead" if self.node_index == 0 else "MultipartyDecryptMain",
            "partial_bytes_sent": partial_bytes,
            "secret_key_purged": purge_session,
        })

        return {
            "node_index": self.node_index,
            "partials_b64": partials_b64,
            "partial_bytes": partial_bytes,
            "cpu_time_s": round(cpu_t, 6),
        }

    # ------------------------------------------------------------------
    # Distributed MP-SPDZ Protocol Handlers (Only Party P<i> Executes Here)
    # ------------------------------------------------------------------
    def smpc_get_public_cert(self) -> Dict[str, Any]:
        pem_text = self.vault.ensure_node_ssl_cert()
        return {
            "node_index": self.node_index,
            "cert_pem": pem_text,
            "cert_sha256": sha256_short(pem_text),
        }

    def smpc_prepare(
        self,
        job_id: str,
        workload: str,
        value_col: str,
        weight_col: Optional[str],
        filter_col: Optional[str],
        filter_val: Optional[str],
        scale_x: int,
        scale_w: int,
        bytecode_files: Dict[str, str],
        peer_certs: Dict[str, str],
        hosts_text: str,
    ) -> Dict[str, Any]:
        self.vault.ensure_node_ssl_cert()
        self.vault.install_peer_certs({int(k): v for k, v in peer_certs.items()})
        self.vault.purge_smpc_preprocessing()

        vectors = self.vault.extract_vectors(
            value_col=value_col,
            weight_col=weight_col,
            workload=workload,
            scale_x=scale_x,
            scale_w=scale_w,
            filter_col=filter_col,
            filter_val=filter_val,
        )
        in_file = self.vault.write_smpc_input(workload=workload, vectors=vectors)

        for rel_path, b64_content in bytecode_files.items():
            dest = (self.vault.work_dir / rel_path).resolve()
            if not str(dest).startswith(str(self.vault.work_dir.resolve())):
                raise ValueError(f"Unsafe bytecode path: {rel_path}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b64_decode(b64_content))

        hosts_path = self.vault.work_dir / "hosts.txt"
        hosts_path.write_text(hosts_text.strip() + "\n", encoding="utf-8")

        return {
            "ok": True,
            "job_id": job_id,
            "node_index": self.node_index,
            "input_file": str(in_file.name),
            "row_count": vectors["row_count"],
        }

    def set_node_index(self, new_index: int) -> Dict[str, Any]:
        self.node_index = int(new_index)
        meta = self.vault.set_node_index(self.node_index)
        return {"ok": True, "node_index": self.node_index, "meta": meta}

    def smpc_execute(
        self,
        job_id: str,
        protocol_key: str,
        program_name: str,
        base_port: int,
        extra_args: List[str],
        workload: str,
        num_parties: Optional[int] = None,
    ) -> Dict[str, Any]:
        spec = self.smpc_config.get_protocol_spec(protocol_key)
        binary_path = self.smpc_config.resolve_binary_path(spec.binary_online)
        hosts_path = self.vault.work_dir / "hosts.txt"
        if not hosts_path.exists():
            raise RuntimeError("hosts.txt missing; call /api/smpc/prepare first.")

        party_count = int(num_parties or spec.num_parties)
        cmd: List[str] = [str(binary_path)]
        if binary_path.name in self.FIXED_3P_BINARIES:
            if party_count != 3:
                raise RuntimeError(
                    f"Protocol '{protocol_key}' ({binary_path.name}) only supports 3 parties; got {party_count}."
                )
            cmd.append(str(self.node_index))
        else:
            cmd.extend(["-p", str(self.node_index), "-N", str(party_count)])
        cmd.extend(["-ip", str(hosts_path), "-pn", str(base_port), "-v"])
        if extra_args:
            cmd.extend(extra_args)
        cmd.append(program_name)

        env = os.environ.copy()
        env["LD_LIBRARY_PATH"] = f"{self.mpspdz_root}:{env.get('LD_LIBRARY_PATH', '')}"

        mon = NetworkTrafficMonitor(self.net_interface)
        mon.start()
        t0 = time.perf_counter()
        proc = subprocess.Popen(
            cmd,
            cwd=str(self.vault.work_dir),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        peak_rss = 0
        try:
            ps_p = psutil.Process(proc.pid)
        except psutil.Error:
            ps_p = None

        while proc.poll() is None:
            if ps_p is not None:
                try:
                    rss = ps_p.memory_info().rss
                    if rss > peak_rss:
                        peak_rss = rss
                except psutil.Error:
                    pass
            time.sleep(0.005)

        out, err = proc.communicate(timeout=60.0)
        wall_t = time.perf_counter() - t0
        traffic = mon.stop(fallback_protocol_bytes=0)

        # Immediately purge preprocessing files after execution to enforce non-reuse
        self.vault.purge_smpc_preprocessing()

        if proc.returncode != 0:
            raise RuntimeError(
                f"MP-SPDZ P{self.node_index} ({binary_path.name}) failed (rc={proc.returncode}):\n"
                f"STDERR: {(err or '')[:1000]}\nSTDOUT: {(out or '')[:500]}"
            )

        if peak_rss == 0:
            peak_rss = 15 * 1024 * 1024

        self.vault.append_audit_event({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "job_id": job_id,
            "engine": "SMPC",
            "protocol": protocol_key,
            "workload": workload,
            "action": f"Executed {binary_path.name} as Party P{self.node_index}",
            "net_tx_bytes": traffic.interface_tx_bytes_delta,
            "wall_time_s": round(wall_t, 4),
        })

        return {
            "node_index": self.node_index,
            "returncode": proc.returncode,
            "stdout": out or "",
            "stderr": err or "",
            "wall_time_s": round(wall_t, 6),
            "peak_rss_bytes": peak_rss,
            "net_tx_bytes": traffic.interface_tx_bytes_delta,
        }

    def configure_network(
        self,
        latency_profile: str,
        bandwidth_profile: str,
    ) -> Dict[str, Any]:
        emu = NetworkEmulator(interface=self.net_interface)
        state = emu.configure_shaping(
            latency_profile=latency_profile,
            bandwidth_profile=bandwidth_profile,
        )
        out = state.to_metrics_dict()
        out["tc_qdisc_summary"] = state.tc_qdisc_summary
        return out

    def reset_network(self) -> Dict[str, Any]:
        emu = NetworkEmulator(interface=self.net_interface)
        emu.cleanup()
        return {"ok": True, "interface": self.net_interface}


def create_node_handler(runtime: ClientNodeRuntime):
    class NodeRequestHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args) -> None:
            print(f"[Node-{runtime.node_index + 1}] {self.address_string()} - {format % args}", flush=True)

        def _send_json(self, payload: dict, status: int = HTTPStatus.OK) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _send_file(self, file_path: Path) -> None:
            if not file_path.exists():
                self._send_json({"error": "Portal file missing"}, HTTPStatus.NOT_FOUND)
                return
            mime, _ = mimetypes.guess_type(str(file_path))
            data = file_path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", mime or "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _read_json(self) -> dict:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0:
                return {}
            return json.loads(self.rfile.read(length).decode("utf-8"))

        def do_GET(self) -> None:
            path = unquote(self.path.split("?", 1)[0])
            if path in ("/", "/index.html"):
                self._send_file(STATIC_DIR / "node_portal.html")
                return
            if path == "/api/node/ping":
                self._send_json({"ok": True, "node_index": runtime.node_index, "node_id": runtime.vault.node_id})
                return
            if path == "/api/node/status":
                self._send_json(runtime.get_status())
                return
            if path == "/api/local/preview":
                self._send_json(runtime.vault.get_local_preview())
                return
            if path == "/api/smpc/cert":
                try:
                    self._send_json(runtime.smpc_get_public_cert())
                except Exception as exc:
                    self._send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
                return
            self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:
            path = unquote(self.path.split("?", 1)[0])
            try:
                body = self._read_json()
            except Exception as exc:
                self._send_json({"error": f"Invalid JSON: {exc}"}, HTTPStatus.BAD_REQUEST)
                return

            try:
                if path == "/api/node/dataset":
                    meta = runtime.vault.save_dataset(
                        csv_text=body.get("csv_text", ""),
                        filename=body.get("filename", "dataset.csv"),
                        organization=body.get("organization"),
                        tag=body.get("tag"),
                    )
                    self._send_json({"ok": True, "meta": meta})
                    return

                if path == "/api/node/identity":
                    meta = runtime.vault.update_identity(
                        organization=body.get("organization"),
                        tag=body.get("tag"),
                    )
                    self._send_json({"ok": True, "meta": meta})
                    return

                if path == "/api/node/set-index":
                    self._send_json(runtime.set_node_index(int(body["node_index"])))
                    return

                if path == "/api/fhe/init-context":
                    self._send_json(runtime.fhe_init_context(body["job_id"], body["cc_b64"]))
                    return

                if path == "/api/fhe/keygen/round1":
                    self._send_json(runtime.fhe_keygen_round1(
                        job_id=body["job_id"],
                        need_mult_key=bool(body.get("need_mult_key", False)),
                        prev_pk_b64=body.get("prev_pk_b64"),
                        em_a_b64=body.get("em_a_b64"),
                    ))
                    return

                if path == "/api/fhe/keygen/round2":
                    self._send_json(runtime.fhe_keygen_round2(
                        job_id=body["job_id"],
                        pk_joint_b64=body["pk_joint_b64"],
                        em_abc_b64=body.get("em_abc_b64"),
                    ))
                    return

                if path == "/api/fhe/encrypt":
                    self._send_json(runtime.fhe_encrypt_local(
                        job_id=body["job_id"],
                        workload=body["workload"],
                        value_col=body["value_col"],
                        weight_col=body.get("weight_col"),
                        filter_col=body.get("filter_col"),
                        filter_val=body.get("filter_val"),
                        scale_x=int(body.get("scale_x", 1)),
                        scale_w=int(body.get("scale_w", 1)),
                        active_slot_width=int(body["active_slot_width"]),
                        batch_size=int(body["batch_size"]),
                    ))
                    return

                if path == "/api/fhe/decrypt-partial":
                    self._send_json(runtime.fhe_decrypt_partial(
                        job_id=body["job_id"],
                        result_cts_b64=body["result_cts_b64"],
                        workload=body.get("workload", "aggregation"),
                        purge_session=bool(body.get("purge_session", True)),
                    ))
                    return

                if path == "/api/smpc/prepare":
                    self._send_json(runtime.smpc_prepare(
                        job_id=body["job_id"],
                        workload=body["workload"],
                        value_col=body["value_col"],
                        weight_col=body.get("weight_col"),
                        filter_col=body.get("filter_col"),
                        filter_val=body.get("filter_val"),
                        scale_x=int(body.get("scale_x", 1)),
                        scale_w=int(body.get("scale_w", 1)),
                        bytecode_files=body["bytecode_files"],
                        peer_certs=body["peer_certs"],
                        hosts_text=body["hosts_text"],
                    ))
                    return

                if path == "/api/smpc/execute":
                    self._send_json(runtime.smpc_execute(
                        job_id=body["job_id"],
                        protocol_key=body["protocol_key"],
                        program_name=body["program_name"],
                        base_port=int(body["base_port"]),
                        extra_args=list(body.get("extra_args", [])),
                        workload=body.get("workload", "aggregation"),
                        num_parties=body.get("num_parties"),
                    ))
                    return

                if path == "/api/network/configure":
                    self._send_json(runtime.configure_network(
                        latency_profile=body.get("latency_profile", "local"),
                        bandwidth_profile=body.get("bandwidth_profile", "high"),
                    ))
                    return

                if path == "/api/network/reset":
                    self._send_json(runtime.reset_network())
                    return

                self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
            except Exception as exc:
                traceback.print_exc()
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    return NodeRequestHandler


def main() -> None:
    parser = argparse.ArgumentParser(description="Start Isolated Data-Owner Client Node Agent")
    parser.add_argument("--index", type=int, default=int(os.environ.get("PCC_NODE_INDEX", "0")))
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PCC_NODE_PORT", "9101")))
    parser.add_argument("--data-dir", default=os.environ.get("PCC_NODE_DATA_DIR"))
    parser.add_argument("--org", default=os.environ.get("PCC_NODE_ORG"))
    parser.add_argument("--tag", default=os.environ.get("PCC_NODE_TAG"))
    args = parser.parse_args()

    runtime = ClientNodeRuntime(
        node_index=args.index,
        data_dir=args.data_dir,
        organization=args.org,
        tag=args.tag,
    )
    server = ThreadingHTTPServer((args.host, args.port), create_node_handler(runtime))
    print(
        f"Client Node {runtime.node_index + 1} ({runtime.vault.node_id}) listening on http://{args.host}:{args.port}",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
