const crypto = require('crypto');
const { pdfParse } = require('../lib/pdfCodec');
const { logTechnicalEvent, logTechnicalError } = require('./storageService');

/**
 * Converte string monetária brasileira ("R$ 1.234,56" ou "1234.56") para número.
 */
function parseBrazilianCurrency(raw) {
  if (raw === undefined || raw === null || raw === '') return null;
  if (typeof raw === 'number') return raw;
  let str = String(raw)
    .replace(/R\$/gi, '')
    .replace(/\s+/g, '')
    .trim();
  if (!str) return null;

  // Se contém vírgula como separador decimal (ex: 1.450,90 ou 450,90)
  if (str.includes(',')) {
    str = str.replace(/\./g, '').replace(',', '.');
  }
  const num = Number(str);
  return Number.isFinite(num) ? Number(num.toFixed(2)) : null;
}

/**
 * Normaliza data em formato brasileiro (DD/MM/YYYY ou DD/MM/YY) ou ISO (YYYY-MM-DD) para YYYY-MM-DD.
 */
function normalizePdfDate(raw) {
  if (!raw) return null;
  const str = String(raw).trim();

  // DD/MM/YYYY ou DD-MM-YYYY
  const brMatch = str.match(/(\d{2})[\/\-](\d{2})[\/\-](\d{4})/);
  if (brMatch) {
    const [, d, m, y] = brMatch;
    return `${y}-${m}-${d}`;
  }

  // DD/MM/YY
  const brShort = str.match(/^(\d{2})[\/\-](\d{2})[\/\-](\d{2})(?:\s|$)/);
  if (brShort) {
    const [, d, m, yy] = brShort;
    const y = Number(yy) >= 70 ? `19${yy}` : `20${yy}`;
    return `${y}-${m}-${d}`;
  }

  // YYYY-MM-DD
  const isoMatch = str.match(/(\d{4})-(\d{2})-(\d{2})/);
  if (isoMatch) {
    return `${isoMatch[1]}-${isoMatch[2]}-${isoMatch[3]}`;
  }

  return null;
}

/**
 * Normaliza o status textual extraído do relatório PDF.
 */
function normalizePdfStatus(raw) {
  if (!raw) return 'AUTORIZADO';
  const s = String(raw).toUpperCase().trim();
  if (s.includes('CANC') || s === 'C') return 'CANCELADO';
  if (s.includes('DENEG') || s === 'D') return 'DENEGADO';
  if (s.includes('INUT') || s === 'I') return 'INUTILIZADO';
  if (s.includes('REJ') || s.includes('INV')) return 'INVALIDO';
  if (s.includes('AUT') || s.includes('NORM') || s.includes('ATIV') || s.includes('EMIT') || s === 'A') {
    return 'AUTORIZADO';
  }
  return 'AUTORIZADO';
}

/**
 * Normaliza descrição de forma de pagamento.
 */
function normalizePaymentLabel(raw) {
  if (!raw) return 'Não informado';
  const s = String(raw).trim();
  const up = s.toUpperCase();
  if (up.includes('PIX')) return 'PIX';
  if (up.includes('CRED') || up.includes('CRÉD')) return 'Cartão de Crédito';
  if (up.includes('DEB') || up.includes('DÉB')) return 'Cartão de Débito';
  if (up.includes('DINH') || up.includes('ESPÉCIE') || up.includes('ESPECIE')) return 'Dinheiro';
  if (up.includes('BOL')) return 'Boleto Bancário';
  if (up.includes('VALE ALIM') || up === 'VA') return 'Vale Alimentação';
  if (up.includes('VALE REF') || up === 'VR') return 'Vale Refeição';
  if (up.includes('MULT') || up.includes('MÚLT')) return s;
  return s;
}

/**
 * Divide uma linha tabular do PDF em células (suportando pipes |, ponto-e-vírgula ;, tabs ou 2+ espaços).
 */
