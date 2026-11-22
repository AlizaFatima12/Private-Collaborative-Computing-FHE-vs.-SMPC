"""OpenFHE Single-Key and 3-Party Threshold Collective Key Generation."""

from dataclasses import dataclass, field
import socket
import struct
import threading
import time
from typing import Any, Dict, List
from fhe.context import FHEContextBundle, require_openfhe


def transmit_payload_over_tcp(payloads: List[bytes], host: str = "127.0.0.1") -> tuple[int, float]:
    """
    Transmits a list of serialized cryptographic payloads over real TCP connections
    so that Linux kernel traffic control (tc/netem) latency, bandwidth shaping,
    and /proc/net/dev byte counters are authentically exercised.

    Returns:
        (total_application_bytes_sent, elapsed_transfer_time_s)
    """
    if not payloads:
        return 0, 0.0

    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind((host, 0))
    _, port = server_sock.getsockname()
    server_sock.listen(1)

    received_sizes: List[int] = []
    server_error: List[Exception] = []

    def _receiver():
        try:
            conn, _ = server_sock.accept()
            with conn:
                while True:
                    hdr = _recv_exact(conn, 4)
                    if not hdr:
                        break
                    (msg_len,) = struct.unpack("!I", hdr)
                    if msg_len == 0:
                        conn.sendall(b"\x01")
                        break
                    data = _recv_exact(conn, msg_len)
                    received_sizes.append(len(data))
                    # Send 1-byte protocol round acknowledgement
                    conn.sendall(b"\x01")
        except Exception as exc:
            server_error.append(exc)
        finally:
            server_sock.close()

    def _recv_exact(sock: socket.socket, n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            chunk = sock.recv(min(65536, n - len(buf)))
            if not chunk:
                raise ConnectionError("Unexpected EOF during cryptographic TCP transfer")
            buf.extend(chunk)
        return bytes(buf)

    rx_thread = threading.Thread(target=_receiver, daemon=True)
    rx_thread.start()

    t0 = time.perf_counter()
    total_bytes = 0
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client_sock:
        client_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        client_sock.connect((host, port))
        for blob in payloads:
            n_bytes = len(blob)
            client_sock.sendall(struct.pack("!I", n_bytes) + blob)
            ack = client_sock.recv(1)
            if ack != b"\x01":
                raise RuntimeError("Failed to receive protocol round ACK")
            total_bytes += n_bytes
        client_sock.sendall(struct.pack("!I", 0))
        client_sock.recv(1)

    rx_thread.join(timeout=60.0)
    elapsed = time.perf_counter() - t0
    if server_error:
        raise RuntimeError(f"TCP receiver error: {server_error[0]}") from server_error[0]

    return total_bytes, elapsed


@dataclass
class FHEKeyMaterial:
    """Holds generated OpenFHE keys (never serialized to repository storage) and metrics."""

    mode: str
    num_parties: int
    public_key: Any
    secret_key_shares: List[Any] = field(default_factory=list)
    pk_keygen_time_s: float = 0.0
    evalmult_keygen_time_s: float = 0.0
    keygen_network_time_s: float = 0.0
    total_keygen_time_s: float = 0.0
    public_key_size_bytes: int = 0
    eval_mult_key_size_bytes: int = 0
    keygen_network_bytes: int = 0
    keygen_rounds: int = 0

    def to_metrics_dict(self) -> Dict[str, Any]:
        return {
            "fhe_pk_keygen_time_s": round(self.pk_keygen_time_s, 6),
            "fhe_evalmult_keygen_time_s": round(self.evalmult_keygen_time_s, 6),
            "fhe_keygen_network_time_s": round(self.keygen_network_time_s, 6),
            "fhe_total_keygen_time_s": round(self.total_keygen_time_s, 6),
            "fhe_public_key_size_bytes": self.public_key_size_bytes,
            "fhe_eval_mult_key_size_bytes": self.eval_mult_key_size_bytes,
            "fhe_keygen_network_bytes": self.keygen_network_bytes,
            "fhe_keygen_rounds": self.keygen_rounds,
        }


class FHEKeyGenerator:
    """Executes either 3-Party Collective Threshold KeyGen or Single-Key Baseline KeyGen."""

    @staticmethod
    def generate_keys(
        ctx_bundle: FHEContextBundle,
        generate_eval_mult_key: bool = True,
        enable_network_transport: bool = True,
    ) -> FHEKeyMaterial:
        fhe = require_openfhe()
        cc = ctx_bundle.crypto_context
        t_start = time.perf_counter()

        if ctx_bundle.mode == "threshold_multiparty":
            if ctx_bundle.num_parties != 3:
                raise ValueError(
                    f"Threshold multiparty FHE expects 3 participants; got {ctx_bundle.num_parties}"
                )

            # Phase 1: 3-Party Collective Public Key Generation (s = s_A + s_B + s_C)
            t_pk0 = time.perf_counter()
            kp_a = cc.KeyGen()
            kp_b = cc.MultipartyKeyGen(kp_a.publicKey)
            kp_c = cc.MultipartyKeyGen(kp_b.publicKey)
            if not (kp_a.good() and kp_b.good() and kp_c.good()):
                raise RuntimeError("OpenFHE 3-party threshold public key generation failed.")
            pk_keygen_time_s = time.perf_counter() - t_pk0

            joint_pk = kp_c.publicKey
            pk_a_bytes = fhe.Serialize(kp_a.publicKey, fhe.BINARY)
            pk_b_bytes = fhe.Serialize(kp_b.publicKey, fhe.BINARY)
            pk_c_bytes = fhe.Serialize(joint_pk, fhe.BINARY)
            pk_size_bytes = len(pk_c_bytes)

            network_payloads: List[bytes] = [pk_a_bytes, pk_b_bytes, pk_c_bytes]
            rounds = 3  # A -> B -> C -> broadcast

            # Phase 2: 3-Party Collective Relinearization (EvalMult) Key Generation
            t_em0 = time.perf_counter()
            eval_mult_key_size = 0
            if generate_eval_mult_key:
                # Round 1: Individual key-switch shares against base secret s_A, s_B, s_C
                em_a = cc.KeySwitchGen(kp_a.secretKey, kp_a.secretKey)
                em_b = cc.MultiKeySwitchGen(kp_b.secretKey, kp_b.secretKey, em_a)
                em_c = cc.MultiKeySwitchGen(kp_c.secretKey, kp_c.secretKey, em_a)

                em_ab = cc.MultiAddEvalKeys(em_a, em_b, kp_b.publicKey.GetKeyTag())
                em_abc = cc.MultiAddEvalKeys(em_ab, em_c, kp_c.publicKey.GetKeyTag())

                # Round 2: Multiply combined eval key by each party's secret key share
                em_c_abc = cc.MultiMultEvalKey(kp_c.secretKey, em_abc, kp_c.publicKey.GetKeyTag())
                em_b_abc = cc.MultiMultEvalKey(kp_b.secretKey, em_abc, kp_c.publicKey.GetKeyTag())
                em_a_abc = cc.MultiMultEvalKey(kp_a.secretKey, em_abc, kp_c.publicKey.GetKeyTag())

                em_bc_abc = cc.MultiAddEvalMultKeys(
                    em_c_abc, em_b_abc, em_c_abc.GetKeyTag()
                )
                em_final = cc.MultiAddEvalMultKeys(
                    em_a_abc, em_bc_abc, kp_c.publicKey.GetKeyTag()
                )
                cc.InsertEvalMultKey([em_final])

                em_final_bytes = fhe.SerializeEvalMultKeyString(fhe.BINARY, "")
                eval_mult_key_size = len(em_final_bytes)
                # In 2-round collective EvalMultKey generation, parties exchange intermediate eval keys
                network_payloads.extend([
                    fhe.Serialize(em_a, fhe.BINARY),
                    fhe.Serialize(em_abc, fhe.BINARY),
                    fhe.Serialize(em_final, fhe.BINARY),
                ])
                rounds += 2

            evalmult_keygen_time_s = time.perf_counter() - t_em0

            keygen_net_bytes = sum(len(p) for p in network_payloads)
            keygen_net_time_s = 0.0
            if enable_network_transport and ctx_bundle.network_transport_enabled:
                keygen_net_bytes, keygen_net_time_s = transmit_payload_over_tcp(
                    network_payloads
                )

            total_time_s = time.perf_counter() - t_start
            return FHEKeyMaterial(
                mode="threshold_multiparty",
                num_parties=3,
                public_key=joint_pk,
                secret_key_shares=[kp_a.secretKey, kp_b.secretKey, kp_c.secretKey],
                pk_keygen_time_s=pk_keygen_time_s,
                evalmult_keygen_time_s=evalmult_keygen_time_s,
                keygen_network_time_s=keygen_net_time_s,
                total_keygen_time_s=total_time_s,
                public_key_size_bytes=pk_size_bytes,
                eval_mult_key_size_bytes=eval_mult_key_size,
                keygen_network_bytes=keygen_net_bytes,
                keygen_rounds=rounds,
            )

        else:
            # Single-Key Baseline (Separated trust model)
            t_pk0 = time.perf_counter()
            kp = cc.KeyGen()
            if not kp.good():
                raise RuntimeError("OpenFHE single-key generation failed.")
            pk_keygen_time_s = time.perf_counter() - t_pk0

            pk_bytes = fhe.Serialize(kp.publicKey, fhe.BINARY)
            pk_size_bytes = len(pk_bytes)

            t_em0 = time.perf_counter()
            eval_mult_key_size = 0
            if generate_eval_mult_key:
                cc.EvalMultKeyGen(kp.secretKey)
                em_bytes = fhe.SerializeEvalMultKeyString(fhe.BINARY, "")
                eval_mult_key_size = len(em_bytes)
            evalmult_keygen_time_s = time.perf_counter() - t_em0

            keygen_net_bytes = pk_size_bytes
            keygen_net_time_s = 0.0
            if enable_network_transport and ctx_bundle.network_transport_enabled:
                keygen_net_bytes, keygen_net_time_s = transmit_payload_over_tcp([pk_bytes])

            total_time_s = time.perf_counter() - t_start
            return FHEKeyMaterial(
                mode=ctx_bundle.mode,
                num_parties=1,
                public_key=kp.publicKey,
                secret_key_shares=[kp.secretKey],
                pk_keygen_time_s=pk_keygen_time_s,
                evalmult_keygen_time_s=evalmult_keygen_time_s,
                keygen_network_time_s=keygen_net_time_s,
                total_keygen_time_s=total_time_s,
                public_key_size_bytes=pk_size_bytes,
                eval_mult_key_size_bytes=eval_mult_key_size,
                keygen_network_bytes=keygen_net_bytes,
                keygen_rounds=1,
            )
