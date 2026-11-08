"""
Extrator de texto de PDF usando apenas a biblioteca padrão do Python.

Suporta o que relatórios de ERP (Clipp, FastReport, QuickReport, Crystal etc.) costumam gerar:
- fluxos comprimidos com FlateDecode (inclusive object streams /ObjStm);
- fontes Type0 / Identity-H com tabela /ToUnicode (texto em hexadecimal <0029002F...>);
- fontes simples (WinAnsi) com strings literais (...);
- operadores Tj, TJ, ' e " com posicionamento Td, TD, Tm, T*.

O resultado é uma lista de páginas; cada página é uma lista de linhas e cada linha
é uma lista de células (pedaços de texto ordenados da esquerda para a direita).
"""

from __future__ import annotations

import re
import zlib
from typing import Any, Dict, List, Optional, Tuple

_OBJ_RE = re.compile(rb"(?<![0-9])(\d+)\s+(\d+)\s+obj\b")
_REF_RE = re.compile(r"(\d+)\s+(\d+)\s+R")


# --------------------------------------------------------------------------- objetos

def _decompress(raw: bytes, dict_text: str) -> Optional[bytes]:
    if "/FlateDecode" in dict_text or "/Fl " in dict_text or "/Fl]" in dict_text:
        for candidate in (raw, raw.rstrip(b"\r\n")):
            try:
                return zlib.decompress(candidate)
            except Exception:
                pass
            try:
                return zlib.decompressobj().decompress(candidate)
            except Exception:
                pass
        return None
    if "/Filter" in dict_text:
        return None  # filtro não suportado (ex.: DCTDecode de imagens)
    return raw


def _parse_objects(data: bytes) -> Dict[int, Dict[str, Any]]:
    """Retorna {num: {"dict": str, "stream": bytes|None}}."""
    objs: Dict[int, Dict[str, Any]] = {}
    matches = list(_OBJ_RE.finditer(data))
    for i, m in enumerate(matches):
        num = int(m.group(1))
        start = m.end()
        end = data.find(b"endobj", start)
        if end < 0:
            end = matches[i + 1].start() if i + 1 < len(matches) else len(data)
        body = data[start:end]
        stream = None
        dict_part = body
        sm = re.search(rb"stream\r?\n", body)
        if sm:
            dict_part = body[: sm.start()]
            dict_text = dict_part.decode("latin1", "ignore")
            raw = body[sm.end():]
            es = raw.rfind(b"endstream")
            if es >= 0:
                raw = raw[:es]
            lm = re.search(r"/Length\s+(\d+)(?!\s+\d+\s+R)", dict_text)
            if lm and int(lm.group(1)) <= len(raw):
                raw = raw[: int(lm.group(1))]
            stream = _decompress(raw, dict_text)
        objs[num] = {"dict": dict_part.decode("latin1", "ignore"), "stream": stream}

    # Objetos dentro de object streams (/Type /ObjStm)
    for num, obj in list(objs.items()):
        if "/ObjStm" not in obj["dict"] or obj["stream"] is None:
            continue
        try:
            n = int(re.search(r"/N\s+(\d+)", obj["dict"]).group(1))
            first = int(re.search(r"/First\s+(\d+)", obj["dict"]).group(1))
            header = obj["stream"][:first].split()
            pairs = [(int(header[k]), int(header[k + 1])) for k in range(0, min(len(header), n * 2), 2)]
            for idx, (onum, off) in enumerate(pairs):
                s = first + off
                e = first + pairs[idx + 1][1] if idx + 1 < len(pairs) else len(obj["stream"])
                if onum not in objs:
                    objs[onum] = {"dict": obj["stream"][s:e].decode("latin1", "ignore"), "stream": None}
        except Exception:
            continue
    return objs


def _resolve_dict(objs: Dict[int, Dict[str, Any]], text: str, key: str) -> str:
    """Retorna o conteúdo do dicionário associado a `key` (inline ou por referência)."""
    m = re.search(r"/" + key + r"\s*(\d+\s+\d+\s+R|<<)", text)
    if not m:
        return ""
    if m.group(1) == "<<":
        return _balanced_dict(text, m.start(1))
    ref = int(m.group(1).split()[0])
    return objs.get(ref, {}).get("dict", "")


