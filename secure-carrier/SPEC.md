# zmux Secure Carrier

Status: draft.

The secure carrier is a transformation applied to one zmux carrier. It proves
with a handshake that the carrier belongs to a peer or to a session, and it
optionally protects the carrier's zmux records. Each carrier chooses its record
mode. The key words MUST, MUST NOT, SHOULD and MAY are used as in RFC 2119.

Normative references: the Noise Protocol Framework, revision 34 ("Noise");
the Noise extension "hfs" (hybrid forward secrecy, "HFS"); FIPS 203 (ML-KEM);
RFC 5869 (HKDF); RFC 9000 Section 16 (variable-length integers); RFC 8446
Section 7.5 (exporters). The vectors in `vectors/` are normative: an
implementation conforms only if it reproduces all of them. `MULTIPATH.md`
defines the session secret, pipe IDs and locators.

Informative references: [BL15] M. Bellare, A. Lysyanskaya, "Symmetric and Dual
PRFs from Standard Assumptions: A Generic Validation of an HMAC Assumption",
IACR ePrint 2015/1198; [BBGS23] M. Backendal, M. Bellare, F. Günther, M. Scarlata,
"When Messages are Keys: Is HMAC a dual-PRF?", CRYPTO 2023.

## 1. Conventions

- Fixed-width integers are big-endian.
- `varint` is the RFC 9000 variable-length integer. Where this document says
  *shortest*, an encoding longer than necessary is invalid.
- `‖` is concatenation. Labels are ASCII without a terminator.
- `HKDF-Expand(prk, info, n)` is RFC 5869 Expand with the hash stated at each
  use.
- In each of the spaces Profile, payload type, `CredType`, Suite, Group and `KeyAlg`, the
  values 2^30 to 2^31-1 are private: this specification never assigns them, and an
  implementation of this document does not interpret them. Such a value is treated as an
  unknown value of its space.

## 2. Protocol summary

Three independent questions are answered by three mechanisms:

| Question | Mechanism |
|---|---|
| Who is the peer and which trust domain is it in? | Establish handshake |
| Does this carrier belong to this session? | Join (Section 11) |
| Are this carrier's records protected here? | Record mode |

A failure never changes the protection in place: no end falls back to another
mode or profile on the same carrier. Choosing differently requires a new
carrier and is a decision of the host.

## 3. Profiles

The first byte of a carrier's handshake is a Profile. A Profile identifies one
complete combination. The client chooses and the value is bound into the prologue
or transcript. There is no negotiation.

| Value | Protocol | Use | Handle | PQ confidentiality |
|---|---|---|---|---|
| 1 | `Noise_XXhfs+psk0_25519+MLKEM768_ChaChaPoly_SHA256` | Establish, with a pre-shared key | yes | yes |
| 2 | Join (Section 11), not a Noise protocol | every further carrier of a session | | |
| 3 | `Noise_XXhfs+psk0_25519+MLKEM768_AESGCM_SHA256` | as 1 | yes | yes |
| 4 | `Noise_XXhfs_25519+MLKEM768_ChaChaPoly_SHA256` | Establish, without a pre-shared key | no | yes |
| 5 | `Noise_XXhfs_25519+MLKEM768_AESGCM_SHA256` | as 4 | no | yes |
| 6, 7 | unassigned | | | |
| 8 | escape: a varint extended Profile follows on the wire | | | |

Values 9 and above are sent through the escape. An unassigned or private value
(Section 1) is an unknown Profile.

"PQ confidentiality" says whether the confidentiality of the handshake and of the keys it
yields resists a passive adversary with a quantum computer, one that records a handshake
now and attacks it later. It is one of the two facts a handshake reports about its
strength (Section 6.8); neither describes records in Bound or Trusted modes.

Every implementation MUST support Profiles 1 to 5. The defaults are fixed so
that two implementations with default settings always complete a handshake:

- A server accepts by default Profiles 1 and 3, and Join (Profile 2) on the
  carriers of its sessions. Profiles 4 and 5 send node credentials to anonymous
  clients: a server accepts them only when its configuration lists them among
  its accepted Profiles.
- A client by default uses Profile 1 or 3 when it has a pre-shared key and
  Profile 4 or 5 when it has none; which of each pair it prefers is local, for
  example by the presence of AES hardware.

### 3.1 Parameters of the functions

| Function | Public key | Key | Tag | Nonce from n | Hash output | PSK | Rekey interval | Max bytes per key |
|---|---|---|---|---|---|---|---|---|
| 25519+MLKEM768, ChaChaPoly, SHA256 | 32 | 32 | 16 | 4 zero bytes ‖ n as 64-bit little-endian | 32 | 32 | 2^20 records | not limited |
| 25519+MLKEM768, AESGCM, SHA256 | 32 | 32 | 16 | 4 zero bytes ‖ n as 64-bit big-endian | 32 | 32 | 2^20 records | 2^56 |

`25519+MLKEM768` is X25519 for the DH tokens and ML-KEM-768 (FIPS 203) for the
KEM tokens of HFS:

| KEM | Encapsulation key | Ciphertext | Shared secret |
|---|---|---|---|
| ML-KEM-768 | 1184 | 1088 | 32 |

The contributory check of X25519 is Section 5 rule 1. ML-KEM has none: the end
that encapsulates alone determines the shared secret, and the X25519 `e` of the
other end still contributes to the keys. Its input check is Section 5 rule 8.

For AESGCM the limit is that of Noise Section 14. A key protects at most 2^20
records of at most 65535 bytes, under 2^36 bytes, which satisfies the limit.

### 3.2 Adding a profile

A new Profile MUST define: the Noise pattern and protocol name; the DH or KEM
function and its public-key and ciphertext lengths; the AEAD with key length,
tag length, nonce format and data-volume limit; the hash and its output length;
the PSK length; the rekey interval, which MUST keep every key within the limit;
the known payload types of each message (Section 6.3); whether it has a Handle;
its PQ confidentiality. Each DH or KEM MUST define its contributory check: which public values or
outputs fail, compared in constant time. The minimum message length of Section 5 rule 6
MUST count KEM ciphertexts. The cipher MUST define REKEY. It MUST
provide vectors for handshake, negative, record and derivation. The largest
record plaintext follows from the tag: `65535 - tag length`.

A new key exchange of Join is a Group (Section 11.6), not a Profile.

### 3.3 Dispatch

Values 1 to 8 cannot begin a zmux first record, so a host dispatches a new carrier
on its first bytes, as follows. `b` is what has arrived; on a message carrier, the
first message.

1. `b` is empty: on a byte stream `incomplete`, need 1; on a message carrier
   `other`.
2. `b[0]` is 1, 3, 4, 5 or 8: `establish`. The Hello parser takes over and may
   still find an unknown Profile behind the escape.
3. `b[0]` is 2: `join`.
4. `b[0]` is 6 or 7: `reserved`, treated as an unknown Profile.
5. Otherwise apply the first record test of `SPEC.md` Section 6.2. On a byte
   stream: a Size of at least 9 and at most the largest record size, as a varint of
   1, 2 or 4 bytes, then a byte `ff` or `ba`. On a message carrier: `ff` or `ba` at
   once. The result is `record`, `other`, or, when `b` ends before the
   test can finish, `incomplete` with the total length needed: the varint's length
   plus 1, at most 5. A first byte of `16` followed by `03` is `other`.

A host that reads `incomplete` reads more, within its own time limit. `dispatch.json`
gives cases for both framings.

## 4. Wire format

### 4.1 Messages

