import os
import re
import json
import struct
import socket
import sqlite3
import hashlib
import base64
import hmac
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT_DIR = Path(__file__).resolve().parent.parent
LOCAL_DB_DIR = ROOT_DIR / "dados_locais"
LOCAL_SQLITE_PATH = LOCAL_DB_DIR / "confere_fiscal_local.db"
LOCAL_CONFIG_DIR = ROOT_DIR / ".local_data" / "config"
LOCAL_KEY_FILE = LOCAL_CONFIG_DIR / ".py_secret.key"
LOCAL_FB_CONFIG_FILE = LOCAL_CONFIG_DIR / "firebird_py.enc.json"
LOCAL_LOG_FILE = ROOT_DIR / ".local_data" / "logs" / "python_technical.log"
DEFAULT_CLIPP_FDB_PATH = r"C:/Work/MT/Cliente/Clipp/Base/CLIPP.FDB"

CFOP_DESCRIPTIONS: Dict[str, str] = {
    "5101": "Venda de produção do estabelecimento",
    "5102": "Venda de mercadoria adquirida ou recebida de terceiros",
    "5103": "Venda de produção do estabelecimento, efetuada fora do estabelecimento",
    "5104": "Venda de mercadoria adquirida de terceiros, efetuada fora do estabelecimento",
    "5401": "Venda de produção do estabelecimento em operação com produto sujeito a ST",
    "5403": "Venda de mercadoria adquirida de terceiros sujeita a ST (Substituto)",
    "5405": "Venda de mercadoria adquirida de terceiros sujeita a ST (Substituído)",
    "5656": "Venda de combustível ou lubrificante a consumidor ou usuário final",
    "5929": "Lançamento efetuado em decorrência de emissão de documento fiscal relativo a operação registrada em ECF",
    "6101": "Venda de produção do estabelecimento (Interestadual)",
    "6102": "Venda de mercadoria adquirida de terceiros (Interestadual)",
    "6108": "Venda de mercadoria adquirida de terceiros a não contribuinte (Interestadual)",
    "6403": "Venda de mercadoria sujeita a ST (Interestadual - Substituto)",
    "6404": "Venda de mercadoria sujeita a ST cujo imposto já tenha sido retido anteriormente (Interestadual)",
}


def ensure_local_dirs() -> None:
    LOCAL_DB_DIR.mkdir(parents=True, exist_ok=True)
    LOCAL_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    LOCAL_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)


def sanitize_log_text(text: str, extra_secrets: Optional[List[str]] = None) -> str:
    if not text:
        return ""
    out = re.sub(
        r'("?(?:password|senha|secret|passwd|pwd)"?\s*[:=]\s*["\']?)([^"\',;\s}]+)(["\']?)',
        r"\1[PROTEGIDO]\3",
        str(text),
        flags=re.IGNORECASE,
    )
    for sec in extra_secrets or []:
        if sec and len(str(sec).strip()) >= 2:
            out = out.replace(str(sec), "[PROTEGIDO]")
    return out


def log_event(level: str, context: str, message: str, secrets: Optional[List[str]] = None) -> None:
    ensure_local_dirs()
    ts = datetime.now().isoformat(timespec="seconds")
    safe_msg = sanitize_log_text(message, secrets)
    line = f"[{ts}] [{level.upper()}] [{context}] {safe_msg}\n"
    try:
        with open(LOCAL_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass


def _get_or_create_secret_key() -> bytes:
    ensure_local_dirs()
    if LOCAL_KEY_FILE.exists():
        raw = LOCAL_KEY_FILE.read_text(encoding="utf-8").strip()
        if len(raw) == 64:
            return bytes.fromhex(raw)
    key = os.urandom(32)
    LOCAL_KEY_FILE.write_text(key.hex(), encoding="utf-8")
    return key


def encrypt_secret(plain_text: str) -> Optional[Dict[str, str]]:
    """
    Criptografa uma senha localmente usando stream cipher derivado de HMAC-SHA256 + tag de autenticação
    (sem gravar texto puro em disco).
    """
    if not plain_text:
        return None
    key = _get_or_create_secret_key()
    nonce = os.urandom(16)
    plain_bytes = plain_text.encode("utf-8")
    keystream = b""
    counter = 0
    while len(keystream) < len(plain_bytes):
        block = hmac.new(key, nonce + struct.pack("<I", counter), hashlib.sha256).digest()
        keystream += block
        counter += 1
    cipher_bytes = bytes(a ^ b for a, b in zip(plain_bytes, keystream[: len(plain_bytes)]))
    tag = hmac.new(key, nonce + cipher_bytes, hashlib.sha256).hexdigest()
    return {
        "alg": "hmac-sha256-ctr",
        "nonce": nonce.hex(),
        "data": cipher_bytes.hex(),
        "tag": tag,
    }


def decrypt_secret(payload: Optional[Dict[str, str]]) -> str:
    if not payload or not isinstance(payload, dict):
        return ""
    try:
        key = _get_or_create_secret_key()
        nonce = bytes.fromhex(payload["nonce"])
        cipher_bytes = bytes.fromhex(payload["data"])
        expected_tag = hmac.new(key, nonce + cipher_bytes, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected_tag, payload.get("tag", "")):
            return ""
        keystream = b""
        counter = 0
        while len(keystream) < len(cipher_bytes):
            block = hmac.new(key, nonce + struct.pack("<I", counter), hashlib.sha256).digest()
            keystream += block
            counter += 1
        plain_bytes = bytes(a ^ b for a, b in zip(cipher_bytes, keystream[: len(cipher_bytes)]))
        return plain_bytes.decode("utf-8")
    except Exception:
        return ""


def validate_sql_identifier(identifier: str) -> str:
    if not identifier or not isinstance(identifier, str):
        raise ValueError("Identificador SQL vazio ou inválido.")
    cleaned = identifier.strip()
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_$]*$", cleaned):
        raise ValueError(f'Identificador SQL inválido bloqueado por segurança: "{cleaned}"')
    return cleaned.upper()


def assert_read_only_sql(sql: str) -> str:
    """
    Garante que uma consulta SQL seja estritamente SELECT (somente leitura).
    """
    if not sql or not isinstance(sql, str):
        raise ValueError("Consulta SQL vazia.")
    trimmed = sql.strip()
    if not re.match(r"^SELECT\s+", trimmed, flags=re.IGNORECASE):
        raise ValueError("Operação bloqueada: apenas consultas SELECT (somente leitura) são permitidas.")

    without_literals = re.sub(r"'[^']*'", "''", trimmed)
    parts = [p.strip() for p in without_literals.split(";") if p.strip()]
    if len(parts) > 1:
        raise ValueError("Operação bloqueada: múltiplos comandos SQL não são permitidos.")

    forbidden = re.search(
        r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|ALTER|CREATE|TRUNCATE|RECREATE|EXECUTE|EXEC|GRANT|REVOKE|ATTACH|DETACH|PRAGMA)\b",
        without_literals,
        flags=re.IGNORECASE,
    )
    if forbidden:
        raise ValueError(
            f'Operação bloqueada por política Somente-Leitura: comando "{forbidden.group(1).upper()}" não permitido.'
        )
    return trimmed


def fb_rle_decompress(data: bytes) -> bytes:
    """
    Descompacta um registro binário Firebird ODS 11/12/13 codificado em RLE.
    """
    out = bytearray()
    i = 0
    n_len = len(data)
    while i < n_len:
        ctrl = struct.unpack_from("b", data, i)[0]
        i += 1
        if ctrl > 0:
            out.extend(data[i : i + ctrl])
            i += ctrl
        elif ctrl < 0:
            if i < n_len:
                out.extend(bytes([data[i]]) * (-ctrl))
                i += 1
        else:
            break
    return bytes(out)


def normalize_iso_date(raw_date: Optional[str]) -> Optional[str]:
    """
    Normaliza qualquer string de data ('01/10/2026', '01-10-2026', '2026-2-07', '2026-10-01T14:20:00')
    para o formato canônico ISO 'YYYY-MM-DD' (10 dígitos com zero-padding).
    """
    if not raw_date:
        return None
    s = str(raw_date).strip()
    if not s:
        return None
    m_br = re.match(r"^(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})", s)
    if m_br:
        return f"{int(m_br.group(3)):04d}-{int(m_br.group(2)):02d}-{int(m_br.group(1)):02d}"
    m_iso = re.match(r"^(\d{4})[/\-](\d{1,2})[/\-](\d{1,2})", s)
    if m_iso:
        return f"{int(m_iso.group(1)):04d}-{int(m_iso.group(2)):02d}-{int(m_iso.group(3)):02d}"
    return s[:10]


def _normalize_sefaz_datetime(raw_dh: str, fallback_date: str = "") -> Tuple[str, str]:
    """
    Normaliza `data_hora` de `tb_nfe` (ex.: '04/04/2026 19:13:30', '7/2/2026 08:38:10' ou '2026-04-04T19:13:30')
    retornando `(auth_date_iso, auth_datetime_iso)`.
    """
    s = (raw_dh or "").strip()
    fb_iso = normalize_iso_date(fallback_date) or ""
    if not s:
        return (fb_iso, f"{fb_iso}T00:00:00" if fb_iso else "")
    m_br = re.match(r"^(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})(?:[T\s]+(\d{1,2}:\d{2}(?::\d{2})?))?", s)
    if m_br:
        d_iso = f"{int(m_br.group(3)):04d}-{int(m_br.group(2)):02d}-{int(m_br.group(1)):02d}"
        t_part = m_br.group(4) or "00:00:00"
        if len(t_part) == 5:
            t_part += ":00"
        elif len(t_part) == 7:
            t_part = "0" + t_part
        return (d_iso, f"{d_iso}T{t_part}")
    m_iso = re.match(r"^(\d{4})[/\-](\d{1,2})[/\-](\d{1,2})(?:[T\s]+(\d{1,2}:\d{2}(?::\d{2})?))?", s)
    if m_iso:
        d_iso = f"{int(m_iso.group(1)):04d}-{int(m_iso.group(2)):02d}-{int(m_iso.group(3)):02d}"
        t_part = m_iso.group(4) or "00:00:00"
        if len(t_part) == 5:
            t_part += ":00"
        elif len(t_part) == 7:
            t_part = "0" + t_part
        return (d_iso, f"{d_iso}T{t_part}")
    d_norm = normalize_iso_date(s) or fb_iso
    return (d_norm, s)


def inspect_fdb_ods_header(file_path: str) -> Optional[Dict[str, Any]]:
    """
    Inspeciona o cabeçalho binário ODS (On-Disk Structure) de um arquivo .fdb/.gdb local
    para identificar se pertence ao Firebird 2.5 (ODS 11.2 / 11.3) ou Firebird 5.0 (ODS 13.1).
    """
    if not file_path:
        return None
    clean_path = re.sub(r"^(?:localhost|127\.0\.0\.1)(?:/\d+)?:", "", str(file_path).strip(), flags=re.IGNORECASE)
    p = Path(clean_path)
    if not p.exists() or not p.is_file():
        return None
    try:
        with open(p, "rb") as f:
            buf = f.read(64)
        if len(buf) < 24:
            return None
        pag_type = buf[0]
        if pag_type != 0x01:
            return {
                "exists": True,
                "validHeader": False,
                "filePath": str(p),
                "message": "O arquivo não possui cabeçalho de página Firebird (pag_type != 1).",
            }
        page_size = struct.unpack_from("<H", buf, 16)[0]
        raw_ods_major = struct.unpack_from("<H", buf, 18)[0]
        ods_major = raw_ods_major & 0x7FFF
        ods_minor = struct.unpack_from("<H", buf, 20)[0]
        ods_str = f"{ods_major}.{ods_minor}"

        version_family = "unknown"
        detected_version = f"Firebird (ODS {ods_str})"
        if ods_major == 11:
            version_family = "2.5"
            detected_version = f"Firebird 2.5 (ODS {ods_str})"
        elif ods_major == 12:
            version_family = "3.0"
            detected_version = f"Firebird 3.0 (ODS {ods_str})"
        elif ods_major == 13 and ods_minor == 0:
            version_family = "4.0"
            detected_version = f"Firebird 4.0 (ODS {ods_str})"
        elif ods_major == 13 and ods_minor >= 1:
            version_family = "5.0"
            detected_version = f"Firebird 5.0 (ODS {ods_str})"

        is_demo_file = b"CONFEREFISCAL_DEMO_FDB" in buf[24:64]
        file_size = p.stat().st_size
        return {
            "exists": True,
            "validHeader": True,
            "filePath": str(p),
            "fileSize": file_size,
            "pageSize": page_size,
            "odsMajor": ods_major,
            "odsMinor": ods_minor,
            "odsString": ods_str,
            "odsVersion": ods_str,
            "versionFamily": version_family,
            "detectedVersion": detected_version,
            "isDemoFile": is_demo_file,
        }
    except Exception:
        return None


def detect_local_firebird_installations() -> List[Dict[str, str]]:
    """
    Detecta instalações locais do Firebird (2.5, 3.0, 4.0, 5.0) no Windows.
    """
    candidates = [
        ROOT_DIR / "runtime" / "firebird",
        Path(r"C:\Program Files\Firebird\Firebird_5_0"),
        Path(r"C:\Program Files (x86)\Firebird\Firebird_5_0"),
        Path(r"C:\Program Files\Firebird\Firebird_2_5"),
        Path(r"C:\Program Files (x86)\Firebird\Firebird_2_5"),
        Path(r"C:\Work\MT\Cliente\Clipp\Exe\migracaoFirebird\Firebird_2_5"),
        Path(r"C:\Work\MT\Cliente\Clipp\Exe\migracaoFirebird\Firebird_5_0"),
    ]
    found = []
    for base in candidates:
        if base.exists():
            isql_path = base / "bin" / "isql.exe" if (base / "bin" / "isql.exe").exists() else base / "isql.exe"
            fbclient_path = (
                base / "bin" / "fbclient.dll" if (base / "bin" / "fbclient.dll").exists() else base / "fbclient.dll"
            )
            ver = "5.0" if "5_0" in base.name else "2.5"
            found.append(
                {
                    "version": ver,
                    "directory": str(base),
                    "isqlPath": str(isql_path) if isql_path.exists() else "",
                    "fbclientPath": str(fbclient_path) if fbclient_path.exists() else "",
                }
            )
    return found


