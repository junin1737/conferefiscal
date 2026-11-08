import os
import re
import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, List, Optional

from python_app.database_manager import (
    LocalDatabaseManager,
    ROOT_DIR,
    DEFAULT_CLIPP_FDB_PATH,
    encrypt_secret,
    log_event,
)
from python_app.fiscal_parsers import (
    extract_xml_containers,
    extract_xml_from_folder,
    analyze_extracted_xmls,
    parse_sales_pdf_bytes,
    perform_local_ocr_pdf_bytes,
)
from python_app.reconciliation_engine import (
    CATEGORY_DEFINITIONS,
    run_reconciliation,
    build_audit_dashboard_report,
    generate_csv_export_bytes,
    generate_xlsx_export_bytes,
    generate_pdf_export_bytes,
)

PUBLIC_DIR = ROOT_DIR / "src" / "public"
EXAMPLES_DIR = ROOT_DIR / "exemplos"
ICON_CANDIDATES = [
    Path(r"C:\Work\Pessoal\Projetos\ConfereFiscal\instalador\icone.png"),
    ROOT_DIR / "instalador" / "icone.png",
    ROOT_DIR / "icone.png",
]

db_manager = LocalDatabaseManager()


def _create_empty_session() -> Dict[str, Any]:
    return {
        "isDemoMode": False,
        "xmlData": {
            "importedAt": None,
            "containerSummaries": [],
            "extractionErrors": [],
            "validationAlerts": [],
            "documents": [],
            "summary": None,
        },
        "pdfData": {
            "importedAt": None,
            "fileName": None,
            "sizeBytes": 0,
            "sha256": None,
            "pageCount": 0,
            "lastBuffer": b"",
            "isScannedImagePdf": False,
            "ocrExecuted": False,
            "scannedWarning": None,
            "rawTextPreview": "",
            "extractionMethod": None,
            "columnsMeta": [],
            "detectedMapping": None,
            "activeMapping": None,
            "rawRows": [],
            "records": [],
            "summary": None,
        },
        "firebirdData": {
            "enabled": True,
            "connectionTested": True,
            "host": "127.0.0.1",
            "port": 3050,
            "database": DEFAULT_CLIPP_FDB_PATH if os.path.exists(DEFAULT_CLIPP_FDB_PATH) else str(EXAMPLES_DIR / "demo_fb50.fdb"),
            "user": "SYSDBA",
            "engineVersion": "2.5.9 (Base Local Python)",
            "versionFamily": "2.5",
            "odsVersion": "11.2",
            "isDemoConnection": False,
            "tables": [],
            "mapping": {
                "tableName": "TB_DOC_FISCAL_SAIDA",
                "numberColumn": "NUM_DOCUMENTO",
                "seriesColumn": "SERIE_DOC",
                "modelColumn": "MODELO_FISCAL",
                "accessKeyColumn": "CHAVE_NFE",
                "issueDateColumn": "DT_EMISSAO",
                "authDateColumn": "DT_AUTORIZACAO",
                "totalValueColumn": "VLR_TOTAL_NF",
                "statusColumn": "SITUACAO_DOC",
                "paymentColumn": "DESC_MEIO_PAGTO",
                "customerColumn": "CPF_CNPJ_DEST",
            },
            "previewSql": None,
            "previewRecords": [],
            "fullRecords": [],
        },
        "reconciliation": {
            "executedAt": None,
            "options": {
                "dateField": "issueDate",
                "startDate": None,
                "endDate": None,
                "comparePayment": True,
                "firebirdEnabled": True,
            },
            "manualDecisions": {},
            "result": None,
        },
    }


SESSION_STATE = _create_empty_session()