Byte stream:

    Hello  = WireProfile ‖ Flags(1) ‖ [Handle(16)] ‖ Len(2) ‖ NoiseMsg1
    Reply  = Len(2) ‖ NoiseMsg2
    Finish = Len(2) ‖ NoiseMsg3          (Profiles with a third message)
    Sealed record = Len(2) ‖ Ciphertext

    WireProfile = Profile(1)             for values 1 to 7
                = 0x08 ‖ varint(Profile) otherwise, shortest, value at least 9

Message carrier: the same contents without `Len`, one message per unit. A message
carrier MUST deliver units reliably and in order, because record nonces are
implicit.

`Len` is the length of the unit that follows and bounds every Noise message and
ciphertext at 65535 bytes. On a message carrier a longer unit is `too_large`. A
receiver MAY apply a lower limit to a Hello before buffering it, and MUST bound
memory and time before authentication.

The client sends nothing after a Hello until it has processed the Reply. A server
that holds other bytes after a complete Hello, or reads any before writing its
Reply, fails with `trailing_bytes`. `Finish` and a Reply MAY be followed by records.

The parser reads a Hello's fields in wire order and the first failure is the result:
Profile, Flags, Handle, then the unit. A header that ends early is `incomplete` on a
byte stream, with the smallest total length that can make progress, and `malformed`
on a message carrier. An empty `NoiseMsg1` parses, and then fails as a short message
(Section 5).

In Bound and Trusted modes, records after the handshake are raw zmux records.

### 4.2 Flags

| Bits | Meaning |
|---|---|
| 0-1 | Mode: 0 Sealed, 1 Bound, 2 Trusted, 3 reserved |
| 2-7 | MUST be zero |

A reserved Mode or a non-zero reserved bit fails the handshake.

### 4.3 Handle

`Handle` is present exactly when the Profile table says so, and is 16 opaque bytes:
configured with the pre-shared key, or derived with it (Section 8.4), never an enumerable
value.

A server finds the key from the Handle alone. It MUST NOT try message 1 or a Join Hello
against several pre-shared keys.

## 5. Prologue and handshake

    prologue = "zmux-seal" ‖ varint(Profile) ‖ Flags ‖ [Handle(16)] ‖ [Binding]

`varint(Profile)` is shortest and is the Profile value, not the WireProfile.
`Handle` is present exactly when it is in the Hello. `Binding`
(Section 8.3, 32 bytes) is present exactly when Mode is Bound. It is never sent.

Noise is applied as specified, with the prologue above. Each end initialises the
symmetric state with the protocol name of the Profile. The messages are:

    Establish (XXhfs+psk0; XXhfs omits psk)
    -> psk, e, e1
    <- e, ee, ekem1, s, es
    -> s, se

The protocol names are those of the Profile table. The pattern modifiers are
joined with `+` (`hfs+psk0`, Noise Section 8.1) and the DH and KEM function names
likewise (`25519+MLKEM768`, HFS). Every name is longer than the hash output, so
the initial `h` is `SHA256(protocol name)`. The tokens are processed as Noise and
HFS define them; `EncryptAndHash` with no key set is `MixHash` of the plaintext:

- `e1`, written by the initiator: it generates an ML-KEM-768 key pair `(ek, dk)`
  and writes `EncryptAndHash(ek)`. With a pre-shared key a key is already set,
  so `ek` is encrypted and carries a tag; in Profiles 4 and 5 it is sent in the
  clear.
- `ekem1`, written by the responder after `ee`: it checks `ek` (rule 8), computes
  `(ct, ss) = Encaps(ek)`, writes `EncryptAndHash(ct)` and then `MixKey(ss)`. The
  initiator reads `DecryptAndHash(ct)`, computes `ss = Decaps(dk, ct)` and calls
  `MixKey(ss)`.

The KEM shared secret enters the chaining key after the DH output of `ee` and
before those of `es` and `se`.
`Split` (Section 7.1), the exporter secret (Section 8.1) and every Join of the
session (Section 11) derive from the final state.

Every handshake payload follows Section 6. In Profiles 4 and 5 the payload of
message 1 is not encrypted.

Requirements:

1. A DH output of all zeros MUST fail the handshake with `kex`, compared in constant
   time. This covers low-order
   and all-zero public keys in every position. Without this rule a peer holding no
   private key could complete Establish without a pre-shared key and be delivered an
   identity. Several encodings can denote the same key, so a public key value
   MUST NOT be used to deduplicate principals.
   The failure occurs at the DH token that uses the key, not when the key is read: a
   responder that reads a low-order `e` in message 1 fails when it writes message 2,
   having sent nothing; an initiator fails while reading message 2 or, for its own
   `se`, a responder while reading message 3.
2. With a pre-shared key, a server MUST NOT perform any DH, check or encapsulate to
   `ek`, sign a proof, or answer before message 1 authenticates, that is before the tag
   of its payload verifies.
3. A client MUST verify the server's static key, credentials and proof from message 2
   before writing message 3 or any zmux bytes.
4. A server MUST verify message 3, its credentials and proof included, before delivering
   the client's identity or any bytes of the carrier.
5. An end that decrypts a peer static key equal to its own fails at once with
   `peer_is_self`, before the next token. This holds for every Profile and removes
   reflection when both ends share a root and a pre-shared key.
6. A message shorter than its pattern requires fails with `malformed` before any
   token of the message is processed, so no DH or AEAD result can take precedence.
   The minimum is the sum of the lengths of each public key, KEM encapsulation key
   and KEM ciphertext, a tag for each `s`, `e1` and `ekem1` written when a key is
   already set, and a tag for the payload when a key is set by then:

   | Message | Profiles 1, 3 | Profiles 4, 5 |
   |---|---|---|
   | 1 | 32 + (1184 + 16) + 16 = 1248 | 32 + 1184 = 1216 |
   | 2 | 32 + (1088 + 16) + (32 + 16) + 16 = 1200 | 1200 |
   | 3 | (32 + 16) + 16 = 64 | 64 |

7. These rules bind the sender of the first message. `Binding` present when Mode is
   not Bound, or absent or of another length when Mode is Bound, is `invalid_config`
   before any byte is sent. The same holds for a pre-shared key absent, of another
   length, or surplus; and a Handle absent, not 16 bytes, or surplus. `config.json` lists
   them. A responder that receives a first message in Bound and has no Binding, because
   its carrier does not meet the contract of Section 7.2 or is a split carrier in Join,
   MUST refuse it with `auth` without processing it. A first message from a sender that
   mixed no Binding would otherwise authenticate. In every other case a responder does
   not judge whether its Binding suits the received Mode: it mixes what it has, and any
   disagreement is `auth`.
8. The responder MUST check `ek` at the `ekem1` token, before it encapsulates and
   having sent nothing, with the encapsulation key check of FIPS 203 Section 7.2: the
   length is 1184, and each 12-bit coefficient of its first 1152 bytes, decoded as
   ByteDecode12, is below q = 3329 (re-encoding gives the same bytes). A failure is
   `kex`. This is the only check of `ek`, and an implementation performs it in this
   one place, whatever its ML-KEM library checks on its own. `ek` is public, so the
   check need not run in constant time. The initiator checks `ct` only by the length
   of rule 6. `Decaps` has implicit rejection: a ciphertext that does not decapsulate
   yields an unrelated `ss`, and the AEAD of the rest of message 2 then fails with
   `auth`. No end reports a decapsulation failure of its own.
