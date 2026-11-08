"""
Leitura de relatórios de vendas em PDF (Clipp e outros ERPs).

Fluxo: PDF -> linhas/células (pdf_text) -> detecção do cabeçalho da tabela -> registros de venda.
Também lê os totais impressos no rodapé ("Total de documentos", "Totais") para conferir
se a extração bateu com o relatório.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

from .pdf_text import extract_pdf_lines


def parse_br_number(raw: str) -> Optional[float]:
    s = str(raw or "").replace("R$", "").replace(" ", "").strip()
    if not re.fullmatch(r"-?[\d.]*\d(,\d+)?", s):
        return None
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif s.count(".") > 1:
        s = s.replace(".", "")
    try:
        return round(float(s), 2)
    except ValueError:
        return None


def _norm(text: str) -> str:
    t = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", t)).strip()


# Palavras usadas para reconhecer cada coluna no cabeçalho do relatório (ordem = prioridade).
_HEADER_KEYS = [
    ("accessKeyCol", ("chave de acesso", "chave")),
    ("seriesCol", ("serie",)),
    ("modelCol", ("mod", "modelo")),
    ("cfopCol", ("cfop",)),
    ("customerCol", ("cliente", "destinatario", "consumidor")),
    ("paymentCol", ("forma pagamento", "pagamento", "forma pgto")),
    ("statusCol", ("status", "situacao")),
    ("dateCol", ("data emissao", "dt emissao", "emissao", "data")),
    ("valueCol", ("total nota", "valor total", "total nf", "vl total", "valor nota", "total", "valor")),
    ("numberCol", ("nf numero", "numero", "num nf", "n nf", "documento", "doc", "nota", "cupom", "nfc e", "nf")),
]


def _detect_header(cells: List[str]) -> Optional[Dict[str, int]]:
    norm = [_norm(c) for c in cells]
    mapping: Dict[str, int] = {}
    used: set = set()
    for key, words in _HEADER_KEYS:
        for w in words:
            idx = next(
                (i for i, c in enumerate(norm)
                 if i not in used and (c == w or c.startswith(w + " ") or c.endswith(" " + w) or (len(w) > 4 and w in c))),
                -1,
            )
            if idx >= 0:
                mapping[key] = idx
                used.add(idx)
                break
    if "numberCol" in mapping and "valueCol" in mapping and ("dateCol" in mapping or "seriesCol" in mapping):
        return mapping
    return None


_DATE_RE = re.compile(r"(\d{2})/(\d{2})/(\d{4})|(\d{4})-(\d{2})-(\d{2})")
_MONEY_RE = re.compile(r"^-?\d{1,3}(\.\d{3})*,\d{2,3}$|^-?\d+,\d{2,3}$")


def _iso_date(raw: str) -> str:
    m = _DATE_RE.search(raw or "")
    if not m:
        return ""
    if m.group(1):
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    return f"{m.group(4)}-{m.group(5)}-{m.group(6)}"


def _status(raw: str) -> str:
    s = (raw or "").strip().upper()
    if not s or s.startswith("AUTORIZ") or s in ("A", "NORMAL", "EMITIDA", "EMITIDO"):
        return "AUTORIZADO"
    if s.startswith("CANCEL") or s == "C":
        return "CANCELADO"
    if s.startswith("DENEG") or s == "D":
        return "DENEGADO"
    if s.startswith("INUTIL") or s == "I":
        return "INUTILIZADO"
    return s


def _row_from_cells(cells: List[str], mp: Dict[str, int]) -> Optional[Dict[str, Any]]:
    def get(key: str) -> str:
        i = mp.get(key, -1)
        return cells[i].strip() if 0 <= i < len(cells) else ""

    num = re.sub(r"\D", "", get("numberCol"))
    iso = _iso_date(get("dateCol")) or next((_iso_date(c) for c in cells if _DATE_RE.search(c)), "")
    value = parse_br_number(get("valueCol"))
    if not num or len(num) > 9 or value is None or not iso:
        return None
    key = re.sub(r"\D", "", get("accessKeyCol"))
    model = re.sub(r"\D", "", get("modelCol")) or (key[20:22] if len(key) == 44 else "65")
    series = re.sub(r"\D", "", get("seriesCol")) or "1"
    return {
        "issueDate": iso,
        "authDate": iso,
        "number": str(int(num)),
        "series": str(int(series)),
        "model": model,
        "accessKey": key,
        "totalValue": value,
        "paymentMethod": get("paymentCol") or "Não informado",
        "status": _status(get("statusCol")),
        "customer": get("customerCol"),
        "cfop": get("cfopCol"),
    }


def _split_pipes(cells: List[str]) -> List[str]:
    if len(cells) == 1 and "|" in cells[0]:
        return [c.strip() for c in cells[0].strip().strip("|").split("|")]
    return cells


def _rows_by_header(lines: List[List[str]]):
    mapping: Optional[Dict[str, int]] = None
    header: List[str] = []
    records, raw_rows = [], []
    for cells in lines:
        cells = _split_pipes(cells)
        hdr = _detect_header(cells)
        if hdr:
            mapping, header = hdr, cells
            continue
        if not mapping:
            continue
        rec = _row_from_cells(cells, mapping)
        if rec:
            records.append(rec)
            raw_rows.append(cells)
    return records, (mapping or {}), raw_rows, header


def _rows_by_heuristic(lines: List[List[str]]):
    """Plano B sem cabeçalho: linha com número + data + valor monetário."""
    records, raw_rows = [], []
    for cells in lines:
        cells = _split_pipes(cells)
        tokens: List[str] = []
        for c in cells:
            tokens.extend([c] if _DATE_RE.search(c) else c.split())
        date_idx = next((i for i, t in enumerate(tokens) if _DATE_RE.search(t)), -1)
        if date_idx < 0:
            continue
        money_idx = [i for i, t in enumerate(tokens) if i > date_idx and _MONEY_RE.match(t)]
        ints = [i for i, t in enumerate(tokens) if re.fullmatch(r"\d{1,9}", t) and i < date_idx]
        if not money_idx or not ints:
            continue
        small = [tokens[i] for i in ints[1:]]
        model = next((t for t in small if t in ("55", "65", "59")), "65")
        series = next((t for t in small if t != model), "1")
        value = parse_br_number(tokens[money_idx[0]])
        if value is None:
            continue
        iso = _iso_date(tokens[date_idx])
        records.append({
            "issueDate": iso, "authDate": iso, "number": str(int(tokens[ints[0]])),
            "series": str(int(series)), "model": model, "accessKey": "", "totalValue": value,
            "paymentMethod": "Não informado", "status": "AUTORIZADO", "customer": "", "cfop": "",
        })
        raw_rows.append(cells)
    return records, raw_rows


def _declared_totals(lines: List[List[str]]) -> Dict[str, Any]:
    """Totais impressos no relatório, ex.: 'Total de documentos: 2438' e 'Totais: 67.007,510'."""
    count, total = None, None
    for cells in lines:
        cells = _split_pipes(cells)
        for i, c in enumerate(cells):
            n = _norm(c)
            inline = re.search(r":\s*([\d.,]+)\s*$", c)
            src = inline.group(1) if inline else (cells[i + 1] if i + 1 < len(cells) else "")
            if n.startswith("total de documentos") or n.startswith("quantidade de documentos") or n.startswith("qtd documentos"):
                digits = re.sub(r"\D", "", src)
                if digits:
                    count = int(digits)
            elif n in ("totais", "total geral", "total do periodo"):
                v = parse_br_number(src)
                if v is not None:
                    total = v
    return {"count": count, "total": total}


def parse_report_lines(lines: List[List[str]], page_count: int = 1) -> Dict[str, Any]:
    records, mapping, raw_rows, header = _rows_by_header(lines)
    method = "cabecalho"
    if not records:
        records, raw_rows = _rows_by_heuristic(lines)
        mapping, header, method = {}, [], "heuristica"

    # Evita duplicar a mesma linha (cabeçalho/rodapé repetidos em cada página)
    seen = set()
    final_records, final_rows = [], []
    for r, cells in zip(records, raw_rows):
        k = (r["model"], r["series"], r["number"], r["issueDate"], r["totalValue"])
        if k in seen:
            continue
        seen.add(k)
        idx = len(final_records) + 1
        final_records.append(dict(r, id=f"pdf_row_{idx}", lineNumber=idx, rawCells=cells))
        final_rows.append(cells)

    declared = _declared_totals(lines)
    auth = [r for r in final_records if r["status"] == "AUTORIZADO"]
    total_all = round(sum(r["totalValue"] for r in final_records), 2)
    checks = []
    if declared["count"] is not None:
        checks.append({"label": "Quantidade de notas", "report": declared["count"],
                       "extracted": len(final_records), "ok": declared["count"] == len(final_records)})
    if declared["total"] is not None:
        checks.append({"label": "Valor total", "report": declared["total"],
                       "extracted": total_all, "ok": abs(declared["total"] - total_all) < 0.05})

    full_map = {k: -1 for k in ("dateCol", "authDateCol", "numberCol", "seriesCol", "modelCol", "valueCol",
                                "paymentCol", "statusCol", "accessKeyCol", "customerCol")}
    full_map.update(mapping)
    return {
        "extractionMethod": method,
        "columnsMeta": [{"index": i, "label": h, "sample": final_rows[0][i] if final_rows and i < len(final_rows[0]) else ""}
                        for i, h in enumerate(header)],
        "detectedMapping": full_map,
        "rawRows": final_rows,
        "records": final_records,
        "reportChecks": checks,
        "summary": {
            "pageCount": page_count,
            "totalRowsExtracted": len(final_records),
            "authorizedCount": len(auth),
            "canceledCount": sum(1 for r in final_records if r["status"] == "CANCELADO"),
            "totalAuthorizedValue": round(sum(r["totalValue"] for r in auth), 2),
            "nfe55AuthorizedValue": round(sum(r["totalValue"] for r in auth if r["model"] == "55"), 2),
            "nfce65AuthorizedValue": round(sum(r["totalValue"] for r in auth if r["model"] == "65"), 2),
            "totalAllValue": total_all,
            "reportDeclaredCount": declared["count"],
            "reportDeclaredTotal": declared["total"],
        },
    }


def text_to_lines(raw_text: str) -> List[List[str]]:
    return [_split_pipes([l.strip()]) for l in raw_text.splitlines() if l.strip()]


def parse_sales_pdf(pdf_bytes: bytes, file_name: str) -> Dict[str, Any]:
    try:
        pages, page_count = extract_pdf_lines(pdf_bytes)
    except Exception:
        pages, page_count = [], 1
    lines = [cells for page in pages for cells in page]
    raw_text = "\n".join(" | ".join(c) for c in lines)
    base = {
        "fileName": file_name,
        "sizeBytes": len(pdf_bytes),
        "sha256": hashlib.sha256(pdf_bytes).hexdigest(),
        "pageCount": page_count,
        "ocrExecuted": False,
    }

    if len(re.sub(r"\s+", "", raw_text)) < 25:
        return {
            **base,
            "isScannedImagePdf": True,
            "scannedWarning": ("Este PDF não tem texto (parece uma imagem escaneada). Gere o relatório de novo no "
                               "sistema usando 'Exportar / Salvar como PDF' em vez de escanear."),
            "rawTextPreview": "",
            "records": [], "rawRows": [], "columnsMeta": [], "detectedMapping": {}, "reportChecks": [],
            "summary": {"pageCount": page_count, "totalRowsExtracted": 0, "authorizedCount": 0, "canceledCount": 0,
                        "totalAuthorizedValue": 0.0, "totalAllValue": 0.0},
        }

    structured = parse_report_lines(lines, page_count)
    warning = None
    if not structured["records"]:
        warning = ("O PDF tem texto, mas não encontrei uma tabela de vendas (colunas Número, Data e Valor). "
                   "Confira se é o relatório de vendas correto.")
    return {**base, "isScannedImagePdf": False, "scannedWarning": warning, "rawTextPreview": raw_text[:4000], **structured}
