"""Open XML SDK로 만든 xlsx (워크북 형식을 [Content_Types].xml 의 Default 로만 적은 파일).

실제 고객 문의 샘플(01_개인정보_검사.xlsx)이 이 PC 검사 단계에서 UNSUPPORTED_FEATURE 로 멈춰
서버로 보내지지 않았다. backend tests/test_xlsx_openxml_sdk.py 와 같은 파일을 만든다.
"""
import unittest
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

from agent import config
from agent.files.processor import OrgProcessor
from agent.files.xlsx.package import RejectedFile, inspect_package
from agent.files.xlsx.sanitizer import sanitize_xlsx
from agent.sanitize.engine import Session

MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
WORKBOOK = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
BOM = "﻿"


def cell(ref, text, style=1):
    return f'<x:c r="{ref}" s="{style}" t="str"><x:v>{text}</x:v></x:c>'


def sdk_book(rows, *, content_types=None):
    sheet_rows = "".join(
        f'<x:row r="{r}">' + "".join(cell(f"{chr(65 + c)}{r}", v, 3 if r == 1 else 1)
                                     for c, v in enumerate(values)) + "</x:row>"
        for r, values in enumerate(rows, start=1)
    )
    parts = {
        "xl/workbook.xml": f'<?xml version="1.0" encoding="utf-8"?><x:workbook xmlns:x="{MAIN_NS}"><x:sheets>'
                           f'<x:sheet name="고객문의" sheetId="1" r:id="R1" xmlns:r="{REL_NS}" /></x:sheets></x:workbook>',
        "xl/styles.xml": f'<?xml version="1.0" encoding="utf-8"?><x:styleSheet xmlns:x="{MAIN_NS}">'
                         '<x:numFmts count="1"><x:numFmt numFmtId="200" formatCode="@" /></x:numFmts>'
                         '<x:fonts count="2"><x:font><x:sz val="11" /><x:name val="Carlito" /></x:font>'
                         '<x:font><x:b /><x:sz val="11" /><x:color rgb="FFFFFFFF" /><x:name val="Carlito" /></x:font></x:fonts>'
                         '<x:fills count="3"><x:fill><x:patternFill patternType="none" /></x:fill>'
                         '<x:fill><x:patternFill patternType="gray125" /></x:fill>'
                         '<x:fill><x:patternFill patternType="solid"><x:fgColor rgb="FF1F4E78" /></x:patternFill></x:fill></x:fills>'
                         '<x:borders count="1"><x:border><x:left /><x:right /><x:top /><x:bottom /><x:diagonal /></x:border></x:borders>'
                         '<x:cellStyleXfs count="1"><x:xf numFmtId="0" fontId="0" fillId="0" borderId="0" /></x:cellStyleXfs>'
                         '<x:cellXfs count="4"><x:xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" />'
                         '<x:xf numFmtId="200" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1" />'
                         '<x:xf numFmtId="200" fontId="0" fillId="2" borderId="0" xfId="0" applyNumberFormat="1" />'
                         '<x:xf numFmtId="200" fontId="1" fillId="2" borderId="0" xfId="0" applyNumberFormat="1" /></x:cellXfs>'
                         '<x:cellStyles count="1"><x:cellStyle name="Normal" xfId="0" /></x:cellStyles></x:styleSheet>',
        "xl/sharedStrings.xml": f'<?xml version="1.0" encoding="utf-8"?><x:sst xmlns:x="{MAIN_NS}" />',
        "xl/worksheets/sheet1.xml": f'<?xml version="1.0" encoding="utf-8"?><x:worksheet xmlns:x="{MAIN_NS}">'
                                    '<x:sheetViews><x:sheetView workbookViewId="0"><x:pane ySplit="1" topLeftCell="A2" '
                                    'activePane="bottomLeft" state="frozen" /></x:sheetView></x:sheetViews>'
                                    '<x:sheetFormatPr defaultRowHeight="15" /><x:cols>'
                                    '<x:col min="1" max="5" width="24" hidden="0" customWidth="1" /></x:cols>'
                                    f'<x:sheetData>{sheet_rows}</x:sheetData>'
                                    '<x:pageMargins left="0.7" right="0.7" top="0.75" bottom="0.75" header="0.3" footer="0.3" />'
                                    '</x:worksheet>',
        "_rels/.rels": BOM + '<?xml version="1.0" encoding="utf-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                       f'<Relationship Type="{REL_NS}/officeDocument" Target="/xl/workbook.xml" Id="R0" /></Relationships>',
        "xl/_rels/workbook.xml.rels": BOM + '<?xml version="1.0" encoding="utf-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                                      f'<Relationship Type="{REL_NS}/styles" Target="/xl/styles.xml" Id="Rs" />'
                                      f'<Relationship Type="{REL_NS}/sharedStrings" Target="/xl/sharedStrings.xml" Id="Rt" />'
                                      f'<Relationship Type="{REL_NS}/worksheet" Target="/xl/worksheets/sheet1.xml" Id="R1" /></Relationships>',
        "[Content_Types].xml": content_types or (
            BOM + '<?xml version="1.0" encoding="utf-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            f'<Default Extension="xml" ContentType="{WORKBOOK}" />'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml" />'
            '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml" />'
            '<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml" />'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml" />'
            '</Types>'),
    }
    out = BytesIO()
    with ZipFile(out, "w", ZIP_DEFLATED) as package:
        for name, text in parts.items():
            package.writestr(name, text.encode("utf-8"))
    return out.getvalue()


ROWS = [
    ["사례", "고객명", "연락처", "상담 내용"],
    ["P01", "김민수", "010-2345-6789", "예약 일정을 확인해 주세요."],
    ["P04", "박영희", "02-234-5678", "유선번호"],
    ["P05", "김민수", "031-234-5678", "기존 가명 형태 [이름_1] 과 박영희"],
]




class OpenXmlSdkTests(unittest.TestCase):
    def test_workbook_declared_by_default_extension_is_accepted(self):
        inspect_package(sdk_book(ROWS))

    def test_untyped_workbook_is_still_rejected(self):
        untyped = ('<?xml version="1.0" encoding="utf-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml" /></Types>')
        with self.assertRaises(RejectedFile) as caught:
            inspect_package(sdk_book(ROWS, content_types=untyped))
        self.assertEqual(str(caught.exception), "UNSUPPORTED_FEATURE")

    def test_local_step_makes_a_copy_to_send(self):
        base = config.load_base()
        result = sanitize_xlsx(sdk_book(ROWS), "t", OrgProcessor(base, Session(base)), filename="01_개인정보_검사.xlsx")
        self.assertEqual(result.status, "READY", result.reason)
        self.assertTrue(result.file_bytes)