9. Each handshake, each Join and each epoch exchange MUST generate a new ML-KEM key
   pair and new X25519 key pairs, also when a host retries with the same peer; a key pair, an encapsulation and a shared secret MUST NOT be
   reused or cached. Each `dk` decapsulates once. `dk`, the seed `(d, z)` it came
   from, the encapsulation randomness `m` and `ss` SHOULD be destroyed as soon as the
   token that uses them is processed; where the platform cannot erase them, an
   implementation drops them as early as it can.
10. The handshake random values are the ephemeral X25519 private key, the ML-KEM
    key generation seed `(d, z)` and encapsulation randomness `m` of Establish, R of
    Join (Section 11.4), and the private values of every Group share (Section 11.6). A process restored from a snapshot, or a clone of one,
    MUST reseed its random source before it generates any of them; this requirement
    has no fallback. Otherwise two copies can generate the same values: a recorded
    handshake or Join then replays with the same keys, and the same key and nonce
    encrypt different plaintexts. An implementation SHOULD draw each of these values
    directly from the operating system's entropy source. A failure of the random
    source fails the handshake or the Join.

## 6. Handshake payloads

### 6.1 Items

A payload is a possibly empty sequence of items:

    Item = Type(varint) ‖ Length(varint) ‖ Value

Rules, all failing the handshake when violated. The parser goes through the items
in byte order and the first violation it meets is the result. Within one item the
checks run in the order: `Type` varint (rule 3 before rule 1); `Length` varint (rule
3 before rule 1); `Value` within bounds (rule 3); order against the previous type
(rule 2); if the type is known, its value (Section 6.4, 11.3); if unknown, rule 4.

1. `Type` and `Length` are shortest varints.
2. Types strictly increase through the payload. No type repeats. There are no
   exceptions: padding is one item, and a credential is one item.
3. Bytes after the last complete item are invalid: no truncated item, no trailing
   bytes.
4. A type is *known* in a message when Section 6.3 allows it there. An unknown type
   with low bit 1 fails the handshake. An unknown type with low bit 0 is skipped.
   A type placed in a message that does not know it is simply unknown there, whatever
   another message makes of it: its value is not examined, and its low bit decides. The
   one exception is application data in message 1 of Profiles 4 and 5 (Section 6.9).

### 6.2 Types

| Type | Name | Value |
|---|---|---|
| 0 | ignorable padding | any bytes, not interpreted. Senders use zeros, and random bytes in a message sent without a key. Known everywhere |
| 1 | credential | Section 6.4 |
| 2 | application data | Section 6.9 |
| 3 | carrier first bytes | the start of the sender's zmux byte stream |
| 8 | proof | Section 6.7 |
| 5 | join pipe | Section 11.3 |
| 6 | frames | Section 11.3 |
| 7 | join decision | Section 11.3 |
| 9 | key share | Section 11.3 |

The handshake layer only carries the values of types 2, 3 and 6.

Carrier first bytes are handed to zmux in order,
before any later bytes of the carrier, and only after the check of Section 5
that applies to the receiver. For a client this means trust in the server is
established first.

A sender MAY lengthen a Hello, a Reply or a Finish with one padding item, and a
Confirm with zero bytes (Section 11.2). Padding in a message sent without a key is
random.

### 6.3 Known types by message

Padding is known everywhere and omitted from the table.

| Message | XXhfs+psk0 (Profiles 1, 3) | XXhfs (Profiles 4, 5) |
|---|---|---|
| 1 | 2 | none |
| 2 | 1, 2, 3, 8 | 1, 2, 8 |
| 3 | 1, 2, 3, 8 | 1, 2, 3, 8 |
| Join Hello | 5, 6, 9 | |
| Join Reply | 6, 7, 9 | |

Message 2 of XXhfs does not authenticate the client, so carrier first bytes are not
known there. A sender MUST NOT place a type in a message that does not know it, odd or
even: a local attempt is `invalid_config`, as is omitting a required item. The skipping
of Section 6.1 rule 4 is a receiver's rule only, and `receiver.json` tests it.

### 6.4 Credential

    Value = CredType(varint) ‖ Credential          Credential: the rest of the value

A message carries at most one credential, in one item of type 1. `CredType` is a shortest
varint and names the format of `Credential`, which is opaque to this document: the handshake
core checks only the bounds of the item and hands the bytes to the host's verifier. An
empty value is `tlv_credentials`. The payload is encrypted under keys that include `ss` and
`es`, so a credential needs no binding of its own to the handshake.

| CredType | Name |
|---|---|
| 0 | no credential |

`CredType` 0 is reserved and means "no credential": a sender does not send it, and a
receiver that reads it, like an empty value, fails the handshake with `tlv_credentials`.
Any other value is unassigned or private (Section 1); a format takes a value of its own.

**Verification.** For each Establish the verifier of the end that authenticates the peer is
asked once, with the role it expects of the peer (node for a client, client for a server),
the Profile, the peer's static key, the Handle in Profiles 1 and 3, the credential when the
message carries one (its `CredType` and bytes), and a local context of the end. It returns
a verdict or fails; failure is `credentials_failed`, also when the credential is absent and
the verifier requires one. A verdict names whom the credential identifies and, optionally,
the KeyId of a proof key (Section 6.7). The core never produces `credentials_failed` from the
credential itself.

A pinned node key is a verifier of its own: it names the KeyId of the node's static key
(KeyAlg 1, Section 6.6), optionally with the KeyId of its proof key, and reads no credential.

### 6.5 Key algorithms

A long-term public key is named by `KeyAlg(varint) ‖ key`, where `key` is the canonical
encoding of the table:

| KeyAlg | Name | Key | Signature | PQ | Use |
|---|---|---|---|---|---|
| 0 | reserved | | | | |
| 1 | X25519 | 32 | none | no | static key of Establish |
| 3 | ML-DSA-44 | 1312, pkEncode of FIPS 204 | 2420 | yes | proof key (Section 6.7) |

Every implementation MUST support KeyAlgs 1 and 3. A key of another length than the table's
is invalid, and a KeyAlg outside its use fails the proof. The PQ column states whether the
algorithm resists a quantum adversary. ML-DSA is pure ML-DSA through the external interface
of FIPS 204, with an empty context string; the role of a signature is written in the signed
message. Any byte string of the right length is a valid ML-DSA public key, so a key is
checked by its length only. A proof key MUST NOT be used in another protocol or for another
purpose of this one.

A new KeyAlg MUST define its encoding and lengths, its public key check, its use, its PQ
column and vectors.

### 6.6 KeyId

    KeyId(KeyAlg, key) = SHA-256("zmux-seal key" ‖ varint(KeyAlg) ‖ key)          32 bytes

`key` is the canonical encoding of Section 6.5. A KeyId is the only name of a key in this
document: a pin and a verdict's proof key use it.

### 6.7 Proof

Type 8 carries a signature over the handshake transcript, in messages 2 and 3:

    Proof = KeyAlg(varint) ‖ PubLen(varint) ‖ Pub ‖ Sig          Sig: the rest of the value
    M     = label ‖ 0x00 ‖ h_p
    label = "zmux-seal proof r" in message 2, "zmux-seal proof i" in message 3

`h_p` is the handshake hash `h` after the last token of the message and before its payload
is encrypted: in message 2 after `es`, in message 3 after `se`. `Sig` is the signature of
`Pub`'s KeyAlg over `M`; with KeyAlg 3 that is `ML-DSA-44.Sign(sk, M)` with an empty context.
A message holds at most one proof.

