import io
import re
import zlib
import zipfile
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

CATEGORY_DEFINITIONS: Dict[str, Dict[str, str]] = {
    "CONCILIADO": {
        "code": "CONCILIADO",
        "label": "Conciliados",
        "badgeClass": "badge-success",
        "severity": "ok",
    },
    "SOMENTE_XML": {
        "code": "SOMENTE_XML",
        "label": "Encontrados somente nos XMLs",
        "badgeClass": "badge-warning",
        "severity": "warning",
    },
    "SOMENTE_PDF": {
        "code": "SOMENTE_PDF",
        "label": "Encontrados somente no PDF",
        "badgeClass": "badge-warning",
        "severity": "warning",
    },
    "SOMENTE_BANCO": {
        "code": "SOMENTE_BANCO",
        "label": "Consta no Banco mas sem XML correspondente",
        "badgeClass": "badge-warning",
        "severity": "warning",
    },
    "DIVERGENCIA_DATA": {
        "code": "DIVERGENCIA_DATA",
        "label": "Divergências de data (D+1 / Emissão vs Autorização)",
        "badgeClass": "badge-amber",
        "severity": "divergence",
    },
    "DIVERGENCIA_VALOR": {
        "code": "DIVERGENCIA_VALOR",
        "label": "Divergências de valor",
        "badgeClass": "badge-danger",
        "severity": "divergence",
    },
    "DIVERGENCIA_IDENTIFICACAO": {
        "code": "DIVERGENCIA_IDENTIFICACAO",
        "label": "Divergências de número, série ou chave",
        "badgeClass": "badge-purple",
        "severity": "divergence",
    },
    "CANCELADO_DENEGADO_INVALIDO": {
        "code": "CANCELADO_DENEGADO_INVALIDO",
        "label": "Cancelados (101), Inutilizados (102), Denegados ou Inválidos",
        "badgeClass": "badge-neutral-alert",
        "severity": "critical",
    },
    "POSSIVEL_DUPLICIDADE": {
        "code": "POSSIVEL_DUPLICIDADE",
        "label": "Possíveis duplicidades",
        "badgeClass": "badge-duplicate",
        "severity": "warning",
    },
    "REVISAO_MANUAL": {
        "code": "REVISAO_MANUAL",
        "label": "Registros que precisam de revisão manual",
        "badgeClass": "badge-review",
        "severity": "review",
    },
}


def _norm_num(val: Any) -> str:
    digits = re.sub(r"\D", "", str(val or ""))
    return str(int(digits)) if digits else ""


def _norm_series(val: Any) -> str:
    digits = re.sub(r"\D", "", str(val or ""))
    return str(int(digits)) if digits else "1"


def _norm_model(val: Any, access_key: str = "") -> str:
    m = re.sub(r"\D", "", str(val or ""))
    if m in ("55", "65"):
        return m
    key_clean = re.sub(r"\D", "", str(access_key or ""))
    if len(key_clean) == 44 and key_clean[20:22] in ("55", "65"):
        return key_clean[20:22]
    return m or "55"


def _norm_iso_date(val: Optional[str]) -> Optional[str]:
    if not val:
        return None
    s = str(val).strip()
    if not s:
        return None
    m_br = re.match(r"^(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})", s)
    if m_br:
        return f"{int(m_br.group(3)):04d}-{int(m_br.group(2)):02d}-{int(m_br.group(1)):02d}"
    m_iso = re.match(r"^(\d{4})[/\-](\d{1,2})[/\-](\d{1,2})", s)
    if m_iso:
        return f"{int(m_iso.group(1)):04d}-{int(m_iso.group(2)):02d}-{int(m_iso.group(3)):02d}"
    return s[:10]


def _fmt_brl_txt(v) -> str:
    try:
        n = float(v or 0)
    except (TypeError, ValueError):
        n = 0.0
    return "R$ " + f"{n:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _fmt_br_date(iso_str: Optional[str]) -> str:
    if not iso_str:
        return "—"
    norm = _norm_iso_date(iso_str)
    if norm and len(norm) == 10 and norm[4] == "-" and norm[7] == "-":
        return f"{norm[8:10]}/{norm[5:7]}/{norm[0:4]}"
    return str(iso_str)


def _fmt_br_datetime(dt_str: Optional[str]) -> str:
    if not dt_str:
        return "—"
    s = str(dt_str).strip()
    m = re.match(r"^(\d{4})[/\-](\d{1,2})[/\-](\d{1,2})[T\s]+(\d{1,2}:\d{2}(?::\d{2})?)", s)
    if m:
        return f"{int(m.group(3)):02d}/{int(m.group(2)):02d}/{int(m.group(1)):04d} {m.group(4)}"
    return _fmt_br_date(s)


def _in_period(dt_val: Optional[str], dt_inicio: Optional[str], dt_fim: Optional[str]) -> bool:
    d_ini = _norm_iso_date(dt_inicio)
    d_fim = _norm_iso_date(dt_fim)
    if not d_ini and not d_fim:
        return True
    d = _norm_iso_date(dt_val)
    if not d:
        return False
    if d_ini and d < d_ini:
        return False
    if d_fim and d > d_fim:
        return False
    return True


