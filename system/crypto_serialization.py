"""OpenFHE Cross-Process Wire Serialization and Cryptographic Digest Helpers."""

import base64
import hashlib
from typing import Any

from fhe.context import require_openfhe


def b64_encode(data: bytes | str) -> str:
    """Encodes raw binary or latin1 string payload into ASCII base64."""
    raw = data if isinstance(data, bytes) else data.encode("latin1")
    return base64.b64encode(raw).decode("ascii")


def b64_decode(text: str) -> bytes:
    """Decodes an ASCII base64 string into raw binary bytes."""
    return base64.b64decode(text.encode("ascii"))


def sha256_short(data: bytes | str, length: int = 16) -> str:
    """Returns a short SHA-256 hex digest for zero-leakage audit logs."""
    raw = data if isinstance(data, bytes) else data.encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:length]


def serialize_cc(cc: Any) -> tuple[str, int]:
    """Serializes an OpenFHE CryptoContext (without secret keys) to base64."""
    fhe = require_openfhe()
    raw = fhe.Serialize(cc, fhe.BINARY)
    return b64_encode(raw), len(raw)


def deserialize_cc(b64_str: str) -> Any:
    """Deserializes an OpenFHE CryptoContext from base64."""
    fhe = require_openfhe()
    return fhe.DeserializeCryptoContextString(b64_decode(b64_str), fhe.BINARY)


def serialize_pk(pk: Any) -> tuple[str, int]:
    """Serializes an OpenFHE PublicKey to base64."""
    fhe = require_openfhe()
    raw = fhe.Serialize(pk, fhe.BINARY)
    return b64_encode(raw), len(raw)


def deserialize_pk(b64_str: str) -> Any:
    """Deserializes an OpenFHE PublicKey from base64."""
    fhe = require_openfhe()
    return fhe.DeserializePublicKeyString(b64_decode(b64_str), fhe.BINARY)


def serialize_eval_key(ek: Any) -> tuple[str, int]:
    """Serializes an OpenFHE EvalKey share to base64."""
    fhe = require_openfhe()
    raw = fhe.Serialize(ek, fhe.BINARY)
    return b64_encode(raw), len(raw)


def deserialize_eval_key(b64_str: str) -> Any:
    """Deserializes an OpenFHE EvalKey share from base64."""
    fhe = require_openfhe()
    return fhe.DeserializeEvalKeyString(b64_decode(b64_str), fhe.BINARY)


def serialize_ciphertext(ct: Any) -> tuple[str, int]:
    """Serializes an OpenFHE Ciphertext or partial decryption share to base64."""
    fhe = require_openfhe()
    raw = fhe.Serialize(ct, fhe.BINARY)
    return b64_encode(raw), len(raw)


def deserialize_ciphertext(b64_str: str) -> Any:
    """Deserializes an OpenFHE Ciphertext or partial decryption share from base64."""
    fhe = require_openfhe()
    return fhe.DeserializeCiphertextString(b64_decode(b64_str), fhe.BINARY)