The core checks the structure: shortest varints (`tlv_non_minimal`) and `Pub` within the
value (`tlv_truncated`). When the verdict names a proof key, the core checks that the message
has a proof, that `KeyId(KeyAlg, Pub)` equals the proof key, that the KeyAlg is a proof key's
(Section 6.5), and that `Sig` verifies over `M`; any failure is `credentials_failed`. When the
verdict names no proof key, a proof is not verified. Production signers MUST use hedged
signatures. A server with a pre-shared key signs only after message 1 authenticates
(Section 5 rule 2).

The payload is encrypted under keys that include `ss` and `es`, so the position of the proof
in the payload does not matter, and a proof can be neither removed nor replaced.

### 6.8 Post-quantum facts

A handshake reports two facts about the peer:

- **PQ confidentiality**: the column of Section 3 for its Profile.
- **PQ authentication**: the verdict named a proof key, the proof verified, and the proof
  key's KeyAlg has PQ yes.

A pre-shared key does not give PQ authentication. An end that requires PQ authentication of
its peer fails the handshake with `credentials_failed` when the fact does not hold. Such an
end MUST NOT accept a pin of an X25519 key alone, which is `invalid_config`: it gives an
active quantum adversary a path that the requirement does not cover. PQ authentication is
no stronger than what the verifier relies on to name the proof key: a verifier whose own
trust rests on classical signatures lets an active quantum adversary name a proof key of its
choice.

### 6.9 Application data

Type 2 carries opaque bytes of the layer above, at most one item per message (Section 6.1
rule 2). This document does not interpret them; an application that needs structure
chooses its own encoding. The type is even, so a receiver that does not know it skips it. A
sender that has no application data for a message sends no item, and the message is then the
same bytes as without the type; it never sends an empty item. A receiver treats an empty
item as no data. A later feature of this protocol takes a payload type of its
own, never a structure inside this one.

| Message | Sender | Protection of the bytes |
|---|---|---|
| 1, Profiles 1 and 3 | client | encrypted under the pre-shared key only: no forward secrecy, replayable, readable by anyone who later learns the pre-shared key; it authenticates no more than knowledge of the pre-shared key |
| 2 | server | forward secret; authenticated as the server once the client's verification of message 2 passes |
| 3 | client | forward secret; authenticated as the client once the server's verification of message 3 passes |

Message 1 of Profiles 4 and 5 is not encrypted: a client MUST NOT send type 2 there, and a
server that reads it there fails the handshake with `malformed`.

Message 1 data has the properties of TLS 0-RTT early data (RFC 8446 Section 8): a receiver
MUST NOT let it trigger an action that is unsafe to repeat. Message 3 data is not to be acted
on with side effects before the server has verified message 3 (Section 5 rule 4). No
application data of the handshake gives 0-RTT semantics to the zmux session; zmux bytes still
follow the rules of Section 5.

A length is bounded only by the encoding and the size of its message (Section 3.3). A
receiver MAY set a lower local bound; data over it fails the handshake with `too_large`.
Data that does not fit the message being written is `too_large` too.

## 7. Records

### 7.1 Sealed

After the last message of Establish, `(c1, c2) = Split()`. The client sends with `c1`
and the server with `c2`. Join takes its keys from Section 11.4. One zmux record is one AEAD message with empty associated data and
the implicit counter n, starting at 0.

The nonce and rekey interval of a cipher are those of Section 3.1. The largest
plaintext is `65535 - tag length`, which is 65519 for every current cipher. An implementation MAY send smaller records. A larger plaintext is refused
without consuming a nonce.

Before processing the record whose n is a positive multiple of the rekey interval,
each direction replaces its key with Noise `REKEY`. n is not reset. The nonce
2^64-1 is never used for a record: reaching it fails the direction.

Any authentication failure or short write fails the carrier permanently. A
ciphertext shorter than the tag is an authentication failure, and an end of the
carrier in the middle of a record is the same carrier failure.

A sealed carrier is closed as SPEC.md Section 6.5 says: its write side is shut down
after the last record, and it is read and discarded until the peer's end or the linger
timeout before it is closed, so that the peer reads the records sent before the end. An end of the carrier at a record boundary, such as a TCP FIN, ends the carrier and
says nothing else. It does not mean that data or a stream ended normally, and it closes
no stream. It is not authenticated: anyone on the path can inject it, so it can cost a
carrier and nothing more. A session judges only by authenticated zmux frames. Normal
closure is expressed by them, and an end that they do not make expected is a pipe
failure (SPEC.md Section 6.4, MULTIPATH.md Section 7.3): the session recovers the pipe's
information as after any failure. A graceful removal ends its pipe this way too, and its
PIPE_ABANDON may come later over another pipe. Until that frame has been received or a
graceful removal has been started locally, the end is treated as a failure, which costs
at most a repeated send.

### 7.2 Bound and Trusted

The record keys are discarded and records are raw zmux records.

`Bound` proves that both ends share the carrier's exporter, and fails if a third
party terminates or splices the carrier. It adds no defence if a carrier endpoint's
own keys leak. The carrier MUST: be TLS 1.3 or later, or TLS 1.2 with the extended
master secret and no renegotiation; exclude PSK without DH; guarantee confidentiality
and integrity of records, as declared by the configuration of both ends rather than
inferred from the presence of an exporter;
be end to end, so a QUIC stream qualifies and a stream multiplexed inside the
connection by H2 or a similar layer does not. A Hello in Bound MUST NOT be sent as
carrier early data.

The Binding identifies the secure connection, not a stream: QUIC streams of one connection
share it. Isolation between them rests on the carrier's stream integrity, correct
dispatch by the endpoint and the one-time use of the Pipe ID.

`Trusted` is an explicit configuration of both ends and is never selected
automatically. Anyone on the path can read all records and relay online; only
membership at handshake time is proven.

A session's protection equals that of its weakest carrier, because scheduling and
retransmission can place any stream on any carrier. A server accepts only Sealed
unless configured otherwise.

## 8. Derivations

HKDF uses SHA-256 in 8.3 and 8.4, and the Profile's or Suite's hash in 8.1 and 8.2.
These outputs are protocol extensions outside Noise. An empty HKDF salt equals `HashLen`
zero bytes. A pseudorandom key given to
HKDF-Expand is required only to be at least the hash output long, and a shorter one is
`invalid_config`. Longer ones are valid; `derive.json` and `exporter.json` have both.

### 8.1 Exporter secret

    exporter_secret = HKDF-Expand(ck, "zmux-seal exporter" ‖ h, hash length)

`ck` and `h` are the final values after the last message. They are computed in
every mode. This requires a Noise implementation that exposes `ck`.

### 8.2 Export

    Export(label, context, n) =
        HKDF-Expand(exporter_secret, LabelLen(1) ‖ label ‖ ContextLen(2) ‖ context ‖ n(2), n)

`label` is at most 255 bytes and `context` at most 65535. `n` is at least 1 and at most
255 hash outputs, which is 8160 for SHA-256. Anything outside these is `invalid_config`.
An absent context and an empty one are the same. The handshake hash, `th` values and ciphertexts MUST NOT be used as globally unique
identifiers or channel bindings: a replayed Hello repeats them. Channel binding uses
`Export` with `n` of at least 32 bytes and a label of its own; shorter outputs can collide and
identify nothing. No exporter of the carrier is passed through: a secure
carrier's exporter is always this one. A session using the secure carrier takes its
keys from this exporter only, and MUST fail if the peer does not select it.

### 8.3 Bound exporter

