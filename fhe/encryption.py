"""OpenFHE SIMD Packing, Encryption, Serialization, and Network Transport."""

from dataclasses import dataclass, field
import time
from typing import Any, Dict, List
from dataset.schemas import CollaborativeDataset
from fhe.context import FHEContextBundle, require_openfhe
from fhe.key_generation import FHEKeyMaterial, transmit_payload_over_tcp


@dataclass
class ParticipantCiphertexts:
    """Holds encrypted SIMD ciphertexts belonging to one participant."""

    participant_id: str
    amount_ciphertexts: List[Any] = field(default_factory=list)
    risk_ciphertexts: List[Any] = field(default_factory=list)
    serialized_bytes_total: int = 0
    num_ciphertexts: int = 0
    encryption_cpu_time_s: float = 0.0


@dataclass
class EncryptedDatasetBundle:
    """All participants' encrypted inputs, storage overhead metrics, and network transfer timing."""

    workload: str
    batch_size: int
    active_slot_width: int
    participant_ciphertexts: Dict[str, ParticipantCiphertexts]
    encryption_cpu_time_s: float
    encryption_network_time_s: float
    total_encryption_time_s: float
    total_ciphertext_bytes: int
    total_plaintext_bytes: int
    ciphertext_expansion_ratio: float
    num_ciphertexts_total: int
    single_ciphertext_bytes: int

    def to_metrics_dict(self) -> Dict[str, Any]:
        return {
            "fhe_encryption_cpu_time_s": round(self.encryption_cpu_time_s, 6),
            "fhe_encryption_network_time_s": round(self.encryption_network_time_s, 6),
            "fhe_total_encryption_time_s": round(self.total_encryption_time_s, 6),
            "fhe_total_ciphertext_bytes": self.total_ciphertext_bytes,
            "fhe_total_plaintext_bytes": self.total_plaintext_bytes,
            "fhe_ciphertext_expansion_ratio": round(self.ciphertext_expansion_ratio, 4),
            "fhe_num_ciphertexts_total": self.num_ciphertexts_total,
            "fhe_single_ciphertext_bytes": self.single_ciphertext_bytes,
        }


class FHEEncryptor:
    """Packs participant integer vectors into SIMD plaintexts and encrypts under the joint public key."""

    @staticmethod
    def _chunk_and_pad(values: List[int], batch_size: int, pad_width: int) -> List[List[int]]:
        chunks: List[List[int]] = []
        for i in range(0, len(values), batch_size):
            sl = values[i : i + batch_size]
            if len(sl) < pad_width:
                sl = sl + [0] * (pad_width - len(sl))
            chunks.append(sl)
        return chunks

    @classmethod
    def encrypt_dataset(
        cls,
        ctx_bundle: FHEContextBundle,
        key_material: FHEKeyMaterial,
        dataset: CollaborativeDataset,
        workload: str = "aggregation",
        enable_network_transport: bool = True,
    ) -> EncryptedDatasetBundle:
        fhe = require_openfhe()
        cc = ctx_bundle.crypto_context
        pk = key_material.public_key
        batch_size = ctx_bundle.batch_size

        max_part_len = max(p.count for p in dataset.partitions.values())
        active_slot_width = min(batch_size, max_part_len)

        t_total_start = time.perf_counter()
        total_cpu_time_s = 0.0
        all_serialized_blobs: List[bytes] = []
        part_ct_map: Dict[str, ParticipantCiphertexts] = {}
        num_ct_total = 0
        single_ct_bytes = 0

        include_risk = workload == "risk_multiplication"

        for part in dataset.get_ordered_partitions():
            t_p0 = time.perf_counter()
            amt_chunks = cls._chunk_and_pad(part.amounts, batch_size, active_slot_width)
            amt_cts: List[Any] = []
            p_bytes = 0

            for chunk in amt_chunks:
                pt = cc.MakePackedPlaintext(chunk)
                ct = cc.Encrypt(pk, pt)
                amt_cts.append(ct)
                blob = fhe.Serialize(ct, fhe.BINARY)
                all_serialized_blobs.append(blob)
                p_bytes += len(blob)
                if single_ct_bytes == 0:
                    single_ct_bytes = len(blob)

            risk_cts: List[Any] = []
            if include_risk:
                risk_chunks = cls._chunk_and_pad(
                    part.risk_scores, batch_size, active_slot_width
                )
                for r_chunk in risk_chunks:
                    pt_r = cc.MakePackedPlaintext(r_chunk)
                    ct_r = cc.Encrypt(pk, pt_r)
                    risk_cts.append(ct_r)
                    blob_r = fhe.Serialize(ct_r, fhe.BINARY)
                    all_serialized_blobs.append(blob_r)
                    p_bytes += len(blob_r)

            p_cpu = time.perf_counter() - t_p0
            total_cpu_time_s += p_cpu
            p_num_ct = len(amt_cts) + len(risk_cts)
            num_ct_total += p_num_ct

            part_ct_map[part.participant_id] = ParticipantCiphertexts(
                participant_id=part.participant_id,
                amount_ciphertexts=amt_cts,
                risk_ciphertexts=risk_cts,
                serialized_bytes_total=p_bytes,
                num_ciphertexts=p_num_ct,
                encryption_cpu_time_s=p_cpu,
            )

        total_ct_bytes = sum(len(b) for b in all_serialized_blobs)
        if include_risk:
            total_pt_bytes = dataset.total_risk_plaintext_bytes
        else:
            total_pt_bytes = dataset.total_amounts_plaintext_bytes

        expansion_ratio = (
            float(total_ct_bytes) / float(total_pt_bytes) if total_pt_bytes > 0 else 0.0
        )

        net_time_s = 0.0
        if enable_network_transport and ctx_bundle.network_transport_enabled:
            _, net_time_s = transmit_payload_over_tcp(all_serialized_blobs)

        total_enc_time_s = time.perf_counter() - t_total_start

        return EncryptedDatasetBundle(
            workload=workload,
            batch_size=batch_size,
            active_slot_width=active_slot_width,
            participant_ciphertexts=part_ct_map,
            encryption_cpu_time_s=total_cpu_time_s,
            encryption_network_time_s=net_time_s,
            total_encryption_time_s=total_enc_time_s,
            total_ciphertext_bytes=total_ct_bytes,
            total_plaintext_bytes=total_pt_bytes,
            ciphertext_expansion_ratio=expansion_ratio,
            num_ciphertexts_total=num_ct_total,
            single_ciphertext_bytes=single_ct_bytes,
        )