def _compute_current_audit_dashboard(options_override: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    opts = dict(SESSION_STATE["reconciliation"]["options"])
    if options_override:
        opts.update(options_override)
    dt_ini = opts.get("startDate") or opts.get("dt_inicio") or None
    dt_fim = opts.get("endDate") or opts.get("dt_fim") or None
    date_field = "authDate" if opts.get("dateField") == "authDate" else "issueDate"

    db_audit_data = db_manager.query_clipp_audit_dataset(
        dt_inicio=dt_ini,
        dt_fim=dt_fim,
        date_field=date_field,
    )
    fb_records = db_manager.query_mapped_sales(SESSION_STATE["firebirdData"]["mapping"], limit=50000)["records"]
    return build_audit_dashboard_report(
        xml_documents=SESSION_STATE["xmlData"].get("documents") or [],
        pdf_records=SESSION_STATE["pdfData"].get("records") or [],
        db_audit_data=db_audit_data,
        firebird_records=fb_records,
        options=opts,
    )


def _parse_multipart(body: bytes, boundary: str) -> List[Dict[str, Any]]:
    files = []
    b_bytes = f"--{boundary}".encode("latin1")
    parts = body.split(b_bytes)
    for part in parts[1:]:
        if part.startswith(b"--"):
            break
        if part.startswith(b"\r\n"):
            part = part[2:]
        if part.endswith(b"\r\n"):
            part = part[:-2]
        sep_idx = part.find(b"\r\n\r\n")
        if sep_idx == -1:
            continue
        raw_headers = part[:sep_idx].decode("utf-8", errors="replace")
        content = part[sep_idx + 4 :]
        fn_match = re.search(r'filename="([^"]*)"', raw_headers, flags=re.IGNORECASE)
        if fn_match and fn_match.group(1):
            files.append(
                {
                    "name": os.path.basename(fn_match.group(1)),
                    "originalname": os.path.basename(fn_match.group(1)),
                    "buffer": content,
                }
            )
    return files


class ConfereFiscalPythonHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        pass

    def _send_json(self, payload: Any, status: int = 200) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length > 0 else b""

    def _read_json(self) -> Dict[str, Any]:
        raw = self._read_body()
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        route = parsed.path

        if route in ("/icone.png", "/favicon.ico", "/api/icon.png"):
            for ico_path in ICON_CANDIDATES:
                if ico_path.exists():
                    data = ico_path.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return

        if route == "/api/status":
            schema_info = db_manager.discover_schema()
            SESSION_STATE["firebirdData"]["tables"] = schema_info.get("tables", [])
            pdf_public = {k: v for k, v in SESSION_STATE["pdfData"].items() if k != "lastBuffer"}
            audit_dash = _compute_current_audit_dashboard()
            self._send_json(
                {
                    "backendEngine": "Python 3.12 Local",
                    "isDemoMode": SESSION_STATE["isDemoMode"],
                    "xmlData": SESSION_STATE["xmlData"],
                    "pdfData": pdf_public,
                    "firebirdData": SESSION_STATE["firebirdData"],
                    "reconciliation": SESSION_STATE["reconciliation"],
                    "auditDashboard": audit_dash,
                    "categoryDefinitions": CATEGORY_DEFINITIONS,
                    "dbOverview": db_manager.get_database_overview(),
                }
            )
            return

        if route == "/api/audit/dashboard":
            qs = parse_qs(parsed.query)
            opts_ov = {
                "startDate": qs.get("startDate", [None])[0],
                "endDate": qs.get("endDate", [None])[0],
                "dateField": qs.get("dateField", [SESSION_STATE["reconciliation"]["options"].get("dateField", "issueDate")])[0],
            }
            SESSION_STATE["reconciliation"]["options"].update(opts_ov)
            dash = _compute_current_audit_dashboard(opts_ov)
            self._send_json({"success": True, "auditDashboard": dash, "dbOverview": db_manager.get_database_overview()})
            return

        if route == "/api/db/overview":
            self._send_json(
                {
                    "success": True,
                    "overview": db_manager.get_database_overview(),
                    "schema": db_manager.discover_schema(),
                }
            )
            return

        if route == "/api/db/records":
            qs = parse_qs(parsed.query)
            filters = {k: v[0] for k, v in qs.items() if v}
            records = db_manager.list_sales_records(filters)
            self._send_json(
                {
                    "success": True,
                    "records": records,
                    "overview": db_manager.get_database_overview(),
                }
            )
            return

        if route == "/api/storage":
            ov = db_manager.get_database_overview()
            self._send_json(
                {
                    "baseDirectory": str(ROOT_DIR / "dados_locais"),
                    "totalFiles": 1,
                    "totalBytes": ov["sizeBytes"],
                    "totalFormatted": ov["sizeFormatted"],
                    "directories": {
                        "local_db": {
                            "path": ov["databasePath"],
                            "description": "Base de dados local Python (SQLite / Espelho Firebird tb_nfvenda & tb_nfe)",
                            "fileCount": 1,
                            "totalBytes": ov["sizeBytes"],
                        }
                    },
                    "recentLogs": [],
                }
            )
            return

        # Serve arquivos estáticos de src/public
        rel = "/index.html" if route == "/" else route
        safe_rel = os.path.normpath(rel.lstrip("/"))
        target_file = (PUBLIC_DIR / safe_rel).resolve()

        if str(target_file).startswith(str(PUBLIC_DIR.resolve())) and target_file.exists() and target_file.is_file():
            mime, _ = mimetypes.guess_type(str(target_file))
            content = target_file.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", f"{mime or 'application/octet-stream'}; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return

        self._send_json({"success": False, "message": f"Rota não encontrada: {route}"}, 404)

    def do_POST(self) -> None:
        global SESSION_STATE
        parsed = urlparse(self.path)
        route = parsed.path

        if route == "/api/audit/dashboard":
            body = self._read_json()
            opts_ov = {
                "startDate": body.get("startDate") or body.get("dt_inicio") or None,
                "endDate": body.get("endDate") or body.get("dt_fim") or None,
                "dateField": "authDate" if body.get("dateField") == "authDate" else "issueDate",
            }
            SESSION_STATE["reconciliation"]["options"].update(opts_ov)
            dash = _compute_current_audit_dashboard(opts_ov)
            self._send_json({"success": True, "auditDashboard": dash, "dbOverview": db_manager.get_database_overview()})
            return

        if route == "/api/db/connect-clipp":
            body = self._read_json()
            fdb_path = str(body.get("database") or body.get("fdbPath") or DEFAULT_CLIPP_FDB_PATH).strip()
            SESSION_STATE["firebirdData"]["host"] = str(body.get("host") or "127.0.0.1")
            SESSION_STATE["firebirdData"]["port"] = int(body.get("port") or 3050)
            SESSION_STATE["firebirdData"]["database"] = fdb_path
            SESSION_STATE["firebirdData"]["user"] = str(body.get("user") or "SYSDBA")

            sync_res = db_manager.sync_clipp_fdb_to_local(fdb_path)
            schema_res = db_manager.discover_schema({"database": fdb_path})
            dash = _compute_current_audit_dashboard()
            if not sync_res.get("ok"):
                self._send_json(
                    {
                        "success": bool(schema_res.get("success")),
                        "message": sync_res.get("error") or schema_res.get("message") or "Base conectada via espelho local.",
                        "syncStats": sync_res,
                        "auditDashboard": dash,
                        "dbOverview": db_manager.get_database_overview(),
                    }
                )
                return

            self._send_json(
                {
                    "success": True,
                    "message": (
                        f"Base Clipp ({fdb_path}) sincronizada: {sync_res['totalSalesSynced']} vendas "
                        f"({sync_res['authorizedCount']} autorizadas, {sync_res['dPlus1Count']} D+1, "
                        f"{sync_res['canceledCount']} canceladas 101, {sync_res['inutilizedCount']} inutilizadas 102)."
                    ),
                    "syncStats": sync_res,
                    "auditDashboard": dash,
                    "dbOverview": db_manager.get_database_overview(),
                }
            )
            return

        if route == "/api/xml/import-folder":
            body = self._read_json()
            folder_path = str(body.get("folderPath") or "").strip()
            ext = extract_xml_from_folder(folder_path)
            if ext["extractionErrors"] and not ext["xmlEntries"]:
                self._send_json({"success": False, "message": ext["extractionErrors"][0].get("message") or ext["extractionErrors"][0].get("error", "Erro")}, 400)
                return
            ana = analyze_extracted_xmls(ext["xmlEntries"])
            s = ana["summary"]
            if not SESSION_STATE["reconciliation"]["options"].get("startDate") and s.get("minIssueDate"):
                SESSION_STATE["reconciliation"]["options"]["startDate"] = s["minIssueDate"]
            if not SESSION_STATE["reconciliation"]["options"].get("endDate") and s.get("maxIssueDate"):
                SESSION_STATE["reconciliation"]["options"]["endDate"] = s["maxIssueDate"]
            SESSION_STATE["isDemoMode"] = False
            SESSION_STATE["xmlData"] = {
                "importedAt": datetime.now().isoformat(),
                "containerSummaries": ext["containerSummaries"],
                "extractionErrors": ext["extractionErrors"],
                "validationAlerts": ext["extractionErrors"] + ana["validationAlerts"],
                "documents": ana["documents"],
                "summary": s,
            }
            dash = _compute_current_audit_dashboard()
            self._send_json(
                {
                    "success": True,
                    "message": f"Lote importado: {len(ana['documents'])} XML(s) (55: {s['nfe55Count']} | 65: {s['nfce65Count']}).",
                    "xmlData": SESSION_STATE["xmlData"],
                    "auditDashboard": dash,
                    "reconciliationOptions": SESSION_STATE["reconciliation"]["options"],
                }
            )
            return

        if route == "/api/xml/upload":
            ct = self.headers.get("Content-Type", "")
            m = re.search(r"boundary=([^;]+)", ct)
            if not m:
                self._send_json({"success": False, "message": "Multipart boundary ausente."}, 400)
                return
            files = _parse_multipart(self._read_body(), m.group(1).strip('"'))
            extraction = extract_xml_containers(files)
            analysis = analyze_extracted_xmls(extraction["xmlEntries"])
            s = analysis["summary"]
            if not SESSION_STATE["reconciliation"]["options"].get("startDate") and s.get("minIssueDate"):
                SESSION_STATE["reconciliation"]["options"]["startDate"] = s["minIssueDate"]
            if not SESSION_STATE["reconciliation"]["options"].get("endDate") and s.get("maxIssueDate"):
                SESSION_STATE["reconciliation"]["options"]["endDate"] = s["maxIssueDate"]
            SESSION_STATE["isDemoMode"] = False
            SESSION_STATE["xmlData"] = {
                "importedAt": datetime.now().isoformat(),
                "containerSummaries": extraction["containerSummaries"],
                "extractionErrors": extraction["extractionErrors"],
                "validationAlerts": extraction["extractionErrors"] + analysis["validationAlerts"],
                "documents": analysis["documents"],
                "summary": s,
            }
            dash = _compute_current_audit_dashboard()
            self._send_json(
                {
                    "success": True,
                    "message": f"Processado em Python: {len(analysis['documents'])} documento(s) extraído(s) (55: {s['nfe55Count']} | 65: {s['nfce65Count']}).",
                    "xmlData": SESSION_STATE["xmlData"],
                    "auditDashboard": dash,
                    "reconciliationOptions": SESSION_STATE["reconciliation"]["options"],
                }
            )
            return

        if route == "/api/pdf/upload":
            ct = self.headers.get("Content-Type", "")
            m = re.search(r"boundary=([^;]+)", ct)
            if not m:
                self._send_json({"success": False, "message": "Multipart boundary ausente."}, 400)
                return
            files = _parse_multipart(self._read_body(), m.group(1).strip('"'))
            if not files:
                self._send_json({"success": False, "message": "Selecione um arquivo PDF."}, 400)
                return
            f_obj = files[0]
            parsed_pdf = parse_sales_pdf_bytes(f_obj["buffer"], f_obj["name"])
            SESSION_STATE["pdfData"] = {
                "importedAt": datetime.now().isoformat(),
                **parsed_pdf,
                "activeMapping": parsed_pdf.get("detectedMapping"),
                "lastBuffer": f_obj["buffer"],
            }
            pdf_pub = {k: v for k, v in SESSION_STATE["pdfData"].items() if k != "lastBuffer"}
            dash = _compute_current_audit_dashboard()
            self._send_json({"success": True, "message": "PDF processado com sucesso.", "pdfData": pdf_pub, "auditDashboard": dash})
            return

        if route == "/api/pdf/ocr":
            buf = SESSION_STATE["pdfData"].get("lastBuffer") or b""
            fn = SESSION_STATE["pdfData"].get("fileName") or "relatorio.pdf"
            ocr_res = perform_local_ocr_pdf_bytes(buf, fn)
            SESSION_STATE["pdfData"].update(ocr_res)
            pdf_pub = {k: v for k, v in SESSION_STATE["pdfData"].items() if k != "lastBuffer"}
            dash = _compute_current_audit_dashboard()
            self._send_json({"success": True, "message": "OCR local executado em Python.", "pdfData": pdf_pub, "auditDashboard": dash})
            return

        if route == "/api/firebird/test":
            body = self._read_json()
            cfg = body.get("config") or {}
            schema_res = db_manager.discover_schema(cfg)
            if not schema_res.get("success"):
                self._send_json(schema_res)
                return
            self._send_json(
                {
                    "success": True,
                    "engineVersion": schema_res["engineVersion"],
                    "versionFamily": schema_res["versionFamily"],
                    "odsVersion": schema_res["odsVersion"],
                    "driverUsed": f"Python 3.12 Local DB / Firebird {schema_res['versionFamily']} Adapter",
                    "readOnlyMode": True,
                    "message": f"Conectado com sucesso à Base Local / Firebird {schema_res['engineVersion']} (ODS {schema_res['odsVersion']}).",
                }
            )
            return

        if route == "/api/firebird/schema":
            body = self._read_json()
            cfg = body.get("config") or {}
            schema_res = db_manager.discover_schema(cfg)
            if schema_res.get("success"):
                SESSION_STATE["firebirdData"]["tables"] = schema_res["tables"]
            self._send_json(schema_res)
            return

        if route == "/api/firebird/preview":
            body = self._read_json()
            mapping = body.get("mapping") or SESSION_STATE["firebirdData"]["mapping"]
            res = db_manager.query_mapped_sales(mapping, limit=15)
            SESSION_STATE["firebirdData"]["enabled"] = True
            SESSION_STATE["firebirdData"]["mapping"] = mapping
            SESSION_STATE["firebirdData"]["previewSql"] = res["generatedSql"]
            SESSION_STATE["firebirdData"]["previewRecords"] = res["records"]
            self._send_json(
                {
                    "success": True,
                    "engineVersion": SESSION_STATE["firebirdData"]["engineVersion"],
                    "generatedSql": res["generatedSql"],
                    "records": res["records"],
                }
            )
            return

        if route == "/api/firebird/toggle":
            body = self._read_json()
            SESSION_STATE["firebirdData"]["enabled"] = bool(body.get("enabled"))
            self._send_json({"success": True, "enabled": SESSION_STATE["firebirdData"]["enabled"]})
            return

        if route == "/api/db/upsert":
            body = self._read_json()
            try:
                saved = db_manager.upsert_sale_record(body.get("record") or body)
                self._send_json(
                    {
                        "success": True,
                        "message": f"Documento Nº {saved.get('NUM_DOCUMENTO')} gravado na base local com sucesso.",
                        "record": saved,
                        "overview": db_manager.get_database_overview(),
                    }
                )
            except Exception as exc:
                self._send_json({"success": False, "message": str(exc)}, 400)
            return

        if route == "/api/db/delete":
            body = self._read_json()
            id_doc = int(body.get("idDoc") or 0)
            ok = db_manager.delete_sale_record(id_doc)
            self._send_json(
                {
                    "success": ok,
                    "message": "Registro removido da base local." if ok else "Registro não encontrado.",
                    "overview": db_manager.get_database_overview(),
                }
            )
            return

        if route == "/api/db/seed":
            count = db_manager.seed_default_sales_data()
            self._send_json(
                {
                    "success": True,
                    "message": f"Base local restaurada com {count} documentos padrão.",
                    "overview": db_manager.get_database_overview(),
                }
            )
            return

        if route == "/api/db/import-xmls":
            body = self._read_json()
            replace = bool(body.get("replaceExisting"))
            docs = SESSION_STATE["xmlData"].get("documents") or []
            stats = db_manager.import_xml_documents_into_local_db(docs, replace_existing=replace)
            self._send_json(
                {
                    "success": True,
                    "message": (
                        f"Importação para a Base Local concluída: {stats['inserted']} inserido(s), "
                        f"{stats['updated']} atualizado(s), {stats['skipped']} ignorado(s)."
                    ),
                    "stats": stats,
                    "overview": db_manager.get_database_overview(),
                }
            )
            return

        if route == "/api/db/sql":
            body = self._read_json()
            sql = str(body.get("sql") or "")
            try:
                res = db_manager.execute_read_only_sql(sql)
                self._send_json(res)
            except Exception as exc:
                self._send_json({"success": False, "message": str(exc)}, 400)
            return

        if route == "/api/reconcile":
            body = self._read_json()
            options = {
                "dateField": "authDate" if body.get("dateField") == "authDate" else "issueDate",
                "startDate": body.get("startDate") or body.get("dt_inicio") or SESSION_STATE["reconciliation"]["options"].get("startDate"),
                "endDate": body.get("endDate") or body.get("dt_fim") or SESSION_STATE["reconciliation"]["options"].get("endDate"),
                "comparePayment": body.get("comparePayment", True),
                "firebirdEnabled": bool(body.get("firebirdEnabled", SESSION_STATE["firebirdData"]["enabled"])),
            }
            fb_records = []
            if options["firebirdEnabled"]:
                q = db_manager.query_mapped_sales(SESSION_STATE["firebirdData"]["mapping"], limit=50000)
                fb_records = q["records"]
                SESSION_STATE["firebirdData"]["fullRecords"] = fb_records

            db_audit_data = db_manager.query_clipp_audit_dataset(
                dt_inicio=options["startDate"],
                dt_fim=options["endDate"],
                date_field=options["dateField"],
            )
            result = run_reconciliation(
                xml_documents=SESSION_STATE["xmlData"].get("documents") or [],
                pdf_records=SESSION_STATE["pdfData"].get("records") or [],
                firebird_records=fb_records,
                options=options,
                manual_decisions=SESSION_STATE["reconciliation"]["manualDecisions"],
                db_audit_data=db_audit_data,
            )
            SESSION_STATE["reconciliation"]["executedAt"] = result["executedAt"]
            SESSION_STATE["reconciliation"]["options"] = options
            SESSION_STATE["reconciliation"]["result"] = result
            self._send_json(
                {
                    "success": True,
                    "message": "Conciliação e Auditoria executadas pelo motor Python.",
                    "reconciliation": SESSION_STATE["reconciliation"],
                    "auditDashboard": result.get("auditDashboard"),
                }
            )
            return

        if route == "/api/reconcile/decision":
            body = self._read_json()
            item_id = body.get("itemId")
            action = body.get("action")
            if action == "CLEAR_DECISION":
                SESSION_STATE["reconciliation"]["manualDecisions"].pop(item_id, None)
            else:
                SESSION_STATE["reconciliation"]["manualDecisions"][item_id] = {
                    "itemId": item_id,
                    "action": action,
                    "targetCategory": body.get("targetCategory"),
                    "note": body.get("note", ""),
                    "decidedAt": datetime.now().isoformat(),
                }
            opts = SESSION_STATE["reconciliation"]["options"]
            db_audit_data = db_manager.query_clipp_audit_dataset(
                dt_inicio=opts.get("startDate"),
                dt_fim=opts.get("endDate"),
                date_field=opts.get("dateField", "issueDate"),
            )
            result = run_reconciliation(
                xml_documents=SESSION_STATE["xmlData"].get("documents") or [],
                pdf_records=SESSION_STATE["pdfData"].get("records") or [],
                firebird_records=SESSION_STATE["firebirdData"].get("fullRecords") or [],
                options=opts,
                manual_decisions=SESSION_STATE["reconciliation"]["manualDecisions"],
                db_audit_data=db_audit_data,
            )
            SESSION_STATE["reconciliation"]["result"] = result
            self._send_json(
                {
                    "success": True,
                    "message": "Decisão registrada.",
                    "reconciliation": SESSION_STATE["reconciliation"],
                    "auditDashboard": result.get("auditDashboard"),
                }
            )
            return

        if route.startswith("/api/export/"):
            fmt = route.split("/")[-1].lower()
            rec_res = SESSION_STATE["reconciliation"].get("result")
            audit_dash = _compute_current_audit_dashboard()
            if not rec_res:
                opts = SESSION_STATE["reconciliation"]["options"]
                fb_records = db_manager.query_mapped_sales(SESSION_STATE["firebirdData"]["mapping"], limit=50000)["records"]
                db_audit_data = db_manager.query_clipp_audit_dataset(
                    dt_inicio=opts.get("startDate"),
                    dt_fim=opts.get("endDate"),
                    date_field=opts.get("dateField", "issueDate"),
                )
                rec_res = run_reconciliation(
                    xml_documents=SESSION_STATE["xmlData"].get("documents") or [],
                    pdf_records=SESSION_STATE["pdfData"].get("records") or [],
                    firebird_records=fb_records,
                    options=opts,
                    manual_decisions=SESSION_STATE["reconciliation"]["manualDecisions"],
                    db_audit_data=db_audit_data,
                )
            items = rec_res["items"]
            summary = rec_res["summary"]
            if fmt == "csv":
                data_bytes = generate_csv_export_bytes(items, summary, {"executedAt": rec_res["executedAt"]}, audit_dash)
                self.send_response(200)
                self.send_header("Content-Type", "text/csv; charset=utf-8")
                self.send_header("Content-Disposition", 'attachment; filename="auditoria-fiscal-conferefiscal.csv"')
                self.send_header("Content-Length", str(len(data_bytes)))
                self.end_headers()
                self.wfile.write(data_bytes)
                return
            if fmt in ("xlsx", "excel"):
                data_bytes = generate_xlsx_export_bytes(items, summary, audit_dash)
                self.send_response(200)
                self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                self.send_header("Content-Disposition", 'attachment; filename="auditoria-fiscal-conferefiscal.xlsx"')
                self.send_header("Content-Length", str(len(data_bytes)))
                self.end_headers()
                self.wfile.write(data_bytes)
                return
            if fmt == "pdf":
                data_bytes = generate_pdf_export_bytes(items, summary, {"executedAt": rec_res["executedAt"]}, audit_dash)
                self.send_response(200)
                self.send_header("Content-Type", "application/pdf")
                self.send_header("Content-Disposition", 'attachment; filename="relatorio-auditoria-conferefiscal.pdf"')
                self.send_header("Content-Length", str(len(data_bytes)))
                self.end_headers()
                self.wfile.write(data_bytes)
                return

        if route == "/api/demo/activate":
            body = self._read_json()
            fb_ver = "5.0" if body.get("firebirdVersion") == "5.0" else "2.5"
            SESSION_STATE = _create_empty_session()
            SESSION_STATE["isDemoMode"] = True

            zip_path = EXAMPLES_DIR / "pacote_vendas_exemplo.zip"
            pdf_path = EXAMPLES_DIR / "relatorio_vendas_exemplo.pdf"
            if zip_path.exists():
                ext = extract_xml_containers([{"name": zip_path.name, "buffer": zip_path.read_bytes()}])
                ana = analyze_extracted_xmls(ext["xmlEntries"])
                SESSION_STATE["xmlData"] = {
                    "importedAt": datetime.now().isoformat(),
                    "containerSummaries": ext["containerSummaries"],
                    "extractionErrors": ext["extractionErrors"],
                    "validationAlerts": ext["extractionErrors"] + ana["validationAlerts"],
                    "documents": ana["documents"],
                    "summary": ana["summary"],
                }
            if pdf_path.exists():
                p_res = parse_sales_pdf_bytes(pdf_path.read_bytes(), pdf_path.name)
                SESSION_STATE["pdfData"] = {
                    "importedAt": datetime.now().isoformat(),
                    **p_res,
                    "activeMapping": p_res.get("detectedMapping"),
                    "lastBuffer": pdf_path.read_bytes(),
                }

            db_manager.seed_default_sales_data()
            q = db_manager.query_mapped_sales(SESSION_STATE["firebirdData"]["mapping"], limit=5000)
            schema_info = db_manager.discover_schema({"versionTarget": fb_ver})
            SESSION_STATE["firebirdData"].update(
                {
                    "enabled": True,
                    "engineVersion": f"{schema_info['engineVersion']} (Base Local Python)",
                    "versionFamily": fb_ver,
                    "odsVersion": schema_info["odsVersion"],
                    "tables": schema_info["tables"],
                    "previewSql": q["generatedSql"],
                    "previewRecords": q["records"][:15],
                    "fullRecords": q["records"],
                }
            )
            db_audit_data = db_manager.query_clipp_audit_dataset()
            rec = run_reconciliation(
                xml_documents=SESSION_STATE["xmlData"]["documents"],
                pdf_records=SESSION_STATE["pdfData"]["records"],
                firebird_records=q["records"],
                options={"dateField": "issueDate", "comparePayment": True, "firebirdEnabled": True},
                manual_decisions={},
                db_audit_data=db_audit_data,
            )
            SESSION_STATE["reconciliation"]["executedAt"] = rec["executedAt"]
            SESSION_STATE["reconciliation"]["result"] = rec

            pdf_pub = {k: v for k, v in SESSION_STATE["pdfData"].items() if k != "lastBuffer"}
            self._send_json(
                {
                    "success": True,
                    "message": f"Demonstração carregada com a Base Local Python (Firebird {fb_ver}).",
                    "isDemoMode": True,
                    "xmlData": SESSION_STATE["xmlData"],
                    "pdfData": pdf_pub,
                    "firebirdData": SESSION_STATE["firebirdData"],
                    "reconciliation": SESSION_STATE["reconciliation"],
                    "auditDashboard": rec.get("auditDashboard"),
                }
            )
            return

        if route == "/api/demo/exit":
            SESSION_STATE = _create_empty_session()
            self._send_json({"success": True, "isDemoMode": False, "message": "Demonstração encerrada."})
            return

        self._send_json({"success": False, "message": f"Endpoint POST não encontrado: {route}"}, 404)


def run_web_server(port: int = 5000) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", port), ConfereFiscalPythonHandler)
    print("===================================================================")
    print("  ConfereFiscal Python — Dashboard Web de Auditoria Fiscal Contábil")
    print(f"  Base de dados local ativa: {db_manager.sqlite_path}")
    print(f"  Acesse no navegador: http://127.0.0.1:{port}")
    print("===================================================================")
    server.serve_forever()


if __name__ == "__main__":
    port_env = int(os.environ.get("PORT", "5000"))
    run_web_server(port_env)
