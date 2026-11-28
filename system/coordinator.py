"""Untrusted Distributed Coordinator & Blind Compute Orchestrator (N >= 2 Organizations)."""

import atexit
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Optional
import urllib.error
from urllib.parse import urlparse
import urllib.request
import uuid

from fhe.context import FHEContextManager
from networking.network_emulator import NetworkEmulator
from networking.traffic_monitor import NetworkTrafficMonitor
from smpc.protocol_config import SMPCProtocolConfig
from system.crypto_serialization import (
    deserialize_ciphertext,
    deserialize_eval_key,
    deserialize_pk,
    serialize_cc,
    serialize_ciphertext,
    serialize_eval_key,
    sha256_short,
)
from system.distributed_smpc import DistributedSMPCCompiler
from system.node_vault import SUPPORTED_WORKLOADS


def _http_json(url: str, payload: Optional[Dict[str, Any]] = None, timeout: float = 120.0) -> Dict[str, Any]:
    """Performs a JSON GET or POST request to a remote Client Node."""
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")
        try:
            err_json = json.loads(err_body)
            msg = err_json.get("error", err_body)
        except Exception:
            msg = err_body
        raise RuntimeError(f"Node RPC error ({url}, HTTP {exc.code}): {msg}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Node unreachable ({url}): {exc.reason}") from exc


def _measure_http_rtt_ms(url: str, probes: int = 3) -> float:
    samples: List[float] = []
    for _ in range(probes):
        t0 = time.perf_counter()
        _http_json(f"{url.rstrip('/')}/api/node/ping", timeout=10.0)
        samples.append((time.perf_counter() - t0) * 1000.0)
    return round(sum(samples) / len(samples), 3) if samples else 0.0


def _find_free_port(start_port: int, used_ports: set[int]) -> int:
    port = start_port
    while port < 65000:
        if port not in used_ports:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                try:
                    s.bind(("0.0.0.0", port))
                    return port
                except OSError:
                    pass
        port += 1
    raise RuntimeError("No free TCP port available for new organization node.")