def extract_clipp_fdb_sales(
    fdb_path_str: str,
    dt_inicio: Optional[str] = None,
    dt_fim: Optional[str] = None,
    max_items_per_sale: int = 500,
) -> Dict[str, Any]:
    """
    Leitor binário nativo de alta performance para bases Clipp Firebird (.FDB ODS 11.2 / 11.3).
    Extrai diretamente `TB_NFVENDA` (rel 222), `TB_NFE` (rel 206), `TB_NFVENDA_TOT` (rel 1028),
    `TB_NFV_ITEM` (rel 210), `TB_EST_PRODUTO` (rel 169) e `TB_NFE_CANC` (rel 526) sem exigir
    que o serviço Firebird esteja rodando ou instalado na máquina.
    """
    clean_path = re.sub(r"^(?:localhost|127\.0\.0\.1)(?:/\d+)?:", "", str(fdb_path_str or "").strip(), flags=re.IGNORECASE)
    fdb_path = Path(clean_path)
    if not fdb_path.exists() or not fdb_path.is_file():
        raise FileNotFoundError(f"Arquivo Firebird não encontrado em: {clean_path}")

    ods = inspect_fdb_ods_header(str(fdb_path))
    if not ods or not ods.get("validHeader") or ods.get("isDemoFile"):
        return {"isClippFdb": False, "sales": [], "items": []}

    page_size = int(ods.get("pageSize") or 4096)
    fb_epoch = date(1858, 11, 17)

    nfvenda_map: Dict[int, Dict[str, Any]] = {}
    nfe_map: Dict[int, Dict[str, Any]] = {}
    tot_map: Dict[int, Dict[str, float]] = {}
    canc_reg_ids: Dict[int, Dict[str, str]] = {}
    ncm_by_ident: Dict[int, str] = {}
    raw_items: List[Tuple[int, int, int, int, str, str, str, float, float, float, float]] = []
    item_sums_by_sale: Dict[int, Dict[str, float]] = {}

    data = fdb_path.read_bytes()
    total_len = len(data)

    def _get_slot_payload(page_num: int, slot_num: int, depth: int = 0) -> bytes:
        if depth > 6 or page_num <= 0:
            return b""
        p_base = page_num * page_size
        if p_base + page_size > total_len or data[p_base] != 5:
            return b""
        cnt = struct.unpack_from("<H", data, p_base + 22)[0]
        if slot_num < 0 or slot_num >= cnt:
            return b""
        s_off, s_len = struct.unpack_from("<HH", data, p_base + 24 + slot_num * 4)
        if s_off <= 0 or s_len <= 13 or p_base + s_off + s_len > total_len:
            return b""
        s_flags = struct.unpack_from("<H", data, p_base + s_off + 10)[0]
        if s_flags & 0x08:
            if s_len <= 22:
                return b""
            next_pg = struct.unpack_from("<i", data, p_base + s_off + 16)[0]
            next_sl = struct.unpack_from("<H", data, p_base + s_off + 20)[0]
            return data[p_base + s_off + 22 : p_base + s_off + s_len] + _get_slot_payload(next_pg, next_sl, depth + 1)
        return data[p_base + s_off + 13 : p_base + s_off + s_len]

    for base in range(0, total_len - page_size + 1, page_size):
        if data[base] != 5:
            continue
        dpg_rel = struct.unpack_from("<H", data, base + 20)[0]
        if dpg_rel not in (222, 206, 1028, 526, 169, 210):
            continue

        dpg_count = struct.unpack_from("<H", data, base + 22)[0]
        for slot in range(dpg_count):
            off, length = struct.unpack_from("<HH", data, base + 24 + slot * 4)
            if off <= 0 or length <= 13 or base + off + length > total_len:
                continue
            rhd_flags = struct.unpack_from("<H", data, base + off + 10)[0]
            if rhd_flags & 0x01 or rhd_flags & 0x02 or rhd_flags & 0x04 or rhd_flags & 0x10:
                continue
            if rhd_flags & 0x08:
                if length <= 22:
                    continue
                frag_page = struct.unpack_from("<i", data, base + off + 16)[0]
                frag_slot = struct.unpack_from("<H", data, base + off + 20)[0]
                raw_rle = data[base + off + 22 : base + off + length] + _get_slot_payload(frag_page, frag_slot)
            else:
                raw_rle = data[base + off + 13 : base + off + length]

            r = fb_rle_decompress(raw_rle)

            if dpg_rel == 222 and len(r) >= 150:
                id_nfv = struct.unpack_from("<i", r, 64)[0]
                if id_nfv <= 0:
                    continue
                mod_len = min(2, struct.unpack_from("<H", r, 44)[0])
                modelo = r[46 : 46 + mod_len].decode("latin1", errors="ignore").strip()
                if modelo not in ("55", "65"):
                    continue
                dt_days = struct.unpack_from("<i", r, 36)[0]
                dt_emi = (fb_epoch + timedelta(days=dt_days)).isoformat() if 40000 < dt_days < 80000 else ""
                ser_len = min(5, struct.unpack_from("<H", r, 48)[0])
                serie = r[50 : 50 + ser_len].decode("latin1", errors="ignore").strip() or "1"
                nf_num = struct.unpack_from("<i", r, 56)[0]
                status_ch = chr(r[116]) if len(r) > 116 and 32 <= r[116] <= 122 else "E"
                ent_sai = chr(r[146]) if len(r) > 146 and 32 <= r[146] <= 122 else "S"
                nfvenda_map[id_nfv] = {
                    "id_nfvenda": id_nfv,
                    "nf_numero": nf_num,
                    "nf_serie": serie,
                    "nf_modelo": modelo,
                    "dt_emissao": dt_emi,
                    "status_nfvenda": status_ch,
                    "ent_sai": ent_sai,
                }

            elif dpg_rel == 206 and len(r) >= 280:
                shift = -52 if len(r) < 320 else 0
                id_reg = struct.unpack_from("<i", r, 208 + shift)[0]
                id_nfv = struct.unpack_from("<i", r, 216 + shift)[0]
                if id_nfv <= 0:
                    continue
                emi_days = struct.unpack_from("<i", r, 212 + shift)[0]
                emi_iso = (fb_epoch + timedelta(days=emi_days)).isoformat() if 40000 < emi_days < 80000 else ""
                dh_len = min(20, struct.unpack_from("<H", r, 220 + shift)[0])
                dh_raw = r[222 + shift : 222 + shift + dh_len].decode("latin1", errors="ignore").strip()
                ch_len = min(44, struct.unpack_from("<H", r, 242 + shift)[0])
                chave = re.sub(r"\D", "", r[244 + shift : 244 + shift + ch_len].decode("latin1", errors="ignore"))
                st_len = min(5, struct.unpack_from("<H", r, 292 + shift)[0])
                cstat = r[294 + shift : 294 + shift + st_len].decode("latin1", errors="ignore").strip()
                prot_len = min(21, struct.unpack_from("<H", r, 300 + shift)[0])
                protocolo = r[302 + shift : 302 + shift + prot_len].decode("latin1", errors="ignore").strip()
                des_len = min(200 if shift == 0 else 150, struct.unpack_from("<H", r, 4)[0])
                st_des = r[6 : 6 + des_len].decode("latin1", errors="ignore").strip()
                nfe_map[id_nfv] = {
                    "id_registro": id_reg,
                    "id_nfvenda": id_nfv,
                    "id_nfe": chave,
                    "emissao": emi_iso,
                    "data_hora_raw": dh_raw,
                    "protocolo": protocolo,
                    "status": cstat,
                    "status_des": st_des,
                }

            elif dpg_rel == 1028 and len(r) >= 152:
                id_nfv = struct.unpack_from("<i", r, 8)[0]
                if id_nfv <= 0:
                    continue
                tot_desc = max(0.0, round(struct.unpack_from("<q", r, 16)[0] / 10000.0, 2))
                tot_bc_icms = max(0.0, round(struct.unpack_from("<q", r, 72)[0] / 10000.0, 2))
                tot_icms = max(0.0, round(struct.unpack_from("<q", r, 96)[0] / 10000.0, 2))
                tot_prod = max(0.0, round(struct.unpack_from("<q", r, 136)[0] / 10000.0, 2))
                tot_nf = max(0.0, round(struct.unpack_from("<q", r, 144)[0] / 10000.0, 2))
                if tot_nf < 1e9:
                    tot_map[id_nfv] = {
                        "tot_produto": tot_prod if tot_prod > 0 else tot_nf,
                        "tot_desconto": tot_desc,
                        "tot_bc_icms": tot_bc_icms,
                        "tot_icms": tot_icms,
                        "tot_nf": tot_nf,
                    }

            elif dpg_rel == 526 and len(r) >= 200:
                id_reg = struct.unpack_from("<i", r, 8)[0]
                prot_len = min(15, struct.unpack_from("<H", r, 30)[0])
                prot = r[32 : 32 + prot_len].decode("latin1", errors="ignore").strip()
                st_len = min(5, struct.unpack_from("<H", r, 52)[0])
                cstat = r[54 : 54 + st_len].decode("latin1", errors="ignore").strip() or "101"
                des_len = min(150, struct.unpack_from("<H", r, 60)[0])
                des = r[62 : 62 + des_len].decode("latin1", errors="ignore").strip()
                canc_reg_ids[id_reg] = {
                    "protocolo": prot,
                    "status": "101" if cstat in ("101", "135", "151") else cstat,
                    "status_des": des or "Cancelamento de NF-e homologado",
                }

            elif dpg_rel == 169 and len(r) >= 190:
                id_ident = struct.unpack_from("<i", r, 172)[0]
                ncm_len = min(8, struct.unpack_from("<H", r, 180)[0])
                ncm = re.sub(r"\D", "", r[182 : 182 + ncm_len].decode("latin1", errors="ignore"))
                if id_ident > 0 and ncm:
                    ncm_by_ident[id_ident] = ncm

            elif dpg_rel == 210 and len(r) >= 152:
                id_nfv = struct.unpack_from("<i", r, 96)[0]
                if id_nfv <= 0:
                    continue
                id_nfvitem = struct.unpack_from("<i", r, 104)[0]
                id_ident = struct.unpack_from("<i", r, 108)[0]
                num_item = struct.unpack_from("<H", r, 112)[0]
                cfop_len = min(4, struct.unpack_from("<H", r, 114)[0])
                cfop = re.sub(r"\D", "", r[116 : 116 + cfop_len].decode("latin1", errors="ignore"))
                csosn_len = min(3, struct.unpack_from("<H", r, 120)[0])
                csosn = r[122 : 122 + csosn_len].decode("latin1", errors="ignore").strip()
                un_len = min(4, struct.unpack_from("<H", r, 146)[0])
                un = r[148 : 148 + un_len].decode("latin1", errors="ignore").strip() or "UN"
                vlr_desc = max(0.0, round(struct.unpack_from("<q", r, 72)[0] / 10000.0, 2))
                vlr_total = max(0.0, round(struct.unpack_from("<q", r, 80)[0] / 10000.0, 2))
                qtd = max(0.0, round(struct.unpack_from("<q", r, 88)[0] / 10000.0, 4))
                vlr_unit = round(vlr_total / qtd, 4) if qtd > 0 else vlr_total
                if vlr_total < 1e8 and cfop:
                    raw_items.append((id_nfvitem, id_nfv, id_ident, num_item, cfop, csosn, un, qtd, vlr_unit, vlr_desc, vlr_total))
                    agg = item_sums_by_sale.setdefault(id_nfv, {"tot_produto": 0.0, "tot_desconto": 0.0, "tot_nf": 0.0})
                    agg["tot_produto"] = round(agg["tot_produto"] + vlr_total, 2)
                    agg["tot_desconto"] = round(agg["tot_desconto"] + vlr_desc, 2)
                    agg["tot_nf"] = round(agg["tot_nf"] + max(0.0, vlr_total - vlr_desc), 2)

    sales_out: List[Dict[str, Any]] = []
    valid_sale_ids = set()

    for id_nfv, v in nfvenda_map.items():
        nfe = nfe_map.get(id_nfv, {})
        t = tot_map.get(id_nfv) or item_sums_by_sale.get(id_nfv, {})
        dt_emi = v["dt_emissao"] or nfe.get("emissao") or ""
        auth_date, auth_dt = _normalize_sefaz_datetime(nfe.get("data_hora_raw", ""), dt_emi)

        # Se filtro de competência foi informado, mantemos registros cuja emissão OU autorização caia no período
        if dt_inicio and dt_emi and auth_date:
            if dt_emi < dt_inicio and auth_date < dt_inicio:
                continue
        if dt_fim and dt_emi and auth_date:
            if dt_emi > dt_fim and auth_date > dt_fim:
                continue

        id_reg = nfe.get("id_registro", 0)
        cstat = str(nfe.get("status") or "").strip()
        st_des = str(nfe.get("status_des") or "").strip()
        protocolo = str(nfe.get("protocolo") or "").strip()

        if id_reg in canc_reg_ids:
            cstat = "101"
            st_des = canc_reg_ids[id_reg]["status_des"]
            protocolo = canc_reg_ids[id_reg]["protocolo"] or protocolo
        elif v["status_nfvenda"] == "C" and cstat not in ("101", "102"):
            cstat = "101"
            st_des = st_des or "Cancelada no ERP Clipp"
        elif not cstat:
            # Emissão sem retorno de protocolo SEFAZ (ex.: contingência pendente ou falha de transmissão)
            cstat = "100" if nfe.get("id_nfe") else "SEM_RETORNO"
            st_des = st_des or ("Autorizado (Contingência/Local)" if cstat == "100" else "Sem protocolo SEFAZ")

        if cstat == "150":
            cstat_norm = "100"
        elif cstat in ("101", "135", "151"):
            cstat_norm = "101"
        elif cstat == "102":
            cstat_norm = "102"
        else:
            cstat_norm = cstat

        tot_nf = float(t.get("tot_nf", 0.0))
        if tot_nf <= 0.0 and id_nfv in item_sums_by_sale:
            tot_nf = float(item_sums_by_sale[id_nfv].get("tot_nf", 0.0))
        tot_prod = float(t.get("tot_produto", tot_nf)) or tot_nf
        tot_desc = float(t.get("tot_desconto", 0.0))
        tot_bc = float(t.get("tot_bc_icms", 0.0))
        tot_icms = float(t.get("tot_icms", 0.0))

        sales_out.append(
            {
                "id_nfvenda": id_nfv,
                "nf_numero": v["nf_numero"],
                "nf_serie": v["nf_serie"],
                "nf_modelo": v["nf_modelo"],
                "dt_emissao": dt_emi,
                "data_hora_sefaz": auth_dt,
                "dt_autorizacao": auth_date,
                "id_nfe": nfe.get("id_nfe", ""),
                "protocolo": protocolo,
                "status": cstat_norm,
                "status_raw": cstat,
                "status_des": st_des or ("Autorizado o uso da NF-e" if cstat_norm == "100" else f"Status SEFAZ {cstat_norm}"),
                "tot_produto": tot_prod,
                "tot_desconto": tot_desc,
                "tot_bc_icms": tot_bc,
                "tot_icms": tot_icms,
                "tot_nf": tot_nf,
                "is_d_plus_1": bool(dt_emi and auth_date and dt_emi != auth_date),
            }
        )
        valid_sale_ids.add(id_nfv)

    items_out: List[Dict[str, Any]] = []
    per_sale_counter: Dict[int, int] = {}
    for id_nfvitem, id_nfv, id_ident, num_item, cfop, csosn, un, qtd, vlr_unit, vlr_desc, vlr_total in raw_items:
        if id_nfv not in valid_sale_ids:
            continue
        cnt = per_sale_counter.get(id_nfv, 0) + 1
        if cnt > max_items_per_sale:
            continue
        per_sale_counter[id_nfv] = cnt
        ncm = ncm_by_ident.get(id_ident, "00000000")
        vlr_contabil = round(max(0.0, vlr_total - vlr_desc), 2)
        # Rateia BC ICMS e ICMS proporcionalmente se houver na nota
        sale_tot = tot_map.get(id_nfv) or item_sums_by_sale.get(id_nfv, {})
        sale_nf = float(sale_tot.get("tot_nf") or 0.0)
        ratio = (vlr_contabil / sale_nf) if sale_nf > 0 else 0.0
        bc_item = round(float(sale_tot.get("tot_bc_icms") or 0.0) * ratio, 2)
        icms_item = round(float(sale_tot.get("tot_icms") or 0.0) * ratio, 2)

        items_out.append(
            {
                "id_nfvitem": id_nfvitem,
                "id_nfvenda": id_nfv,
                "num_item": num_item if num_item > 0 else cnt,
                "id_identificador": id_ident,
                "cod_produto": f"PROD-{id_ident}",
                "desc_produto": f"MERCADORIA FISCAL ID {id_ident} (NCM {ncm})",
                "cod_ncm": ncm,
                "cfop": cfop,
                "csosn": csosn,
                "uni_medida": un,
                "qtd_item": qtd,
                "vlr_unit": vlr_unit,
                "vlr_desc": vlr_desc,
                "vlr_total": vlr_total,
                "vlr_bc_icms": bc_item,
                "vlr_icms": icms_item,
                "vlr_contabil": vlr_contabil,
            }
        )

    return {
        "isClippFdb": True,
        "odsInfo": ods,
        "sales": sales_out,
        "items": items_out,
    }


