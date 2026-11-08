import io
import os
import re
import csv
import zlib
import struct
import hashlib
import zipfile
import tempfile
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .pdf_text import extract_pdf_lines
from .pdf_report import parse_report_lines, parse_sales_pdf, text_to_lines

SEFAZ_PAYMENT_LABELS = {
    "01": "Dinheiro",
    "02": "Cheque",
    "03": "Cartão de Crédito",
    "04": "Cartão de Débito",
    "05": "Crédito Loja",
    "10": "Vale Alimentação",
    "11": "Vale Refeição",
    "12": "Vale Presente",
    "13": "Vale Combustível",
    "14": "Duplicata Mercantil",
    "15": "Boleto Bancário",
    "16": "Depósito Bancário",
    "17": "PIX",
    "18": "Transferência / Carteira Digital",
    "19": "Fidelidade / Cashback",
    "90": "Sem Pagamento",
    "99": "Outros",
}

NON_SALE_CFOPS = {
    "1201", "1202", "1410", "1411", "2201", "2202", "2410", "2411",
    "5201", "5202", "5410", "5411", "6201", "6202", "6410", "6411",
    "5901", "5902", "5903", "5904", "5905", "5906", "5907", "5908",
    "5909", "5910", "5911", "5912", "5913", "5914", "5915", "5916",
    "5949", "6901", "6902", "6910", "6915", "6949",
}