`E(half)` is the carrier exporter, no context, 32 bytes, of label
`EXPORTER-zmux-seal-bound` for Establish and `EXPORTER-zmux-join` for Join. On TLS 1.2
it is read with the no-context form of RFC 5705, which differs from an empty context.

    Binding = E(up)                                 if E(up) = E(down)
            = HKDF-Expand(HKDF-Extract("", E(up) ‖ E(down)), "zmux-seal bound", 32)   otherwise

The order is by role, uplink then downlink, regardless of which end reads. If
either half has no exporter, the configuration is invalid and no byte is sent. The
two-connection formula is kept for later use by Join: Bound Join on a split carrier is
not enabled at present (Section 11.4).

### 8.4 Handle and pre-shared key from a secret

A host MAY derive both from one secret of at least 16 bytes (32 by default), with
HKDF over SHA-256:

    PRK    = HKDF-Extract("zmux-seal token" ‖ deployment_salt, secret)
    Handle = HKDF-Expand(PRK, "handle" ‖ DomainLen(2) ‖ domain, 16)
    PSK    = HKDF-Expand(PRK, "psk" ‖ DomainLen(2) ‖ domain, 32)

The result is the client's Handle and pre-shared key. The first argument of
`HKDF-Extract` is the salt. `deployment_salt` is optional and empty by default. A shorter secret is an invalid configuration. The secret MUST be
uniformly random, and passwords MUST NOT be used.

A derived Handle lets anyone who observes it test guesses of the secret offline, against
many Handles at once. A 16-byte secret is the minimum and the default is 32 bytes. A
`deployment_salt` keeps such a batch of Handles to one deployment.

## 9. Errors

Nothing is ever sent to report an error. The error classes below are those of the
vectors. Each fails the handshake or the direction permanently, except `incomplete`,
and a refused oversized plaintext.

| Class | Condition |
|---|---|
| `incomplete` | more stream bytes needed |
| `malformed` | application data in message 1 of Profiles 4 and 5, handshake message shorter than its pattern, Join ciphertext shorter than a tag, Trusted Confirm shorter than its tag, header cut on a message carrier, invalid escape or Suite encoding, wrong Reply type byte |
| `trailing_bytes` | bytes after a complete Hello |
| `unknown_profile` | Profile not supported or unassigned, first byte 0 or above 8, escape below 9 |
| `unknown_suite` | Join Suite not in this implementation or not usable in the session |
| `bad_flags` | reserved Mode or reserved bit set |
| `bad_handle` | Locator length outside its range |
| `auth` | AEAD or Confirm failure, including a differing prologue, key or Binding, a KEM ciphertext that does not decapsulate (Section 5 rule 8), and a sealed ciphertext shorter than a tag |
| `kex` | key exchange input refused: all-zero DH output (Section 5 rule 1), invalid KEM encapsulation key (Section 5 rule 8), the same in a Join key share (Section 11.6) |
| `peer_is_self` | peer static key equals the local one |
| `too_large` | message, Hello, Reply or plaintext over its limit, reading or writing; application data over the receiver's local bound or too long for its message (Section 6.9) |
| `exhausted` | nonce space used up |
| `random_failed` | the random source failed while making a key or an R; there is no fallback |
| `tlv_truncated` | item or entry runs past the end, trailing bytes, a join pipe value with bytes left or too few |
| `tlv_non_minimal` | non-shortest varint |
| `tlv_order` | types not strictly increasing |
| `tlv_unknown_critical` | unknown type with low bit 1 |
| `tlv_credentials` | empty credential value, or `CredType` 0 |
| `missing_field` | required Join item absent |
| `bad_field` | Join item value out of range |
| `bad_confirm` | a byte other than zero in a Sealed confirmation plaintext or after a Trusted Confirm's tag |
| `credentials_failed` | the verifier failed the peer, a proof the verdict requires failed, or PQ authentication is required and does not hold (Sections 6.4, 6.7, 6.8) |
| `invalid_config` | local parameters invalid, nothing sent: Section 5 rule 7, Section 8, a half without an exporter, a secret too short, a type a message does not know, a required item omitted |

A differing Binding, a wrong pre-shared key and a changed Flags field are the same
`auth` failure. In Profiles 4 and 5 message 1 is not authenticated: the mismatch
appears when the client reads message 2, and a third party can obtain only the node
credentials.

**Reject policy.** Every failure before the peer is authenticated MUST be handed to
the server's reject policy, which is the only response to it. For Establish these are
all failures before message 3 passes, among them an unknown Handle, a wrong pre-shared
key and a Profile or Mode the server does not accept. For a Join they are all failures
before the Hello's payload passes (Section 11.5 steps 1 and 2): a Hello that does not
parse, an unknown Locator, a Suite not usable in the session, Bound without a Binding
(Section 5 rule 7), a failed AEAD, and a carrier the acceptor declines for a local
resource limit. The policy is one of two, chosen by the server for Establish and for
Join separately: **close**, the carrier is closed; or **stall**, nothing more is read
or sent and the carrier is closed when a time limit expires. The default is stall for
Establish and close for a Join. The time limit always exists, also when the handshake
itself has none; for a Join it is one join timeout of the listener from the refusal.
Whether a server can stall one more carrier depends only on its own resource bounds for
the carrier's source, never on the failure; a carrier it cannot stall is closed at
once.

What this guarantees is that every such failure gets the same response: no byte, and
the same way of ending, a close or a stall of the same limit. The moment of the close
is the time taken to receive and process the input plus that fixed wait; differences
in processing time between failures are outside what this specification guarantees.

An implementation MAY report a failure locally with a finer reason than these classes,
for example which server setting refused a Profile; the classes, and what is visible on
the wire, stay as above.

## 10. Vectors

All values are hex strings unless noted. `mode` is the Flags value. Ephemeral and
static keys are X25519 private scalars given as seeds. An ML-KEM-768 key pair is given
as its 64-byte seed `d ‖ z` (FIPS 203 ML-KEM.KeyGen_internal), an encapsulation as its
32-byte randomness `m` (ML-KEM.Encaps_internal). ML-DSA signatures of the vectors are
deterministic, with `rnd` of 32 zero bytes (FIPS 204 ML-DSA.Sign_internal), and ML-DSA
keys are given as their 32-byte seeds. Implementations need an internal
entrance to fix ephemeral keys and R; production interfaces MUST NOT offer one.

