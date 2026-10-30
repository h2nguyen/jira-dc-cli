"""The text of Word, Excel and PowerPoint files (Office Open XML: docx, xlsx, pptx) with the standard library only.

These files are ZIP archives of XML parts, and the archive comes from outside: a ZIP can be a bomb (a few kilobytes that expand to
gigabytes) and XML can declare entities that expand the same way. So the archive is looked at before it is opened (how many entries,
how large its directory, a ZIP64 record that a long comment hides), a part is unpacked only when it is stored or deflated, as Office
files do it (bzip2 and LZMA expand a whole block at once, whatever limit is asked for), every part is read with a limit and all parts
together are limited, and a part that declares a document type or is not UTF-8 is refused. What is read is text only: no macro, no
embedded object, no picture is touched.

More limits close the ways that a small file can still grow while it is read. The names in the XML are checked before it is parsed
with namespaces (a namespace address of thousands of characters, used by thousands of attributes, becomes that many copies of it). The
text that comes out is counted as it is made, and a cell or a shared string is cut at the length Excel allows (one string that a million
cells point to is a million copies). The tags of all the parts together are counted before a part is parsed, because the time that
reading takes grows with them. And every number in the file is read with a bound, because a number in XML is whatever the file says.
"""
from __future__ import annotations

import io
import posixpath
import re
import struct
import xml.etree.ElementTree as ET
import zipfile
import zlib
from typing import Iterator
from xml.parsers import expat

from .errors import ValidationError

MAX_ENTRIES = 2000  # files in the archive
MAX_DIRECTORY_BYTES = 1_000_000  # the directory of the archive: every entry in it costs memory as soon as the archive is opened
MAX_PART_BYTES = 6 * 1024 * 1024  # one XML part, as it is after unpacking
MAX_TOTAL_BYTES = 32 * 1024 * 1024  # all the parts that are read
MAX_TEXT_CHARS = MAX_PART_BYTES  # the text that a document may make, whatever the reader wants to see of it: the text of a part is shorter than the part
MAX_CELL_CHARS = 32_767  # what Excel allows in one cell; a cell or a shared string that is longer is cut
MAX_MARKUP = 1_000_000  # `<` in all the parts of a document: reading takes up to about two microseconds for each, so about two seconds at the most
MAX_PARTS = 400  # sheets or slides that are read from one file
MAX_NAME_CHARS = 128  # an element or attribute name as it is written (`prefix:name`): the real ones have less than 50
MAX_NAMESPACE_CHARS = 200  # an `xmlns` value: the real ones have less than 100
MAX_ATTRIBUTES_PER_ELEMENT = 256  # `xmlns` declarations included: the root element of a Word document has about 50
MAX_ATTRIBUTES = 2_000_000  # in one part
MAX_COLUMNS = 16_384  # `XFD`, the last column of a sheet
_MAX_GAP = 8  # empty cells that stand in for a gap in a row of a sheet
_MAX_NESTING = 4  # tables inside tables
_MAX_LEVEL = 8  # list levels that are shown as an indent
_MAX_DIGITS = 9  # a number in the XML that is longer is not looked at: `int()` is slow on thousands of digits and refuses some
_MAX_REFERENCE = 12  # characters of a cell reference: `XFD1048576` has ten
_MIB = 1024 * 1024
_HOSTILE = "a damaged or hostile file"
_PACKINGS = (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)  # how an Office file packs a part. `zipfile` bounds the output of these two when it is asked to
_ENCRYPTED = 0x1  # the bit of the flags of a part that says that it is encrypted
_END_WINDOW = 65_535 + 22  # the longest comment and the end record: the last bytes of a file that `zipfile` looks into for the end record

KINDS = ("docx", "xlsx", "pptx")
_UNSAFE_XML = re.compile(rb"<!(?:DOCTYPE|ENTITY)", re.IGNORECASE)
_DECLARED_ENCODING = re.compile(rb"""^\s*<\?xml[^>]*?\bencoding\s*=\s*["']([^"']+)["']""")
_HEADING = re.compile(r"^heading\s?([1-9])$", re.IGNORECASE)
_CELL_REFERENCE = re.compile(r"([A-Za-z]{1,3})[0-9]{1,7}")


class UnreadableDocument(ValidationError):
    """The file is no Office Open XML document that can be read safely; the message says why."""


