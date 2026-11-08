const path = require('path');
const { express, multer } = require('./lib/httpRouter');

const {
  SECURITY_LIMITS,
  saveFirebirdConfig,
  loadFirebirdConfigInternal,
  getFirebirdConfigPublic,
  validateUploadedBuffer,
  getStorageStatus,
  clearAllLocalData,
  logTechnicalEvent,
  logTechnicalError
} = require('./services/storageService');

const { processUploadedXmlContainers } = require('./services/archiveService');
const { analyzeExtractedXmls } = require('./services/xmlParserService');
const {
  parseSalesPdfBuffer,
  performLocalOcrOnPdfBuffer,
  buildPdfRecordsFromMapping,
  computePdfSummary
} = require('./services/pdfParserService');
const {
  testFirebirdConnection,
  discoverFirebirdSchema,
  queryMappedFirebirdSales
} = require('./services/firebirdService');
const { runReconciliation, CATEGORY_DEFINITIONS } = require('./services/reconciliationService');
const {
  generateCsvExport,
  generateExcelExport,
  generatePdfReportExport
} = require('./services/exportService');
const {
  buildSampleZipBuffer,
  buildSampleSalesPdfBuffer
} = require('./services/demoService');

const app = express();
app.use(express.json({ limit: '25mb' }));
app.use(express.static(path.join(__dirname, 'public')));

// Upload em memória para preservar os arquivos originais do usuário intocados
const upload = multer({
  storage: multer.memoryStorage(),
  limits: {
    fileSize: SECURITY_LIMITS.MAX_SINGLE_FILE_BYTES,
    files: 50
  }
});

/**
 * Estado da sessão local de auditoria (isolado entre modo real e modo demonstração).
 */
let sessionState = createEmptySessionState();

function createEmptySessionState() {
  return {
    isDemoMode: false,
    xmlData: {
      importedAt: null,
      containerSummaries: [],
      extractionErrors: [],
      validationAlerts: [],
      documents: [],
      summary: null
    },
    pdfData: {
      importedAt: null,
      fileName: null,
      sizeBytes: 0,
      sha256: null,
      pageCount: 0,
      lastBuffer: null,
      isScannedImagePdf: false,
      ocrExecuted: false,
      scannedWarning: null,
      rawTextPreview: '',
      extractionMethod: null,
      columnsMeta: [],
      detectedMapping: null,
      activeMapping: null,
      rawRows: [],
      records: [],
      summary: null
    },
    firebirdData: {
      enabled: false,
      connectionTested: false,
      engineVersion: null,
      versionFamily: null,
      odsVersion: null,
      isDemoConnection: false,
      tables: [],
      mapping: null,
      previewSql: null,
      previewRecords: [],
      fullRecords: []
    },
    reconciliation: {
      executedAt: null,
      options: {
        dateField: 'issueDate',
        comparePayment: true,
        firebirdEnabled: false
      },
      manualDecisions: {},
      result: null
    }
  };
}

/**
 * Resolve a configuração de conexão Firebird combinando os parâmetros enviados com a senha criptografada salva localmente.
 */
function resolveFirebirdConfig(bodyConfig = {}) {
  const saved = loadFirebirdConfigInternal() || {};
  const password =
    bodyConfig.password !== undefined && bodyConfig.password !== ''
      ? bodyConfig.password
      : saved.password || '';

  return {
    host: bodyConfig.host || saved.host || '127.0.0.1',
    port: Number(bodyConfig.port || saved.port || 3050),
    database: bodyConfig.database !== undefined ? bodyConfig.database : saved.database || '',
    user: bodyConfig.user || saved.user || 'SYSDBA',
    password,
    charset: bodyConfig.charset || saved.charset || 'UTF8',
    role: bodyConfig.role !== undefined ? bodyConfig.role : saved.role || '',
    versionTarget: bodyConfig.versionTarget || saved.versionTarget || 'auto',
    useDemoEngine: Boolean(bodyConfig.useDemoEngine || sessionState.isDemoMode)
  };
}

/**
 * Filtra os itens da conciliação segundo os filtros ativos na interface/exportação.
 */