| File | Content |
|---|---|
| `handshake.json` | each Establish Profile and mode, and payload variants, including complete handshakes with an opaque credential and a proof whose key the verdict names, with a pin, and with PQ authentication required: prologue; the initiator's ML-KEM seed and the responder's encapsulation randomness; for each message the payload, `noise`, `wire_stream`, `wire_message` and `h` and `ck` afterwards; final hash, `ck`, split, exporter secret and an export. `split_c1`, `split_c2` are used for records only when `records_use_split` is true |
| `negative.json` | one end with fixed inputs driven through `steps` on `framing`; the last step fails with class `error`, and earlier ones succeed. Ops: `hello` parses a Hello, refuses bytes after it, and reads message 1, taking the Profile, mode and Handle from it; `read` cuts a frame and reads it; `write` writes with an empty payload, or with a padding item of `padding` bytes; `force_n` sets n of the current handshake cipher to 2^64-1 through the internal entrance |
| `receiver.json` | `establish` and `join` lists in the form of `negative.json` and `join_negative.json`, in which every step succeeds: received types a message does not know, with low bit 0, are skipped |
| `record.json` | per cipher: `key` in effect before the record, `n`, plaintext (or `plaintext_fill`), ciphertext (or its SHA-256), `key_after`, `n_after`, or `error`. A vector with `ciphertext` and `error` is an open; one with only plaintext and `error` is a seal |
| `exporter.json` | `Export` from a given `exporter_secret`. `output_sha256` stands for a long output; `label_fill` stands for a label of that many `l` bytes and `context_fill` for that many zero bytes. With `error`, the call fails |
| `derive.json` | Sections 6.6, 8.1, 8.3, 8.4 and 11.4. A `join_psk` entry derives from its `epoch_secret`, `suite`, `hash_len` and the sets `crypto_i`, `crypto_r` of `session_crypto`; a `key_id` entry gives `key_alg`, `key` and `key_id` |
| `dispatch.json` | first bytes of a carrier on each `framing`: `class` and `need` as in Section 3.3. `hello` and `record` say what they can begin; both false means neither |
| `hello.json` | Hello, Reply and frame boundaries. The input is `header` followed by `fill` bytes of `5a`; `kind` (`establish`, `join`, `reply` on Suite 1, `frame`) says which parser; the result is `consumed`, or `error` and, for `incomplete`, `need` |
| `config.json` | local parameters that are `invalid_config` |
| `join.json` | each Suite, Group, Mode, session role of the joiner, with and without a batch, and variants: inputs (`attempt`, 7, and `random_i` make `r_i`; `attempt_r`, 8, and `random_r` make `r_r`; `crypto_i`, `crypto_r`; `group`, the X25519 private keys `x_i` and `x_r`, for Group 2 the joiner's ML-KEM seed `kem_seed` and the acceptor's encapsulation randomness `encaps_m`), `r_i`, `r_r`, `th`, `th2`, `th3`, `k0`, `k1`, the shared secrets `ss` in order, `hs`, `key_i2r`, `key_r2i`, `mac_key`, `exporter_secret`, `confirm_tag`, each wire on both framings, an export. `confirm_tag` is computed in every Mode and sent only in Trusted. `confirm_padding`, when given, is the number of zero bytes the Confirm holds |
| `join_negative.json` | as `negative.json`, with `side` joiner or acceptor, and the key exchange inputs of `join.json`. Ops: `write_hello` with `hello_payload`, preceded by a padding item of `padding` bytes when given, `hello` parses, refuses bytes after it, and opens, `write_reply` with `reply_payload`, padded alike, and `attempt_r`, `random_r`, `reply` reads a Reply, `confirm` reads a confirmation, and `need`, when given, is the total length an `incomplete` asks for. An acceptor uses `join_psk` and `binding` as given, so a disagreeing `binding` is `auth` |

## 11. Join

Join proves that a carrier belongs to an existing session. It uses a key derived from
the secret of the session's current epoch and a key exchange of a Group. It is a protocol of zmux itself, used by every session
for every carrier after the first, in all Modes, and is independent of Noise. A session
whose first pipe does not use this document has its session secret from that pipe's
exporter, and its host MUST have proven that pipe end to end before joining.

### 11.1 Suites

| Suite | AEAD | Hash | Key | Tag | Nonce from n | N |
|---|---|---|---|---|---|---|
| 1 | AES-256-GCM | SHA-256 | 32 | 16 | as AESGCM, Section 3.1 | 32 |
| 2 | ChaCha20-Poly1305 | SHA-256 | 32 | 16 | as ChaChaPoly, Section 3.1 | 32 |

Suites 1 and 2 MUST be implemented. `N` is the length of R, at least 16 and at most the
hash output. Each end's sets of Suites and Groups are exchanged during establishment in
`zmux_session_crypto`, which also defines `session_crypto` (`MULTIPATH.md` Section 3.7).
The usable Suites are the intersection of the two sets of Suites. The session layer
refuses a Hello with any other Suite as `unknown_suite` before calling the protocol
layer's AEAD. A local set that breaks the rules of that parameter is `invalid_config`.

### 11.2 Messages

    Hello = 0x02 ‖ Suite(varint) ‖ ModeByte(1) ‖ LocatorLen(1) ‖ Locator ‖ R_i(N) ‖ Len(2) ‖ AEAD(k0, payload)
    Reply = 0x03 ‖ R_r(N) ‖ Len(2) ‖ AEAD(k1, payload)
    Confirm:
      Sealed  = one Sealed record (Section 7.1), n = 0, whose plaintext is zero or more zero bytes
      Trusted = Len(2) ‖ HMAC(mac_key, "confirm" ‖ th3) ‖ zero or more zero bytes
      Bound   = nothing

On a message carrier the contents are the same without `Len`, one message per unit. The
zero bytes of a Confirm enter no transcript.

`Suite` is a shortest varint, not zero. `ModeByte` holds the Mode of Section 4.2 in bits
0-1 and the key phase, e mod 2 for the epoch e of the joiner's key (Section 11.4), in bit
7; bits 2-6 are zero. A reserved Mode or a non-zero bit 2-6 is `bad_flags`. `ModeByte`
enters `th`, so the phase is authenticated. `Locator` is 1 to 255 bytes, opaque, a Session
Locator that the acceptor announced (`MULTIPATH.md` Sections 3.1 and 10.7), and is what the
acceptor's session table finds. A length the acceptor never issues is an unknown Locator, not `bad_handle`. The AEADs use key `k0` or `k1` once, nonce n = 0, empty
associated data. `Len` is not part of any transcript, so the two framings derive the
same keys. The parser reads the fields in wire order and the first failure is the
result. A cut is treated as in Section 4.1, and a ciphertext shorter than a tag is
`malformed`.

Reading a Reply: the joiner classifies the next bytes. Empty on a byte stream is
`incomplete`. A first byte `03` is the Reply: no first record begins with it. Anything
else can only be the transport parameters record that a shared listener writes first:
the session layer consumes at most one such record before calling the protocol layer,
which then sees the Reply first and treats any other first byte as `malformed`. Bytes the
joiner reads before the Reply is proven are not delivered to the session.

### 11.3 Payloads

Hello and Reply carry the payload of Section 6, with the known types of Section 6.3.

    join pipe (5) = PipeID(varint) ‖ Direction(1) ‖ FateGroup(varint) ‖ Priority(varint) ‖ Standby(1)
    frames (6)    = encoded zmux frames, carried opaquely by the handshake
    join decision (7) = 0 accept | 1 refuse
    key share (9)     = Group(varint) ‖ Share

- *join pipe* is required in Hello. `Direction` is 0 up, 1 down, 2 bidi. `Standby` is 0
  or 1. Other values are `bad_field`. Varints are shortest. A missing item is
  `missing_field`, checked after the whole payload has parsed.
- *join decision* is required in Reply, one byte. It is the acceptor's read-only
  prediction of the cap check. A joiner that reads 1 does not become Active. It is not
  a handshake failure, and the Reply's other checks have already passed.
- *key share* is required in Hello and in Reply. The joiner chooses any usable Group
  (`MULTIPATH.md` Section 3.7) and sends its initiator share; the acceptor answers with the
  responder share of the same Group. Its value is checked when the payload is parsed, with
  the payload: a missing item is `missing_field`; a non-shortest `Group` is
  `tlv_non_minimal`; a Group that is not usable in the session, a Reply's Group other than
  the Hello's, and a `Share` whose length is not that of the Group and the sender's role
  are `bad_field`. The checks of the share's contents and the key exchange itself come
  later (Section 11.5).