class _Budget:
    """What one document may use: bytes of XML that are read, tags that are parsed, characters of text that are made, and each part once."""

    def __init__(self) -> None:
        self.left = MAX_TOTAL_BYTES
        self.markup_left = MAX_MARKUP
        self.text_left = MAX_TEXT_CHARS
        self.opened: set[str] = set()

    def open(self, name: str) -> None:
        """A part is unpacked once. A file that lists the same part again (400 sheets that are one part) makes the reader do the same work
        over and over; no real document does that."""
        if name in self.opened:
            raise UnreadableDocument(
                f"The part {name} is used more than once by the document: it is read once, and a file that uses it again is refused ({_HOSTILE})."
            )
        self.opened.add(name)

    def take(self, size: int) -> None:
        self.left -= size
        if self.left < 0:
            raise UnreadableDocument(f"The document holds more than {MAX_TOTAL_BYTES // _MIB} MiB of XML: it is not read ({_HOSTILE}).")

    def markup(self, count: int, name: str) -> None:
        """Counts the tags of a part before it is parsed: a refusal then costs a count and no more."""
        if count > self.markup_left:
            if count > MAX_MARKUP:
                what = f"The part {name} holds more than {MAX_MARKUP:,} tags"
            else:
                what = f"The document holds more than {MAX_MARKUP:,} tags in all its parts"
            raise UnreadableDocument(f"{what}: it is not read ({_HOSTILE}, or too large to be read as text).")
        self.markup_left -= count

    def emit(self, count: int) -> None:
        """Counts text that is made, before it is put together: a refusal then comes while little of it exists."""
        self.text_left -= count
        if self.text_left < 0:
            raise UnreadableDocument(
                f"The document expands to more text than can be read (more than {MAX_TEXT_CHARS:,} characters, a cell that is used again "
                "counted every time): it is not read."
            )


def _whole_number(text: str, ceiling: int) -> int | None:
    """The number that `text` is, at most `ceiling`; None when it is none. Only the digits 0 to 9 count (`str.isdigit` also takes `²`,
    which `int()` refuses), and a number of more than nine digits is a huge one."""
    text = text.strip()
    if not text or not text.isascii() or not text.isdecimal():
        return None
    digits = text.lstrip("0")
    return min(int(digits or "0"), ceiling) if len(digits) <= _MAX_DIGITS else ceiling


def _add(lines: list[str], budget: _Budget, *new: str) -> None:
    """Lines to the text, counted."""
    for line in new:
        budget.emit(len(line) + 1)
    lines.extend(new)


# -- the archive -------------------------------------------------------------------------------


def _check_directory(data: bytes) -> None:
    """Refuse an archive with too many entries before `zipfile` builds an object for each of them, and a ZIP64 archive altogether (its
    end record would say one thing and the ZIP64 record another: `zipfile` believes the second). The numbers come from the record at the
    end of the file, which is where `zipfile` looks for them too: in the last 65,557 bytes, as far back as the longest comment reaches.
    The locator of the ZIP64 record stands in the 20 bytes in front of the end record, wherever in the file that is, so it is read from
    the whole file and not from that window: a comment of 65,535 bytes pushes the end record to the start of the window."""
    tail = data[-_END_WINDOW:]
    at = tail.rfind(b"PK\x05\x06")
    if at < 0 or len(tail) - at < 22:
        raise UnreadableDocument("The file is no Office Open XML document: it is no ZIP archive (it may be damaged or protected with a password).")
    end = len(data) - len(tail) + at  # where the end record stands in the file
    if end >= 20 and data[end - 20 : end - 16] == b"PK\x06\x07":  # the locator of a ZIP64 end record: `zipfile` then takes its numbers from there
        raise UnreadableDocument(
            "The archive is a ZIP64 archive, which only a very large file or one with a huge number of entries needs: it is too large for a text read."
        )
    (entries,) = struct.unpack_from("<H", tail, at + 10)
    (size,) = struct.unpack_from("<I", tail, at + 12)
    if entries > MAX_ENTRIES or size > MAX_DIRECTORY_BYTES:  # 0xFFFF and 0xFFFFFFFF, the signs of a ZIP64 archive, are over the limits too
        raise UnreadableDocument(f"The archive holds more than {MAX_ENTRIES} files or an oversized directory: it is not read ({_HOSTILE}).")


