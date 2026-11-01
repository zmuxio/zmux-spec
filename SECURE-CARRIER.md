# Protected carriers

Status: experimental draft. The wire format below may change in any
revision, without version negotiation or compatibility with earlier
drafts. Reference implementation: zmux-go `seal`; vectors in zmux-go
`testdata/seal/vectors.json`.

A protected carrier is a carrier transform: below the session, above the
carrier (TCP, TLS, WebSocket, an exchange carrier). It gives each carrier
end-to-end confidentiality, integrity and mutual authentication against
any intermediary that terminates the outer TLS. The session sees a
message carrier (SPEC.md Section 3.4) whose messages are records; nothing
in the session changes, and no transport parameter is involved. Each
carrier, joins included, runs its own handshake.

## 1. Handshakes

Two Noise patterns (Noise Protocol Framework, revision 34), one suite,
nothing negotiated:

- **regular**: `Noise_IKpsk1_25519_AESGCM_SHA256`; the client knows the
  server's static key (a pinned set; the first is used) and has a static
  key of its own;
- **bootstrap**: `Noise_NXpsk0_25519_AESGCM_SHA256`
  (`-> psk, e` / `<- e, ee, s, es`); the client knows only a PSK and learns
  the server's static key from msg2.

Prologue: the ASCII string `zmux sc v1`. Static and ephemeral keys are
X25519. The Noise psk is SHA-256 of the host's PSK (at least 16 bytes of
at least 128 bits of uniform randomness), or 32 zero bytes when the
regular handshake has no PSK; the bootstrap handshake requires one.

### 1.1 Messages

    regular   msg1 = e(32) ‖ enc_s(48) ‖ Len(2) ‖ enc_payload(Len+16)
    bootstrap msg1 = e(32) ‖ tag(16)   ‖ Len(2) ‖ enc_payload(Len+16)
    regular   msg2 = e(32)             ‖ Len(2) ‖ enc_payload(Len+16)
    bootstrap msg2 = e(32) ‖ enc_s(48) ‖ Len(2) ‖ enc_payload(Len+16)

The tokens are processed as Noise specifies. In addition, the tag is
mixed into the handshake hash (MixHash) right after `e`, and Len, the
payload's plaintext length as a big-endian uint16, is mixed in right
before the payload's EncryptAndHash. msg1 is at most 65535 bytes. On a
byte stream the messages follow each other; on a message carrier each is
one message.

- payload1 = Stamp(8) ‖ records: Stamp is the client's estimate of the
  server's Unix time in milliseconds, big-endian; records are the client's
  first flight, zero or more records in the byte-stream form of SPEC.md
  Section 3.1 (Size, then Frames).
- payload2 = ServerTime(8) ‖ Flags(1) ‖ records: Flags bit 0 is set when
  the first flight was accepted; other bits are 0 and ignored. The records
  are the server's first records (the reference implementation sends
  none).

Every record of a payload has a Size from 1 to 65535; anything else, and
a payload shorter than its fixed fields, fails the handshake.

### 1.2 Ephemeral keys

The first 5 bytes of each ephemeral public key must not begin a zmux
first record in either framing (SPEC.md Section 6.2; REGISTRY.md Section
2.4), and its first two bytes must not be `16 03`. A key that fails is
drawn again. A server therefore tells a plain zmux carrier from a
protected one by its first bytes, without trying keys.

### 1.3 Bootstrap tags

    K_tag  = HKDF-SHA256(IKM = Noise psk, salt = empty, info = "zmux sc tag", 32)
    bucket = floor(Stamp / 600000)
    tag    = first 16 bytes of HMAC-SHA256(K_tag, BE64(bucket))

The server indexes the tags of its bootstrap clients for the buckets
within ±2 of its own clock.

### 1.4 Server processing of msg1

1. Classify the first bytes. A zmux first record is a plain carrier (an
   entrance may accept it only on TLS that the server terminates; one
   that an intermediary may reach over TLS of its own accepts none).
2. If bytes 32..47 are an indexed tag, process a bootstrap msg1 with that
   client's PSK; on success the client is identified.
3. Otherwise, for each server static key in turn, process e, es and s; the
   first key that decrypts `enc_s` is used. The decrypted static key is
   looked up by the host, which returns whether it is allowed and the
   PSK it must have mixed in; then ss, psk and the payload are processed.
4. Any failure (no key, not allowed, PSK or payload authentication,
   malformed payload, a carrier that ends or times out first) closes the
   carrier or stalls it. Nothing is ever sent in answer.

### 1.5 First flight and replay

After msg1 verifies, the server accepts its first flight when the Stamp is
within W of its clock (default 60 s), not before its start minus W, and
the handshake hash after msg1 was not seen within the last 2W (a
single-use cache; when the cache is full nothing new is accepted).
Otherwise the handshake still completes, the payload's records are
dropped and Flags bit 0 is clear. A client whose first flight was refused
sends those records again as its first transport frames, before anything
else, so the session loses nothing.

The client learns the offset of its clock from ServerTime (taking it as
the server's time halfway between sending msg1 and receiving msg2) and
uses it for later stamps and tags. Before the first msg2 it may use an
outer HTTP `Date` response field as an unauthenticated hint.

The server treats streams of an accepted first flight as replayable
(SPEC.md Section 6.1.2) until the client's first transport frame arrives,
which proves the handshake was not replayed. A replayed msg1 can reach
another server that shares keys and PSKs but not the cache; first-flight
data must tolerate that.

## 2. Transport frames

After msg2 the Noise Split gives the client's sending and receiving
cipher states. Each record (its Frames, without Size) is one frame:

    byte stream:     Len(2) ‖ AEAD(record)
    message carrier: AEAD(record)              (one message per frame)

Len is the record's length (1 to 65535), big-endian. The AEAD's
associated data is the two Len bytes in both forms. Nonces are the cipher
state's implicit counter n (the 12-byte AES-GCM nonce is 4 zero bytes and
BE64(n)). Each direction applies Noise REKEY before frames n = 2^20,
2·2^20, … (n continues). An authentication failure, a Len of 0, a frame
cut short and a nonce of 2^64−1 fail the carrier; nothing is sent.

Overhead: 18 bytes per record on a byte stream, 16 on a message carrier.
A message carrier carries messages of up to 65535 + 16 bytes.

## 3. Exporter

    exporter_secret = HKDF-Expand(ck, "zmux sc exporter" ‖ h, 32)
    Export(label, context, L) = HKDF-Expand(HKDF-Extract(salt = exporter_secret, IKM = label), context, L)

with ck and h the chaining key and handshake hash after msg2. A carrier
offers this exporter, and not that of any TLS below it, as its innermost
end-to-end handshake (MULTIPATH.md Section 10.1): it is the source of
`pipe_auth` source E and of carrier-bound joins. A carrier-bound join
completes its handshake before it writes the PIPE_JOIN.

## 4. Admission

On a byte stream a server reads msg1 within its admission (5 bytes to
classify, then the regular or bootstrap header, then the payload). A
carrier decided on its HTTP request head (WebSocket) is accepted on the
head and its first message is read and verified before the session sees
the carrier. The host's identity of the client is what admission hands to
the session.

## 5. Limits

- Split bootstrap carriers are not supported: the handshake needs both
  directions on one carrier.
- The first flight of a bootstrap msg1 depends on the PSK alone: a PSK
  disclosed later decrypts it. A party that knows the PSK can act as the
  server in a bootstrap handshake.
- The regular msg1 payload depends on es, ss and psk; traffic after msg2
  has forward secrecy.
