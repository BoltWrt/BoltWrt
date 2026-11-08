"""Version format shared by firmware configuration and verification."""
import re


_NUMBER = r"(?:0|[1-9][0-9]*)"
PRODUCT_VERSION = rf"{_NUMBER}\.{_NUMBER}\.{_NUMBER}(?:-rc[1-9][0-9]*)?"


def version_from_tag(tag):
    if match := re.fullmatch(rf"v({PRODUCT_VERSION})", tag):
        return match[1]
    raise ValueError(f"invalid release tag {tag!r} (expected vX.Y.Z or vX.Y.Z-rcN)")
