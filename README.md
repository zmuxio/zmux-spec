# zmux Specification

zmux is a stream multiplexer compatible with QMux: it carries many
flow-controlled byte streams, and optional datagrams, over reliable byte
pipes. A session can run on a single TCP or TLS connection, or on many pipes
at once, uplinks and downlinks from pluggable carriers (TCP, TLS,
WebSocket, HTTP bodies, QUIC streams, streams of another zmux session),
with every stream spread frame by frame across them, pipes added and
removed at any time, transparent failover when a pipe breaks, and
resumption after all of them are gone.

This repository is the protocol specification: what goes on the wire, how
each endpoint must react, and the test vectors that check it. It is written
for anyone implementing zmux or interoperating with it.

## QMux compatibility

zmux uses the record format of QMux ([draft-ietf-quic-qmux-02]) and the
frame encodings of QUIC version 1 [RFC 9000], with zmux frames and transport
parameters at codepoints of 2^14 and above.

- **A zmux endpoint talking to a plain QMux peer is a QMux-02 endpoint.**
  This is a hard invariant (SPEC.md Section 13): the peer sees only QMux-02
  behaviour, plus transport parameters it ignores.
- **zmux extensions are negotiated with transport parameters** and used only
  between zmux endpoints: multipath, pipe acknowledgement, lanes, resume,
  role negotiation, GOAWAY, stream preambles, priority updates and datagram
  flows (SPEC.md Section 5.5).

## Model

- **Pipes.** The unit of transport is a unidirectional, reliable, ordered
  byte pipe. A bidirectional carrier is two pipes that share a pipe ID and
  fail together. Pipes come from pluggable carrier adapters; a pipe on a
  multi-stream connection (QUIC, for example) can use several streams of it
  as lanes.
- **Session.** QUIC stream semantics (offsets, final sizes, absolute
  flow-control limits, idempotent control frames) over the set of pipes.
  Each pipe has an implicit byte position; PIPE_ACK reports positions, and
  the unacknowledged information of a broken pipe is re-sent on the others.
- **Features.** Multipath and resume are part of the core protocol and are
  enabled per session; the other features are negotiated extensions.

## Documents

| Document | Content |
|---|---|
| [SPEC.md](./SPEC.md) | records, frames, transport parameters, session lifecycle, streams, flow control, robustness rules, errors, QMux compatibility, security |
| [MULTIPATH.md](./MULTIPATH.md) | pipes, lanes, joins, acknowledgement, failure detection, reinjection, removal, sender rules, join security and carrier binding |
| [RESUME.md](./RESUME.md) | suspension and authenticated resumption |
| [EXTENSIONS.md](./EXTENSIONS.md) | roles, GOAWAY, stream preamble, priorities, datagrams, application error codes, nested sessions and relays, duplicate sessions |
| [STATE_MACHINE.md](./STATE_MACHINE.md) | stream, pipe and session state machines |
| [REGISTRY.md](./REGISTRY.md) | every codepoint, GREASE, registration policy |
| [WIRE_EXAMPLES.md](./WIRE_EXAMPLES.md) | byte-exact examples, generated and machine-checked |
| [CONFORMANCE.md](./CONFORMANCE.md) | conformance requirements, invalid inputs, test vectors, QMux interoperability |
| [TRACES.md](./TRACES.md) | the executable trace format and the trace runner |

All documents except WIRE_EXAMPLES.md are normative; the test-mode
settings of TRACES.md Section 3.4 bind only a trace runner. How an implementation
schedules frames across pipes, sizes its buffers and presents its API is
up to the implementation, within the rules these documents state.

## Test vectors

| Path | Content |
|---|---|
| [assets/registry.json](./assets/registry.json) | the registry in machine-readable form |
| [fixtures/wire/valid.ndjson](./fixtures/wire/valid.ndjson), [fixtures/wire/invalid.ndjson](./fixtures/wire/invalid.ndjson) | the wire corpus |
| [fixtures/traces/index.json](./fixtures/traces/index.json), [fixtures/profiles.json](./fixtures/profiles.json) | the executable traces and their endpoint profiles |
| [assets/manifest.json](./assets/manifest.json) | inventory, counts and checksums |
| [tools/](./tools) | reference codec, generators and validator |

Regenerate and check everything with:

```sh
python3 tools/rebuild_assets.py && python3 tools/validate_assets.py
```

The tools need Python 3.12 or later and nothing beyond the standard library.

## Versioning

This is version 1 of the zmux protocol. It replaces the earlier zmux wire
format (the one that starts with the ASCII magic `ZMUX`) under the same
major version; the two fail at their first bytes and do not interoperate.
zmux has no version negotiation: additive changes are negotiated
extensions, and a change to existing encodings would be a new protocol
version with new ALPN identifiers (REGISTRY.md Section 2.6).

## Implementations

- [zmux-go](https://github.com/zmuxio/zmux-go) (Go)
- [zmux-rs](https://github.com/zmuxio/zmux-rs) (Rust)
- [zmux-python](https://github.com/zmuxio/zmux-python) (Python, bindings to zmux-rs)

[draft-ietf-quic-qmux-02]: https://datatracker.ietf.org/doc/draft-ietf-quic-qmux/02/
[RFC 9000]: https://www.rfc-editor.org/rfc/rfc9000