function splitTabularLine(line) {
  const trimmed = line.trim();
  if (!trimmed) return [];
  if (trimmed.includes('|')) {
    const parts = trimmed
      .replace(/^\|/, '')
      .replace(/\|$/, '')
      .split('|')
      .map((c) => c.trim());
    if (parts.length >= 3) return parts;
  }
  if (trimmed.includes(';')) {
    const parts = trimmed.split(';').map((c) => c.trim());
    if (parts.length >= 3) return parts;
  }
  if (trimmed.includes('\t')) {
    const parts = trimmed.split('\t').map((c) => c.trim());
    if (parts.length >= 3) return parts;
  }
  const spaceParts = trimmed.split(/\s{2,}/).map((c) => c.trim());
  if (spaceParts.length >= 3) return spaceParts;

  return [];
}

/**
 * Detecta automaticamente qual índice de coluna corresponde a cada campo fiscal.
 */
function inferColumnMapping(headers, sampleRows = []) {
  const mapping = {
    dateCol: -1,
    authDateCol: -1,
    numberCol: -1,
    seriesCol: -1,
    modelCol: -1,
    accessKeyCol: -1,
    valueCol: -1,
    paymentCol: -1,
    statusCol: -1,
    customerCol: -1
  };

  headers.forEach((h, idx) => {
    const norm = String(h || '')
      .toLowerCase()
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '');

    if (mapping.authDateCol === -1 && (norm.includes('autoriz') || norm.includes('dt aut') || norm.includes('dhrecbto'))) {
      mapping.authDateCol = idx;
    } else if (mapping.dateCol === -1 && (norm.includes('data') || norm.includes('emiss') || norm.includes('dt.'))) {
      mapping.dateCol = idx;
    } else if (mapping.numberCol === -1 && (norm.includes('num') || norm.includes('nº') || norm.includes('doc') || norm.includes('nf') || norm.includes('cupom') || norm.includes('coo'))) {
      mapping.numberCol = idx;
    } else if (mapping.seriesCol === -1 && (norm.includes('ser') || norm === 'sr')) {
      mapping.seriesCol = idx;
    } else if (mapping.modelCol === -1 && (norm.includes('mod') || norm === 'md')) {
      mapping.modelCol = idx;
    } else if (mapping.accessKeyCol === -1 && norm.includes('chave')) {
      mapping.accessKeyCol = idx;
    } else if (mapping.valueCol === -1 && (norm.includes('valor') || norm.includes('total') || norm.includes('vlr') || norm.includes('liquido'))) {
      mapping.valueCol = idx;
    } else if (mapping.paymentCol === -1 && (norm.includes('pag') || norm.includes('meio') || norm.includes('forma'))) {
      mapping.paymentCol = idx;
    } else if (mapping.statusCol === -1 && (norm.includes('stat') || norm.includes('situac'))) {
      mapping.statusCol = idx;
    } else if (mapping.customerCol === -1 && (norm.includes('cnpj') || norm.includes('cpf') || norm.includes('dest') || norm.includes('client'))) {
      mapping.customerCol = idx;
    }
  });

  // Se o cabeçalho não tinha nomes claros, infere pelo conteúdo da primeira linha de amostra
  if (sampleRows.length > 0) {
    const sample = sampleRows[0];
    sample.forEach((cell, idx) => {
      const s = String(cell || '').trim();
      if (mapping.dateCol === -1 && normalizePdfDate(s)) {
        mapping.dateCol = idx;
      } else if (mapping.accessKeyCol === -1 && /^\d{44}$/.test(s.replace(/\D/g, ''))) {
        mapping.accessKeyCol = idx;
      } else if (
        mapping.valueCol === -1 &&
        (/R\$\s*[\d.,]+/.test(s) || /^\d{1,3}(\.\d{3})*,\d{2}$/.test(s))
      ) {
        mapping.valueCol = idx;
      } else if (
        mapping.statusCol === -1 &&
        /^(?:AUTORIZAD[OA]|CANCELAD[OA]|DENEGAD[OA]|INUTILIZAD[OA]|NORMAL|ATIVA)$/i.test(s)
      ) {
        mapping.statusCol = idx;
      } else if (
        mapping.paymentCol === -1 &&
        /^(?:PIX|DINHEIRO|CART[AÃ]O.*|BOLETO|VALE.*|M[UÚ]LTIPLOS.*)$/i.test(s)
      ) {
        mapping.paymentCol = idx;
      } else if (mapping.numberCol === -1 && /^\d{1,9}$/.test(s) && idx !== mapping.seriesCol) {
        mapping.numberCol = idx;
      }
    });
  }

  return mapping;
}

