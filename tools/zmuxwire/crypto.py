"""Key schedule for pipe joins (MULTIPATH.md Section 10)."""

import hashlib
import hmac

from . import registry as R
from . import frames as F

FLAG_E, FLAG_P, FLAG_I = 0x01, 0x04, 0x08


def hkdf_extract(salt, ikm):
    return hmac.new(salt, ikm, hashlib.sha256).digest()


def hkdf_expand(prk, info, length=32):
    out = b""
    t = b""
    i = 1
    while len(out) < length:
        t = hmac.new(prk, t + info + bytes([i]), hashlib.sha256).digest()
        out += t
        i += 1
    return out[:length]


def select_secret_mode(flags_a, flags_b):
    """Deterministic secret-source selection (MULTIPATH.md 10.1).

    Returns (exporter, psk, insecure) or None when multipath stays disabled.
    """
    both = flags_a & flags_b
    exporter = bool(both & FLAG_E)
    psk = bool(both & FLAG_P)
    if not exporter and not psk:
        if both & FLAG_I:
            return (False, False, True)
        return None
    return (exporter, psk, False)


def session_secret(initiator_nonce, responder_nonce, exporter_value=b"", psk=b""):
    """IKM = exporter value (fixed length: 32 bytes, 64 for a split bootstrap, or empty)
    || PSK (or empty)."""
    if len(exporter_value) not in (0, 32, 64):
        raise ValueError("exporter value must be 32 or 64 bytes")
    salt = R.LABELS["hkdf_salt_prefix"].encode() + initiator_nonce + responder_nonce
    return hkdf_extract(salt, exporter_value + psk)


def join_key(secret, joiner_role):
    label = R.LABELS["hkdf_join_initiator"] if joiner_role == "initiator" else R.LABELS[
        "hkdf_join_responder"]
    return hkdf_expand(secret, label.encode(), 32)


def join_token(secret, joiner_role, join_frame, carrier_binding=b""):
    """Token for a PIPE_JOIN dict (its own ``token`` field is ignored)."""
    f = dict(join_frame)
    f["token"] = b"\x00" * 16
    prefix = F.join_bytes_without_token(f)
    mac = hmac.new(join_key(secret, joiner_role), prefix + carrier_binding,
                   hashlib.sha256).digest()
    return mac[:16]


def verify_join(secret, joiner_role, join_frame, carrier_binding=b""):
    return hmac.compare_digest(join_token(secret, joiner_role, join_frame, carrier_binding),
                               join_frame["token"])