- *frames* in Hello is the first batch and in Reply the return frames. It has low bit
  0, so an acceptor that drops it loses nothing: the sender keeps what it sent. Rules for
  the first batch, at the session layer: STREAM frames without FIN only; a new stream's
  ID is allocated first and not cancelled afterwards; new bytes count against flow
  control; a frame in error is dropped and counted, without closing the session; no
  position counts and no ACK results; the sender retains the data and may send it again; a
  final size covers the batch's last offset; replay is deduplicated by stream ID and
  offset. The size of a batch is a local setting. An acceptor
  MAY refuse a longer Hello; a limit it sets leaves room for the key share of every usable
  Group beside the first batch it accepts.

### 11.4 Keys

`H` and HKDF use the Suite's hash, `K` is the AEAD key length and `HashLen` the hash
output. `‖` joins without length prefixes: each input is self-delimiting given the Suite.

    join_psk[e]_x = HKDF-Expand(epoch_secret[e],
                        "zmux join " ‖ x ‖ SHA-256(session_crypto) ‖ varint(Suite), HashLen)
                    SHA-256; x is "i" for the session initiator, "r" for the responder

    psk = join_psk[e]_x of the joiner's role in the session, on both ends;
          e is the epoch the joiner uses (MULTIPATH.md Section 10.7)

    th  = H(Hello bytes from 0x02 through R_i ‖ [Binding])
    th2 = H(th ‖ Hello ciphertext ‖ R_r)
    th3 = H(th2 ‖ Reply ciphertext)

    k0  = HKDF-Expand(psk, "hello" ‖ th, K)
    k1  = HKDF-Expand(psk, "reply" ‖ th2, K)

    hs  = Combine(psk, ss_1, ..., ss_n)                 Section 11.6, the Group's shared secrets
    key_i2r = HKDF-Expand(hs, "i2r" ‖ th3, K)
    key_r2i = HKDF-Expand(hs, "r2i" ‖ th3, K)
    mac_key = HKDF-Expand(hs, "mac" ‖ th3, HashLen)
    exporter_secret = HKDF-Expand(hs, "exporter" ‖ th3, HashLen)

- `join_psk` is the one derivation of this section that uses SHA-256 whatever the Suite;
  its output has the Suite's `HashLen`. `epoch_secret[0]` is the session secret
  (`MULTIPATH.md` Section 10.3) and the later epochs are those of `MULTIPATH.md` Section
  10.7.
- The acceptor takes e from the epochs it accepts by the key phase; at most one matches.
  When none does, it opens the Hello with a key of its own that no joiner has, a fresh
  random value of the process, so that the Hello fails as `auth` like any wrong key.
- Each end derives both `join_psk` values of each usable Suite when an epoch secret
  becomes known and keeps them while it keeps that epoch, so Joins work after pipe 0 has
  gone.
- `join_psk` MUST NOT be derived from a session secret made with empty input keying
  material. There is no mode without a real session secret.
- Every Hello and every Reply MUST take an R that the end has never used before, also
  across copies of a cloned or restored process (Section 5 rule 10). A receiver does
  not check how R was made. If a Reply's R repeats and its payload is constant, a
  replayed Hello gets the same `R_r` and `th3`; an old Confirm then replays, and
  records under `r2i` reuse a key and nonce. An end SHOULD make R as

      R = first N bytes of HMAC(psk, "zmux join nonce" ‖ Attempt(8) ‖ RNG(N) ‖ H(head ‖ payload))

  where `head` is the bytes of the message before R, `payload` its plaintext, `RNG(N)`
  N bytes from the random source and `Attempt` a counter that increases with each R of
  a process. This limits the harm of repeated random output only when the messages
  differ. An end MAY also mix a wall-clock time in nanoseconds into the HMAC input.
- `Binding` (Section 8.3) is present exactly when Mode is Bound, and a violation is
  `invalid_config` before any byte is sent at the joiner. An acceptor that receives a
  Hello in Bound and has no Binding MUST refuse it with `auth` without processing it, and
  counts it as a failed join authentication with its event. Otherwise it mixes what it
  has, and any disagreement is `auth`. Bound Join on a split carrier is not enabled at
  present, so such a carrier has no Binding; the formula of Section 8.3 is kept for it.
- `i2r` is the joiner's sending direction. In Sealed these are the record keys, with
  the rekey of Section 7.1, in the joiner's direction starting with Confirm at n = 0. In
  other Modes they are discarded.
- `Export` of a Join is Section 8.2 with this `exporter_secret`.

### 11.5 State machines

    Joiner:   Pending → HelloSent → Proven → Active
    Acceptor: Unconfirmed → Confirmed

The joiner is Proven when the Reply's AEAD and payload pass and its side of the key
exchange succeeds; it performs no key exchange before the Reply's AEAD passes, and a share
refused there is `kex`. It writes Confirm, after any local delay, and then its records. An acceptor in order:

1. Dispatches on `02`, parses, finds the session by Locator, and refuses a Suite not
   usable in that session.
2. Opens the Hello and parses its payload, with the checks of Section 11.3. A failure in
   step 1 or 2 goes to the reject policy (Section 9).
3. Applies Mode and authorization, sets the Pipe ID's one-time bit, performs its side of
   the key exchange (Section 11.6), and applies a first batch. A key share refused there is
   `kex`: the carrier is closed and the Pipe ID's bit stays set.
4. Writes the Reply with a decision.
5. Becomes Confirmed: in Sealed when the first record authenticates and its plaintext holds
   only zero bytes, any other byte being `bad_confirm`, and its frames are not parsed; in
   Trusted when the tag is equal, compared in constant time, and the bytes after it are zero,
   any other byte being `bad_confirm`. The tag is judged first. `Len`, or a message, shorter
   than `HashLen` is `malformed`. On a byte stream a shorter read is `incomplete`, with need
   2 and then 2 + `Len`. In Bound when the Hello has passed step 2.

Confirmed is the condition for the cap check, eviction, opening the pipe and leaving a
suspended state, so a replayed Hello cannot affect paths. It is also the condition for
the acceptor's records. Before it, return frames are limited to the Reply payload, and
in Sealed and Bound the whole Reply, its padding included, is at most 3 times the bytes
received, frames taking precedence over padding; the session layer enforces this limit, since
the protocol layer does not count them. A new authenticated Hello of the
session MAY evict the oldest Unconfirmed one and MUST NOT be refused for lack of room.
If the pipe still loses at Confirmed, the acceptor closes the carrier and the joiner
handles it as a pipe failure.

A failure at any step is permanent for that carrier and sends nothing. `incomplete` is
not a failure. Both ends bound a Join by the join deadline of `MULTIPATH.md` Section 5.7,
default 10 s: the joiner from before the Hello to Proven, the acceptor from the Hello to
Confirmed. Expiry is a failure of the same kind.

Replay: the Hello can be replayed at ciphertext level. R_r makes the record keys and
the Confirm fresh, so whoever replays it can neither read the Reply nor make a Confirm.
The zero bytes of a Confirm add nothing to this: in Sealed they are authenticated, and in
Trusted a party on the path can change their number, which moves only the start of records
that Trusted does not protect. One-time use comes from the Pipe ID. A replay that arrives first makes the original
Join fail as though lost, and a retry uses a new Pipe ID. In Bound the carrier's own
integrity covers duplication on one carrier and the Binding covers other carriers.

Forward secrecy covers the keys derived from `hs`: the record keys, the Trusted
confirmation key and the exporter secret. The payloads of the Hello and the Reply,
among them a first batch and the return frames, are protected by `join_psk` alone, as
TLS 1.3 early data is by its pre-shared key: whoever learns the secret of an epoch can
read and forge the payloads of that epoch's Joins, recorded or later. Without a party's
ephemeral private values the record keys stay secret; under Group 1 that rests on X25519,
under Group 2 on either X25519 or ML-KEM-768. A party that holds an epoch secret can also
forge new Joins of the epoch, and from them obtain record keys of its own. Bound and
Trusted Join records are not protected by these keys. The record keys of the Establish
carrier do not derive from the session secret.

