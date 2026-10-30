"""Decoding the bytes of a text file that a person (or Windows PowerShell 5.1) wrote."""
from __future__ import annotations

import codecs


def decode_text(data: bytes) -> str:
    """The text of a file, whichever encoding the editor chose.

    A byte order mark decides (UTF-8, UTF-16 as `Out-File` and `>` write it in Windows PowerShell 5.1, UTF-32). UTF-16 without
    a mark is recognised by the NUL that every second byte of mostly ASCII text holds. Otherwise the bytes are read as UTF-8, and
    when they are not UTF-8 as Windows-1252, the "ANSI" text that `Set-Content` writes there. Line ends are left as they are."""
    if data.startswith(codecs.BOM_UTF8):
        return data[len(codecs.BOM_UTF8) :].decode("utf-8", "replace")
    if data.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        return data.decode("utf-32", "replace")
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return data.decode("utf-16", "replace")
    unmarked = _utf16_without_mark(data)
    if unmarked is not None:
        return unmarked
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", "replace")


def _utf16_without_mark(data: bytes) -> str | None:
    """The text when the bytes look like UTF-16 with no byte order mark: NUL in (nearly) every second byte and nowhere else."""
    sample = data[:2048]
    pairs = len(sample) // 2
    if not pairs:
        return None
    even, odd = sample[0::2].count(0), sample[1::2].count(0)
    if odd >= pairs * 0.4 and even <= pairs * 0.05:
        return data.decode("utf-16-le", "replace")
    if even >= pairs * 0.4 and odd <= pairs * 0.05:
        return data.decode("utf-16-be", "replace")
    return None