def _balanced_dict(text: str, pos: int) -> str:
    depth = 0
    i = pos
    while i < len(text) - 1:
        if text[i:i + 2] == "<<":
            depth += 1
            i += 2
            continue
        if text[i:i + 2] == ">>":
            depth -= 1
            i += 2
            if depth == 0:
                return text[pos:i]
            continue
        i += 1
    return text[pos:]


def _page_object_ids(objs: Dict[int, Dict[str, Any]], data: bytes) -> List[int]:
    root = None
    tm = list(re.finditer(rb"/Root\s+(\d+)\s+\d+\s+R", data))
    if tm:
        root = int(tm[-1].group(1))
    else:
        for num, o in objs.items():
            if re.search(r"/Type\s*/Catalog", o["dict"]):
                root = num
                break
    pages: List[int] = []

    def walk(num: int, depth: int = 0) -> None:
        if depth > 40 or num not in objs:
            return
        d = objs[num]["dict"]
        if re.search(r"/Type\s*/Pages\b", d):
            km = re.search(r"/Kids\s*\[([^\]]*)\]", d)
            if km:
                for r in _REF_RE.finditer(km.group(1)):
                    walk(int(r.group(1)), depth + 1)
        elif re.search(r"/Type\s*/Page\b", d):
            pages.append(num)

    if root is not None and root in objs:
        pm = re.search(r"/Pages\s+(\d+)\s+\d+\s+R", objs[root]["dict"])
        if pm:
            walk(int(pm.group(1)))
    if not pages:
        pages = [n for n, o in sorted(objs.items()) if re.search(r"/Type\s*/Page\b", o["dict"])]
    return pages


def _inherited_resources(objs: Dict[int, Dict[str, Any]], page_num: int) -> str:
    num: Optional[int] = page_num
    for _ in range(40):
        if num is None or num not in objs:
            break
        d = objs[num]["dict"]
        res = _resolve_dict(objs, d, "Resources")
        if res:
            return res
        pm = re.search(r"/Parent\s+(\d+)\s+\d+\s+R", d)
        num = int(pm.group(1)) if pm else None
    return ""


# --------------------------------------------------------------------------- fontes

class _Font:
    def __init__(self, two_byte: bool, cmap: Dict[int, str]):
        self.two_byte = two_byte
        self.cmap = cmap

    def decode(self, raw: bytes) -> str:
        out: List[str] = []
        if self.two_byte:
            for i in range(0, len(raw) - 1, 2):
                code = (raw[i] << 8) | raw[i + 1]
                out.append(self.cmap.get(code, chr(code) if 32 <= code < 0xD800 else ""))
        else:
            for b in raw:
                if b in self.cmap:
                    out.append(self.cmap[b])
                else:
                    out.append(bytes([b]).decode("cp1252", "replace"))
        return "".join(out)


def _hex_to_str(h: str) -> str:
    h = re.sub(r"\s+", "", h)
    if len(h) % 4:
        h = h.ljust((len(h) // 4 + 1) * 4, "0")
    try:
        return bytes.fromhex(h).decode("utf-16-be", "ignore")
    except ValueError:
        return ""


def _parse_cmap(text: str) -> Dict[int, str]:
    cmap: Dict[int, str] = {}
    for block in re.findall(r"beginbfchar(.*?)endbfchar", text, re.S):
        for src, dst in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]*)>", block):
            cmap[int(src, 16)] = _hex_to_str(dst)
    for block in re.findall(r"beginbfrange(.*?)endbfrange", text, re.S):
        for m in re.finditer(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(\[[^\]]*\]|<[0-9A-Fa-f]+>)", block):
            lo, hi, dst = int(m.group(1), 16), int(m.group(2), 16), m.group(3)
            if hi - lo > 65535:
                continue
            if dst.startswith("["):
                items = re.findall(r"<([0-9A-Fa-f]+)>", dst)
                for k, item in enumerate(items):
                    cmap[lo + k] = _hex_to_str(item)
            else:
                base = int(dst[1:-1], 16)
                for k in range(hi - lo + 1):
                    cmap[lo + k] = chr(base + k) if base + k < 0x110000 else ""
    return cmap


