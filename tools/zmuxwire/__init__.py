"""Reference codec helpers for the zmux specification assets.

This package is a test-asset generator, not an implementation. It encodes and
decodes the wire format of SPEC.md, MULTIPATH.md, RESUME.md and EXTENSIONS.md
so that WIRE_EXAMPLES.md, the wire corpus and the executable traces can be
generated and checked byte for byte.
"""

from . import registry, frames, crypto, preamble, varint  # noqa: F401