class DistributedCoordinator:
    """
    Untrusted Coordinator Server supporting any number of organizations (N >= 2):
      - Maintains a persistent Organization Registry (pre-configured containers + dynamically added nodes)
      - Checks schema alignment using column metadata only (0 raw rows)
      - Executes Distributed OpenFHE (N-of-N threshold BFV) blindly on ciphertexts
      - Executes Distributed MP-SPDZ (N-party TLS) by dispatching compiled bytecode
      - Stores job history and zero-leakage audit logs
    """

    def __init__(
        self,
        node_urls: Optional[List[str]] = None,
        data_dir: Optional[str | Path] = None,
    ):
        self.data_dir = Path(
            data_dir or os.environ.get("PCC_COORDINATOR_DATA_DIR", "/app/coordinator_data")
        )
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.jobs_path = self.data_dir / "jobs.json"
        self.orgs_path = self.data_dir / "organizations.json"
        self.dynamic_nodes_dir = self.data_dir / "dynamic_nodes"
        self.dynamic_nodes_dir.mkdir(parents=True, exist_ok=True)
        self.smpc_compile_dir = self.data_dir / "smpc_compile"
        self.smpc_compile_dir.mkdir(parents=True, exist_ok=True)

        self.net_interface = os.environ.get("PCC_NETWORK_INTERFACE", "eth0")
        self.fhe_ctx_mgr = FHEContextManager()
        self.smpc_cfg = SMPCProtocolConfig()
        self.smpc_compiler = DistributedSMPCCompiler(self.smpc_cfg)
        self._fhe_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="openfhe-coord")
        self._port_offset = 0
        self._lock = threading.Lock()
        self._managed_procs: Dict[str, subprocess.Popen] = {}
        atexit.register(self.shutdown_managed_nodes)

        self._orgs: List[Dict[str, Any]] = self._load_or_init_orgs(node_urls)

    def _load_or_init_orgs(self, explicit_urls: Optional[List[str]]) -> List[Dict[str, Any]]:
        if explicit_urls is not None:
            if len(explicit_urls) < 2:
                raise ValueError(f"At least 2 Client Node URLs are required; got {explicit_urls}")
            orgs = [
                {
                    "uid": f"static_{idx}",
                    "node_index": idx,
                    "url": u.strip().rstrip("/"),
                    "managed": False,
                    "organization": f"Organization {idx + 1}",
                    "tag": f"Site-{idx + 1}",
                }
                for idx, u in enumerate(explicit_urls)
                if u.strip()
            ]
            self._write_orgs(orgs)
            return orgs

        if self.orgs_path.exists():
            try:
                orgs = json.loads(self.orgs_path.read_text(encoding="utf-8"))
                if isinstance(orgs, list) and len(orgs) >= 2:
                    self._respawn_managed_nodes(orgs)
                    return orgs
            except Exception:
                pass

        env_urls = os.environ.get(
            "PCC_NODE_URLS",
            "http://node-1:9101,http://node-2:9102,http://node-3:9103",
        )
        raw_urls = [u.strip().rstrip("/") for u in env_urls.split(",") if u.strip()]
        if len(raw_urls) < 2:
            raise ValueError(f"At least 2 Client Node URLs are required; got {raw_urls}")
        orgs = [
            {
                "uid": f"static_{idx}",
                "node_index": idx,
                "url": u,
                "managed": False,
                "organization": f"Organization {idx + 1}",
                "tag": f"Site-{idx + 1}",
            }
            for idx, u in enumerate(raw_urls)
        ]
        self._write_orgs(orgs)
        return orgs

    def _write_orgs(self, orgs: List[Dict[str, Any]]) -> None:
        self.orgs_path.write_text(json.dumps(orgs, indent=2), encoding="utf-8")

    @property
    def node_urls(self) -> List[str]:
        return [o["url"] for o in self._orgs]

    def _internal_url(self, org: Dict[str, Any]) -> str:
        return org.get("local_url") or org["url"]

    def _advertise_host(self) -> str:
        if os.environ.get("PCC_COORDINATOR_HOST"):
            return os.environ["PCC_COORDINATOR_HOST"]
        for o in self._orgs:
            host = urlparse(o["url"]).hostname or ""
            if host and host not in ("127.0.0.1", "localhost"):
                return "coordinator"
        return "127.0.0.1"

    def _spawn_managed_node(self, org_entry: Dict[str, Any]) -> None:
        uid = org_entry["uid"]
        if uid in self._managed_procs and self._managed_procs[uid].poll() is None:
            return
        port = int(org_entry["port"])
        ping_url = f"http://127.0.0.1:{port}/api/node/ping"
        try:
            _http_json(ping_url, timeout=1.0)
            return
        except Exception:
            pass
        data_dir = Path(org_entry["data_dir"])
        data_dir.mkdir(parents=True, exist_ok=True)
        cmd = [
            sys.executable,
            "-m",
            "system.node_agent",
            "--index",
            str(org_entry["node_index"]),
            "--port",
            str(port),
            "--data-dir",
            str(data_dir),
            "--org",
            org_entry["organization"],
            "--tag",
            org_entry["tag"],
        ]
        env = os.environ.copy()
        env["PCC_NETWORK_INTERFACE"] = self.net_interface
        proc = subprocess.Popen(cmd, env=env)
        self._managed_procs[uid] = proc

        deadline = time.monotonic() + 12.0
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(f"Dynamic node process exited early (code={proc.returncode})")
            try:
                _http_json(ping_url, timeout=1.5)
                return
            except Exception:
                time.sleep(0.2)
        raise RuntimeError(f"Timed out waiting for dynamic node on port {port}")

    def _respawn_managed_nodes(self, orgs: List[Dict[str, Any]]) -> None:
        for o in orgs:
            if o.get("managed"):
                try:
                    self._spawn_managed_node(o)
                except Exception:
                    pass

    def shutdown_managed_nodes(self) -> None:
        for proc in list(self._managed_procs.values()):
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
        self._managed_procs.clear()

    def add_organization(
        self,
        organization: str,
        tag: str = "",
        url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Registers a remote organization node or auto-provisions an isolated local node."""
        org_name = (organization or "").strip()
        if not org_name:
            raise ValueError("Organization name is required.")
        org_tag = (tag or "").strip()

        with self._lock:
            new_idx = len(self._orgs)
            if not org_tag:
                org_tag = f"Site-{new_idx + 1}"

            clean_url = (url or "").strip().rstrip("/")
            if clean_url:
                if any(o["url"] == clean_url for o in self._orgs):
                    raise ValueError(f"Node URL '{clean_url}' is already registered.")
                _http_json(f"{clean_url}/api/node/ping", timeout=5.0)
                _http_json(f"{clean_url}/api/node/set-index", {"node_index": new_idx}, timeout=5.0)
                _http_json(
                    f"{clean_url}/api/node/identity",
                    {"organization": org_name, "tag": org_tag},
                    timeout=5.0,
                )
                entry: Dict[str, Any] = {
                    "uid": f"ext_{uuid.uuid4().hex[:8]}",
                    "node_index": new_idx,
                    "url": clean_url,
                    "managed": False,
                    "organization": org_name,
                    "tag": org_tag,
                }
            else:
                used_ports = {
                    int(urlparse(o["url"]).port or 0) for o in self._orgs
                } | {int(o.get("port", 0)) for o in self._orgs}
                port = _find_free_port(9104 + max(0, new_idx - 3), used_ports)
                uid = f"dyn_{uuid.uuid4().hex[:8]}"
                node_dir = self.dynamic_nodes_dir / uid
                adv_host = self._advertise_host()
                entry = {
                    "uid": uid,
                    "node_index": new_idx,
                    "url": f"http://{adv_host}:{port}",
                    "local_url": f"http://127.0.0.1:{port}",
                    "port": port,
                    "data_dir": str(node_dir),
                    "managed": True,
                    "organization": org_name,
                    "tag": org_tag,
                }
                self._spawn_managed_node(entry)

            self._orgs.append(entry)
            self._write_orgs(self._orgs)
            return entry

    def remove_organization(self, node_index: int) -> Dict[str, Any]:
        """Removes an organization node and re-indexes remaining nodes (minimum 2 required)."""
        with self._lock:
            if len(self._orgs) <= 2:
                raise ValueError("At least 2 organizations are required for collaborative computing.")
            if node_index < 0 or node_index >= len(self._orgs):
                raise ValueError(f"Invalid node_index {node_index}")

            removed = self._orgs.pop(node_index)
            uid = removed["uid"]
            if removed.get("managed"):
                proc = self._managed_procs.pop(uid, None)
                if proc and proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                if removed.get("data_dir"):
                    shutil.rmtree(removed["data_dir"], ignore_errors=True)

            for idx, o in enumerate(self._orgs):
                o["node_index"] = idx
                try:
                    _http_json(
                        f"{self._internal_url(o)}/api/node/set-index",
                        {"node_index": idx},
                        timeout=3.0,
                    )
                except Exception:
                    pass

            self._write_orgs(self._orgs)
            return {"removed": removed, "remaining": len(self._orgs)}

    def get_node_internal_url(self, node_index: int) -> str:
        with self._lock:
            if node_index < 0 or node_index >= len(self._orgs):
                raise ValueError(f"Invalid node_index {node_index}")
            return self._internal_url(self._orgs[node_index])

    def get_cluster_status(self) -> Dict[str, Any]:
        """Polls all registered client nodes for health and schema metadata (never raw data)."""
        orgs_snapshot = list(self._orgs)
        num_nodes = len(orgs_snapshot)

        def _fetch(org: Dict[str, Any]) -> Dict[str, Any]:
            idx = int(org["node_index"])
            fetch_url = self._internal_url(org)
            try:
                st = _http_json(f"{fetch_url}/api/node/status", timeout=5.0)
                st["url"] = org["url"]
                st["managed"] = bool(org.get("managed", False))
                st["uid"] = org.get("uid", f"node_{idx}")
                st["node_index"] = idx
                st["reachable"] = True
                return st
            except Exception as exc:
                return {
                    "uid": org.get("uid", f"node_{idx}"),
                    "node_index": idx,
                    "node_id": f"node_{idx + 1}",
                    "organization": org.get("organization", f"Organization {idx + 1}"),
                    "tag": org.get("tag", f"Node-{idx + 1}"),
                    "url": org["url"],
                    "managed": bool(org.get("managed", False)),
                    "reachable": False,
                    "error": str(exc),
                    "dataset_loaded": False,
                    "columns": [],
                    "numeric_columns": [],
                    "categorical_columns": [],
                    "row_count": 0,
                }

        with ThreadPoolExecutor(max_workers=max(1, num_nodes)) as pool:
            nodes = list(pool.map(_fetch, orgs_snapshot))

        reachable_count = sum(1 for n in nodes if n.get("reachable"))
        all_loaded = all(n.get("dataset_loaded") for n in nodes)

        schema_aligned = False
        schema_error: Optional[str] = None
        shared_columns: List[str] = []
        shared_numeric_cols: List[str] = []
        shared_categorical_cols: List[str] = []
        total_rows = 0

        if all_loaded and nodes:
            ref_cols = nodes[0].get("columns", [])
            mismatches = []
            for n in nodes[1:]:
                cols = n.get("columns", [])
                if set(cols) != set(ref_cols):
                    mismatches.append(f"{n['organization']} has {cols} vs {ref_cols}")
            if mismatches:
                schema_error = "Schema mismatch across nodes: " + "; ".join(mismatches)
            else:
                schema_aligned = True
                shared_columns = list(ref_cols)
                num_sets = [set(n.get("numeric_columns", [])) for n in nodes]
                shared_numeric_cols = [c for c in ref_cols if all(c in s for s in num_sets)]
                shared_categorical_cols = [c for c in ref_cols if c not in shared_numeric_cols]
                total_rows = sum(int(n.get("row_count", 0)) for n in nodes)
                if not shared_numeric_cols:
                    schema_aligned = False
                    schema_error = f"No shared numeric columns found across the {num_nodes} nodes."

        return {
            "coordinator_isolation": "Zero raw records and zero FHE secret keys stored on Coordinator.",
            "node_count": num_nodes,
            "nodes": nodes,
            "reachable_nodes": reachable_count,
            "all_datasets_loaded": all_loaded,
            "schema_aligned": schema_aligned,
            "schema_error": schema_error,
            "shared_columns": shared_columns,
            "shared_numeric_columns": shared_numeric_cols,
            "shared_categorical_columns": shared_categorical_cols,
            "total_rows": total_rows,
        }

    def proxy_upload_to_node(
        self,
        node_index: int,
        csv_text: str,
        filename: str = "dataset.csv",
        organization: Optional[str] = None,
        tag: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Forwards a CSV upload directly to the target Client Node without storing it on Coordinator."""
        target_url = self.get_node_internal_url(node_index)
        res = _http_json(
            f"{target_url}/api/node/dataset",
            payload={
                "csv_text": csv_text,
                "filename": filename,
                "organization": organization,
                "tag": tag,
            },
        )
        with self._lock:
            if 0 <= node_index < len(self._orgs):
                if organization and organization.strip():
                    self._orgs[node_index]["organization"] = organization.strip()
                if tag and tag.strip():
                    self._orgs[node_index]["tag"] = tag.strip()
                self._write_orgs(self._orgs)
        return res

    @staticmethod
    def _resolve_scales(
        nodes: List[Dict[str, Any]],
        value_col: str,
        weight_col: Optional[str],
        workload: str,
        scale_mode: str,
    ) -> tuple[int, int]:
        def _col_is_int(col: str) -> bool:
            return all(
                bool(n.get("numeric_bounds", {}).get(col, {}).get("is_integer", False))
                for n in nodes
            )

        if scale_mode != "auto":
            s_val = max(1, int(scale_mode))
            return s_val, (s_val if workload == "risk_multiplication" and weight_col else 1)

        scale_x = 1 if _col_is_int(value_col) else 100
        scale_w = 1
        if workload == "risk_multiplication" and weight_col:
            scale_w = 1 if _col_is_int(weight_col) else 100
        return scale_x, scale_w

    @staticmethod
    def _validate_plaintext_modulus(
        nodes: List[Dict[str, Any]],
        value_col: str,
        weight_col: Optional[str],
        workload: str,
        scale_x: int,
        scale_w: int,
        plaintext_modulus: int = 536903681,
    ) -> None:
        total_n = sum(int(n["row_count"]) for n in nodes)
        max_x = max(
            abs(float(n["numeric_bounds"][value_col]["max"])) * scale_x
            for n in nodes
        )
        min_x = min(
            float(n["numeric_bounds"][value_col]["min"]) * scale_x
            for n in nodes
        )
        if min_x < 0:
            max_abs_x = max(
                abs(float(n["numeric_bounds"][value_col]["min"])) * scale_x,
                abs(float(n["numeric_bounds"][value_col]["max"])) * scale_x,
            )
        else:
            max_abs_x = max_x

        half_p = plaintext_modulus // 2
        if workload in ("aggregation", "filtered_aggregation"):
            worst = total_n * max_abs_x
        elif workload == "risk_multiplication":
            w_col = weight_col or value_col
            max_abs_w = max(
                max(
                    abs(float(n["numeric_bounds"][w_col]["min"])) * scale_w,
                    abs(float(n["numeric_bounds"][w_col]["max"])) * scale_w,
                )
                for n in nodes
            )
            worst = total_n * max_abs_x * max_abs_w
        elif workload == "variance":
            worst = total_n * (max_abs_x ** 2)
        else:
            worst = 0

        if worst >= half_p:
            raise ValueError(
                f"Plaintext modulus safety bound exceeded: worst-case scaled sum ({int(worst):,}) "
                f">= p/2 ({half_p:,}). Use Decimal Scale Mode = 1 or smaller values for BFV."
            )

    def _run_distributed_fhe(
        self,
        job_id: str,
        nodes: List[Dict[str, Any]],
        internal_urls: List[str],
        workload: str,
        value_col: str,
        weight_col: Optional[str],
        filter_col: Optional[str],
        filter_val: Optional[str],
        scale_x: int,
        scale_w: int,
        fhe_profile: str,
    ) -> Dict[str, Any]:
        t_start = time.perf_counter()
        num_nodes = len(nodes)
        coord_mon = NetworkTrafficMonitor(self.net_interface)
        coord_mon.start()

        # 1. Initialize CryptoContext for N-party Threshold BFV & distribute (0 secret keys)
        ctx_bundle = self.fhe_ctx_mgr.create_context(
            profile_name=fhe_profile,
            override_params={"num_parties": num_nodes},
        )
        if ctx_bundle.mode != "threshold_multiparty":
            raise ValueError(
                f"FHE profile '{fhe_profile}' is not a threshold_multiparty profile."
            )
        cc = ctx_bundle.crypto_context
        cc_b64, cc_bytes = serialize_cc(cc)

        with ThreadPoolExecutor(max_workers=num_nodes) as pool:
            list(pool.map(
                lambda u: _http_json(f"{u}/api/fhe/init-context", {"job_id": job_id, "cc_b64": cc_b64}),
                internal_urls,
            ))

        # 2. Sequential N-Party Collective KeyGen Round 1: Node 0 -> Node 1 -> ... -> Node N-1
        need_mult_key = workload in ("risk_multiplication", "variance")
        t_kg0 = time.perf_counter()

        r1_list: List[Dict[str, Any]] = []
        r1_0 = _http_json(f"{internal_urls[0]}/api/fhe/keygen/round1", {
            "job_id": job_id,
            "need_mult_key": need_mult_key,
        })
        r1_list.append(r1_0)
        for i in range(1, num_nodes):
            r1_i = _http_json(f"{internal_urls[i]}/api/fhe/keygen/round1", {
                "job_id": job_id,
                "need_mult_key": need_mult_key,
                "prev_pk_b64": r1_list[-1]["pk_b64"],
                "em_a_b64": r1_0["em_share_b64"],
            })
            r1_list.append(r1_i)

        pk_joint_b64 = r1_list[-1]["pk_b64"]
        pk_joint = deserialize_pk(pk_joint_b64)
        key_tag = pk_joint.GetKeyTag()
        pk_bytes = int(r1_list[-1]["pk_bytes"])

        em_acc_b64: Optional[str] = None
        if need_mult_key:
            em_acc = deserialize_eval_key(r1_list[0]["em_share_b64"])
            for i in range(1, num_nodes):
                pk_i = deserialize_pk(r1_list[i]["pk_b64"])
                em_i = deserialize_eval_key(r1_list[i]["em_share_b64"])
                em_acc = cc.MultiAddEvalKeys(em_acc, em_i, pk_i.GetKeyTag())
            em_acc_b64, _ = serialize_eval_key(em_acc)

        # Round 2 in parallel across all N nodes
        with ThreadPoolExecutor(max_workers=num_nodes) as pool:
            r2_list = list(pool.map(
                lambda u: _http_json(f"{u}/api/fhe/keygen/round2", {
                    "job_id": job_id,
                    "pk_joint_b64": pk_joint_b64,
                    "em_abc_b64": em_acc_b64,
                }),
                internal_urls,
            ))

        eval_mult_key_bytes = 0
        if need_mult_key:
            em_parts = [deserialize_eval_key(r["em_mult_b64"]) for r in r2_list]
            em_final = em_parts[-1]
            for i in range(num_nodes - 2, -1, -1):
                tag_arg = key_tag if i == 0 else em_final.GetKeyTag()
                em_final = cc.MultiAddEvalMultKeys(em_parts[i], em_final, tag_arg)
            cc.InsertEvalMultKey([em_final])
            _, eval_mult_key_bytes = serialize_eval_key(em_final)

        keygen_time_s = time.perf_counter() - t_kg0

        # 3. Client-side SIMD Encryption & Upload over Network
        max_rows = max(int(n["row_count"]) for n in nodes)
        active_slot_width = min(ctx_bundle.batch_size, max_rows)

        t_enc0 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=num_nodes) as pool:
            enc_outputs = list(pool.map(
                lambda u: _http_json(f"{u}/api/fhe/encrypt", {
                    "job_id": job_id,
                    "workload": workload,
                    "value_col": value_col,
                    "weight_col": weight_col,
                    "filter_col": filter_col,
                    "filter_val": filter_val,
                    "scale_x": scale_x,
                    "scale_w": scale_w,
                    "active_slot_width": active_slot_width,
                    "batch_size": ctx_bundle.batch_size,
                }, timeout=180.0),
                internal_urls,
            ))
        enc_wall_time_s = time.perf_counter() - t_enc0

        # 4. Blind Homomorphic Evaluation on Coordinator
        t_eval0 = time.perf_counter()
        num_adds = 0
        num_mults = 0
        result_cts: List[Any] = []
        ct_digests: List[str] = []

        if workload == "aggregation":
            all_cts = []
            for eo in enc_outputs:
                ct_digests.extend(eo["ciphertext_digests"])
                for b64_ct in eo["primary_cts_b64"]:
                    all_cts.append(deserialize_ciphertext(b64_ct))
            acc = all_cts[0]
            for ct in all_cts[1:]:
                acc = cc.EvalAdd(acc, ct)
                num_adds += 1
            result_cts = [acc]

        elif workload == "filtered_aggregation":
            sum_cts = []
            mask_cts = []
            for eo in enc_outputs:
                ct_digests.extend(eo["ciphertext_digests"])
                for b64_x, b64_m in zip(eo["primary_cts_b64"], eo["secondary_cts_b64"]):
                    sum_cts.append(deserialize_ciphertext(b64_x))
                    mask_cts.append(deserialize_ciphertext(b64_m))
            acc_x = sum_cts[0]
            acc_m = mask_cts[0]
            for cx in sum_cts[1:]:
                acc_x = cc.EvalAdd(acc_x, cx)
                num_adds += 1
            for cm in mask_cts[1:]:
                acc_m = cc.EvalAdd(acc_m, cm)
                num_adds += 1
            result_cts = [acc_x, acc_m]

        elif workload == "risk_multiplication":
            prod_cts = []
            for eo in enc_outputs:
                ct_digests.extend(eo["ciphertext_digests"])
                for b64_x, b64_w in zip(eo["primary_cts_b64"], eo["secondary_cts_b64"]):
                    ct_x = deserialize_ciphertext(b64_x)
                    ct_w = deserialize_ciphertext(b64_w)
                    prod_cts.append(cc.EvalMult(ct_x, ct_w))
                    num_mults += 1
            acc = prod_cts[0]
            for p_ct in prod_cts[1:]:
                acc = cc.EvalAdd(acc, p_ct)
                num_adds += 1
            result_cts = [acc]

        elif workload == "variance":
            x_cts = []
            sq_cts = []
            for eo in enc_outputs:
                ct_digests.extend(eo["ciphertext_digests"])
                for b64_x in eo["primary_cts_b64"]:
                    ct_x = deserialize_ciphertext(b64_x)
                    x_cts.append(ct_x)
                    sq_cts.append(cc.EvalMult(ct_x, ct_x))
                    num_mults += 1
            acc_x = x_cts[0]
            for cx in x_cts[1:]:
                acc_x = cc.EvalAdd(acc_x, cx)
                num_adds += 1
            acc_sq = sq_cts[0]
            for cs in sq_cts[1:]:
                acc_sq = cc.EvalAdd(acc_sq, cs)
                num_adds += 1
            result_cts = [acc_x, acc_sq]

        eval_time_s = time.perf_counter() - t_eval0

        # 5. Distributed N-of-N Threshold Decryption
        t_dec0 = time.perf_counter()
        result_cts_b64 = [serialize_ciphertext(ct)[0] for ct in result_cts]
        with ThreadPoolExecutor(max_workers=num_nodes) as pool:
            dec_partials = list(pool.map(
                lambda u: _http_json(f"{u}/api/fhe/decrypt-partial", {
                    "job_id": job_id,
                    "result_cts_b64": result_cts_b64,
                    "workload": workload,
                    "purge_session": True,
                }),
                internal_urls,
            ))

        decrypted_sums: List[int] = []
        for idx in range(len(result_cts)):
            parts = [deserialize_ciphertext(dp["partials_b64"][idx]) for dp in dec_partials]
            pt_fused = cc.MultipartyDecryptFusion(parts)
            pt_fused.SetLength(active_slot_width)
            slot_vals = [int(v) for v in pt_fused.GetPackedValue()[:active_slot_width]]
            decrypted_sums.append(int(sum(slot_vals)))
        dec_time_s = time.perf_counter() - t_dec0

        total_time_s = time.perf_counter() - t_start
        coord_traffic = coord_mon.stop(fallback_protocol_bytes=0)

        total_pt_bytes = sum(int(eo["plaintext_bytes"]) for eo in enc_outputs)
        total_ct_bytes = sum(int(eo["total_ciphertext_bytes"]) for eo in enc_outputs)
        total_partial_bytes = sum(int(dp["partial_bytes"]) for dp in dec_partials)
        total_net_bytes = total_ct_bytes + total_partial_bytes + pk_bytes + eval_mult_key_bytes
        node_tx_sum = sum(int(eo.get("net_tx_bytes", 0)) for eo in enc_outputs) + coord_traffic.interface_tx_bytes_delta
        total_rows = sum(int(n["row_count"]) for n in nodes)

        metrics = self._compute_query_outputs(
            workload=workload,
            result1=decrypted_sums[0],
            result2=decrypted_sums[1] if len(decrypted_sums) > 1 else 0,
            total_count=total_rows,
            scale_x=scale_x,
            scale_w=scale_w,
        )

        return {
            "engine": "FHE",
            "protocol": fhe_profile,
            "scheme": ctx_bundle.scheme,
            "num_parties": num_nodes,
            "trust_assumption": f"{num_nodes}-of-{num_nodes} threshold decryption",
            "ring_dimension": ctx_bundle.ring_dimension,
            "plaintext_modulus": ctx_bundle.plaintext_modulus,
            "setup_time_s": round(ctx_bundle.context_init_time_s + keygen_time_s, 6),
            "encryption_time_s": round(enc_wall_time_s, 6),
            "computation_time_s": round(eval_time_s, 6),
            "decryption_time_s": round(dec_time_s, 6),
            "total_execution_time_s": round(total_time_s, 6),
            "num_homomorphic_additions": num_adds,
            "num_homomorphic_multiplications": num_mults,
            "num_ciphertexts_total": sum(int(eo["num_ciphertexts"]) for eo in enc_outputs),
            "plaintext_bytes": total_pt_bytes,
            "ciphertext_bytes": total_ct_bytes,
            "expansion_ratio": round(total_ct_bytes / total_pt_bytes, 3) if total_pt_bytes else 0.0,
            "public_key_bytes": pk_bytes,
            "eval_mult_key_bytes": eval_mult_key_bytes,
            "protocol_network_bytes": total_net_bytes,
            "kernel_tx_bytes": node_tx_sum,
            "communication_rounds": num_nodes + (2 if need_mult_key else 0) + 2,
            "outputs": metrics,
            "audit_proof": {
                "coordinator_saw_raw_rows": 0,
                "coordinator_held_secret_keys": 0,
                "sample_ciphertext_digests": ct_digests[:6],
                "public_key_digest": sha256_short(pk_joint_b64),
                "context_bytes": cc_bytes,
            },
        }

    def _run_distributed_smpc(
        self,
        job_id: str,
        nodes: List[Dict[str, Any]],
        internal_urls: List[str],
        workload: str,
        value_col: str,
        weight_col: Optional[str],
        filter_col: Optional[str],
        filter_val: Optional[str],
        scale_x: int,
        scale_w: int,
        smpc_protocol: str,
    ) -> Dict[str, Any]:
        t_start = time.perf_counter()
        num_nodes = len(nodes)
        spec = self.smpc_cfg.get_protocol_spec(smpc_protocol)
        if "shamir" in smpc_protocol and num_nodes < 3:
            raise ValueError(
                f"SMPC protocol '{smpc_protocol}' requires at least 3 organizations; "
                f"use 'semi_dishonest_majority_3p' or 'mascot_malicious_3p' for 2 organizations."
            )

        counts = [int(n["row_count"]) for n in nodes]

        # 1. Compile .mpc bytecode on Coordinator (zero private inputs)
        compiled = self.smpc_compiler.compile_and_package(
            compile_dir=self.smpc_compile_dir,
            counts=counts,
            spec=spec,
            workload=workload,
        )

        # 2. Fetch public TLS certs from each node (private keys stay on nodes)
        with ThreadPoolExecutor(max_workers=num_nodes) as pool:
            cert_responses = list(pool.map(
                lambda u: _http_json(f"{u}/api/smpc/cert"),
                internal_urls,
            ))
        peer_certs = {str(c["node_index"]): c["cert_pem"] for c in cert_responses}

        # Resolve hostnames to IPv4 addresses for MP-SPDZ -ip hosts.txt
        resolved_hosts: List[str] = []
        for n in nodes:
            hostname = urlparse(n["url"]).hostname or "127.0.0.1"
            try:
                resolved_hosts.append(socket.gethostbyname(hostname))
            except socket.gaierror:
                resolved_hosts.append(hostname)
        hosts_text = "\n".join(resolved_hosts) + "\n"

        # 3. Provision isolated Input-P<i>-0 + bytecode + hosts.txt on each node
        with ThreadPoolExecutor(max_workers=num_nodes) as pool:
            list(pool.map(
                lambda u: _http_json(f"{u}/api/smpc/prepare", {
                    "job_id": job_id,
                    "workload": workload,
                    "value_col": value_col,
                    "weight_col": weight_col,
                    "filter_col": filter_col,
                    "filter_val": filter_val,
                    "scale_x": scale_x,
                    "scale_w": scale_w,
                    "bytecode_files": compiled["bytecode_files"],
                    "peer_certs": peer_certs,
                    "hosts_text": hosts_text,
                }),
                internal_urls,
            ))

        # 4. Execute P0..P(N-1) concurrently across all N nodes
        with self._lock:
            active_port = self.smpc_cfg.base_port + (self._port_offset % 500) * 20
            self._port_offset += 1

        total_n = sum(counts)
        opt_batch = max(100, min(total_n, spec.batch_size))
        extra_args = ["-b", str(opt_batch)]

        t_exec0 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=num_nodes) as pool:
            party_results = list(pool.map(
                lambda u: _http_json(f"{u}/api/smpc/execute", {
                    "job_id": job_id,
                    "protocol_key": smpc_protocol,
                    "program_name": compiled["program_name"],
                    "base_port": active_port,
                    "extra_args": extra_args,
                    "workload": workload,
                    "num_parties": num_nodes,
                }, timeout=300.0),
                internal_urls,
            ))
        exec_wall_s = time.perf_counter() - t_exec0

        party_stdout = {int(r["node_index"]): r["stdout"] for r in party_results}
        party_stderr = {int(r["node_index"]): r["stderr"] for r in party_results}
        parsed = self.smpc_compiler.parse_party_logs(
            party_stdout=party_stdout,
            party_stderr=party_stderr,
            compile_time_s=compiled["compile_time_s"],
            wall_time_s=exec_wall_s,
        )

        total_time_s = time.perf_counter() - t_start
        vec_mult = 2 if workload in ("risk_multiplication", "filtered_aggregation") else 1
        plaintext_bytes = total_n * vec_mult * 8
        share_bytes = total_n * vec_mult * 16 * num_nodes
        node_tx_sum = sum(int(r.get("net_tx_bytes", 0)) for r in party_results)
        peak_rss_mb = round(sum(int(r.get("peak_rss_bytes", 0)) for r in party_results) / (1024.0 * 1024.0), 2)

        metrics = self._compute_query_outputs(
            workload=workload,
            result1=parsed["result1"],
            result2=parsed["result2"],
            total_count=parsed["total_count"],
            scale_x=scale_x,
            scale_w=scale_w,
        )

        return {
            "engine": "SMPC",
            "protocol": smpc_protocol,
            "binary": spec.binary_online,
            "scheme": spec.secret_sharing_scheme,
            "num_parties": num_nodes,
            "trust_assumption": spec.corruption_threshold,
            "compile_time_s": parsed["compile_time_s"],
            "preprocessing_time_s": parsed["preprocessing_time_s"],
            "input_sharing_time_s": parsed["input_sharing_time_s"],
            "computation_time_s": parsed["online_computation_time_s"],
            "output_reconstruction_time_s": parsed["output_reconstruction_time_s"],
            "total_execution_time_s": round(total_time_s, 6),
            "beaver_triples_consumed": parsed["triples_consumed"],
            "triple_reuse_prevented": True,
            "plaintext_bytes": plaintext_bytes,
            "share_bytes_all_parties": share_bytes,
            "expansion_ratio": round(share_bytes / plaintext_bytes, 3) if plaintext_bytes else 0.0,
            "protocol_network_bytes": parsed["global_network_bytes_sent"],
            "kernel_tx_bytes": node_tx_sum,
            "communication_rounds": parsed["communication_rounds"],
            "peak_rss_mb": peak_rss_mb,
            "outputs": metrics,
            "audit_proof": {
                "coordinator_saw_raw_rows": 0,
                "coordinator_saw_tls_private_keys": 0,
                "mpc_program_name": compiled["program_name"],
                "mpc_source_sha256": compiled["mpc_source_sha256"],
                "party_hosts": resolved_hosts,
                "cert_fingerprints": {str(c["node_index"]): c["cert_sha256"] for c in cert_responses},
            },
        }

    @staticmethod
    def _compute_query_outputs(
        workload: str,
        result1: int,
        result2: int,
        total_count: int,
        scale_x: int,
        scale_w: int,
    ) -> Dict[str, Any]:
        if workload == "aggregation":
            unscaled_sum = result1 / scale_x
            unscaled_mean = unscaled_sum / total_count if total_count else 0.0
            return {
                "scaled_sum": result1,
                "total_count": total_count,
                "sum": round(unscaled_sum, 6),
                "mean": round(unscaled_mean, 6),
                "primary_value": round(unscaled_sum, 6),
            }
        if workload == "risk_multiplication":
            unscaled_prod = result1 / (scale_x * scale_w)
            return {
                "scaled_weighted_sum": result1,
                "total_count": total_count,
                "weighted_inner_product": round(unscaled_prod, 6),
                "primary_value": round(unscaled_prod, 6),
            }
        if workload == "variance":
            unscaled_sum = result1 / scale_x
            unscaled_sq = result2 / (scale_x ** 2)
            mean = unscaled_sum / total_count if total_count else 0.0
            var = max(0.0, (unscaled_sq / total_count) - (mean ** 2)) if total_count else 0.0
            return {
                "scaled_sum": result1,
                "scaled_sum_sq": result2,
                "total_count": total_count,
                "sum": round(unscaled_sum, 6),
                "mean": round(mean, 6),
                "variance": round(var, 6),
                "std_dev": round(var ** 0.5, 6),
                "primary_value": round(var, 6),
            }
        if workload == "filtered_aggregation":
            unscaled_sum = result1 / scale_x
            matched_count = int(result2)
            mean = unscaled_sum / matched_count if matched_count > 0 else 0.0
            return {
                "scaled_sum": result1,
                "matched_count": matched_count,
                "total_count": total_count,
                "sum": round(unscaled_sum, 6),
                "mean": round(mean, 6),
                "primary_value": round(unscaled_sum, 6),
            }
        raise ValueError(f"Unsupported workload '{workload}'")

    def run_job(
        self,
        engine: str = "fhe",
        workload: str = "aggregation",
        value_col: str = "",
        weight_col: Optional[str] = None,
        filter_col: Optional[str] = None,
        filter_val: Optional[str] = None,
        scale_mode: str = "auto",
        fhe_profile: str = "threshold_bfv_128",
        smpc_protocol: str = "semi_dishonest_majority_3p",
        latency_profile: str = "local",
        bandwidth_profile: str = "high",
        apply_network: bool = False,
    ) -> Dict[str, Any]:
        """Executes a collaborative analytics job across all registered Client Nodes (N >= 2)."""
        engine = engine.lower().strip()
        if engine not in ("fhe", "smpc", "both"):
            raise ValueError(f"Invalid engine '{engine}'. Choose 'fhe', 'smpc', or 'both'.")
        if workload not in SUPPORTED_WORKLOADS:
            raise ValueError(f"Invalid workload '{workload}'. Choose from {SUPPORTED_WORKLOADS}.")

        cluster = self.get_cluster_status()
        num_nodes = cluster["node_count"]
        if cluster["reachable_nodes"] != num_nodes:
            raise RuntimeError(
                f"All {num_nodes} Client Nodes must be online; currently {cluster['reachable_nodes']}/{num_nodes} reachable."
            )
        if not cluster["all_datasets_loaded"]:
            missing = [n["organization"] for n in cluster["nodes"] if not n["dataset_loaded"]]
            raise ValueError(f"All {num_nodes} nodes must have a dataset loaded. Missing: {missing}")
        if not cluster["schema_aligned"]:
            raise ValueError(cluster["schema_error"] or "Schemas are not aligned across nodes.")

        nodes = cluster["nodes"]
        internal_urls = [self._internal_url(o) for o in self._orgs]
        if not value_col:
            value_col = cluster["shared_numeric_columns"][0]
        if value_col not in cluster["shared_numeric_columns"]:
            raise ValueError(f"Column '{value_col}' is not a shared numeric column ({cluster['shared_numeric_columns']}).")

        if workload == "risk_multiplication":
            w_col = weight_col or value_col
            if w_col not in cluster["shared_numeric_columns"]:
                raise ValueError(f"Weight column '{w_col}' is not a shared numeric column.")
            weight_col = w_col

        if workload == "filtered_aggregation":
            if not filter_col or filter_col not in cluster["shared_columns"]:
                raise ValueError(f"Filter column '{filter_col}' must be a shared column ({cluster['shared_columns']}).")
            if filter_val is None or str(filter_val).strip() == "":
                raise ValueError("A non-empty filter_val is required for filtered_aggregation.")

        scale_x, scale_w = self._resolve_scales(nodes, value_col, weight_col, workload, scale_mode)
        if engine in ("fhe", "both"):
            self._validate_plaintext_modulus(nodes, value_col, weight_col, workload, scale_x, scale_w)

        job_id = f"job_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        net_info: Dict[str, Any] = {
            "apply_network": apply_network,
            "latency_profile": latency_profile,
            "bandwidth_profile": bandwidth_profile,
            "tc_shaping_active": False,
            "measured_cross_node_rtt_ms": _measure_http_rtt_ms(internal_urls[0], probes=2),
        }

        try:
            if apply_network:
                emu = NetworkEmulator(interface=self.net_interface)
                coord_state = emu.configure_shaping(latency_profile=latency_profile, bandwidth_profile=bandwidth_profile)
                with ThreadPoolExecutor(max_workers=num_nodes) as pool:
                    node_net_states = list(pool.map(
                        lambda u: _http_json(f"{u}/api/network/configure", {
                            "latency_profile": latency_profile,
                            "bandwidth_profile": bandwidth_profile,
                        }),
                        internal_urls,
                    ))
                net_info.update({
                    "tc_shaping_active": bool(coord_state.tc_shaping_active or any(s.get("tc_shaping_active") for s in node_net_states)),
                    "configured_latency_ms": coord_state.configured_one_way_delay_ms,
                    "configured_bandwidth_mbps": coord_state.configured_bandwidth_mbps,
                    "measured_cross_node_rtt_ms": _measure_http_rtt_ms(internal_urls[0], probes=3),
                    "qdisc_summary": coord_state.tc_qdisc_summary,
                })

            fhe_res = None
            smpc_res = None
            if engine in ("fhe", "both"):
                fhe_res = self._fhe_executor.submit(
                    self._run_distributed_fhe,
                    job_id=job_id,
                    nodes=nodes,
                    internal_urls=internal_urls,
                    workload=workload,
                    value_col=value_col,
                    weight_col=weight_col,
                    filter_col=filter_col,
                    filter_val=filter_val,
                    scale_x=scale_x,
                    scale_w=scale_w,
                    fhe_profile=fhe_profile,
                ).result()
            if engine in ("smpc", "both"):
                smpc_res = self._run_distributed_smpc(
                    job_id=job_id,
                    nodes=nodes,
                    internal_urls=internal_urls,
                    workload=workload,
                    value_col=value_col,
                    weight_col=weight_col,
                    filter_col=filter_col,
                    filter_val=filter_val,
                    scale_x=scale_x,
                    scale_w=scale_w,
                    smpc_protocol=smpc_protocol,
                )
        finally:
            if apply_network:
                try:
                    NetworkEmulator(interface=self.net_interface).cleanup()
                except Exception:
                    pass
                with ThreadPoolExecutor(max_workers=num_nodes) as pool:
                    list(pool.map(lambda u: _http_json(f"{u}/api/network/reset", {}), internal_urls))

        cross_verification = None
        if fhe_res and smpc_res:
            exact_match = (
                abs(fhe_res["outputs"]["primary_value"] - smpc_res["outputs"]["primary_value"]) < 1e-6
            )
            ft = fhe_res["total_execution_time_s"]
            st = smpc_res["total_execution_time_s"]
            cross_verification = {
                "exact_match": exact_match,
                "faster_engine": "FHE" if ft <= st else "SMPC",
                "speedup_factor": round(max(ft, st) / min(ft, st), 3) if min(ft, st) > 0 else 1.0,
            }

        primary_result = (fhe_res or smpc_res)["outputs"]
        job_record = {
            "job_id": job_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "engine": engine,
            "workload": workload,
            "value_col": value_col,
            "weight_col": weight_col,
            "filter_col": filter_col,
            "filter_val": filter_val,
            "scale_x": scale_x,
            "scale_w": scale_w,
            "total_rows": cluster["total_rows"],
            "node_count": num_nodes,
            "participants": [
                {"node_id": n["node_id"], "organization": n["organization"], "tag": n["tag"], "row_count": n["row_count"]}
                for n in nodes
            ],
            "network": net_info,
            "outputs": primary_result,
            "fhe": fhe_res,
            "smpc": smpc_res,
            "cross_verification": cross_verification,
        }
        self._save_job(job_record)
        return job_record

    def _save_job(self, job: Dict[str, Any]) -> None:
        with self._lock:
            jobs = self.list_jobs()
            jobs.insert(0, job)
            self.jobs_path.write_text(json.dumps(jobs[:100], indent=2), encoding="utf-8")

    def list_jobs(self) -> List[Dict[str, Any]]:
        if not self.jobs_path.exists():
            return []
        try:
            return json.loads(self.jobs_path.read_text(encoding="utf-8"))
        except Exception:
            return []