def _load_fonts(objs: Dict[int, Dict[str, Any]], resources: str) -> Dict[str, _Font]:
    fonts: Dict[str, _Font] = {}
    font_dict = _resolve_dict(objs, resources, "Font")
    for name, ref in re.findall(r"/([^\s/<>\[\]()]+)\s+(\d+)\s+\d+\s+R", font_dict):
        fobj = objs.get(int(ref))
        if not fobj:
            continue
        fd = fobj["dict"]
        two_byte = bool(re.search(r"/Subtype\s*/Type0", fd)) or "Identity-H" in fd or "Identity-V" in fd
        cmap: Dict[int, str] = {}
        tm = re.search(r"/ToUnicode\s+(\d+)\s+\d+\s+R", fd)
        if tm:
            tobj = objs.get(int(tm.group(1)))
            if tobj and tobj["stream"]:
                cmap = _parse_cmap(tobj["stream"].decode("latin1", "ignore"))
        fonts[name] = _Font(two_byte, cmap)
    return fonts


# --------------------------------------------------------------------------- conteúdo

_TOKEN_RE = re.compile(
    rb"\s*(?:"
    rb"(?P<hex><[0-9A-Fa-f\s]*>)"
    rb"|(?P<dict><<|>>)"
    rb"|(?P<arr>[\[\]])"
    rb"|(?P<name>/[^\s/<>\[\]()%{}]*)"
    rb"|(?P<num>[+-]?(?:\d+\.?\d*|\.\d+))"
    rb"|(?P<lit>\()"
    rb"|(?P<comment>%[^\r\n]*)"
    rb"|(?P<op>[^\s/<>\[\]()%{}]+)"
    rb"|(?P<other>[{}])"
    rb")"
)


def _read_literal(data: bytes, pos: int) -> Tuple[bytes, int]:
    """Lê uma string literal a partir do '(' em `pos`. Retorna (bytes, nova_posição)."""
    out = bytearray()
    depth = 1
    i = pos + 1
    n = len(data)
    while i < n and depth > 0:
        c = data[i]
        if c == 0x5C:  # barra invertida
            i += 1
            if i >= n:
                break
            e = data[i]
            mapping = {0x6E: 10, 0x72: 13, 0x74: 9, 0x62: 8, 0x66: 12}
            if e in mapping:
                out.append(mapping[e])
            elif 0x30 <= e <= 0x37:
                oct_s = bytes([e])
                for _ in range(2):
                    if i + 1 < n and 0x30 <= data[i + 1] <= 0x37:
                        i += 1
                        oct_s += bytes([data[i]])
                out.append(int(oct_s, 8) & 0xFF)
            elif e in (0x0D, 0x0A):
                if e == 0x0D and i + 1 < n and data[i + 1] == 0x0A:
                    i += 1
            else:
                out.append(e)
        elif c == 0x28:
            depth += 1
            out.append(c)
        elif c == 0x29:
            depth -= 1
            if depth > 0:
                out.append(c)
        else:
            out.append(c)
        i += 1
    return bytes(out), i


def _tokenize(data: bytes):
    pos = 0
    n = len(data)
    while pos < n:
        m = _TOKEN_RE.match(data, pos)
        if not m or m.end() == pos:
            pos += 1
            continue
        kind = m.lastgroup
        if kind == "lit":
            raw, pos = _read_literal(data, m.end() - 1)
            yield ("str", raw)
            continue
        pos = m.end()
        val = m.group(kind)
        if kind == "hex":
            h = re.sub(rb"\s+", b"", val[1:-1])
            if len(h) % 2:
                h += b"0"
            try:
                yield ("str", bytes.fromhex(h.decode()))
            except ValueError:
                yield ("str", b"")
        elif kind == "num":
            try:
                yield ("num", float(val))
            except ValueError:
                pass
        elif kind == "name":
            yield ("name", val[1:].decode("latin1"))
        elif kind == "arr":
            yield ("arr", val.decode())
        elif kind == "op":
            yield ("op", val.decode("latin1"))
        elif kind == "dict":
            yield ("dict", val.decode())


def _mult(a: List[float], b: List[float]) -> List[float]:
    return [
        a[0] * b[0] + a[1] * b[2], a[0] * b[1] + a[1] * b[3],
        a[2] * b[0] + a[3] * b[2], a[2] * b[1] + a[3] * b[3],
        a[4] * b[0] + a[5] * b[2] + b[4], a[4] * b[1] + a[5] * b[3] + b[5],
    ]


