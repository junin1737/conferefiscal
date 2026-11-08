import unittest
from pathlib import Path

from python_app.database_manager import (
    LocalDatabaseManager,
    ROOT_DIR,
    inspect_fdb_ods_header,
    detect_local_firebird_installations,
    normalize_iso_date,
)
from python_app.fiscal_parsers import (
    extract_xml_containers,
    extract_xml_from_folder,
    analyze_extracted_xmls,
    parse_sales_pdf_bytes,
    perform_local_ocr_pdf_bytes,
)
from python_app.reconciliation_engine import (
    run_reconciliation,
    generate_csv_export_bytes,
    generate_xlsx_export_bytes,
    generate_pdf_export_bytes,
)


class TestConfereFiscalPythonApp(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.db = LocalDatabaseManager()
        cls.examples_dir = ROOT_DIR / "exemplos"

    def test_01_local_database_crud_and_seed(self) -> None:
        # 1. Restaura os 10 documentos padrão na base local
        seeded_count = self.db.seed_default_sales_data()
        self.assertEqual(seeded_count, 10)

        overview = self.db.get_database_overview()
        self.assertTrue(Path(overview["databasePath"]).exists())
        self.assertEqual(overview["totalDocuments"], 10)

        # 2. Testa inserção de um novo registro na base local
        inserted = self.db.upsert_sale_record(
            {
                "NUM_DOCUMENTO": "9901",
                "SERIE_DOC": "1",
                "MODELO_FISCAL": "65",
                "CHAVE_NFE": "35260312345678000195650010000099011000099019",
                "DT_EMISSAO": "2026-03-20T10:15:00",
                "DT_AUTORIZACAO": "2026-03-20T10:15:05",
                "VLR_TOTAL_NF": 345.67,
                "VLR_DESCONTO": 5.00,
                "DESC_MEIO_PAGTO": "PIX",
                "SITUACAO_DOC": "A",
                "CPF_CNPJ_DEST": "12345678909",
                "OBSERVACOES": "Registro inserido pelo teste automatizado Python",
            }
        )
        self.assertEqual(int(inserted["NUM_DOCUMENTO"]), 9901)
        self.assertAlmostEqual(float(inserted["VLR_TOTAL_NF"]), 345.67, places=2)

        # 3. Testa edição do registro recém-criado
        updated = self.db.upsert_sale_record(
            {
                "ID_DOC": inserted["ID_DOC"],
                "NUM_DOCUMENTO": "9901",
                "SERIE_DOC": "1",
                "MODELO_FISCAL": "65",
                "CHAVE_NFE": "35260312345678000195650010000099011000099019",
                "DT_EMISSAO": "2026-03-20T10:15:00",
                "DT_AUTORIZACAO": "2026-03-20T10:15:05",
                "VLR_TOTAL_NF": 400.00,
                "DESC_MEIO_PAGTO": "Cartão de Crédito",
                "SITUACAO_DOC": "A",
            }
        )
        self.assertAlmostEqual(float(updated["VLR_TOTAL_NF"]), 400.00, places=2)
        self.assertEqual(updated["DESC_MEIO_PAGTO"], "Cartão de Crédito")

        # 4. Testa filtro de busca e exclusão do registro temporário
        found = self.db.list_sales_records({"search": "9901"})
        self.assertEqual(len(found), 1)

        deleted = self.db.delete_sale_record(int(inserted["ID_DOC"]))
        self.assertTrue(deleted)
        self.assertEqual(self.db.get_database_overview()["totalDocuments"], 10)

    def test_02_sql_console_and_firebird_ods_inspection(self) -> None:
        # Suporte à sintaxe Firebird SELECT FIRST N
        res = self.db.execute_read_only_sql(
            "SELECT FIRST 5 ID_DOC, NUM_DOCUMENTO, VLR_TOTAL_NF, SITUACAO_DOC FROM TB_DOC_FISCAL_SAIDA ORDER BY ID_DOC"
        )
        self.assertTrue(res["success"])
        self.assertEqual(res["rowCount"], 5)

        # Bloqueio de comandos destrutivos no console SQL
        with self.assertRaises(ValueError):
            self.db.execute_read_only_sql("DELETE FROM TB_DOC_FISCAL_SAIDA")

        # Inspeção binária de cabeçalho Firebird 2.5 (ODS 11.2) e Firebird 5.0 (ODS 13.1)
        fb25 = inspect_fdb_ods_header(str(self.examples_dir / "demo_fb25.fdb"))
        self.assertTrue(fb25["exists"])
        self.assertTrue(fb25["validHeader"])
        self.assertEqual(fb25["versionFamily"], "2.5")
        self.assertEqual(fb25["odsVersion"], "11.2")

        fb50 = inspect_fdb_ods_header(str(self.examples_dir / "demo_fb50.fdb"))
        self.assertTrue(fb50["exists"])
        self.assertTrue(fb50["validHeader"])
        self.assertEqual(fb50["versionFamily"], "5.0")
        self.assertEqual(fb50["odsVersion"], "13.1")

        # Verifica descoberta de instalações locais do Firebird
        installations = detect_local_firebird_installations()
        self.assertIsInstance(installations, list)

    def test_03_xml_zip_rar_and_db_import(self) -> None:
        zip_bytes = (self.examples_dir / "pacote_vendas_exemplo.zip").read_bytes()
        rar_bytes = (self.examples_dir / "pacote_vendas_exemplo.rar").read_bytes()

        ext_zip = extract_xml_containers([{"name": "pacote_vendas_exemplo.zip", "buffer": zip_bytes}])
        self.assertEqual(len(ext_zip["xmlEntries"]), 14)

        ext_rar = extract_xml_containers([{"name": "pacote_vendas_exemplo.rar", "buffer": rar_bytes}])
        self.assertGreaterEqual(len(ext_rar["xmlEntries"]), 13)

        ana = analyze_extracted_xmls(ext_zip["xmlEntries"])
        summary = ana["summary"]
        self.assertEqual(summary["authorizedSalesCount"], 8)
        self.assertEqual(summary["canceledCount"], 1)
        self.assertEqual(summary["deniedCount"], 1)
        self.assertEqual(summary["duplicateCount"], 1)
        self.assertEqual(summary["nonSaleCount"], 1)
        self.assertEqual(summary["invalidCount"], 1)
        self.assertEqual(summary["linkedCancelEventsCount"], 1)

        # Sincroniza os XMLs válidos de venda para dentro da Base Local Python
        stats = self.db.import_xml_documents_into_local_db(ana["documents"], replace_existing=False)
        # O Doc 1010 (presente no XML e ausente no seed inicial) deve ser inserido na base local
        self.assertGreaterEqual(stats["inserted"], 1)
        # Restaura os 10 originais para o teste de conciliação de 3 vias
        self.db.seed_default_sales_data()

    def test_04_pdf_ocr_reconciliation_and_exports(self) -> None:
        zip_bytes = (self.examples_dir / "pacote_vendas_exemplo.zip").read_bytes()
        ext_zip = extract_xml_containers([{"name": "pacote_vendas_exemplo.zip", "buffer": zip_bytes}])
        xml_docs = analyze_extracted_xmls(ext_zip["xmlEntries"])["documents"]

        pdf_bytes = (self.examples_dir / "relatorio_vendas_exemplo.pdf").read_bytes()
        pdf_res = parse_sales_pdf_bytes(pdf_bytes, "relatorio_vendas_exemplo.pdf")
        self.assertFalse(pdf_res["isScannedImagePdf"])
        self.assertEqual(len(pdf_res["records"]), 9)

        ocr_bytes = (self.examples_dir / "relatorio_digitalizado_ocr.pdf").read_bytes()
        ocr_detect = parse_sales_pdf_bytes(ocr_bytes, "relatorio_digitalizado_ocr.pdf")
        self.assertTrue(ocr_detect["isScannedImagePdf"])
        ocr_done = perform_local_ocr_pdf_bytes(ocr_bytes, "relatorio_digitalizado_ocr.pdf")
        self.assertTrue(ocr_done["ocrExecuted"])
        self.assertEqual(len(ocr_done["records"]), 9)

        fb_records = self.db.query_mapped_sales(limit=5000)["records"]
        self.assertEqual(len(fb_records), 10)

        # Conciliação por Data de Emissão (issueDate)
        rec_issue = run_reconciliation(
            xml_documents=xml_docs,
            pdf_records=pdf_res["records"],
            firebird_records=fb_records,
            options={"dateField": "issueDate", "comparePayment": True, "firebirdEnabled": True},
        )
        cats_issue = {c["code"]: c["count"] for c in rec_issue["summary"]["categories"]}
        self.assertGreaterEqual(cats_issue["CONCILIADO"], 2)
        self.assertGreaterEqual(cats_issue["SOMENTE_XML"], 1)
        self.assertGreaterEqual(cats_issue["SOMENTE_PDF"], 1)
        self.assertGreaterEqual(cats_issue["DIVERGENCIA_DATA"], 1)
        self.assertGreaterEqual(cats_issue["DIVERGENCIA_VALOR"], 1)
        self.assertGreaterEqual(cats_issue["DIVERGENCIA_IDENTIFICACAO"], 1)
        self.assertGreaterEqual(cats_issue["CANCELADO_DENEGADO_INVALIDO"], 3)
        self.assertGreaterEqual(cats_issue["POSSIVEL_DUPLICIDADE"], 1)
        self.assertGreaterEqual(cats_issue["REVISAO_MANUAL"], 1)

        # Alterna para Data de Autorização (authDate) -> Doc 1004 passa a convergir na data
        rec_auth = run_reconciliation(
            xml_documents=xml_docs,
            pdf_records=pdf_res["records"],
            firebird_records=fb_records,
            options={"dateField": "authDate", "comparePayment": True, "firebirdEnabled": True},
        )
        doc1004_issue = next(i for i in rec_issue["items"] if i["documentNumber"] == "1004")
        doc1004_auth = next(i for i in rec_auth["items"] if i["documentNumber"] == "1004")
        self.assertEqual(doc1004_issue["category"], "DIVERGENCIA_DATA")
        self.assertEqual(doc1004_auth["category"], "CONCILIADO")

        # Exportações CSV, XLSX e PDF
        csv_b = generate_csv_export_bytes(
            rec_issue["items"], rec_issue["summary"], {"executedAt": rec_issue["executedAt"]}, rec_issue.get("auditDashboard")
        )
        xlsx_b = generate_xlsx_export_bytes(
            rec_issue["items"], rec_issue["summary"], rec_issue.get("auditDashboard")
        )
        pdf_b = generate_pdf_export_bytes(
            rec_issue["items"], rec_issue["summary"], {"executedAt": rec_issue["executedAt"]}, rec_issue.get("auditDashboard")
        )

        self.assertTrue(len(csv_b) > 500)
        self.assertEqual(xlsx_b[:2], b"PK")
        self.assertTrue(pdf_b.startswith(b"%PDF-1.4"))

        # Valida estrutura do auditDashboard (KPIs, Comparativo Triplo, CFOP/NCM, 101/102 e Matriz de Divergências)
        ad = rec_issue["auditDashboard"]
        self.assertIn("kpiCards", ad)
        self.assertIn("tripleComparison", ad)
        self.assertIn("fiscalGroupings", ad)
        self.assertIn("separateEvents", ad)
        self.assertIn("divergenceMatrix", ad)
        self.assertGreater(len(ad["divergenceMatrix"]), 0)
        first_div = ad["divergenceMatrix"][0]
        for mandatory_col in ("nf_numero", "id_nfe", "dt_emissao", "data_hora_sefaz", "status_des", "valor", "origem_falha"):
            self.assertIn(mandatory_col, first_div)

    def test_05_inutilizacao_102_xml_and_clipp_audit_dataset(self) -> None:
        inut_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
        <procInutNFe versao="4.00" xmlns="http://www.portalfiscal.inf.br/nfe">
          <inutNFe versao="4.00">
            <infInut Id="ID35261234567800019565001000001050000001055">
              <tpAmb>1</tpAmb>
              <xServ>INUTILIZAR</xServ>
              <cUF>35</cUF>
              <ano>26</ano>
              <CNPJ>12345678000195</CNPJ>
              <mod>65</mod>
              <serie>1</serie>
              <nNFIni>1050</nNFIni>
              <nNFFin>1055</nNFFin>
              <xJust>Quebra de sequencia na numeracao do PDV</xJust>
            </infInut>
          </inutNFe>
          <retInutNFe versao="4.00">
            <infInut>
              <cStat>102</cStat>
              <xMotivo>Inutilizacao de numero homologado</xMotivo>
              <dhRecbto>2026-10-16T18:00:00-03:00</dhRecbto>
              <nProt>135260009999999</nProt>
            </infInut>
          </retInutNFe>
        </procInutNFe>"""
        ext = extract_xml_containers([{"name": "inut_1050_1055.xml", "buffer": inut_xml}])
        ana = analyze_extracted_xmls(ext["xmlEntries"])
        self.assertEqual(ana["summary"]["inutilizedCount"], 1)
        doc_inut = ana["documents"][0]
        self.assertEqual(doc_inut["status"], "INUTILIZADO")
        self.assertEqual(doc_inut["cStat"], "102")

        audit_ds = self.db.query_clipp_audit_dataset()
        self.assertIn("totalBancoEmissao", audit_ds)
        self.assertIn("totalBancoAutorizacao", audit_ds)
        self.assertIn("dPlus1Records", audit_ds)
        self.assertIn("canceledAndInutilized", audit_ds)

    def test_06_folder_extraction_and_br_date_normalization(self) -> None:
        # 1. Normalização de datas brasileiras e datas Firebird sem zero à esquerda
        self.assertEqual(normalize_iso_date("2026-2-07"), "2026-02-07")
        self.assertEqual(normalize_iso_date("2026-2-7"), "2026-02-07")
        self.assertEqual(normalize_iso_date("01/10/2026"), "2026-10-01")
        self.assertEqual(normalize_iso_date("31-10-2026"), "2026-10-31")

        # 2. Extração a partir de pasta contendo .zip/.rar e diretamente de arquivo .zip
        ext_folder = extract_xml_from_folder(str(self.examples_dir))
        self.assertIn("xmlEntries", ext_folder)
        self.assertGreaterEqual(len(ext_folder["xmlEntries"]), 14)

        ext_single_zip = extract_xml_from_folder(str(self.examples_dir / "pacote_vendas_exemplo.zip"))
        self.assertEqual(len(ext_single_zip["xmlEntries"]), 14)
        ana = analyze_extracted_xmls(ext_single_zip["xmlEntries"])
        self.assertIn("minIssueDate", ana["summary"])
        self.assertIn("maxIssueDate", ana["summary"])

    def test_07_desktop_app_initialization_and_mandatory_period_filter(self) -> None:
        import tkinter as tk
        from python_app.desktop_app import ConfereFiscalDesktopApp

        root = tk.Tk()
        root.withdraw()
        try:
            app = ConfereFiscalDesktopApp(root)
            root.update_idletasks()

            # 1. Ao abrir, valores zerados; importação sempre liberada (o período é preenchido pelos arquivos)
            self.assertFalse(app.period_filter_applied)
            self.assertTrue(app.btn_import_xml_files._enabled)
            self.assertTrue(app.btn_import_pdf._enabled)
            self.assertEqual(app.kpi_bco_emi_val.cget("text"), "R$ 0,00")
            self.assertEqual(app.kpi_bco_aut_val.cget("text"), "R$ 0,00")
            self.assertEqual(app.kpi_xml_val.cget("text"), "R$ 0,00")
            self.assertEqual(app.kpi_pdf_val.cget("text"), "R$ 0,00")

            # 2. Aplica filtro de período válido -> libera importação de XML e carrega valores reais do período
            app.var_dt_inicio.set("01/10/2026")
            app.var_dt_fim.set("31/10/2026")
            app.apply_period_filter()
            root.update_idletasks()

            self.assertTrue(app.period_filter_applied)
            self.assertTrue(app.btn_import_xml_files._enabled)
            self.assertTrue(app.btn_import_xml_folder._enabled)
            self.assertTrue(app.btn_import_pdf._enabled)
            self.assertEqual(app.applied_dt_inicio_iso, "2026-10-01")
            self.assertEqual(app.applied_dt_fim_iso, "2026-10-31")
            self.assertNotEqual(app.kpi_bco_emi_val.cget("text"), "R$ 0,00")
            self.assertNotEqual(app.kpi_bco_aut_val.cget("text"), "R$ 0,00")
            self.assertGreater(float(app.last_db_audit_data.get("totalBancoEmissao", 0.0)), 0.0)
            self.assertGreater(float(app.last_db_audit_data.get("totalBancoAutorizacao", 0.0)), 0.0)

            # 3. Alterna entre abas principais sem erros
            for tab_key in ("overview", "nfe55", "nfce65", "events", "divergences"):
                app.switch_main_tab(tab_key)
                root.update_idletasks()

            # 4. Limpa o período -> volta a R$ 0,00
            app.clear_period_filter()
            root.update_idletasks()
            self.assertFalse(app.period_filter_applied)
            self.assertEqual(app.kpi_bco_emi_val.cget("text"), "R$ 0,00")
        finally:
            root.destroy()


    def test_08_pdf_relatorio_com_fonte_identity_h(self) -> None:
        """Relatório no formato do Clipp: texto em hexadecimal (Identity-H) com tabela ToUnicode."""
        import zlib
        from python_app.fiscal_parsers import parse_sales_pdf_bytes

        def hx(text: str) -> str:  # glyph = código do caractere - 29 (como no Arial do Clipp)
            return "<" + "".join(f"{ord(c) - 29:04X}" for c in text) + ">"

        rows = [
            ["NF Número", "Série", "Mod", "Data", "CFOP", "Cliente", "Total Nota", "ICMS"],
            ["13700", "1", "65", "01/10/2026", "5102", "CLIENTES DIVERSOS", "56,590", "0,000"],
            ["13701", "1", "65", "02/10/2026", "5102", "CLIENTES DIVERSOS", "1.023,840", "0,000"],
            ["Total de documentos:", "2", "Totais:", "1.080,430"],
        ]
        xs = [30, 80, 110, 140, 200, 240, 450, 520]
        ops = []
        y = 780
        for r in rows:
            for x, cell in zip(xs, r):
                ops.append(f"BT /F1 8 Tf {x} {y} Td {hx(cell)} Tj ET")
            y -= 12
        content = zlib.compress("\n".join(ops).encode("latin1"))
        chars = sorted({c for r in rows for cell in r for c in cell})
        cmap = ("1 begincodespacerange <0000> <FFFF> endcodespacerange\n" + f"{len(chars)} beginbfchar\n"
                + "".join(f"<{ord(c) - 29:04X}> <{ord(c):04X}>\n" for c in chars) + "endbfchar")
        cmap_b = cmap.encode("latin1")
        objs = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
            b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(content) + content + b"\nendstream",
            b"<< /Type /Font /Subtype /Type0 /BaseFont /Arial /Encoding /Identity-H /ToUnicode 6 0 R >>",
            b"<< /Length %d >>\nstream\n" % len(cmap_b) + cmap_b + b"\nendstream",
        ]
        pdf = b"%PDF-1.7\n" + b"".join(b"%d 0 obj\n" % (i + 1) + o + b"\nendobj\n" for i, o in enumerate(objs))
        pdf += b"trailer\n<< /Root 1 0 R >>\n%%EOF"

        res = parse_sales_pdf_bytes(pdf, "relatorio_clipp.pdf")
        self.assertFalse(res["isScannedImagePdf"])
        self.assertEqual(len(res["records"]), 2)
        self.assertEqual(res["records"][0]["number"], "13700")
        self.assertEqual(res["records"][0]["issueDate"], "2026-10-01")
        self.assertAlmostEqual(res["records"][1]["totalValue"], 1023.84)
        self.assertEqual(res["records"][0]["customer"], "CLIENTES DIVERSOS")
        self.assertEqual(len(res["reportChecks"]), 2)
        self.assertTrue(all(c["ok"] for c in res["reportChecks"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