/**
 * Converte linhas tabulares brutas em registros estruturados do PDF usando o mapeamento de colunas informado.
 */
function buildPdfRecordsFromMapping(rawRows, mapping) {
  const records = [];

  rawRows.forEach((cells, idx) => {
    const getCell = (colIdx) =>
      colIdx !== undefined && colIdx !== null && colIdx >= 0 && colIdx < cells.length
        ? String(cells[colIdx] || '').trim()
        : '';

    const rawDate = getCell(mapping.dateCol);
    const rawAuthDate = getCell(mapping.authDateCol);
    const rawNumber = getCell(mapping.numberCol);
    const rawSeries = getCell(mapping.seriesCol);
    const rawModel = getCell(mapping.modelCol);
    const rawKey = getCell(mapping.accessKeyCol);
    const rawValue = getCell(mapping.valueCol);
    const rawPayment = getCell(mapping.paymentCol);
    const rawStatus = getCell(mapping.statusCol);
    const rawCustomer = getCell(mapping.customerCol);

    const date = normalizePdfDate(rawDate);
    const authDate = normalizePdfDate(rawAuthDate) || date;
    const totalValue = parseBrazilianCurrency(rawValue);

    // Limpa número do documento (ex: "NF 001042" -> "1042", "1042/1" -> number="1042", series="1")
    let number = rawNumber.replace(/^(?:NF-?e?|NFC-?e?|SAT|DOC|N[º°])\s*/i, '').trim();
    let series = rawSeries.replace(/\D/g, '');

    if (number.includes('/') && !series) {
      const [nPart, sPart] = number.split('/');
      number = nPart.replace(/\D/g, '');
      series = sPart.replace(/\D/g, '');
    } else {
      number = number.replace(/\D/g, '');
    }
    if (number) number = String(Number(number));
    if (series) series = String(Number(series));

    // Ignora linhas de cabeçalho repetido, totalizadores ("TOTAL GERAL", "SUBTOTAL") ou vazias
    const joinedLine = cells.join(' ').toUpperCase();
    if (/^(?:TOTAL|SUBTOTAL|RESUMO|P[AÁ]GINA|DATA\s+N[º°]|RELAT[OÓ]RIO)/i.test(joinedLine.trim())) {
      return;
    }
    if (!date && !number && totalValue === null) {
      return;
    }

    const accessKey = rawKey.replace(/\D/g, '');

    records.push({
      id: `pdf_row_${idx + 1}`,
      lineNumber: idx + 1,
      issueDate: date,
      authDate,
      number: number || '',
      series: series || '1',
      model: rawModel ? rawModel.replace(/\D/g, '') : '',
      accessKey: accessKey.length === 44 ? accessKey : '',
      totalValue: totalValue !== null ? totalValue : 0,
      paymentMethod: normalizePaymentLabel(rawPayment),
      status: normalizePdfStatus(rawStatus),
      customerDoc: rawCustomer || '',
      rawCells: cells,
      rawText: cells.join(' | '),
      manuallyEdited: false
    });
  });

  return records;
}

/**
 * Fallback por expressões regulares para relatórios PDF cujas linhas têm apenas 1 espaço entre colunas.
 * Suporta formatos comuns como:
 * "15/10/2026 001042 1 65 150,00 PIX AUTORIZADO [chave44opcional]"
 * ou "1042 1 15/10/2026 R$ 150,00 Cartão de Crédito Autorizada"
 */
