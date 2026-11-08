const { XMLParser } = require('../lib/xmlCodec');

const SEFAZ_PAYMENT_LABELS = {
  '01': 'Dinheiro',
  '02': 'Cheque',
  '03': 'Cartão de Crédito',
  '04': 'Cartão de Débito',
  '05': 'Crédito Loja',
  '10': 'Vale Alimentação',
  '11': 'Vale Refeição',
  '12': 'Vale Presente',
  '13': 'Vale Combustível',
  '14': 'Duplicata Mercantil',
  '15': 'Boleto Bancário',
  '16': 'Depósito Bancário',
  '17': 'PIX',
  '18': 'Transferência / Carteira Digital',
  '19': 'Fidelidade / Cashback',
  '90': 'Sem Pagamento',
  '99': 'Outros'
};

const MODEL_LABELS = {
  '55': 'NF-e (Modelo 55)',
  '65': 'NFC-e (Modelo 65)',
  '59': 'CF-e SAT (Modelo 59)'
};

// CFOPs tipicamente de devolução, remessa ou simples faturamento (não venda efetiva)
const NON_SALE_CFOPS = new Set([
  '1201', '1202', '1410', '1411', '2201', '2202', '2410', '2411',
  '5201', '5202', '5410', '5411', '6201', '6202', '6410', '6411',
  '5901', '5902', '5903', '5904', '5905', '5906', '5907', '5908', '5909',
  '5910', '5911', '5912', '5913', '5914', '5915', '5916', '5917', '5918',
  '5919', '5920', '5921', '5949', '6901', '6902', '6910', '6915', '6949',
  '5151', '5152', '6151', '6152'
]);

const xmlParser = new XMLParser({
  ignoreAttributes: false,
  attributeNamePrefix: '@_',
  parseTagValue: false, // Mantém strings exatas para chaves de 44 dígitos, CNPJ, zeros à esquerda
  trimValues: true,
  processEntities: false
});

function asArray(val) {
  if (val === undefined || val === null) return [];
  return Array.isArray(val) ? val : [val];
}

function toNum(val, fallback = 0) {
  if (val === undefined || val === null || val === '') return fallback;
  const cleaned = String(val).replace(',', '.').trim();
  const n = Number(cleaned);
  return Number.isFinite(n) ? n : fallback;
}

function normalizeDateAndTime(rawDate, rawTime) {
  if (!rawDate) return { iso: null, dateOnly: null, display: null };
  const str = String(rawDate).trim();

  // Formato ISO NF-e 3.10/4.00: 2026-10-15T14:32:10-03:00
  if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/.test(str)) {
    const dateOnly = str.slice(0, 10);
    const timeOnly = str.slice(11, 19);
    const [y, m, d] = dateOnly.split('-');
    return {
      iso: str,
      dateOnly,
      timeOnly,
      display: `${d}/${m}/${y} ${timeOnly}`
    };
  }

  // Formato antigo NF-e 2.00: YYYY-MM-DD
  if (/^\d{4}-\d{2}-\d{2}$/.test(str)) {
    const [y, m, d] = str.split('-');
    const timePart = rawTime ? String(rawTime).trim().slice(0, 8) : '00:00:00';
    return {
      iso: `${str}T${timePart}`,
      dateOnly: str,
      timeOnly: timePart,
      display: `${d}/${m}/${y}${rawTime ? ' ' + timePart : ''}`
    };
  }

  // Formato SAT CF-e: YYYYMMDD e HHMMSS
  if (/^\d{8}$/.test(str)) {
    const y = str.slice(0, 4);
    const m = str.slice(4, 6);
    const d = str.slice(6, 8);
    const tStr = rawTime && /^\d{6}$/.test(String(rawTime))
      ? `${String(rawTime).slice(0, 2)}:${String(rawTime).slice(2, 4)}:${String(rawTime).slice(4, 6)}`
      : '00:00:00';
    return {
      iso: `${y}-${m}-${d}T${tStr}`,
      dateOnly: `${y}-${m}-${d}`,
      timeOnly: tStr,
      display: `${d}/${m}/${y} ${tStr}`
    };
  }

  return { iso: str, dateOnly: str.slice(0, 10), timeOnly: '', display: str };
}

function cleanAccessKey(rawKey) {
  if (!rawKey) return '';
  return String(rawKey).replace(/^(?:NFe|CFe)/i, '').replace(/\D/g, '');
}

/**
 * Extrai impostos de um item <det><imposto> da NF-e/NFC-e/SAT.
 */