def _extract_page_items(content: bytes, fonts: Dict[str, _Font]) -> List[Tuple[float, float, str]]:
    items: List[Tuple[float, float, str]] = []
    ctm = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]
    ctm_stack: List[List[float]] = []
    tm = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]
    tlm = list(tm)
    leading = 0.0
    font: Optional[_Font] = None
    font_size = 10.0
    stack: List[Any] = []
    arr: Optional[List[Any]] = None
    default_font = _Font(False, {})

    def show(text: str) -> None:
        nonlocal tm
        if not text:
            return
        m = _mult(tm, ctm)
        items.append((m[4], m[5], text))
        # avanço aproximado para que dois Tj seguidos na mesma linha não se sobreponham
        tm = [tm[0], tm[1], tm[2], tm[3], tm[4] + len(text) * font_size * 0.5 * tm[0], tm[5]]

    def decode(raw: bytes) -> str:
        return (font or default_font).decode(raw)

    for kind, val in _tokenize(content):
        if kind == "arr":
            if val == "[":
                arr = []
            else:
                stack.append(arr if arr is not None else [])
                arr = None
            continue
        if arr is not None:
            if kind in ("str", "num"):
                arr.append(val)
            continue
        if kind != "op":
            stack.append(val)
            continue

        op = val
        try:
            if op == "q":
                ctm_stack.append(list(ctm))
            elif op == "Q":
                if ctm_stack:
                    ctm = ctm_stack.pop()
            elif op == "cm" and len(stack) >= 6:
                ctm = _mult([float(x) for x in stack[-6:]], ctm)
            elif op == "BT":
                tm = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]
                tlm = list(tm)
            elif op == "Tf" and len(stack) >= 2:
                font = fonts.get(str(stack[-2]))
                font_size = float(stack[-1]) if isinstance(stack[-1], float) else font_size
            elif op == "TL" and stack:
                leading = float(stack[-1])
            elif op in ("Td", "TD") and len(stack) >= 2:
                tx, ty = float(stack[-2]), float(stack[-1])
                if op == "TD":
                    leading = -ty
                tlm = _mult([1, 0, 0, 1, tx, ty], tlm)
                tm = list(tlm)
            elif op == "Tm" and len(stack) >= 6:
                tlm = [float(x) for x in stack[-6:]]
                tm = list(tlm)
            elif op == "T*":
                tlm = _mult([1, 0, 0, 1, 0, -leading], tlm)
                tm = list(tlm)
            elif op == "Tj" and stack and isinstance(stack[-1], bytes):
                show(decode(stack[-1]))
            elif op in ("'", '"') and stack and isinstance(stack[-1], bytes):
                tlm = _mult([1, 0, 0, 1, 0, -leading], tlm)
                tm = list(tlm)
                show(decode(stack[-1]))
            elif op == "TJ" and stack and isinstance(stack[-1], list):
                parts: List[str] = []
                for el in stack[-1]:
                    if isinstance(el, bytes):
                        parts.append(decode(el))
                    elif isinstance(el, float) and el < -180:
                        parts.append(" ")
                show("".join(parts))
        except (TypeError, ValueError):
            pass
        stack = []
    return items


def _group_lines(items: List[Tuple[float, float, str]], y_tol: float = 2.5) -> List[List[str]]:
    rows: List[Tuple[float, List[Tuple[float, str]]]] = []
    for x, y, text in sorted(items, key=lambda it: (-it[1], it[0])):
        if not text.strip():
            continue
        if rows and abs(rows[-1][0] - y) <= y_tol:
            rows[-1][1].append((x, text))
        else:
            rows.append((y, [(x, text)]))
    lines: List[List[str]] = []
    for _, cells in rows:
        cells.sort(key=lambda c: c[0])
        lines.append([c[1].strip() for c in cells if c[1].strip()])
    return lines


def extract_pdf_lines(pdf_bytes: bytes) -> Tuple[List[List[List[str]]], int]:
    """Retorna (páginas, quantidade_de_páginas). Cada página = lista de linhas = lista de células."""
    objs = _parse_objects(pdf_bytes)
    page_ids = _page_object_ids(objs, pdf_bytes)
    pages_out: List[List[List[str]]] = []
    for pid in page_ids:
        pdict = objs[pid]["dict"]
        resources = _inherited_resources(objs, pid)
        fonts = _load_fonts(objs, resources)
        content = b""
        cm = re.search(r"/Contents\s*(\[[^\]]*\]|\d+\s+\d+\s+R)", pdict)
        if cm:
            for r in _REF_RE.finditer(cm.group(1)):
                cobj = objs.get(int(r.group(1)))
                if cobj and cobj["stream"]:
                    content += cobj["stream"] + b"\n"
        items = _extract_page_items(content, fonts) if content else []
        pages_out.append(_group_lines(items))
    return pages_out, max(1, len(page_ids))
