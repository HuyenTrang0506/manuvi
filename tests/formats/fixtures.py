"""Synthetic documents for the format tests (no real files). Built by hand as OOXML packages so every feature is
present: runs with different formatting, hyperlinks, tracked changes, fields, text boxes, merged cells, footnotes."""
from __future__ import annotations

import zipfile
from pathlib import Path

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" ' \
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" ' \
    'xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape" ' \
    'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" ' \
    'xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml"'
CT_DOCX = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
           '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
           '<Default Extension="xml" ContentType="application/xml"/>'
           '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
           '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
           '<Override PartName="/word/header1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/>'
           '<Override PartName="/word/footer1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/>'
           '<Override PartName="/word/footnotes.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml"/>'
           '</Types>')
RELS = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
DOC_RELS = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" Target="header1.xml"/>'
            '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" Target="footer1.xml"/>'
            '<Relationship Id="rId4" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes" Target="footnotes.xml"/>'
            '<Relationship Id="rId5" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="https://example.com/a" TargetMode="External"/>'
            '</Relationships>')
STYLES = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:styles {W}><w:docDefaults><w:rPrDefault><w:rPr>'
          '<w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/><w:lang w:val="en-US"/></w:rPr></w:rPrDefault></w:docDefaults>'
          '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>'
          '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/></w:style></w:styles>')


def r(text, rpr="", tag="w:t"):
    return f'<w:r>{"<w:rPr>" + rpr + "</w:rPr>" if rpr else ""}<{tag} xml:space="preserve">{text}</{tag}></w:r>'


BOLD, ITAL = "<w:b/>", "<w:i/>"
DOC_BODY = (
    '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr>' + r("Remove the relay box") + '</w:p>'
    '<w:p>' + r("Disconnect the ") + r("battery", BOLD) + r(" before ") + r("service", ITAL) + r(" of part YN20-01234-A.") + '</w:p>'
    '<w:p>' + r("See ") + '<w:hyperlink r:id="rId5">' + r("the online guide", '<w:rStyle w:val="Hyperlink"/><w:u w:val="single"/>') + '</w:hyperlink>' + r(" for details.") + '</w:p>'
    '<w:p>' + r("Tighten to 12 Nm") + '<w:r><w:tab/></w:r>' + r("then check") + '<w:r><w:br/></w:r>' + r("the second line.") + '</w:p>'
    '<w:p>' + r("Original ") + '<w:ins w:id="1" w:author="Ann" w:date="2024-01-01T00:00:00Z">' + r("inserted words", BOLD) + '</w:ins>'
    '<w:del w:id="2" w:author="Bob" w:date="2024-01-01T00:00:00Z">' + r("deleted words", "", "w:delText") + '</w:del>' + r(" end.") + '</w:p>'
    '<w:p>' + r("Page ") + '<w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText xml:space="preserve"> PAGE </w:instrText></w:r>'
    '<w:r><w:fldChar w:fldCharType="separate"/></w:r>' + r("1") + '<w:r><w:fldChar w:fldCharType="end"/></w:r>' + r(" of the manual") + '</w:p>'
    '<w:p>' + r("Note the footnote") + '<w:r><w:footnoteReference w:id="2"/></w:r>' + r(" here.") + '</w:p>'
    '<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr>' + r("First list item") + '</w:p>'
    '<w:p><w:r><w:rPr><w:rFonts w:ascii="Bebas Neue" w:hAnsi="Bebas Neue"/></w:rPr><w:t>Display font heading</w:t></w:r></w:p>'
    '<w:tbl><w:tblPr/><w:tblGrid><w:gridCol w:w="2000"/><w:gridCol w:w="2000"/></w:tblGrid>'
    '<w:tr><w:tc><w:tcPr><w:gridSpan w:val="2"/></w:tcPr><w:p>' + r("Merged header cell") + '</w:p></w:tc></w:tr>'
    '<w:tr><w:tc><w:p>' + r("Left cell text") + '</w:p></w:tc><w:tc><w:p>' + r("Right cell text") + '</w:p></w:tc></w:tr></w:tbl>'
    '<w:p><w:r><mc:AlternateContent><mc:Choice Requires="wps"><w:drawing><wps:wsp><wps:txbx><w:txbxContent><w:p>'
    + r("Text inside a box") + '</w:p></w:txbxContent></wps:txbx></wps:wsp></w:drawing></mc:Choice><mc:Fallback/></mc:AlternateContent></w:r></w:p>'
    '<w:sectPr><w:headerReference w:type="default" r:id="rId2"/><w:footerReference w:type="default" r:id="rId3"/></w:sectPr>')


