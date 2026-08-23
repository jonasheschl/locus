from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from app.spreadsheets import read_spreadsheet, spreadsheet_to_markdown


ODS_CONTENT = """<?xml version="1.0" encoding="UTF-8"?>
<office:document-content
 xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
 xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0"
 xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">
 <office:body><office:spreadsheet><table:table table:name="Ideas">
  <table:table-row>
   <table:table-cell/><table:table-cell office:value-type="string"><text:p>Name</text:p></table:table-cell>
   <table:table-cell office:value-type="string"><text:p>Notes</text:p></table:table-cell>
  </table:table-row>
  <table:table-row>
   <table:table-cell/><table:table-cell office:value-type="string"><text:p>Locus</text:p></table:table-cell>
   <table:table-cell office:value-type="string"><text:p>Local wiki</text:p></table:table-cell>
  </table:table-row>
 </table:table></office:spreadsheet></office:body>
</office:document-content>
"""


def test_reads_ods_and_converts_it_to_markdown(tmp_path: Path) -> None:
    path = tmp_path / "Ideas.ods"
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr("content.xml", ODS_CONTENT)

    sheets = read_spreadsheet(path)
    markdown = spreadsheet_to_markdown(path)

    assert sheets == [
        {
            "name": "Ideas",
            "rows": [["Name", "Notes"], ["Locus", "Local wiki"]],
            "truncated": False,
        }
    ]
    assert "## Ideas" in markdown
    assert "| Locus | Local wiki |" in markdown