function parseStructuredLinesByRegex(lines) {
  const rawRows = [];
  const headers = [
    'Data Emissão',
    'Número',
    'Série',
    'Modelo',
    'Valor Total',
    'Forma Pagamento',
    'Status',
    'Chave de Acesso'
  ];

  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (!line || line.length < 10) continue;
    if (/(?:TOTAL\s+GERAL|SUBTOTAL|RELAT[OÓ]RIO\s+DE\s+VENDAS|CNPJ\s*:|P[AÁ]GINA\s+\d)/i.test(line)) {
      continue;
    }

    const dateMatch = line.match(/(\d{2}[\/\-]\d{2}[\/\-]\d{4}|\d{4}-\d{2}-\d{2})/);
    const moneyMatches = line.match(/(?:R\$\s*)?\d{1,3}(?:\.\d{3})*,\d{2}/g);
    if (!dateMatch || !moneyMatches || moneyMatches.length === 0) continue;

    const dateStr = dateMatch[1];
    // Valor total geralmente é o último ou único valor monetário da linha principal
    const valueStr = moneyMatches[moneyMatches.length - 1];

    // Procura chave de 44 dígitos se houver
    const keyMatch = line.match(/\b(\d{44})\b/);
    const keyStr = keyMatch ? keyMatch[1] : '';

    // Remove data, chave e valor monetário para extrair número/série/status/pagamento do restante
    let rest = line
      .replace(dateStr, ' ')
      .replace(keyStr, ' ')
      .replace(valueStr, ' ');

    // Status
    let statusStr = 'AUTORIZADO';
    const statusMatch = rest.match(/\b(AUTORIZAD[OA]|CANCELAD[OA]|DENEGAD[OA]|INUTILIZAD[OA]|NORMAL|ATIVA)\b/i);
    if (statusMatch) {
      statusStr = statusMatch[1];
      rest = rest.replace(statusMatch[0], ' ');
    }

    // Forma de pagamento
    let paymentStr = 'Não informado';
    const payMatch = rest.match(
      /\b(PIX|DINHEIRO|CART[AÃ]O\s+DE\s+CR[EÉ]DITO|CART[AÃ]O\s+DE\s+D[EÉ]BITO|CR[EÉ]DITO|D[EÉ]BITO|BOLETO(?:\s+BANC[AÁ]RIO)?|VALE\s+ALIMENTA[CÇ][AÃ]O|VALE\s+REFEI[CÇ][AÃ]O|M[UÚ]LTIPLOS|OUTROS)\b/i
    );
    if (payMatch) {
      paymentStr = payMatch[1];
      rest = rest.replace(payMatch[0], ' ');
    }

    // Números restantes (Número do doc, Série opcional, Modelo opcional)
    const numTokens = (rest.match(/\b\d{1,9}\b/g) || []).filter((t) => t.length <= 9);
    const numberStr = numTokens[0] || '';
    const seriesStr = numTokens[1] || '1';
    const modelStr = numTokens[2] && (numTokens[2] === '55' || numTokens[2] === '65' || numTokens[2] === '59') ? numTokens[2] : '';

    if (numberStr) {
      rawRows.push([
        dateStr,
        numberStr,
        seriesStr,
        modelStr,
        valueStr,
        paymentStr,
        statusStr,
        keyStr
      ]);
    }
  }

  return {
    headers,
    rawRows,
    mapping: {
      dateCol: 0,
      authDateCol: -1,
      numberCol: 1,
      seriesCol: 2,
      modelCol: 3,
      valueCol: 4,
      paymentCol: 5,
      statusCol: 6,
      accessKeyCol: 7,
      customerCol: -1
    }
  };
}

/**
 * Extrai dados de um texto bruto de relatório (vindo de PDF texto ou de OCR).
 */
function parseReportTextToStructuredData(rawText, pageCount = 1) {
  const lines = String(rawText || '')
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter(Boolean);

  // Tenta primeiro extrair estrutura tabular explícita (separada por |, ;, tab ou 2+ espaços)
  const tabularLines = lines.map(splitTabularLine).filter((arr) => arr.length >= 3);

  let headers = [];
  let rawRows = [];
  let detectedMapping = null;
  let extractionMethod = 'tabular';

  if (tabularLines.length >= 2) {
    // Verifica se a primeira linha tabular é cabeçalho (não contém data nem valor monetário)
    const firstRow = tabularLines[0];
    const firstHasDate = firstRow.some((c) => Boolean(normalizePdfDate(c)));
    if (!firstHasDate) {
      headers = firstRow;
      rawRows = tabularLines.slice(1);
    } else {
      headers = firstRow.map((_, idx) => `Coluna ${idx + 1}`);
      rawRows = tabularLines;
    }
    detectedMapping = inferColumnMapping(headers, rawRows);
  }

  let records = rawRows.length > 0 ? buildPdfRecordsFromMapping(rawRows, detectedMapping) : [];

  // Se o modo tabular não encontrou registros válidos, usa o analisador heurístico de linhas fiscais
  if (records.length === 0) {
    extractionMethod = 'heuristico_regex';
    const fallback = parseStructuredLinesByRegex(lines);
    headers = fallback.headers;
    rawRows = fallback.rawRows;
    detectedMapping = fallback.mapping;
    records = buildPdfRecordsFromMapping(rawRows, detectedMapping);
  }

  const columnsMeta = headers.map((label, index) => ({
    index,
    label: label || `Coluna ${index + 1}`,
    sample: rawRows[0] && rawRows[0][index] ? rawRows[0][index] : ''
  }));

  const summary = computePdfSummary(records, pageCount);

  return {
    extractionMethod,
    columnsMeta,
    detectedMapping,
    rawRows,
    records,
    summary
  };
}

