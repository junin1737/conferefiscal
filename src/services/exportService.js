const { ExcelJS } = require('../lib/xlsxCodec');
const { PDFDocument } = require('../lib/pdfCodec');

function formatCurrencyBR(value) {
  if (value === null || value === undefined || value === '') return '—';
  const num = Number(value);
  if (!Number.isFinite(num)) return '—';
  return `R$ ${num.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function formatDateBR(dateStr) {
  if (!dateStr) return '—';
  const m = String(dateStr).match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (m) return `${m[3]}/${m[2]}/${m[1]}`;
  return String(dateStr);
}

function buildAuditMetadataLines(exportContext = {}) {
  const analysisDate = exportContext.executedAt
    ? new Date(exportContext.executedAt).toLocaleString('pt-BR')
    : new Date().toLocaleString('pt-BR');
  const exportDate = new Date().toLocaleString('pt-BR');

  const xmlFilesList =
    exportContext.xmlFiles && exportContext.xmlFiles.length > 0
      ? exportContext.xmlFiles.map((f) => `${f.name} (${f.extractedCount || 1} XMLs)`).join('; ')
      : 'Nenhum pacote informado';

  const pdfFileName = exportContext.pdfFile
    ? `${exportContext.pdfFile.fileName} (${exportContext.pdfFile.totalRows || 0} registros)`
    : 'Nenhum relatório PDF informado';

  const firebirdDesc = exportContext.firebirdInfo?.enabled
    ? `Ativo (${exportContext.firebirdInfo.engineVersion || 'Firebird'} - Tabela: ${
        exportContext.firebirdInfo.tableName || 'Mapeada'
      })`
    : 'Desativado nesta análise';

  const criteriaDesc = [
    `Data de Referência: ${exportContext.options?.dateFieldLabel || 'Data de Emissão'}`,
    `Comparar Forma de Pagamento: ${exportContext.options?.comparePayment !== false ? 'Sim' : 'Não'}`,
    `Confronto com Firebird: ${exportContext.options?.firebirdEnabled ? 'Ativo' : 'Inativo'}`,
    'Política de Vínculo: Estrita (sem correspondências silenciosas por valor aproximado)'
  ].join(' | ');

  const f = exportContext.activeFilters || {};
  const activeFilterParts = [];
  if (f.category && f.category !== 'ALL') activeFilterParts.push(`Categoria=${f.category}`);
  if (f.status && f.status !== 'ALL') activeFilterParts.push(`Status=${f.status}`);
  if (f.dateFrom) activeFilterParts.push(`De=${formatDateBR(f.dateFrom)}`);
  if (f.dateTo) activeFilterParts.push(`Até=${formatDateBR(f.dateTo)}`);
  if (f.series) activeFilterParts.push(`Série=${f.series}`);
  if (f.number) activeFilterParts.push(`Número=${f.number}`);
  if (f.payment && f.payment !== 'ALL') activeFilterParts.push(`Pagamento=${f.payment}`);
  if (f.search) activeFilterParts.push(`Busca="${f.search}"`);

  const filtersDesc =
    activeFilterParts.length > 0 ? activeFilterParts.join('; ') : 'Todos os registros (sem filtros restritivos)';

  return {
    analysisDate,
    exportDate,
    xmlFilesList,
    pdfFileName,
    firebirdDesc,
    criteriaDesc,
    filtersDesc,
    isDemoMode: Boolean(exportContext.isDemoMode)
  };
}

/**
 * Gera conteúdo CSV (UTF-8 com BOM e separador ;) incluindo cabeçalho de metadados de auditoria e linhas filtradas.
 */
function generateCsvExport(items = [], summary = {}, exportContext = {}) {
  const meta = buildAuditMetadataLines(exportContext);
  const escapeCsv = (val) => {
    if (val === null || val === undefined) return '""';
    const str = String(val).replace(/"/g, '""');
    return `"${str}"`;
  };

  const lines = [];
  lines.push('# RELATÓRIO DE CONCILIAÇÃO DE VENDAS FISCAIS - CONFEREFISCAL');
  if (meta.isDemoMode) {
    lines.push('# MODO DEMONSTRAÇÃO (DADOS FICTÍCIOS DE EXEMPLO)');
  }
  lines.push(`# Data/Hora da Análise:;${escapeCsv(meta.analysisDate)}`);
  lines.push(`# Data/Hora da Exportação:;${escapeCsv(meta.exportDate)}`);
  lines.push(`# Arquivos XML Utilizados:;${escapeCsv(meta.xmlFilesList)}`);
  lines.push(`# Relatório PDF Utilizado:;${escapeCsv(meta.pdfFileName)}`);
  lines.push(`# Integração Firebird:;${escapeCsv(meta.firebirdDesc)}`);
  lines.push(`# Critérios de Comparação:;${escapeCsv(meta.criteriaDesc)}`);
  lines.push(`# Filtros Aplicados:;${escapeCsv(meta.filtersDesc)}`);
  lines.push('');

  const headers = [
    'Categoria',
    'Status Revisão',
    'Tipo Doc',
    'Modelo',
    'Número',
    'Série',
    'Chave de Acesso',
    'Data Emissão',
    'Data Autorização',
    'Status Fiscal',
    'Forma de Pagamento',
    'Valor XML (R$)',
    'Valor PDF (R$)',
    'Valor Firebird (R$)',
    'Diferença Valor (R$)',
    'Critério de Correspondência',
    'Motivo / Detalhamento da Classificação',
    'Arquivo XML de Origem'
  ];
  lines.push(headers.map(escapeCsv).join(';'));

  for (const item of items) {
    const row = [
      item.categoryLabel,
      item.reviewStatus,
      item.docType,
      item.model,
      item.documentNumber,
      item.series,
      item.accessKey || '',
      formatDateBR(item.issueDate),
      formatDateBR(item.authDate),
      item.status,
      item.paymentMethod,
      item.xmlValue !== null ? item.xmlValue.toFixed(2).replace('.', ',') : '',
      item.pdfValue !== null ? item.pdfValue.toFixed(2).replace('.', ',') : '',
      item.fbValue !== null ? item.fbValue.toFixed(2).replace('.', ',') : '',
      item.valueDifference !== null ? item.valueDifference.toFixed(2).replace('.', ',') : '',
      item.matchCriterion || '',
      (item.reasons || []).join(' | '),
      item.sources?.xml?.displayPath || ''
    ];
    lines.push(row.map(escapeCsv).join(';'));
  }

  // Adiciona BOM UTF-8 (\uFEFF) para abertura direta no Microsoft Excel com acentuação correta
  return Buffer.from('\uFEFF' + lines.join('\r\n'), 'utf8');
}

