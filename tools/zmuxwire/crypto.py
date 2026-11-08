"""Session secret and join keys (MULTIPATH.md Section 10, secure-carrier/SPEC.md 11.4)."""

import hashlib
import hmac

from . import registry as R
from . import frames as F

FLAG_E, FLAG_P = 0x01, 0x04


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
    """Secret source (MULTIPATH.md 10.1). None without a source; flags are never intersected.

    Raises ValueError when both are non-zero and differ, which closes the session.
    """
    mask = FLAG_E | FLAG_P
    a, b = flags_a & mask, flags_b & mask
    if not a or not b:
        return None
    if a != b:
        raise ValueError("Secret Flags differ")
    return bool(a & FLAG_E), bool(a & FLAG_P)


def session_secret(initiator_nonce, responder_nonce, exporter_value=b"", psk=b""):
    """IKM = exporter value (fixed length: 32 bytes, 64 for a split bootstrap, or empty)
    || PSK (or empty)."""
    if len(exporter_value) not in (0, 32, 64):
        raise ValueError("exporter value must be 32 or 64 bytes")
    salt = R.LABELS["hkdf_salt_prefix"].encode() + initiator_nonce + responder_nonce
    return hkdf_extract(salt, exporter_value + psk)


DEFAULT_SESSION_CRYPTO = {"suites": [1], "groups": [2]}


def session_crypto(crypto_i=None, crypto_r=None):
    """session_crypto of MULTIPATH.md Section 3.7: Value(initiator) || Value(responder)."""
    return F.encode_session_crypto(crypto_i or DEFAULT_SESSION_CRYPTO) + \
        F.encode_session_crypto(crypto_r or DEFAULT_SESSION_CRYPTO)


def join_psk(epoch_secret, joiner_role, suite=1, crypto_i=None, crypto_r=None, length=32):
    """join_psk of the joiner's role (secure-carrier/SPEC.md Section 11.4), SHA-256.

    ``epoch_secret`` is the secret of the epoch (the session secret for epoch 0);
    ``length`` is the Suite's HashLen."""
    label = R.LABELS["hkdf_join_initiator"] if joiner_role == "initiator" else R.LABELS[
        "hkdf_join_responder"]
    h = hashlib.sha256(session_crypto(crypto_i, crypto_r)).digest()
    return hkdf_expand(epoch_secret, label.encode() + h + F.encode_varint(suite), length)


def combine(salt, shared_secrets, hashfn=hashlib.sha256):
    """Combine of secure-carrier/SPEC.md Section 11.6: a chain of HKDF-Extract."""
    out = salt
    for ss in shared_secrets:
        out = hmac.new(out, ss, hashfn).digest()
    return out


def next_epoch_secret(epoch_secret, epoch, offer_body, answer_body, shared_secrets,
                      crypto_i=None, crypto_r=None):
    """epoch_secret[e+1] of MULTIPATH.md Section 10.7; ``epoch`` is e+1."""
    info = (R.LABELS["hkdf_epoch"].encode() + F.encode_varint(epoch) +
            hashlib.sha256(session_crypto(crypto_i, crypto_r)).digest() +
            hashlib.sha256(offer_body + answer_body).digest())
    return combine(hkdf_expand(epoch_secret, info, 32), shared_secrets)


def key_id(key_alg, key):
    """KeyId of secure-carrier/SPEC.md Section 6.6."""
    return hashlib.sha256(R.LABELS["key_id"].encode() + F.encode_varint(key_alg) +
                          bytes(key)).digest()