def _open(data: bytes) -> zipfile.ZipFile:
    _check_directory(data)
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, NotImplementedError, ValueError, OSError) as error:
        raise UnreadableDocument(f"The file is no Office Open XML document: its archive cannot be read ({error}).") from error
    if len(archive.infolist()) > MAX_ENTRIES:
        raise UnreadableDocument(f"The archive holds more than {MAX_ENTRIES} files: it is not read ({_HOSTILE}).")
    return archive


def _part(archive: zipfile.ZipFile, name: str, budget: _Budget) -> bytes | None:
    """The unpacked bytes of one part, None when the archive has no such part. Never more than `MAX_PART_BYTES`.

    Only a part that is stored or deflated is unpacked, and one that is not encrypted: a bzip2 or LZMA member is unpacked by `zipfile` a
    whole block at a time, with no bound on what the block becomes, so a few hundred bytes would take gigabytes before the size that
    the archive declares (which the file chooses) could be looked at. `read` of a stored or deflated part stops at the limit that it is
    given, whatever the part declares."""
    try:
        info = archive.getinfo(name)
    except KeyError:
        return None
    if info.flag_bits & _ENCRYPTED:
        raise UnreadableDocument(f"The part {name} is protected with a password: it is not read.")
    if info.compress_type not in _PACKINGS:
        packing = getattr(zipfile, "compressor_names", {}).get(info.compress_type, f"method {info.compress_type}")
        raise UnreadableDocument(
            f"The part {name} is packed with {packing}, which an Office file never uses (it stores or deflates its parts): it is not read ({_HOSTILE})."
        )
    budget.open(name)
    too_large = f"it is not read (a bomb, or too large to be read as text: the limit is {MAX_PART_BYTES // _MIB} MiB)"
    if info.file_size > MAX_PART_BYTES:
        raise UnreadableDocument(f"The part {name} unpacks to {info.file_size // _MIB} MiB: {too_large}.")
    try:
        with archive.open(info) as stream:
            data = stream.read(MAX_PART_BYTES + 1)
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, zlib.error, EOFError, OSError, ValueError, struct.error) as error:
        # These messages come from `zipfile` and `zlib` and say nothing of the content.
        raise UnreadableDocument(f"The part {name} cannot be unpacked ({error}).") from error
    if len(data) > MAX_PART_BYTES:
        raise UnreadableDocument(f"The part {name} unpacks to more than {MAX_PART_BYTES // _MIB} MiB: {too_large}.")
    budget.take(len(data))
    return data


def _shape(data: bytes, name: str) -> None:
    """Refuse XML that is made to grow when it is parsed with namespaces. `ElementTree` writes every name as `{address}name`, so an
    `xmlns` address of 20,000 characters used by 10,000 attributes costs 200 MB, from a file of a few kilobytes. The names are read here
    as they are written, without namespaces, and refused when they are far longer than any real document has them: an `xmlns` value, an
    element or attribute name, and the number of attributes of one element or of the whole part."""
    total = 0

    def too(what: str) -> UnreadableDocument:
        return UnreadableDocument(f"The part {name} has {what}: it is refused ({_HOSTILE}).")

    def start(tag: str, attributes: list[str]) -> None:
        nonlocal total
        if len(tag) > MAX_NAME_CHARS:
            raise too(f"an element name of more than {MAX_NAME_CHARS} characters")
        if not attributes:
            return
        count = len(attributes) // 2
        total += count
        if count > MAX_ATTRIBUTES_PER_ELEMENT:
            raise too(f"an element with more than {MAX_ATTRIBUTES_PER_ELEMENT} attributes")
        if total > MAX_ATTRIBUTES:
            raise too(f"more than {MAX_ATTRIBUTES:,} attributes")
        for position in range(0, len(attributes), 2):
            if len(attributes[position]) > MAX_NAME_CHARS:
                raise too(f"an attribute name of more than {MAX_NAME_CHARS} characters")
            if attributes[position].startswith("xmlns") and len(attributes[position + 1]) > MAX_NAMESPACE_CHARS:
                raise too(f"a namespace address of more than {MAX_NAMESPACE_CHARS} characters")

    def document_type(*_: object) -> None:
        raise UnreadableDocument(f"The part {name} declares a document type or an entity: it is refused (such XML can be made to expand without end).")

    parser = expat.ParserCreate()  # no namespace processing: a name stays as long as it is written
    parser.ordered_attributes = True
    parser.StartElementHandler = start
    parser.StartDoctypeDeclHandler = document_type
    try:
        parser.Parse(data, True)
    except expat.ExpatError as error:
        raise UnreadableDocument(f"The part {name} is not well-formed XML ({error}).") from error


