"""
Exportação da tela atual (tabelas como estão na tela, com o filtro aplicado) para Excel (.xlsx) e PDF.
Usa apenas a biblioteca padrão.

Formato de entrada:
    title: str                      -> título do relatório
    meta: List[str]                 -> linhas de informação (período, arquivos, filtro...)
    sections: List[Tuple[str, List[str], List[List[str]]]]
                                    -> (subtítulo, cabeçalhos, linhas)
"""

from __future__ import annotations

import io
import re
import zipfile
from datetime import datetime
from typing import List, Optional, Tuple
from xml.sax.saxutils import escape

Section = Tuple[str, List[str], List[List[str]]]

_MONEY_RE = re.compile(r"^-?R\$\s?-?[\d.]+,\d{2}$")
_INT_RE = re.compile(r"^-?\d{1,9}$")


def _money_value(text: str) -> Optional[float]:
    t = text.strip()
    if not _MONEY_RE.match(t):
        return None
    neg = t.startswith("-") or "R$ -" in t or "R$-" in t
    num = re.sub(r"[^\d,]", "", t).replace(",", ".")
    try:
        v = float(num)
    except ValueError:
        return None
    return -v if neg else v


# ============================================================================ XLSX

def _col_letter(idx: int) -> str:
    s = ""
    idx += 1
    while idx:
        idx, r = divmod(idx - 1, 26)
        s = chr(65 + r) + s
    return s


def build_xlsx(title: str, meta: List[str], sections: List[Section]) -> bytes:
    rows_xml: List[str] = []
    widths: dict = {}
    r = 0

    def cell_str(c: int, text: str, style: int = 0) -> str:
        widths[c] = max(widths.get(c, 8), min(60, len(text) + 2))
        return (f'<c r="{_col_letter(c)}{r}" t="inlineStr" s="{style}"><is><t xml:space="preserve">'
                f"{escape(text)}</t></is></c>")

    def cell_num(c: int, value: float, style: int) -> str:
        widths[c] = max(widths.get(c, 8), 14)
        return f'<c r="{_col_letter(c)}{r}" s="{style}"><v>{value}</v></c>'

    def add_row(cells: List[str]) -> None:
        rows_xml.append(f'<row r="{r}">' + "".join(cells) + "</row>")

    r += 1
    add_row([cell_str(0, title, 1)])
    for line in meta:
        r += 1
        add_row([cell_str(0, line, 0)])

    for subtitle, headers, rows in sections:
        r += 2
        add_row([cell_str(0, f"{subtitle}  ({len(rows)} linhas)", 1)])
        r += 1
        add_row([cell_str(i, h, 2) for i, h in enumerate(headers)])
        for row in rows:
            r += 1
            cells = []
            for i, val in enumerate(row):
                text = "" if val is None else str(val)
                money = _money_value(text)
                if money is not None:
                    cells.append(cell_num(i, money, 3))
                elif _INT_RE.match(text) and not text.startswith("0"):
                    cells.append(cell_num(i, int(text), 0))
                else:
                    cells.append(cell_str(i, text, 0))
            add_row(cells)

    cols_xml = "".join(f'<col min="{c + 1}" max="{c + 1}" width="{w}" customWidth="1"/>' for c, w in sorted(widths.items()))
    sheet = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
             f"<cols>{cols_xml}</cols><sheetData>{''.join(rows_xml)}</sheetData></worksheet>")
    styles = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
              '<numFmts count="1"><numFmt numFmtId="164" formatCode="&quot;R$&quot; #,##0.00"/></numFmts>'
              '<fonts count="2"><font><sz val="10"/><name val="Calibri"/></font>'
              '<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
              '<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
              '<fill><patternFill patternType="solid"><fgColor rgb="FFDCE6F1"/></patternFill></fill></fills>'
              '<borders count="1"><border/></borders>'
              '<cellStyleXfs count="1"><xf/></cellStyleXfs>'
              '<cellXfs count="4"><xf/><xf fontId="1" applyFont="1"/>'
              '<xf fontId="1" fillId="2" applyFont="1" applyFill="1"/>'
              '<xf numFmtId="164" applyNumberFormat="1"/></cellXfs></styleSheet>')
    sheet_name = escape(re.sub(r"[\[\]:*?/\\]", "", title)[:31] or "Planilha")
    files = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            "</Types>"),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>"),
        "xl/workbook.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f'<sheets><sheet name="{sheet_name}" sheetId="1" r:id="rId1"/></sheets></workbook>'),
        "xl/_rels/workbook.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
            "</Relationships>"),
        "xl/worksheets/sheet1.xml": sheet,
        "xl/styles.xml": styles,
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


# ============================================================================ PDF

_PAGE_W, _PAGE_H = 842.0, 595.0  # A4 paisagem
_MARGIN = 28.0
_FONT_SIZE = 7.0
_ROW_H = 11.0