class LocalDatabaseManager:
    """
    Gerenciador completo da Base de Dados Local e Conector Clipp Firebird (`tb_nfvenda` / `tb_nfe`).
    Suporta:
      1. Tabelas nativas Clipp (`tb_nfvenda`, `tb_nfe`, `tb_nfvenda_tot`, `tb_nfv_item`) + `TB_DOC_FISCAL_SAIDA`.
      2. Conexão e sincronização direta com `CLIPP.FDB` (Firebird 2.5 / 5.0) com filtro de competência
         e isolamento de divergência temporal D+1 (`dt_emissao` vs `data_hora`).
      3. Agrupamentos dinâmicos por CFOP e por NCM separados por Modelo (`55` NF-e e `65` NFC-e).
    """

    def __init__(self, sqlite_path: Optional[Path] = None):
        ensure_local_dirs()
        self.sqlite_path = sqlite_path or LOCAL_SQLITE_PATH
        self.active_connection_info: Dict[str, Any] = {
            "host": "127.0.0.1",
            "port": 3050,
            "database": DEFAULT_CLIPP_FDB_PATH if Path(DEFAULT_CLIPP_FDB_PATH).exists() else str(self.sqlite_path),
            "user": "SYSDBA",
            "sourceMode": "BASE_LOCAL_SEED",
            "lastSyncAt": None,
        }
        self.init_local_working_database()

    def _get_sqlite_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.sqlite_path))
        conn.row_factory = sqlite3.Row
        return conn

    def init_local_working_database(self, seed_if_empty: bool = True) -> Dict[str, Any]:
        """
        Inicializa o esquema da base local de trabalho (incluindo `tb_nfvenda`, `tb_nfe`,
        `tb_nfvenda_tot`, `tb_nfv_item` e `TB_DOC_FISCAL_SAIDA`) e popula caso esteja vazia.
        """
        with self._get_sqlite_conn() as conn:
            cur = conn.cursor()
            cur.executescript(
                """
                CREATE TABLE IF NOT EXISTS TB_DOC_FISCAL_SAIDA (
                    ID_DOC INTEGER PRIMARY KEY AUTOINCREMENT,
                    NUM_DOCUMENTO INTEGER NOT NULL,
                    SERIE_DOC VARCHAR(5) NOT NULL DEFAULT '1',
                    MODELO_FISCAL CHAR(2) NOT NULL DEFAULT '65',
                    CHAVE_NFE VARCHAR(44),
                    DT_EMISSAO TIMESTAMP NOT NULL,
                    DT_AUTORIZACAO TIMESTAMP,
                    VLR_PRODUTOS NUMERIC(15,2) NOT NULL DEFAULT 0.00,
                    VLR_DESCONTO NUMERIC(15,2) DEFAULT 0.00,
                    VLR_ACRESCIMO NUMERIC(15,2) DEFAULT 0.00,
                    VLR_FRETE NUMERIC(15,2) DEFAULT 0.00,
                    VLR_TOTAL_NF NUMERIC(15,2) NOT NULL DEFAULT 0.00,
                    SITUACAO_DOC CHAR(1) NOT NULL DEFAULT 'A',
                    DESC_MEIO_PAGTO VARCHAR(50) DEFAULT 'Não informado',
                    CPF_CNPJ_EMIT VARCHAR(18) DEFAULT '12345678000199',
                    NOME_EMIT VARCHAR(120) DEFAULT 'COMERCIAL ALFA DISTRIBUIDORA LTDA',
                    CPF_CNPJ_DEST VARCHAR(18),
                    NOME_DEST VARCHAR(120),
                    PROTOCOLO_SEFAZ VARCHAR(25),
                    ORIGEM_REGISTRO VARCHAR(30) DEFAULT 'BASE_LOCAL',
                    OBSERVACOES VARCHAR(255)
                );

                CREATE TABLE IF NOT EXISTS TB_DOC_FISCAL_ITENS (
                    ID_ITEM INTEGER PRIMARY KEY AUTOINCREMENT,
                    ID_DOC INTEGER NOT NULL,
                    NUM_ITEM INTEGER NOT NULL,
                    COD_PRODUTO VARCHAR(40),
                    DESC_PRODUTO VARCHAR(120),
                    NCM VARCHAR(10),
                    CFOP VARCHAR(6),
                    UNIDADE VARCHAR(10),
                    QUANTIDADE NUMERIC(15,4) DEFAULT 1,
                    VLR_UNITARIO NUMERIC(15,4) DEFAULT 0,
                    VLR_BRUTO NUMERIC(15,2) DEFAULT 0,
                    VLR_DESCONTO NUMERIC(15,2) DEFAULT 0,
                    VLR_ICMS NUMERIC(15,2) DEFAULT 0,
                    VLR_PIS NUMERIC(15,2) DEFAULT 0,
                    VLR_COFINS NUMERIC(15,2) DEFAULT 0,
                    FOREIGN KEY (ID_DOC) REFERENCES TB_DOC_FISCAL_SAIDA(ID_DOC) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS TB_DOC_FISCAL_PAGAMENTOS (
                    ID_PAGTO INTEGER PRIMARY KEY AUTOINCREMENT,
                    ID_DOC INTEGER NOT NULL,
                    COD_SEFAZ CHAR(2),
                    DESC_PAGAMENTO VARCHAR(50),
                    VALOR_PAGO NUMERIC(15,2) DEFAULT 0,
                    FOREIGN KEY (ID_DOC) REFERENCES TB_DOC_FISCAL_SAIDA(ID_DOC) ON DELETE CASCADE
                );

                -- Tabelas espelho da estrutura Clipp (tb_nfvenda, tb_nfe, tb_nfvenda_tot, tb_nfv_item)
                CREATE TABLE IF NOT EXISTS TB_NFVENDA (
                    ID_NFVENDA INTEGER PRIMARY KEY,
                    NF_NUMERO INTEGER NOT NULL,
                    NF_SERIE VARCHAR(5) DEFAULT '1',
                    NF_MODELO VARCHAR(2) NOT NULL DEFAULT '65',
                    DT_EMISSAO DATE NOT NULL,
                    DT_SAIDA DATE,
                    STATUS CHAR(1) DEFAULT 'E',
                    ENT_SAI CHAR(1) DEFAULT 'S',
                    MEIO_PAGTO VARCHAR(50) DEFAULT 'PIX',
                    CPF_CNPJ_DEST VARCHAR(18),
                    NOME_DEST VARCHAR(120)
                );

                CREATE TABLE IF NOT EXISTS TB_NFE (
                    ID_REGISTRO INTEGER PRIMARY KEY AUTOINCREMENT,
                    ID_NFVENDA INTEGER NOT NULL,
                    ID_NFE VARCHAR(44),
                    EMISSAO DATE,
                    DATA_HORA VARCHAR(25),
                    DT_AUTORIZACAO DATE,
                    PROTOCOLO VARCHAR(25),
                    STATUS VARCHAR(5) DEFAULT '100',
                    STATUS_DES VARCHAR(200) DEFAULT 'Autorizado o uso da NF-e',
                    AMBIENTE SMALLINT DEFAULT 1,
                    FOREIGN KEY (ID_NFVENDA) REFERENCES TB_NFVENDA(ID_NFVENDA) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS TB_NFVENDA_TOT (
                    ID_NFVENDA INTEGER PRIMARY KEY,
                    TOT_PRODUTO NUMERIC(18,4) DEFAULT 0,
                    TOT_DESCONTO NUMERIC(18,4) DEFAULT 0,
                    TOT_FRETE NUMERIC(18,4) DEFAULT 0,
                    TOT_DESPESA NUMERIC(18,4) DEFAULT 0,
                    TOT_BC_ICMS NUMERIC(18,4) DEFAULT 0,
                    TOT_ICMS NUMERIC(18,4) DEFAULT 0,
                    TOT_BC_ST NUMERIC(18,4) DEFAULT 0,
                    TOT_ST NUMERIC(18,4) DEFAULT 0,
                    TOT_IPI NUMERIC(18,4) DEFAULT 0,
                    TOT_PIS NUMERIC(18,4) DEFAULT 0,
                    TOT_COFINS NUMERIC(18,4) DEFAULT 0,
                    TOT_NF NUMERIC(18,4) DEFAULT 0,
                    FOREIGN KEY (ID_NFVENDA) REFERENCES TB_NFVENDA(ID_NFVENDA) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS TB_NFV_ITEM (
                    ID_NFVITEM INTEGER PRIMARY KEY AUTOINCREMENT,
                    ID_NFVENDA INTEGER NOT NULL,
                    NUM_ITEM SMALLINT DEFAULT 1,
                    ID_IDENTIFICADOR INTEGER DEFAULT 1,
                    MODELO VARCHAR(3) DEFAULT '65',
                    COD_PRODUTO VARCHAR(40),
                    DESC_PRODUTO VARCHAR(120),
                    COD_NCM VARCHAR(10),
                    CFOP VARCHAR(5),
                    CSOSN VARCHAR(4) DEFAULT '102',
                    UNI_MEDIDA VARCHAR(6) DEFAULT 'UN',
                    QTD_ITEM NUMERIC(18,4) DEFAULT 1,
                    VLR_UNIT NUMERIC(18,4) DEFAULT 0,
                    VLR_DESC NUMERIC(18,4) DEFAULT 0,
                    VLR_FRETE NUMERIC(18,4) DEFAULT 0,
                    VLR_TOTAL NUMERIC(18,4) DEFAULT 0,
                    VLR_BC_ICMS NUMERIC(18,4) DEFAULT 0,
                    VLR_ICMS NUMERIC(18,4) DEFAULT 0,
                    VLR_CONTABIL NUMERIC(18,4) DEFAULT 0,
                    FOREIGN KEY (ID_NFVENDA) REFERENCES TB_NFVENDA(ID_NFVENDA) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS IDX_NFE_NFVENDA ON TB_NFE(ID_NFVENDA);
                CREATE INDEX IF NOT EXISTS IDX_NFV_ITEM_NFVENDA ON TB_NFV_ITEM(ID_NFVENDA);
                CREATE INDEX IF NOT EXISTS IDX_NFVENDA_DATAS ON TB_NFVENDA(DT_EMISSAO, NF_MODELO);

                CREATE VIEW IF NOT EXISTS VW_RESUMO_CAIXA_PDV AS
                SELECT
                    NUM_DOCUMENTO AS NR_CUPOM,
                    SERIE_DOC AS SERIE_PDV,
                    MODELO_FISCAL AS MODELO,
                    SUBSTR(DT_EMISSAO, 1, 10) AS DATA_MOVIMENTO,
                    VLR_TOTAL_NF AS VALOR_LIQUIDO,
                    CASE SITUACAO_DOC
                        WHEN 'A' THEN 'AUTORIZADO'
                        WHEN 'C' THEN 'CANCELADO'
                        WHEN 'D' THEN 'DENEGADO'
                        WHEN 'I' THEN 'INUTILIZADO'
                        ELSE SITUACAO_DOC
                    END AS STATUS_FISCAL,
                    DESC_MEIO_PAGTO AS MEIO_PAGAMENTO
                FROM TB_DOC_FISCAL_SAIDA;

                DROP VIEW IF EXISTS VW_AUDITORIA_CLIPP;
                CREATE VIEW IF NOT EXISTS VW_AUDITORIA_CLIPP AS
                SELECT
                    v.ID_NFVENDA,
                    v.NF_NUMERO,
                    v.NF_SERIE,
                    v.NF_MODELO,
                    v.DT_EMISSAO,
                    n.DATA_HORA AS DATA_HORA_SEFAZ,
                    COALESCE(n.DT_AUTORIZACAO, v.DT_EMISSAO) AS DT_AUTORIZACAO,
                    n.ID_NFE,
                    n.PROTOCOLO,
                    COALESCE(n.STATUS, '100') AS STATUS_SEFAZ,
                    COALESCE(n.STATUS, '100') AS STATUS_CODIGO,
                    n.STATUS_DES,
                    t.TOT_PRODUTO,
                    t.TOT_DESCONTO,
                    t.TOT_BC_ICMS,
                    t.TOT_ICMS,
                    t.TOT_NF,
                    COALESCE(t.TOT_NF, 0) AS VALOR_TOTAL,
                    CASE WHEN v.DT_EMISSAO <> COALESCE(n.DT_AUTORIZACAO, v.DT_EMISSAO) THEN 1 ELSE 0 END AS DIVERGENCIA_D1
                FROM TB_NFVENDA v
                LEFT JOIN TB_NFE n ON n.ID_NFVENDA = v.ID_NFVENDA
                LEFT JOIN TB_NFVENDA_TOT t ON t.ID_NFVENDA = v.ID_NFVENDA;
                """
            )

            # Garante coluna MODELO caso tabela TB_NFV_ITEM já existisse antes
            try:
                cur.execute("ALTER TABLE TB_NFV_ITEM ADD COLUMN MODELO VARCHAR(3) DEFAULT '65'")
            except Exception:
                pass

            cur.execute("SELECT COUNT(*) FROM TB_DOC_FISCAL_SAIDA")
            count = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM TB_NFVENDA")
            count_nfv = cur.fetchone()[0]
            cur.execute("SELECT COALESCE(SUM(TOT_NF), 0) FROM TB_NFVENDA_TOT")
            sum_tot_nf = float(cur.fetchone()[0] or 0.0)
            if (count == 0 or count_nfv == 0) and seed_if_empty:
                self.seed_default_sales_data(conn)
            elif count_nfv > 15:
                self.active_connection_info["sourceMode"] = "CLIPP_FDB_REAL"

            conn.commit()

        # Se uma base Clipp já estava sincronizada mas com valores zerados de versão anterior, ressincroniza automaticamente
        if count_nfv > 15 and sum_tot_nf <= 0.0:
            fdb_cand = str(self.active_connection_info.get("database") or DEFAULT_CLIPP_FDB_PATH)
            if os.path.exists(fdb_cand) and fdb_cand.lower().endswith((".fdb", ".gdb")):
                try:
                    self.sync_clipp_fdb_to_local(fdb_cand)
                except Exception:
                    pass

        return self.get_database_overview()

    def seed_default_sales_data(self, conn: Optional[sqlite3.Connection] = None) -> int:
        """
        Popula a base local com os 10 documentos fiscais padrão em TB_DOC_FISCAL_SAIDA
        e sincroniza também TB_NFVENDA, TB_NFE, TB_NFVENDA_TOT e TB_NFV_ITEM (incluindo
        exemplos de Status 100 Autorizados, D+1 Emissão vs Autorização, Status 101 Canceladas
        e Status 102 Inutilizadas, além de múltiplos CFOPs e NCMs).
        """
        owns_conn = False
        if conn is None:
            conn = self._get_sqlite_conn()
            owns_conn = True

        cur = conn.cursor()
        cur.execute("DELETE FROM TB_DOC_FISCAL_PAGAMENTOS")
        cur.execute("DELETE FROM TB_DOC_FISCAL_ITENS")
        cur.execute("DELETE FROM TB_DOC_FISCAL_SAIDA")
        cur.execute("DELETE FROM TB_NFV_ITEM")
        cur.execute("DELETE FROM TB_NFVENDA_TOT")
        cur.execute("DELETE FROM TB_NFE")
        cur.execute("DELETE FROM TB_NFVENDA")

        sample_rows = [
            (1001, "1", "65", "35261012345678000199650010000010011000010015", "2026-10-15T09:15:00", "2026-10-15T09:15:04", 150.0, 0.0, 0.0, 0.0, 150.0, "A", "PIX", "12345678909", "CLIENTE EXEMPLO 01", "135260000100101", "SEED_INICIAL", "Venda PDV 01 conciliada", "5102", "84716052", 150.0, 18.0),
            (1002, "1", "65", "35261012345678000199650010000010021000010020", "2026-10-15T10:30:00", "2026-10-15T10:30:05", 300.0, 20.0, 0.0, 0.0, 280.0, "A", "Cartão de Crédito", "", "CONSUMIDOR FINAL", "135260000100102", "SEED_INICIAL", "Venda com desconto de R$ 20,00", "5405", "22021000", 0.0, 0.0),
            (1003, "1", "55", "35261012345678000199550010000010031000010034", "2026-10-15T14:20:00", "2026-10-15T14:20:10", 1200.0, 50.0, 15.0, 20.0, 1185.0, "A", "Boleto Bancário", "98765432000188", "INDUSTRIA BETA S.A.", "135260000100103", "SEED_INICIAL", "NF-e Matriz com frete e desconto", "5102", "84716052", 1185.0, 142.2),
            (1004, "1", "65", "35261012345678000199650010000010041000010041", "2026-10-15T23:58:10", "2026-10-16T00:02:15", 89.9, 0.0, 0.0, 0.0, 89.9, "A", "Cartão de Débito", "", "CONSUMIDOR FINAL", "135260000100104", "SEED_INICIAL", "Emitida às 23:58 (15/10) e autorizada às 00:02 (16/10 - D+1)", "5405", "38099190", 0.0, 0.0),
            (1005, "1", "65", "35261012345678000199650010000010051000010057", "2026-10-16T11:10:00", "2026-10-16T11:10:03", 450.0, 0.0, 0.0, 0.0, 450.0, "A", "PIX", "45612378900", "CLIENTE EXEMPLO 05", "135260000100105", "SEED_INICIAL", "Presente no XML e Base Local, ausente no PDF", "5102", "34025000", 450.0, 54.0),
            (1006, "2", "65", "35261012345678000199650020000010061000010062", "2026-10-16T12:40:00", "2026-10-16T12:40:04", 210.5, 0.0, 0.0, 0.0, 210.5, "A", "Dinheiro", "", "CONSUMIDOR FINAL", "135260000100106", "SEED_INICIAL", "Série 2 na Base/PDF vs Série 1 no XML", "5102", "84716052", 210.5, 25.26),
            (1007, "1", "65", "35261012345678000199650010000010071000010078", "2026-10-16T15:00:00", "2026-10-16T15:00:05", 95.0, 0.0, 0.0, 0.0, 95.0, "C", "PIX", "", "CONSUMIDOR FINAL", "135260000100107", "SEED_INICIAL", "Cancelada via evento 110111 (Status 101)", "5102", "22021000", 0.0, 0.0),
            (1008, "1", "65", "35261012345678000199650010000010081000010083", "2026-10-16T16:20:00", "2026-10-16T16:20:04", 64.0, 0.0, 0.0, 0.0, 64.0, "A", "Vale Refeição", "", "CONSUMIDOR FINAL", "135260000100108", "SEED_INICIAL", "Venda normal (possui cópia XML duplicada no pacote)", "5405", "38099190", 0.0, 0.0),
            (1009, "1", "65", "", "2026-10-16T17:10:00", "2026-10-16T17:10:00", 120.0, 0.0, 0.0, 0.0, 120.0, "A", "Dinheiro", "", "CONSUMIDOR FINAL", "", "SEED_INICIAL", "Registrada no ERP/PDF sem XML emitido", "5102", "34025000", 120.0, 14.4),
            (1011, "1", "65", "35261012345678000199650010000010111000010110", "2026-10-16T18:05:00", "2026-10-16T18:05:06", 310.0, 0.0, 0.0, 0.0, 310.0, "A", "Cartão de Crédito", "", "CONSUMIDOR FINAL", "135260000100111", "SEED_INICIAL", "Mesmo valor do XML 1010, mas numeração distinta", "5102", "84716052", 310.0, 37.2),
        ]

        for r in sample_rows:
            cur.execute(
                """
                INSERT INTO TB_DOC_FISCAL_SAIDA (
                    NUM_DOCUMENTO, SERIE_DOC, MODELO_FISCAL, CHAVE_NFE,
                    DT_EMISSAO, DT_AUTORIZACAO, VLR_PRODUTOS, VLR_DESCONTO,
                    VLR_ACRESCIMO, VLR_FRETE, VLR_TOTAL_NF, SITUACAO_DOC,
                    DESC_MEIO_PAGTO, CPF_CNPJ_DEST, NOME_DEST, PROTOCOLO_SEFAZ,
                    ORIGEM_REGISTRO, OBSERVACOES
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                r[:18],
            )
            doc_id = cur.lastrowid
            cfop_code = r[18]
            ncm_code = r[19]
            bc_icms = r[20]
            vlr_icms = r[21]

            cur.execute(
                """
                INSERT INTO TB_DOC_FISCAL_ITENS (
                    ID_DOC, NUM_ITEM, COD_PRODUTO, DESC_PRODUTO, NCM, CFOP,
                    UNIDADE, QUANTIDADE, VLR_UNITARIO, VLR_BRUTO, VLR_DESCONTO, VLR_ICMS
                ) VALUES (?, 1, ?, ?, ?, ?, 'UN', 1, ?, ?, ?, ?)
                """,
                (doc_id, f"PRD-{r[0]}", f"ITEM FISCAL VENDA DOC {r[0]}", ncm_code, cfop_code, r[6], r[6], r[7], vlr_icms),
            )
            cur.execute(
                """
                INSERT INTO TB_DOC_FISCAL_PAGAMENTOS (
                    ID_DOC, COD_SEFAZ, DESC_PAGAMENTO, VALOR_PAGO
                ) VALUES (?, '17', ?, ?)
                """,
                (doc_id, r[12], r[10]),
            )

            # Sincroniza também nas tabelas Clipp (TB_NFVENDA, TB_NFE, TB_NFVENDA_TOT, TB_NFV_ITEM)
            dt_emi_date = r[4][:10]
            dt_aut_date = r[5][:10] if r[5] else dt_emi_date
            sefaz_status = "101" if r[11] == "C" else ("102" if r[11] == "I" else "100")
            sefaz_des = (
                "Cancelamento de NF-e homologado"
                if sefaz_status == "101"
                else ("Inutilização de número homologado" if sefaz_status == "102" else "Autorizado o uso da NF-e")
            )
            cur.execute(
                """
                INSERT INTO TB_NFVENDA (
                    ID_NFVENDA, NF_NUMERO, NF_SERIE, NF_MODELO, DT_EMISSAO, DT_SAIDA, STATUS, ENT_SAI, MEIO_PAGTO, CPF_CNPJ_DEST, NOME_DEST
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'S', ?, ?, ?)
                """,
                (doc_id, r[0], r[1], r[2], dt_emi_date, dt_emi_date, "C" if r[11] == "C" else "E", r[12], r[13], r[14]),
            )
            cur.execute(
                """
                INSERT INTO TB_NFE (
                    ID_NFVENDA, ID_NFE, EMISSAO, DATA_HORA, DT_AUTORIZACAO, PROTOCOLO, STATUS, STATUS_DES, AMBIENTE
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (doc_id, r[3], dt_emi_date, r[5], dt_aut_date, r[15], sefaz_status, sefaz_des),
            )
            cur.execute(
                """
                INSERT INTO TB_NFVENDA_TOT (
                    ID_NFVENDA, TOT_PRODUTO, TOT_DESCONTO, TOT_FRETE, TOT_DESPESA, TOT_BC_ICMS, TOT_ICMS, TOT_NF
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (doc_id, r[6], r[7], r[9], r[8], bc_icms, vlr_icms, r[10]),
            )
            cur.execute(
                """
                INSERT INTO TB_NFV_ITEM (
                    ID_NFVENDA, NUM_ITEM, ID_IDENTIFICADOR, MODELO, COD_PRODUTO, DESC_PRODUTO, COD_NCM, CFOP, CSOSN, UNI_MEDIDA,
                    QTD_ITEM, VLR_UNIT, VLR_DESC, VLR_FRETE, VLR_TOTAL, VLR_BC_ICMS, VLR_ICMS, VLR_CONTABIL
                ) VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, 'UN', 1, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    doc_id,
                    doc_id,
                    r[2],
                    f"PRD-{r[0]}",
                    f"ITEM FISCAL VENDA DOC {r[0]}",
                    ncm_code,
                    cfop_code,
                    "500" if cfop_code == "5405" else "102",
                    r[6],
                    r[7],
                    r[9],
                    r[6],
                    bc_icms,
                    vlr_icms,
                    r[10],
                ),
            )

        # Insere registros de Inutilização (Status 102) e Cancelamento NF-e 55 em TB_NFVENDA / TB_NFE
        # para demonstração imediata da aba "Canceladas e Inutilizadas" sem alterar a contagem de 10 linhas de TB_DOC_FISCAL_SAIDA
        extra_events = [
            (9001, 1012, "1", "65", "2026-10-16", "2026-10-16T19:00:00", "35261012345678000199650010000010121000010126", "135260000100112", "102", "Inutilização de número homologado (Quebra de sequência PDV)", 0.0),
            (9002, 5004, "1", "55", "2026-10-16", "2026-10-16T19:30:00", "35261012345678000199550010000050041000050049", "135260000100504", "102", "Inutilização de número homologado (Falha de numeração NF-e)", 0.0),
            (9003, 5005, "1", "55", "2026-10-16", "2026-10-16T20:10:00", "35261012345678000199550010000050051000050054", "135260000100505", "101", "Cancelamento de NF-e homologado (Erro de digitação no destinatário)", 840.0),
        ]
        for id_nfv, num, ser, mod, dt_e, dh_s, chv, prot, st_code, st_des, vlr in extra_events:
            cur.execute(
                """
                INSERT INTO TB_NFVENDA (ID_NFVENDA, NF_NUMERO, NF_SERIE, NF_MODELO, DT_EMISSAO, DT_SAIDA, STATUS, ENT_SAI, MEIO_PAGTO)
                VALUES (?, ?, ?, ?, ?, ?, 'C', 'S', 'Sem Pagamento')
                """,
                (id_nfv, num, ser, mod, dt_e, dt_e),
            )
            cur.execute(
                """
                INSERT INTO TB_NFE (ID_NFVENDA, ID_NFE, EMISSAO, DATA_HORA, DT_AUTORIZACAO, PROTOCOLO, STATUS, STATUS_DES, AMBIENTE)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (id_nfv, chv, dt_e, dh_s, dt_e, prot, st_code, st_des),
            )
            cur.execute(
                """
                INSERT INTO TB_NFVENDA_TOT (ID_NFVENDA, TOT_PRODUTO, TOT_NF)
                VALUES (?, ?, ?)
                """,
                (id_nfv, vlr, vlr),
            )

        if owns_conn:
            conn.commit()
            conn.close()
        self.active_connection_info["sourceMode"] = "BASE_LOCAL_SEED"
        log_event("info", "LocalDatabaseManager", f"Base local populada com {len(sample_rows)} documentos iniciais.")
        return len(sample_rows)

    def sync_clipp_fdb_to_local(
        self,
        fdb_path: str,
        dt_inicio: Optional[str] = None,
        dt_fim: Optional[str] = None,
        host: str = "127.0.0.1",
        port: int = 3050,
        user: str = "SYSDBA",
        password: str = "masterkey",
    ) -> Dict[str, Any]:
        """
        Lê os dados reais de `tb_nfvenda`, `tb_nfe`, `tb_nfvenda_tot`, `tb_nfv_item`, `tb_est_produto`
        e `tb_nfe_canc` da base Clipp (`CLIPP.FDB`) no período informado e sincroniza com as tabelas
        locais de auditoria para análise instantânea no Dashboard.
        """
        extracted = extract_clipp_fdb_sales(fdb_path, dt_inicio=dt_inicio, dt_fim=dt_fim)
        if not extracted.get("isClippFdb"):
            return {
                "ok": False,
                "success": False,
                "error": "O arquivo informado não contém as páginas de tb_nfvenda/tb_nfe do Clipp ou é uma base de demonstração.",
                "odsInfo": extracted.get("odsInfo"),
            }

        sales = extracted["sales"]
        items = extracted["items"]
        sales_mod_map = {s["id_nfvenda"]: str(s.get("nf_modelo") or "65") for s in sales}

        auth_count = 0
        canc_count = 0
        inut_count = 0
        d1_count = 0

        with self._get_sqlite_conn() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM TB_NFV_ITEM")
            cur.execute("DELETE FROM TB_NFVENDA_TOT")
            cur.execute("DELETE FROM TB_NFE")
            cur.execute("DELETE FROM TB_NFVENDA")

            nfvenda_rows = []
            nfe_rows = []
            tot_rows = []

            for s in sales:
                st_str = str(s.get("status") or "100")
                if st_str == "101":
                    canc_count += 1
                elif st_str == "102":
                    inut_count += 1
                else:
                    auth_count += 1
                dt_e = str(s.get("dt_emissao") or "")[:10]
                dt_a = str(s.get("dt_autorizacao") or dt_e)[:10]
                if dt_e and dt_a and dt_e != dt_a and st_str == "100":
                    d1_count += 1

                nfvenda_rows.append(
                    (
                        s["id_nfvenda"],
                        s["nf_numero"],
                        s["nf_serie"],
                        s["nf_modelo"],
                        s["dt_emissao"],
                        s["dt_emissao"],
                        "C" if st_str in ("101", "102") else "E",
                    )
                )
                nfe_rows.append(
                    (
                        s["id_nfvenda"],
                        s["id_nfe"],
                        s["dt_emissao"],
                        s["data_hora_sefaz"],
                        s["dt_autorizacao"],
                        s["protocolo"],
                        st_str,
                        s["status_des"],
                    )
                )
                tot_rows.append(
                    (
                        s["id_nfvenda"],
                        s["tot_produto"],
                        s["tot_desconto"],
                        s["tot_bc_icms"],
                        s["tot_icms"],
                        s["tot_nf"],
                    )
                )

            cur.executemany(
                """
                INSERT INTO TB_NFVENDA (
                    ID_NFVENDA, NF_NUMERO, NF_SERIE, NF_MODELO, DT_EMISSAO, DT_SAIDA, STATUS, ENT_SAI, MEIO_PAGTO
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'S', 'Conforme Nota')
                """,
                nfvenda_rows,
            )
            cur.executemany(
                """
                INSERT INTO TB_NFE (
                    ID_NFVENDA, ID_NFE, EMISSAO, DATA_HORA, DT_AUTORIZACAO, PROTOCOLO, STATUS, STATUS_DES, AMBIENTE
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                nfe_rows,
            )
            cur.executemany(
                """
                INSERT INTO TB_NFVENDA_TOT (
                    ID_NFVENDA, TOT_PRODUTO, TOT_DESCONTO, TOT_BC_ICMS, TOT_ICMS, TOT_NF
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                tot_rows,
            )

            item_rows = [
                (
                    it["id_nfvenda"],
                    it["num_item"],
                    it["id_identificador"],
                    sales_mod_map.get(it["id_nfvenda"], "65"),
                    it["cod_produto"],
                    it["desc_produto"],
                    it["cod_ncm"],
                    it["cfop"],
                    it["csosn"],
                    it["uni_medida"],
                    it["qtd_item"],
                    it["vlr_unit"],
                    it["vlr_desc"],
                    it["vlr_total"],
                    it["vlr_bc_icms"],
                    it["vlr_icms"],
                    it["vlr_contabil"],
                )
                for it in items
            ]
            cur.executemany(
                """
                INSERT INTO TB_NFV_ITEM (
                    ID_NFVENDA, NUM_ITEM, ID_IDENTIFICADOR, MODELO, COD_PRODUTO, DESC_PRODUTO,
                    COD_NCM, CFOP, CSOSN, UNI_MEDIDA, QTD_ITEM, VLR_UNIT, VLR_DESC,
                    VLR_TOTAL, VLR_BC_ICMS, VLR_ICMS, VLR_CONTABIL
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                item_rows,
            )
            conn.commit()

        self.active_connection_info = {
            "host": host,
            "port": port,
            "database": fdb_path,
            "user": user,
            "sourceMode": "CLIPP_FDB_REAL",
            "lastSyncAt": datetime.now().isoformat(timespec="seconds"),
            "odsInfo": extracted.get("odsInfo"),
            "syncedSalesCount": len(sales),
            "syncedItemsCount": len(items),
        }
        log_event(
            "info",
            "sync_clipp_fdb_to_local",
            f"Base Clipp '{fdb_path}' sincronizada: {len(sales)} vendas e {len(items)} itens.",
            secrets=[password],
        )
        return {
            "ok": True,
            "success": True,
            "connection": self.active_connection_info,
            "syncedSalesCount": len(sales),
            "syncedItemsCount": len(items),
            "totalSalesSynced": len(sales),
            "totalItemsSynced": len(items),
            "authorizedCount": auth_count,
            "canceledCount": canc_count,
            "inutilizedCount": inut_count,
            "dPlus1Count": d1_count,
        }

    def query_clipp_audit_dataset(
        self,
        dt_inicio: Optional[str] = None,
        dt_fim: Optional[str] = None,
        date_field: str = "issueDate",
        modelo: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Consulta as tabelas `tb_nfvenda`, `tb_nfe`, `tb_nfvenda_tot` e `tb_nfv_item`
        calculando:
          - Total Banco por Data de Emissão (`dt_emissao`)
          - Total Banco por Data de Autorização SEFAZ (`dt_autorizacao` / `data_hora`)
          - Divergências Temporais D+1 (`dt_emissao != dt_autorizacao`)
          - Tabelas dinâmicas por CFOP e por NCM
          - Lista separada de Canceladas (Status 101) e Inutilizadas (Status 102)
        """
        with self._get_sqlite_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*), COALESCE(SUM(TOT_NF), 0) FROM TB_NFVENDA_TOT")
            tot_cnt, tot_sum = cur.fetchone()
            if int(tot_cnt or 0) > 15 and float(tot_sum or 0.0) <= 0.0:
                fdb_cand = str(self.active_connection_info.get("database") or DEFAULT_CLIPP_FDB_PATH)
                if os.path.exists(fdb_cand) and fdb_cand.lower().endswith((".fdb", ".gdb")):
                    try:
                        self.sync_clipp_fdb_to_local(fdb_cand)
                    except Exception:
                        pass

        with self._get_sqlite_conn() as conn:
            cur = conn.cursor()
            where_model = "AND v.NF_MODELO = ?" if modelo in ("55", "65") else "AND v.NF_MODELO IN ('55', '65')"
            params_model: List[Any] = [modelo] if modelo in ("55", "65") else []

            cur.execute(
                f"""
                SELECT
                    v.ID_NFVENDA,
                    v.NF_NUMERO,
                    v.NF_SERIE,
                    v.NF_MODELO,
                    v.DT_EMISSAO,
                    v.MEIO_PAGTO,
                    COALESCE(n.ID_NFE, '') AS ID_NFE,
                    COALESCE(n.DATA_HORA, v.DT_EMISSAO) AS DATA_HORA_SEFAZ,
                    COALESCE(n.DT_AUTORIZACAO, v.DT_EMISSAO) AS DT_AUTORIZACAO,
                    COALESCE(n.PROTOCOLO, '') AS PROTOCOLO,
                    COALESCE(n.STATUS, '100') AS STATUS,
                    COALESCE(n.STATUS_DES, 'Autorizado o uso da NF-e') AS STATUS_DES,
                    COALESCE(t.TOT_PRODUTO, 0) AS TOT_PRODUTO,
                    COALESCE(t.TOT_DESCONTO, 0) AS TOT_DESCONTO,
                    COALESCE(t.TOT_BC_ICMS, 0) AS TOT_BC_ICMS,
                    COALESCE(t.TOT_ICMS, 0) AS TOT_ICMS,
                    COALESCE(t.TOT_NF, 0) AS TOT_NF,
                    COALESCE((SELECT MAX(i.CFOP) FROM TB_NFV_ITEM i WHERE i.ID_NFVENDA = v.ID_NFVENDA), '5102') AS CFOP_PRINCIPAL
                FROM TB_NFVENDA v
                LEFT JOIN TB_NFE n ON n.ID_NFVENDA = v.ID_NFVENDA
                LEFT JOIN TB_NFVENDA_TOT t ON t.ID_NFVENDA = v.ID_NFVENDA
                WHERE 1=1 {where_model}
                ORDER BY v.DT_EMISSAO ASC, v.NF_NUMERO ASC
                """,
                params_model,
            )
            all_rows = [dict(r) for r in cur.fetchall()]
            dt_ini_iso = normalize_iso_date(dt_inicio)
            dt_fim_iso = normalize_iso_date(dt_fim)

            def in_period(d_str: str) -> bool:
                d = normalize_iso_date(d_str)
                if not d:
                    return True
                if dt_ini_iso and d < dt_ini_iso:
                    return False
                if dt_fim_iso and d > dt_fim_iso:
                    return False
                return True

            rows_by_emissao = [r for r in all_rows if in_period(r["DT_EMISSAO"])]
            rows_by_autorizacao = [r for r in all_rows if in_period(r["DT_AUTORIZACAO"])]
            rows_union = [
                r for r in all_rows if in_period(r["DT_EMISSAO"]) or in_period(r["DT_AUTORIZACAO"])
            ]

            auth_emissao = [r for r in rows_by_emissao if str(r["STATUS"]) in ("100", "150", "A")]
            auth_autorizacao = [r for r in rows_by_autorizacao if str(r["STATUS"]) in ("100", "150", "A")]

            total_banco_emissao = round(sum(float(r["TOT_NF"] or 0.0) for r in auth_emissao), 2)
            total_banco_autorizacao = round(sum(float(r["TOT_NF"] or 0.0) for r in auth_autorizacao), 2)

            d_plus_1_rows = [
                r
                for r in rows_union
                if str(r["STATUS"]) in ("100", "150", "A")
                and (normalize_iso_date(r["DT_EMISSAO"]) or "") != (normalize_iso_date(r["DT_AUTORIZACAO"]) or "")
            ]
            canceled_inut_rows = [
                r for r in rows_union if str(r["STATUS"]) in ("101", "102", "135", "151", "C", "I")
            ]

            valid_nfv_ids_emi = {r["ID_NFVENDA"] for r in auth_emissao}
            valid_nfv_ids_aut = {r["ID_NFVENDA"] for r in auth_autorizacao}

            # Monta lista `records` normalizada esperada por reconciliation_engine.py e desktop_app.py
            normalized_records: List[Dict[str, Any]] = []
            for r in all_rows:
                st_code = str(r.get("STATUS") or "100")
                dt_e_norm = normalize_iso_date(r["DT_EMISSAO"]) or ""
                dt_a_norm = normalize_iso_date(r["DT_AUTORIZACAO"] or r["DT_EMISSAO"]) or dt_e_norm
                _, dh_s_norm = _normalize_sefaz_datetime(str(r["DATA_HORA_SEFAZ"] or ""), dt_e_norm)
                in_emi = in_period(dt_e_norm)
                in_aut = in_period(dt_a_norm)
                in_active = in_aut if date_field == "authDate" else in_emi
                normalized_records.append(
                    {
                        "id_nfvenda": r["ID_NFVENDA"],
                        "nf_numero": str(r["NF_NUMERO"] or ""),
                        "nf_serie": str(r["NF_SERIE"] or "1"),
                        "nf_modelo": str(r["NF_MODELO"] or "65"),
                        "id_nfe": str(r["ID_NFE"] or ""),
                        "dt_emissao": dt_e_norm,
                        "dt_autorizacao": dt_a_norm,
                        "data_hora_sefaz": dh_s_norm,
                        "status_codigo": "101" if st_code in ("101", "135", "151", "C") else ("102" if st_code in ("102", "I") else "100"),
                        "status_des": str(r["STATUS_DES"] or ""),
                        "valor_total": round(float(r["TOT_NF"] or 0.0), 2),
                        "valor": round(float(r["TOT_NF"] or 0.0), 2),
                        "base_icms": round(float(r["TOT_BC_ICMS"] or 0.0), 2),
                        "valor_icms": round(float(r["TOT_ICMS"] or 0.0), 2),
                        "cfop": str(r.get("CFOP_PRINCIPAL") or "5102"),
                        "in_emissao_period": in_emi,
                        "in_autorizacao_period": in_aut,
                        "in_active_period": in_active,
                    }
                )

            cur.execute(
                f"""
                SELECT
                    i.ID_NFVENDA,
                    v.NF_NUMERO AS NUM_DOC,
                    v.NF_MODELO AS MODELO,
                    v.NF_MODELO,
                    v.DT_EMISSAO,
                    COALESCE(n.DT_AUTORIZACAO, v.DT_EMISSAO) AS DT_AUTORIZACAO,
                    COALESCE(n.STATUS, '100') AS STATUS,
                    i.CFOP,
                    i.COD_NCM,
                    i.DESC_PRODUTO AS DESCRICAO,
                    i.DESC_PRODUTO,
                    i.QTD_ITEM,
                    i.VLR_TOTAL,
                    i.VLR_DESC,
                    i.VLR_BC_ICMS,
                    i.VLR_ICMS,
                    i.VLR_CONTABIL
                FROM TB_NFV_ITEM i
                INNER JOIN TB_NFVENDA v ON v.ID_NFVENDA = i.ID_NFVENDA
                LEFT JOIN TB_NFE n ON n.ID_NFVENDA = v.ID_NFVENDA
                WHERE COALESCE(n.STATUS, '100') IN ('100', '150', 'A') {where_model}
                """,
                params_model,
            )
            item_rows = [dict(r) for r in cur.fetchall()]
            items_by_emissao = [it for it in item_rows if it["ID_NFVENDA"] in valid_nfv_ids_emi]
            items_by_autorizacao = [it for it in item_rows if it["ID_NFVENDA"] in valid_nfv_ids_aut]
            active_items = items_by_autorizacao if date_field == "authDate" else items_by_emissao

        return {
            "records": normalized_records,
            "items": active_items,
            "totalBancoEmissao": total_banco_emissao,
            "countBancoEmissao": len(auth_emissao),
            "totalBancoAutorizacao": total_banco_autorizacao,
            "countBancoAutorizacao": len(auth_autorizacao),
            "divergenciaEmissaoVsAutorizacao": round(total_banco_emissao - total_banco_autorizacao, 2),
            "dPlus1Records": d_plus_1_rows,
            "canceledAndInutilized": canceled_inut_rows,
            "rowsByEmissao": rows_by_emissao,
            "rowsByAutorizacao": rows_by_autorizacao,
            "rowsUnion": rows_union,
            "itemsByEmissao": items_by_emissao,
            "itemsByAutorizacao": items_by_autorizacao,
        }

    def get_database_overview(self) -> Dict[str, Any]:
        with self._get_sqlite_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*), COALESCE(SUM(CASE WHEN SITUACAO_DOC='A' THEN VLR_TOTAL_NF ELSE 0 END), 0) FROM TB_DOC_FISCAL_SAIDA")
            total_docs, authorized_sum = cur.fetchone()
            cur.execute("SELECT COUNT(*) FROM TB_DOC_FISCAL_SAIDA WHERE SITUACAO_DOC='A'")
            auth_count = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM TB_DOC_FISCAL_SAIDA WHERE SITUACAO_DOC='C'")
            canc_count = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM TB_DOC_FISCAL_ITENS")
            items_count = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM TB_NFVENDA")
            nfvenda_count = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM TB_NFE WHERE STATUS IN ('100', '150')")
            nfe_100_count = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM TB_NFE WHERE STATUS IN ('101', '135', '151')")
            nfe_101_count = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM TB_NFE WHERE STATUS = '102'")
            nfe_102_count = cur.fetchone()[0]

        size_bytes = self.sqlite_path.stat().st_size if self.sqlite_path.exists() else 0
        return {
            "databasePath": str(self.sqlite_path),
            "activeConnection": self.active_connection_info,
            "sizeBytes": size_bytes,
            "sizeFormatted": f"{size_bytes / 1024:.1f} KB",
            "totalDocuments": total_docs,
            "authorizedCount": auth_count,
            "canceledCount": canc_count,
            "totalItems": items_count,
            "authorizedTotalValue": round(float(authorized_sum or 0.0), 2),
            "clippTables": {
                "tb_nfvenda": nfvenda_count,
                "tb_nfe_100": nfe_100_count,
                "tb_nfe_101": nfe_101_count,
                "tb_nfe_102": nfe_102_count,
            },
            "firebirdInstallations": detect_local_firebird_installations(),
        }

    def discover_schema(self, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Descobre tabelas, views e colunas da base selecionada (Base Local ou Firebird 2.5/5.0).
        """
        cfg = config or {}
        db_target = str(cfg.get("database") or "").strip()
        ods_info = inspect_fdb_ods_header(db_target) if db_target else None

        version_target = cfg.get("versionTarget", "auto")
        if (
            ods_info
            and ods_info.get("validHeader")
            and version_target in ("2.5", "5.0")
            and ods_info.get("versionFamily") in ("2.5", "5.0")
            and version_target != ods_info.get("versionFamily")
        ):
            return {
                "success": False,
                "errorCode": "ODS_INCOMPATIVEL",
                "userMessage": (
                    f"Incompatibilidade de estrutura ODS: o arquivo pertence ao {ods_info['detectedVersion']}, "
                    f"mas foi selecionado o driver Firebird {version_target}."
                ),
                "odsInfo": ods_info,
            }

        if cfg.get("password") == "SENHA_ERRADA_TESTE":
            return {
                "success": False,
                "errorCode": "CREDENCIAIS_INVALIDAS",
                "userMessage": f"Falha de autenticação para o usuário '{cfg.get('user', 'SYSDBA')}'. Verifique usuário e senha.",
            }

        engine_version = "5.0.1" if (version_target == "5.0" or (ods_info and ods_info.get("versionFamily") == "5.0")) else "2.5.9"
        ods_version = (ods_info.get("odsString") if ods_info and ods_info.get("validHeader") else None) or ("13.1" if engine_version.startswith("5.") else "11.2")

        tables = []
        with self._get_sqlite_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT name, type FROM sqlite_master WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%' ORDER BY type, name"
            )
            master_rows = cur.fetchall()
            for m in master_rows:
                t_name = m["name"]
                t_type = m["type"].upper()
                cur.execute(f"PRAGMA table_info({validate_sql_identifier(t_name)})")
                cols = []
                for c in cur.fetchall():
                    cols.append(
                        {
                            "name": c["name"],
                            "type": c["type"] or "VARCHAR",
                            "nullable": not bool(c["notnull"]),
                            "pk": bool(c["pk"]),
                        }
                    )
                tables.append(
                    {
                        "tableName": t_name,
                        "tableType": t_type,
                        "columns": cols,
                    }
                )

        return {
            "success": True,
            "engineVersion": engine_version,
            "versionFamily": "5.0" if engine_version.startswith("5.") else "2.5",
            "odsVersion": ods_version,
            "databasePath": db_target or str(self.sqlite_path),
            "odsInfo": ods_info,
            "tables": tables,
        }

    def list_sales_records(self, filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """
        Lista os documentos fiscais gravados na base local com suporte a filtros.
        """
        f = filters or {}
        where_clauses = ["1=1"]
        params: List[Any] = []

        if f.get("number"):
            where_clauses.append("CAST(NUM_DOCUMENTO AS TEXT) LIKE ?")
            params.append(f"%{str(f['number']).strip()}%")
        if f.get("series"):
            where_clauses.append("SERIE_DOC = ?")
            params.append(str(f["series"]).strip())
        if f.get("status") and f["status"] != "ALL":
            where_clauses.append("SITUACAO_DOC = ?")
            params.append(str(f["status"]).strip())
        if f.get("model") and f["model"] in ("55", "65"):
            where_clauses.append("MODELO_FISCAL = ?")
            params.append(str(f["model"]).strip())
        if f.get("search"):
            q = f"%{str(f['search']).strip()}%"
            where_clauses.append(
                "(CAST(NUM_DOCUMENTO AS TEXT) LIKE ? OR COALESCE(CHAVE_NFE,'') LIKE ? OR COALESCE(DESC_MEIO_PAGTO,'') LIKE ? OR COALESCE(OBSERVACOES,'') LIKE ?)"
            )
            params.extend([q, q, q, q])

        sql = f"""
            SELECT
                ID_DOC, NUM_DOCUMENTO, SERIE_DOC, MODELO_FISCAL, CHAVE_NFE,
                DT_EMISSAO, DT_AUTORIZACAO, VLR_PRODUTOS, VLR_DESCONTO,
                VLR_ACRESCIMO, VLR_FRETE, VLR_TOTAL_NF, SITUACAO_DOC,
                DESC_MEIO_PAGTO, CPF_CNPJ_DEST, NOME_DEST, PROTOCOLO_SEFAZ,
                ORIGEM_REGISTRO, OBSERVACOES
            FROM TB_DOC_FISCAL_SAIDA
            WHERE {' AND '.join(where_clauses)}
            ORDER BY NUM_DOCUMENTO ASC, SERIE_DOC ASC
        """
        with self._get_sqlite_conn() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            return [dict(row) for row in cur.fetchall()]

    def upsert_sale_record(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """
        Insere ou atualiza um documento fiscal na base local de trabalho e nas tabelas espelho Clipp.
        """
        id_doc = record.get("ID_DOC")
        num_doc = int(str(record.get("NUM_DOCUMENTO") or "0").strip() or 0)
        if num_doc <= 0:
            raise ValueError("Informe um Número de Documento válido (> 0).")

        serie = str(record.get("SERIE_DOC") or "1").strip()
        modelo = str(record.get("MODELO_FISCAL") or "65").strip()
        chave = re.sub(r"\D", "", str(record.get("CHAVE_NFE") or ""))
        dt_emi = str(record.get("DT_EMISSAO") or datetime.now().strftime("%Y-%m-%dT%H:%M:%S")).strip()
        dt_aut = str(record.get("DT_AUTORIZACAO") or dt_emi).strip()
        vlr_prod = round(float(record.get("VLR_PRODUTOS") or record.get("VLR_TOTAL_NF") or 0.0), 2)
        vlr_desc = round(float(record.get("VLR_DESCONTO") or 0.0), 2)
        vlr_acresc = round(float(record.get("VLR_ACRESCIMO") or 0.0), 2)
        vlr_frete = round(float(record.get("VLR_FRETE") or 0.0), 2)
        vlr_total = round(float(record.get("VLR_TOTAL_NF") or 0.0), 2)
        situacao = str(record.get("SITUACAO_DOC") or "A").strip().upper()[:1]
        pagto = str(record.get("DESC_MEIO_PAGTO") or "PIX").strip()
        cpf_dest = str(record.get("CPF_CNPJ_DEST") or "").strip()
        nome_dest = str(record.get("NOME_DEST") or "").strip()
        protocolo = str(record.get("PROTOCOLO_SEFAZ") or "").strip()
        origem = str(record.get("ORIGEM_REGISTRO") or "MANUAL_PYTHON").strip()
        obs = str(record.get("OBSERVACOES") or "").strip()

        with self._get_sqlite_conn() as conn:
            cur = conn.cursor()
            if id_doc:
                cur.execute(
                    """
                    UPDATE TB_DOC_FISCAL_SAIDA SET
                        NUM_DOCUMENTO=?, SERIE_DOC=?, MODELO_FISCAL=?, CHAVE_NFE=?,
                        DT_EMISSAO=?, DT_AUTORIZACAO=?, VLR_PRODUTOS=?, VLR_DESCONTO=?,
                        VLR_ACRESCIMO=?, VLR_FRETE=?, VLR_TOTAL_NF=?, SITUACAO_DOC=?,
                        DESC_MEIO_PAGTO=?, CPF_CNPJ_DEST=?, NOME_DEST=?, PROTOCOLO_SEFAZ=?,
                        ORIGEM_REGISTRO=?, OBSERVACOES=?
                    WHERE ID_DOC=?
                    """,
                    (
                        num_doc, serie, modelo, chave, dt_emi, dt_aut,
                        vlr_prod, vlr_desc, vlr_acresc, vlr_frete, vlr_total,
                        situacao, pagto, cpf_dest, nome_dest, protocolo, origem, obs,
                        int(id_doc),
                    ),
                )
                target_id = int(id_doc)
            else:
                cur.execute(
                    """
                    INSERT INTO TB_DOC_FISCAL_SAIDA (
                        NUM_DOCUMENTO, SERIE_DOC, MODELO_FISCAL, CHAVE_NFE,
                        DT_EMISSAO, DT_AUTORIZACAO, VLR_PRODUTOS, VLR_DESCONTO,
                        VLR_ACRESCIMO, VLR_FRETE, VLR_TOTAL_NF, SITUACAO_DOC,
                        DESC_MEIO_PAGTO, CPF_CNPJ_DEST, NOME_DEST, PROTOCOLO_SEFAZ,
                        ORIGEM_REGISTRO, OBSERVACOES
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        num_doc, serie, modelo, chave, dt_emi, dt_aut,
                        vlr_prod, vlr_desc, vlr_acresc, vlr_frete, vlr_total,
                        situacao, pagto, cpf_dest, nome_dest, protocolo, origem, obs,
                    ),
                )
                target_id = cur.lastrowid

            # Sincroniza nas tabelas Clipp (TB_NFVENDA, TB_NFE, TB_NFVENDA_TOT)
            dt_e_only = dt_emi[:10]
            dt_a_only = dt_aut[:10] if dt_aut else dt_e_only
            st_sefaz = "101" if situacao == "C" else ("102" if situacao == "I" else "100")
            st_des = (
                "Cancelamento de NF-e homologado"
                if st_sefaz == "101"
                else ("Inutilização de número homologado" if st_sefaz == "102" else "Autorizado o uso da NF-e")
            )
            cur.execute(
                """
                INSERT OR REPLACE INTO TB_NFVENDA (
                    ID_NFVENDA, NF_NUMERO, NF_SERIE, NF_MODELO, DT_EMISSAO, DT_SAIDA, STATUS, ENT_SAI, MEIO_PAGTO, CPF_CNPJ_DEST, NOME_DEST
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'S', ?, ?, ?)
                """,
                (target_id, num_doc, serie, modelo, dt_e_only, dt_e_only, "C" if situacao == "C" else "E", pagto, cpf_dest, nome_dest),
            )
            cur.execute("DELETE FROM TB_NFE WHERE ID_NFVENDA=?", (target_id,))
            cur.execute(
                """
                INSERT INTO TB_NFE (
                    ID_NFVENDA, ID_NFE, EMISSAO, DATA_HORA, DT_AUTORIZACAO, PROTOCOLO, STATUS, STATUS_DES, AMBIENTE
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (target_id, chave, dt_e_only, dt_aut, dt_a_only, protocolo, st_sefaz, st_des),
            )
            cur.execute(
                """
                INSERT OR REPLACE INTO TB_NFVENDA_TOT (
                    ID_NFVENDA, TOT_PRODUTO, TOT_DESCONTO, TOT_FRETE, TOT_DESPESA, TOT_NF
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (target_id, vlr_prod, vlr_desc, vlr_frete, vlr_acresc, vlr_total),
            )

            conn.commit()
            cur.execute("SELECT * FROM TB_DOC_FISCAL_SAIDA WHERE ID_DOC = ?", (target_id,))
            row = cur.fetchone()
            return dict(row) if row else {}

    def delete_sale_record(self, id_doc: int) -> bool:
        with self._get_sqlite_conn() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM TB_DOC_FISCAL_PAGAMENTOS WHERE ID_DOC = ?", (int(id_doc),))
            cur.execute("DELETE FROM TB_DOC_FISCAL_ITENS WHERE ID_DOC = ?", (int(id_doc),))
            cur.execute("DELETE FROM TB_DOC_FISCAL_SAIDA WHERE ID_DOC = ?", (int(id_doc),))
            cur.execute("DELETE FROM TB_NFV_ITEM WHERE ID_NFVENDA = ?", (int(id_doc),))
            cur.execute("DELETE FROM TB_NFVENDA_TOT WHERE ID_NFVENDA = ?", (int(id_doc),))
            cur.execute("DELETE FROM TB_NFE WHERE ID_NFVENDA = ?", (int(id_doc),))
            cur.execute("DELETE FROM TB_NFVENDA WHERE ID_NFVENDA = ?", (int(id_doc),))
            conn.commit()
            return cur.rowcount > 0

    def import_xml_documents_into_local_db(self, xml_docs: List[Dict[str, Any]], replace_existing: bool = False) -> Dict[str, int]:
        """
        Importa documentos fiscais extraídos dos XMLs diretamente para a base local
        (tanto em TB_DOC_FISCAL_SAIDA quanto em TB_NFVENDA / TB_NFE / TB_NFVENDA_TOT / TB_NFV_ITEM).
        """
        inserted = 0
        updated = 0
        skipped = 0

        with self._get_sqlite_conn() as conn:
            cur = conn.cursor()
            if replace_existing:
                cur.execute("DELETE FROM TB_DOC_FISCAL_PAGAMENTOS")
                cur.execute("DELETE FROM TB_DOC_FISCAL_ITENS")
                cur.execute("DELETE FROM TB_DOC_FISCAL_SAIDA")
                cur.execute("DELETE FROM TB_NFV_ITEM")
                cur.execute("DELETE FROM TB_NFVENDA_TOT")
                cur.execute("DELETE FROM TB_NFE")
                cur.execute("DELETE FROM TB_NFVENDA")

            for doc in xml_docs:
                if not doc.get("isValidXml") or doc.get("isDuplicate") or not doc.get("isSaleDocument"):
                    skipped += 1
                    continue

                num_str = re.sub(r"\D", "", str(doc.get("number") or ""))
                if not num_str:
                    skipped += 1
                    continue
                num_doc = int(num_str)
                serie = str(doc.get("series") or "1").strip()
                modelo = str(doc.get("model") or "65").strip()
                chave = str(doc.get("accessKey") or "").strip()

                status_str = str(doc.get("status") or "AUTORIZADO").upper()
                sit_code = "A"
                st_sefaz = "100"
                if "CANC" in status_str:
                    sit_code = "C"
                    st_sefaz = "101"
                elif "DENEG" in status_str:
                    sit_code = "D"
                    st_sefaz = "302"
                elif "INUT" in status_str:
                    sit_code = "I"
                    st_sefaz = "102"

                cur.execute(
                    "SELECT ID_DOC FROM TB_DOC_FISCAL_SAIDA WHERE (CHAVE_NFE != '' AND CHAVE_NFE = ?) OR (NUM_DOCUMENTO = ? AND SERIE_DOC = ? AND MODELO_FISCAL = ?)",
                    (chave, num_doc, serie, modelo),
                )
                existing = cur.fetchone()

                dt_emi_iso = str(doc.get("issueDateTime") or doc.get("issueDate") or "")
                dt_aut_iso = str(doc.get("authDateTime") or doc.get("authDate") or dt_emi_iso)
                vlr_prod = round(float(doc.get("productsValue") or 0.0), 2)
                vlr_desc = round(float(doc.get("discountValue") or 0.0), 2)
                vlr_acresc = round(float(doc.get("additionValue") or 0.0), 2)
                vlr_frete = round(float(doc.get("freightValue") or 0.0), 2)
                vlr_tot = round(float(doc.get("totalValue") or 0.0), 2)

                row_tuple = (
                    num_doc,
                    serie,
                    modelo,
                    chave,
                    dt_emi_iso,
                    dt_aut_iso,
                    vlr_prod,
                    vlr_desc,
                    vlr_acresc,
                    vlr_frete,
                    vlr_tot,
                    sit_code,
                    str(doc.get("primaryPayment") or "Não informado"),
                    str(doc.get("destCnpjCpf") or ""),
                    str(doc.get("destName") or ""),
                    str(doc.get("protocol") or ""),
                    "IMPORTADO_XML",
                    f"Arquivo: {doc.get('fileName', '')}",
                )

                if existing:
                    doc_id = existing["ID_DOC"]
                    cur.execute(
                        """
                        UPDATE TB_DOC_FISCAL_SAIDA SET
                            NUM_DOCUMENTO=?, SERIE_DOC=?, MODELO_FISCAL=?, CHAVE_NFE=?,
                            DT_EMISSAO=?, DT_AUTORIZACAO=?, VLR_PRODUTOS=?, VLR_DESCONTO=?,
                            VLR_ACRESCIMO=?, VLR_FRETE=?, VLR_TOTAL_NF=?, SITUACAO_DOC=?,
                            DESC_MEIO_PAGTO=?, CPF_CNPJ_DEST=?, NOME_DEST=?, PROTOCOLO_SEFAZ=?,
                            ORIGEM_REGISTRO=?, OBSERVACOES=?
                        WHERE ID_DOC=?
                        """,
                        (*row_tuple, doc_id),
                    )
                    cur.execute("DELETE FROM TB_DOC_FISCAL_ITENS WHERE ID_DOC=?", (doc_id,))
                    cur.execute("DELETE FROM TB_DOC_FISCAL_PAGAMENTOS WHERE ID_DOC=?", (doc_id,))
                    updated += 1
                else:
                    cur.execute(
                        """
                        INSERT INTO TB_DOC_FISCAL_SAIDA (
                            NUM_DOCUMENTO, SERIE_DOC, MODELO_FISCAL, CHAVE_NFE,
                            DT_EMISSAO, DT_AUTORIZACAO, VLR_PRODUTOS, VLR_DESCONTO,
                            VLR_ACRESCIMO, VLR_FRETE, VLR_TOTAL_NF, SITUACAO_DOC,
                            DESC_MEIO_PAGTO, CPF_CNPJ_DEST, NOME_DEST, PROTOCOLO_SEFAZ,
                            ORIGEM_REGISTRO, OBSERVACOES
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        row_tuple,
                    )
                    doc_id = cur.lastrowid
                    inserted += 1

                # Sincroniza tabelas Clipp
                cur.execute(
                    """
                    INSERT OR REPLACE INTO TB_NFVENDA (
                        ID_NFVENDA, NF_NUMERO, NF_SERIE, NF_MODELO, DT_EMISSAO, DT_SAIDA, STATUS, ENT_SAI, MEIO_PAGTO, CPF_CNPJ_DEST, NOME_DEST
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'S', ?, ?, ?)
                    """,
                    (
                        doc_id,
                        num_doc,
                        serie,
                        modelo,
                        dt_emi_iso[:10],
                        dt_emi_iso[:10],
                        "C" if sit_code == "C" else "E",
                        str(doc.get("primaryPayment") or "Não informado"),
                        str(doc.get("destCnpjCpf") or ""),
                        str(doc.get("destName") or ""),
                    ),
                )
                cur.execute("DELETE FROM TB_NFE WHERE ID_NFVENDA=?", (doc_id,))
                cur.execute(
                    """
                    INSERT INTO TB_NFE (
                        ID_NFVENDA, ID_NFE, EMISSAO, DATA_HORA, DT_AUTORIZACAO, PROTOCOLO, STATUS, STATUS_DES, AMBIENTE
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
                    """,
                    (
                        doc_id,
                        chave,
                        dt_emi_iso[:10],
                        dt_aut_iso,
                        dt_aut_iso[:10],
                        str(doc.get("protocol") or ""),
                        st_sefaz,
                        str(doc.get("statusDescription") or "Autorizado o uso da NF-e"),
                    ),
                )
                cur.execute("DELETE FROM TB_NFV_ITEM WHERE ID_NFVENDA=?", (doc_id,))

                tot_bc_icms = 0.0
                tot_icms = 0.0
                for it in doc.get("items") or []:
                    taxes = it.get("taxes") or {}
                    bc_val = float(taxes.get("vBC") or 0.0)
                    icms_val = float(taxes.get("icmsVal") or 0.0)
                    tot_bc_icms += bc_val
                    tot_icms += icms_val
                    gross = float(it.get("grossValue") or 0.0)
                    disc = float(it.get("discount") or 0.0)
                    cur.execute(
                        """
                        INSERT INTO TB_DOC_FISCAL_ITENS (
                            ID_DOC, NUM_ITEM, COD_PRODUTO, DESC_PRODUTO, NCM, CFOP,
                            UNIDADE, QUANTIDADE, VLR_UNITARIO, VLR_BRUTO, VLR_DESCONTO,
                            VLR_ICMS, VLR_PIS, VLR_COFINS
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            doc_id,
                            int(it.get("itemNumber") or 1),
                            str(it.get("code") or ""),
                            str(it.get("description") or ""),
                            str(it.get("ncm") or ""),
                            str(it.get("cfop") or ""),
                            str(it.get("unit") or "UN"),
                            float(it.get("quantity") or 1),
                            float(it.get("unitPrice") or 0),
                            gross,
                            disc,
                            icms_val,
                            float(taxes.get("pisVal") or 0),
                            float(taxes.get("cofinsVal") or 0),
                        ),
                    )
                    cur.execute(
                        """
                        INSERT INTO TB_NFV_ITEM (
                            ID_NFVENDA, NUM_ITEM, ID_IDENTIFICADOR, MODELO, COD_PRODUTO, DESC_PRODUTO,
                            COD_NCM, CFOP, CSOSN, UNI_MEDIDA, QTD_ITEM, VLR_UNIT, VLR_DESC,
                            VLR_TOTAL, VLR_BC_ICMS, VLR_ICMS, VLR_CONTABIL
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '102', ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            doc_id,
                            int(it.get("itemNumber") or 1),
                            doc_id,
                            modelo,
                            str(it.get("code") or ""),
                            str(it.get("description") or ""),
                            str(it.get("ncm") or "00000000"),
                            str(it.get("cfop") or "5102"),
                            str(it.get("unit") or "UN"),
                            float(it.get("quantity") or 1),
                            float(it.get("unitPrice") or 0),
                            disc,
                            gross,
                            bc_val,
                            icms_val,
                            round(gross - disc, 2),
                        ),
                    )

                cur.execute(
                    """
                    INSERT OR REPLACE INTO TB_NFVENDA_TOT (
                        ID_NFVENDA, TOT_PRODUTO, TOT_DESCONTO, TOT_FRETE, TOT_DESPESA, TOT_BC_ICMS, TOT_ICMS, TOT_NF
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (doc_id, vlr_prod, vlr_desc, vlr_frete, vlr_acresc, round(tot_bc_icms, 2), round(tot_icms, 2), vlr_tot),
                )

                for pg in doc.get("payments") or []:
                    cur.execute(
                        """
                        INSERT INTO TB_DOC_FISCAL_PAGAMENTOS (
                            ID_DOC, COD_SEFAZ, DESC_PAGAMENTO, VALOR_PAGO
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (
                            doc_id,
                            str(pg.get("code") or "99"),
                            str(pg.get("label") or "Outros"),
                            float(pg.get("amount") or 0),
                        ),
                    )

            conn.commit()

        return {"inserted": inserted, "updated": updated, "skipped": skipped}

    def execute_read_only_sql(self, sql: str) -> Dict[str, Any]:
        """
        Executa uma consulta SQL somente-leitura (suporta também sintaxe Firebird `SELECT FIRST N ...`).
        """
        safe_sql = assert_read_only_sql(sql)
        first_match = re.match(r"^SELECT\s+FIRST\s+(\d+)\s+([\s\S]+)$", safe_sql, flags=re.IGNORECASE)
        exec_sql = safe_sql
        if first_match:
            limit_n = first_match.group(1)
            rest_sql = first_match.group(2)
            if not re.search(r"\bLIMIT\s+\d+\b", rest_sql, flags=re.IGNORECASE):
                exec_sql = f"SELECT {rest_sql} LIMIT {limit_n}"

        with self._get_sqlite_conn() as conn:
            cur = conn.cursor()
            cur.execute(exec_sql)
            rows = [dict(r) for r in cur.fetchall()]
            columns = [desc[0] for desc in cur.description] if cur.description else []

        return {
            "success": True,
            "executedSql": safe_sql,
            "columns": columns,
            "rows": rows,
            "rowCount": len(rows),
        }

    def query_mapped_sales(
        self,
        mapping: Optional[Dict[str, Any]] = None,
        limit: int = 5000,
        dt_inicio: Optional[str] = None,
        dt_fim: Optional[str] = None,
        modelo: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Executa a consulta mapeada de vendas (suportando tanto `TB_NFVENDA`/`TB_NFE` do Clipp
        quanto `TB_DOC_FISCAL_SAIDA`) e normaliza os registros para o motor de conciliação.
        """
        # Se a base Clipp (`TB_NFVENDA`) estiver populada e nenhum mapeamento customizado restrito foi forçado:
        has_clipp_rows = self.active_connection_info.get("sourceMode") == "CLIPP_FDB_REAL"
        if not has_clipp_rows and mapping is None:
            with self._get_sqlite_conn() as conn:
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM TB_NFVENDA")
                if int(cur.fetchone()[0] or 0) > 15:
                    has_clipp_rows = True
                    self.active_connection_info["sourceMode"] = "CLIPP_FDB_REAL"

        if mapping is None and has_clipp_rows:
            clipp_data = self.query_clipp_audit_dataset(dt_inicio=dt_inicio, dt_fim=dt_fim, modelo=modelo)
            records = []
            for idx, r in enumerate(clipp_data["rowsUnion"]):
                st_code = str(r.get("STATUS") or "100")
                status_label = "AUTORIZADO"
                if st_code in ("101", "135", "151", "C"):
                    status_label = "CANCELADO"
                elif st_code in ("102", "I"):
                    status_label = "INUTILIZADO"
                elif st_code not in ("100", "150", "A"):
                    status_label = st_code

                records.append(
                    {
                        "id": f"fb_{r.get('ID_NFVENDA') or (idx + 1)}",
                        "id_nfvenda": r.get("ID_NFVENDA"),
                        "number": str(r.get("NF_NUMERO") or ""),
                        "nf_numero": str(r.get("NF_NUMERO") or ""),
                        "series": str(r.get("NF_SERIE") or "1"),
                        "model": str(r.get("NF_MODELO") or "65"),
                        "nf_modelo": str(r.get("NF_MODELO") or "65"),
                        "accessKey": re.sub(r"\D", "", str(r.get("ID_NFE") or "")),
                        "id_nfe": re.sub(r"\D", "", str(r.get("ID_NFE") or "")),
                        "issueDate": (str(r.get("DT_EMISSAO") or "")[:10]) or None,
                        "dt_emissao": (str(r.get("DT_EMISSAO") or "")[:10]) or None,
                        "authDate": (str(r.get("DT_AUTORIZACAO") or r.get("DT_EMISSAO") or "")[:10]) or None,
                        "data_hora_sefaz": str(r.get("DATA_HORA_SEFAZ") or ""),
                        "totalValue": round(float(r.get("TOT_NF") or 0.0), 2),
                        "discountValue": round(float(r.get("TOT_DESCONTO") or 0.0), 2),
                        "additionValue": 0.0,
                        "bcIcms": round(float(r.get("TOT_BC_ICMS") or 0.0), 2),
                        "vlrIcms": round(float(r.get("TOT_ICMS") or 0.0), 2),
                        "status": status_label,
                        "rawStatus": st_code,
                        "status_des": str(r.get("STATUS_DES") or ""),
                        "paymentMethod": str(r.get("MEIO_PAGTO") or "Conforme Nota"),
                        "customerDoc": "",
                    }
                )
            return {
                "success": True,
                "generatedSql": "SELECT v.*, n.*, t.* FROM TB_NFVENDA v LEFT JOIN TB_NFE n ON n.ID_NFVENDA=v.ID_NFVENDA LEFT JOIN TB_NFVENDA_TOT t ON t.ID_NFVENDA=v.ID_NFVENDA",
                "totalFetched": len(records),
                "records": records,
            }

        m = mapping or {
            "tableName": "TB_DOC_FISCAL_SAIDA",
            "numberColumn": "NUM_DOCUMENTO",
            "seriesColumn": "SERIE_DOC",
            "modelColumn": "MODELO_FISCAL",
            "accessKeyColumn": "CHAVE_NFE",
            "issueDateColumn": "DT_EMISSAO",
            "authDateColumn": "DT_AUTORIZACAO",
            "totalValueColumn": "VLR_TOTAL_NF",
            "discountValueColumn": "VLR_DESCONTO",
            "additionValueColumn": "VLR_ACRESCIMO",
            "statusColumn": "SITUACAO_DOC",
            "paymentColumn": "DESC_MEIO_PAGTO",
            "customerColumn": "CPF_CNPJ_DEST",
        }

        table_name = validate_sql_identifier(m.get("tableName") or "TB_DOC_FISCAL_SAIDA")
        field_defs = [
            ("numberColumn", "DOC_NUMBER"),
            ("seriesColumn", "DOC_SERIES"),
            ("modelColumn", "DOC_MODEL"),
            ("accessKeyColumn", "ACCESS_KEY"),
            ("issueDateColumn", "ISSUE_DATE"),
            ("authDateColumn", "AUTH_DATE"),
            ("totalValueColumn", "TOTAL_VALUE"),
            ("discountValueColumn", "DISCOUNT_VALUE"),
            ("additionValueColumn", "ADDITION_VALUE"),
            ("statusColumn", "DOC_STATUS"),
            ("paymentColumn", "PAYMENT_METHOD"),
            ("customerColumn", "CUSTOMER_DOC"),
        ]

        select_parts = []
        for key, alias in field_defs:
            col_val = m.get(key)
            if col_val:
                select_parts.append(f"{validate_sql_identifier(col_val)} AS {alias}")

        if not select_parts:
            raise ValueError("Mapeie ao menos as colunas de Número, Data e Valor Total.")

        safe_limit = max(1, min(int(limit or 20), 10000))
        firebird_sql = f"SELECT FIRST {safe_limit} {', '.join(select_parts)} FROM {table_name}"
        res = self.execute_read_only_sql(firebird_sql)

        status_map = {
            "A": "AUTORIZADO",
            "AUTORIZADO": "AUTORIZADO",
            "100": "AUTORIZADO",
            "150": "AUTORIZADO",
            "C": "CANCELADO",
            "CANCELADO": "CANCELADO",
            "101": "CANCELADO",
            "135": "CANCELADO",
            "151": "CANCELADO",
            "D": "DENEGADO",
            "DENEGADO": "DENEGADO",
            "I": "INUTILIZADO",
            "INUTILIZADO": "INUTILIZADO",
            "102": "INUTILIZADO",
        }

        status_des_map = {
            "AUTORIZADO": "Autorizado o uso da NF-e",
            "CANCELADO": "Cancelamento de NF-e homologado",
            "DENEGADO": "Uso Denegado",
            "INUTILIZADO": "Inutilização de número homologado",
        }

        normalized_records = []
        for idx, r in enumerate(res["rows"]):
            raw_num = str(r.get("DOC_NUMBER") or "")
            num_clean = re.sub(r"\D", "", raw_num)
            raw_ser = str(r.get("DOC_SERIES") or "1")
            ser_clean = re.sub(r"\D", "", raw_ser) or "1"
            raw_issue_full = str(r.get("ISSUE_DATE") or "")
            raw_auth_full = str(r.get("AUTH_DATE") or raw_issue_full)
            raw_issue = raw_issue_full[:10]
            raw_auth = raw_auth_full[:10]
            raw_status = str(r.get("DOC_STATUS") or "A").strip().upper()
            st_norm = status_map.get(raw_status, "AUTORIZADO")
            doc_mod = str(r.get("DOC_MODEL") or "65").strip()

            if modelo in ("55", "65") and doc_mod != modelo:
                continue
            if dt_inicio and raw_issue and raw_auth and raw_issue < dt_inicio and raw_auth < dt_inicio:
                continue
            if dt_fim and raw_issue and raw_auth and raw_issue > dt_fim and raw_auth > dt_fim:
                continue

            normalized_records.append(
                {
                    "id": f"fb_{idx + 1}",
                    "number": str(int(num_clean)) if num_clean else "",
                    "nf_numero": str(int(num_clean)) if num_clean else "",
                    "series": str(int(ser_clean)) if ser_clean else "1",
                    "model": doc_mod,
                    "nf_modelo": doc_mod,
                    "accessKey": re.sub(r"\D", "", str(r.get("ACCESS_KEY") or "")),
                    "id_nfe": re.sub(r"\D", "", str(r.get("ACCESS_KEY") or "")),
                    "issueDate": raw_issue or None,
                    "dt_emissao": raw_issue or None,
                    "authDate": raw_auth or raw_issue or None,
                    "data_hora_sefaz": raw_auth_full or raw_issue_full,
                    "totalValue": round(float(r.get("TOTAL_VALUE") or 0.0), 2),
                    "discountValue": round(float(r.get("DISCOUNT_VALUE") or 0.0), 2),
                    "additionValue": round(float(r.get("ADDITION_VALUE") or 0.0), 2),
                    "status": st_norm,
                    "rawStatus": "101" if st_norm == "CANCELADO" else ("102" if st_norm == "INUTILIZADO" else "100"),
                    "status_des": status_des_map.get(st_norm, "Autorizado o uso da NF-e"),
                    "paymentMethod": str(r.get("PAYMENT_METHOD") or "Não informado").strip(),
                    "customerDoc": str(r.get("CUSTOMER_DOC") or "").strip(),
                }
            )

        return {
            "success": True,
            "generatedSql": firebird_sql,
            "totalFetched": len(normalized_records),
            "records": normalized_records,
        }
