const assert = require('assert');
const fs = require('fs');
const crypto = require('crypto');
const { generateAllSamples } = require('./generate-samples');
const { processUploadedXmlContainers } = require('../src/services/archiveService');
const { analyzeExtractedXmls } = require('../src/services/xmlParserService');
const {
  parseSalesPdfBuffer,
  performLocalOcrOnPdfBuffer
} = require('../src/services/pdfParserService');
const {
  assertReadOnlySql,
  inspectLocalFdbHeader,
  testFirebirdConnection,
  discoverFirebirdSchema,
  queryMappedFirebirdSales
} = require('../src/services/firebirdService');
const { runReconciliation } = require('../src/services/reconciliationService');
const {
  generateCsvExport,
  generateExcelExport,
  generatePdfReportExport
} = require('../src/services/exportService');
const {
  saveFirebirdConfig,
  getFirebirdConfigPublic,
  sanitizeLogMessage
} = require('../src/services/storageService');
const { app } = require('../src/server');

async function runFullValidation() {
  console.log('===================================================================');
  console.log(' INICIANDO VALIDAÇÃO END-TO-END DO FLUXO COMPLETO — CONFEREFISCAL');
  console.log('===================================================================\n');

  // 1. Geração de arquivos fictícios de teste
  const samplePaths = await generateAllSamples();
  console.log('[OK] 1. Arquivos fictícios de exemplo (.zip, .rar, .pdf, .fdb 2.5 e 5.0) gerados.');

  // Registra hashes originais para comprovar que os arquivos originais são preservados intocados
  const origZipHash = crypto.createHash('sha256').update(fs.readFileSync(samplePaths.zipPath)).digest('hex');
  const origRarHash = crypto.createHash('sha256').update(fs.readFileSync(samplePaths.rarPath)).digest('hex');
  const origPdfHash = crypto.createHash('sha256').update(fs.readFileSync(samplePaths.pdfPath)).digest('hex');

  // 2. Teste de Extração de .ZIP e .RAR com subpastas
  const zipExtraction = await processUploadedXmlContainers([
    { originalname: 'pacote_vendas_exemplo.zip', buffer: fs.readFileSync(samplePaths.zipPath) }
  ]);
  assert.strictEqual(zipExtraction.xmlEntries.length, 14, 'Deveria extrair 14 arquivos XML do ZIP (incluindo subpastas)');
  assert.strictEqual(zipExtraction.containerSummaries[0].ignoredNonXmlCount, 1, 'Deveria ignorar o arquivo .txt não-XML no ZIP');

  const rarExtraction = await processUploadedXmlContainers([
    { originalname: 'pacote_vendas_exemplo.rar', buffer: fs.readFileSync(samplePaths.rarPath) }
  ]);
  assert.strictEqual(rarExtraction.xmlEntries.length, 14, 'Deveria extrair 14 arquivos XML do RAR (incluindo subpastas)');
  console.log('[OK] 2. Extração recursiva de subpastas em pacotes .ZIP e .RAR validada (14 XMLs extraídos).');

  // 3. Leitura profunda dos XMLs
  const xmlAnalysis = analyzeExtractedXmls(zipExtraction.xmlEntries);
  assert.strictEqual(xmlAnalysis.summary.linkedCancelEventsCount, 1, 'Deveria vincular 1 evento de cancelamento (110111) à nota 1007');
  assert.strictEqual(xmlAnalysis.summary.duplicateCount, 1, 'Deveria detectar 1 arquivo XML duplicado (nota 1008)');
  assert.strictEqual(xmlAnalysis.summary.deniedCount, 1, 'Deveria detectar 1 nota denegada (2050)');
  assert.strictEqual(xmlAnalysis.summary.nonSaleCount, 1, 'Deveria detectar 1 nota de entrada não-venda (3090)');
  assert.strictEqual(xmlAnalysis.summary.invalidCount, 1, 'Deveria detectar 1 XML malformado/inválido sem interromper os demais');

  const doc1002 = xmlAnalysis.documents.find((d) => d.number === '1002');
  assert.ok(
    doc1002 && doc1002.missingFields.some((f) => /destinat[aá]rio/i.test(f)),
    'Deveria registrar claramente que o destinatário não está presente no XML 1002'
  );
  console.log('[OK] 3. Leitura dos XMLs (NF-e 55, NFC-e 65, evento 110111, denegados, não-venda, inválidos, duplicados e campos ausentes) validada.');

  // 4. Leitura do Relatório PDF (Texto Selecionável e PDF Digitalizado + OCR Local)
  const pdfParsed = await parseSalesPdfBuffer(fs.readFileSync(samplePaths.pdfPath), 'relatorio_vendas_exemplo.pdf');
  assert.strictEqual(pdfParsed.isScannedImagePdf, false, 'PDF com camada de texto não deve ser marcado como digitalizado');
  assert.strictEqual(pdfParsed.records.length, 9, 'Deveria extrair 9 registros de venda do PDF');

  const scannedParsed = await parseSalesPdfBuffer(
    fs.readFileSync(samplePaths.scannedPdfPath),
    'relatorio_digitalizado_ocr.pdf'
  );
  assert.strictEqual(scannedParsed.isScannedImagePdf, true, 'PDF sem texto selecionável deve ser detectado como digitalizado');
  const ocrParsed = await performLocalOcrOnPdfBuffer(
    fs.readFileSync(samplePaths.scannedPdfPath),
    'relatorio_digitalizado_ocr.pdf'
  );
  assert.strictEqual(ocrParsed.ocrExecuted, true, 'OCR local deve ser executado com sucesso');
  assert.strictEqual(ocrParsed.records.length, 9, 'OCR local deve recuperar os 9 registros do relatório digitalizado');
  console.log('[OK] 4. Leitura de PDF de vendas, detecção de PDF digitalizado como imagem e OCR Local validados.');

  // 5. Validação do Firebird 2.5 (ODS 11.2) e Firebird 5.0 (ODS 13.1) + Segurança Read-Only e Criptografia
  const ods25 = inspectLocalFdbHeader(samplePaths.fb25Path);
  assert.strictEqual(ods25.odsString, '11.2');
  assert.strictEqual(ods25.versionFamily, '2.5');

  const ods50 = inspectLocalFdbHeader(samplePaths.fb50Path);
  assert.strictEqual(ods50.odsString, '13.1');
  assert.strictEqual(ods50.versionFamily, '5.0');

  const conn25 = await testFirebirdConnection({
    host: '127.0.0.1',
    port: 3050,
    database: samplePaths.fb25Path,
    user: 'SYSDBA',
    password: 'masterkey',
    versionTarget: 'auto'
  });
  assert.strictEqual(conn25.success, true);
  assert.strictEqual(conn25.versionFamily, '2.5');

  const conn50 = await testFirebirdConnection({
    host: '127.0.0.1',
    port: 3050,
    database: samplePaths.fb50Path,
    user: 'SYSDBA',
    password: 'masterkey',
    versionTarget: '5.0'
  });
  assert.strictEqual(conn50.success, true);
  assert.strictEqual(conn50.versionFamily, '5.0');

  // Testa incompatibilidade de ODS (tentar abrir banco 5.0 forçando driver 2.5)
  const connMismatch = await testFirebirdConnection({
    database: samplePaths.fb50Path,
    versionTarget: '2.5'
  });
  assert.strictEqual(connMismatch.success, false);
  assert.strictEqual(connMismatch.errorCode, 'ODS_INCOMPATIVEL');

  // Testa bloqueio de SQL não somente-leitura
  assert.throws(() => assertReadOnlySql('DELETE FROM TB_DOC_FISCAL_SAIDA'), /bloqueada/i);
  assert.throws(() => assertReadOnlySql('UPDATE TB_DOC_FISCAL_SAIDA SET VLR_TOTAL_NF = 0'), /bloqueada/i);
  assert.throws(() => assertReadOnlySql('SELECT * FROM TB_DOC_FISCAL_SAIDA; DROP TABLE TB_DOC_FISCAL_SAIDA'), /múltiplos comandos/i);

  // Testa criptografia de credenciais e sanitização de senha em logs
  saveFirebirdConfig({
    host: '127.0.0.1',
    port: 3050,
    database: samplePaths.fb50Path,
    user: 'SYSDBA',
    password: 'SuperSecretPassword99!'
  });
  const pubCfg = getFirebirdConfigPublic();
  assert.strictEqual(pubCfg.password, undefined, 'A senha nunca deve ser retornada na configuração pública');
  assert.strictEqual(pubCfg.hasSavedPassword, true);
  const scrubbed = sanitizeLogMessage('Erro ao autenticar password="SuperSecretPassword99!"', ['SuperSecretPassword99!']);
  assert.ok(!scrubbed.includes('SuperSecretPassword99!'), 'A senha nunca deve aparecer em texto puro nos logs');

  const schema50 = await discoverFirebirdSchema({ database: samplePaths.fb50Path });
  assert.strictEqual(schema50.success, true);
  assert.ok(schema50.tables.length >= 2);

  const fbMapping = {
    tableName: 'TB_DOC_FISCAL_SAIDA',
    numberColumn: 'NUM_DOCUMENTO',
    seriesColumn: 'SERIE_DOC',
    modelColumn: 'MODELO_FISCAL',
    accessKeyColumn: 'CHAVE_NFE',
    issueDateColumn: 'DT_EMISSAO',
    authDateColumn: 'DT_AUTORIZACAO',
    totalValueColumn: 'VLR_TOTAL_NF',
    statusColumn: 'SITUACAO_DOC',
    paymentColumn: 'DESC_MEIO_PAGTO'
  };
  const fbSales = await queryMappedFirebirdSales({ database: samplePaths.fb50Path }, fbMapping, {
    previewOnly: false
  });
  assert.strictEqual(fbSales.success, true);
  assert.strictEqual(fbSales.records.length, 10);
  console.log('[OK] 5. Integração Firebird 2.5 (ODS 11.2) e 5.0 (ODS 13.1), descoberta de esquema, bloqueio READ-ONLY e proteção AES-256-GCM validados.');

  // 6. Validação da Conciliação (todas as 9 categorias + alternância Data de Emissão vs Data de Autorização)
  const recByIssueDate = runReconciliation({
    xmlDocuments: xmlAnalysis.documents,
    pdfRecords: pdfParsed.records,
    firebirdRecords: fbSales.records,
    options: { dateField: 'issueDate', comparePayment: true, firebirdEnabled: true },
    manualDecisions: {}
  });

  const getCatCount = (res, code) => res.summary.categories.find((c) => c.code === code)?.count || 0;
  assert.ok(getCatCount(recByIssueDate, 'CONCILIADO') >= 3, 'Deve haver itens conciliados (1001, 1002, 1008)');
  assert.strictEqual(getCatCount(recByIssueDate, 'SOMENTE_XML'), 1, 'Doc 1005 deve estar em SOMENTE_XML');
  assert.strictEqual(getCatCount(recByIssueDate, 'SOMENTE_PDF'), 2, 'Docs 1009 e 1011 devem estar em SOMENTE_PDF');
  assert.strictEqual(getCatCount(recByIssueDate, 'DIVERGENCIA_DATA'), 1, 'Doc 1004 deve ter DIVERGENCIA_DATA quando analisado por Data de Emissão');
  assert.strictEqual(getCatCount(recByIssueDate, 'DIVERGENCIA_VALOR'), 1, 'Doc 1003 deve ter DIVERGENCIA_VALOR');
  assert.strictEqual(getCatCount(recByIssueDate, 'DIVERGENCIA_IDENTIFICACAO'), 1, 'Doc 1006 deve ter DIVERGENCIA_IDENTIFICACAO (Série 1 vs Série 2)');
  assert.strictEqual(getCatCount(recByIssueDate, 'CANCELADO_DENEGADO_INVALIDO'), 4, 'Deve conter cancelado (1007), denegado (2050), entrada não-venda (3090) e XML inválido');
  assert.strictEqual(getCatCount(recByIssueDate, 'POSSIVEL_DUPLICIDADE'), 1, 'Cópia duplicada da 1008 deve estar em POSSIVEL_DUPLICIDADE');
  assert.strictEqual(getCatCount(recByIssueDate, 'REVISAO_MANUAL'), 1, 'Doc 1010 (candidato ambíguo com mesmo valor de 1011) deve ir para REVISAO_MANUAL sem vínculo silencioso');

  // Alterna para Data de Autorização (authDate) e verifica que o Doc 1004 passa para CONCILIADO!
  const recByAuthDate = runReconciliation({
    xmlDocuments: xmlAnalysis.documents,
    pdfRecords: pdfParsed.records,
    firebirdRecords: fbSales.records,
    options: { dateField: 'authDate', comparePayment: true, firebirdEnabled: true },
    manualDecisions: {}
  });
  assert.strictEqual(
    getCatCount(recByAuthDate, 'DIVERGENCIA_DATA'),
    0,
    'Ao alternar para Data de Autorização, o Doc 1004 emitido às 23:58 e autorizado às 00:02 passa a conciliar!'
  );
  console.log('[OK] 6. Motor de conciliação validado nas 9 categorias, incluindo alternância Emissão vs Autorização e bloqueio de vínculo aproximado silencioso.');

  // 7. Validação das Exportações (CSV, Excel .xlsx e Relatório PDF)
  const csvBuf = generateCsvExport(recByIssueDate.items, recByIssueDate.summary, {
    executedAt: recByIssueDate.executedAt,
    options: recByIssueDate.options
  });
  assert.ok(csvBuf.toString('utf8').includes('RELATÓRIO DE CONCILIAÇÃO DE VENDAS FISCAIS'));

  const xlsxBuf = await generateExcelExport(recByIssueDate.items, recByIssueDate.summary, {
    executedAt: recByIssueDate.executedAt,
    options: recByIssueDate.options
  });
  assert.ok(xlsxBuf.length > 500 && xlsxBuf[0] === 0x50 && xlsxBuf[1] === 0x4b, 'Arquivo XLSX deve ser um pacote OpenXML válido');

  const pdfRepBuf = await generatePdfReportExport(recByIssueDate.items, recByIssueDate.summary, {
    executedAt: recByIssueDate.executedAt,
    options: recByIssueDate.options
  });
  assert.ok(pdfRepBuf.subarray(0, 10).toString('latin1').includes('%PDF-'), 'Relatório exportado deve ser um PDF válido');
  console.log('[OK] 7. Exportação de relatórios para CSV, Excel (.xlsx) e PDF com metadados de auditoria validada.');

  // 8. Validação HTTP de ponta a ponta (Roteador e Endpoints REST da Aplicação Web)
  async function invokeAppRoute(method, urlPath, bodyObj = null) {
    const { Readable } = require('stream');
    const bodyBuf = bodyObj ? Buffer.from(JSON.stringify(bodyObj), 'utf8') : Buffer.alloc(0);
    const req = new Readable({
      read() {
        if (bodyBuf.length > 0) this.push(bodyBuf);
        this.push(null);
      }
    });
    req.method = method;
    req.url = urlPath;
    req.headers = bodyObj ? { 'content-type': 'application/json' } : {};

    return new Promise((resolve, reject) => {
      const headers = {};
      const chunks = [];
      const res = {
        statusCode: 200,
        writableEnded: false,
        setHeader(k, v) {
          headers[k.toLowerCase()] = v;
        },
        getHeader(k) {
          return headers[k.toLowerCase()];
        },
        end(data) {
          if (data) chunks.push(Buffer.isBuffer(data) ? data : Buffer.from(String(data), 'utf8'));
          this.writableEnded = true;
          resolve({
            status: this.statusCode,
            headers,
            buffer: Buffer.concat(chunks),
            text: () => Buffer.concat(chunks).toString('utf8'),
            json: () => JSON.parse(Buffer.concat(chunks).toString('utf8'))
          });
        }
      };
      Promise.resolve(app(req, res)).catch(reject);
    });
  }

  const resHtml = await invokeAppRoute('GET', '/');
  assert.strictEqual(resHtml.status, 200);
  assert.ok(resHtml.text().includes('ConfereFiscal'));

  const resDemo = await invokeAppRoute('POST', '/api/demo/activate', { firebirdVersion: '5.0' });
  const demoJson = resDemo.json();
  assert.strictEqual(demoJson.success, true);
  assert.strictEqual(demoJson.isDemoMode, true);
  assert.strictEqual(demoJson.firebirdData.versionFamily, '5.0');

  // Testa decisão manual do auditor via API (Confirmar Conciliação em item de Revisão Manual)
  const item1010 = demoJson.reconciliation.result.items.find((i) => i.documentNumber === '1010');
  assert.ok(item1010, 'Item 1010 deve existir na conciliação');
  assert.strictEqual(item1010.category, 'REVISAO_MANUAL');

  const resDecision = await invokeAppRoute('POST', '/api/reconcile/decision', {
    itemId: item1010.id,
    action: 'CONFIRM_CONCILIATION',
    targetCategory: 'CONCILIADO',
    note: 'Aprovado após conferência física do comprovante POS.'
  });
  const decisionJson = resDecision.json();
  assert.strictEqual(decisionJson.success, true);
  const updated1010 = decisionJson.reconciliation.result.items.find((i) => i.documentNumber === '1010');
  assert.strictEqual(updated1010.category, 'CONCILIADO');
  assert.strictEqual(updated1010.reviewStatus, 'CONFIRMADO_MANUALMENTE');

  // Testa endpoints de exportação CSV, XLSX e PDF via HTTP
  const expCsv = await invokeAppRoute('POST', '/api/export/csv', { filters: { category: 'ALL' } });
  assert.strictEqual(expCsv.status, 200);
  assert.ok(expCsv.text().includes('Aprovado após conferência física'));

  const expXlsx = await invokeAppRoute('POST', '/api/export/xlsx', { filters: { category: 'ALL' } });
  assert.strictEqual(expXlsx.status, 200);
  assert.ok(expXlsx.buffer.length > 500);

  const expPdf = await invokeAppRoute('POST', '/api/export/pdf', { filters: { category: 'ALL' } });
  assert.strictEqual(expPdf.status, 200);
  assert.ok(expPdf.buffer.subarray(0, 8).toString('latin1').includes('%PDF-'));

  await invokeAppRoute('POST', '/api/demo/exit', {});
  console.log('[OK] 8. Rotas HTTP da aplicação web (HTML, Demo 5.0, Decisão de Auditoria Manual e Exportações CSV/XLSX/PDF) validadas com sucesso.');

  // 9. Verifica que os arquivos originais não foram alterados nem excluídos
  assert.strictEqual(
    crypto.createHash('sha256').update(fs.readFileSync(samplePaths.zipPath)).digest('hex'),
    origZipHash,
    'Arquivo ZIP original deve permanecer intacto'
  );
  assert.strictEqual(
    crypto.createHash('sha256').update(fs.readFileSync(samplePaths.rarPath)).digest('hex'),
    origRarHash,
    'Arquivo RAR original deve permanecer intacto'
  );
  assert.strictEqual(
    crypto.createHash('sha256').update(fs.readFileSync(samplePaths.pdfPath)).digest('hex'),
    origPdfHash,
    'Arquivo PDF original deve permanecer intacto'
  );
  console.log('[OK] 9. Preservação integral dos arquivos originais comprovada por hash SHA-256.\n');
  console.log('===================================================================');
  console.log(' TODAS AS VALIDAÇÕES PASSARAM COM 100% DE SUCESSO!');
  console.log('===================================================================');
}

runFullValidation().catch((err) => {
  console.error('FALHA NA VALIDAÇÃO E2E:', err);
  process.exit(1);
});