/**
 * Gera planilha Excel (.xlsx) completa com múltiplas abas (Resumo & Metadados, Conciliação Completa, Divergências).
 */
async function generateExcelExport(items = [], summary = {}, exportContext = {}) {
  const meta = buildAuditMetadataLines(exportContext);
  const workbook = new ExcelJS.Workbook();
  workbook.creator = 'ConfereFiscal - Auditoria Fiscal Local';
  workbook.created = new Date();

  // ABA 1: Resumo Executivo e Metadados
  const wsSummary = workbook.addWorksheet('Resumo Executivo');
  wsSummary.columns = [
    { header: 'Campo / Categoria', key: 'colA', width: 45 },
    { header: 'Quantidade / Detalhe', key: 'colB', width: 35 },
    { header: 'Valor Total (R$)', key: 'colC', width: 25 }
  ];

  wsSummary.addRow(['CONFEREFISCAL — RELATÓRIO DE CONCILIAÇÃO DE VENDAS FISCAIS', '', '']).font = {
    bold: true,
    size: 13
  };
  if (meta.isDemoMode) {
    wsSummary.addRow(['AVISO: DADOS DE DEMONSTRAÇÃO (FICTÍCIOS)', '', '']).font = {
      bold: true,
      color: { argb: 'FFB45309' }
    };
  }
  wsSummary.addRow([]);
  wsSummary.addRow(['METADADOS DA ANÁLISE', 'VALOR', '']).font = { bold: true };
  wsSummary.addRow(['Data e Hora da Conciliação', meta.analysisDate, '']);
  wsSummary.addRow(['Data e Hora da Exportação', meta.exportDate, '']);
  wsSummary.addRow(['Arquivos XML Utilizados', meta.xmlFilesList, '']);
  wsSummary.addRow(['Relatório PDF Utilizado', meta.pdfFileName, '']);
  wsSummary.addRow(['Conexão Firebird', meta.firebirdDesc, '']);
  wsSummary.addRow(['Critérios de Comparação', meta.criteriaDesc, '']);
  wsSummary.addRow(['Filtros Aplicados', meta.filtersDesc, '']);
  wsSummary.addRow([]);

  const catHeaderRow = wsSummary.addRow(['CATEGORIA DE CONCILIAÇÃO', 'QUANTIDADE DE REGISTROS', 'VALOR TOTAL (R$)']);
  catHeaderRow.font = { bold: true, color: { argb: 'FFFFFFFF' } };
  catHeaderRow.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FF1E293B' } };

  for (const cat of summary.categories || []) {
    const r = wsSummary.addRow([cat.label, cat.count, cat.totalValue]);
    r.getCell(3).numFmt = 'R$ #,##0.00';
  }

  wsSummary.addRow([]);
  const srcHeader = wsSummary.addRow(['COMPARATIVO POR FONTE (AUTORIZADOS)', 'DOCUMENTOS', 'VALOR AUTORIZADO (R$)']);
  srcHeader.font = { bold: true };
  if (summary.totalsBySource) {
    const rXml = wsSummary.addRow([
      'Documentos Fiscais (XML)',
      summary.totalsBySource.xml.count,
      summary.totalsBySource.xml.authorizedValue
    ]);
    rXml.getCell(3).numFmt = 'R$ #,##0.00';

    const rPdf = wsSummary.addRow([
      'Relatório de Vendas (PDF)',
      summary.totalsBySource.pdf.count,
      summary.totalsBySource.pdf.authorizedValue
    ]);
    rPdf.getCell(3).numFmt = 'R$ #,##0.00';

    if (summary.firebirdEnabled) {
      const rFb = wsSummary.addRow([
        'Banco de Dados Firebird',
        summary.totalsBySource.firebird.count,
        summary.totalsBySource.firebird.authorizedValue
      ]);
      rFb.getCell(3).numFmt = 'R$ #,##0.00';
    }
  }

  // Função auxiliar para popular abas de registros
  function populateItemsSheet(sheetName, subset) {
    const ws = workbook.addWorksheet(sheetName);
    ws.columns = [
      { header: 'Categoria', key: 'categoryLabel', width: 34 },
      { header: 'Revisão', key: 'reviewStatus', width: 20 },
      { header: 'Tipo', key: 'docType', width: 12 },
      { header: 'Modelo', key: 'model', width: 10 },
      { header: 'Número', key: 'documentNumber', width: 14 },
      { header: 'Série', key: 'series', width: 10 },
      { header: 'Chave de Acesso', key: 'accessKey', width: 48 },
      { header: 'Data Emissão', key: 'issueDate', width: 15 },
      { header: 'Data Autorização', key: 'authDate', width: 16 },
      { header: 'Status', key: 'status', width: 16 },
      { header: 'Pagamento', key: 'paymentMethod', width: 22 },
      { header: 'Valor XML', key: 'xmlValue', width: 16 },
      { header: 'Valor PDF', key: 'pdfValue', width: 16 },
      { header: 'Valor Firebird', key: 'fbValue', width: 16 },
      { header: 'Diferença (R$)', key: 'valueDifference', width: 16 },
      { header: 'Critério de Vínculo', key: 'matchCriterion', width: 42 },
      { header: 'Motivo / Diagnóstico', key: 'reasons', width: 65 },
      { header: 'Arquivo XML', key: 'xmlFile', width: 38 }
    ];

    ws.getRow(1).font = { bold: true, color: { argb: 'FFFFFFFF' } };
    ws.getRow(1).fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FF0F172A' } };

    for (const item of subset) {
      const row = ws.addRow({
        categoryLabel: item.categoryLabel,
        reviewStatus: item.reviewStatus,
        docType: item.docType,
        model: item.model,
        documentNumber: item.documentNumber,
        series: item.series,
        accessKey: item.accessKey || '—',
        issueDate: formatDateBR(item.issueDate),
        authDate: formatDateBR(item.authDate),
        status: item.status,
        paymentMethod: item.paymentMethod,
        xmlValue: item.xmlValue !== null ? item.xmlValue : null,
        pdfValue: item.pdfValue !== null ? item.pdfValue : null,
        fbValue: item.fbValue !== null ? item.fbValue : null,
        valueDifference: item.valueDifference !== null ? item.valueDifference : null,
        matchCriterion: item.matchCriterion || '',
        reasons: (item.reasons || []).join(' | '),
        xmlFile: item.sources?.xml?.displayPath || '—'
      });

      ['xmlValue', 'pdfValue', 'fbValue', 'valueDifference'].forEach((colKey) => {
        const cell = row.getCell(colKey);
        if (cell.value !== null && cell.value !== undefined) {
          cell.numFmt = 'R$ #,##0.00';
        }
      });
    }
  }

  populateItemsSheet('Registros Exportados', items);
  populateItemsSheet(
    'Apenas Divergências e Alertas',
    items.filter((i) => i.category !== 'CONCILIADO')
  );

  const arrayBuffer = await workbook.xlsx.writeBuffer();
  return Buffer.from(arrayBuffer);
}

