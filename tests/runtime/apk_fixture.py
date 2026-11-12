#!/usr/bin/env python3
"""Mutate signed APK v3 metadata, rather than deflate framing or padding."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import zlib

PACKAGE = b"luci-i18n-passwall2-zh-cn"


def tampered_metadata(content):
    if content[:4] == b"ADB.":
        plain = content
    elif content[:4] == b"ADBd":
        plain = zlib.decompress(content[4:], -15)
    elif content[:4] == b"ADBc" and len(content) >= 6 and content[4] == 1:
        plain = zlib.decompress(content[6:], -15)
    else:
        raise ValueError("expected an uncompressed or deflate APK v3 package")
    if plain[:4] != b"ADB." or len(plain) < 12:
        raise ValueError("invalid uncompressed ADB package")
    offset = 8
    metadata = None
    signed = False
    while offset < len(plain):
        if offset + 4 > len(plain):
            raise ValueError("truncated ADB block")
        header = struct.unpack_from("<I", plain, offset)[0]
        kind, size = header >> 30, header & 0x3fffffff
        if kind == 3 or size < 4 or offset + size > len(plain):
            raise ValueError("unsupported or truncated ADB block")
        if kind == 0:
            if metadata is not None or offset != 8:
                raise ValueError("unexpected metadata block")
            metadata = (offset + 4, offset + size)
        signed |= kind == 1
        offset += (size + 7) & ~7
    if offset != len(plain) or metadata is None or not signed:
        raise ValueError("expected complete metadata and signature blocks")
    start, end = metadata
    position = plain.find(PACKAGE, start, end)
    if position < 0:
        raise ValueError("translation name is absent from signed metadata")
    # Keep the ADB structure and all signature/data blocks intact. The changed
    # package name remains syntactically valid but its old signature must fail.
    changed = plain[:position] + b"m" + plain[position + 1:]
    proof = {
        "source_apk_sha256": hashlib.sha256(content).hexdigest(),
        "canonical_apk_sha256": hashlib.sha256(plain).hexdigest(),
        "tampered_apk_sha256": hashlib.sha256(changed).hexdigest(),
        "signed_metadata_offset": position,
        "changed_bytes": 1,
        "mutation": "same-length package-name change inside signed ADB metadata",
    }
    return changed, proof


def write_fixture(source, destination):
    changed, proof = tampered_metadata(Path(source).read_bytes())
    with Path(destination).open("xb") as output:
        output.write(changed)
    return proof


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    print(json.dumps(write_fixture(args.source, args.destination), indent=2))


if __name__ == "__main__":
    main()