function extractItemTaxes(impostoNode) {
  if (!impostoNode) {
    return {
      vTotTrib: null,
      icmsCST: null,
      icmsVal: 0,
      pisVal: 0,
      cofinsVal: 0,
      ipiVal: 0
    };
  }

  const vTotTrib = impostoNode.vTotTrib !== undefined ? toNum(impostoNode.vTotTrib, 0) : null;
  let icmsCST = null;
  let icmsVal = 0;

  if (impostoNode.ICMS) {
    const icmsGroupKey = Object.keys(impostoNode.ICMS)[0];
    const icmsObj = icmsGroupKey ? impostoNode.ICMS[icmsGroupKey] : null;
    if (icmsObj && typeof icmsObj === 'object') {
      icmsCST = icmsObj.CST || icmsObj.CSOSN || null;
      icmsVal = toNum(icmsObj.vICMS, 0);
    }
  }

  let pisVal = 0;
  if (impostoNode.PIS) {
    const pisKey = Object.keys(impostoNode.PIS)[0];
    const pisObj = pisKey ? impostoNode.PIS[pisKey] : null;
    if (pisObj && typeof pisObj === 'object') {
      pisVal = toNum(pisObj.vPIS, 0);
    }
  }

  let cofinsVal = 0;
  if (impostoNode.COFINS) {
    const cofKey = Object.keys(impostoNode.COFINS)[0];
    const cofObj = cofKey ? impostoNode.COFINS[cofKey] : null;
    if (cofObj && typeof cofObj === 'object') {
      cofinsVal = toNum(cofObj.vCOFINS, 0);
    }
  }

  let ipiVal = 0;
  if (impostoNode.IPI && impostoNode.IPI.IPITrib) {
    ipiVal = toNum(impostoNode.IPI.IPITrib.vIPI, 0);
  }

  return {
    vTotTrib,
    icmsCST,
    icmsVal,
    pisVal,
    cofinsVal,
    ipiVal
  };
}

/**
 * Extrai formas de pagamento do grupo <pag> da NF-e/NFC-e ou <pgto> do SAT.
 */
function extractPayments(pagNode, pgtoSatNode) {
  const payments = [];
  let troco = 0;

  if (pagNode) {
    troco = toNum(pagNode.vTroco, 0);
    const detPagList = asArray(pagNode.detPag);
    if (detPagList.length > 0) {
      for (const det of detPagList) {
        const code = String(det.tPag || '99').padStart(2, '0');
        payments.push({
          code,
          label: det.xPag || SEFAZ_PAYMENT_LABELS[code] || `Código ${code}`,
          amount: toNum(det.vPag, 0),
          cardCnpj: det.card ? det.card.CNPJ || null : null,
          cardBand: det.card ? det.card.tBand || null : null,
          cardAut: det.card ? det.card.cAut || null : null
        });
      }
    } else if (pagNode.tPag !== undefined) {
      // Layout NF-e 3.10 onde <pag> continha diretamente <tPag> e <vPag>
      for (const item of asArray(pagNode)) {
        const code = String(item.tPag || '99').padStart(2, '0');
        payments.push({
          code,
          label: SEFAZ_PAYMENT_LABELS[code] || `Código ${code}`,
          amount: toNum(item.vPag, 0)
        });
      }
    }
  } else if (pgtoSatNode) {
    troco = toNum(pgtoSatNode.vTroco, 0);
    for (const mp of asArray(pgtoSatNode.MP)) {
      const code = String(mp.cMP || '99').padStart(2, '0');
      payments.push({
        code,
        label: SEFAZ_PAYMENT_LABELS[code] || `Código ${code}`,
        amount: toNum(mp.vMP, 0)
      });
    }
  }

  const primaryLabel =
    payments.length === 0
      ? 'Não informado'
      : payments.length === 1
      ? payments[0].label
      : `Múltiplos (${payments.map((p) => p.label).join(' + ')})`;

  return {
    payments,
    troco,
    primaryPaymentLabel: primaryLabel,
    totalPaid: Number(payments.reduce((acc, p) => acc + p.amount, 0).toFixed(2))
  };
}

/**
 * Analisa um único arquivo XML extraído e retorna sua estrutura padronizada.
 */