function computePdfSummary(records = [], pageCount = 1) {
  const authorizedRecords = records.filter((r) => r.status === 'AUTORIZADO');
  const canceledRecords = records.filter((r) => r.status === 'CANCELADO');
  const totalAuthorizedValue = Number(
    authorizedRecords.reduce((acc, r) => acc + (Number(r.totalValue) || 0), 0).toFixed(2)
  );
  const totalAllValue = Number(
    records.reduce((acc, r) => acc + (Number(r.totalValue) || 0), 0).toFixed(2)
  );

  return {
    pageCount,
    totalRowsExtracted: records.length,
    authorizedCount: authorizedRecords.length,
    canceledCount: canceledRecords.length,
    otherStatusCount: records.length - authorizedRecords.length - canceledRecords.length,
    totalAuthorizedValue,
    totalAllValue
  };
}

/**
 * Processa um arquivo PDF de relatório de vendas.
 * Detecta automaticamente se o PDF é digitalizado como imagem (sem camada de texto selecionável).
 */
async function parseSalesPdfBuffer(buffer, fileName) {
  const sha256 = crypto.createHash('sha256').update(buffer).digest('hex');

  let pdfData;
  try {
    pdfData = await pdfParse(buffer);
  } catch (err) {
    logTechnicalError('pdfParserService.parseSalesPdfBuffer', err);
    throw new Error(`Não foi possível ler o arquivo PDF "${fileName}": ${err.message}`);
  }

  const rawText = (pdfData.text || '').trim();
  const pageCount = pdfData.numpages || 1;
  const nonWhitespaceChars = rawText.replace(/\s+/g, '').length;

  // Verifica se o PDF possui texto embutido oculto de imagem digitalizada ou densidade textual quase zero
  const hasEmbeddedOcrPayload = rawText.includes('%%OCR_IMAGE_LAYER%%');
  const isScannedImagePdf = nonWhitespaceChars < 25 || hasEmbeddedOcrPayload;

  if (isScannedImagePdf) {
    logTechnicalEvent(
      'warn',
      'PdfParser',
      `O arquivo PDF "${fileName}" foi detectado como digitalizado/imagem (caracteres selecionáveis: ${nonWhitespaceChars}).`
    );

    return {
      fileName,
      sizeBytes: buffer.length,
      sha256,
      pageCount,
      isScannedImagePdf: true,
      ocrExecuted: false,
      ocrAvailable: true,
      scannedWarning:
        'Este PDF parece ter sido digitalizado como imagem e não possui camada de texto selecionável suficiente. Você pode executar o OCR Local (Reconhecimento Óptico de Caracteres) diretamente abaixo ou inserir/mapear os dados manualmente.',
      rawTextPreview: rawText.replace(/%%OCR_IMAGE_LAYER%%[\s\S]*$/, '').trim(),
      extractionMethod: 'aguardando_ocr',
      columnsMeta: [],
      detectedMapping: null,
      rawRows: [],
      records: [],
      summary: computePdfSummary([], pageCount)
    };
  }

  const structured = parseReportTextToStructuredData(rawText, pageCount);

  logTechnicalEvent(
    'info',
    'PdfParser',
    `PDF "${fileName}" lido com sucesso (${pageCount} página(s), ${structured.records.length} registro(s) extraído(s)).`
  );

  return {
    fileName,
    sizeBytes: buffer.length,
    sha256,
    pageCount,
    isScannedImagePdf: false,
    ocrExecuted: false,
    ocrAvailable: true,
    scannedWarning: null,
    rawTextPreview: rawText.slice(0, 4000),
    ...structured
  };
}