function filterReconciliationItems(items = [], filters = {}) {
  return items.filter((item) => {
    if (filters.category && filters.category !== 'ALL' && item.category !== filters.category) {
      return false;
    }
    if (filters.status && filters.status !== 'ALL' && item.status !== filters.status) {
      return false;
    }
    if (filters.series && String(filters.series).trim() !== '') {
      if (String(item.series) !== String(filters.series).trim()) return false;
    }
    if (filters.number && String(filters.number).trim() !== '') {
      if (!String(item.documentNumber).includes(String(filters.number).trim())) return false;
    }
    if (filters.payment && filters.payment !== 'ALL') {
      if (!String(item.paymentMethod || '').toLowerCase().includes(String(filters.payment).toLowerCase())) {
        return false;
      }
    }
    if (filters.dateFrom && item.referenceDate && item.referenceDate < filters.dateFrom) {
      return false;
    }
    if (filters.dateTo && item.referenceDate && item.referenceDate > filters.dateTo) {
      return false;
    }
    if (filters.search && String(filters.search).trim() !== '') {
      const q = String(filters.search).toLowerCase().trim();
      const hay = [
        item.documentNumber,
        item.series,
        item.accessKey,
        item.categoryLabel,
        item.paymentMethod,
        item.status,
        item.matchCriterion,
        ...(item.reasons || []),
        item.sources?.xml?.fileName || '',
        item.sources?.xml?.destCnpjCpf || '',
        item.sources?.xml?.destName || ''
      ]
        .join(' ')
        .toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
}

// ============================================================================
// ROTAS DE STATUS E SEGURANÇA LOCAL
// ============================================================================

app.get('/api/status', (req, res) => {
  const { lastBuffer, ...publicPdfData } = sessionState.pdfData;
  res.json({
    isDemoMode: sessionState.isDemoMode,
    xmlData: sessionState.xmlData,
    pdfData: publicPdfData,
    firebirdData: sessionState.firebirdData,
    firebirdSavedConfig: getFirebirdConfigPublic(),
    reconciliation: sessionState.reconciliation,
    categoryDefinitions: CATEGORY_DEFINITIONS,
    storage: getStorageStatus()
  });
});

app.get('/api/storage', (req, res) => {
  res.json(getStorageStatus());
});

app.post('/api/storage/clear', (req, res) => {
  const keepConfig = Boolean(req.body && req.body.keepConfig);
  sessionState = createEmptySessionState();
  const status = clearAllLocalData({ keepConfig });
  res.json({
    success: true,
    message: 'Todos os arquivos temporários, sessões em memória e registros locais foram removidos com segurança.',
    storage: status
  });
});

// ============================================================================
// ETAPA 1: IMPORTAÇÃO E LEITURA DE XMLs (.ZIP, .RAR, .XML)
// ============================================================================

app.post('/api/xml/upload', upload.array('files', 50), async (req, res) => {
  try {
    if (!req.files || req.files.length === 0) {
      return res.status(400).json({
        success: false,
        message: 'Selecione ao menos um arquivo .zip, .rar ou .xml para importar.'
      });
    }

    // Se estava em modo demonstração e o usuário enviou arquivos reais, desativa o modo demo para nunca misturar dados!
    if (sessionState.isDemoMode) {
      sessionState = createEmptySessionState();
    }

    const extraction = await processUploadedXmlContainers(req.files);
    const analysis = analyzeExtractedXmls(extraction.xmlEntries);

    sessionState.xmlData = {
      importedAt: new Date().toISOString(),
      containerSummaries: extraction.containerSummaries,
      extractionErrors: extraction.extractionErrors,
      validationAlerts: [...extraction.extractionErrors, ...analysis.validationAlerts],
      documents: analysis.documents,
      summary: analysis.summary
    };

    res.json({
      success: true,
      message: `Processamento concluído: ${analysis.documents.length} documento(s) analisado(s) a partir de ${req.files.length} arquivo(s) importado(s).`,
      xmlData: sessionState.xmlData
    });
  } catch (err) {
    logTechnicalError('POST /api/xml/upload', err);
    res.status(500).json({
      success: false,
      message: `Erro ao processar os arquivos XML/compactados: ${err.message}`
    });
  }
});

// ============================================================================
// ETAPA 2 & 3: IMPORTAÇÃO DO RELATÓRIO PDF, OCR E MAPEAMENTO DE COLUNAS
// ============================================================================

app.post('/api/pdf/upload', upload.single('file'), async (req, res) => {
  try {
    if (!req.file) {
      return res.status(400).json({
        success: false,
        message: 'Selecione um arquivo PDF de relatório de vendas.'
      });
    }

    if (sessionState.isDemoMode) {
      sessionState = createEmptySessionState();
    }

    const validation = validateUploadedBuffer(req.file.buffer, req.file.originalname);
    if (!validation.valid || validation.detectedType !== 'pdf') {
      return res.status(400).json({
        success: false,
        message: validation.reason || 'O arquivo enviado não é um PDF válido.'
      });
    }

    const parsedPdf = await parseSalesPdfBuffer(req.file.buffer, req.file.originalname);

    sessionState.pdfData = {
      importedAt: new Date().toISOString(),
      ...parsedPdf,
      activeMapping: parsedPdf.detectedMapping,
      lastBuffer: req.file.buffer
    };

    const { lastBuffer, ...publicPdfData } = sessionState.pdfData;
    res.json({
      success: true,
      message: parsedPdf.isScannedImagePdf
        ? 'PDF digitalizado como imagem detectado. Execute o OCR Local para extrair os registros.'
        : `Relatório PDF processado: ${parsedPdf.records.length} registro(s) extraído(s).`,
      pdfData: publicPdfData
    });
  } catch (err) {
    logTechnicalError('POST /api/pdf/upload', err);
    res.status(500).json({
      success: false,
      message: `Erro ao processar o relatório PDF: ${err.message}`
    });
  }
});

app.post('/api/pdf/ocr', async (req, res) => {
  try {
    if (!sessionState.pdfData.lastBuffer) {
      return res.status(400).json({
        success: false,
        message: 'Nenhum arquivo PDF carregado na sessão para execução de OCR.'
      });
    }

    const ocrResult = await performLocalOcrOnPdfBuffer(
      sessionState.pdfData.lastBuffer,
      sessionState.pdfData.fileName || 'relatorio.pdf'
    );

    sessionState.pdfData = {
      ...sessionState.pdfData,
      ...ocrResult,
      activeMapping: ocrResult.detectedMapping
    };

    const { lastBuffer, ...publicPdfData } = sessionState.pdfData;
    res.json({
      success: true,
      message: `OCR local concluído: ${ocrResult.records.length} registro(s) extraído(s).`,
      pdfData: publicPdfData
    });
  } catch (err) {
    logTechnicalError('POST /api/pdf/ocr', err);
    res.status(500).json({
      success: false,
      message: `Falha ao executar OCR local no PDF: ${err.message}`
    });
  }
});

app.post('/api/pdf/remap', (req, res) => {
  try {
    const newMapping = req.body.mapping;
    if (!newMapping) {
      return res.status(400).json({
        success: false,
        message: 'Informe o mapeamento de colunas desejado.'
      });
    }

    const rawRows = sessionState.pdfData.rawRows || [];
    const records = buildPdfRecordsFromMapping(rawRows, newMapping);
    const summary = computePdfSummary(records, sessionState.pdfData.pageCount || 1);

    sessionState.pdfData.activeMapping = newMapping;
    sessionState.pdfData.records = records;
    sessionState.pdfData.summary = summary;

    const { lastBuffer, ...publicPdfData } = sessionState.pdfData;
    res.json({
      success: true,
      message: `Mapeamento de colunas aplicado: ${records.length} registro(s) estruturado(s).`,
      pdfData: publicPdfData
    });
  } catch (err) {
    res.status(500).json({
      success: false,
      message: `Erro ao aplicar mapeamento de colunas: ${err.message}`
    });
  }
});

app.put('/api/pdf/records', (req, res) => {
  try {
    const updatedRecords = Array.isArray(req.body.records) ? req.body.records : [];
    const sanitized = updatedRecords.map((r, idx) => ({
      id: r.id || `pdf_row_${idx + 1}`,
      lineNumber: idx + 1,
      issueDate: r.issueDate || null,
      authDate: r.authDate || r.issueDate || null,
      number: String(r.number || '').trim(),
      series: String(r.series || '1').trim(),
      model: String(r.model || '').trim(),
      accessKey: String(r.accessKey || '').replace(/\D/g, ''),
      totalValue: Number(Number(r.totalValue || 0).toFixed(2)),
      paymentMethod: String(r.paymentMethod || 'Não informado').trim(),
      status: String(r.status || 'AUTORIZADO').toUpperCase().trim(),
      customerDoc: String(r.customerDoc || '').trim(),
      manuallyEdited: Boolean(r.manuallyEdited)
    }));

    sessionState.pdfData.records = sanitized;
    sessionState.pdfData.summary = computePdfSummary(sanitized, sessionState.pdfData.pageCount || 1);

    const { lastBuffer, ...publicPdfData } = sessionState.pdfData;
    res.json({
      success: true,
      message: `Prévia do PDF atualizada com sucesso (${sanitized.length} registro(s)).`,
      pdfData: publicPdfData
    });
  } catch (err) {
    res.status(500).json({
      success: false,
      message: `Erro ao atualizar registros do PDF: ${err.message}`
    });
  }
});

// ============================================================================
// ETAPA 4: INTEGRAÇÃO COM FIREBIRD (2.5 e 5.0)
// ============================================================================

app.post('/api/firebird/test', async (req, res) => {
  try {
    const config = resolveFirebirdConfig(req.body.config || {});
    if (req.body.saveConfig) {
      saveFirebirdConfig(config);
    }

    const result = await testFirebirdConnection(config);
    if (result.success) {
      sessionState.firebirdData.connectionTested = true;
      sessionState.firebirdData.engineVersion = result.engineVersion;
      sessionState.firebirdData.versionFamily = result.versionFamily;
      sessionState.firebirdData.odsVersion = result.odsVersion;
      sessionState.firebirdData.isDemoConnection = Boolean(result.isDemoConnection);
    }

    res.json({
      ...result,
      savedConfig: getFirebirdConfigPublic()
    });
  } catch (err) {
    logTechnicalError('POST /api/firebird/test', err);
    res.status(500).json({
      success: false,
      userMessage: `Erro ao testar conexão com o Firebird: ${err.message}`
    });
  }
});

app.post('/api/firebird/schema', async (req, res) => {
  try {
    const config = resolveFirebirdConfig(req.body.config || {});
    const schemaRes = await discoverFirebirdSchema(config);
    if (schemaRes.success) {
      sessionState.firebirdData.connectionTested = true;
      sessionState.firebirdData.engineVersion = schemaRes.engineVersion;
      sessionState.firebirdData.versionFamily = schemaRes.versionFamily;
      sessionState.firebirdData.odsVersion = schemaRes.odsVersion;
      sessionState.firebirdData.tables = schemaRes.tables;
    }
    res.json(schemaRes);
  } catch (err) {
    logTechnicalError('POST /api/firebird/schema', err);
    res.status(500).json({
      success: false,
      userMessage: `Erro ao descobrir esquema do Firebird: ${err.message}`
    });
  }
});

app.post('/api/firebird/preview', async (req, res) => {
  try {
    const config = resolveFirebirdConfig(req.body.config || {});
    const mapping = req.body.mapping || {};
    const enabled = req.body.enabled !== false;

    const previewRes = await queryMappedFirebirdSales(config, mapping, { previewOnly: true });
    if (previewRes.success) {
      sessionState.firebirdData.enabled = enabled;
      sessionState.firebirdData.mapping = mapping;
      sessionState.firebirdData.previewSql = previewRes.generatedSql;
      sessionState.firebirdData.previewRecords = previewRes.records;
      sessionState.firebirdData.engineVersion = previewRes.engineVersion;
    }

    res.json(previewRes);
  } catch (err) {
    logTechnicalError('POST /api/firebird/preview', err);
    res.status(500).json({
      success: false,
      userMessage: `Erro ao executar prévia do mapeamento no Firebird: ${err.message}`
    });
  }
});

app.post('/api/firebird/toggle', (req, res) => {
  sessionState.firebirdData.enabled = Boolean(req.body.enabled);
  res.json({
    success: true,
    enabled: sessionState.firebirdData.enabled
  });
});

// ============================================================================
// ETAPA 5 & 6: CONCILIAÇÃO E REVISÃO DE DIVERGÊNCIAS
// ============================================================================

app.post('/api/reconcile', async (req, res) => {
  try {
    const options = {
      dateField: req.body.dateField === 'authDate' ? 'authDate' : 'issueDate',
      comparePayment: req.body.comparePayment !== false,
      firebirdEnabled: Boolean(
        req.body.firebirdEnabled !== undefined
          ? req.body.firebirdEnabled
          : sessionState.firebirdData.enabled
      )
    };

    let fbRecords = sessionState.firebirdData.fullRecords || [];
    if (options.firebirdEnabled && sessionState.firebirdData.mapping) {
      const config = resolveFirebirdConfig(req.body.firebirdConfig || {});
      const fbQuery = await queryMappedFirebirdSales(config, sessionState.firebirdData.mapping, {
        previewOnly: false
      });
      if (fbQuery.success) {
        fbRecords = fbQuery.records;
        sessionState.firebirdData.fullRecords = fbRecords;
      }
    }

    const result = runReconciliation({
      xmlDocuments: sessionState.xmlData.documents || [],
      pdfRecords: sessionState.pdfData.records || [],
      firebirdRecords: fbRecords,
      options,
      manualDecisions: sessionState.reconciliation.manualDecisions || {}
    });

    sessionState.reconciliation.executedAt = result.executedAt;
    sessionState.reconciliation.options = options;
    sessionState.reconciliation.result = result;

    res.json({
      success: true,
      message: 'Conciliação executada com sucesso.',
      reconciliation: sessionState.reconciliation
    });
  } catch (err) {
    logTechnicalError('POST /api/reconcile', err);
    res.status(500).json({
      success: false,
      message: `Erro ao executar a conciliação: ${err.message}`
    });
  }
});

app.post('/api/reconcile/decision', (req, res) => {
  try {
    const { itemId, action, targetCategory, note } = req.body;
    if (!itemId || !action) {
      return res.status(400).json({
        success: false,
        message: 'Informe o item e a decisão de auditoria.'
      });
    }

    if (action === 'CLEAR_DECISION') {
      delete sessionState.reconciliation.manualDecisions[itemId];
    } else {
      sessionState.reconciliation.manualDecisions[itemId] = {
        itemId,
        action,
        targetCategory: targetCategory || null,
        note: note ? String(note).trim() : '',
        decidedAt: new Date().toISOString()
      };
    }

    const result = runReconciliation({
      xmlDocuments: sessionState.xmlData.documents || [],
      pdfRecords: sessionState.pdfData.records || [],
      firebirdRecords: sessionState.firebirdData.fullRecords || [],
      options: sessionState.reconciliation.options,
      manualDecisions: sessionState.reconciliation.manualDecisions
    });

    sessionState.reconciliation.result = result;

    res.json({
      success: true,
      message: 'Decisão de revisão manual registrada.',
      reconciliation: sessionState.reconciliation
    });
  } catch (err) {
    res.status(500).json({
      success: false,
      message: `Erro ao registrar decisão manual: ${err.message}`
    });
  }
});

// ============================================================================
// ETAPA 7: EXPORTAÇÃO (CSV, EXCEL .XLSX, RELATÓRIO PDF)
// ============================================================================

app.post('/api/export/:format', async (req, res) => {
  try {
    const format = String(req.params.format || 'csv').toLowerCase();
    const recResult = sessionState.reconciliation.result;
    if (!recResult || !recResult.items) {
      return res.status(400).json({
        success: false,
        message: 'Execute a conciliação antes de exportar os resultados.'
      });
    }

    const activeFilters = req.body.filters || {};
    const filteredItems = filterReconciliationItems(recResult.items, activeFilters);

    const exportContext = {
      isDemoMode: sessionState.isDemoMode,
      executedAt: recResult.executedAt,
      options: recResult.options,
      activeFilters,
      xmlFiles: sessionState.xmlData.containerSummaries || [],
      pdfFile: sessionState.pdfData.fileName
        ? {
            fileName: sessionState.pdfData.fileName,
            totalRows: (sessionState.pdfData.records || []).length
          }
        : null,
      firebirdInfo: {
        enabled: recResult.options.firebirdEnabled,
        engineVersion: sessionState.firebirdData.engineVersion,
        tableName: sessionState.firebirdData.mapping?.tableName || null
      }
    };

    const timestampTag = new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-');

    if (format === 'csv') {
      const csvBuf = generateCsvExport(filteredItems, recResult.summary, exportContext);
      res.setHeader('Content-Type', 'text/csv; charset=utf-8');
      res.setHeader(
        'Content-Disposition',
        `attachment; filename="conciliacao-fiscal-${timestampTag}.csv"`
      );
      return res.send(csvBuf);
    }

    if (format === 'xlsx' || format === 'excel') {
      const xlsxBuf = await generateExcelExport(filteredItems, recResult.summary, exportContext);
      res.setHeader(
        'Content-Type',
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
      );
      res.setHeader(
        'Content-Disposition',
        `attachment; filename="conciliacao-fiscal-${timestampTag}.xlsx"`
      );
      return res.send(xlsxBuf);
    }

    if (format === 'pdf') {
      const pdfBuf = await generatePdfReportExport(filteredItems, recResult.summary, exportContext);
      res.setHeader('Content-Type', 'application/pdf');
      res.setHeader(
        'Content-Disposition',
        `attachment; filename="relatorio-conciliacao-${timestampTag}.pdf"`
      );
      return res.send(pdfBuf);
    }

    return res.status(400).json({
      success: false,
      message: `Formato de exportação não suportado: "${format}". Use csv, xlsx ou pdf.`
    });
  } catch (err) {
    logTechnicalError('POST /api/export', err);
    res.status(500).json({
      success: false,
      message: `Erro ao exportar relatório: ${err.message}`
    });
  }
});

// ============================================================================
// MODO DE DEMONSTRAÇÃO (DADOS DE EXEMPLO ISOLADOS)
// ============================================================================

app.post('/api/demo/activate', async (req, res) => {
  try {
    // Limpa estado anterior para jamais misturar dados reais com dados de demonstração
    sessionState = createEmptySessionState();
    sessionState.isDemoMode = true;

    const fbVersion = req.body && req.body.firebirdVersion === '5.0' ? '5.0' : '2.5';

    // 1. Gera e processa o pacote .ZIP de exemplo com subpastas
    const zipBuffer = buildSampleZipBuffer();
    const extraction = await processUploadedXmlContainers([
      {
        originalname: 'DEMO_pacote_vendas_outubro_2026.zip',
        buffer: zipBuffer
      }
    ]);
    const xmlAnalysis = analyzeExtractedXmls(extraction.xmlEntries);

    sessionState.xmlData = {
      importedAt: new Date().toISOString(),
      containerSummaries: extraction.containerSummaries,
      extractionErrors: extraction.extractionErrors,
      validationAlerts: [...extraction.extractionErrors, ...xmlAnalysis.validationAlerts],
      documents: xmlAnalysis.documents,
      summary: xmlAnalysis.summary
    };

    // 2. Gera e processa o relatório PDF de exemplo
    const pdfBuffer = await buildSampleSalesPdfBuffer();
    const parsedPdf = await parseSalesPdfBuffer(pdfBuffer, 'DEMO_relatorio_vendas_outubro_2026.pdf');

    sessionState.pdfData = {
      importedAt: new Date().toISOString(),
      ...parsedPdf,
      activeMapping: parsedPdf.detectedMapping,
      lastBuffer: pdfBuffer
    };

    // 3. Configura conexão e mapeamento de demonstração do Firebird (2.5 ou 5.0)
    const demoFbConfig = {
      host: '127.0.0.1',
      port: 3050,
      database: `C:\\Dados\\Demo\\VENDAS_FISCAIS_FB${fbVersion.replace('.', '')}.FDB`,
      user: 'SYSDBA',
      password: 'masterkey',
      charset: 'UTF8',
      versionTarget: fbVersion,
      useDemoEngine: true
    };
    const demoMapping = {
      tableName: 'TB_DOC_FISCAL_SAIDA',
      numberColumn: 'NUM_DOCUMENTO',
      seriesColumn: 'SERIE_DOC',
      modelColumn: 'MODELO_FISCAL',
      accessKeyColumn: 'CHAVE_NFE',
      issueDateColumn: 'DT_EMISSAO',
      authDateColumn: 'DT_AUTORIZACAO',
      totalValueColumn: 'VLR_TOTAL_NF',
      discountValueColumn: 'VLR_DESCONTO',
      additionValueColumn: 'VLR_ACRESCIMO',
      statusColumn: 'SITUACAO_DOC',
      paymentColumn: 'DESC_MEIO_PAGTO',
      customerColumn: 'CPF_CNPJ_DEST'
    };

    const schemaRes = await discoverFirebirdSchema(demoFbConfig);
    const previewRes = await queryMappedFirebirdSales(demoFbConfig, demoMapping, {
      previewOnly: false
    });

    sessionState.firebirdData = {
      enabled: true,
      connectionTested: true,
      engineVersion: schemaRes.engineVersion,
      versionFamily: schemaRes.versionFamily,
      odsVersion: schemaRes.odsVersion,
      isDemoConnection: true,
      tables: schemaRes.tables || [],
      mapping: demoMapping,
      previewSql: previewRes.generatedSql,
      previewRecords: (previewRes.records || []).slice(0, 15),
      fullRecords: previewRes.records || []
    };

    // 4. Executa conciliação inicial de demonstração
    const recOptions = {
      dateField: 'issueDate',
      comparePayment: true,
      firebirdEnabled: true
    };

    const recResult = runReconciliation({
      xmlDocuments: sessionState.xmlData.documents,
      pdfRecords: sessionState.pdfData.records,
      firebirdRecords: sessionState.firebirdData.fullRecords,
      options: recOptions,
      manualDecisions: {}
    });

    sessionState.reconciliation = {
      executedAt: recResult.executedAt,
      options: recOptions,
      manualDecisions: {},
      result: recResult
    };

    logTechnicalEvent('info', 'DemoMode', `Modo de demonstração ativado (Firebird ${fbVersion}).`);

    const { lastBuffer, ...publicPdfData } = sessionState.pdfData;
    res.json({
      success: true,
      message:
        'Modo de Demonstração ativado com dados fictícios de exemplo (XMLs em subpastas, Relatório PDF e Firebird simulado).',
      isDemoMode: true,
      xmlData: sessionState.xmlData,
      pdfData: publicPdfData,
      firebirdData: sessionState.firebirdData,
      reconciliation: sessionState.reconciliation
    });
  } catch (err) {
    logTechnicalError('POST /api/demo/activate', err);
    res.status(500).json({
      success: false,
      message: `Erro ao ativar modo de demonstração: ${err.message}`
    });
  }
});

app.post('/api/demo/exit', (req, res) => {
  sessionState = createEmptySessionState();
  logTechnicalEvent('info', 'DemoMode', 'Modo de demonstração encerrado; sessão limpa para dados reais.');
  res.json({
    success: true,
    message: 'Modo de demonstração desativado. Todos os dados fictícios foram removidos da sessão.',
    isDemoMode: false
  });
});

// Tratamento de erros do Multer (ex: arquivo maior que o limite)
app.use((err, req, res, next) => {
  if (err instanceof multer.MulterError) {
    return res.status(400).json({
      success: false,
      message: `Erro no envio do arquivo: ${
        err.code === 'LIMIT_FILE_SIZE'
          ? 'O tamanho do arquivo excede o limite de segurança de 150 MB.'
          : err.message
      }`
    });
  }
  if (err) {
    logTechnicalError('ExpressErrorMiddleware', err);
    return res.status(500).json({
      success: false,
      message: `Erro interno na operação local: ${err.message}`
    });
  }
  next();
});

const PORT = process.env.PORT || 3000;

if (require.main === module) {
  app.listen(PORT, '127.0.0.1', () => {
    console.log('===================================================================');
    console.log('  ConfereFiscal - Conciliação Local de Vendas Fiscais');
    console.log(`  Servidor local ativo em: http://127.0.0.1:${PORT}`);
    console.log('  Operação 100% local (sem envio de dados para serviços externos)');
    console.log('===================================================================');
  });
}

module.exports = { app };