def _checked(data: bytes, name: str, budget: _Budget) -> bytes:
    """The XML only when it is safe to parse: UTF-8, without a document type (that is where entities are declared), with a number of
    tags that the time allows (counted for all the parts of the document together, before any of them is parsed) and with names of a
    size that a document has."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")) or b"\x00" in data[:4]:
        raise UnreadableDocument(f"The part {name} is not UTF-8, which is all that is read.")
    declared = _DECLARED_ENCODING.match(data[3:203] if data.startswith(b"\xef\xbb\xbf") else data[:200])
    if declared and declared.group(1).lower().replace(b"_", b"-") not in (b"utf-8", b"utf8"):
        encoding = declared.group(1).decode("ascii", "replace")
        raise UnreadableDocument(f"The part {name} declares the encoding {encoding}, which is not UTF-8, and UTF-8 is all that is read.")
    if _UNSAFE_XML.search(data):
        raise UnreadableDocument(f"The part {name} declares a document type or an entity: it is refused (such XML can be made to expand without end).")
    budget.markup(data.count(b"<"), name)
    _shape(data, name)
    return data


def _xml(archive: zipfile.ZipFile, name: str, budget: _Budget) -> ET.Element | None:
    data = _part(archive, name, budget)
    if data is None:
        return None
    try:
        return ET.fromstring(_checked(data, name, budget))
    except ET.ParseError as error:
        raise UnreadableDocument(f"The part {name} is not well-formed XML ({error}).") from error


def _normalized(folder: str, target: str) -> str:
    return target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join(folder, target))


def _relationships(archive: zipfile.ZipFile, budget: _Budget, part: str) -> dict[str, tuple[str, str]]:
    """`{id: (type, path of the target)}` from the relationships file of a part."""
    folder, _, name = part.rpartition("/")
    root = _xml(archive, f"{folder}/_rels/{name}.rels".lstrip("/"), budget)
    found: dict[str, tuple[str, str]] = {}
    for item in _children(root, "Relationship"):
        found[item.get("Id", "")] = (item.get("Type", ""), _normalized(folder, item.get("Target", "")))
    return found


# -- small helpers on elements ---------------------------------------------------------------------


def _local(tag: str) -> str:
    return tag.rpartition("}")[2]


def _children(node: ET.Element | None, name: str) -> list[ET.Element]:
    return [child for child in node if _local(child.tag) == name] if node is not None else []


def _child(node: ET.Element | None, name: str) -> ET.Element | None:
    for child in node if node is not None else []:
        if _local(child.tag) == name:
            return child
    return None


def _attr(node: ET.Element | None, name: str) -> str:
    """An attribute by its local name, so that the namespace of a transitional or a strict document does not matter."""
    for key, value in (node.attrib if node is not None else {}).items():
        if _local(key) == name:
            return value
    return ""


def _qualified(node: ET.Element, name: str) -> str:
    """An attribute that has a namespace, by its local name: the `r:id` of a relationship, not the plain `id` that may stand next to it."""
    for key, value in node.attrib.items():
        if key.startswith("{") and _local(key) == name:
            return value
    return ""


def _walk(node: ET.Element, skip: frozenset[str]) -> Iterator[ET.Element]:
    """Every descendant in document order except those below an element named in `skip`, without recursion: a hostile file can nest as
    deep as it likes."""
    stack = [iter(node)]
    while stack:
        for child in stack[-1]:
            yield child
            if _local(child.tag) not in skip:
                stack.append(iter(child))
                break
        else:
            stack.pop()


# -- docx ---------------------------------------------------------------------------------------

_DOCX_SKIP = frozenset({"pPr", "rPr", "del", "Fallback", "instrText", "delText", "tblPr", "sectPr"})
_DOCX_BLOCKS = frozenset({"sdt", "sdtContent", "customXml", "ins", "moveTo"})


def _runs(paragraph: ET.Element) -> str:
    """The text of a paragraph: runs, tabs and breaks. Deleted text (tracked changes), field codes and the second copy of a text box
    (`mc:Fallback`) are left out."""
    parts: list[str] = []
    for node in _walk(paragraph, _DOCX_SKIP):
        tag = _local(node.tag)
        if tag == "t":
            parts.append(node.text or "")
        elif tag == "tab":
            parts.append("\t")
        elif tag in ("br", "cr"):
            parts.append("\n")
        elif tag == "p":  # a paragraph inside a text box
            parts.append("\n")
    return "".join(parts)


def _blocks(container: ET.Element) -> Iterator[ET.Element]:
    """The paragraphs and tables of a body or a cell, in order, also those inside content controls."""
    stack = [iter(container)]
    while stack:
        for node in stack[-1]:
            tag = _local(node.tag)
            if tag in ("p", "tbl"):
                yield node
            elif tag in _DOCX_BLOCKS:
                stack.append(iter(node))
                break
        else:
            stack.pop()


def _paragraph(paragraph: ET.Element) -> str:
    """A paragraph as a line: headings with `#`, list items with `-`. `numId` 0 is how Word says that a paragraph is no list item."""
    text = _runs(paragraph).strip()
    if not text:
        return ""
    properties = _child(paragraph, "pPr")
    style = _attr(_child(properties, "pStyle"), "val")
    heading = _HEADING.match(style)
    if heading or style.lower() in ("title", "subtitle"):
        return "#" * (int(heading.group(1)) if heading else 1) + " " + text
    numbering = _child(properties, "numPr")
    if numbering is not None and _whole_number(_attr(_child(numbering, "numId"), "val"), 1) != 0:
        level = _whole_number(_attr(_child(numbering, "ilvl"), "val"), _MAX_LEVEL)
        return "  " * (level or 0) + "- " + text
    return text


def _table(table: ET.Element, depth: int = 0) -> list[str]:
    """The rows of a table as lines, the cells separated by ` | `."""
    rows: list[str] = []
    for row in _children(table, "tr"):
        cells: list[str] = []
        for cell in _children(row, "tc"):
            texts = []
            for block in _blocks(cell):
                if _local(block.tag) == "p":
                    texts.append(" ".join(_runs(block).split()))
                elif depth < _MAX_NESTING:
                    texts.append(" / ".join(_table(block, depth + 1)))
            cells.append(" ".join(text for text in texts if text))
        if any(cells):
            rows.append(" | ".join(cells))
    return rows


def _docx(archive: zipfile.ZipFile, budget: _Budget) -> str:
    root = _xml(archive, "word/document.xml", budget)
    if root is None:
        raise UnreadableDocument("The file has no word/document.xml: it is no Word document.")
    body = _child(root, "body")
    lines: list[str] = []
    for block in _blocks(body) if body is not None else []:
        if _local(block.tag) == "p":
            line = _paragraph(block)
            if line:
                _add(lines, budget, line)
        else:
            _add(lines, budget, *_table(block))
    return "\n".join(lines)


# -- xlsx ---------------------------------------------------------------------------------------


def _all_text(node: ET.Element, skip: frozenset[str] = frozenset({"rPh"})) -> str:
    """The text of a string item or of an inline string: every `t`, phonetic hints (`rPh`) aside."""
    return "".join(item.text or "" for item in _walk(node, skip) if _local(item.tag) == "t")


def _one_line(text: str) -> str:
    """Blanks and line breaks folded into single blanks, and no more than `MAX_CELL_CHARS` characters: a longer text is cut and ends
    with `…`."""
    text = " ".join(text.split())
    return text if len(text) <= MAX_CELL_CHARS else text[: MAX_CELL_CHARS - 1] + "\u2026"


def _shared_strings(archive: zipfile.ZipFile, budget: _Budget) -> list[str]:
    """The shared strings, each made one line and cut once here, so that a cell that points to one costs no more than its length."""
    root = _xml(archive, "xl/sharedStrings.xml", budget)
    return [_one_line(_all_text(item)) for item in _children(root, "si")] if root is not None else []


def _column(reference: str) -> int | None:
    """The zero based column of a cell reference such as `AB12`; None when there is none, or one beyond the last column of a sheet (`XFD`).
    A reference of `XFD1048576` has ten characters: a longer one is no reference, and is not looked at."""
    if len(reference) > _MAX_REFERENCE:
        return None
    found = _CELL_REFERENCE.fullmatch(reference)
    if found is None:
        return None
    number = 0
    for char in found.group(1).upper():
        number = number * 26 + ord(char) - 64
    return number - 1 if number <= MAX_COLUMNS else None


def _cell(cell: ET.Element, strings: list[str]) -> str:
    kind = cell.get("t", "")
    value = _child(cell, "v")
    text = (value.text or "") if value is not None else ""
    if kind == "s":
        index = _whole_number(text, len(strings))
        return strings[index] if index is not None and index < len(strings) else ""  # one line and cut already
    if kind == "inlineStr":
        inline = _child(cell, "is")
        text = _all_text(inline) if inline is not None else ""
    elif kind == "b":
        text = "TRUE" if text.strip() == "1" else "FALSE"
    return _one_line(text)


def _row(row: ET.Element, strings: list[str], budget: _Budget) -> str:
    """One row as a line, the cells separated by tabs, a gap in the row by a few empty cells, no tab at the end. The text is counted cell
    by cell, before the line is put together: a row of cells that all point to one long string must not be built first."""
    cells: list[str] = []
    for position, cell in enumerate(_children(row, "c")):
        column = _column(cell.get("r", ""))
        column = position if column is None else column
        gap = min(column - len(cells), _MAX_GAP) if column > len(cells) else 0
        text = _cell(cell, strings)
        budget.emit(len(text) + gap + 1)
        cells.extend([""] * gap)
        cells.append(text)
    return "\t".join(cells).rstrip("\t")


def _rows(data: bytes, name: str, strings: list[str], budget: _Budget) -> Iterator[str]:
    """The non-empty rows of a sheet, read one at a time so that a large sheet does not become a large tree."""
    try:
        for _, element in ET.iterparse(io.BytesIO(_checked(data, name, budget)), events=("end",)):
            if _local(element.tag) == "row":
                line = _row(element, strings, budget)
                element.clear()
                if line.strip("\t"):
                    yield line
    except ET.ParseError as error:
        raise UnreadableDocument(f"The part {name} is not well-formed XML ({error}).") from error


def _numbered(archive: zipfile.ZipFile, prefix: str) -> list[str]:
    """The parts `<prefix><number>.xml`, ordered by the number: `sheet2` before `sheet10`. The number is plain digits, at most nine."""
    pattern = re.compile(re.escape(prefix) + r"([0-9]{1,9})\.xml")
    found = [(int(match.group(1)), name) for name in archive.namelist() if (match := pattern.fullmatch(name))]
    return [name for _, name in sorted(found)]


def _sheets(archive: zipfile.ZipFile, budget: _Budget) -> list[tuple[str, str]]:
    """`(name, part)` of the sheets in the order of the workbook; without a usable workbook the sheets by their file number."""
    workbook = _xml(archive, "xl/workbook.xml", budget)
    if workbook is None:
        raise UnreadableDocument("The file has no xl/workbook.xml: it is no Excel workbook.")
    targets = _relationships(archive, budget, "xl/workbook.xml")
    sheets = _child(workbook, "sheets")
    found = []
    for sheet in _children(sheets if sheets is not None else workbook, "sheet"):
        target = targets.get(_qualified(sheet, "id"))
        if target:
            found.append((sheet.get("name", ""), target[1]))
    if found:
        return found
    return [(f"Sheet{index}", name) for index, name in enumerate(_numbered(archive, "xl/worksheets/sheet"), 1)]


def _xlsx(archive: zipfile.ZipFile, budget: _Budget) -> str:
    sheets = _sheets(archive, budget)
    strings = _shared_strings(archive, budget)
    lines: list[str] = []
    for name, part in sheets[:MAX_PARTS]:
        data = _part(archive, part, budget)
        if data is None:
            continue
        _add(lines, budget, f"## {name}")
        for line in _rows(data, part, strings, budget):  # a row is already counted, cell by cell
            lines.append(line)
    if len(sheets) > MAX_PARTS:
        lines.append(f"[{len(sheets) - MAX_PARTS} more sheets are not read]")
    return "\n".join(lines)


# -- pptx ---------------------------------------------------------------------------------------

_PPTX_SKIP = frozenset({"pPr", "rPr", "endParaRPr"})


def _slide_paragraph(paragraph: ET.Element) -> str:
    parts: list[str] = []
    for node in _walk(paragraph, _PPTX_SKIP):
        tag = _local(node.tag)
        if tag == "t":
            parts.append(node.text or "")
        elif tag == "br":
            parts.append("\n")
    text = "".join(parts).strip()
    level = _whole_number(_attr(_child(paragraph, "pPr"), "lvl"), _MAX_LEVEL)
    return "  " * (level or 0) + text if text else ""


def _shape_lines(shape: ET.Element, only: frozenset[str] | None = None) -> list[str]:
    """The paragraphs of a shape; a title gets `#`. With `only`, just a shape whose placeholder type is one of those."""
    placeholder = _attr(_child(_child(_child(shape, "nvSpPr"), "nvPr"), "ph"), "type")
    if only is not None and placeholder not in only:
        return []
    body = _child(shape, "txBody")
    lines = [line for line in (_slide_paragraph(item) for item in _children(body, "p")) if line] if body is not None else []
    if lines and placeholder in ("title", "ctrTitle"):
        lines[0] = "# " + lines[0]
    return lines


def _cell_text(cell: ET.Element) -> str:
    paragraphs = (_slide_paragraph(item) for item in _children(_child(cell, "txBody"), "p"))
    return " ".join(" ".join(paragraphs).split())


def _slide_lines(tree: ET.Element, only: frozenset[str] | None = None) -> list[str]:
    """The text of the shapes of a slide (or of its notes) in order, the shapes of groups and the rows of tables included."""
    lines: list[str] = []
    for node in _walk(tree, frozenset({"txBody", "tbl"})):
        tag = _local(node.tag)
        if tag == "sp":
            lines.extend(_shape_lines(node, only))
        elif tag == "tbl" and only is None:
            for row in _children(node, "tr"):
                cells = [_cell_text(cell) for cell in _children(row, "tc")]
                if any(cells):
                    lines.append(" | ".join(cells))
    return lines


def _slides(archive: zipfile.ZipFile, budget: _Budget) -> list[str]:
    """The parts of the slides in the order of the presentation; without a usable list by their file number."""
    presentation = _xml(archive, "ppt/presentation.xml", budget)
    if presentation is None:
        raise UnreadableDocument("The file has no ppt/presentation.xml: it is no PowerPoint presentation.")
    targets = _relationships(archive, budget, "ppt/presentation.xml")
    identifiers = [_qualified(item, "id") for item in _children(_child(presentation, "sldIdLst"), "sldId")]
    found = [targets[identifier][1] for identifier in identifiers if identifier in targets]
    return found or _numbered(archive, "ppt/slides/slide")


def _pptx(archive: zipfile.ZipFile, budget: _Budget) -> str:
    slides = _slides(archive, budget)
    lines: list[str] = []
    for number, part in enumerate(slides[:MAX_PARTS], 1):
        tree = _xml(archive, part, budget)
        if tree is None:
            continue
        _add(lines, budget, f"## Slide {number}", *_slide_lines(tree))
        notes = next((target for kind, target in _relationships(archive, budget, part).values() if kind.endswith("/notesSlide")), "")
        notes_tree = _xml(archive, notes, budget) if notes else None
        spoken = _slide_lines(notes_tree, frozenset({"body"})) if notes_tree is not None else []
        if spoken:
            _add(lines, budget, "Notes:", *spoken)
    if len(slides) > MAX_PARTS:
        lines.append(f"[{len(slides) - MAX_PARTS} more slides are not read]")
    return "\n".join(lines)


def office_text(data: bytes, kind: str) -> str:
    """The text of a docx, xlsx or pptx file (`kind`), as lines: Markdown-like headings and list items for a Word document, tab separated
    rows under `## sheet name` for a workbook, `## Slide n` with the text of its shapes and its notes for a presentation.

    Raises `UnreadableDocument` for a file that is no such document, is damaged, is password protected, or is refused as unsafe. It raises
    nothing else: a file that makes the reader stumble on something that was not foreseen is a file that cannot be read, and the message
    names the kind of the failure and never the content."""
    if kind not in KINDS:
        raise ValueError(f"not an Office Open XML kind: {kind}")
    reader = {"docx": _docx, "xlsx": _xlsx, "pptx": _pptx}[kind]
    try:
        return reader(_open(data), _Budget())
    except UnreadableDocument:
        raise
    except Exception as error:  # noqa: BLE001 - the input is hostile; whatever it breaks is its own fault, and the message must not echo it
        raise UnreadableDocument(f"The document cannot be read: the reader failed on it ({type(error).__name__}).") from None
