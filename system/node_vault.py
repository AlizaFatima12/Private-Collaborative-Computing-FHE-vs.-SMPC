"""Isolated Local Data Vault & MP-SPDZ Party Storage for a Single Client Node."""

import csv
from datetime import datetime, timezone
import io
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import threading
from typing import Any, Dict, List, Optional


SUPPORTED_WORKLOADS = (
    "aggregation",
    "risk_multiplication",
    "variance",
    "filtered_aggregation",
)


class NodeVault:
    """
    Manages a single data-owner node's private CSV dataset, schema metadata,
    local MP-SPDZ Player-Data (including its own private TLS key and Input-P<i>-0),
    and local participation audit log.
    """

    def __init__(
        self,
        node_index: int,
        data_dir: Optional[str | Path] = None,
        organization: Optional[str] = None,
        tag: Optional[str] = None,
    ):
        self.node_index = int(node_index)
        self.node_id = f"node_{self.node_index + 1}"
        base_dir = Path(
            data_dir
            or os.environ.get("PCC_NODE_DATA_DIR")
            or f"/app/node_data/node_{self.node_index + 1}"
        )
        self.data_dir = base_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.csv_path = self.data_dir / "dataset.csv"
        self.meta_path = self.data_dir / "meta.json"
        self.audit_path = self.data_dir / "audit_log.json"
        self.work_dir = self.data_dir / "mpspdz_work"
        self.player_data_dir = self.work_dir / "Player-Data"
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.player_data_dir.mkdir(parents=True, exist_ok=True)

        self._lock = threading.Lock()
        self._default_org = organization or os.environ.get(
            "PCC_NODE_ORG", f"Organization {self.node_index + 1}"
        )
        self._default_tag = tag or os.environ.get(
            "PCC_NODE_TAG", f"Node-{self.node_index + 1}"
        )
        self._init_meta_if_missing()

    def _init_meta_if_missing(self) -> None:
        if not self.meta_path.exists():
            meta = {
                "node_index": self.node_index,
                "node_id": self.node_id,
                "organization": self._default_org,
                "tag": self._default_tag,
                "dataset_loaded": False,
                "dataset_filename": None,
                "uploaded_at": None,
                "columns": [],
                "numeric_columns": [],
                "categorical_columns": [],
                "row_count": 0,
                "numeric_bounds": {},
            }
            self.meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    def _read_meta(self) -> Dict[str, Any]:
        return json.loads(self.meta_path.read_text(encoding="utf-8"))

    def _write_meta(self, meta: Dict[str, Any]) -> None:
        self.meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    def update_identity(self, organization: Optional[str] = None, tag: Optional[str] = None) -> Dict[str, Any]:
        with self._lock:
            meta = self._read_meta()
            if organization and organization.strip():
                meta["organization"] = organization.strip()
            if tag and tag.strip():
                meta["tag"] = tag.strip()
            self._write_meta(meta)
            return meta

    def set_node_index(self, new_index: int) -> Dict[str, Any]:
        """Updates this node's party index (used when organizations are added/removed dynamically)."""
        with self._lock:
            self.node_index = int(new_index)
            self.node_id = f"node_{self.node_index + 1}"
            meta = self._read_meta()
            meta["node_index"] = self.node_index
            meta["node_id"] = self.node_id
            self._write_meta(meta)
            return meta

    def save_dataset(
        self,
        csv_text: str,
        filename: str = "dataset.csv",
        organization: Optional[str] = None,
        tag: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Validates and saves a private CSV dataset strictly inside this node's vault."""
        cleaned_text = (csv_text or "").strip()
        if not cleaned_text:
            raise ValueError("CSV payload is empty.")

        reader = csv.DictReader(io.StringIO(cleaned_text))
        if not reader.fieldnames:
            raise ValueError("CSV has no header row.")
        cols = [c.strip() for c in reader.fieldnames if c is not None and c.strip()]
        if not cols:
            raise ValueError("CSV header columns are empty.")

        rows: List[Dict[str, str]] = []
        for raw_row in reader:
            row = {
                (k.strip() if k else ""): (v.strip() if v is not None else "")
                for k, v in raw_row.items()
                if k is not None and k.strip()
            }
            if any(row.values()):
                rows.append(row)

        if not rows:
            raise ValueError(f"CSV contains headers ({cols}) but 0 data rows.")

        numeric_cols: List[str] = []
        categorical_cols: List[str] = []
        numeric_bounds: Dict[str, Dict[str, Any]] = {}

        for col in cols:
            vals: List[float] = []
            is_num = True
            for r in rows:
                s = r.get(col, "")
                if s == "":
                    is_num = False
                    break
                try:
                    vf = float(s)
                    if not math.isfinite(vf):
                        is_num = False
                        break
                    vals.append(vf)
                except ValueError:
                    is_num = False
                    break
            if is_num and vals:
                numeric_cols.append(col)
                is_int = all(abs(v - round(v)) < 1e-9 for v in vals)
                numeric_bounds[col] = {
                    "min": min(vals),
                    "max": max(vals),
                    "is_integer": is_int,
                }
            else:
                categorical_cols.append(col)

        if not numeric_cols:
            raise ValueError(
                f"No numeric columns found in CSV (headers: {cols}). "
                "At least one numeric column is required."
            )

        with self._lock:
            self.csv_path.write_text(cleaned_text + "\n", encoding="utf-8")
            meta = self._read_meta()
            if organization and organization.strip():
                meta["organization"] = organization.strip()
            if tag and tag.strip():
                meta["tag"] = tag.strip()
            meta.update({
                "dataset_loaded": True,
                "dataset_filename": Path(filename).name,
                "uploaded_at": datetime.now(timezone.utc).isoformat(),
                "columns": cols,
                "numeric_columns": numeric_cols,
                "categorical_columns": categorical_cols,
                "row_count": len(rows),
                "numeric_bounds": numeric_bounds,
            })
            self._write_meta(meta)
            return meta

    def get_status(self) -> Dict[str, Any]:
        """Returns safe schema & isolation metadata (never raw rows)."""
        with self._lock:
            meta = self._read_meta()
            ssl_ready = (
                (self.player_data_dir / f"P{self.node_index}.pem").exists()
                and (self.player_data_dir / f"P{self.node_index}.key").exists()
            )
            return {
                **meta,
                "vault_path": str(self.data_dir),
                "ssl_key_local": ssl_ready,
                "isolation_policy": "Private CSV, FHE secret key share, and MP-SPDZ Input-P file never leave this node.",
            }

    def get_local_preview(self, limit: int = 8) -> Dict[str, Any]:
        """Returns private row previews and local stats ONLY for this node's own local UI."""
        with self._lock:
            meta = self._read_meta()
            if not meta.get("dataset_loaded") or not self.csv_path.exists():
                return {"meta": meta, "preview_rows": [], "local_stats": {}}

            with open(self.csv_path, "r", encoding="utf-8", newline="") as f:
                rows = list(csv.DictReader(f))

            local_stats: Dict[str, Dict[str, float]] = {}
            for nc in meta.get("numeric_columns", []):
                vals = [float(r[nc]) for r in rows]
                local_stats[nc] = {
                    "sum": round(sum(vals), 4),
                    "mean": round(sum(vals) / len(vals), 4) if vals else 0.0,
                    "min": min(vals) if vals else 0.0,
                    "max": max(vals) if vals else 0.0,
                }

            cat_samples: Dict[str, List[str]] = {}
            for cc in meta.get("categorical_columns", []):
                uniq = sorted({str(r.get(cc, "")).strip() for r in rows if str(r.get(cc, "")).strip()})
                cat_samples[cc] = uniq[:20]

            return {
                "meta": meta,
                "preview_rows": rows[:limit],
                "local_stats": local_stats,
                "category_values": cat_samples,
            }

    def read_local_rows(self) -> List[Dict[str, str]]:
        """Reads raw CSV rows strictly inside the local node."""
        with self._lock:
            if not self.csv_path.exists():
                raise ValueError(f"{self.node_id}: No dataset loaded yet.")
            with open(self.csv_path, "r", encoding="utf-8", newline="") as f:
                rows = [
                    {(k.strip() if k else ""): (v.strip() if v is not None else "") for k, v in r.items()}
                    for r in csv.DictReader(f)
                ]
            if not rows:
                raise ValueError(f"{self.node_id}: Dataset file is empty.")
            return rows

    def extract_vectors(
        self,
        value_col: str,
        weight_col: Optional[str] = None,
        workload: str = "aggregation",
        scale_x: int = 1,
        scale_w: int = 1,
        filter_col: Optional[str] = None,
        filter_val: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Extracts integer-scaled private vectors for cryptographic processing."""
        if workload not in SUPPORTED_WORKLOADS:
            raise ValueError(f"Unsupported workload '{workload}'. Supported: {SUPPORTED_WORKLOADS}")

        rows = self.read_local_rows()
        if value_col not in rows[0]:
            raise ValueError(f"{self.node_id}: Column '{value_col}' not found.")

        raw_x = [float(r[value_col]) for r in rows]
        x_ints = [int(round(v * scale_x)) for v in raw_x]

        if workload == "risk_multiplication":
            w_col = weight_col or value_col
            if w_col not in rows[0]:
                raise ValueError(f"{self.node_id}: Weight column '{w_col}' not found.")
            raw_w = [float(r[w_col]) for r in rows]
            w_ints = [int(round(v * scale_w)) for v in raw_w]
            return {"primary": x_ints, "secondary": w_ints, "row_count": len(x_ints)}

        if workload == "variance":
            return {"primary": x_ints, "secondary": [], "row_count": len(x_ints)}

        if workload == "filtered_aggregation":
            if not filter_col or filter_col not in rows[0]:
                raise ValueError(f"{self.node_id}: Filter column '{filter_col}' not found.")
            target = (filter_val or "").strip()
            mask = [1 if str(r.get(filter_col, "")).strip() == target else 0 for r in rows]
            masked_x = [xv if mv == 1 else 0 for xv, mv in zip(x_ints, mask)]
            return {"primary": masked_x, "secondary": mask, "row_count": len(x_ints)}

        # aggregation
        return {"primary": x_ints, "secondary": [], "row_count": len(x_ints)}

    def ensure_node_ssl_cert(self) -> str:
        """
        Generates ONLY this node's own private TLS key (P<i>.key) and public cert (P<i>.pem).
        Private keys never leave this node.
        """
        pem_path = self.player_data_dir / f"P{self.node_index}.pem"
        key_path = self.player_data_dir / f"P{self.node_index}.key"
        if not (pem_path.exists() and key_path.exists()):
            subprocess.run(
                [
                    "openssl",
                    "req",
                    "-newkey",
                    "rsa:2048",
                    "-nodes",
                    "-x509",
                    "-days",
                    "365",
                    "-out",
                    str(pem_path),
                    "-keyout",
                    str(key_path),
                    "-subj",
                    f"/CN=P{self.node_index}",
                ],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        return pem_path.read_text(encoding="utf-8")

    def install_peer_certs(self, certs_by_index: Dict[int, str]) -> None:
        """Installs all parties' public .pem certificates and runs c_rehash locally."""
        for idx, pem_text in certs_by_index.items():
            target = self.player_data_dir / f"P{int(idx)}.pem"
            if int(idx) != self.node_index or not target.exists():
                target.write_text(pem_text, encoding="utf-8")
        for old_link in self.player_data_dir.glob("*.0"):
            old_link.unlink(missing_ok=True)
        subprocess.run(
            ["c_rehash", str(self.player_data_dir)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def purge_smpc_preprocessing(self) -> None:
        """Purges previous triples/schedules/bytecode to enforce Beaver triple non-reuse."""
        if not self.player_data_dir.exists():
            return
        for item in self.player_data_dir.iterdir():
            if item.name.startswith("Input-P") or item.name.endswith((".pem", ".key", ".0")):
                continue
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
            else:
                item.unlink(missing_ok=True)

    def write_smpc_input(self, workload: str, vectors: Dict[str, Any]) -> Path:
        """Writes ONLY this node's private Input-P<i>-0 file into its own Player-Data."""
        in_file = self.player_data_dir / f"Input-P{self.node_index}-0"
        primary = vectors["primary"]
        secondary = vectors.get("secondary", [])
        with open(in_file, "w", encoding="utf-8") as f:
            f.write(" ".join(str(x) for x in primary) + "\n")
            if workload in ("risk_multiplication", "filtered_aggregation") and secondary:
                f.write(" ".join(str(w) for w in secondary) + "\n")
        return in_file

    def append_audit_event(self, event: Dict[str, Any]) -> None:
        with self._lock:
            history: List[Dict[str, Any]] = []
            if self.audit_path.exists():
                try:
                    history = json.loads(self.audit_path.read_text(encoding="utf-8"))
                except Exception:
                    history = []
            history.insert(0, event)
            self.audit_path.write_text(json.dumps(history[:50], indent=2), encoding="utf-8")

    def get_audit_history(self) -> List[Dict[str, Any]]:
        with self._lock:
            if not self.audit_path.exists():
                return []
            try:
                return json.loads(self.audit_path.read_text(encoding="utf-8"))
            except Exception:
                return []
