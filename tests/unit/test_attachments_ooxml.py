"""Text from Word, Excel and PowerPoint files, and a reader that cannot be hurt by a hostile file: bombs, entities, odd encodings."""
import contextlib
import functools
import io
import struct
import time
import tracemalloc
import unittest
import zipfile
import zlib
from unittest import mock

from jira_dc_cli.domain import ooxml
from jira_dc_cli.domain.ooxml import UnreadableDocument, office_text

try:
    import bz2
    import lzma
except ImportError:  # pragma: no cover - a Python built without them
    bz2 = lzma = None

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
S = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
P = 'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
RELS = 'xmlns="http://schemas.openxmlformats.org/package/2006/relationships"'
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"


def zipped(parts: dict, compression: int = zipfile.ZIP_DEFLATED) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression) as archive:
        for name, content in parts.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def docx(body: str) -> bytes:
    return zipped({"word/document.xml": f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document {W}><w:body>{body}</w:body></w:document>'})


def para(text: str, style: str = "", numbered: int | None = None) -> str:
    properties = ""
    if style:
        properties += f'<w:pStyle w:val="{style}"/>'
    if numbered is not None:
        properties += f'<w:numPr><w:ilvl w:val="{numbered}"/><w:numId w:val="1"/></w:numPr>'
    return f"<w:p><w:pPr>{properties}</w:pPr><w:r><w:t xml:space=\"preserve\">{text}</w:t></w:r></w:p>"


def sheet(rows: str) -> str:
    return f"<worksheet {S}><sheetData>{rows}</sheetData></worksheet>"


def workbook(sheets: dict, strings: list | None = None, with_workbook_rels: bool = True) -> bytes:
    names = list(sheets)
    parts = {
        "xl/workbook.xml": f"<workbook {S}><sheets>" + "".join(f'<sheet name="{name}" sheetId="{n}" r:id="rId{n}"/>' for n, name in enumerate(names, 1)) + "</sheets></workbook>"
    }
    if with_workbook_rels:
        parts["xl/_rels/workbook.xml.rels"] = f"<Relationships {RELS}>" + "".join(
            f'<Relationship Id="rId{n}" Type="{REL}worksheet" Target="{"/xl/" if n % 2 == 0 else ""}worksheets/sheet{n}.xml"/>' for n in range(1, len(names) + 1)
        ) + "</Relationships>"
    for n, name in enumerate(names, 1):
        parts[f"xl/worksheets/sheet{n}.xml"] = sheets[name]
    if strings is not None:
        parts["xl/sharedStrings.xml"] = f"<sst {S}>" + "".join(strings) + "</sst>"
    return zipped(parts)


def shape(text: str, placeholder: str = "") -> str:
    ph = f'<p:ph type="{placeholder}"/>' if placeholder else ""
    return f'<p:sp><p:nvSpPr><p:cNvPr id="2" name="s"/><p:cNvSpPr/><p:nvPr>{ph}</p:nvPr></p:nvSpPr><p:txBody><a:bodyPr/>{text}</p:txBody></p:sp>'


def line(text: str, level: int | None = None) -> str:
    properties = f'<a:pPr lvl="{level}"/>' if level is not None else ""
    return f"<a:p>{properties}<a:r><a:rPr/><a:t>{text}</a:t></a:r></a:p>"


def slide(content: str) -> str:
    return f"<p:sld {P}><p:cSld><p:spTree>{content}</p:spTree></p:cSld></p:sld>"


def a_deck() -> bytes:
    """A presentation of two slides, the first with a title, a list, a group, a table and a notes page."""
    cell = lambda text: f"<a:tc><p:txBody>{line(text)}</p:txBody></a:tc>"  # noqa: E731
    table = f"<p:graphicFrame><a:graphic><a:graphicData><a:tbl><a:tr>{cell('c1')}{cell('c2')}</a:tr><a:tr><a:tc/></a:tr></a:tbl></a:graphicData></a:graphic></p:graphicFrame>"
    first = slide(shape(line("Slide title"), "title") + shape(line("point") + line("sub point", 1)) + "<p:grpSp>" + shape(line("grouped")) + "</p:grpSp>" + table)
    second = slide(shape(line("Second"), "ctrTitle"))
    notes = slide(shape("", "sldImg") + shape(line("Say hello."), "body") + shape(line("7"), "sldNum"))
    return zipped(
        {
            "ppt/presentation.xml": f'<p:presentation {P}><p:sldIdLst><p:sldId id="257" r:id="rId2"/><p:sldId id="256" r:id="rId1"/></p:sldIdLst></p:presentation>',
            "ppt/_rels/presentation.xml.rels": f'<Relationships {RELS}><Relationship Id="rId1" Type="{REL}slide" Target="slides/slide1.xml"/><Relationship Id="rId2" Type="{REL}slide" Target="slides/slide2.xml"/></Relationships>',
            "ppt/slides/slide1.xml": first,
            "ppt/slides/slide2.xml": second,
            "ppt/slides/_rels/slide1.xml.rels": f'<Relationships {RELS}><Relationship Id="rId1" Type="{REL}notesSlide" Target="../notesSlides/notesSlide1.xml"/></Relationships>',
            "ppt/notesSlides/notesSlide1.xml": notes,
        }
    )


def repacked(data: bytes, methods: dict | None = None, default: int = zipfile.ZIP_DEFLATED, changed: dict | None = None) -> bytes:
    """The same archive, every part packed the way `methods` says (by the name of the part) and the others the way `default` says, with
    the content of the parts in `changed` replaced, or added when the archive has no such part."""
    source = zipfile.ZipFile(io.BytesIO(data))
    contents = {info.filename: source.read(info) for info in source.infolist()}
    contents.update(changed or {})
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as target:
        for name, content in contents.items():
            target.writestr(name, content, compress_type=(methods or {}).get(name, default))
    return buffer.getvalue()


def claiming(data: bytes, size: int, only: str | None = None) -> bytes:
    """The archive with the size that a part declares changed to `size`, in its local header and in the directory: what a hostile file
    does so that a check of the declared size lets it through. Every part, or just the one that is named."""
    archive = zipfile.ZipFile(io.BytesIO(data))
    patched = bytearray(data)
    in_directory = archive.start_dir
    for info in archive.infolist():
        if only is None or info.filename == only:
            struct.pack_into("<I", patched, info.header_offset + 22, size)
            struct.pack_into("<I", patched, in_directory + 24, size)
        in_directory += 46 + len(info.filename.encode("utf-8")) + len(info.extra) + len(info.comment)
    return bytes(patched)


BOMB_PART = "xl/worksheets/sheet1.xml"


@functools.lru_cache(maxsize=None)
def bomb(method: int) -> bytes:
    """A real one: a workbook whose sheet is 16 MiB of one letter, packed with `method` to a few kilobytes, and whose headers say 1,000 bytes."""
    packed = repacked(workbook({"S": sheet("")}), {BOMB_PART: method}, changed={BOMB_PART: b"A" * (16 << 20)})
    return claiming(packed, 1000, BOMB_PART)


def with_comment(data: bytes, comment: bytes) -> bytes:
    """An archive that has no comment, given one: its length is a field of the end record, and the text follows the record."""
    patched = bytearray(data)
    struct.pack_into("<H", patched, patched.rindex(b"PK\x05\x06") + 20, len(comment))
    return bytes(patched) + comment


def zip64_by_hand(declared: int, real: int, comment: int) -> bytes:
    """A ZIP64 archive the way a hostile file makes it: the end record says one entry and a small directory, the ZIP64 record says
    `declared` entries, and a comment of `comment` bytes follows. The directory holds `real` copies of one entry, so that a claim of
    400,000 entries costs no memory here (`zipfile` would build 400,000 objects for it)."""
    name = b"word/document.xml"
    body = f"<w:document {W}><w:body>{para('hello')}</w:body></w:document>".encode()
    packer = zlib.compressobj(9, zlib.DEFLATED, -15)
    packed = packer.compress(body) + packer.flush()
    crc = zlib.crc32(body)
    local = struct.pack("<IHHHHHIIIHH", 0x04034B50, 20, 0, 8, 0, 0x21, crc, len(packed), len(body), len(name), 0) + name + packed
    entry = struct.pack("<IHHHHHHIIIHHHHHII", 0x02014B50, 20, 20, 0, 8, 0, 0x21, crc, len(packed), len(body), len(name), 0, 0, 0, 0, 0, 0) + name
    directory = entry * real
    record = struct.pack("<IQHHIIQQQQ", 0x06064B50, 44, 45, 45, 0, 0, declared, declared, len(entry) * declared, len(local))
    locator = struct.pack("<IIQI", 0x07064B50, 0, len(local) + len(directory), 1)
    end = struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, 1, 1, len(entry), len(local), comment)
    return local + directory + record + locator + end + b"c" * comment


class Invoked(BaseException):
    """Raised by what must not be used. Not an `Exception`: the catch-all of the reader could hide that."""


@contextlib.contextmanager
def no_decompressor():
    """While it is active, creating the decompressor of a bzip2 or LZMA part raises `Invoked`: `zipfile` creates one when it opens such a part."""
    with contextlib.ExitStack() as stack:
        for owner, name in ((bz2, "BZ2Decompressor"), (zipfile, "LZMADecompressor"), (lzma, "LZMADecompressor")):
            stack.enter_context(mock.patch.object(owner, name, side_effect=Invoked(f"{owner.__name__}.{name} was created")))
        yield


class WordTest(unittest.TestCase):
    def test_headings_lists_runs_tabs_breaks_and_tracked_changes(self):
        body = (
            para("Report", "Heading1")
            + para("Sub part", "Heading2")
            + "<w:p><w:r><w:t>Hello </w:t></w:r><w:r><w:t>world</w:t></w:r></w:p>"
            + para("first", numbered=0)
            + para("nested", numbered=1)
            + "<w:p><w:r><w:t>tab</w:t><w:tab/><w:t>bed</w:t><w:br/><w:t>next</w:t></w:r></w:p>"
            + "<w:p><w:del><w:r><w:delText>gone</w:delText></w:r></w:del><w:ins><w:r><w:t>added</w:t></w:r></w:ins></w:p>"
            + "<w:p><w:r><w:instrText>HYPERLINK x</w:instrText></w:r><w:hyperlink><w:r><w:t>a link</w:t></w:r></w:hyperlink></w:p>"
            + para("")
            + para("The title", "Title")
        )
        self.assertEqual(
            office_text(docx(body), "docx"),
            "# Report\n## Sub part\nHello world\n- first\n  - nested\ntab\tbed\nnext\nadded\na link\n# The title",
        )

    def test_tables_are_rows_with_pipes_and_content_controls_are_looked_into(self):
        table = "<w:tbl><w:tr><w:tc>" + para("A1") + "</w:tc><w:tc>" + para("B1") + para("B1 second") + "</w:tc></w:tr><w:tr><w:tc>" + para("A2") + "</w:tc><w:tc/></w:tr></w:tbl>"
        nested = "<w:tbl><w:tr><w:tc>" + para("outer") + "<w:tbl><w:tr><w:tc>" + para("inner") + "</w:tc></w:tr></w:tbl></w:tc></w:tr></w:tbl>"
        controlled = "<w:sdt><w:sdtContent>" + para("inside a control") + "</w:sdtContent></w:sdt>"
        self.assertEqual(office_text(docx(table + nested + controlled), "docx"), "A1 | B1 B1 second\nA2 | \nouter inner\ninside a control")

    def test_the_second_copy_of_a_text_box_is_not_read_twice(self):
        box = '<w:p><w:r><w:t>before</w:t><mc:AlternateContent xmlns:mc="urn:mc"><mc:Choice><w:txbxContent><w:p><w:r><w:t>boxed</w:t></w:r></w:p></w:txbxContent></mc:Choice><mc:Fallback><w:txbxContent><w:p><w:r><w:t>boxed</w:t></w:r></w:p></w:txbxContent></mc:Fallback></mc:AlternateContent></w:r></w:p>'
        self.assertEqual(office_text(docx(box), "docx").count("boxed"), 1)

    def test_a_document_without_text_is_empty_text(self):
        self.assertEqual(office_text(docx(""), "docx"), "")

    def test_the_namespace_of_the_strict_variant_does_not_matter(self):
        strict = zipped({"word/document.xml": '<d:document xmlns:d="http://purl.oclc.org/ooxml/wordprocessingml/main"><d:body><d:p><d:r><d:t>strict</d:t></d:r></d:p></d:body></d:document>'})
        self.assertEqual(office_text(strict, "docx"), "strict")

    def test_a_very_deep_nesting_is_no_problem(self):
        depth = 20000
        xml = "<w:sdt><w:sdtContent>" * depth + para("deep") + "</w:sdtContent></w:sdt>" * depth
        self.assertEqual(office_text(docx(xml), "docx"), "deep")


class ExcelTest(unittest.TestCase):
    def setUp(self):
        self.strings = ["<si><t>Name</t></si>", "<si><r><t>Rich</t></r><r><t> text</t></r><rPh><t>phonetic</t></rPh></si>", "<si><t>Amount</t></si>"]

    def test_rows_cells_of_every_type_and_gaps(self):
        first = sheet(
            '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="C1" t="s"><v>2</v></c></row>'
            '<row r="2"><c r="A2" t="s"><v>1</v></c><c r="B2"><v>12.5</v></c><c r="C2" t="b"><v>1</v></c><c r="D2" t="inlineStr"><is><t>inline</t></is></c>'
            '<c r="E2" t="str"><f>A1</f><v>formula text</v></c><c r="F2" t="e"><v>#DIV/0!</v></c></row>'
            '<row r="3"/><row r="4"><c r="B4"><v>1</v></c><c r="Z4"><v>far</v></c><c r="A5" t="s"><v>99</v></c></row>'
        )
        second = sheet('<row r="1"><c t="s"><v>0</v></c><c><v>7</v></c></row>')
        text = office_text(workbook({"Data": first, "Notes": second}, self.strings), "xlsx")
        self.assertEqual(
            text.split("\n"),
            ["## Data", "Name\t\tAmount", "Rich text\t12.5\tTRUE\tinline\tformula text\t#DIV/0!", "\t1" + "\t" * 9 + "far", "## Notes", "Name\t7"],
        )  # a gap of 23 columns is shown as 8 empty cells, and a cell without a text leaves the row out

    def test_a_tab_or_line_break_inside_a_cell_does_not_break_the_row(self):
        cells = sheet('<row><c t="inlineStr"><is><t>two\nlines\tand tab</t></is></c><c t="inlineStr"><is><t>next</t></is></c></row>')
        self.assertEqual(office_text(workbook({"S": cells}), "xlsx"), "## S\ntwo lines and tab\tnext")

    def test_the_order_of_the_workbook_counts_and_without_relationships_the_file_numbers(self):
        a, b = sheet('<row><c t="inlineStr"><is><t>a</t></is></c></row>'), sheet('<row><c t="inlineStr"><is><t>b</t></is></c></row>')
        self.assertEqual(office_text(workbook({"First": a, "Second": b}), "xlsx"), "## First\na\n## Second\nb")
        self.assertEqual(office_text(workbook({"First": a, "Second": b}, with_workbook_rels=False), "xlsx"), "## Sheet1\na\n## Sheet2\nb")

    def test_a_workbook_without_a_workbook_part_is_refused(self):
        with self.assertRaises(UnreadableDocument) as caught:
            office_text(zipped({"xl/worksheets/sheet1.xml": sheet("")}), "xlsx")
        self.assertIn("xl/workbook.xml", caught.exception.message)

    def test_a_large_sheet_is_read_row_by_row(self):
        rows = "".join(f'<row r="{n}"><c r="A{n}"><v>{n}</v></c></row>' for n in range(1, 30001))
        text = office_text(workbook({"Big": sheet(rows)}), "xlsx")
        self.assertEqual((text.count("\n"), text.split("\n")[-1]), (30000, "30000"))

    def test_more_sheets_than_are_read_are_said(self):
        with mock.patch.object(ooxml, "MAX_PARTS", 2):
            one = sheet('<row><c t="inlineStr"><is><t>x</t></is></c></row>')
            text = office_text(workbook({"A": one, "B": one, "C": one, "D": one}), "xlsx")
        self.assertEqual(text.split("\n")[-1], "[2 more sheets are not read]")


class PowerPointTest(unittest.TestCase):
    def deck(self) -> bytes:
        return a_deck()

    def test_slides_in_the_order_of_the_presentation_with_titles_tables_and_notes(self):
        self.assertEqual(
            office_text(self.deck(), "pptx").split("\n"),
            ["## Slide 1", "# Second", "## Slide 2", "# Slide title", "point", "  sub point", "grouped", "c1 | c2", "Notes:", "Say hello."],
        )

    def test_without_a_slide_list_the_file_numbers_decide(self):
        parts = {"ppt/presentation.xml": f"<p:presentation {P}/>", "ppt/slides/slide10.xml": slide(shape(line("ten"))), "ppt/slides/slide2.xml": slide(shape(line("two")))}
        self.assertEqual(office_text(zipped(parts), "pptx"), "## Slide 1\ntwo\n## Slide 2\nten")

    def test_a_presentation_without_its_part_is_refused(self):
        with self.assertRaises(UnreadableDocument):
            office_text(zipped({"ppt/slides/slide1.xml": slide("")}), "pptx")


class HostileFileTest(unittest.TestCase):
    def refused(self, data: bytes, kind: str, *words: str) -> str:
        started = time.monotonic()
        with self.assertRaises(UnreadableDocument) as caught:
            office_text(data, kind)
        self.assertLess(time.monotonic() - started, 5, "a refusal must be quick")
        for word in words:
            self.assertIn(word, caught.exception.message)
        return caught.exception.message

    def test_a_document_type_is_refused_before_anything_expands(self):
        laughs = '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;"><!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">]>'
        document = f'{laughs}<w:document {W}><w:body><w:p><w:r><w:t>&lol3;</w:t></w:r></w:p></w:body></w:document>'
        self.refused(zipped({"word/document.xml": document}), "docx", "document type")
        self.refused(workbook({"S": laughs + sheet("")}), "xlsx", "document type")

    def test_an_entity_declaration_is_refused_wherever_it_hides(self):
        hidden = f'<w:document {W}><!-- <!ENTITY x "y"> --><w:body/></w:document>'
        self.refused(zipped({"word/document.xml": hidden}), "docx", "entity")
        self.refused(zipped({"word/document.xml": hidden.replace("<!ENTITY", "<!entity").replace("-- <", "-- <!DocType")}), "docx", "document type")

    def test_xml_that_is_not_utf8_is_refused_because_a_filter_on_bytes_cannot_see_into_it(self):
        text = f'<?xml version="1.0" encoding="UTF-16"?><w:document {W}><w:body/></w:document>'
        self.refused(zipped({"word/document.xml": text.encode("utf-16")}), "docx", "not UTF-8")
        self.refused(zipped({"word/document.xml": text.replace("UTF-16", "ebcdic-cp-us").encode("ascii")}), "docx", "not UTF-8")
        # the way round a filter: an ASCII declaration of an encoding, then a body in that encoding, where `<!DOCTYPE` has other bytes
        evil = f'<!DOCTYPE d [<!ENTITY x "y">]><w:document {W}><w:body/></w:document>'
        sneaky = b'<?xml version="1.0" encoding="cp037"?>' + evil.encode("cp037")
        self.assertNotIn(b"<!DOCTYPE", sneaky)
        self.refused(zipped({"word/document.xml": sneaky}), "docx", "cp037", "not UTF-8")
        self.refused(zipped({"word/document.xml": text.replace("UTF-16", "cp037").encode("cp037")}), "docx")  # all of it in that encoding: expat cannot read it either
        for declared in ("UTF-8", "utf-8", "utf8"):
            xml = f'<?xml version="1.0" encoding="{declared}"?><w:document {W}><w:body>{para("fine")}</w:body></w:document>'
            self.assertEqual(office_text(zipped({"word/document.xml": xml}), "docx"), "fine")
        with_bom = b"\xef\xbb\xbf" + f'<?xml version="1.0" encoding="UTF-8"?><w:document {W}><w:body>{para("bom")}</w:body></w:document>'.encode("utf-8")
        self.assertEqual(office_text(zipped({"word/document.xml": with_bom}), "docx"), "bom")
        wrong_after_bom = b"\xef\xbb\xbf" + b'<?xml version="1.0" encoding="cp037"?><d/>'
        self.refused(zipped({"word/document.xml": wrong_after_bom}), "docx", "not UTF-8")

    def test_a_part_that_unpacks_to_a_lot_is_refused_by_what_the_archive_declares(self):
        bomb = zipped({"word/document.xml": b"A" * 60_000_000})
        self.assertLess(len(bomb), 100_000)
        with mock.patch.object(zipfile.ZipFile, "open", side_effect=AssertionError("a byte was unpacked")):
            self.refused(bomb, "docx", "word/document.xml", "bomb")  # refused before anything is unpacked

    def test_a_part_is_never_read_without_a_bound(self):
        # This replaces the stream that `zipfile` returns, so it proves what is asked of it. How `zipfile` itself behaves is proved with
        # real archives in `PackingTest`: a bzip2 or LZMA member ignores the bound and is therefore refused before it is opened.
        asked = []

        class Unbounded:
            """A stream that does not stop at the size that the archive declares, as an implementation of `zipfile` might not."""

            def __enter__(self):
                return self

            def __exit__(self, *error):
                return False

            def read(self, size=-1):
                asked.append(size)
                return b"A" * (size if size >= 0 else 50_000_000)

        data = docx(para("x"))  # made before `open` is replaced: writing an archive uses it too
        with mock.patch.object(zipfile.ZipFile, "open", return_value=Unbounded()):
            self.refused(data, "docx", "bomb")
        self.assertEqual(asked, [ooxml.MAX_PART_BYTES + 1])  # one more than the limit, so that too much can be told from enough

    def test_a_part_that_lies_about_its_size_is_still_stopped(self):
        data = bytearray(zipped({"word/document.xml": b"B" * 3_000_000}))
        with mock.patch.object(ooxml, "MAX_PART_BYTES", 1_000_000):
            honest = bytes(data)
            self.refused(honest, "docx", "bomb")
            at = data.index(b"PK\x01\x02")
            struct.pack_into("<I", data, at + 24, 100)  # the directory now says 100 bytes: the reader may not trust it
            with self.assertRaises(UnreadableDocument):
                office_text(bytes(data), "docx")

    def test_all_the_parts_together_are_limited_too(self):
        one = sheet('<row><c t="inlineStr"><is><t>' + "x" * 3000 + "</t></is></c></row>")
        data = workbook({f"S{n}": one for n in range(1, 8)})
        with mock.patch.object(ooxml, "MAX_TOTAL_BYTES", 10_000):
            self.refused(data, "xlsx", "more than")

    def test_too_many_entries_are_refused_before_the_archive_is_opened(self):
        many = zipped({f"f{n}": b"" for n in range(ooxml.MAX_ENTRIES + 5)})
        with mock.patch.object(zipfile, "ZipFile", side_effect=AssertionError("the archive was opened")):
            self.refused(many, "docx", "more than")

    def test_the_numbers_in_the_end_record_are_checked_as_they_stand_there(self):
        for offset, packed in ((10, struct.pack("<H", 0xFFFF)), (12, struct.pack("<I", 0xFFFFFFFF)), (12, struct.pack("<I", 5_000_000))):
            data = bytearray(docx(para("x")))
            end = data.rindex(b"PK\x05\x06")
            data[end + offset : end + offset + len(packed)] = packed
            with self.subTest(offset=offset, value=packed):
                with mock.patch.object(zipfile, "ZipFile", side_effect=AssertionError("the archive was opened")):
                    self.refused(bytes(data), "docx", "more than")

    def test_what_is_no_zip_is_refused_with_a_reason(self):
        self.refused(b"", "docx", "ZIP")
        self.refused(b"PK\x03\x04 not really", "docx", "ZIP")
        self.refused(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 600, "xlsx", "ZIP")  # the header of an old binary Office file or a password protected one

    def test_a_password_protected_part_is_refused(self):
        data = bytearray(docx(para("secret")))
        local, central = data.index(b"PK\x03\x04"), data.index(b"PK\x01\x02")
        data[local + 6] |= 1
        data[central + 8] |= 1  # the flag of encryption in both headers
        with mock.patch.object(zipfile.ZipFile, "open", side_effect=Invoked("an encrypted part was opened")):  # the refusal is not left to `zipfile`
            message = self.refused(bytes(data), "docx", "word/document.xml")
        self.assertIn("password", message)

    def test_malformed_xml_missing_parts_and_a_wrong_kind_are_clear_errors(self):
        self.refused(zipped({"word/document.xml": "<w:document"}), "docx", "not well-formed")
        self.refused(zipped({"other.xml": "<x/>"}), "docx", "word/document.xml")
        self.refused(zipped({"word/document.xml": f'<w:document {W}/>'.replace("/>", ">")}), "docx", "not well-formed")
        with self.assertRaises(ValueError):
            office_text(docx(""), "doc")

    def test_a_sheet_that_breaks_in_the_middle_is_refused_not_half_read(self):
        broken = sheet('<row><c t="inlineStr"><is><t>a</t></is></c></row><row><c>')
        self.refused(workbook({"S": broken.replace("</sheetData></worksheet>", "")}), "xlsx", "not well-formed")

    def test_a_part_with_too_many_tags_is_refused(self):
        with mock.patch.object(ooxml, "MAX_MARKUP", 50):
            self.refused(docx(para("x") * 40), "docx", "tags")


def refuse(test: unittest.TestCase, data: bytes, kind: str, *words: str, seconds: float = 5) -> str:
    """The message of the refusal of a file. It must come quickly, whatever reading the file would have cost."""
    started = time.monotonic()
    with test.assertRaises(UnreadableDocument) as caught:
        office_text(data, kind)
    test.assertLess(time.monotonic() - started, seconds, "a refusal must be quick")
    for word in words:
        test.assertIn(word, caught.exception.message)
    return caught.exception.message


def strings_workbook(shared: str, cells: int, rows: int = 1) -> bytes:
    """A workbook of one sheet whose cells all point to the one shared string."""
    row = '<c t="s"><v>0</v></c>' * cells
    body = "".join(f'<row r="{n}">{row}</row>' for n in range(1, rows + 1))
    return workbook({"S1": sheet(body)}, [f"<si><t>{shared}</t></si>"])


class ExpansionTest(unittest.TestCase):
    """The text that a document makes is counted as it is made, so that a small file cannot become a huge text: one string that a
    million cells point to is a million copies of it."""

    def test_a_string_that_many_cells_point_to_cannot_multiply_the_text(self):
        data = strings_workbook("A" * 1_000_000, 500)  # 500 million characters if they were all made
        self.assertLess(len(data), 10_000)
        tracemalloc.start()
        try:
            message = refuse(self, data, "xlsx", "expands to more text than can be read")
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        self.assertLess(peak, 150 * 1024 * 1024, "the text must not be made before it is refused")
        self.assertNotIn("A" * 20, message)

    def test_the_text_is_counted_cell_by_cell_before_the_row_is_put_together(self):
        original = ooxml._Budget.emit
        with mock.patch.object(ooxml, "MAX_TEXT_CHARS", 10_000):
            with mock.patch.object(ooxml._Budget, "emit", autospec=True, side_effect=original) as emit:
                refuse(self, strings_workbook("x" * 100, 5000), "xlsx", "expands to more text")  # one row of 5000 cells of 100 characters
        self.assertLess(emit.call_count, 120)  # it stopped at about the hundredth cell, it did not make the row of 500,000 characters first

    def test_a_cell_and_a_shared_string_are_cut_at_the_length_that_excel_allows(self):
        limit = 32_767
        cut = "y" * (limit - 1) + "\u2026"
        for label, text, expected in (("at the limit", "y" * limit, "y" * limit), ("over", "y" * (limit + 1), cut), ("far over", "y" * 5_000_000, cut)):
            with self.subTest(label, via="a shared string"):
                self.assertEqual(office_text(strings_workbook(text, 1), "xlsx"), "## S1\n" + expected)
            with self.subTest(label, via="an inline string"):
                cell = f'<row><c t="inlineStr"><is><t>{text}</t></is></c></row>'
                self.assertEqual(office_text(workbook({"S1": sheet(cell)}), "xlsx"), "## S1\n" + expected)

    def test_a_shared_string_is_made_one_line_once_and_every_cell_that_points_to_it_gets_that_line(self):
        data = strings_workbook("two\nlines\t  and   blanks", 3)
        self.assertEqual(office_text(data, "xlsx"), "## S1\n" + "\t".join(["two lines and blanks"] * 3))

    def test_word_and_powerpoint_text_is_counted_too(self):
        deck = zipped(
            {"ppt/presentation.xml": f"<p:presentation {P}/>", "ppt/slides/slide1.xml": slide(shape("".join(line("p" * 40) for _ in range(20))))}
        )
        table = "<w:tbl>" + ("<w:tr><w:tc>" + para("c" * 40) + "</w:tc></w:tr>") * 20 + "</w:tbl>"
        with mock.patch.object(ooxml, "MAX_TEXT_CHARS", 500):
            refuse(self, docx(para("w" * 40) * 20), "docx", "expands to more text")
            refuse(self, deck, "pptx", "expands to more text")
            refuse(self, docx(table), "docx", "expands to more text")
            self.assertEqual(office_text(docx(para("w" * 40) * 5), "docx").count("w"), 200)  # 205 characters with the line ends: under the budget

    def test_the_budget_is_a_few_million_characters_and_an_ordinary_large_sheet_is_read(self):
        self.assertTrue(1_000_000 <= ooxml.MAX_TEXT_CHARS <= 10_000_000)
        rows = "".join(
            f'<row r="{n}"><c r="A{n}"><v>{n}</v></c><c r="B{n}" t="inlineStr"><is><t>some text {n}</t></is></c></row>' for n in range(1, 40001)
        )
        started = time.monotonic()
        text = office_text(workbook({"Big": sheet(rows)}), "xlsx")
        self.assertEqual((text.count("\n"), text.split("\n")[-1]), (40000, "40000\tsome text 40000"))
        self.assertLess(time.monotonic() - started, 10)


def with_root(attributes: str = "", inner: str = "") -> bytes:
    return zipped({"word/document.xml": f"<w:document {W} {attributes}><w:body>{inner}</w:body></w:document>"})


WORD_NAMESPACES = {  # the namespaces that the root element of a document made by Word declares
    "wpc": "http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas",
    "cx": "http://schemas.microsoft.com/office/drawing/2014/chartex",
    "cx1": "http://schemas.microsoft.com/office/drawing/2015/9/8/chartex",
    "cx2": "http://schemas.microsoft.com/office/drawing/2015/10/21/chartex",
    "cx3": "http://schemas.microsoft.com/office/drawing/2016/5/9/chartex",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "aink": "http://schemas.microsoft.com/office/drawing/2016/ink",
    "am3d": "http://schemas.microsoft.com/office/drawing/2017/model3d",
    "o": "urn:schemas-microsoft-com:office:office",
    "oel": "http://schemas.microsoft.com/office/2019/extlst",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
    "v": "urn:schemas-microsoft-com:vml",
    "wp14": "http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "w10": "urn:schemas-microsoft-com:office:word",
    "w14": "http://schemas.microsoft.com/office/word/2010/wordml",
    "w15": "http://schemas.microsoft.com/office/word/2012/wordml",
    "w16cex": "http://schemas.microsoft.com/office/word/2018/wordml/cex",
    "w16cid": "http://schemas.microsoft.com/office/word/2016/wordml/cid",
    "w16": "http://schemas.microsoft.com/office/word/2018/wordml",
    "w16du": "http://schemas.microsoft.com/office/word/2023/wordml/word16du",
    "w16sdtdh": "http://schemas.microsoft.com/office/word/2020/wordml/sdtdatahash",
    "w16se": "http://schemas.microsoft.com/office/word/2015/wordml/symex",
    "wpg": "http://schemas.microsoft.com/office/word/2010/wordprocessingGroup",
    "wpi": "http://schemas.microsoft.com/office/word/2010/wordprocessingInk",
    "wne": "http://schemas.microsoft.com/office/word/2006/wordml",
    "wps": "http://schemas.microsoft.com/office/word/2010/wordprocessingShape",
}


class ExpandingNamesTest(unittest.TestCase):
    """`ElementTree` writes every name as `{address}name`: a long address that many names use costs a copy of it for each of them."""

    def test_a_long_namespace_address_used_by_many_attributes_is_refused_before_it_is_parsed(self):
        address = "http://x/" + "u" * 20_000
        attributes = " ".join(f'x:a{n}=""' for n in range(10_000))
        body = f'<w:document {W} xmlns:x="{address}"><w:body><w:p {attributes}><w:r><w:t>hello</w:t></w:r></w:p></w:body></w:document>'
        data = zipped({"word/document.xml": body})
        self.assertLess(len(data), 50_000)
        with mock.patch.object(ooxml.ET, "fromstring", side_effect=AssertionError("it was parsed")):
            tracemalloc.start()
            try:
                refuse(self, data, "docx", "namespace address")
                peak = tracemalloc.get_traced_memory()[1]
            finally:
                tracemalloc.stop()
        self.assertLess(peak, 50 * 1024 * 1024)  # parsed, it took 670 MB

    def test_the_address_of_a_default_namespace_and_one_declared_on_a_child_count_too(self):
        long = "http://x/" + "u" * 5000
        refuse(self, zipped({"word/document.xml": f'<w:document {W} xmlns="{long}"><w:body/></w:document>'}), "docx", "namespace address")
        refuse(self, zipped({"word/document.xml": f"<w:document {W}><w:body><w:p xmlns:q='{long}'/></w:body></w:document>"}), "docx", "namespace address")
        at_the_limit = "http://x/" + "u" * (200 - len("http://x/"))
        fits = with_root(f'xmlns:q="{at_the_limit}"', para("fine"))
        self.assertEqual(office_text(fits, "docx"), "fine")
        refuse(self, with_root(f'xmlns:q="{at_the_limit}u"', para("fine")), "docx", "namespace address")

    def test_a_long_element_or_attribute_name_is_refused(self):
        name = "n" * 129
        refuse(self, with_root("", f"<w:{name}/>"), "docx", "element name")
        refuse(self, with_root("", f'<w:p w:{name}="1"/>'), "docx", "attribute name")
        longest = "n" * (128 - len("w:"))
        self.assertEqual(office_text(with_root("", f"<w:{longest}/>" + para("ok")), "docx"), "ok")

    def test_an_element_with_hundreds_of_attributes_is_refused_and_a_real_one_is_not(self):
        many = " ".join(f'w:a{n}="1"' for n in range(257))
        refuse(self, with_root("", f"<w:p {many}/>"), "docx", "more than 256 attributes")
        fine = " ".join(f'w:a{n}="1"' for n in range(200))
        self.assertEqual(office_text(with_root("", f"<w:p {fine}/>" + para("ok")), "docx"), "ok")

    def test_the_attributes_of_a_whole_part_are_counted(self):
        with mock.patch.object(ooxml, "MAX_ATTRIBUTES", 100):
            refuse(self, with_root("", '<w:p w:a="1"/>' * 150), "docx", "100 attributes")
            self.assertEqual(office_text(with_root("", '<w:p w:a="1"/>' * 50 + para("ok")), "docx"), "ok")

    def test_the_limits_are_the_ones_that_the_guide_names(self):
        self.assertEqual(
            (ooxml.MAX_NAME_CHARS, ooxml.MAX_NAMESPACE_CHARS, ooxml.MAX_ATTRIBUTES_PER_ELEMENT, ooxml.MAX_CELL_CHARS, ooxml.MAX_COLUMNS),
            (128, 200, 256, 32_767, 16_384),
        )
        self.assertTrue(1_000_000 <= ooxml.MAX_TEXT_CHARS <= 10_000_000)
        self.assertEqual((ooxml._MAX_LEVEL, ooxml._MAX_DIGITS), (8, 9))
        self.assertEqual((ooxml.MAX_PART_BYTES, ooxml.MAX_TOTAL_BYTES, ooxml.MAX_MARKUP), (6 * 1024 * 1024, 32 * 1024 * 1024, 1_000_000))
        self.assertEqual((ooxml.MAX_ENTRIES, ooxml.MAX_PARTS), (2000, 400))

    def test_the_namespaces_that_word_writes_are_no_problem(self):
        self.assertLess(max(len(address) for address in WORD_NAMESPACES.values()), 100)
        declared = " ".join(f'xmlns:{prefix}="{address}"' for prefix, address in WORD_NAMESPACES.items())
        root = declared + ' mc:Ignorable="w14 w15 w16se w16cid w16 w16cex w16sdtdh wp14"'
        self.assertEqual(office_text(with_root(root, para("as Word writes it")), "docx"), "as Word writes it")

    def test_a_sheet_part_is_checked_like_every_other_part(self):
        long = "http://x/" + "u" * 5000
        cells = '<row><c t="inlineStr"><is><t>a</t></is></c></row>'
        refuse(self, workbook({"S": f"<worksheet {S} xmlns:q=\"{long}\"><sheetData>{cells}</sheetData></worksheet>"}), "xlsx", "namespace address")


class ColumnAndNumberTest(unittest.TestCase):
    def test_a_column_is_at_most_three_letters_and_at_most_xfd(self):
        cases = {
            "A1": 0, "a1": 0, "Z9": 25, "AA1": 26, "AB12": 27, "XFD1": 16383, "XFD1048576": 16383,
            "XFE1": None, "ZZZ1": None, "AAAA1": None, "A": None, "1": None, "": None, "A1B": None, "A-1": None, "\u0410\u04101": None,
        }
        for reference, column in cases.items():
            with self.subTest(reference=reference):
                self.assertEqual(ooxml._column(reference), column)
        started = time.monotonic()
        self.assertIsNone(ooxml._column("A" * 100_000 + "1"))
        self.assertLess(time.monotonic() - started, 1)

    def test_a_cell_with_a_reference_of_a_hundred_thousand_letters_is_read_in_its_place(self):
        cells = f'<row><c r="{"A" * 100_000}1" t="inlineStr"><is><t>first</t></is></c><c t="inlineStr"><is><t>second</t></is></c></row>'
        started = time.monotonic()
        self.assertEqual(office_text(workbook({"S": sheet(cells)}), "xlsx"), "## S\nfirst\tsecond")
        self.assertLess(time.monotonic() - started, 5)

    def test_a_number_in_the_xml_is_read_with_a_bound(self):
        cases = [
            ("5", 8, 5), ("  7 ", 8, 7), ("0", 8, 0), ("", 8, None), ("-1", 8, None), ("1.5", 8, None), ("\u00b2", 8, None), ("\u0663", 8, None),
            ("\u0661\u0662", 8, None), ("100000000", 8, 8), ("9" * 5000, 8, 8), ("0" * 5000 + "3", 8, 3), ("12", 8, 8), ("12", 100, 12),
        ]
        for text, ceiling, expected in cases:
            with self.subTest(text=text[:12], ceiling=ceiling):
                self.assertEqual(ooxml._whole_number(text, ceiling), expected)

    def test_a_level_that_is_no_number_or_a_huge_one_is_no_crash_and_no_wide_indent(self):
        for level, indent in (("\u00b2", 0), ("9" * 5000, 8), ("100000000", 8), ("-3", 0), ("3", 3), ("", 0)):
            with self.subTest(level=level[:10]):
                body = f'<w:p><w:pPr><w:numPr><w:ilvl w:val="{level}"/><w:numId w:val="1"/></w:numPr></w:pPr><w:r><w:t>item</w:t></w:r></w:p>'
                self.assertEqual(office_text(docx(body), "docx"), "  " * indent + "- item")
                deck = zipped(
                    {"ppt/presentation.xml": f"<p:presentation {P}/>", "ppt/slides/slide1.xml": slide(shape(line("text", level)))}  # type: ignore[arg-type]
                )
                self.assertEqual(office_text(deck, "pptx"), "## Slide 1\n" + "  " * indent + "text")

    def test_a_shared_string_index_that_is_no_number_is_an_empty_cell(self):
        for index in ("\u00b2", "9" * 5000, "-1", "1.5", "99999999999", " "):
            with self.subTest(index=index[:10]):
                cells = f'<row><c t="s"><v>{index}</v></c><c t="inlineStr"><is><t>after</t></is></c></row>'
                self.assertEqual(office_text(workbook({"S": sheet(cells)}, ["<si><t>zero</t></si>"]), "xlsx"), "## S\n\tafter")

    def test_a_part_with_a_name_that_is_no_number_is_not_a_sheet(self):
        fake = sheet('<row><c t="inlineStr"><is><t>fake</t></is></c></row>')
        names = ["xl/worksheets/sheet" + "9" * 5000 + ".xml", "xl/worksheets/sheet\u00b2.xml", "xl/worksheets/sheet\u0663.xml", "xl/worksheets/sheet" + "1" * 10 + ".xml"]
        for name in names:
            with self.subTest(name=name[-14:]):
                data = zipped(
                    {
                        "xl/workbook.xml": f"<workbook {S}><sheets/></workbook>",
                        "xl/worksheets/sheet1.xml": sheet('<row><c t="inlineStr"><is><t>real</t></is></c></row>'),
                        name: fake,
                    }
                )
                self.assertEqual(office_text(data, "xlsx"), "## Sheet1\nreal")

    def test_whatever_the_reader_stumbles_on_is_an_unreadable_document_that_names_the_class_only(self):
        for failure in (RuntimeError, MemoryError, RecursionError, ValueError, KeyError, ZeroDivisionError):
            with self.subTest(failure=failure.__name__):
                with mock.patch.object(ooxml, "_docx", side_effect=failure("secret words from the file")):
                    with self.assertRaises(UnreadableDocument) as caught:
                        office_text(docx(para("x")), "docx")
                self.assertIn(failure.__name__, caught.exception.message)
                self.assertNotIn("secret", caught.exception.message)
                self.assertIsNone(caught.exception.__cause__)  # nothing is chained that a traceback could print

    def test_a_damaged_deflate_part_is_an_unreadable_document(self):
        body = f'<w:document {W}><w:body><w:p><w:r><w:t>{"hello world " * 200}</w:t></w:r></w:p></w:body></w:document>'
        data = zipped({"word/document.xml": body})
        self.assertEqual(office_text(data, "docx").count("hello"), 200)
        damaged = bytearray(data)
        start = damaged.index(b"document.xml") + len("document.xml")
        for position in range(start + 20, start + 60):
            damaged[position] ^= 0xFF
        refuse(self, bytes(damaged), "docx", "word/document.xml", "cannot be unpacked")
        refuse(self, data[: len(data) // 2], "docx", "ZIP")  # cut in the middle: the end of the archive is gone

    def test_numid_zero_is_no_list_item_and_every_other_value_is(self):
        def item(num_id: str) -> bytes:
            return docx(f'<w:p><w:pPr><w:numPr><w:ilvl w:val="1"/>{num_id}</w:numPr></w:pPr><w:r><w:t>text</w:t></w:r></w:p>')

        cases = {
            '<w:numId w:val="0"/>': "text", '<w:numId w:val="00"/>': "text", '<w:numId w:val="1"/>': "  - text", '<w:numId w:val="12"/>': "  - text",
            "": "  - text", '<w:numId w:val=""/>': "  - text", '<w:numId w:val="\u00b2"/>': "  - text",
        }
        for tag, expected in cases.items():
            with self.subTest(tag=tag):
                self.assertEqual(office_text(item(tag), "docx"), expected)


class Zip64Test(unittest.TestCase):
    """A ZIP64 archive is refused: its end record says one thing and the ZIP64 record another, and `zipfile` believes the second."""

    def archive(self, comment: bytes = b"") -> bytes:
        buffer = io.BytesIO()
        with mock.patch.object(zipfile, "ZIP_FILECOUNT_LIMIT", 1):  # three entries are then too many for the end record: a real ZIP64 archive
            with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
                archive.writestr("word/document.xml", f"<w:document {W}><w:body>{para('x')}</w:body></w:document>")
                archive.writestr("a.txt", "a")
                archive.writestr("b.txt", "b")
                archive.comment = comment
        return buffer.getvalue()

    def test_such_an_archive_is_valid_and_is_refused_before_it_is_opened(self):
        data = self.archive()
        self.assertEqual(len(zipfile.ZipFile(io.BytesIO(data)).namelist()), 3)
        with mock.patch.object(zipfile, "ZipFile", side_effect=AssertionError("it was opened")):
            refuse(self, data, "docx", "ZIP64", "too large for a text read")

    def test_an_end_record_that_says_one_entry_while_the_zip64_record_says_more_is_refused_too(self):
        data = bytearray(self.archive())
        end = data.rindex(b"PK\x05\x06")
        data[end + 8 : end + 12] = struct.pack("<HH", 1, 1)  # the entries on this disk and in all
        data[end + 12 : end + 20] = struct.pack("<II", 10, 0)  # a small directory
        with mock.patch.object(zipfile, "ZipFile", side_effect=AssertionError("it was opened")):
            refuse(self, bytes(data), "docx", "ZIP64")

    def test_an_ordinary_archive_is_no_zip64_archive(self):
        self.assertEqual(office_text(docx(para("ordinary")), "docx"), "ordinary")
        self.assertEqual(office_text(workbook({"S": sheet('<row><c t="inlineStr"><is><t>a</t></is></c></row>')}), "xlsx"), "## S\na")

    def test_a_comment_of_any_length_does_not_hide_the_zip64_record(self):
        # `zipfile` looks for the end record in the last 65,557 bytes and then reads the locator in the 20 bytes in front of it, wherever
        # they are. A comment of 65,516 bytes or more puts the end record less than 20 bytes into that window.
        for length in (0, 1, 65_514, 65_515, 65_516, 65_534, 65_535):
            data = self.archive(b"c" * length)
            with self.subTest(comment=length):
                self.assertEqual(len(zipfile.ZipFile(io.BytesIO(data)).namelist()), 3)  # `zipfile` does find the ZIP64 record behind the comment
                with mock.patch.object(zipfile, "ZipFile", side_effect=AssertionError("it was opened")):
                    refuse(self, data, "docx", "ZIP64", "too large for a text read")

    def test_the_archives_that_the_review_made_by_hand_are_refused_before_zipfile_builds_an_entry_for_them(self):
        for declared, real in ((5, 5), (400_000, 1)):  # the second claims 25 MB of directory and holds one entry: the claim costs nothing here
            for comment in (0, 65_535):
                data = zip64_by_hand(declared, real, comment)
                with self.subTest(declared=declared, comment=comment):
                    self.assertLess(len(data), 70_000)
                    if real == declared:  # `zipfile` believes the ZIP64 record and not the end record, which says one entry
                        self.assertEqual(len(zipfile.ZipFile(io.BytesIO(data)).namelist()), declared)
                    with mock.patch.object(zipfile, "ZipFile", side_effect=AssertionError("it was opened")):
                        refuse(self, data, "docx", "ZIP64")

    def test_an_ordinary_archive_with_the_longest_comment_is_read_and_a_comment_that_looks_like_a_locator_is_no_locator(self):
        for comment in (b"c" * 65_535, b"PK\x06\x07" * 16_000, b"PK\x05" * 20_000, b"\x00" * 65_535):
            with self.subTest(comment=comment[:4], length=len(comment)):
                self.assertEqual(office_text(with_comment(docx(para("ordinary")), comment), "docx"), "ordinary")


@unittest.skipIf(bz2 is None or lzma is None, "this Python has no bz2 or lzma")
class PackingTest(unittest.TestCase):
    """An Office file stores or deflates its parts. `zipfile` unpacks a bzip2 or LZMA part a whole block at a time, with no limit on what
    the block becomes, whatever size is asked for: a few hundred bytes would take a gigabyte before the size that the archive declares,
    which the file chooses, could be compared with anything. So a part packed any other way is refused before it is opened."""

    PACKED_WITH = {"bzip2": zipfile.ZIP_BZIP2, "lzma": zipfile.ZIP_LZMA}

    def documents(self):
        """`(kind, archive, [every part that the reader opens])` of a small document of each kind."""
        cells = sheet('<row r="1"><c r="A1" t="s"><v>0</v></c></row>')
        return [
            ("docx", docx(para("hello")), ["word/document.xml"]),
            (
                "xlsx",
                workbook({"S": cells}, ["<si><t>Name</t></si>"]),
                ["xl/workbook.xml", "xl/_rels/workbook.xml.rels", "xl/sharedStrings.xml", "xl/worksheets/sheet1.xml"],
            ),
            (
                "pptx",
                a_deck(),
                [
                    "ppt/presentation.xml", "ppt/_rels/presentation.xml.rels", "ppt/slides/slide1.xml", "ppt/slides/slide2.xml",
                    "ppt/slides/_rels/slide1.xml.rels", "ppt/notesSlides/notesSlide1.xml",
                ],
            ),
        ]

    def test_a_part_packed_with_bzip2_or_lzma_is_refused_before_a_decompressor_exists(self):
        for kind, data, parts in self.documents():
            self.assertTrue(office_text(data, kind))  # the document reads when its parts are deflated
            for part in parts:
                for label, method in self.PACKED_WITH.items():
                    with self.subTest(kind=kind, part=part, packed_with=label):
                        packed = repacked(data, {part: method})
                        with no_decompressor():
                            refuse(self, packed, kind, part, f"packed with {label}", "never uses")

    def test_a_part_that_is_not_read_may_be_packed_any_way(self):
        # a picture, the properties of the document: Office files hold many parts, and the text is in a few of them
        for kind, data, _ in self.documents():
            expected = office_text(data, kind)
            extra = {"docProps/thumbnail.bin": b"A" * 100_000, "customXml/item1.xml": b"B" * 100_000}
            for label, method in self.PACKED_WITH.items():
                with self.subTest(kind=kind, packed_with=label):
                    packed = repacked(data, {name: method for name in extra}, changed=extra)
                    with no_decompressor():
                        self.assertEqual(office_text(packed, kind), expected)

    def test_a_few_kilobytes_that_unpack_to_hundreds_of_megabytes_are_never_unpacked(self):
        for label, method in self.PACKED_WITH.items():
            data = bomb(method)
            with self.subTest(packed_with=label):
                self.assertLess(len(data), 20_000)
                tracemalloc.start()
                try:
                    with no_decompressor():
                        refuse(self, data, "xlsx", BOMB_PART, f"packed with {label}")
                    peak = tracemalloc.get_traced_memory()[1]
                finally:
                    tracemalloc.stop()
                self.assertLess(peak, 4 * 1024 * 1024)  # the part holds 16 MiB, and opened it takes all of that at once

    def test_the_watch_on_the_decompressors_sees_what_zipfile_does_with_such_a_part(self):
        # the proof that the tests above can see what they look for: `zipfile` itself creates the decompressor as soon as it opens the part
        for label, method in self.PACKED_WITH.items():
            with self.subTest(packed_with=label), no_decompressor(), self.assertRaises(Invoked):
                zipfile.ZipFile(io.BytesIO(bomb(method))).open(BOMB_PART)

    def test_a_damaged_bzip2_or_lzma_part_is_refused_as_such(self):
        for label, method in self.PACKED_WITH.items():
            data = bytearray(repacked(docx(para("hello " * 100)), {"word/document.xml": method}))
            start = data.index(b"document.xml") + len("document.xml")
            for position in range(start + 20, start + 40):
                data[position] ^= 0xFF
            with self.subTest(packed_with=label), no_decompressor():
                refuse(self, bytes(data), "docx", "word/document.xml", f"packed with {label}")

    def test_any_other_packing_is_refused_by_name_or_number(self):
        data = docx(para("x"))
        for number, said in ((1, "shrink"), (6, "implode"), (9, "deflate64"), (12, "bzip2"), (14, "lzma"), (98, "ppmd"), (99, "method 99")):
            patched = bytearray(data)
            struct.pack_into("<H", patched, patched.index(b"PK\x01\x02") + 10, number)  # the method in the directory, which is what `zipfile` goes by
            with self.subTest(method=number):
                refuse(self, bytes(patched), "docx", "word/document.xml", f"packed with {said}")

    def test_stored_and_deflated_parts_are_read_whichever_way_they_are_mixed(self):
        for kind, data, parts in self.documents():
            expected = office_text(data, kind)
            alternating = {part: (zipfile.ZIP_STORED if number % 2 else zipfile.ZIP_DEFLATED) for number, part in enumerate(parts)}
            for label, packed in (
                ("all stored", repacked(data, default=zipfile.ZIP_STORED)),
                ("all deflated", repacked(data, default=zipfile.ZIP_DEFLATED)),
                ("mixed", repacked(data, alternating)),
            ):
                with self.subTest(kind=kind, packing=label):
                    self.assertEqual(office_text(packed, kind), expected)

    def test_an_encrypted_part_is_refused_without_being_opened_whatever_its_packing(self):
        for method in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            data = bytearray(repacked(docx(para("secret")), default=method))
            data[data.index(b"PK\x03\x04") + 6] |= 1
            data[data.index(b"PK\x01\x02") + 8] |= 1  # the flag of encryption, in the local header and in the directory
            with self.subTest(method=method), mock.patch.object(zipfile.ZipFile, "open", side_effect=Invoked("an encrypted part was opened")):
                refuse(self, bytes(data), "docx", "word/document.xml", "password")
        data = bytearray(repacked(docx(para("secret")), default=zipfile.ZIP_BZIP2))
        data[data.index(b"PK\x01\x02") + 8] |= 1
        with no_decompressor():
            refuse(self, bytes(data), "docx", "word/document.xml")  # encrypted and packed with bzip2: refused, whichever of the two is said

    def test_a_deflated_part_that_declares_a_small_size_is_stopped_at_what_is_read(self):
        data = bomb(zipfile.ZIP_DEFLATED)
        self.assertLess(len(data), 40_000)
        tracemalloc.start()
        try:
            refuse(self, data, "xlsx", BOMB_PART, "cannot be unpacked")  # the checksum does not fit the 1,000 bytes that the headers allow
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        self.assertLess(peak, 24 * 1024 * 1024)  # about twice the 6 MiB that `read` is asked for (the output is joined from blocks), never the 32 MiB that the whole part takes


class BudgetTest(unittest.TestCase):
    """The time that reading a document takes grows with the tags in it, so they are counted for all the parts together before a part is
    parsed, and every part is read once: a refusal costs a count, not a read."""

    def cells(self, count: int) -> str:
        return sheet('<row r="1">' + "<c/>" * count + "</row>")

    def test_the_tags_of_all_the_parts_are_counted_together(self):
        one = sheet('<row><c t="inlineStr"><is><t>x</t></is></c></row>' * 12)  # a hundred tags
        with mock.patch.object(ooxml, "MAX_MARKUP", 200):
            for _ in range(2):  # the count belongs to one document and starts again with the next
                self.assertEqual(office_text(workbook({"A": one}), "xlsx").count("x"), 12)
            message = refuse(self, workbook({"A": one, "B": one}), "xlsx", "tags in all its parts")
        self.assertNotIn("The part", message)

    def test_a_part_with_more_tags_than_the_whole_document_may_have_is_named(self):
        with mock.patch.object(ooxml, "MAX_MARKUP", 100):
            refuse(self, docx(para("x") * 40), "docx", "The part word/document.xml holds more than 100 tags")

    def test_the_limit_is_exact_for_one_part_and_for_all_parts_together(self):
        def tags(data: bytes) -> int:
            archive = zipfile.ZipFile(io.BytesIO(data))
            return sum(archive.read(name).count(b"<") for name in archive.namelist())

        for kind, data in (("docx", docx("<w:p/>" * 46)), ("xlsx", workbook({"A": self.cells(30)}))):
            count = tags(data)
            with self.subTest(kind=kind, tags=count):
                with mock.patch.object(ooxml, "MAX_MARKUP", count):
                    office_text(data, kind)  # exactly as many tags as are allowed: read
                with mock.patch.object(ooxml, "MAX_MARKUP", count - 1):
                    refuse(self, data, kind, "tags")  # one more: refused

    def test_the_documents_of_the_review_are_refused_by_a_count_in_a_fraction_of_a_second(self):
        shapes = {
            "a million and a half empty cells in one row": (workbook({"S": self.cells(1_500_000)}), "xlsx"),
            "a million empty rows": (workbook({"S": sheet("<row/>" * 1_040_000)}), "xlsx"),
            "300,000 shared strings": (workbook({"S": sheet("<row/>")}, ["<si><t>x</t></si>"] * 300_000), "xlsx"),
            "a million empty paragraphs": (docx("<w:p/>" * 1_040_000), "docx"),
            "a million empty paragraphs of a slide": (zipped({"ppt/presentation.xml": f"<p:presentation {P}/>", "ppt/slides/slide1.xml": slide(shape("<a:p/>" * 1_040_000))}), "pptx"),
        }
        for label, (data, kind) in shapes.items():
            with self.subTest(label):
                self.assertLess(len(data), 60_000)
                refuse(self, data, kind, "1,000,000 tags", seconds=2)

    def test_a_refusal_by_the_count_comes_before_the_part_is_parsed(self):
        data = workbook({"S": self.cells(1_500_000)})
        with mock.patch.object(ooxml, "_shape", wraps=ooxml._shape) as shaped, mock.patch.object(ooxml.ET, "iterparse", side_effect=AssertionError("it was parsed")):
            refuse(self, data, "xlsx", "xl/worksheets/sheet1.xml", seconds=2)
        self.assertNotIn("xl/worksheets/sheet1.xml", [call.args[1] for call in shaped.call_args_list])  # the small parts before it were looked at, this one was not

    def two_sheets_that_are_one_part(self, sheets: int, cells: int) -> bytes:
        names = "".join(f'<sheet name="S{n}" sheetId="{n}" r:id="rId{n}"/>' for n in range(1, sheets + 1))
        targets = "".join(f'<Relationship Id="rId{n}" Type="{REL}worksheet" Target="worksheets/sheet1.xml"/>' for n in range(1, sheets + 1))
        return zipped(
            {
                "xl/workbook.xml": f"<workbook {S}><sheets>{names}</sheets></workbook>",
                "xl/_rels/workbook.xml.rels": f"<Relationships {RELS}>{targets}</Relationships>",
                "xl/worksheets/sheet1.xml": self.cells(cells),
            }
        )

    def test_a_part_that_the_document_lists_again_is_unpacked_once_and_then_refused(self):
        for sheets in (2, 400):  # 400: many sheets that all point at one part
            data = self.two_sheets_that_are_one_part(sheets, 60_000)
            original = zipfile.ZipFile.open
            with self.subTest(sheets=sheets), mock.patch.object(zipfile.ZipFile, "open", autospec=True, side_effect=original) as opened:
                refuse(self, data, "xlsx", "xl/worksheets/sheet1.xml", "more than once")
            self.assertEqual([call.args[1].filename for call in opened.call_args_list].count("xl/worksheets/sheet1.xml"), 1)

    def test_a_workbook_that_lists_another_part_as_a_sheet_is_refused(self):
        data = zipped(
            {
                "xl/workbook.xml": f'<workbook {S}><sheets><sheet name="W" sheetId="1" r:id="rId1"/></sheets></workbook>',
                "xl/_rels/workbook.xml.rels": f'<Relationships {RELS}><Relationship Id="rId1" Type="{REL}worksheet" Target="workbook.xml"/></Relationships>',
            }
        )
        refuse(self, data, "xlsx", "xl/workbook.xml", "more than once")

    def test_the_slowest_document_that_the_limits_let_through_is_read_in_seconds(self):
        # a million empty cells in one row: the most that the count allows, and the shape that costs most for each tag. About two seconds
        # on an ordinary PC; the bound leaves room for a loaded machine, and is far below what the old limits allowed (three times as long).
        data = workbook({"S": self.cells(990_000)})
        started = time.monotonic()
        self.assertEqual(office_text(data, "xlsx"), "## S")
        self.assertLess(time.monotonic() - started, 8)


if __name__ == "__main__":
    unittest.main()