def build_audit_dashboard_report(
    xml_documents: List[Dict[str, Any]],
    pdf_records: List[Dict[str, Any]],
    db_audit_data: Optional[Dict[str, Any]] = None,
    firebird_records: Optional[List[Dict[str, Any]]] = None,
    options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Constrói o Painel Completo de Conciliação e Auditoria Fiscal Contábil:
    1. Visão Geral & Conciliação Automática (KPIs Topo + Desafio Temporal D+1 + Comparativo Triplo Banco vs XML vs PDF)
    2. Agrupamentos Fiscais por Modelo (55 e 65): Tabelas Dinâmicas por CFOP e por NCM (Base de Cálculo, ICMS e Valor Contábil)
    3. Eventos Separados: Canceladas (Status 101) e Inutilizadas (Status 102) isoladas do faturamento líquido (Status 100)
    4. Matriz de Divergências & Auditoria de Chaves com colunas obrigatórias:
       nf_numero, id_nfe, dt_emissao, data_hora_sefaz, status_des, valor, origem_falha
    """
    opts = options or {}
    date_field = "authDate" if opts.get("dateField") == "authDate" else "issueDate"
    date_field_label = "Por Data de Autorização SEFAZ" if date_field == "authDate" else "Por Data de Emissão"
    dt_inicio = _norm_iso_date((opts.get("startDate") or opts.get("dt_inicio") or "").strip())
    dt_fim = _norm_iso_date((opts.get("endDate") or opts.get("dt_fim") or "").strip())

    db_data = db_audit_data or {}
    db_records_raw = db_data.get("records")
    if db_records_raw is None:
        db_records_raw = firebird_records or []

    db_items_raw = db_data.get("items") or []

    # Normaliza registros do Banco (suporta tanto formato Clipp tb_nfvenda/tb_nfe quanto formato genérico)
    norm_db_docs: List[Dict[str, Any]] = []
    for r in db_records_raw:
        key = re.sub(r"\D", "", str(r.get("id_nfe") or r.get("accessKey") or r.get("CHAVE_NFE") or ""))
        num = _norm_num(r.get("nf_numero") or r.get("number") or r.get("NUM_DOC"))
        ser = _norm_series(r.get("nf_serie") or r.get("series") or r.get("SERIE"))
        mod = _norm_model(r.get("nf_modelo") or r.get("model") or r.get("MODELO"), key)
        dt_emi = _norm_iso_date(str(r.get("dt_emissao") or r.get("issueDate") or r.get("DT_EMISSAO") or ""))
        dt_aut = _norm_iso_date(str(r.get("dt_autorizacao") or r.get("authDate") or r.get("DT_AUTORIZACAO") or ""))
        dh_sefaz = str(r.get("data_hora_sefaz") or r.get("data_hora") or dt_aut or dt_emi or "")
        st_code = str(r.get("status_codigo") or r.get("status") or "100").strip()
        st_des = str(r.get("status_des") or r.get("statusDescription") or "").strip()

        if st_code in ("101",) or "CANCEL" in st_code.upper() or "CANCEL" in st_des.upper():
            st_norm = "CANCELADO"
            st_code_num = "101"
            if not st_des:
                st_des = "Cancelamento de NF-e homologado"
        elif st_code in ("102",) or "INUT" in st_code.upper() or "INUT" in st_des.upper():
            st_norm = "INUTILIZADO"
            st_code_num = "102"
            if not st_des:
                st_des = "Inutilização de número homologado"
        elif st_code in ("110", "301", "302") or "DENEG" in st_code.upper() or "DENEG" in st_des.upper():
            st_norm = "DENEGADO"
            st_code_num = st_code if st_code.isdigit() else "110"
            if not st_des:
                st_des = "Uso Denegado"
        else:
            st_norm = "AUTORIZADO"
            st_code_num = "100"
            if not st_des:
                st_des = "Autorizado o uso da NF-e"

        val = round(float(r.get("valor_total") if r.get("valor_total") is not None else (r.get("totalValue") or r.get("VLR_TOTAL") or 0.0)), 2)
        bc_icms = round(float(r.get("base_icms") if r.get("base_icms") is not None else (r.get("VLR_BC_ICMS") or 0.0)), 2)
        vlr_icms = round(float(r.get("valor_icms") if r.get("valor_icms") is not None else (r.get("VLR_ICMS") or 0.0)), 2)

        in_emi = _in_period(dt_emi, dt_inicio, dt_fim)
        in_aut = _in_period(dt_aut or dt_emi, dt_inicio, dt_fim)
        in_active = in_aut if date_field == "authDate" else in_emi

        norm_db_docs.append(
            {
                "id": r.get("id") or f"db_{num}_{ser}_{mod}",
                "id_nfvenda": r.get("id_nfvenda"),
                "nf_numero": num,
                "nf_serie": ser,
                "nf_modelo": mod,
                "id_nfe": key,
                "dt_emissao": dt_emi,
                "dt_autorizacao": dt_aut or dt_emi,
                "data_hora_sefaz": dh_sefaz,
                "status_codigo": st_code_num,
                "status_des": st_des,
                "status": st_norm,
                "valor": val,
                "base_icms": bc_icms,
                "valor_icms": vlr_icms,
                "cfop": str(r.get("cfop") or r.get("CFOP") or ""),
                "is_d_plus_1": bool(dt_emi and dt_aut and dt_emi != dt_aut),
                "in_emissao_period": in_emi,
                "in_autorizacao_period": in_aut,
                "in_active_period": in_active,
            }
        )

    # Filtra XMLs pelo período ativo (exceto se não houver filtro de data)
    valid_xml_docs = [x for x in xml_documents if not x.get("isDuplicate") and x.get("isValidXml")]
    xml_in_period: List[Dict[str, Any]] = []
    for x in valid_xml_docs:
        x_dt = (x.get("authDate") or x.get("issueDate")) if date_field == "authDate" else (x.get("issueDate") or x.get("authDate"))
        if _in_period(x_dt, dt_inicio, dt_fim):
            xml_in_period.append(x)

    # Filtra PDF pelo período ativo
    pdf_in_period: List[Dict[str, Any]] = []
    for p in pdf_records:
        p_dt = (p.get("authDate") or p.get("issueDate")) if date_field == "authDate" else (p.get("issueDate") or p.get("authDate"))
        if _in_period(p_dt, dt_inicio, dt_fim):
            pdf_in_period.append(p)

    # =========================================================================
    # 1. KPIs DO TOPO & COMPARATIVO TRIPLO (GERAL, NF-e 55, NFC-e 65)
    # =========================================================================
    db_auth_emissao = [d for d in norm_db_docs if d["status"] == "AUTORIZADO" and d["in_emissao_period"]]
    db_auth_sefaz = [d for d in norm_db_docs if d["status"] == "AUTORIZADO" and d["in_autorizacao_period"]]
    db_auth_active = [d for d in norm_db_docs if d["status"] == "AUTORIZADO" and d["in_active_period"]]

    xml_auth = [
        x for x in xml_in_period
        if x.get("status") == "AUTORIZADO" and x.get("recordKind") == "DOCUMENTO_FISCAL" and x.get("isSaleDocument")
    ]
    pdf_auth = [p for p in pdf_in_period if str(p.get("status") or "AUTORIZADO").upper() == "AUTORIZADO"]

    total_banco_emissao = round(sum(d["valor"] for d in db_auth_emissao), 2)
    total_banco_autorizacao = round(sum(d["valor"] for d in db_auth_sefaz), 2)
    total_xml_importados = round(sum(float(x.get("totalValue") or 0.0) for x in xml_auth), 2)
    total_relatorio_pdf = round(sum(float(p.get("totalValue") or 0.0) for p in pdf_auth), 2)

    def _compute_batimento(
        v_b_emi: float,
        v_b_aut: float,
        v_xml: float,
        v_pdf: float,
        has_db: bool,
        has_xml: bool,
        has_pdf: bool,
    ) -> Dict[str, Any]:
        def _brl(v: float) -> str:
            return "R$ " + f"{abs(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

        # Define valor de referência externo (XML prioritário, ou PDF)
        ref_ext = v_xml if has_xml else (v_pdf if has_pdf else None)
        if has_db and ref_ext is not None:
            diff_emi = round(ref_ext - v_b_emi, 2)
            diff_aut = round(ref_ext - v_b_aut, 2)
            bateu_emi = abs(diff_emi) < 0.01
            bateu_aut = abs(diff_aut) < 0.01
            if bateu_emi and bateu_aut:
                return {
                    "code": "BATEU_AMBOS",
                    "label": "✓ Valores batem",
                    "badge": "success",
                    "difference": 0.0,
                    "detail": "Banco igual aos arquivos, por emissão e por autorização.",
                }
            if bateu_emi:
                return {
                    "code": "BATEU_EMISSAO",
                    "label": "✓ Bate pela emissão",
                    "badge": "success",
                    "difference": diff_aut,
                    "detail": f"Pela data de autorização a diferença é {_brl(diff_aut)}.",
                }
            if bateu_aut:
                return {
                    "code": "BATEU_AUTORIZACAO",
                    "label": "✓ Bate pela autorização",
                    "badge": "success",
                    "difference": diff_emi,
                    "detail": f"Pela data de emissão a diferença é {_brl(diff_emi)}.",
                }
            active_diff = diff_aut if date_field == "authDate" else diff_emi
            return {
                "code": "DIVERGENTE",
                "label": f"Diferença de {_brl(active_diff)}",
                "badge": "danger",
                "difference": active_diff,
                "detail": f"Por emissão: {_brl(diff_emi)} • Por autorização: {_brl(diff_aut)}",
            }
        elif has_xml and has_pdf:
            diff_xp = round(v_xml - v_pdf, 2)
            if abs(diff_xp) < 0.01:
                return {
                    "code": "BATEU_XML_PDF",
                    "label": "✓ XML igual ao relatório",
                    "badge": "success",
                    "difference": 0.0,
                    "detail": "O total dos XMLs é igual ao do relatório PDF.",
                }
            return {
                "code": "DIVERGENTE",
                "label": f"Diferença de {_brl(diff_xp)}",
                "badge": "danger",
                "difference": diff_xp,
                "detail": f"XML × relatório PDF: {_brl(diff_xp)}",
            }
        return {
            "code": "AGUARDANDO_FONTES",
            "label": "Aguardando arquivos",
            "badge": "neutral",
            "difference": 0.0,
            "detail": "Carregue o banco e os XMLs ou o relatório.",
        }

    has_db = len(norm_db_docs) > 0
    has_xml = len(xml_in_period) > 0
    has_pdf = len(pdf_in_period) > 0

    batimento_geral = _compute_batimento(
        total_banco_emissao,
        total_banco_autorizacao,
        total_xml_importados,
        total_relatorio_pdf,
        has_db,
        has_xml,
        has_pdf,
    )

    def _build_model_comparison(model_Filter: Optional[str], label: str) -> Dict[str, Any]:
        b_emi = [d for d in db_auth_emissao if (not model_Filter or d["nf_modelo"] == model_Filter)]
        b_aut = [d for d in db_auth_sefaz if (not model_Filter or d["nf_modelo"] == model_Filter)]
        x_list = [x for x in xml_auth if (not model_Filter or _norm_model(x.get("model"), x.get("accessKey", "")) == model_Filter)]
        p_list = [
            p for p in pdf_auth
            if (not model_Filter or _norm_model(p.get("model"), p.get("accessKey", "")) == model_Filter)
        ]
        # Se o PDF não informar modelo por linha e estivermos filtrando por modelo, mantém proporcional ou exibe no Geral
        v_b_emi = round(sum(d["valor"] for d in b_emi), 2)
        v_b_aut = round(sum(d["valor"] for d in b_aut), 2)
        v_xml = round(sum(float(x.get("totalValue") or 0.0) for x in x_list), 2)
        v_pdf = round(sum(float(p.get("totalValue") or 0.0) for p in p_list), 2)
        v_b_active = v_b_aut if date_field == "authDate" else v_b_emi

        bat = _compute_batimento(
            v_b_emi,
            v_b_aut,
            v_xml,
            v_pdf,
            len(b_emi) > 0 or len(b_aut) > 0,
            len(x_list) > 0,
            len(p_list) > 0,
        )
        return {
            "model": model_Filter or "ALL",
            "label": label,
            "qtdBancoEmissao": len(b_emi),
            "totalBancoEmissao": v_b_emi,
            "qtdBancoAutorizacao": len(b_aut),
            "totalBancoAutorizacao": v_b_aut,
            "divergenciaDPlus1": round(v_b_emi - v_b_aut, 2),
            "qtdXml": len(x_list),
            "totalXml": v_xml,
            "qtdPdf": len(p_list),
            "totalPdf": v_pdf,
            "diffBancoVsXml": round(v_b_active - v_xml, 2) if (has_db and has_xml) else None,
            "diffXmlVsPdf": round(v_xml - v_pdf, 2) if (has_xml and has_pdf) else None,
            "diffBancoVsPdf": round(v_b_active - v_pdf, 2) if (has_db and has_pdf) else None,
            "batimento": bat,
        }

    triple_comparison = {
        "ALL": _build_model_comparison(None, "Consolidado Geral (55 + 65)"),
        "55": _build_model_comparison("55", "NF-e Modelo 55"),
        "65": _build_model_comparison("65", "NFC-e Modelo 65"),
    }

    # Registros D+1 (Emissão != Autorização SEFAZ)
    d_plus_1_records = [
        d for d in norm_db_docs
        if d["is_d_plus_1"] and (d["in_emissao_period"] or d["in_autorizacao_period"])
    ]

    # =========================================================================
    # 2. AGRUPAMENTOS FISCAIS POR MODELO (CFOP e NCM)
    # =========================================================================
    # Map de id_nfvenda -> doc autorizado no período ativo para filtrar itens do banco
    active_db_by_id_nfvenda: Dict[Any, Dict[str, Any]] = {}
    active_db_by_num_mod: Dict[str, Dict[str, Any]] = {}
    for d in db_auth_active:
        if d.get("id_nfvenda") is not None:
            active_db_by_id_nfvenda[d["id_nfvenda"]] = d
        active_db_by_num_mod[f"{d['nf_numero']}|{d['nf_modelo']}"] = d

    def _build_cfop_and_ncm_pivots(target_model: Optional[str]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        cfop_map: Dict[str, Dict[str, Any]] = {}
        ncm_map: Dict[str, Dict[str, Any]] = {}

        def _get_cfop_bucket(cfop_code: str, mod_str: str) -> Dict[str, Any]:
            c_clean = re.sub(r"\D", "", str(cfop_code or "")) or "SEM_CFOP"
            key = f"{mod_str}|{c_clean}" if not target_model else c_clean
            if key not in cfop_map:
                cfop_map[key] = {
                    "cfop": c_clean,
                    "modelo": mod_str,
                    "docsBanco": set(),
                    "qtdItensBanco": 0,
                    "baseCalculoBanco": 0.0,
                    "valorIcmsBanco": 0.0,
                    "valorContabilBanco": 0.0,
                    "docsXml": set(),
                    "qtdItensXml": 0,
                    "baseCalculoXml": 0.0,
                    "valorIcmsXml": 0.0,
                    "valorContabilXml": 0.0,
                }
            return cfop_map[key]

        def _get_ncm_bucket(ncm_code: str, mod_str: str, desc_hint: str = "") -> Dict[str, Any]:
            n_clean = re.sub(r"\D", "", str(ncm_code or "")) or "SEM_NCM"
            key = f"{mod_str}|{n_clean}" if not target_model else n_clean
            if key not in ncm_map:
                ncm_map[key] = {
                    "ncm": n_clean,
                    "modelo": mod_str,
                    "descricao": desc_hint or "Mercadoria / Produto Fiscal",
                    "qtdItensBanco": 0,
                    "baseCalculoBanco": 0.0,
                    "valorIcmsBanco": 0.0,
                    "valorContabilBanco": 0.0,
                    "qtdItensXml": 0,
                    "baseCalculoXml": 0.0,
                    "valorIcmsXml": 0.0,
                    "valorContabilXml": 0.0,
                }
            elif desc_hint and ncm_map[key]["descricao"] in ("", "Mercadoria / Produto Fiscal"):
                ncm_map[key]["descricao"] = desc_hint
            return ncm_map[key]

        # 2.1 Popula a partir dos itens do Banco (tb_nfv_item)
        used_db_docs_in_items = set()
        for it in db_items_raw:
            id_nfv = it.get("ID_NFVENDA") or it.get("id_nfvenda")
            num_doc = _norm_num(it.get("NUM_DOC") or it.get("nf_numero"))
            mod_doc = _norm_model(it.get("MODELO") or it.get("nf_modelo"))
            parent_doc = active_db_by_id_nfvenda.get(id_nfv) or active_db_by_num_mod.get(f"{num_doc}|{mod_doc}")
            if not parent_doc:
                continue
            mod_eff = parent_doc["nf_modelo"]
            if target_model and mod_eff != target_model:
                continue

            used_db_docs_in_items.add(parent_doc["id"])
            cfop_val = str(it.get("CFOP") or it.get("cfop") or parent_doc.get("cfop") or "5102")
            ncm_val = str(it.get("COD_NCM") or it.get("ncm") or "00000000")
            desc_val = str(it.get("DESCRICAO") or it.get("description") or "")
            bc_val = float(it.get("VLR_BC_ICMS") if it.get("VLR_BC_ICMS") is not None else (it.get("icmsBase") or 0.0))
            icms_val = float(it.get("VLR_ICMS") if it.get("VLR_ICMS") is not None else (it.get("icmsValue") or 0.0))
            cont_val = float(
                it.get("VLR_CONTABIL")
                if it.get("VLR_CONTABIL") is not None
                else (it.get("VLR_TOTAL") or it.get("totalValue") or 0.0)
            )

            cb = _get_cfop_bucket(cfop_val, mod_eff)
            cb["docsBanco"].add(parent_doc["nf_numero"])
            cb["qtdItensBanco"] += 1
            cb["baseCalculoBanco"] = round(cb["baseCalculoBanco"] + bc_val, 2)
            cb["valorIcmsBanco"] = round(cb["valorIcmsBanco"] + icms_val, 2)
            cb["valorContabilBanco"] = round(cb["valorContabilBanco"] + cont_val, 2)

            nb = _get_ncm_bucket(ncm_val, mod_eff, desc_val)
            nb["qtdItensBanco"] += 1
            nb["baseCalculoBanco"] = round(nb["baseCalculoBanco"] + bc_val, 2)
            nb["valorIcmsBanco"] = round(nb["valorIcmsBanco"] + icms_val, 2)
            nb["valorContabilBanco"] = round(nb["valorContabilBanco"] + cont_val, 2)

        # Caso haja notas no banco sem itens detalhados em tb_nfv_item, usa cabeçalho da nota para CFOP
        for d in db_auth_active:
            if d["id"] in used_db_docs_in_items:
                continue
            if target_model and d["nf_modelo"] != target_model:
                continue
            if d.get("cfop"):
                cb = _get_cfop_bucket(d["cfop"], d["nf_modelo"])
                cb["docsBanco"].add(d["nf_numero"])
                cb["qtdItensBanco"] += 1
                cb["baseCalculoBanco"] = round(cb["baseCalculoBanco"] + d["base_icms"], 2)
                cb["valorIcmsBanco"] = round(cb["valorIcmsBanco"] + d["valor_icms"], 2)
                cb["valorContabilBanco"] = round(cb["valorContabilBanco"] + d["valor"], 2)

        # 2.2 Popula a partir dos itens dos XMLs importados
        for x in xml_auth:
            mod_x = _norm_model(x.get("model"), x.get("accessKey", ""))
            if target_model and mod_x != target_model:
                continue
            num_x = _norm_num(x.get("number"))
            x_items = x.get("items") or []
            if x_items:
                for it in x_items:
                    cfop_val = str(it.get("cfop") or "SEM_CFOP")
                    ncm_val = str(it.get("ncm") or "SEM_NCM")
                    desc_val = str(it.get("description") or "")
                    bc_val = float(it.get("icmsBase") or 0.0)
                    icms_val = float(it.get("icmsValue") or 0.0)
                    cont_val = float(it.get("contabilValue") if it.get("contabilValue") is not None else (it.get("totalValue") or 0.0))

                    cb = _get_cfop_bucket(cfop_val, mod_x)
                    cb["docsXml"].add(num_x)
                    cb["qtdItensXml"] += 1
                    cb["baseCalculoXml"] = round(cb["baseCalculoXml"] + bc_val, 2)
                    cb["valorIcmsXml"] = round(cb["valorIcmsXml"] + icms_val, 2)
                    cb["valorContabilXml"] = round(cb["valorContabilXml"] + cont_val, 2)

                    nb = _get_ncm_bucket(ncm_val, mod_x, desc_val)
                    nb["qtdItensXml"] += 1
                    nb["baseCalculoXml"] = round(nb["baseCalculoXml"] + bc_val, 2)
                    nb["valorIcmsXml"] = round(nb["valorIcmsXml"] + icms_val, 2)
                    nb["valorContabilXml"] = round(nb["valorContabilXml"] + cont_val, 2)

        cfop_rows: List[Dict[str, Any]] = []
        for k in sorted(cfop_map.keys()):
            b = cfop_map[k]
            dif_bc = round(b["baseCalculoBanco"] - b["baseCalculoXml"], 2)
            dif_cont = round(b["valorContabilBanco"] - b["valorContabilXml"], 2)
            cfop_rows.append(
                {
                    "cfop": b["cfop"],
                    "modelo": b["modelo"],
                    "qtdNotasBanco": len(b["docsBanco"]),
                    "qtdItensBanco": b["qtdItensBanco"],
                    "baseCalculoBanco": b["baseCalculoBanco"],
                    "valorIcmsBanco": b["valorIcmsBanco"],
                    "valorContabilBanco": b["valorContabilBanco"],
                    "qtdNotasXml": len(b["docsXml"]),
                    "qtdItensXml": b["qtdItensXml"],
                    "baseCalculoXml": b["baseCalculoXml"],
                    "valorIcmsXml": b["valorIcmsXml"],
                    "valorContabilXml": b["valorContabilXml"],
                    "difBaseCalculo": dif_bc,
                    "difValorContabil": dif_cont,
                    "status": "Bateu" if abs(dif_cont) < 0.01 else "Divergente",
                }
            )

        ncm_rows: List[Dict[str, Any]] = []
        for k in sorted(ncm_map.keys()):
            b = ncm_map[k]
            dif_bc = round(b["baseCalculoBanco"] - b["baseCalculoXml"], 2)
            dif_cont = round(b["valorContabilBanco"] - b["valorContabilXml"], 2)
            ncm_rows.append(
                {
                    "ncm": b["ncm"],
                    "modelo": b["modelo"],
                    "descricao": b["descricao"][:60],
                    "qtdItensBanco": b["qtdItensBanco"],
                    "baseCalculoBanco": b["baseCalculoBanco"],
                    "valorIcmsBanco": b["valorIcmsBanco"],
                    "valorContabilBanco": b["valorContabilBanco"],
                    "qtdItensXml": b["qtdItensXml"],
                    "baseCalculoXml": b["baseCalculoXml"],
                    "valorIcmsXml": b["valorIcmsXml"],
                    "valorContabilXml": b["valorContabilXml"],
                    "difBaseCalculo": dif_bc,
                    "difValorContabil": dif_cont,
                    "status": "Bateu" if abs(dif_cont) < 0.01 else "Divergente",
                }
            )
        ncm_rows.sort(key=lambda r: max(r["valorContabilBanco"], r["valorContabilXml"]), reverse=True)
        return cfop_rows, ncm_rows

    cfop_all, ncm_all = _build_cfop_and_ncm_pivots(None)
    cfop_55, ncm_55 = _build_cfop_and_ncm_pivots("55")
    cfop_65, ncm_65 = _build_cfop_and_ncm_pivots("65")

    # =========================================================================
    # 3. EVENTOS SEPARADOS: CANCELADAS (STATUS 101) E INUTILIZADAS (STATUS 102)
    # =========================================================================
    events_map: Dict[str, Dict[str, Any]] = {}

    for d in norm_db_docs:
        if d["status"] not in ("CANCELADO", "INUTILIZADO", "DENEGADO"):
            continue
        if not (d["in_emissao_period"] or d["in_autorizacao_period"]):
            continue
        ev_key = d["id_nfe"] if len(d["id_nfe"]) == 44 else f"{d['nf_modelo']}|{d['nf_serie']}|{d['nf_numero']}"
        events_map[ev_key] = {
            "nf_numero": d["nf_numero"] or "—",
            "nf_serie": d["nf_serie"] or "1",
            "nf_modelo": d["nf_modelo"] or "55",
            "id_nfe": d["id_nfe"] or "—",
            "dt_emissao": d["dt_emissao"],
            "data_hora_sefaz": d["data_hora_sefaz"],
            "status_codigo": d["status_codigo"],
            "status": d["status"],
            "status_des": d["status_des"],
            "valor": d["valor"],
            "constaBanco": True,
            "constaXml": False,
            "constaPdf": False,
            "alertaPoluicao": False,
        }

    for x in xml_in_period:
        st_x = str(x.get("status") or "")
        c_stat = str(x.get("cStat") or "")
        if st_x not in ("CANCELADO", "INUTILIZADO", "DENEGADO") and c_stat not in ("101", "102", "135", "155"):
            continue
        key_x = re.sub(r"\D", "", str(x.get("accessKey") or ""))
        num_x = _norm_num(x.get("number")) or str(x.get("number") or "—")
        ser_x = _norm_series(x.get("series"))
        mod_x = _norm_model(x.get("model"), key_x)
        ev_key = key_x if len(key_x) == 44 else f"{mod_x}|{ser_x}|{num_x}"
        st_code_x = "102" if (st_x == "INUTILIZADO" or c_stat == "102") else ("101" if st_x == "CANCELADO" else (c_stat or "101"))
        if ev_key in events_map:
            events_map[ev_key]["constaXml"] = True
            if not events_map[ev_key]["id_nfe"] or events_map[ev_key]["id_nfe"] == "—":
                events_map[ev_key]["id_nfe"] = key_x or "—"
        else:
            events_map[ev_key] = {
                "nf_numero": num_x,
                "nf_serie": ser_x,
                "nf_modelo": mod_x,
                "id_nfe": key_x or "—",
                "dt_emissao": x.get("issueDate"),
                "data_hora_sefaz": x.get("authTimestamp") or x.get("authDate") or x.get("issueDate"),
                "status_codigo": st_code_x,
                "status": "INUTILIZADO" if st_code_x == "102" else st_x,
                "status_des": x.get("statusDescription") or ("Inutilização de número homologado" if st_code_x == "102" else "Cancelamento de NF-e homologado"),
                "valor": round(float(x.get("totalValue") or 0.0), 2),
                "constaBanco": False,
                "constaXml": True,
                "constaPdf": False,
                "alertaPoluicao": False,
            }

    for p in pdf_in_period:
        st_p = str(p.get("status") or "").upper()
        if st_p not in ("CANCELADO", "INUTILIZADO", "DENEGADO"):
            continue
        key_p = re.sub(r"\D", "", str(p.get("accessKey") or ""))
        num_p = _norm_num(p.get("number"))
        ser_p = _norm_series(p.get("series"))
        mod_p = _norm_model(p.get("model"), key_p)
        ev_key = key_p if len(key_p) == 44 else f"{mod_p}|{ser_p}|{num_p}"
        st_code_p = "102" if "INUT" in st_p else "101"
        if ev_key in events_map:
            events_map[ev_key]["constaPdf"] = True
        else:
            events_map[ev_key] = {
                "nf_numero": num_p or "—",
                "nf_serie": ser_p,
                "nf_modelo": mod_p,
                "id_nfe": key_p or "—",
                "dt_emissao": p.get("issueDate"),
                "data_hora_sefaz": p.get("authDate") or p.get("issueDate"),
                "status_codigo": st_code_p,
                "status": "INUTILIZADO" if st_code_p == "102" else "CANCELADO",
                "status_des": f"{st_p} no Relatório PDF",
                "valor": round(float(p.get("totalValue") or 0.0), 2),
                "constaBanco": False,
                "constaXml": False,
                "constaPdf": True,
                "alertaPoluicao": False,
            }

    events_list = list(events_map.values())
    events_list.sort(key=lambda r: (r["status_codigo"], str(r["dt_emissao"] or ""), str(r["nf_numero"])), reverse=True)
    canceladas_101 = [e for e in events_list if e["status_codigo"] == "101" or e["status"] == "CANCELADO"]
    inutilizadas_102 = [e for e in events_list if e["status_codigo"] == "102" or e["status"] == "INUTILIZADO"]

    # =========================================================================
    # 4. MATRIZ DE DIVERGÊNCIAS & AUDITORIA DE CHAVES
    # =========================================================================
    # Indexa Banco (ativos + eventos no período), XMLs e PDF por Chave (44 dígitos) e por (Modelo, Série, Número)
    db_active_all = [d for d in norm_db_docs if d["in_active_period"]]
    db_by_key: Dict[str, Dict[str, Any]] = {}
    db_by_mod_num: Dict[str, Dict[str, Any]] = {}
    for d in db_active_all:
        if len(d["id_nfe"]) == 44:
            db_by_key[d["id_nfe"]] = d
        if d["nf_numero"]:
            db_by_mod_num[f"{d['nf_modelo']}|{d['nf_numero']}"] = d

    # Só documentos válidos e autorizados entram na conferência de existência/valor.
    # Canceladas e inutilizadas têm aba própria.
    def _is_auth(rec: Dict[str, Any]) -> bool:
        return str(rec.get("status") or "AUTORIZADO").upper() == "AUTORIZADO"

    xml_valid_period = [x for x in xml_in_period if x.get("recordKind") == "DOCUMENTO_FISCAL"]
    xml_by_key: Dict[str, Dict[str, Any]] = {}
    xml_by_mod_num: Dict[str, Dict[str, Any]] = {}
    for x in xml_valid_period:
        k = re.sub(r"\D", "", str(x.get("accessKey") or ""))
        n = _norm_num(x.get("number"))
        m = _norm_model(x.get("model"), k)
        if len(k) == 44:
            xml_by_key[k] = x
        if n:
            xml_by_mod_num[f"{m}|{n}"] = x

    pdf_by_key: Dict[str, Dict[str, Any]] = {}
    pdf_by_mod_num: Dict[str, Dict[str, Any]] = {}
    for pr in pdf_in_period:
        k = re.sub(r"\D", "", str(pr.get("accessKey") or ""))
        n = _norm_num(pr.get("number"))
        m = _norm_model(pr.get("model"), k)
        if len(k) == 44:
            pdf_by_key[k] = pr
        if n:
            pdf_by_mod_num[f"{m}|{n}"] = pr

    def _find(idx_key: Dict[str, Any], idx_mn: Dict[str, Any], key: str, mod: str, num: str):
        if len(key) == 44 and key in idx_key:
            return idx_key[key]
        if num:
            return idx_mn.get(f"{mod}|{num}")
        return None

    # Índices de todas as notas (sem filtro de período) para dizer "cai fora do período" em vez de "falta"
    def _index(recs, key_fn, mod_fn, num_fn):
        by_key: Dict[str, Any] = {}
        by_mn: Dict[str, Any] = {}
        for rec in recs:
            k_, m_, n_ = key_fn(rec), mod_fn(rec), num_fn(rec)
            if len(k_) == 44:
                by_key[k_] = rec
            if n_:
                by_mn[f"{m_}|{n_}"] = rec
        return by_key, by_mn

    def _key_of(rec):
        return re.sub(r"\D", "", str(rec.get("accessKey") or ""))

    db_all_by_key, db_all_by_mod_num = _index(norm_db_docs, lambda d: d["id_nfe"], lambda d: d["nf_modelo"], lambda d: d["nf_numero"])
    xml_all_by_key, xml_all_by_mod_num = _index(
        valid_xml_docs, _key_of, lambda r: _norm_model(r.get("model"), _key_of(r)), lambda r: _norm_num(r.get("number")))
    pdf_all_by_key, pdf_all_by_mod_num = _index(
        pdf_records, _key_of, lambda r: _norm_model(r.get("model"), _key_of(r)), lambda r: _norm_num(r.get("number")))

    # Notas emitidas num dia e autorizadas em outro (para explicar a diferença na linha)
    d1_by_key = {d["id_nfe"]: d for d in d_plus_1_records if len(d["id_nfe"]) == 44}
    d1_by_mod_num = {f"{d['nf_modelo']}|{d['nf_numero']}": d for d in d_plus_1_records if d["nf_numero"]}

    divergence_matrix: List[Dict[str, Any]] = []
    seen_ident = set()
    nomes = {"db": "banco", "xml": "XML", "pdf": "relatório"}

    def _brl(v: Optional[float]) -> str:
        return _fmt_brl_txt(v) if v is not None else "—"

    def _check_note(key: str, mod: str, num: str, serie: str, b, x, pr, base: Dict[str, Any]) -> None:
        """Confere uma nota nas fontes carregadas e registra o que estiver faltando ou diferente."""
        ident = key if len(key) == 44 else f"{mod}|{num}"
        if ident in seen_ident:
            return
        seen_ident.add(ident)

        present = {"db": b is not None, "xml": x is not None, "pdf": pr is not None}
        loaded = {"db": has_db, "xml": has_xml, "pdf": has_pdf}
        missing = [s_ for s_ in ("db", "xml", "pdf") if loaded[s_] and not present[s_]]
        found = [s_ for s_ in ("db", "xml", "pdf") if present[s_]]

        d1 = d1_by_key.get(key) if len(key) == 44 else None
        d1 = d1 or d1_by_mod_num.get(f"{mod}|{num}")
        d1_txt = (f"  •  emitida {_fmt_br_date(d1['dt_emissao'])} e autorizada {_fmt_br_date(d1['dt_autorizacao'])}"
                  if d1 else "")

        v_db = b["valor"] if b else None
        v_xml = round(float(x.get("totalValue") or 0.0), 2) if x else None
        v_pdf = round(float(pr.get("totalValue") or 0.0), 2) if pr else None

        def _row(origem: str, codigos: List[str]) -> None:
            divergence_matrix.append({
                "nf_numero": num or "—",
                "id_nfe": key if len(key) == 44 else (base.get("id_nfe") or "(sem chave de acesso)"),
                "nf_modelo": mod or "55",
                "nf_serie": serie or "1",
                "dt_emissao": base.get("dt_emissao") or "—",
                "data_hora_sefaz": base.get("data_hora_sefaz") or "—",
                "status_des": base.get("status_des") or "Autorizado o uso da NF-e",
                "valor": round(float(base.get("valor") or 0.0), 2),
                "origem_falha": origem + d1_txt,
                "codigo_falha": codigos[0],
                "codigos": codigos,
                "d_mais_1": bool(d1),
                "valor_banco": v_db,
                "valor_xml": v_xml,
                "valor_pdf": v_pdf,
            })

        if missing and found:
            # Separa o que realmente não existe do que existe, mas cai fora do período pela data escolhida
            out_idx = {"db": (db_all_by_key, db_all_by_mod_num), "xml": (xml_all_by_key, xml_all_by_mod_num),
                       "pdf": (pdf_all_by_key, pdf_all_by_mod_num)}
            fora = [m_ for m_ in missing if _find(out_idx[m_][0], out_idx[m_][1], key, mod, num) is not None]
            falta = [m_ for m_ in missing if m_ not in fora]
            partes = []
            if falta:
                partes.append("Falta no " + " e no ".join(nomes[m_] for m_ in falta))
            if fora:
                partes.append("No " + " e no ".join(nomes[m_] for m_ in fora) + " cai fora do período")
            texto = "; ".join(partes) + " — está no " + " e no ".join(nomes[f_] for f_ in found)
            codigos = ["FALTA_" + {"db": "BANCO", "xml": "XML", "pdf": "RELATORIO"}[m_] for m_ in falta]
            if fora:
                codigos.append("FORA_DO_PERIODO")
            _row(texto, codigos)

        valores = {k_: v_ for k_, v_ in (("db", v_db), ("xml", v_xml), ("pdf", v_pdf)) if v_ is not None}
        if len(valores) >= 2 and max(valores.values()) - min(valores.values()) >= 0.01:
            texto = "Valor diferente: " + "  •  ".join(f"{nomes[k_]} {_brl(v_)}" for k_, v_ in valores.items())
            _row(texto, ["DIVERGENCIA_VALOR"])

    # 1) Notas do banco (autorizadas no período)
    for d in db_active_all:
        if d["status"] != "AUTORIZADO":
            continue
        k_d, n_d, m_d = d["id_nfe"], d["nf_numero"], d["nf_modelo"]
        x = _find(xml_by_key, xml_by_mod_num, k_d, m_d, n_d)
        pr = _find(pdf_by_key, pdf_by_mod_num, k_d, m_d, n_d)
        _check_note(k_d, m_d, n_d, d["nf_serie"], d, x, pr, d)

        # Nota autorizada no banco sem chave de acesso válida
        if len(k_d) != 44:
            divergence_matrix.append({
                "nf_numero": n_d or "—", "id_nfe": k_d or "(sem chave de acesso)", "nf_modelo": m_d,
                "nf_serie": d["nf_serie"], "dt_emissao": d["dt_emissao"] or "—",
                "data_hora_sefaz": d["data_hora_sefaz"] or "—", "status_des": d["status_des"],
                "valor": d["valor"], "origem_falha": "Nota no banco sem chave de acesso válida (não transmitida?)",
                "codigo_falha": "CHAVE_INVALIDA_OU_AUSENTE", "codigos": ["CHAVE_INVALIDA_OU_AUSENTE"],
                "d_mais_1": False, "valor_banco": d["valor"], "valor_xml": None, "valor_pdf": None,
            })

    # 2) XMLs que não casaram com o banco
    for x in xml_valid_period:
        if not _is_auth(x):
            continue
        k_x = re.sub(r"\D", "", str(x.get("accessKey") or ""))
        n_x = _norm_num(x.get("number"))
        m_x = _norm_model(x.get("model"), k_x)
        b = _find(db_by_key, db_by_mod_num, k_x, m_x, n_x)
        if b is not None:
            continue  # já conferido no passo 1
        pr = _find(pdf_by_key, pdf_by_mod_num, k_x, m_x, n_x)
        _check_note(k_x, m_x, n_x, _norm_series(x.get("series")), None, x, pr, {
            "dt_emissao": x.get("issueDate"),
            "data_hora_sefaz": x.get("authTimestamp") or x.get("authDate") or x.get("issueDate"),
            "status_des": x.get("statusDescription") or "",
            "valor": x.get("totalValue"),
        })

    # 3) Linhas do relatório que não casaram com banco nem XML
    for pr in pdf_in_period:
        if not _is_auth(pr):
            continue
        k_p = re.sub(r"\D", "", str(pr.get("accessKey") or ""))
        n_p = _norm_num(pr.get("number"))
        m_p = _norm_model(pr.get("model"), k_p)
        if _find(db_by_key, db_by_mod_num, k_p, m_p, n_p) is not None:
            continue
        x = _find(xml_by_key, xml_by_mod_num, k_p, m_p, n_p)
        if x is not None and _is_auth(x):
            continue  # já conferido no passo 2
        _check_note(k_p, m_p, n_p, _norm_series(pr.get("series")), None, None, pr, {
            "dt_emissao": pr.get("issueDate"),
            "data_hora_sefaz": pr.get("authDate") or pr.get("issueDate"),
            "status_des": "Consta no relatório PDF",
            "valor": pr.get("totalValue"),
        })

    divergence_matrix.sort(key=lambda r: (str(r["dt_emissao"]), str(r["nf_modelo"]), str(r["nf_numero"]).zfill(9)))

    # Notas autorizadas em outro dia que NÃO batem na comparação (faltam em alguma fonte ou valor diferente)
    d1_problems: List[Dict[str, Any]] = []
    d1_seen = set()
    for r in divergence_matrix:
        if r["d_mais_1"]:
            ident = r["id_nfe"] if len(str(r["id_nfe"])) == 44 else f"{r['nf_modelo']}|{r['nf_numero']}"
            if ident not in d1_seen:
                d1_seen.add(ident)
                d1_problems.append(r)

    # Marca em TODAS as notas autorizadas em outro dia se têm XML, se estão no relatório e o que não bate
    problem_by_ident = {}
    for r in divergence_matrix:
        ident = r["id_nfe"] if len(str(r["id_nfe"])) == 44 else f"{r['nf_modelo']}|{r['nf_numero']}"
        problem_by_ident.setdefault(ident, []).append(r["origem_falha"].split("  •  emitida")[0])
    for d in d_plus_1_records:
        k_d, m_d, n_d = d["id_nfe"], d["nf_modelo"], d["nf_numero"]
        d["tem_xml"] = _find(xml_all_by_key, xml_all_by_mod_num, k_d, m_d, n_d) is not None
        d["no_relatorio"] = _find(pdf_all_by_key, pdf_all_by_mod_num, k_d, m_d, n_d) is not None
        ident = k_d if len(k_d) == 44 else f"{m_d}|{n_d}"
        d["problema"] = "; ".join(problem_by_ident.get(ident, []))

    def _count(code: str) -> int:
        return sum(1 for r in divergence_matrix if code in r["codigos"])

    matrix_counts = {
        "total": len(divergence_matrix),
        "FALTA_BANCO": _count("FALTA_BANCO"),
        "FALTA_XML": _count("FALTA_XML"),
        "FALTA_RELATORIO": _count("FALTA_RELATORIO"),
        "DIVERGENCIA_VALOR": _count("DIVERGENCIA_VALOR"),
        "FORA_DO_PERIODO": _count("FORA_DO_PERIODO"),
        "CHAVE_INVALIDA_OU_AUSENTE": _count("CHAVE_INVALIDA_OU_AUSENTE"),
        "DIVERGENCIA_D_MAIS_1": len(d1_problems),
    }

    return {
        "generatedAt": datetime.now().isoformat(),
        "competenceFilter": {
            "startDate": dt_inicio,
            "endDate": dt_fim,
            "dateField": date_field,
            "dateFieldLabel": date_field_label,
        },
        "kpiCards": {
            "totalBancoEmissao": total_banco_emissao,
            "qtdBancoEmissao": len(db_auth_emissao),
            "totalBancoAutorizacao": total_banco_autorizacao,
            "qtdBancoAutorizacao": len(db_auth_sefaz),
            "divergenciaEmissaoVsAutorizacao": round(total_banco_emissao - total_banco_autorizacao, 2),
            "qtdDPlus1": len(d_plus_1_records),
            "totalXmlImportados": total_xml_importados,
            "qtdXmlImportados": len(xml_auth),
            "totalRelatorioPdf": total_relatorio_pdf,
            "qtdRelatorioPdf": len(pdf_auth),
            "statusBatimento": batimento_geral,
        },
        "tripleComparison": triple_comparison,
        "fiscalGroupings": {
            "ALL": {"cfop": cfop_all, "ncm": ncm_all},
            "55": {"cfop": cfop_55, "ncm": ncm_55},
            "65": {"cfop": cfop_65, "ncm": ncm_65},
        },
        "separateEvents": {
            "totalEvents": len(events_list),
            "qtdCanceladas101": len(canceladas_101),
            "valorCanceladas101": round(sum(e["valor"] for e in canceladas_101), 2),
            "qtdInutilizadas102": len(inutilizadas_102),
            "valorInutilizadas102": round(sum(e["valor"] for e in inutilizadas_102), 2),
            "canceladas101": canceladas_101,
            "inutilizadas102": inutilizadas_102,
            "allEvents": events_list,
        },
        "divergenceMatrix": divergence_matrix,
        "divergenceCounts": matrix_counts,
        "dPlus1Records": d_plus_1_records,
        "dPlus1Problems": d1_problems,
    }


def run_reconciliation(
    xml_documents: List[Dict[str, Any]],
    pdf_records: List[Dict[str, Any]],
    firebird_records: Optional[List[Dict[str, Any]]] = None,
    options: Optional[Dict[str, Any]] = None,
    manual_decisions: Optional[Dict[str, Dict[str, Any]]] = None,
    db_audit_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    opts = options or {}
    date_field = "authDate" if opts.get("dateField") == "authDate" else "issueDate"
    date_field_label = "Data de Autorização" if date_field == "authDate" else "Data de Emissão"
    compare_payment = opts.get("comparePayment", True)
    fb_list = firebird_records or []
    firebird_enabled = bool(opts.get("firebirdEnabled") and len(fb_list) > 0)
    decisions = manual_decisions or {}

    used_pdf_ids = set()
    used_fb_ids = set()
    items: List[Dict[str, Any]] = []

    pdf_by_key: Dict[str, Dict[str, Any]] = {}
    pdf_by_num_ser: Dict[str, Dict[str, Any]] = {}
    pdf_by_num_only: Dict[str, List[Dict[str, Any]]] = {}

    for p in pdf_records:
        k = re.sub(r"\D", "", str(p.get("accessKey") or ""))
        n = _norm_num(p.get("number"))
        s = _norm_series(p.get("series"))
        if len(k) == 44 and k not in pdf_by_key:
            pdf_by_key[k] = p
        if n:
            ns = f"{n}|{s}"
            if ns not in pdf_by_num_ser:
                pdf_by_num_ser[ns] = p
            pdf_by_num_only.setdefault(n, []).append(p)

    fb_by_key: Dict[str, Dict[str, Any]] = {}
    fb_by_num_ser: Dict[str, Dict[str, Any]] = {}
    fb_by_num_only: Dict[str, List[Dict[str, Any]]] = {}

    if firebird_enabled:
        for fb in fb_list:
            k = re.sub(r"\D", "", str(fb.get("accessKey") or fb.get("id_nfe") or ""))
            n = _norm_num(fb.get("number") or fb.get("nf_numero"))
            s = _norm_series(fb.get("series") or fb.get("nf_serie"))
            if len(k) == 44 and k not in fb_by_key:
                fb_by_key[k] = fb
            if n:
                ns = f"{n}|{s}"
                if ns not in fb_by_num_ser:
                    fb_by_num_ser[ns] = fb
                fb_by_num_only.setdefault(n, []).append(fb)

    def match_fb(doc: Dict[str, Any]):
        if not firebird_enabled:
            return None, None
        k = re.sub(r"\D", "", str(doc.get("accessKey") or doc.get("id_nfe") or ""))
        n = _norm_num(doc.get("number") or doc.get("nf_numero"))
        s = _norm_series(doc.get("series") or doc.get("nf_serie"))
        if len(k) == 44 and k in fb_by_key:
            r = fb_by_key[k]
            used_fb_ids.add(r.get("id"))
            return r, "Chave de acesso exata (44 dígitos) na Base Local / Firebird"
        if n and f"{n}|{s}" in fb_by_num_ser:
            r = fb_by_num_ser[f"{n}|{s}"]
            used_fb_ids.add(r.get("id"))
            return r, f"Número ({n}) + Série ({s}) exatos na Base Local / Firebird"
        if n and n in fb_by_num_only:
            r = fb_by_num_only[n][0]
            used_fb_ids.add(r.get("id"))
            return r, f"Número ({n}) localizado na Base Local com Série divergente ({r.get('series')})"
        return None, "Não localizado na Base Local / Firebird"

    for idx_xml, xml_doc in enumerate(xml_documents):
        item_id = f"rec_{xml_doc.get('id') or idx_xml}"

        if xml_doc.get("isDuplicate"):
            items.append(
                _build_item(
                    item_id=item_id,
                    category="POSSIVEL_DUPLICIDADE",
                    xml_doc=xml_doc,
                    pdf_rec=None,
                    fb_rec=None,
                    date_field=date_field,
                    match_criterion="Detecção de duplicidade entre arquivos XML",
                    fb_criterion=None,
                    reasons=[xml_doc.get("duplicateReason") or "Arquivo XML duplicado."],
                    candidates=[],
                    decisions=decisions,
                )
            )
            continue

        if (
            not xml_doc.get("isValidXml")
            or xml_doc.get("recordKind") in ("INVALIDO", "NAO_VENDA", "INUTILIZACAO")
            or xml_doc.get("status") in ("INVALIDO", "SEM_PROTOCOLO", "INUTILIZADO")
        ):
            fb_rec, fb_crit = match_fb(xml_doc)
            items.append(
                _build_item(
                    item_id=item_id,
                    category="CANCELADO_DENEGADO_INVALIDO",
                    xml_doc=xml_doc,
                    pdf_rec=None,
                    fb_rec=fb_rec,
                    date_field=date_field,
                    match_criterion="Validação estrutural e natureza fiscal do XML",
                    fb_criterion=fb_crit,
                    reasons=[xml_doc.get("statusDescription") or "Documento inválido, inutilizado ou não-venda."],
                    candidates=[],
                    decisions=decisions,
                )
            )
            continue

        xml_key = re.sub(r"\D", "", str(xml_doc.get("accessKey") or ""))
        xml_num = _norm_num(xml_doc.get("number"))
        xml_ser = _norm_series(xml_doc.get("series"))
        ns_key = f"{xml_num}|{xml_ser}"

        matched_pdf = None
        match_criterion = ""
        id_divergence = None

        if len(xml_key) == 44 and xml_key in pdf_by_key and pdf_by_key[xml_key]["id"] not in used_pdf_ids:
            matched_pdf = pdf_by_key[xml_key]
            used_pdf_ids.add(matched_pdf["id"])
            match_criterion = "Correspondência exata por Chave de Acesso (44 dígitos)"
            if _norm_series(matched_pdf.get("series")) != xml_ser or _norm_num(matched_pdf.get("number")) != xml_num:
                id_divergence = (
                    f"Mesma chave de acesso, porém Série/Número divergentes "
                    f"(XML: {xml_num}/{xml_ser} vs PDF: {matched_pdf.get('number')}/{matched_pdf.get('series')})."
                )

        if not matched_pdf and xml_num and ns_key in pdf_by_num_ser and pdf_by_num_ser[ns_key]["id"] not in used_pdf_ids:
            matched_pdf = pdf_by_num_ser[ns_key]
            used_pdf_ids.add(matched_pdf["id"])
            match_criterion = f"Correspondência exata por Número ({xml_num}) e Série ({xml_ser})"

        if not matched_pdf and xml_num and xml_num in pdf_by_num_only:
            cands = [c for c in pdf_by_num_only[xml_num] if c["id"] not in used_pdf_ids]
            if len(cands) == 1:
                matched_pdf = cands[0]
                used_pdf_ids.add(matched_pdf["id"])
                match_criterion = f"Correspondência por Número ({xml_num}) com Série divergente"
                id_divergence = f"Divergência de Série para o documento Nº {xml_num}: XML Série {xml_ser} vs PDF Série {matched_pdf.get('series')}."

        fb_rec, fb_crit = match_fb(xml_doc)

        if xml_doc.get("status") in ("CANCELADO", "DENEGADO"):
            reasons = [f"Documento {xml_doc.get('status')} no XML ({xml_doc.get('statusDescription')})."]
            if matched_pdf and matched_pdf.get("status") == "AUTORIZADO":
                reasons.append("ALERTA CRÍTICO: Documento cancelado/denegado no XML, mas consta como AUTORIZADO no PDF!")
            items.append(
                _build_item(
                    item_id=item_id,
                    category="CANCELADO_DENEGADO_INVALIDO",
                    xml_doc=xml_doc,
                    pdf_rec=matched_pdf,
                    fb_rec=fb_rec,
                    date_field=date_field,
                    match_criterion=match_criterion or f"Status fiscal do XML ({xml_doc.get('status')})",
                    fb_criterion=fb_crit,
                    reasons=reasons,
                    candidates=[],
                    decisions=decisions,
                )
            )
            continue

        if not matched_pdf:
            xml_val = float(xml_doc.get("totalValue") or 0.0)
            ambiguous = []
            for p in pdf_records:
                if p["id"] in used_pdf_ids:
                    continue
                if abs(float(p.get("totalValue") or 0.0) - xml_val) < 0.01:
                    ambiguous.append(
                        {
                            "pdfId": p["id"],
                            "number": p.get("number"),
                            "series": p.get("series"),
                            "totalValue": p.get("totalValue"),
                            "reason": (
                                f"Candidato retido (sem vínculo silencioso): Nº {p.get('number')} no PDF tem mesmo valor "
                                f"R$ {xml_val:.2f}, mas numeração distinta do XML (Nº {xml_num})."
                            ),
                        }
                    )
            reasons = [f"Documento Nº {xml_num} (Série {xml_ser}, R$ {xml_val:.2f}) presente no XML, mas ausente no PDF."]
            if ambiguous:
                reasons.append(ambiguous[0]["reason"])
            items.append(
                _build_item(
                    item_id=item_id,
                    category="REVISAO_MANUAL" if ambiguous else "SOMENTE_XML",
                    xml_doc=xml_doc,
                    pdf_rec=None,
                    fb_rec=fb_rec,
                    date_field=date_field,
                    match_criterion="Candidato ambíguo retido para Revisão Manual" if ambiguous else "Ausente no relatório PDF",
                    fb_criterion=fb_crit,
                    reasons=reasons,
                    candidates=ambiguous,
                    decisions=decisions,
                )
            )
            continue

        reasons = []
        div_id = bool(id_divergence)
        if id_divergence:
            reasons.append(id_divergence)

        xml_val = round(float(xml_doc.get("totalValue") or 0.0), 2)
        pdf_val = round(float(matched_pdf.get("totalValue") or 0.0), 2)
        div_val = abs(xml_val - pdf_val) >= 0.01
        if div_val:
            reasons.append(f"Divergência de Valor: XML = R$ {xml_val:.2f} | PDF = R$ {pdf_val:.2f} (Dif: R$ {abs(xml_val - pdf_val):.2f}).")

        xml_dt = xml_doc.get(date_field) or xml_doc.get("issueDate")
        pdf_dt = matched_pdf.get(date_field) or matched_pdf.get("issueDate")
        div_date = bool(xml_dt and pdf_dt and xml_dt != pdf_dt)
        if div_date:
            reasons.append(f"Divergência de Data ({date_field_label}): XML = {_fmt_br_date(xml_dt)} | PDF = {_fmt_br_date(pdf_dt)}.")

        if div_id:
            cat = "DIVERGENCIA_IDENTIFICACAO"
        elif div_val:
            cat = "DIVERGENCIA_VALOR"
        elif div_date:
            cat = "DIVERGENCIA_DATA"
        else:
            cat = "CONCILIADO"
            reasons.append(f"Conciliado com sucesso ({match_criterion}).")

        items.append(
            _build_item(
                item_id=item_id,
                category=cat,
                xml_doc=xml_doc,
                pdf_rec=matched_pdf,
                fb_rec=fb_rec,
                date_field=date_field,
                match_criterion=match_criterion,
                fb_criterion=fb_crit,
                reasons=reasons,
                candidates=[],
                decisions=decisions,
            )
        )

    for p in pdf_records:
        if p["id"] in used_pdf_ids:
            continue
        fb_rec, fb_crit = match_fb(p)
        items.append(
            _build_item(
                item_id=f"rec_pdf_{p['id']}",
                category="SOMENTE_PDF",
                xml_doc=None,
                pdf_rec=p,
                fb_rec=fb_rec,
                date_field=date_field,
                match_criterion="Presente no PDF sem arquivo XML correspondente",
                fb_criterion=fb_crit,
                reasons=[f"Venda Nº {p.get('number')} (R$ {float(p.get('totalValue') or 0):.2f}) listada no PDF sem XML correspondente."],
                candidates=[],
                decisions=decisions,
            )
        )

    audit_dashboard = build_audit_dashboard_report(
        xml_documents=xml_documents,
        pdf_records=pdf_records,
        db_audit_data=db_audit_data,
        firebird_records=fb_list,
        options=opts,
    )

    summary = _build_summary(items, date_field, date_field_label, firebird_enabled)
    return {
        "executedAt": datetime.now().isoformat(),
        "options": {
            "dateField": date_field,
            "dateFieldLabel": date_field_label,
            "comparePayment": compare_payment,
            "firebirdEnabled": firebird_enabled,
            "startDate": opts.get("startDate") or opts.get("dt_inicio"),
            "endDate": opts.get("endDate") or opts.get("dt_fim"),
        },
        "categoryDefinitions": CATEGORY_DEFINITIONS,
        "summary": summary,
        "items": items,
        "auditDashboard": audit_dashboard,
    }


def _build_item(
    item_id: str,
    category: str,
    xml_doc: Optional[Dict[str, Any]],
    pdf_rec: Optional[Dict[str, Any]],
    fb_rec: Optional[Dict[str, Any]],
    date_field: str,
    match_criterion: str,
    fb_criterion: Optional[str],
    reasons: List[str],
    candidates: List[Dict[str, Any]],
    decisions: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    xml_val = round(float(xml_doc["totalValue"]), 2) if xml_doc and xml_doc.get("isSaleDocument") else None
    pdf_val = round(float(pdf_rec["totalValue"]), 2) if pdf_rec else None
    fb_val = (
        round(float(fb_rec.get("totalValue") if fb_rec.get("totalValue") is not None else (fb_rec.get("valor_total") or 0.0)), 2)
        if fb_rec
        else None
    )

    diff_val = round(xml_val - pdf_val, 2) if (xml_val is not None and pdf_val is not None) else None
    ref_val = xml_val if xml_val is not None else (pdf_val if pdf_val is not None else (fb_val or 0.0))

    eff_cat = category
    rev_status = "PENDENTE" if category == "REVISAO_MANUAL" else "AUTOMATICO"
    eff_reasons = list(reasons)
    dec = decisions.get(item_id)
    if dec:
        act = dec.get("action")
        note = dec.get("note") or ""
        if act == "CONFIRM_CONCILIATION":
            eff_cat = "CONCILIADO"
            rev_status = "CONFIRMADO_MANUALMENTE"
            eff_reasons.insert(0, f"[CONFIRMADO MANUALMENTE]: {note}")
        elif act == "RECLASSIFY" and dec.get("targetCategory"):
            eff_cat = dec["targetCategory"]
            rev_status = "AJUSTADO_MANUALMENTE"
            eff_reasons.insert(0, f"[AJUSTADO MANUALMENTE]: {note}")
        elif act == "REJECT_MATCH":
            eff_cat = "REVISAO_MANUAL"
            rev_status = "REJEITADO_MANUALMENTE"
            eff_reasons.insert(0, f"[REJEITADO MANUALMENTE]: {note}")

    cat_def = CATEGORY_DEFINITIONS.get(eff_cat, CATEGORY_DEFINITIONS["REVISAO_MANUAL"])
    ref_date = (
        (xml_doc.get(date_field) if xml_doc else None)
        or (pdf_rec.get(date_field) if pdf_rec else None)
        or (fb_rec.get(date_field) if fb_rec else None)
    )

    src_any = xml_doc or pdf_rec or fb_rec or {}
    return {
        "id": item_id,
        "originalCategory": category,
        "category": eff_cat,
        "categoryLabel": cat_def["label"],
        "badgeClass": cat_def["badgeClass"],
        "severity": cat_def["severity"],
        "reviewStatus": rev_status,
        "manualDecision": dec,
        "documentNumber": src_any.get("number") or src_any.get("nf_numero") or "—",
        "series": src_any.get("series") or src_any.get("nf_serie") or "—",
        "model": src_any.get("model") or src_any.get("nf_modelo") or "—",
        "docType": (xml_doc or {}).get("docType", "Doc Fiscal"),
        "accessKey": src_any.get("accessKey") or src_any.get("id_nfe") or "",
        "referenceDate": ref_date,
        "issueDate": src_any.get("issueDate") or src_any.get("dt_emissao"),
        "authDate": src_any.get("authDate") or src_any.get("dt_autorizacao"),
        "authTimestamp": (xml_doc or {}).get("authTimestamp") or (fb_rec or {}).get("data_hora_sefaz") or src_any.get("authDate"),
        "status": src_any.get("status", "DESCONHECIDO"),
        "statusDescription": (xml_doc or {}).get("statusDescription") or (fb_rec or {}).get("status_des") or src_any.get("status", ""),
        "paymentMethod": (xml_doc or {}).get("primaryPayment") or (pdf_rec or fb_rec or {}).get("paymentMethod", "Não informado"),
        "xmlValue": xml_val,
        "pdfValue": pdf_val,
        "fbValue": fb_val,
        "referenceValue": round(float(ref_val), 2),
        "valueDifference": diff_val,
        "matchCriterion": match_criterion,
        "fbMatchCriterion": fb_criterion,
        "reasons": eff_reasons,
        "ambiguousCandidates": candidates,
        "sources": {
            "hasXml": xml_doc is not None,
            "hasPdf": pdf_rec is not None,
            "hasFirebird": fb_rec is not None,
            "xml": xml_doc,
            "pdf": pdf_rec,
            "firebird": fb_rec,
        },
    }


def _build_summary(items: List[Dict[str, Any]], date_field: str, date_label: str, fb_enabled: bool) -> Dict[str, Any]:
    by_cat = {
        code: {
            "code": code,
            "label": d["label"],
            "badgeClass": d["badgeClass"],
            "count": 0,
            "totalValue": 0.0,
        }
        for code, d in CATEGORY_DEFINITIONS.items()
    }

    xml_count = 0
    xml_val = 0.0
    pdf_count = 0
    pdf_val = 0.0
    fb_count = 0
    fb_val = 0.0

    for it in items:
        c = by_cat.get(it["category"], by_cat["REVISAO_MANUAL"])
        c["count"] += 1
        c["totalValue"] = round(c["totalValue"] + (it.get("referenceValue") or 0.0), 2)

        if it["sources"]["hasXml"] and it["xmlValue"] is not None:
            xml_count += 1
            if it["sources"]["xml"].get("status") == "AUTORIZADO" and not it["sources"]["xml"].get("isDuplicate"):
                xml_val = round(xml_val + it["xmlValue"], 2)
        if it["sources"]["hasPdf"] and it["pdfValue"] is not None:
            pdf_count += 1
            if it["sources"]["pdf"].get("status") == "AUTORIZADO":
                pdf_val = round(pdf_val + it["pdfValue"], 2)
        if it["sources"]["hasFirebird"] and it["fbValue"] is not None:
            fb_count += 1
            if it["sources"]["firebird"].get("status") in ("AUTORIZADO", "100"):
                fb_val = round(fb_val + it["fbValue"], 2)

    return {
        "totalItems": len(items),
        "dateField": date_field,
        "dateFieldLabel": date_label,
        "firebirdEnabled": fb_enabled,
        "totalsBySource": {
            "xml": {"count": xml_count, "authorizedValue": xml_val},
            "pdf": {"count": pdf_count, "authorizedValue": pdf_val},
            "firebird": {"count": fb_count, "authorizedValue": fb_val},
            "diffXmlVsPdf": round(xml_val - pdf_val, 2),
            "diffXmlVsFirebird": round(xml_val - fb_val, 2) if fb_enabled else None,
        },
        "categories": list(by_cat.values()),
    }


def generate_csv_export_bytes(
    items: List[Dict[str, Any]],
    summary: Dict[str, Any],
    meta: Optional[Dict[str, Any]] = None,
    audit_dashboard: Optional[Dict[str, Any]] = None,
) -> bytes:
    m = meta or {}
    lines = [
        "# RELATÓRIO DE AUDITORIA E CONCILIAÇÃO FISCAL CONTÁBIL - CONFEREFISCAL",
        f"# Data/Hora da Auditoria:;\"{m.get('executedAt', datetime.now().isoformat())}\"",
        f"# Critério de Competência:;\"{summary.get('dateFieldLabel', 'Data de Emissão')}\"",
        f"# Confronto Banco Local / Firebird:;\"{'Ativo' if summary.get('firebirdEnabled') else 'Inativo'}\"",
        "",
        "\"Categoria\";\"Revisão\";\"nf_numero\";\"Série\";\"Modelo\";\"id_nfe (Chave 44 dígitos)\";\"dt_emissao\";\"data_hora_sefaz\";\"status_des\";\"Pagamento\";\"Valor XML\";\"Valor PDF\";\"Valor Banco\";\"Diferença\";\"Origem da Falha / Motivo\"",
    ]
    for it in items:
        row = [
            it.get("categoryLabel", ""),
            it.get("reviewStatus", ""),
            it.get("documentNumber", ""),
            it.get("series", ""),
            it.get("model", ""),
            it.get("accessKey", ""),
            _fmt_br_date(it.get("issueDate")),
            _fmt_br_datetime(it.get("authTimestamp") or it.get("authDate")),
            it.get("statusDescription") or it.get("status", ""),
            it.get("paymentMethod", ""),
            f"{it['xmlValue']:.2f}".replace(".", ",") if it.get("xmlValue") is not None else "",
            f"{it['pdfValue']:.2f}".replace(".", ",") if it.get("pdfValue") is not None else "",
            f"{it['fbValue']:.2f}".replace(".", ",") if it.get("fbValue") is not None else "",
            f"{it['valueDifference']:.2f}".replace(".", ",") if it.get("valueDifference") is not None else "",
            " | ".join(it.get("reasons") or []),
        ]
        escaped = [f"\"{str(v).replace('\"', '\"\"')}\"" for v in row]
        lines.append(";".join(escaped))

    if audit_dashboard and audit_dashboard.get("divergenceMatrix"):
        lines.extend(
            [
                "",
                "# MATRIZ DE DIVERGÊNCIAS & AUDITORIA DE CHAVES",
                "\"nf_numero\";\"id_nfe\";\"Modelo\";\"Série\";\"dt_emissao\";\"data_hora_sefaz\";\"status_des\";\"Valor\";\"Origem da Falha\"",
            ]
        )
        for dv in audit_dashboard["divergenceMatrix"]:
            r_dv = [
                dv.get("nf_numero", ""),
                dv.get("id_nfe", ""),
                dv.get("nf_modelo", ""),
                dv.get("nf_serie", ""),
                _fmt_br_date(dv.get("dt_emissao")),
                _fmt_br_datetime(dv.get("data_hora_sefaz")),
                dv.get("status_des", ""),
                f"{float(dv.get('valor') or 0.0):.2f}".replace(".", ","),
                dv.get("origem_falha", ""),
            ]
            lines.append(";".join(f"\"{str(v).replace('\"', '\"\"')}\"" for v in r_dv))

    return ("\ufeff" + "\r\n".join(lines)).encode("utf-8")


def _col_letter(col_idx_zero: int) -> str:
    result = ""
    n = col_idx_zero + 1
    while n > 0:
        n, rem = divmod(n - 1, 26)
        result = chr(65 + rem) + result
    return result


def _build_worksheet_xml(rows_data: List[List[Any]]) -> str:
    def esc(val: Any) -> str:
        return (
            str(val if val is not None else "")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )

    xml_rows = []
    for r_idx, r in enumerate(rows_data, start=1):
        cells = []
        for c_idx, val in enumerate(r):
            ref = f"{_col_letter(c_idx)}{r_idx}"
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                cells.append(f'<c r="{ref}"><v>{val}</v></c>')
            else:
                cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{esc(val)}</t></is></c>')
        xml_rows.append(f'<row r="{r_idx}">{"".join(cells)}</row>')

    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(xml_rows)}</sheetData></worksheet>'
    )


def generate_xlsx_export_bytes(
    items: List[Dict[str, Any]],
    summary: Dict[str, Any],
    audit_dashboard: Optional[Dict[str, Any]] = None,
) -> bytes:
    """
    Gera um arquivo Excel (.xlsx) OpenXML multipasta nativo em Python (com Cabeçalho de Auditoria,
    Matriz de Divergências, Tabelas Dinâmicas CFOP/NCM, Canceladas/Inutilizadas 101/102 e Detalhamento).
    """
    ad = audit_dashboard or {}
    kpis = ad.get("kpiCards") or {}
    comp = ad.get("competenceFilter") or {}
    bat = kpis.get("statusBatimento") or {}
    tc = ad.get("tripleComparison") or {}

    # Aba 1: Resumo & Cabeçalho de Auditoria + Matriz de Divergências
    sheet1_rows: List[List[Any]] = [
        ["AUDITORIA E CONCILIAÇÃO FISCAL CONTÁBIL - CONFEREFISCAL"],
        ["Data/Hora de Geração", datetime.now().strftime("%d/%m/%Y %H:%M:%S")],
        ["Base de Competência", comp.get("dateFieldLabel") or summary.get("dateFieldLabel", "Por Data de Emissão")],
        ["Período Filtrado", f"{_fmt_br_date(comp.get('startDate'))} a {_fmt_br_date(comp.get('endDate'))}"],
        ["Status de Batimento", bat.get("label", "Processado"), bat.get("detail", "")],
        [],
        ["INDICADORES GERAIS (CARDS KPI)", "QTD DOCUMENTOS", "VALOR TOTAL (R$)"],
        ["Total Banco (Por Data de Emissão)", kpis.get("qtdBancoEmissao", 0), kpis.get("totalBancoEmissao", 0.0)],
        ["Total Banco (Por Data de Autorização SEFAZ)", kpis.get("qtdBancoAutorizacao", 0), kpis.get("totalBancoAutorizacao", 0.0)],
        ["Divergência Temporal D+1 (Emissão vs Autorização)", kpis.get("qtdDPlus1", 0), kpis.get("divergenciaEmissaoVsAutorizacao", 0.0)],
        ["Total XMLs Importados (Autorizados Status 100)", kpis.get("qtdXmlImportados", 0), kpis.get("totalXmlImportados", 0.0)],
        ["Total Relatório PDF (Autorizados)", kpis.get("qtdRelatorioPdf", 0), kpis.get("totalRelatorioPdf", 0.0)],
        [],
        [
            "COMPARATIVO TRIPLO (BANCO vs XML vs RELATÓRIO)",
            "Qtd Banco (Emissão)",
            "Total Banco (Emissão)",
            "Qtd Banco (Autorização)",
            "Total Banco (Autorização)",
            "Qtd XML",
            "Total XML",
            "Qtd PDF",
            "Total PDF",
            "Status Batimento",
        ],
    ]
    for m_key in ("ALL", "55", "65"):
        row_m = tc.get(m_key)
        if row_m:
            sheet1_rows.append(
                [
                    row_m.get("label", m_key),
                    row_m.get("qtdBancoEmissao", 0),
                    row_m.get("totalBancoEmissao", 0.0),
                    row_m.get("qtdBancoAutorizacao", 0),
                    row_m.get("totalBancoAutorizacao", 0.0),
                    row_m.get("qtdXml", 0),
                    row_m.get("totalXml", 0.0),
                    row_m.get("qtdPdf", 0),
                    row_m.get("totalPdf", 0.0),
                    (row_m.get("batimento") or {}).get("label", ""),
                ]
            )

    # Aba 2: Matriz de Divergências & Auditoria de Chaves
    sheet2_rows: List[List[Any]] = [
        ["MATRIZ DE DIVERGÊNCIAS & AUDITORIA DE CHAVES (BANCO vs XML vs RELATÓRIO PDF)"],
        [
            "nf_numero",
            "id_nfe (Chave 44 dígitos)",
            "Modelo",
            "Série",
            "dt_emissao",
            "data_hora_sefaz",
            "status_des",
            "Valor",
            "Origem da Falha",
            "Valor Banco",
            "Valor XML",
            "Valor PDF",
        ],
    ]
    for dv in ad.get("divergenceMatrix") or []:
        sheet2_rows.append(
            [
                dv.get("nf_numero", ""),
                dv.get("id_nfe", ""),
                dv.get("nf_modelo", ""),
                dv.get("nf_serie", ""),
                _fmt_br_date(dv.get("dt_emissao")),
                _fmt_br_datetime(dv.get("data_hora_sefaz")),
                dv.get("status_des", ""),
                dv.get("valor", 0.0),
                dv.get("origem_falha", ""),
                dv.get("valor_banco") if dv.get("valor_banco") is not None else "",
                dv.get("valor_xml") if dv.get("valor_xml") is not None else "",
                dv.get("valor_pdf") if dv.get("valor_pdf") is not None else "",
            ]
        )

    # Aba 3: Agrupamentos Fiscais por CFOP e NCM
    fg = ad.get("fiscalGroupings") or {}
    sheet3_rows: List[List[Any]] = [
        ["AGRUPAMENTO FISCAL POR CFOP (NF-e 55 e NFC-e 65)"],
        [
            "Modelo",
            "CFOP",
            "Qtd Notas Banco",
            "Base de Cálculo Banco (R$)",
            "Valor ICMS Banco (R$)",
            "Valor Contábil Banco (R$)",
            "Qtd Notas XML",
            "Base de Cálculo XML (R$)",
            "Valor ICMS XML (R$)",
            "Valor Contábil XML (R$)",
            "Diferença Contábil (R$)",
            "Status",
        ],
    ]
    for r_cfop in (fg.get("ALL") or {}).get("cfop") or []:
        sheet3_rows.append(
            [
                r_cfop.get("modelo", ""),
                r_cfop.get("cfop", ""),
                r_cfop.get("qtdNotasBanco", 0),
                r_cfop.get("baseCalculoBanco", 0.0),
                r_cfop.get("valorIcmsBanco", 0.0),
                r_cfop.get("valorContabilBanco", 0.0),
                r_cfop.get("qtdNotasXml", 0),
                r_cfop.get("baseCalculoXml", 0.0),
                r_cfop.get("valorIcmsXml", 0.0),
                r_cfop.get("valorContabilXml", 0.0),
                r_cfop.get("difValorContabil", 0.0),
                r_cfop.get("status", ""),
            ]
        )
    sheet3_rows.append([])
    sheet3_rows.append(["AGRUPAMENTO FISCAL POR NCM (NF-e 55 e NFC-e 65)"])
    sheet3_rows.append(
        [
            "Modelo",
            "NCM",
            "Descrição",
            "Qtd Itens Banco",
            "Base de Cálculo Banco (R$)",
            "Valor Contábil Banco (R$)",
            "Qtd Itens XML",
            "Base de Cálculo XML (R$)",
            "Valor Contábil XML (R$)",
            "Diferença Contábil (R$)",
            "Status",
        ]
    )
    for r_ncm in (fg.get("ALL") or {}).get("ncm") or []:
        sheet3_rows.append(
            [
                r_ncm.get("modelo", ""),
                r_ncm.get("ncm", ""),
                r_ncm.get("descricao", ""),
                r_ncm.get("qtdItensBanco", 0),
                r_ncm.get("baseCalculoBanco", 0.0),
                r_ncm.get("valorContabilBanco", 0.0),
                r_ncm.get("qtdItensXml", 0),
                r_ncm.get("baseCalculoXml", 0.0),
                r_ncm.get("valorContabilXml", 0.0),
                r_ncm.get("difValorContabil", 0.0),
                r_ncm.get("status", ""),
            ]
        )

    # Aba 4: Canceladas (101) e Inutilizadas (102)
    sep_ev = ad.get("separateEvents") or {}
    sheet4_rows: List[List[Any]] = [
        ["EVENTOS SEPARADOS: CANCELADAS (STATUS 101) E INUTILIZADAS (STATUS 102)"],
        [
            "Código Status",
            "Status",
            "nf_numero",
            "Série",
            "Modelo",
            "id_nfe (Chave 44 dígitos)",
            "dt_emissao",
            "data_hora_sefaz",
            "status_des",
            "Valor (R$)",
            "Consta Banco",
            "Consta XML",
            "Consta PDF",
        ],
    ]
    for ev in sep_ev.get("allEvents") or []:
        sheet4_rows.append(
            [
                ev.get("status_codigo", ""),
                ev.get("status", ""),
                ev.get("nf_numero", ""),
                ev.get("nf_serie", ""),
                ev.get("nf_modelo", ""),
                ev.get("id_nfe", ""),
                _fmt_br_date(ev.get("dt_emissao")),
                _fmt_br_datetime(ev.get("data_hora_sefaz")),
                ev.get("status_des", ""),
                ev.get("valor", 0.0),
                "Sim" if ev.get("constaBanco") else "Não",
                "Sim" if ev.get("constaXml") else "Não",
                "Sim" if ev.get("constaPdf") else "Não",
            ]
        )

    # Aba 5: Todos os Documentos Conciliados
    sheet5_rows: List[List[Any]] = [
        [
            "Categoria",
            "Revisão",
            "nf_numero",
            "Série",
            "Modelo",
            "id_nfe (Chave 44 dígitos)",
            "dt_emissao",
            "data_hora_sefaz",
            "status_des",
            "Pagamento",
            "Valor XML",
            "Valor PDF",
            "Valor Base Local",
            "Diferença",
            "Critério",
            "Motivo",
        ]
    ]
    for it in items:
        sheet5_rows.append(
            [
                it.get("categoryLabel", ""),
                it.get("reviewStatus", ""),
                it.get("documentNumber", ""),
                it.get("series", ""),
                it.get("model", ""),
                it.get("accessKey", ""),
                _fmt_br_date(it.get("issueDate")),
                _fmt_br_datetime(it.get("authTimestamp") or it.get("authDate")),
                it.get("statusDescription") or it.get("status", ""),
                it.get("paymentMethod", ""),
                it.get("xmlValue") if it.get("xmlValue") is not None else "",
                it.get("pdfValue") if it.get("pdfValue") is not None else "",
                it.get("fbValue") if it.get("fbValue") is not None else "",
                it.get("valueDifference") if it.get("valueDifference") is not None else "",
                it.get("matchCriterion", ""),
                " | ".join(it.get("reasons") or []),
            ]
        )

    sheets_spec = [
        ("Auditoria Resumo", sheet1_rows),
        ("Matriz Divergencias", sheet2_rows),
        ("CFOP e NCM", sheet3_rows),
        ("Canceladas Inut 101 102", sheet4_rows),
        ("Conciliacao Detalhada", sheet5_rows),
    ]

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        overrides = [
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        ]
        sheet_entries = []
        rel_entries = []
        for idx, (s_name, s_rows) in enumerate(sheets_spec, start=1):
            overrides.append(
                f'<Override PartName="/xl/worksheets/sheet{idx}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            )
            sheet_entries.append(f'<sheet name="{s_name}" sheetId="{idx}" r:id="rId{idx}"/>')
            rel_entries.append(
                f'<Relationship Id="rId{idx}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{idx}.xml"/>'
            )
            zf.writestr(f"xl/worksheets/sheet{idx}.xml", _build_worksheet_xml(s_rows))

        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            f'{"".join(overrides)}'
            "</Types>",
        )
        zf.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>",
        )
        zf.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f'<sheets>{"".join(sheet_entries)}</sheets></workbook>',
        )
        zf.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'{"".join(rel_entries)}'
            "</Relationships>",
        )

    return out.getvalue()


def generate_pdf_export_bytes(
    items: List[Dict[str, Any]],
    summary: Dict[str, Any],
    meta: Optional[Dict[str, Any]] = None,
    audit_dashboard: Optional[Dict[str, Any]] = None,
) -> bytes:
    """
    Gera um relatório PDF de Auditoria Fiscal Contábil multipágina com Cabeçalho de Auditoria,
    Comparativo Triplo, CFOP/NCM, Eventos 101/102 e Matriz de Divergências.
    """
    m = meta or {}
    ad = audit_dashboard or {}
    kpis = ad.get("kpiCards") or {}
    comp = ad.get("competenceFilter") or {}
    bat = kpis.get("statusBatimento") or {}
    tc = ad.get("tripleComparison") or {}
    sep_ev = ad.get("separateEvents") or {}

    lines: List[str] = [
        "==================================================================================================",
        "  CABECALHO DE AUDITORIA FISCAL E CONTABIL - CONFEREFISCAL (NF-e 55 / NFC-e 65 / SPED)",
        "==================================================================================================",
        f"Data/Hora da Auditoria   : {m.get('executedAt', datetime.now().strftime('%d/%m/%Y %H:%M:%S'))}",
        f"Regime de Competencia    : {comp.get('dateFieldLabel') or summary.get('dateFieldLabel', 'Por Data de Emissao')}",
        f"Periodo Auditado         : {_fmt_br_date(comp.get('startDate'))} ate {_fmt_br_date(comp.get('endDate'))}",
        f"Status de Batimento      : {bat.get('label', 'Processado')} ({bat.get('detail', '')})",
        "--------------------------------------------------------------------------------------------------",
        "1. VISAO GERAL & CARDS KPI:",
        f"  * Total Banco (Por Data de Emissao)           : Qtd {kpis.get('qtdBancoEmissao', 0):>5} | R$ {kpis.get('totalBancoEmissao', 0.0):>12.2f}",
        f"  * Total Banco (Por Data de Autorizacao SEFAZ) : Qtd {kpis.get('qtdBancoAutorizacao', 0):>5} | R$ {kpis.get('totalBancoAutorizacao', 0.0):>12.2f}",
        f"  * Desafio Temporal D+1 (Emissao != Autoriz.)  : Qtd {kpis.get('qtdDPlus1', 0):>5} | R$ {kpis.get('divergenciaEmissaoVsAutorizacao', 0.0):>12.2f}",
        f"  * Total XMLs Importados (Status 100)          : Qtd {kpis.get('qtdXmlImportados', 0):>5} | R$ {kpis.get('totalXmlImportados', 0.0):>12.2f}",
        f"  * Total Relatorio PDF                         : Qtd {kpis.get('qtdRelatorioPdf', 0):>5} | R$ {kpis.get('totalRelatorioPdf', 0.0):>12.2f}",
        "--------------------------------------------------------------------------------------------------",
        "2. COMPARATIVO TRIPLO (BANCO vs XML vs RELATORIO):",
    ]

    for m_key in ("ALL", "55", "65"):
        rm = tc.get(m_key)
        if rm:
            lbl = str(rm.get("label", m_key))[:26].ljust(26)
            lines.append(
                f"  [{lbl}] Bco(Emi): R$ {rm.get('totalBancoEmissao', 0.0):>10.2f} | "
                f"Bco(Aut): R$ {rm.get('totalBancoAutorizacao', 0.0):>10.2f} | "
                f"XML: R$ {rm.get('totalXml', 0.0):>10.2f} | PDF: R$ {rm.get('totalPdf', 0.0):>10.2f}"
            )

    lines.extend(
        [
            "--------------------------------------------------------------------------------------------------",
            "3. EVENTOS SEPARADOS (NAO POLUEM O FATURAMENTO LIQUIDO STATUS 100):",
            f"  * Canceladas (Status 101)   : Qtd {sep_ev.get('qtdCanceladas101', 0):>5} | R$ {sep_ev.get('valorCanceladas101', 0.0):>12.2f}",
            f"  * Inutilizadas (Status 102) : Qtd {sep_ev.get('qtdInutilizadas102', 0):>5} | R$ {sep_ev.get('valorInutilizadas102', 0.0):>12.2f}",
        ]
    )

    cfop_all = ((ad.get("fiscalGroupings") or {}).get("ALL") or {}).get("cfop") or []
    if cfop_all:
        lines.extend(
            [
                "--------------------------------------------------------------------------------------------------",
                "4. AGRUPAMENTO FISCAL POR CFOP (BASE DE CALCULO E VALOR CONTABIL):",
                "MOD | CFOP | BCO BC ICMS  | BCO CONTABIL | XML BC ICMS  | XML CONTABIL | DIFERENCA    | STATUS",
            ]
        )
        for c in cfop_all[:25]:
            lines.append(
                f" {str(c.get('modelo', ''))[:2]:<2} | {str(c.get('cfop', ''))[:4]:<4} | "
                f"{c.get('baseCalculoBanco', 0.0):>12.2f} | {c.get('valorContabilBanco', 0.0):>12.2f} | "
                f"{c.get('baseCalculoXml', 0.0):>12.2f} | {c.get('valorContabilXml', 0.0):>12.2f} | "
                f"{c.get('difValorContabil', 0.0):>12.2f} | {c.get('status', '')}"
            )

    div_matrix = ad.get("divergenceMatrix") or []
    if div_matrix:
        lines.extend(
            [
                "--------------------------------------------------------------------------------------------------",
                f"5. MATRIZ DE DIVERGENCIAS & AUDITORIA DE CHAVES ({len(div_matrix)} OCORRENCIAS):",
                "NF_NUMERO | MOD | DT_EMISSAO | DATA_HORA_SEFAZ     | VALOR (R$) | ORIGEM DA FALHA",
                "--------------------------------------------------------------------------------------------------",
            ]
        )
        for dv in div_matrix[:250]:
            num = str(dv.get("nf_numero", "—"))[:9].ljust(9)
            mod = str(dv.get("nf_modelo", "55"))[:3].ljust(3)
            dte = _fmt_br_date(dv.get("dt_emissao"))[:10].ljust(10)
            dhs = _fmt_br_datetime(dv.get("data_hora_sefaz"))[:19].ljust(19)
            vlr = f"{float(dv.get('valor') or 0.0):>10.2f}"
            org = str(dv.get("origem_falha", ""))[:45]
            lines.append(f"{num} | {mod} | {dte} | {dhs} | {vlr} | {org}")
            lines.append(f"   Chave 44d: {dv.get('id_nfe', '')} | Status: {dv.get('status_des', '')}")

    lines.extend(
        [
            "--------------------------------------------------------------------------------------------------",
            "6. DETALHAMENTO DE DOCUMENTOS CONCILIADOS (AMOSTRA):",
            "DOC       | SER | DATA       | CATEGORIA               | XML (R$)   | PDF (R$)   | BASE LOCAL",
            "--------------------------------------------------------------------------------------------------",
        ]
    )

    for it in items[:200]:
        doc = str(it.get("documentNumber", "—"))[:9].ljust(9)
        ser = str(it.get("series", "1"))[:3].ljust(3)
        dt = _fmt_br_date(it.get("referenceDate"))[:10].ljust(10)
        cat = str(it.get("categoryLabel", ""))[:23].ljust(23)
        xv = (f"{it['xmlValue']:.2f}" if it.get("xmlValue") is not None else "—").rjust(10)
        pv = (f"{it['pdfValue']:.2f}" if it.get("pdfValue") is not None else "—").rjust(10)
        fv = (f"{it['fbValue']:.2f}" if it.get("fbValue") is not None else "—").rjust(10)
        lines.append(f"{doc} | {ser} | {dt} | {cat} | {xv} | {pv} | {fv}")

    lines_per_page = 48
    pages = [lines[i : i + lines_per_page] for i in range(0, max(len(lines), 1), lines_per_page)]

    def _pdf_esc(s: str) -> str:
        clean = s.encode("latin-1", errors="replace").decode("latin-1")
        return clean.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    objects: List[str] = []
    page_ids = [4 + idx * 2 for idx in range(len(pages))]
    kids_ref = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects.append("1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n")
    objects.append(f"2 0 obj\n<< /Type /Pages /Kids [{kids_ref}] /Count {len(pages)} >>\nendobj\n")
    objects.append("3 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Courier /Encoding /WinAnsiEncoding >>\nendobj\n")

    for idx, page_lines in enumerate(pages):
        page_id = 4 + idx * 2
        content_id = page_id + 1
        stream_cmds = ["BT", "/F1 7.6 Tf", "26 808 Td", "13.5 TL"]
        for l_idx, txt in enumerate(page_lines):
            if l_idx == 0:
                stream_cmds.append(f"({_pdf_esc(txt)}) Tj")
            else:
                stream_cmds.append(f"T* ({_pdf_esc(txt)}) Tj")
        stream_cmds.append("ET")
        stream_str = "\n".join(stream_cmds)
        objects.append(
            f"{page_id} 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>\nendobj\n"
        )
        objects.append(
            f"{content_id} 0 obj\n<< /Length {len(stream_str.encode('latin-1'))} >>\nstream\n{stream_str}\nendstream\nendobj\n"
        )

    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objects:
        offsets.append(len(out))
        out.extend(obj.encode("latin-1"))

    xref_pos = len(out)
    out.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode("latin-1"))
    for off in offsets[1:]:
        out.extend(f"{off:010d} 00000 n \n".encode("latin-1"))
    out.extend(
        f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode("latin-1")
    )
    return bytes(out)