def _pdf_text(text: str) -> str:
    # A fonte padrão do PDF não tem ✓ ✗ ⚠: removemos os símbolos (o texto ao lado já diz Sim/Não)
    for sym in ("✓ ", "✗ ", "⚠ ", "✓", "✗", "⚠"):
        text = text.replace(sym, "")
    data = text.encode("cp1252", "replace").decode("latin1")
    return data.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _fit(text: str, width: float, size: float) -> str:
    max_chars = max(1, int(width / (size * 0.52)))
    return text if len(text) <= max_chars else text[: max_chars - 1] + "…"


def build_pdf(title: str, meta: List[str], sections: List[Section]) -> bytes:
    pages: List[List[str]] = []
    ops: List[str] = []
    y = 0.0
    usable_w = _PAGE_W - 2 * _MARGIN

    def new_page() -> None:
        nonlocal ops, y
        ops = []
        pages.append(ops)
        y = _PAGE_H - _MARGIN

    def text(x: float, yy: float, s: str, size: float = _FONT_SIZE, bold: bool = False) -> None:
        ops.append(f"BT /{'F2' if bold else 'F1'} {size} Tf {x:.1f} {yy:.1f} Td ({_pdf_text(s)}) Tj ET")

    def header_row(headers: List[str], widths: List[float]) -> None:
        nonlocal y
        ops.append(f"0.86 0.90 0.95 rg {_MARGIN:.1f} {y - _ROW_H + 3:.1f} {usable_w:.1f} {_ROW_H:.1f} re f 0 0 0 rg")
        x = _MARGIN + 2
        for h, w in zip(headers, widths):
            text(x, y - _ROW_H + 6, _fit(h, w - 4, _FONT_SIZE), bold=True)
            x += w
        y -= _ROW_H

    new_page()
    text(_MARGIN, y - 12, title, 13, bold=True)
    y -= 22
    for line in meta:
        text(_MARGIN, y - 8, _fit(line, usable_w, 8), 8)
        y -= 11

    for subtitle, headers, rows in sections:
        if y < _MARGIN + 60:
            new_page()
        y -= 10
        text(_MARGIN, y - 9, f"{subtitle}  ({len(rows)} linhas)", 10, bold=True)
        y -= 16
        # Larguras proporcionais ao conteúdo (limitadas para não estourar)
        lens = [max([len(h)] + [len(str(r[i])) for r in rows[:300] if i < len(r)]) for i, h in enumerate(headers)]
        lens = [min(max(l_, 4), 48) for l_ in lens]
        total = float(sum(lens)) or 1.0
        widths = [usable_w * l_ / total for l_ in lens]
        header_row(headers, widths)
        for idx, row in enumerate(rows):
            if y < _MARGIN + _ROW_H:
                new_page()
                header_row(headers, widths)
            if idx % 2:
                ops.append(f"0.97 0.97 0.97 rg {_MARGIN:.1f} {y - _ROW_H + 3:.1f} {usable_w:.1f} {_ROW_H:.1f} re f 0 0 0 rg")
            x = _MARGIN + 2
            for i, w in enumerate(widths):
                val = str(row[i]) if i < len(row) and row[i] is not None else ""
                txt = _fit(val, w - 4, _FONT_SIZE)
                if _money_value(val) is not None:  # valores alinhados à direita
                    tx = x + w - 4 - len(txt) * _FONT_SIZE * 0.5
                    text(max(x, tx), y - _ROW_H + 6, txt)
                else:
                    text(x, y - _ROW_H + 6, txt)
                x += w
            y -= _ROW_H

    total_pages = len(pages)
    stamp = datetime.now().strftime("%d/%m/%Y %H:%M")
    for n, page_ops in enumerate(pages, start=1):
        page_ops.append(f"BT /F1 7 Tf {_MARGIN:.1f} 14 Td ({_pdf_text(f'ConfereFiscal  •  gerado em {stamp}')}) Tj ET")
        page_ops.append(f"BT /F1 7 Tf {_PAGE_W - _MARGIN - 50:.1f} 14 Td ({_pdf_text(f'Página {n}/{total_pages}')}) Tj ET")

    # Monta o arquivo PDF
    objs: List[bytes] = []
    font_base = 3 + 2 * total_pages
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(total_pages))
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {total_pages} >>".encode())
    for i, page_ops in enumerate(pages):
        content = "\n".join(page_ops).encode("latin1")
        objs.append(
            (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {_PAGE_W:.0f} {_PAGE_H:.0f}] "
             f"/Resources << /Font << /F1 {font_base} 0 R /F2 {font_base + 1} 0 R >> >> "
             f"/Contents {4 + 2 * i} 0 R >>").encode())
        objs.append(b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, o in enumerate(objs, start=1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % i + o + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1))
    for off in offsets:
        out.write(b"%010d 00000 n \n" % off)
    out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref))
    return out.getvalue()