def make_docx(path: Path, extra_entries: dict[str, bytes] | None = None, content_types: str | None = None) -> Path:
    parts = {
        "[Content_Types].xml": content_types or CT_DOCX, "_rels/.rels": RELS, "word/_rels/document.xml.rels": DOC_RELS,
        "word/styles.xml": STYLES,
        "word/document.xml": f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document {W}><w:body>{DOC_BODY}</w:body></w:document>',
        "word/header1.xml": f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:hdr {W}><w:p>{r("Service manual header")}</w:p></w:hdr>',
        "word/footer1.xml": f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:ftr {W}><w:p>{r("Footer text")}</w:p></w:ftr>',
        "word/footnotes.xml": f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:footnotes {W}>'
                              f'<w:footnote w:type="separator" w:id="0"><w:p><w:r><w:separator/></w:r></w:p></w:footnote>'
                              f'<w:footnote w:id="2"><w:p><w:r><w:footnoteRef/></w:r>{r(" The footnote says check the fuse.")}</w:p></w:footnote></w:footnotes>',
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for k, v in {**parts, **(extra_entries or {})}.items():
            z.writestr(k, v)
    return path


def make_pptx(path: Path) -> Path:
    """Built with python-pptx (blank layout) then enriched: title, runs, table, notes, group, small box."""
    from pptx import Presentation
    from pptx.enum.text import MSO_AUTO_SIZE
    from pptx.util import Inches, Pt
    prs = Presentation()
    s = prs.slides.add_slide(prs.slide_layouts[5])
    s.shapes.title.text = "Relay box service"
    tb = s.shapes.add_textbox(Inches(1), Inches(2), Inches(5), Inches(1))
    para = tb.text_frame.paragraphs[0]
    for text, bold, ital in (("Disconnect the ", False, False), ("battery", True, False), (" first, then ", False, False),
                             ("wait", False, True), (" two minutes.", False, False)):
        run = para.add_run()
        run.text = text
        run.font.bold, run.font.italic, run.font.size = bold, ital, Pt(18)
    small = s.shapes.add_textbox(Inches(1), Inches(4), Inches(1.2), Inches(0.4))
    small.text_frame.word_wrap = True
    small.text_frame.auto_size = MSO_AUTO_SIZE.NONE      # fixed-size box: the shrink guard applies
    small.text_frame.text = "Remove the cover and check the harness"
    small.text_frame.paragraphs[0].runs[0].font.size = Pt(18)
    tbl = s.shapes.add_table(2, 2, Inches(1), Inches(5), Inches(6), Inches(1)).table
    tbl.cell(0, 0).text, tbl.cell(0, 1).text = "Part", "Description"
    tbl.cell(1, 0).text, tbl.cell(1, 1).text = "YN20-01234-A", "Relay for the starter circuit"
    grp = s.shapes.add_group_shape()
    g = grp.shapes.add_textbox(Inches(7), Inches(1), Inches(2), Inches(0.5))
    g.text_frame.text = "Grouped shape label"
    s.notes_slide.notes_text_frame.text = "Speaker note: explain the relay."
    s2 = prs.slides.add_slide(prs.slide_layouts[1])
    s2.shapes.title.text = "Second slide"
    s2.placeholders[1].text = "Bullet one is here"
    prs.save(path)
    return path