### 11.6 Groups

A Group is a key exchange used by Join and by session epochs (`MULTIPATH.md` Section 3.7):

| Group | Name | Priority | Initiator share | Responder share | Shared secrets, in order |
|---|---|---|---|---|---|
| 0 | reserved | | | | |
| 1 | X25519 | 1 | 32 | 32 | the X25519 output, 32 bytes |
| 2 | X25519+MLKEM768 | 2 | 32 ‖ 1184, `e` ‖ `ek` | 32 ‖ 1088, `e` ‖ `ct` | the X25519 output, then the ML-KEM-768 shared secret, 32 bytes each |

Groups 1 and 2 MUST be implemented. Priority is a total order fixed by this table, higher
preferred. The initiator is the joiner of a Join and the offering end of an epoch
exchange; the responder is the acceptor or the answering end.

- **Shares.** In Group 2 the initiator generates an X25519 key pair and an ML-KEM-768 key
  pair and sends `e ‖ ek`; the responder generates an X25519 key pair, checks `ek` as
  Section 5 rule 8 says, computes `(ct, ss) = Encaps(ek)` and sends `e ‖ ct`; the
  initiator computes `ss = Decaps(dk, ct)`. In both Groups the X25519 output is that of
  the two `e`. An all-zero X25519 output, compared in constant time, and a refused `ek`
  are `kex`; `ct` is checked only by its length.
- **Combination.** The shared secrets, in the order of the table, are each the input
  keying material of one HKDF-Extract, with the previous output as salt:

      Combine(salt, ss_1, ..., ss_n) = Extract(... Extract(Extract(salt, ss_1), ss_2) ..., ss_n)

  Join uses the Suite's hash, an epoch SHA-256. Each shared secret is 32 bytes and
  uniformly random to the adversary, so the argument of Section 12 (Combination) applies
  to each step. The public values are bound by the transcript, not by `Combine`: in Join
  `th3` covers the ciphertexts that carry the shares, and in an epoch the derivation
  covers the frames that carry them (`MULTIPATH.md` Section 10.7).
- **Private values.** The joiner keeps its private values from the Hello until it is
  Proven or the Join fails; the acceptor drops its own once it has the shared secrets.
  They are random values of Section 5 rules 9 and 10.

A new Group MUST define: its share lengths for each role; the checks of its public values
and which of them are `kex`; the number, order and length of its shared secrets, each a
uniformly random value of 32 bytes or reduced to one by HKDF-Extract first; the single use
of its random values; its priority; and vectors.


**PQ confidentiality.** Every Establish Profile combines X25519 with ML-KEM-768. The keys of
the handshake, the records, the exporter and every Join of the session stay
secret as long as either X25519 or ML-KEM-768 resists the adversary, and, with a
pre-shared key, also while the pre-shared key stays secret. For a Join this holds
only in a session whose first pipe used Establish, since a session whose secret comes
from a TLS exporter has the strength of that TLS handshake, and for a Join's payloads,
which carry first batches and return frames, only while the epoch secret also stays
secret (Section 11.5). The record keys of a Join under Group 1 rest on X25519 alone
against a later quantum adversary that also holds the epoch secret; under Group 2 they
keep the strength of the handshake.
The responder's static
key and credentials in message 2 and the initiator's in message 3 are encrypted
under keys that already include the KEM shared secret, so they share this
protection against a passive adversary. An active adversary learns the responder's
identity in Profiles 4 and 5, which present it to any client, and in Profiles 1 and 3
once the pre-shared key is known. Without PQ authentication the handshake binds the peer
through its X25519 static key alone: an adversary able to break X25519 during a handshake
can impersonate a peer.

**PQ authentication.** The proof signs `h_p`, which covers the client's fresh `ek` and the
`ct` sent to it; only the holder of `dk` and the server that encapsulated know `ss`, and the
final keys depend on `ss`, so a proof binds its signer to this handshake. ML-DSA binds the
public key into what it signs. To impersonate a peer with PQ authentication an active quantum
adversary has to break both X25519 and ML-DSA-44, or the trust on which the verifier names
the proof key (Section 6.8). It covers Establish only: an epoch exchange on a Bound pipe keeps
the integrity of that carrier's own handshake.

**Application data.** Message 2 data goes out before the client is authenticated: in
Profiles 4 and 5 any party that connects, an active one on the path included, reads it, so it
holds only what may be disclosed to any client; in Profiles 1 and 3 only a holder of the
pre-shared key reads it. Message 1 data can be replayed (Section 6.9).

**Linkability.** Profiles 1 and 3 send the client's Handle in the clear, so a passive
observer can link the Establishes made with one Handle; Profiles 4 and 5 send no client
identifier, and Joins of one epoch share a Locator.

PQ confidentiality describes the handshake and the keys derived from it. Session data has the
protection of the carrier that carries it: in Bound that of the carrier's own cipher
suite, and in Trusted none from this layer; Section 7.2 states that a session is
protected as its weakest carrier.

In Profiles 1 and 3 the keys that protect message 1 depend only on the pre-shared key
and public values. A pre-shared key that leaks later therefore lets a recorded message
1 be decrypted, exposing `ek` and its payload: padding and any application data
(Section 6.9). Under the assumptions of the combination below, it exposes
neither the protected contents of messages 2 and 3 of an honest handshake nor the
keys derived at its end.

**Combination.** Each shared secret enters the chaining key through `MixKey`, that
is as the input keying material of HKDF-Extract, with the chaining key as salt. The
output is pseudorandom when either input is unknown to the adversary, by the
dual-PRF property of HMAC-SHA256 [BL15] [BBGS23]. That argument needs every shared
secret to have a fixed length of 32 bytes and be uniformly random to the adversary,
`ek` and `ct` to enter `h`, which every later AEAD of the handshake takes as associated
data, and `Split` to derive from the chaining key only; the integrity of the transcript
comes from those AEADs, so a substituted `ek` or `ct` fails there with `auth`. The order of the tokens is fixed and there is no
negotiation: the Profile enters the prologue and selects the protocol name, so a
changed Profile fails as `auth`.

The security of the combination as a whole rests on the following assumptions and is
not proven. A passive and an active adversary are separate models. Against an active
adversary the analysis has to cover at least one chosen-ciphertext interaction and what
the adversary observes after it; where X25519 is broken and ML-KEM-768 is not, the AEAD
that carries `ct` cannot be taken as a barrier to forgery.
ML-KEM-768 is used in full, IND-CCA with implicit rejection (Section 5 rule 8). A
formal analysis that assumes an ideal IND-CCA KEM does not show that a weaker
assumption suffices.

**Message sizes.** A Hello is at least 1216 bytes (Profiles 4 and 5) or 1248 bytes
(Profiles 1 and 3) plus its header, and a Reply at least 1200 bytes plus its
credentials; neither needs more than one Noise message. A receiver that sets a
lower limit for a Hello (Section 4.1) keeps it above these. In Profiles 4 and 5
`ek` is sent in the clear and its coefficients are all below 3329, so it is not
uniformly random; in Profiles 1 and 3 it is encrypted. The X25519 `e` is in the
clear in every Profile. The first message of Profiles 4 and 5 can therefore be
recognised as this protocol; a deployment that needs it to be harder to recognise
uses Profile 1 or 3.