function parseSingleXmlEntry(entry) {
  const baseMeta = {
    id: `xml_${entry.sha256.slice(0, 12)}_${Math.random().toString(36).slice(2, 6)}`,
    fileName: entry.fileName,
    archiveName: entry.archiveName,
    internalPath: entry.internalPath,
    displayPath: entry.displayPath,
    subfolder: entry.subfolder,
    sizeBytes: entry.sizeBytes,
    sha256: entry.sha256
  };

  let xmlText = '';
  try {
    xmlText = entry.buffer.toString('utf8');
    // Remove BOM UTF-8 se presente
    if (xmlText.charCodeAt(0) === 0xfeff) {
      xmlText = xmlText.slice(1);
    }
  } catch (err) {
    return {
      ...baseMeta,
      recordKind: 'INVALIDO',
      isValidXml: false,
      isSaleDocument: false,
      status: 'INVALIDO',
      statusDescription: `Erro de codificação de caracteres: ${err.message}`,
      missingFields: []
    };
  }

  // Validação sintática preliminar de fechamento básico de tags XML
  const trimmed = xmlText.trim();
  if (!trimmed.startsWith('<') || !trimmed.endsWith('>')) {
    return {
      ...baseMeta,
      recordKind: 'INVALIDO',
      isValidXml: false,
      isSaleDocument: false,
      status: 'INVALIDO',
      statusDescription: 'Arquivo XML truncado ou corrompido (início/fim de marcação inválido).',
      missingFields: []
    };
  }

  let parsed;
  try {
    parsed = xmlParser.parse(trimmed);
  } catch (err) {
    return {
      ...baseMeta,
      recordKind: 'INVALIDO',
      isValidXml: false,
      isSaleDocument: false,
      status: 'INVALIDO',
      statusDescription: `Estrutura XML malformada: ${err.message}`,
      missingFields: []
    };
  }

  // Verifica se há tags de fechamento incompatíveis simples (fast-xml-parser valida quando possível, mas reforçamos)
  const openNFe = (trimmed.match(/<infNFe[\s>]/g) || []).length;
  const closeNFe = (trimmed.match(/<\/infNFe>/g) || []).length;
  if (openNFe !== closeNFe) {
    return {
      ...baseMeta,
      recordKind: 'INVALIDO',
      isValidXml: false,
      isSaleDocument: false,
      status: 'INVALIDO',
      statusDescription: 'Estrutura XML corrompida: tag <infNFe> não foi fechada corretamente.',
      missingFields: []
    };
  }

  // 1. Caso: Evento de NF-e/NFC-e (procEventoNFe / envEvento / evento / retCancNFe)
  const procEvento = parsed.procEventoNFe || parsed.evento || parsed.retEvento;
  if (procEvento) {
    const eventoObj = procEvento.evento || procEvento;
    const infEvento = eventoObj.infEvento || {};
    const retInfEvento =
      (procEvento.retEvento && procEvento.retEvento.infEvento) ||
      ( parsed.retEvento && parsed.retEvento.infEvento ) ||
      {};

    const tpEvento = String(infEvento.tpEvento || retInfEvento.tpEvento || '');
    const chNFe = cleanAccessKey(infEvento.chNFe || retInfEvento.chNFe || '');
    const dhEventoInfo = normalizeDateAndTime(infEvento.dhEvento || retInfEvento.dhRegEvento);
    const nProt = retInfEvento.nProt || infEvento.detEvento?.nProt || null;
    const cStat = String(retInfEvento.cStat || '135');
    const xMotivo =
      retInfEvento.xMotivo ||
      infEvento.detEvento?.descEvento ||
      (tpEvento === '110111' ? 'Cancelamento de NF-e/NFC-e registrado' : 'Evento Fiscal');
    const xJust = infEvento.detEvento?.xJust || null;

    const isCancelEvent = tpEvento === '110111' || tpEvento === '110112';

    return {
      ...baseMeta,
      recordKind: isCancelEvent ? 'EVENTO_CANCELAMENTO' : 'EVENTO_OUTRO',
      isValidXml: true,
      isSaleDocument: false,
      docType: isCancelEvent ? 'Evento de Cancelamento' : `Evento Fiscal (${tpEvento})`,
      model: chNFe.length === 44 ? chNFe.slice(20, 22) : null,
      accessKey: chNFe,
      number: chNFe.length === 44 ? String(Number(chNFe.slice(25, 34))) : null,
      series: chNFe.length === 44 ? String(Number(chNFe.slice(22, 25))) : null,
      issueDate: dhEventoInfo.dateOnly,
      issueDateTime: dhEventoInfo.iso,
      issueDisplay: dhEventoInfo.display,
      authDate: dhEventoInfo.dateOnly,
      authDateTime: dhEventoInfo.iso,
      authDisplay: dhEventoInfo.display,
      protocol: nProt,
      cStat,
      status: isCancelEvent ? 'CANCELADO' : 'NAO_VENDA',
      statusDescription: `${xMotivo}${xJust ? ` (Justificativa: ${xJust})` : ''}`,
      tpEvento,
      totalValue: 0,
      missingFields: []
    };
  }

  // 2. Caso: Inutilização de Numeração (ProcInutNFe / inutNFe / retInutNFe)
  const inutRoot = parsed.ProcInutNFe || parsed.inutNFe || parsed.retInutNFe;
  if (inutRoot) {
    const infInut =
      inutRoot.inutNFe?.infInut ||
      inutRoot.retInutNFe?.infInut ||
      inutRoot.infInut ||
      {};
    const retInut = inutRoot.retInutNFe?.infInut || {};
    const dhRecbtoInfo = normalizeDateAndTime(retInut.dhRecbto);
    return {
      ...baseMeta,
      recordKind: 'INUTILIZACAO',
      isValidXml: true,
      isSaleDocument: false,
      docType: 'Inutilização de Numeração',
      model: String(infInut.mod || ''),
      modelLabel: MODEL_LABELS[String(infInut.mod || '')] || 'Inutilização Fiscal',
      accessKey: cleanAccessKey(infInut['@_Id'] || ''),
      number:
        infInut.nNFIni && infInut.nNFFin
          ? infInut.nNFIni === infInut.nNFFin
            ? String(infInut.nNFIni)
            : `${infInut.nNFIni}-${infInut.nNFFin}`
          : null,
      series: infInut.serie !== undefined ? String(infInut.serie) : null,
      issueDate: dhRecbtoInfo.dateOnly,
      issueDateTime: dhRecbtoInfo.iso,
      issueDisplay: dhRecbtoInfo.display,
      authDate: dhRecbtoInfo.dateOnly,
      authDateTime: dhRecbtoInfo.iso,
      authDisplay: dhRecbtoInfo.display,
      protocol: retInut.nProt || null,
      cStat: String(retInut.cStat || '102'),
      status: 'INUTILIZADO',
      statusDescription: retInut.xMotivo || infInut.xJust || 'Numeração inutilizada',
      emitCnpjCpf: infInut.CNPJ || null,
      totalValue: 0,
      missingFields: []
    };
  }

  // 3. Caso: CF-e SAT (Modelo 59)
  const cfeRoot = parsed.CFe || parsed.cfeProc?.CFe;
  if (cfeRoot && cfeRoot.infCFe) {
    const infCFe = cfeRoot.infCFe;
    const ide = infCFe.ide || {};
    const emit = infCFe.emit || {};
    const dest = infCFe.dest || {};
    const total = infCFe.total || {};
    const icmsTot = total.ICMSTot || {};
    const accessKey = cleanAccessKey(infCFe['@_Id'] || '');
    const issueInfo = normalizeDateAndTime(ide.dEmi, ide.hEmi);
    const payInfo = extractPayments(null, infCFe.pgto);

    const detList = asArray(infCFe.det).map((det, idx) => {
      const prod = det.prod || {};
      const taxes = extractItemTaxes(det.imposto);
      return {
        itemNumber: det['@_nItem'] || idx + 1,
        code: prod.cProd || '',
        description: prod.xProd || '',
        ncm: prod.NCM || '',
        cfop: prod.CFOP || '',
        unit: prod.uCom || 'UN',
        quantity: toNum(prod.qCom, 1),
        unitPrice: toNum(prod.vUnCom, 0),
        grossValue: toNum(prod.vProd, 0),
        discount: toNum(prod.vDesc, 0),
        otherExpenses: toNum(prod.vOutro, 0),
        freight: 0,
        taxes
      };
    });

    const missingFields = [];
    if (!dest.CNPJ && !dest.CPF) missingFields.push('Destinatário (CNPJ/CPF não informado)');
    if (payInfo.payments.length === 0) missingFields.push('Forma de pagamento');

    return {
      ...baseMeta,
      recordKind: 'DOCUMENTO_FISCAL',
      isValidXml: true,
      isSaleDocument: true,
      docType: 'CF-e SAT',
      model: String(ide.mod || '59'),
      modelLabel: 'CF-e SAT (Modelo 59)',
      accessKey,
      number: String(ide.nCFe || ''),
      series: String(ide.nserieSAT || '1'),
      issueDate: issueInfo.dateOnly,
      issueDateTime: issueInfo.iso,
      issueDisplay: issueInfo.display,
      authDate: issueInfo.dateOnly,
      authDateTime: issueInfo.iso,
      authDisplay: issueInfo.display,
      protocol: ide.assinaturaQRCODE ? 'SAT-ASSINADO' : null,
      cStat: '100',
      status: 'AUTORIZADO',
      statusDescription: 'CF-e SAT emitido e assinado pelo equipamento SAT',
      natOp: 'Venda CF-e SAT',
      tpNF: '1',
      finNFe: '1',
      emitCnpjCpf: emit.CNPJ || emit.CPF || null,
      emitName: emit.xNome || emit.xFant || 'Emitente não informado',
      destCnpjCpf: dest.CNPJ || dest.CPF || null,
      destName: dest.xNome || (dest.CNPJ || dest.CPF ? 'Consumidor Identificado' : 'Consumidor Final Não Identificado'),
      productsValue: toNum(icmsTot.vProd, 0),
      discountValue: toNum(icmsTot.vDesc, 0),
      additionValue: toNum(icmsTot.vOutro, 0),
      freightValue: 0,
      insuranceValue: 0,
      totalTaxesValue: toNum(total.vCFeLei12741, 0),
      totalValue: toNum(total.vCFe || icmsTot.vProd, 0),
      payments: payInfo.payments,
      primaryPayment: payInfo.primaryPaymentLabel,
      troco: payInfo.troco,
      items: detList,
      itemsCount: detList.length,
      missingFields
    };
  }

  // 4. Caso: NF-e (Modelo 55) e NFC-e (Modelo 65) — com ou sem nfeProc
  const nfeProc = parsed.nfeProc || parsed.procNFe || null;
  const nfeNode = (nfeProc && nfeProc.NFe) || parsed.NFe || null;
  const protNFeNode = (nfeProc && nfeProc.protNFe) || parsed.protNFe || null;

  if (!nfeNode || !nfeNode.infNFe) {
    return {
      ...baseMeta,
      recordKind: 'INVALIDO',
      isValidXml: true,
      isSaleDocument: false,
      status: 'INVALIDO',
      statusDescription:
        'O arquivo é um XML sintaticamente válido, mas não contém estrutura de documento fiscal (NF-e, NFC-e, CF-e SAT ou Evento).',
      missingFields: []
    };
  }

  const infNFe = nfeNode.infNFe;
  const ide = infNFe.ide || {};
  const emit = infNFe.emit || {};
  const dest = infNFe.dest || {};
  const total = infNFe.total || {};
  const icmsTot = total.ICMSTot || {};
  const infProt = (protNFeNode && protNFeNode.infProt) || {};

  const model = String(ide.mod || '55');
  const modelLabel = MODEL_LABELS[model] || `Modelo ${model}`;
  const docType = model === '65' ? 'NFC-e' : model === '55' ? 'NF-e' : `Doc Fiscal (${model})`;

  const accessKey = cleanAccessKey(infProt.chNFe || infNFe['@_Id'] || '');
  const number = ide.nNF !== undefined ? String(ide.nNF) : '';
  const series = ide.serie !== undefined ? String(ide.serie) : '';

  const issueInfo = normalizeDateAndTime(ide.dhEmi || ide.dEmi, ide.hSaiEnt);
  const authInfo = normalizeDateAndTime(infProt.dhRecbto);

  const protocol = infProt.nProt ? String(infProt.nProt) : null;
  const cStat = infProt.cStat ? String(infProt.cStat) : null;
  const xMotivo = infProt.xMotivo || null;

  const tpNF = ide.tpNF !== undefined ? String(ide.tpNF) : '1'; // 0 = Entrada, 1 = Saída
  const finNFe = ide.finNFe !== undefined ? String(ide.finNFe) : '1'; // 1=Normal, 2=Complementar, 3=Ajuste, 4=Devolução
  const natOp = ide.natOp || '';

  // Extrai itens e CFOPs
  const detList = asArray(infNFe.det).map((det, idx) => {
    const prod = det.prod || {};
    const taxes = extractItemTaxes(det.imposto);
    return {
      itemNumber: det['@_nItem'] || idx + 1,
      code: prod.cProd || '',
      description: prod.xProd || '',
      ncm: prod.NCM || '',
      cfop: String(prod.CFOP || ''),
      unit: prod.uCom || 'UN',
      quantity: toNum(prod.qCom, 1),
      unitPrice: toNum(prod.vUnCom, 0),
      grossValue: toNum(prod.vProd, 0),
      discount: toNum(prod.vDesc, 0),
      otherExpenses: toNum(prod.vOutro, 0),
      freight: toNum(prod.vFrete, 0),
      taxes
    };
  });

  // Verifica se é realmente um documento de VENDA
  const allNonSaleCfops =
    detList.length > 0 && detList.every((item) => NON_SALE_CFOPS.has(item.cfop));
  const isEntryDoc = tpNF === '0';
  const isReturnDoc = finNFe === '4' || /devolu[cç][aã]o/i.test(natOp);
  const isRemittanceDoc =
    allNonSaleCfops && /(?:remessa|transfer[eê]ncia|simples\s+remessa|comodato|conserto)/i.test(natOp);

  let isSaleDocument = true;
  let nonSaleReason = null;
  if (isEntryDoc) {
    isSaleDocument = false;
    nonSaleReason = `Documento de ENTRADA (tpNF=0, Natureza: "${natOp || 'Entrada'}") — não é uma venda.`;
  } else if (isReturnDoc) {
    isSaleDocument = false;
    nonSaleReason = `Nota Fiscal de DEVOLUÇÃO (finNFe=${finNFe}, Natureza: "${natOp}") — não é uma venda.`;
  } else if (isRemittanceDoc) {
    isSaleDocument = false;
    nonSaleReason = `Documento de REMESSA/TRANSFERÊNCIA (CFOP ${detList[0]?.cfop || ''}, Natureza: "${natOp}") — não é uma venda.`;
  }

  // Determina o status fiscal do documento
  let status = 'AUTORIZADO';
  let statusDescription = xMotivo || 'Autorizado o uso da NF-e';

  if (!cStat && !protocol) {
    status = 'SEM_PROTOCOLO';
    statusDescription = 'XML sem protocolo de autorização SEFAZ (<protNFe> ausente).';
  } else if (cStat === '100' || cStat === '150') {
    status = 'AUTORIZADO';
    statusDescription = xMotivo || (cStat === '150' ? 'Autorizado fora de prazo' : 'Autorizado o uso da NF-e');
  } else if (cStat === '101' || cStat === '151' || cStat === '155' || cStat === '135') {
    status = 'CANCELADO';
    statusDescription = xMotivo || 'Cancelamento de NF-e homologado';
  } else if (cStat === '110' || cStat === '301' || cStat === '302' || cStat === '303') {
    status = 'DENEGADO';
    statusDescription = xMotivo || 'Uso Denegado pela SEFAZ';
  } else {
    status = 'REJEITADO_INVALIDO';
    statusDescription = xMotivo || `Rejeição/Status SEFAZ cStat=${cStat}`;
  }

  if (!isSaleDocument && status === 'AUTORIZADO') {
    status = 'NAO_VENDA';
    statusDescription = nonSaleReason;
  }

  const payInfo = extractPayments(infNFe.pag, null);

  // Registro explícito de informações ausentes no XML
  const missingFields = [];
  if (!accessKey || accessKey.length !== 44) {
    missingFields.push('Chave de acesso de 44 dígitos');
  }
  if (!number) {
    missingFields.push('Número do documento (nNF)');
  }
  if (!series) {
    missingFields.push('Série do documento (serie)');
  }
  if (!issueInfo.dateOnly) {
    missingFields.push('Data de emissão (dhEmi/dEmi)');
  }
  if (!authInfo.dateOnly) {
    missingFields.push('Data/hora de autorização (dhRecbto)');
  }
  if (!protocol) {
    missingFields.push('Protocolo de autorização (nProt)');
  }
  if (!emit.CNPJ && !emit.CPF) {
    missingFields.push('CNPJ/CPF do emitente');
  }
  if (!dest.CNPJ && !dest.CPF && !dest.idEstrangeiro) {
    missingFields.push('CNPJ/CPF do destinatário (consumidor não identificado)');
  }
  if (payInfo.payments.length === 0) {
    missingFields.push('Forma de pagamento (grupo <pag> ausente)');
  }
  if (detList.length === 0) {
    missingFields.push('Itens da venda (<det> ausente)');
  }

  const totalValue = toNum(icmsTot.vNF, 0);
  const productsValue = toNum(icmsTot.vProd, 0);
  const discountValue = toNum(icmsTot.vDesc, 0);
  const additionValue = toNum(icmsTot.vOutro, 0);
  const freightValue = toNum(icmsTot.vFrete, 0);
  const insuranceValue = toNum(icmsTot.vSeg, 0);
  const totalTaxesValue = toNum(icmsTot.vTotTrib, 0);

  return {
    ...baseMeta,
    recordKind: isSaleDocument ? 'DOCUMENTO_FISCAL' : 'NAO_VENDA',
    isValidXml: true,
    isSaleDocument,
    nonSaleReason,
    docType,
    model,
    modelLabel,
    accessKey,
    number,
    series,
    issueDate: issueInfo.dateOnly,
    issueDateTime: issueInfo.iso,
    issueDisplay: issueInfo.display,
    authDate: authInfo.dateOnly,
    authDateTime: authInfo.iso,
    authDisplay: authInfo.display,
    protocol,
    cStat,
    status,
    statusDescription,
    natOp,
    tpNF,
    finNFe,
    emitCnpjCpf: emit.CNPJ || emit.CPF || null,
    emitName: emit.xNome || emit.xFant || 'Emitente não informado',
    emitUf: emit.enderEmit?.UF || null,
    destCnpjCpf: dest.CNPJ || dest.CPF || dest.idEstrangeiro || null,
    destName:
      dest.xNome ||
      (dest.CNPJ || dest.CPF ? 'Destinatário Identificado' : 'Consumidor Final Não Identificado'),
    productsValue,
    discountValue,
    additionValue,
    freightValue,
    insuranceValue,
    totalTaxesValue,
    totalValue,
    payments: payInfo.payments,
    primaryPayment: payInfo.primaryPaymentLabel,
    troco: payInfo.troco,
    items: detList,
    itemsCount: detList.length,
    missingFields
  };
}