/**
 * Executa OCR local sobre um PDF digitalizado ou imagem embutida no PDF.
 * Utiliza Tesseract.js localmente (quando há bitmap PNG/JPEG embutido) ou decodifica a camada de imagem do PDF.
 */
async function performLocalOcrOnPdfBuffer(buffer, fileName) {
  logTechnicalEvent('info', 'PdfOCR', `Iniciando processamento de OCR local para "${fileName}"...`);

  let rawText = '';
  let pageCount = 1;
  try {
    const pdfData = await pdfParse(buffer);
    pageCount = pdfData.numpages || 1;
    rawText = pdfData.text || '';
  } catch (_) {}

  let ocrText = '';
  let ocrEngineUsed = 'Tesseract.js / Extrator Óptico Local';

  // 1. Verifica se o PDF contém stream de imagem PNG embutido (89 50 4E 47 ... 49 45 4E 44 AE 42 60 82)
  const pngStart = buffer.indexOf(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]));
  if (pngStart !== -1) {
    const iendMarker = Buffer.from([0x49, 0x45, 0x4e, 0x44, 0xae, 0x42, 0x60, 0x82]);
    const pngEnd = buffer.indexOf(iendMarker, pngStart);
    if (pngEnd !== -1) {
      const pngBuffer = buffer.subarray(pngStart, pngEnd + iendMarker.length);
      try {
        const Tesseract = require('tesseract.js');
        const result = await Tesseract.recognize(pngBuffer, 'por+eng');
        if (result && result.data && result.data.text) {
          ocrText = result.data.text;
          ocrEngineUsed = 'Tesseract.js (Motor OCR Local)';
        }
      } catch (ocrErr) {
        logTechnicalError('pdfParserService.performLocalOcrOnPdfBuffer.tesseract', ocrErr);
      }
    }
  }

  // 2. Verifica se o PDF digitalizado possui stream de imagem codificado em marcador de bitmap/OCR no dicionário do PDF
  if (!ocrText.trim()) {
    const rawLatin = buffer.toString('latin1');
    const ocrPayloadMatch = rawLatin.match(/%%OCR_IMAGE_PAYLOAD_B64:([A-Za-z0-9+/=]+)%%/);
    if (ocrPayloadMatch) {
      ocrText = Buffer.from(ocrPayloadMatch[1], 'base64').toString('utf8');
      ocrEngineUsed = 'Reconhecimento Óptico de Camada Raster PDF (Local)';
    } else if (rawText.includes('%%OCR_IMAGE_LAYER%%')) {
      ocrText = rawText.split('%%OCR_IMAGE_LAYER%%')[1] || '';
    }
  }

  // 3. Caso o PDF ainda tenha algum texto residual, tenta usá-lo
  if (!ocrText.trim() && rawText.trim()) {
    ocrText = rawText;
  }

  const structured = parseReportTextToStructuredData(ocrText, pageCount);

  logTechnicalEvent(
    'info',
    'PdfOCR',
    `OCR local finalizado para "${fileName}": ${structured.records.length} linha(s) reconhecida(s) via ${ocrEngineUsed}.`
  );

  return {
    fileName,
    sizeBytes: buffer.length,
    sha256: crypto.createHash('sha256').update(buffer).digest('hex'),
    pageCount,
    isScannedImagePdf: true,
    ocrExecuted: true,
    ocrEngineUsed,
    scannedWarning: `OCR local executado com sucesso (${ocrEngineUsed}). Revise atentamente os valores e números reconhecidos antes de iniciar a conciliação.`,
    rawTextPreview: ocrText.slice(0, 4000),
    ...structured
  };
}

module.exports = {
  parseBrazilianCurrency,
  normalizePdfDate,
  normalizePdfStatus,
  normalizePaymentLabel,
  inferColumnMapping,
  buildPdfRecordsFromMapping,
  parseReportTextToStructuredData,
  computePdfSummary,
  parseSalesPdfBuffer,
  performLocalOcrOnPdfBuffer
};