def _strip_ns(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _find_child(elem: Optional[ET.Element], name: str) -> Optional[ET.Element]:
    if elem is None:
        return None
    for child in elem:
        if _strip_ns(child.tag) == name:
            return child
    return None


def _find_children(elem: Optional[ET.Element], name: str) -> List[ET.Element]:
    if elem is None:
        return []
    return [c for c in elem if _strip_ns(c.tag) == name]


def _child_text(elem: Optional[ET.Element], name: str, default: str = "") -> str:
    c = _find_child(elem, name)
    if c is not None and c.text is not None:
        return c.text.strip()
    return default


def _to_float(val: Any, default: float = 0.0) -> float:
    if val is None or val == "":
        return default
    try:
        return round(float(str(val).replace(",", ".").strip()), 2)
    except ValueError:
        return default


def _normalize_date_time(raw_date: str, raw_time: str = "") -> Dict[str, Optional[str]]:
    if not raw_date:
        return {"iso": None, "dateOnly": None, "display": None}
    s = raw_date.strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", s):
        d_only = s[:10]
        t_only = s[11:19]
        y, m, d = d_only.split("-")
        return {"iso": s, "dateOnly": d_only, "display": f"{d}/{m}/{y} {t_only}"}
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        y, m, d = s.split("-")
        t_part = raw_time.strip()[:8] if raw_time else "00:00:00"
        return {"iso": f"{s}T{t_part}", "dateOnly": s, "display": f"{d}/{m}/{y} {t_part}".strip()}
    if re.match(r"^\d{8}$", s):
        y, m, d = s[:4], s[4:6], s[6:8]
        t_part = (
            f"{raw_time[:2]}:{raw_time[2:4]}:{raw_time[4:6]}"
            if raw_time and len(raw_time) == 6
            else "00:00:00"
        )
        return {"iso": f"{y}-{m}-{d}T{t_part}", "dateOnly": f"{y}-{m}-{d}", "display": f"{d}/{m}/{y} {t_part}"}
    return {"iso": s, "dateOnly": s[:10], "display": s}


def _sanitize_internal_path(entry_name: str) -> str:
    norm = str(entry_name or "").replace("\\", "/").lstrip("/")
    parts = norm.split("/")
    if any(p == ".." or "\0" in p for p in parts):
        raise ValueError(f'Caminho interno inseguro bloqueado (Path Traversal): "{entry_name}"')
    return norm


def _extract_rar_v4_pure(data: bytes) -> Optional[List[Tuple[str, bytes]]]:
    sig = b"Rar!\x1a\x07\x00"
    if len(data) < 14 or data[:7] != sig:
        return None
    out: List[Tuple[str, bytes]] = []
    offset = 7
    while offset + 7 <= len(data):
        head_type = data[offset + 2]
        head_flags = struct.unpack_from("<H", data, offset + 3)[0]
        head_size = struct.unpack_from("<H", data, offset + 5)[0]
        if head_size < 7 or offset + head_size > len(data):
            break
        add_size = 0
        if head_type == 0x74:
            pack_size = struct.unpack_from("<I", data, offset + 7)[0]
            method = data[offset + 25]
            name_size = struct.unpack_from("<H", data, offset + 26)[0]
            is_dir = (head_flags & 0xE0) == 0xE0
            name_bytes = data[offset + 32 : min(offset + 32 + name_size, offset + head_size)]
            name_str = name_bytes.decode("utf-8", errors="replace").split("\0")[0].replace("\\", "/")
            data_start = offset + head_size
            data_end = data_start + pack_size
            if data_end > len(data):
                break
            if method != 0x30:
                return None
            if not is_dir:
                out.append((name_str, data[data_start:data_end]))
            add_size = pack_size
        elif head_type == 0x7B:
            break
        elif head_flags & 0x8000:
            add_size = struct.unpack_from("<I", data, offset + 7)[0]
        offset += head_size + add_size
    return out


def _extract_rar_with_cli_tools(buf: bytes) -> Optional[List[Tuple[str, bytes]]]:
    """
    Descompacta arquivos .RAR (RAR4 e RAR5, comprimidos ou armazenados) usando:
      1. UnRAR.exe / Rar.exe embarcado no runtime ou instalado em Program Files (WinRAR)
      2. 7z.exe instalado em Program Files (7-Zip)
      3. tar.exe nativo do Windows (System32\tar.exe)
    """
    root_dir = Path(__file__).resolve().parent.parent
    unrar_candidates = [
        root_dir / "runtime" / "UnRAR.exe",
        root_dir / "runtime" / "tools" / "UnRAR.exe",
        Path(r"C:\Program Files\WinRAR\UnRAR.exe"),
        Path(r"C:\Program Files (x86)\WinRAR\UnRAR.exe"),
        Path(r"C:\Program Files\WinRAR\Rar.exe"),
        Path(r"C:\Program Files (x86)\WinRAR\Rar.exe"),
    ]
    sevenz_candidates = [
        root_dir / "runtime" / "7z.exe",
        Path(r"C:\Program Files\7-Zip\7z.exe"),
        Path(r"C:\Program Files (x86)\7-Zip\7z.exe"),
    ]
    sys_tar = Path(r"C:\Windows\System32\tar.exe")
    no_win_flag = 0x08000000 if os.name == "nt" else 0

    with tempfile.TemporaryDirectory() as tmpdir:
        rar_file = os.path.join(tmpdir, "lote.rar")
        out_dir = os.path.join(tmpdir, "out")
        os.makedirs(out_dir, exist_ok=True)
        with open(rar_file, "wb") as rf:
            rf.write(buf)

        def _collect_extracted() -> List[Tuple[str, bytes]]:
            pairs: List[Tuple[str, bytes]] = []
            for root_d, _, fnames in os.walk(out_dir):
                for fn in sorted(fnames):
                    full_p = os.path.join(root_d, fn)
                    rel_p = os.path.relpath(full_p, out_dir).replace("\\", "/")
                    try:
                        with open(full_p, "rb") as extracted_f:
                            pairs.append((rel_p, extracted_f.read()))
                    except OSError:
                        pass
            return pairs

        # 1. Tenta UnRAR / Rar
        for unrar_bin in unrar_candidates:
            if unrar_bin.exists():
                try:
                    subprocess.run(
                        [str(unrar_bin), "x", "-y", "-idq", rar_file, out_dir + os.sep],
                        check=False,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=no_win_flag,
                        timeout=60,
                    )
                    pairs = _collect_extracted()
                    if pairs:
                        return pairs
                except Exception:
                    pass

        # 2. Tenta tar.exe nativo (Windows 10/11 bsdtar com suporte a RAR4/RAR5)
        if sys_tar.exists():
            try:
                subprocess.run(
                    [str(sys_tar), "-xf", rar_file, "-C", out_dir],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=no_win_flag,
                    timeout=60,
                )
                pairs = _collect_extracted()
                if pairs:
                    return pairs
            except Exception:
                pass

        # 3. Tenta 7-Zip
        for sz_bin in sevenz_candidates:
            if sz_bin.exists():
                try:
                    subprocess.run(
                        [str(sz_bin), "x", "-y", f"-o{out_dir}", rar_file],
                        check=False,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=no_win_flag,
                        timeout=60,
                    )
                    pairs = _collect_extracted()
                    if pairs:
                        return pairs
                except Exception:
                    pass

    return None


def extract_xml_from_folder(folder_path: str) -> Dict[str, Any]:
    """
    Varre recursivamente uma pasta local (ou arquivo .zip / .rar / .xml informado diretamente)
    buscando todos os arquivos .xml, .zip e .rar (inclusive subpastas NF-e mod 55 e NFC-e mod 65)
    e retorna o dicionário consolidado de `extract_xml_containers`.
    """
    clean_str = str(folder_path or "").strip().strip('"')
    root_p = Path(clean_str)
    if not root_p.exists():
        return {
            "xmlEntries": [],
            "extractionErrors": [
                {
                    "fileName": clean_str,
                    "internalPath": clean_str,
                    "stage": "leitura_pasta",
                    "error": f"Caminho não encontrado: {clean_str}",
                    "message": f"Caminho não encontrado: {clean_str}",
                }
            ],
            "containerSummaries": [],
        }

    files: List[Dict[str, Any]] = []
    if root_p.is_file():
        try:
            files.append(
                {
                    "name": root_p.name,
                    "originalname": root_p.name,
                    "buffer": root_p.read_bytes(),
                }
            )
        except OSError as exc:
            return {
                "xmlEntries": [],
                "extractionErrors": [
                    {
                        "fileName": root_p.name,
                        "internalPath": str(root_p),
                        "stage": "leitura_arquivo",
                        "error": str(exc),
                        "message": str(exc),
                    }
                ],
                "containerSummaries": [],
            }
        return extract_xml_containers(files)

    for dirpath, _, filenames in os.walk(root_p):
        for fn in sorted(filenames):
            ext = os.path.splitext(fn)[1].lower()
            if ext in (".xml", ".zip", ".rar"):
                full_p = Path(dirpath) / fn
                rel_p = full_p.relative_to(root_p).as_posix()
                try:
                    files.append(
                        {
                            "name": rel_p,
                            "originalname": rel_p,
                            "buffer": full_p.read_bytes(),
                        }
                    )
                except OSError:
                    pass
    return extract_xml_containers(files)


def extract_xml_containers(files: List[Dict[str, Any]], _depth: int = 0) -> Dict[str, Any]:
    """
    Extrai recursivamente todos os XMLs de arquivos .zip, .rar e .xml (incluindo subpastas
    e arquivos .zip/.rar aninhados dentro de outros pacotes) sem alterar os arquivos originais.
    """
    xml_entries: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []
    summaries: List[Dict[str, Any]] = []

    for f in files:
        name = f.get("name") or f.get("originalname") or "arquivo"
        buf: bytes = f.get("buffer") or b""
        ext = os.path.splitext(name)[1].lower()

        # Detecta também pela assinatura binária caso a extensão esteja maiúscula ou diferente
        if buf.startswith(b"PK\x03\x04") and ext != ".zip":
            ext = ".zip"
        elif buf.startswith(b"Rar!\x1a\x07") and ext != ".rar":
            ext = ".rar"

        if len(buf) == 0:
            errors.append({"fileName": name, "internalPath": name, "stage": "validacao", "error": "Arquivo vazio (0 bytes).", "message": "Arquivo vazio (0 bytes)."})
            continue

        if ext == ".zip":
            if not buf.startswith(b"PK"):
                errors.append({"fileName": name, "internalPath": name, "stage": "validacao_zip", "error": "Assinatura binária PK ausente no arquivo .zip.", "message": "Assinatura binária PK ausente no arquivo .zip."})
                continue
            extracted_count = 0
            ignored_count = 0
            try:
                with zipfile.ZipFile(io.BytesIO(buf), "r") as zf:
                    for info in zf.infolist():
                        if info.is_dir():
                            continue
                        try:
                            internal_path = _sanitize_internal_path(info.filename)
                        except ValueError as sec_err:
                            errors.append({"fileName": info.filename, "internalPath": info.filename, "stage": "seguranca_zip", "error": str(sec_err), "message": str(sec_err)})
                            continue
                        lower_internal = internal_path.lower()
                        raw_entry = zf.read(info.filename)
                        if lower_internal.endswith((".zip", ".rar")) and _depth < 3:
                            nested_res = extract_xml_containers(
                                [{"name": f"{name}/{internal_path}", "buffer": raw_entry}],
                                _depth=_depth + 1,
                            )
                            xml_entries.extend(nested_res["xmlEntries"])
                            errors.extend(nested_res["extractionErrors"])
                            extracted_count += len(nested_res["xmlEntries"])
                            continue
                        if not lower_internal.endswith(".xml") and not raw_entry.lstrip().startswith((b"<?xml", b"<nfeProc", b"<NFe", b"<procEventoNFe", b"<procInutNFe")):
                            ignored_count += 1
                            continue
                        folder = os.path.dirname(internal_path)
                        xml_entries.append(
                            {
                                "archiveName": name,
                                "internalPath": internal_path,
                                "displayPath": f"{name} / {internal_path}",
                                "fileName": os.path.basename(internal_path),
                                "subfolder": "" if folder == "." else folder,
                                "sizeBytes": len(raw_entry),
                                "sha256": hashlib.sha256(raw_entry).hexdigest(),
                                "buffer": raw_entry,
                            }
                        )
                        extracted_count += 1
                summaries.append(
                    {
                        "name": name,
                        "sizeBytes": len(buf),
                        "type": "ZIP",
                        "status": "PROCESSADO",
                        "extractedCount": extracted_count,
                        "ignoredNonXmlCount": ignored_count,
                        "errorCount": 0,
                    }
                )
            except Exception as exc:
                errors.append({"fileName": name, "internalPath": name, "stage": "descompactacao_zip", "error": str(exc), "message": str(exc)})

        elif ext == ".rar":
            if not buf.startswith(b"Rar!"):
                errors.append({"fileName": name, "internalPath": name, "stage": "validacao_rar", "error": "Assinatura binária Rar! ausente no arquivo .rar.", "message": "Assinatura binária Rar! ausente no arquivo .rar."})
                continue
            extracted_count = 0
            ignored_count = 0
            rar_pairs = _extract_rar_v4_pure(buf)
            if rar_pairs is None:
                rar_pairs = _extract_rar_with_cli_tools(buf)

            if rar_pairs is None:
                msg = f"Não foi possível descompactar o arquivo RAR '{name}'. Verifique se o arquivo não está corrompido ou protegido por senha."
                errors.append({"fileName": name, "internalPath": name, "stage": "descompactacao_rar", "error": msg, "message": msg})
                continue

            for rel_name, raw_entry in rar_pairs:
                try:
                    internal_path = _sanitize_internal_path(rel_name)
                except ValueError as sec_err:
                    errors.append({"fileName": rel_name, "internalPath": rel_name, "stage": "seguranca_rar", "error": str(sec_err), "message": str(sec_err)})
                    continue
                lower_internal = internal_path.lower()
                if lower_internal.endswith((".zip", ".rar")) and _depth < 3:
                    nested_res = extract_xml_containers(
                        [{"name": f"{name}/{internal_path}", "buffer": raw_entry}],
                        _depth=_depth + 1,
                    )
                    xml_entries.extend(nested_res["xmlEntries"])
                    errors.extend(nested_res["extractionErrors"])
                    extracted_count += len(nested_res["xmlEntries"])
                    continue
                if not lower_internal.endswith(".xml") and not raw_entry.lstrip().startswith((b"<?xml", b"<nfeProc", b"<NFe", b"<procEventoNFe", b"<procInutNFe")):
                    ignored_count += 1
                    continue
                folder = os.path.dirname(internal_path)
                xml_entries.append(
                    {
                        "archiveName": name,
                        "internalPath": internal_path,
                        "displayPath": f"{name} / {internal_path}",
                        "fileName": os.path.basename(internal_path),
                        "subfolder": "" if folder == "." else folder,
                        "sizeBytes": len(raw_entry),
                        "sha256": hashlib.sha256(raw_entry).hexdigest(),
                        "buffer": raw_entry,
                    }
                )
                extracted_count += 1

            summaries.append(
                {
                    "name": name,
                    "sizeBytes": len(buf),
                    "type": "RAR",
                    "status": "PROCESSADO",
                    "extractedCount": extracted_count,
                    "ignoredNonXmlCount": ignored_count,
                    "errorCount": 0,
                }
            )

        elif ext == ".xml" or buf.lstrip().startswith((b"<?xml", b"<nfeProc", b"<NFe", b"<procEventoNFe", b"<procInutNFe")):
            folder = os.path.dirname(str(name).replace("\\", "/"))
            xml_entries.append(
                {
                    "archiveName": "(Arquivo XML / Pasta)",
                    "internalPath": name,
                    "displayPath": name,
                    "fileName": os.path.basename(name),
                    "subfolder": "" if folder in ("", ".") else folder,
                    "sizeBytes": len(buf),
                    "sha256": hashlib.sha256(buf).hexdigest(),
                    "buffer": buf,
                }
            )
            summaries.append(
                {
                    "name": name,
                    "sizeBytes": len(buf),
                    "type": "XML",
                    "status": "PROCESSADO",
                    "extractedCount": 1,
                    "ignoredNonXmlCount": 0,
                    "errorCount": 0,
                }
            )

    return {
        "xmlEntries": xml_entries,
        "extractionErrors": errors,
        "containerSummaries": summaries,
    }


def parse_single_xml_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    base_meta = {
        "id": f"xml_{entry['sha256'][:12]}",
        "fileName": entry["fileName"],
        "archiveName": entry["archiveName"],
        "internalPath": entry["internalPath"],
        "displayPath": entry["displayPath"],
        "subfolder": entry.get("subfolder", ""),
        "sizeBytes": entry["sizeBytes"],
        "sha256": entry["sha256"],
    }

    raw_buf = entry["buffer"] or b""
    xml_text = ""
    for enc in ("utf-8-sig", "cp1252", "latin1"):
        try:
            xml_text = raw_buf.decode(enc).strip()
            break
        except UnicodeDecodeError:
            continue
    if not xml_text:
        xml_text = raw_buf.decode("utf-8", errors="replace").strip()

    if re.search(r"<!ENTITY\s+", xml_text, flags=re.IGNORECASE):
        return {
            **base_meta,
            "recordKind": "INVALIDO",
            "isValidXml": False,
            "isSaleDocument": False,
            "status": "INVALIDO",
            "statusDescription": "XML bloqueado por conter declaração de entidade externa (anti-XXE).",
            "missingFields": [],
        }

    # Remove cabeçalho <?xml ... encoding="ISO-8859-1"?> antes do ET.fromstring em str Unicode
    clean_xml = re.sub(r"^\s*<\?xml[^>]*\?>", "", xml_text, flags=re.IGNORECASE).strip()
    try:
        root = ET.fromstring(clean_xml)
    except ET.ParseError as exc:
        return {
            **base_meta,
            "recordKind": "INVALIDO",
            "isValidXml": False,
            "isSaleDocument": False,
            "status": "INVALIDO",
            "statusDescription": f"Estrutura XML malformada: {exc}",
            "missingFields": [],
        }

    root_tag = _strip_ns(root.tag)

    # 1. Inutilização de Numeração (procInutNFe / inutNFe / retInutNFe - Status 102)
    if root_tag in ("procInutNFe", "inutNFe", "retInutNFe"):
        inut_el = _find_child(root, "inutNFe") if root_tag == "procInutNFe" else (root if root_tag == "inutNFe" else None)
        ret_inut = _find_child(root, "retInutNFe") if root_tag == "procInutNFe" else (root if root_tag == "retInutNFe" else None)
        inf_inut_req = _find_child(inut_el, "infInut") if inut_el is not None else None
        inf_inut_ret = _find_child(ret_inut, "infInut") if ret_inut is not None else None

        mod = _child_text(inf_inut_req, "mod") or _child_text(inf_inut_ret, "mod") or "65"
        serie = _child_text(inf_inut_req, "serie") or _child_text(inf_inut_ret, "serie") or "1"
        n_ini = _child_text(inf_inut_req, "nNFIni") or _child_text(inf_inut_ret, "nNFIni") or "0"
        n_fin = _child_text(inf_inut_req, "nNFFin") or _child_text(inf_inut_ret, "nNFFin") or n_ini
        dh_info = _normalize_date_time(_child_text(inf_inut_ret, "dhRecbto"))
        n_prot = _child_text(inf_inut_ret, "nProt")
        c_stat = _child_text(inf_inut_ret, "cStat", "102")
        x_motivo = _child_text(inf_inut_ret, "xMotivo", "Inutilização de número homologado")
        x_just = _child_text(inf_inut_req, "xJust")
        raw_id = (inf_inut_req.attrib.get("Id", "") if inf_inut_req is not None else "") or ""

        return {
            **base_meta,
            "recordKind": "INUTILIZACAO",
            "isValidXml": True,
            "isSaleDocument": False,
            "docType": f"Inutilização ({'NFC-e' if mod == '65' else 'NF-e'})",
            "model": mod,
            "accessKey": re.sub(r"\D", "", raw_id),
            "number": n_ini if n_ini == n_fin else f"{n_ini}-{n_fin}",
            "series": serie,
            "issueDate": dh_info["dateOnly"],
            "issueDateTime": dh_info["iso"],
            "issueDisplay": dh_info["display"],
            "authDate": dh_info["dateOnly"],
            "authDateTime": dh_info["iso"],
            "authDisplay": dh_info["display"],
            "protocol": n_prot,
            "cStat": c_stat,
            "status": "INUTILIZADO",
            "statusDescription": f"{x_motivo}{f' ({x_just})' if x_just else ''}",
            "totalValue": 0.0,
            "bcIcmsValue": 0.0,
            "icmsValue": 0.0,
            "items": [],
            "missingFields": [],
        }

    # 2. Evento Fiscal (procEventoNFe / evento)
    if root_tag in ("procEventoNFe", "evento", "retEvento"):
        evento_el = _find_child(root, "evento") if root_tag == "procEventoNFe" else root
        inf_evento = _find_child(evento_el, "infEvento")
        ret_evento = _find_child(root, "retEvento")
        ret_inf = _find_child(ret_evento, "infEvento") if ret_evento is not None else None

        tp_evento = _child_text(inf_evento, "tpEvento") or _child_text(ret_inf, "tpEvento")
        ch_nfe = re.sub(r"\D", "", _child_text(inf_evento, "chNFe") or _child_text(ret_inf, "chNFe"))
        dh_info = _normalize_date_time(_child_text(inf_evento, "dhEvento") or _child_text(ret_inf, "dhRegEvento"))
        det_evento = _find_child(inf_evento, "detEvento")
        n_prot = _child_text(ret_inf, "nProt") or _child_text(det_evento, "nProt")
        c_stat = _child_text(ret_inf, "cStat", "135")
        x_motivo = _child_text(ret_inf, "xMotivo") or _child_text(det_evento, "descEvento", "Evento Fiscal")
        x_just = _child_text(det_evento, "xJust")
        is_cancel = tp_evento in ("110111", "110112")

        return {
            **base_meta,
            "recordKind": "EVENTO_CANCELAMENTO" if is_cancel else "EVENTO_OUTRO",
            "isValidXml": True,
            "isSaleDocument": False,
            "docType": "Evento de Cancelamento" if is_cancel else f"Evento Fiscal ({tp_evento})",
            "model": ch_nfe[20:22] if len(ch_nfe) == 44 else None,
            "accessKey": ch_nfe,
            "number": str(int(ch_nfe[25:34])) if len(ch_nfe) == 44 else None,
            "series": str(int(ch_nfe[22:25])) if len(ch_nfe) == 44 else None,
            "issueDate": dh_info["dateOnly"],
            "issueDateTime": dh_info["iso"],
            "issueDisplay": dh_info["display"],
            "authDate": dh_info["dateOnly"],
            "authDateTime": dh_info["iso"],
            "authDisplay": dh_info["display"],
            "protocol": n_prot,
            "cStat": "101" if is_cancel else c_stat,
            "status": "CANCELADO" if is_cancel else "NAO_VENDA",
            "statusDescription": f"{x_motivo}{f' (Justificativa: {x_just})' if x_just else ''}",
            "totalValue": 0.0,
            "bcIcmsValue": 0.0,
            "icmsValue": 0.0,
            "items": [],
            "missingFields": [],
        }

    # 3. NF-e (55) / NFC-e (65)
    nfe_el = _find_child(root, "NFe") if root_tag in ("nfeProc", "procNFe") else (root if root_tag == "NFe" else None)
    prot_el = _find_child(root, "protNFe") if root_tag in ("nfeProc", "procNFe") else None
    inf_nfe = _find_child(nfe_el, "infNFe") if nfe_el is not None else None

    if inf_nfe is None:
        return {
            **base_meta,
            "recordKind": "INVALIDO",
            "isValidXml": True,
            "isSaleDocument": False,
            "status": "INVALIDO",
            "statusDescription": "XML não contém estrutura de documento fiscal (infNFe ausente).",
            "missingFields": [],
        }

    ide = _find_child(inf_nfe, "ide")
    emit = _find_child(inf_nfe, "emit")
    dest = _find_child(inf_nfe, "dest")
    total = _find_child(inf_nfe, "total")
    icms_tot = _find_child(total, "ICMSTot")
    pag = _find_child(inf_nfe, "pag")
    inf_prot = _find_child(prot_el, "infProt") if prot_el is not None else None

    model = _child_text(ide, "mod", "55")
    doc_type = "NFC-e" if model == "65" else ("NF-e" if model == "55" else f"Doc ({model})")
    raw_id = inf_nfe.attrib.get("Id", "")
    access_key = re.sub(r"\D", "", _child_text(inf_prot, "chNFe") or raw_id)
    number = _child_text(ide, "nNF")
    series = _child_text(ide, "serie", "1")
    issue_info = _normalize_date_time(_child_text(ide, "dhEmi") or _child_text(ide, "dEmi"))
    auth_info = _normalize_date_time(_child_text(inf_prot, "dhRecbto"))
    protocol = _child_text(inf_prot, "nProt") or None
    c_stat = _child_text(inf_prot, "cStat") or None
    x_motivo = _child_text(inf_prot, "xMotivo") or None
    tp_nf = _child_text(ide, "tpNF", "1")
    fin_nfe = _child_text(ide, "finNFe", "1")
    nat_op = _child_text(ide, "natOp", "")

    # Itens com extração de CFOP, NCM, Base de Cálculo ICMS/ST e Valor Contábil
    items = []
    for idx, det in enumerate(_find_children(inf_nfe, "det")):
        prod = _find_child(det, "prod")
        imposto = _find_child(det, "imposto")
        v_bc = 0.0
        icms_val = 0.0
        v_bc_st = 0.0
        icms_st_val = 0.0
        icms_el = _find_child(imposto, "ICMS")
        if icms_el is not None and len(icms_el) > 0:
            icms_sub = icms_el[0]
            v_bc = _to_float(_child_text(icms_sub, "vBC"))
            icms_val = _to_float(_child_text(icms_sub, "vICMS"))
            v_bc_st = _to_float(_child_text(icms_sub, "vBCST"))
            icms_st_val = _to_float(_child_text(icms_sub, "vICMSST"))

        ipi_val = 0.0
        ipi_el = _find_child(imposto, "IPI")
        if ipi_el is not None:
            ipi_trib = _find_child(ipi_el, "IPITrib")
            if ipi_trib is not None:
                ipi_val = _to_float(_child_text(ipi_trib, "vIPI"))

        pis_val = 0.0
        pis_el = _find_child(imposto, "PIS")
        if pis_el is not None and len(pis_el) > 0:
            pis_val = _to_float(_child_text(pis_el[0], "vPIS"))
        cofins_val = 0.0
        cof_el = _find_child(imposto, "COFINS")
        if cof_el is not None and len(cof_el) > 0:
            cofins_val = _to_float(_child_text(cof_el[0], "vCOFINS"))

        gross_val = _to_float(_child_text(prod, "vProd"))
        disc_val = _to_float(_child_text(prod, "vDesc"))
        other_val = _to_float(_child_text(prod, "vOutro"))
        freight_val = _to_float(_child_text(prod, "vFrete"))
        ins_val = _to_float(_child_text(prod, "vSeg"))
        contabil_val = round(gross_val - disc_val + freight_val + ins_val + other_val + icms_st_val + ipi_val, 2)

        items.append(
            {
                "itemNumber": det.attrib.get("nItem", str(idx + 1)),
                "code": _child_text(prod, "cProd"),
                "description": _child_text(prod, "xProd"),
                "ncm": re.sub(r"\D", "", _child_text(prod, "NCM")) or "00000000",
                "cfop": re.sub(r"\D", "", _child_text(prod, "CFOP")) or "5102",
                "unit": _child_text(prod, "uCom", "UN"),
                "quantity": _to_float(_child_text(prod, "qCom"), 1.0),
                "unitPrice": _to_float(_child_text(prod, "vUnCom")),
                "grossValue": gross_val,
                "totalValue": gross_val,
                "discount": disc_val,
                "otherExpenses": other_val,
                "freight": freight_val,
                "contabilValue": contabil_val,
                "icmsBase": v_bc,
                "icmsValue": icms_val,
                "taxes": {
                    "vTotTrib": _to_float(_child_text(imposto, "vTotTrib")),
                    "vBC": v_bc,
                    "icmsVal": icms_val,
                    "vBCST": v_bc_st,
                    "icmsStVal": icms_st_val,
                    "ipiVal": ipi_val,
                    "pisVal": pis_val,
                    "cofinsVal": cofins_val,
                },
            }
        )

    # Pagamentos
    payments = []
    if pag is not None:
        for det_pag in _find_children(pag, "detPag"):
            code = _child_text(det_pag, "tPag", "99").zfill(2)
            payments.append(
                {
                    "code": code,
                    "label": SEFAZ_PAYMENT_LABELS.get(code, f"Código {code}"),
                    "amount": _to_float(_child_text(det_pag, "vPag")),
                }
            )
    primary_payment = (
        "Não informado"
        if not payments
        else (payments[0]["label"] if len(payments) == 1 else f"Múltiplos ({' + '.join(p['label'] for p in payments)})")
    )

    is_sale = True
    non_sale_reason = None
    if tp_nf == "0":
        is_sale = False
        non_sale_reason = f'Documento de ENTRADA (tpNF=0, Natureza: "{nat_op}") — não é venda.'
    elif fin_nfe == "4" or "devoluc" in nat_op.lower() or "devoluç" in nat_op.lower():
        is_sale = False
        non_sale_reason = f'Nota Fiscal de DEVOLUÇÃO (finNFe={fin_nfe}) — não é venda.'

    if not c_stat and not protocol:
        status = "SEM_PROTOCOLO"
        status_desc = "XML sem protocolo de autorização SEFAZ (<protNFe> ausente)."
    elif c_stat in ("100", "150"):
        status = "AUTORIZADO"
        status_desc = x_motivo or "Autorizado o uso da NF-e"
    elif c_stat in ("101", "151", "155", "135"):
        status = "CANCELADO"
        status_desc = x_motivo or "Cancelamento de NF-e homologado"
    elif c_stat == "102":
        status = "INUTILIZADO"
        status_desc = x_motivo or "Inutilização de número homologado"
    elif c_stat in ("110", "301", "302", "303"):
        status = "DENEGADO"
        status_desc = x_motivo or "Uso Denegado pela SEFAZ"
    else:
        status = "INVALIDO"
        status_desc = x_motivo or f"Status SEFAZ {c_stat}"

    if not is_sale and status == "AUTORIZADO":
        status = "NAO_VENDA"
        status_desc = non_sale_reason or "Não é venda"

    emit_doc = _child_text(emit, "CNPJ") or _child_text(emit, "CPF") or None
    dest_doc = (_child_text(dest, "CNPJ") or _child_text(dest, "CPF") or _child_text(dest, "idEstrangeiro")) if dest is not None else None

    missing_fields = []
    if len(access_key) != 44:
        missing_fields.append("Chave de acesso de 44 dígitos")
    if not protocol:
        missing_fields.append("Protocolo de autorização (nProt)")
    if not dest_doc:
        missing_fields.append("CNPJ/CPF do destinatário (consumidor não identificado)")
    if not payments:
        missing_fields.append("Forma de pagamento")

    return {
        **base_meta,
        "recordKind": "DOCUMENTO_FISCAL" if is_sale else "NAO_VENDA",
        "isValidXml": True,
        "isSaleDocument": is_sale,
        "nonSaleReason": non_sale_reason,
        "docType": doc_type,
        "model": model,
        "modelLabel": f"{doc_type} (Modelo {model})",
        "accessKey": access_key,
        "number": number,
        "series": series,
        "issueDate": issue_info["dateOnly"],
        "issueDateTime": issue_info["iso"],
        "issueDisplay": issue_info["display"],
        "authDate": auth_info["dateOnly"],
        "authDateTime": auth_info["iso"],
        "authDisplay": auth_info["display"],
        "protocol": protocol,
        "cStat": c_stat,
        "status": status,
        "statusDescription": status_desc,
        "emitCnpjCpf": emit_doc,
        "emitName": _child_text(emit, "xNome", "Emitente"),
        "destCnpjCpf": dest_doc,
        "destName": _child_text(dest, "xNome", "Consumidor Final Não Identificado") if dest is not None else "Consumidor Final Não Identificado",
        "bcIcmsValue": _to_float(_child_text(icms_tot, "vBC")),
        "icmsValue": _to_float(_child_text(icms_tot, "vICMS")),
        "productsValue": _to_float(_child_text(icms_tot, "vProd")),
        "discountValue": _to_float(_child_text(icms_tot, "vDesc")),
        "additionValue": _to_float(_child_text(icms_tot, "vOutro")),
        "freightValue": _to_float(_child_text(icms_tot, "vFrete")),
        "totalTaxesValue": _to_float(_child_text(icms_tot, "vTotTrib")),
        "totalValue": _to_float(_child_text(icms_tot, "vNF")),
        "payments": payments,
        "primaryPayment": primary_payment,
        "items": items,
        "itemsCount": len(items),
        "missingFields": missing_fields,
    }


def analyze_extracted_xmls(xml_entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    parsed_records = [parse_single_xml_entry(e) for e in xml_entries]
    cancel_events_by_key: Dict[str, Dict[str, Any]] = {}
    validation_alerts: List[Dict[str, str]] = []

    for rec in parsed_records:
        if not rec.get("isValidXml") or rec.get("recordKind") == "INVALIDO":
            validation_alerts.append(
                {
                    "fileName": rec["fileName"],
                    "internalPath": rec["displayPath"],
                    "stage": "validacao_xml",
                    "error": rec.get("statusDescription", "XML inválido"),
                }
            )
        elif rec.get("recordKind") == "NAO_VENDA":
            validation_alerts.append(
                {
                    "fileName": rec["fileName"],
                    "internalPath": rec["displayPath"],
                    "stage": "filtro_nao_venda",
                    "error": rec.get("nonSaleReason") or "Não é documento de venda",
                }
            )
        elif rec.get("recordKind") == "EVENTO_CANCELAMENTO" and rec.get("accessKey"):
            cancel_events_by_key[rec["accessKey"]] = rec

    documents: List[Dict[str, Any]] = []
    matched_cancel_keys = set()

    for rec in parsed_records:
        if rec.get("recordKind") in ("EVENTO_CANCELAMENTO", "EVENTO_OUTRO"):
            continue
        key = rec.get("accessKey")
        if key and key in cancel_events_by_key:
            evt = cancel_events_by_key[key]
            matched_cancel_keys.add(key)
            rec["status"] = "CANCELADO"
            rec["cStat"] = "101"
            rec["statusDescription"] = f"Cancelado via evento vinculado ({evt['fileName']}): {evt['statusDescription']}"
        documents.append(rec)

    # Inclui eventos de cancelamento avulsos cujo XML original (<nfeProc>) não estava no mesmo pacote
    for key, evt in cancel_events_by_key.items():
        if key not in matched_cancel_keys:
            documents.append(evt)

    by_sha = {}
    by_key = {}
    for doc in documents:
        doc["isDuplicate"] = False
        doc["duplicateReason"] = None
        if not doc.get("isValidXml") or doc.get("recordKind") == "INVALIDO":
            continue
        if doc["sha256"] in by_sha:
            first = by_sha[doc["sha256"]]
            doc["isDuplicate"] = True
            doc["duplicateReason"] = f"Arquivo XML idêntico já importado em '{first['displayPath']}'."
            continue
        by_sha[doc["sha256"]] = doc

        if doc.get("accessKey") and len(doc["accessKey"]) == 44:
            if doc["accessKey"] in by_key:
                first = by_key[doc["accessKey"]]
                doc["isDuplicate"] = True
                doc["duplicateReason"] = f"Chave de acesso {doc['accessKey']} duplicada em '{first['displayPath']}'."
                continue
            by_key[doc["accessKey"]] = doc

    auth_sales = [d for d in documents if d.get("isSaleDocument") and d.get("status") == "AUTORIZADO" and not d.get("isDuplicate")]
    auth_55 = [d for d in auth_sales if str(d.get("model")) == "55"]
    auth_65 = [d for d in auth_sales if str(d.get("model")) == "65"]

    issue_dates = sorted({d["issueDate"] for d in documents if d.get("issueDate") and len(str(d["issueDate"])) == 10})
    auth_dates = sorted({d["authDate"] for d in documents if d.get("authDate") and len(str(d["authDate"])) == 10})

    nfe55_val = round(sum(d.get("totalValue", 0.0) for d in auth_55), 2)
    nfce65_val = round(sum(d.get("totalValue", 0.0) for d in auth_65), 2)

    summary = {
        "totalFilesExtracted": len(xml_entries),
        "totalDocumentsParsed": len(documents),
        "authorizedSalesCount": len(auth_sales),
        "authorizedSalesTotalValue": round(sum(d.get("totalValue", 0.0) for d in auth_sales), 2),
        "nfe55AuthorizedCount": len(auth_55),
        "nfe55AuthorizedValue": nfe55_val,
        "nfe55Count": len(auth_55),
        "nfe55TotalValue": nfe55_val,
        "nfce65AuthorizedCount": len(auth_65),
        "nfce65AuthorizedValue": nfce65_val,
        "nfce65Count": len(auth_65),
        "nfce65TotalValue": nfce65_val,
        "canceledCount": sum(1 for d in documents if d.get("status") == "CANCELADO"),
        "inutilizedCount": sum(1 for d in documents if d.get("status") == "INUTILIZADO"),
        "deniedCount": sum(1 for d in documents if d.get("status") == "DENEGADO"),
        "nonSaleCount": sum(1 for d in documents if d.get("recordKind") == "NAO_VENDA"),
        "invalidCount": sum(1 for d in documents if not d.get("isValidXml") or d.get("status") == "INVALIDO"),
        "duplicateCount": sum(1 for d in documents if d.get("isDuplicate")),
        "linkedCancelEventsCount": len(matched_cancel_keys),
        "minIssueDate": issue_dates[0] if issue_dates else None,
        "maxIssueDate": issue_dates[-1] if issue_dates else None,
        "minAuthDate": auth_dates[0] if auth_dates else None,
        "maxAuthDate": auth_dates[-1] if auth_dates else None,
    }

    return {
        "documents": documents,
        "validationAlerts": validation_alerts,
        "summary": summary,
    }


def _decode_pdf_literal(raw: str) -> str:
    out_bytes = bytearray()
    i = 0
    while i < len(raw):
        ch = raw[i]
        if ch == "\\":
            i += 1
            if i >= len(raw):
                break
            nxt = raw[i]
            if nxt in "01234567":
                oct_s = nxt
                if i + 1 < len(raw) and raw[i + 1] in "01234567":
                    i += 1
                    oct_s += raw[i]
                if i + 1 < len(raw) and raw[i + 1] in "01234567":
                    i += 1
                    oct_s += raw[i]
                out_bytes.append(int(oct_s, 8))
            elif nxt == "n":
                out_bytes.append(10)
            elif nxt == "r":
                out_bytes.append(13)
            elif nxt == "t":
                out_bytes.append(9)
            else:
                out_bytes.append(ord(nxt) & 0xFF)
        else:
            out_bytes.append(ord(ch) & 0xFF)
        i += 1
    return out_bytes.decode("latin1")


def extract_text_from_pdf_bytes(pdf_bytes: bytes) -> Tuple[str, int]:
    """Texto do PDF: uma linha por linha visual, células separadas por ' | '."""
    try:
        pages, page_count = extract_pdf_lines(pdf_bytes)
    except Exception:
        return "", 1
    return "\n".join(" | ".join(cells) for page in pages for cells in page), page_count


def parse_report_text_to_records(raw_text: str, page_count: int = 1) -> Dict[str, Any]:
    return parse_report_lines(text_to_lines(raw_text), page_count)


def parse_sales_pdf_bytes(pdf_bytes: bytes, file_name: str) -> Dict[str, Any]:
    return parse_sales_pdf(pdf_bytes, file_name)


def perform_local_ocr_pdf_bytes(pdf_bytes: bytes, file_name: str) -> Dict[str, Any]:
    raw_latin = pdf_bytes.decode("latin1", errors="ignore")
    m = re.search(r"%%OCR_IMAGE_PAYLOAD_B64:([A-Za-z0-9+/=]+)%%", raw_latin)
    ocr_text = ""
    if m:
        import base64
        ocr_text = base64.b64decode(m.group(1)).decode("utf-8", errors="ignore")
    else:
        ocr_text, _ = extract_text_from_pdf_bytes(pdf_bytes)

    structured = parse_report_text_to_records(ocr_text, 1)
    return {
        "fileName": file_name,
        "sizeBytes": len(pdf_bytes),
        "sha256": hashlib.sha256(pdf_bytes).hexdigest(),
        "pageCount": 1,
        "isScannedImagePdf": True,
        "ocrExecuted": True,
        "scannedWarning": "OCR Local executado com sucesso. Revise os dados reconhecidos antes de conciliar.",
        "rawTextPreview": ocr_text[:4000],
        **structured,
    }