/**
 * Processa todos os XMLs extraídos, correlaciona eventos de cancelamento (110111)
 * às suas respectivas chaves de acesso e detecta duplicidades.
 */
function analyzeExtractedXmls(xmlEntries = []) {
  const parsedRecords = [];
  const cancelEventsByKey = new Map();
  const fileErrors = [];

  for (const entry of xmlEntries) {
    const rec = parseSingleXmlEntry(entry);
    parsedRecords.push(rec);

    if (!rec.isValidXml || rec.recordKind === 'INVALIDO') {
      fileErrors.push({
        fileName: rec.fileName,
        internalPath: rec.displayPath,
        stage: 'validacao_xml',
        error: rec.statusDescription
      });
    } else if (rec.recordKind === 'NAO_VENDA') {
      fileErrors.push({
        fileName: rec.fileName,
        internalPath: rec.displayPath,
        stage: 'filtro_nao_venda',
        error: rec.nonSaleReason || 'XML não corresponde a uma operação de venda.'
      });
    } else if (rec.recordKind === 'EVENTO_CANCELAMENTO' && rec.accessKey) {
      cancelEventsByKey.set(rec.accessKey, rec);
    }
  }

  // Separa os documentos principais dos arquivos puramente de evento
  const documents = [];
  const standaloneEvents = [];
  const matchedCancelKeys = new Set();

  for (const rec of parsedRecords) {
    if (rec.recordKind === 'EVENTO_CANCELAMENTO') {
      standaloneEvents.push(rec);
      continue;
    }
    if (rec.recordKind === 'EVENTO_OUTRO') {
      standaloneEvents.push(rec);
      continue;
    }

    // Se existe um XML de evento de cancelamento (110111) para esta chave de acesso, atualiza o status para CANCELADO
    if (rec.accessKey && cancelEventsByKey.has(rec.accessKey)) {
      const cancelEvt = cancelEventsByKey.get(rec.accessKey);
      matchedCancelKeys.add(rec.accessKey);
      rec.status = 'CANCELADO';
      rec.cancelProtocol = cancelEvt.protocol || rec.protocol;
      rec.cancelDateTime = cancelEvt.issueDisplay || cancelEvt.issueDateTime;
      rec.cancelEventFile = cancelEvt.displayPath;
      rec.statusDescription = `Cancelado via evento vinculado (${cancelEvt.fileName}): ${cancelEvt.statusDescription}`;
    }

    documents.push(rec);
  }

  // Caso haja um XML de evento de cancelamento cujo XML original da nota não estava no pacote,
  // registra-o como documento cancelado para que seja confrontado com o PDF e Firebird!
  for (const evt of standaloneEvents) {
    if (evt.recordKind === 'EVENTO_CANCELAMENTO' && !matchedCancelKeys.has(evt.accessKey)) {
      documents.push({
        ...evt,
        recordKind: 'DOCUMENTO_FISCAL_EVENTO_ORFÃO',
        isSaleDocument: true,
        status: 'CANCELADO',
        statusDescription: `${evt.statusDescription} (Apenas XML do evento de cancelamento presente no pacote; XML da nota original ausente)`,
        missingFields: ['XML original da NF-e/NFC-e (apenas evento de cancelamento encontrado)']
      });
    }
  }

  // Detecção de duplicidades entre os XMLs
  const byAccessKey = new Map();
  const byNumberSeries = new Map();
  const bySha256 = new Map();

  for (const doc of documents) {
    doc.isDuplicate = false;
    doc.duplicateType = null;
    doc.duplicateOf = null;
    doc.duplicateReason = null;

    if (!doc.isValidXml || doc.recordKind === 'INVALIDO') continue;

    // 1. Duplicidade exata de binário (mesmo SHA-256 em caminhos/arquivos distintos)
    if (bySha256.has(doc.sha256)) {
      const first = bySha256.get(doc.sha256);
      doc.isDuplicate = true;
      doc.duplicateType = 'ARQUIVO_IDENTICO';
      doc.duplicateOf = first.displayPath;
      doc.duplicateReason = `Arquivo XML idêntico (mesmo hash SHA-256) já importado em "${first.displayPath}".`;
      first.hasDuplicates = true;
      continue;
    }
    bySha256.set(doc.sha256, doc);

    // 2. Mesma chave de acesso de 44 dígitos em mais de um XML de nota
    if (doc.accessKey && doc.accessKey.length === 44) {
      if (byAccessKey.has(doc.accessKey)) {
        const first = byAccessKey.get(doc.accessKey);
        doc.isDuplicate = true;
        doc.duplicateType = 'CHAVE_ACESSO_DUPLICADA';
        doc.duplicateOf = first.displayPath;
        doc.duplicateReason = `Chave de acesso ${doc.accessKey} duplicada (já presente em "${first.displayPath}").`;
        first.hasDuplicates = true;
        continue;
      }
      byAccessKey.set(doc.accessKey, doc);
    }

    // 3. Mesmo Modelo + Série + Número + CNPJ emitente com chaves diferentes
    if (doc.number && doc.series) {
      const numKey = `${doc.emitCnpjCpf || 'SEM_EMIT'}|${doc.model || ''}|${Number(doc.series)}|${Number(doc.number)}`;
      if (byNumberSeries.has(numKey)) {
        const first = byNumberSeries.get(numKey);
        doc.isDuplicate = true;
        doc.duplicateType = 'NUMERO_SERIE_DUPLICADO';
        doc.duplicateOf = first.displayPath;
        doc.duplicateReason = `Mesmo Número (${doc.number}) e Série (${doc.series}) já encontrados no arquivo "${first.displayPath}" com chave distinta.`;
        first.hasDuplicates = true;
        continue;
      }
      byNumberSeries.set(numKey, doc);
    }
  }

  // Estatísticas consolidadas da leitura de XMLs
  const summary = {
    totalFilesExtracted: xmlEntries.length,
    totalDocumentsParsed: documents.length,
    authorizedSalesCount: documents.filter((d) => d.isSaleDocument && d.status === 'AUTORIZADO' && !d.isDuplicate).length,
    authorizedSalesTotalValue: Number(
      documents
        .filter((d) => d.isSaleDocument && d.status === 'AUTORIZADO' && !d.isDuplicate)
        .reduce((acc, d) => acc + (d.totalValue || 0), 0)
        .toFixed(2)
    ),
    canceledCount: documents.filter((d) => d.status === 'CANCELADO').length,
    deniedCount: documents.filter((d) => d.status === 'DENEGADO').length,
    inutilizedCount: documents.filter((d) => d.status === 'INUTILIZADO').length,
    nonSaleCount: documents.filter((d) => d.recordKind === 'NAO_VENDA').length,
    invalidCount: documents.filter((d) => !d.isValidXml || d.status === 'INVALIDO' || d.status === 'SEM_PROTOCOLO').length,
    duplicateCount: documents.filter((d) => d.isDuplicate).length,
    linkedCancelEventsCount: matchedCancelKeys.size
  };

  return {
    documents,
    standaloneEvents,
    validationAlerts: fileErrors,
    summary
  };
}

module.exports = {
  SEFAZ_PAYMENT_LABELS,
  MODEL_LABELS,
  parseSingleXmlEntry,
  analyzeExtractedXmls
};