/**
 * Gera um Relatório PDF executivo da conciliação fiscal usando PDFKit.
 */
function generatePdfReportExport(items = [], summary = {}, exportContext = {}) {
  return new Promise((resolve, reject) => {
    try {
      const meta = buildAuditMetadataLines(exportContext);
      const doc = new PDFDocument({
        size: 'A4',
        layout: 'landscape',
        margin: 32,
        bufferPages: true
      });

      const chunks = [];
      doc.on('data', (chunk) => chunks.push(chunk));
      doc.on('end', () => resolve(Buffer.concat(chunks)));
      doc.on('error', reject);

      // Cabeçalho Principal
      doc.rect(32, 28, 778, 52).fill('#0F172A');
      doc
        .fillColor('#FFFFFF')
        .font('Helvetica-Bold')
        .fontSize(15)
        .text('CONFEREFISCAL — RELATÓRIO DE AUDITORIA E CONCILIAÇÃO FISCAL', 46, 39);
      doc
        .font('Helvetica')
        .fontSize(9)
        .fillColor('#CBD5E1')
        .text(
          `Gerado localmente em: ${meta.exportDate}  |  Data da Análise: ${meta.analysisDate}${
            meta.isDemoMode ? '  |  [MODO DEMONSTRAÇÃO - DADOS FICTÍCIOS]' : ''
          }`,
          46,
          60
        );

      doc.moveDown(2.2);
      doc.x = 32;
      doc.y = 92;

      // Bloco de Metadados de Auditoria
      doc.fillColor('#0F172A').font('Helvetica-Bold').fontSize(10).text('1. Parâmetros, Fontes e Critérios da Conciliação');
      doc.moveDown(0.3);
      doc.font('Helvetica').fontSize(8.5).fillColor('#334155');
      doc.text(`• Arquivos XML utilizados: ${meta.xmlFilesList}`);
      doc.text(`• Relatório PDF utilizado: ${meta.pdfFileName}`);
      doc.text(`• Integração Firebird (2.5 / 5.0): ${meta.firebirdDesc}`);
      doc.text(`• Critérios de Comparação: ${meta.criteriaDesc}`);
      doc.text(`• Filtros aplicados nesta exportação: ${meta.filtersDesc}`);

      doc.moveDown(0.8);
      doc.fillColor('#0F172A').font('Helvetica-Bold').fontSize(10).text('2. Resumo por Categoria de Conciliação');
      doc.moveDown(0.4);

      // Tabela de Resumo por Categoria
      const sumTop = doc.y;
      doc.font('Helvetica-Bold').fontSize(8).fillColor('#1E293B');
      doc.text('Categoria de Conciliação', 36, sumTop);
      doc.text('Qtd. Registros', 320, sumTop, { width: 80, align: 'right' });
      doc.text('Valor Total de Referência', 420, sumTop, { width: 140, align: 'right' });

      let curY = sumTop + 13;
      doc.moveTo(32, curY - 3).lineTo(580, curY - 3).strokeColor('#CBD5E1').stroke();

      doc.font('Helvetica').fontSize(8).fillColor('#1E293B');
      for (const cat of summary.categories || []) {
        doc.text(cat.label, 36, curY);
        doc.text(String(cat.count), 320, curY, { width: 80, align: 'right' });
        doc.text(formatCurrencyBR(cat.totalValue), 420, curY, { width: 140, align: 'right' });
        curY += 12;
      }

      doc.y = curY + 10;
      doc.x = 32;
      doc
        .fillColor('#0F172A')
        .font('Helvetica-Bold')
        .fontSize(10)
        .text(`3. Detalhamento dos Registros (${items.length} item(ns))`);
      doc.moveDown(0.4);

      function drawTableHeader(yPos) {
        doc.rect(32, yPos, 778, 16).fill('#1E293B');
        doc.fillColor('#FFFFFF').font('Helvetica-Bold').fontSize(7.5);
        doc.text('Doc / Série', 36, yPos + 4, { width: 65 });
        doc.text('Data Ref.', 104, yPos + 4, { width: 55 });
        doc.text('Categoria', 162, yPos + 4, { width: 135 });
        doc.text('Status', 300, yPos + 4, { width: 62 });
        doc.text('Valor XML', 365, yPos + 4, { width: 65, align: 'right' });
        doc.text('Valor PDF', 435, yPos + 4, { width: 65, align: 'right' });
        doc.text('Valor FB', 505, yPos + 4, { width: 62, align: 'right' });
        doc.text('Critério / Diagnóstico', 575, yPos + 4, { width: 230 });
        return yPos + 20;
      }

      let rowY = drawTableHeader(doc.y);

      items.forEach((item, index) => {
        if (rowY > 525) {
          doc.addPage({ size: 'A4', layout: 'landscape', margin: 32 });
          rowY = drawTableHeader(32);
        }

        if (index % 2 === 1) {
          doc.rect(32, rowY - 2, 778, 16).fill('#F8FAFC');
        }

        doc.fillColor('#0F172A').font('Helvetica').fontSize(7.2);
        doc.text(`${item.documentNumber}/${item.series}`, 36, rowY, { width: 65, lineBreak: false });
        doc.text(formatDateBR(item.referenceDate), 104, rowY, { width: 55, lineBreak: false });
        doc.font('Helvetica-Bold').text(item.categoryLabel.slice(0, 32), 162, rowY, { width: 135, lineBreak: false });
        doc.font('Helvetica').text(String(item.status || '').slice(0, 12), 300, rowY, { width: 62, lineBreak: false });
        doc.text(formatCurrencyBR(item.xmlValue), 365, rowY, { width: 65, align: 'right', lineBreak: false });
        doc.text(formatCurrencyBR(item.pdfValue), 435, rowY, { width: 65, align: 'right', lineBreak: false });
        doc.text(formatCurrencyBR(item.fbValue), 505, rowY, { width: 62, align: 'right', lineBreak: false });

        const diag = `${item.matchCriterion ? `[${item.matchCriterion}] ` : ''}${(item.reasons || [])[0] || ''}`;
        doc.text(diag.slice(0, 72), 575, rowY, { width: 230, lineBreak: false });

        rowY += 15;
      });

      doc.end();
    } catch (err) {
      reject(err);
    }
  });
}

module.exports = {
  generateCsvExport,
  generateExcelExport,
  generatePdfReportExport
};
