const CATEGORY_DEFINITIONS = {
  CONCILIADO: {
    code: 'CONCILIADO',
    label: 'Conciliados',
    badgeClass: 'badge-success',
    severity: 'ok',
    description: 'Registros com correspondência exata de identificação, data, valor e status entre as fontes.'
  },
  SOMENTE_XML: {
    code: 'SOMENTE_XML',
    label: 'Encontrados somente nos XMLs',
    badgeClass: 'badge-warning',
    severity: 'warning',
    description: 'Documentos fiscais presentes nos arquivos XML, mas ausentes no relatório em PDF.'
  },
  SOMENTE_PDF: {
    code: 'SOMENTE_PDF',
    label: 'Encontrados somente no PDF',
    badgeClass: 'badge-warning',
    severity: 'warning',
    description: 'Vendas listadas no relatório em PDF sem o respectivo arquivo XML correspondente.'
  },
  DIVERGENCIA_DATA: {
    code: 'DIVERGENCIA_DATA',
    label: 'Divergências de data',
    badgeClass: 'badge-amber',
    severity: 'divergence',
    description: 'Documento localizado nas fontes, porém com data divergente segundo o critério selecionado (Emissão ou Autorização).'
  },
  DIVERGENCIA_VALOR: {
    code: 'DIVERGENCIA_VALOR',
    label: 'Divergências de valor',
    badgeClass: 'badge-danger',
    severity: 'divergence',
    description: 'Documento localizado nas fontes, porém com diferença no valor total da venda.'
  },
  DIVERGENCIA_IDENTIFICACAO: {
    code: 'DIVERGENCIA_IDENTIFICACAO',
    label: 'Divergências de número, série ou chave',
    badgeClass: 'badge-purple',
    severity: 'divergence',
    description: 'Inconsistência entre número do documento, série ou chave de acesso de 44 dígitos entre as fontes.'
  },
  CANCELADO_DENEGADO_INVALIDO: {
    code: 'CANCELADO_DENEGADO_INVALIDO',
    label: 'Cancelados, denegados ou inválidos',
    badgeClass: 'badge-neutral-alert',
    severity: 'critical',
    description: 'Documentos cancelados, denegados, inutilizados, sem protocolo, não-venda ou com estrutura XML inválida.'
  },
  POSSIVEL_DUPLICIDADE: {
    code: 'POSSIVEL_DUPLICIDADE',
    label: 'Possíveis duplicidades',
    badgeClass: 'badge-duplicate',
    severity: 'warning',
    description: 'Arquivos XML duplicados ou lançamentos repetidos com mesma chave ou numeração.'
  },
  REVISAO_MANUAL: {
    code: 'REVISAO_MANUAL',
    label: 'Registros que precisam de revisão manual',
    badgeClass: 'badge-review',
    severity: 'review',
    description: 'Casos ambíguos, divergência de forma de pagamento, conflito com banco Firebird ou candidatos que exigem decisão do auditor.'
  }
};

function normNum(val) {
  if (val === undefined || val === null || val === '') return '';
  const digits = String(val).replace(/\D/g, '');
  return digits ? String(Number(digits)) : '';
}

function normSeries(val) {
  if (val === undefined || val === null || val === '') return '1';
  const digits = String(val).replace(/\D/g, '');
  return digits ? String(Number(digits)) : '1';
}

function normPaymentCompare(p) {
  if (!p) return '';
  const s = String(p)
    .toLowerCase()
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .trim();
  if (s.includes('nao informado') || s === '-' || s === '') return '';
  if (s.includes('pix')) return 'pix';
  if (s.includes('credito')) return 'cartao_credito';
  if (s.includes('debito')) return 'cartao_debito';
  if (s.includes('dinheiro') || s.includes('especie')) return 'dinheiro';
  if (s.includes('boleto')) return 'boleto';
  if (s.includes('vale refeic') || s === 'vr') return 'vale_refeicao';
  if (s.includes('vale aliment') || s === 'va') return 'vale_alimentacao';
  return s;
}

function formatBrDate(isoDate) {
  if (!isoDate || typeof isoDate !== 'string') return '—';
  const m = isoDate.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (m) return `${m[3]}/${m[2]}/${m[1]}`;
  return isoDate;
}

/**
 * Executa a conciliação completa entre os XMLs processados, os registros do PDF e (opcionalmente) os registros do Firebird.
 *
 * @param {Object} params
 * @param {Array} params.xmlDocuments - Documentos extraídos dos XMLs
 * @param {Array} params.pdfRecords - Registros extraídos/revisados do PDF
 * @param {Array} params.firebirdRecords - Registros lidos do Firebird (opcional)
 * @param {Object} params.options - { dateField: 'issueDate' | 'authDate', comparePayment: true, firebirdEnabled: false }
 * @param {Object} params.manualDecisions - Mapa de decisões manuais tomadas pelo usuário { [itemId]: decision }
 */
function runReconciliation({
  xmlDocuments = [],
  pdfRecords = [],
  firebirdRecords = [],
  options = {},
  manualDecisions = {}
}) {
  const dateField = options.dateField === 'authDate' ? 'authDate' : 'issueDate';
  const dateFieldLabel = dateField === 'authDate' ? 'Data de Autorização' : 'Data de Emissão';
  const comparePayment = options.comparePayment !== false;
  const firebirdEnabled = Boolean(options.firebirdEnabled && firebirdRecords && firebirdRecords.length > 0);

  const items = [];
  const usedPdfIds = new Set();
  const usedFbIds = new Set();

  // Indexa registros do PDF para busca determinística
  const pdfByAccessKey = new Map();
  const pdfByNumSeries = new Map();
  const pdfByNumOnly = new Map();
  const pdfDuplicateIds = new Set();

  for (const pdfRec of pdfRecords) {
    const key = (pdfRec.accessKey || '').replace(/\D/g, '');
    const num = normNum(pdfRec.number);
    const ser = normSeries(pdfRec.series);
    const numSerKey = `${num}|${ser}`;

    if (key.length === 44) {
      if (pdfByAccessKey.has(key)) {
        pdfDuplicateIds.add(pdfRec.id);
      } else {
        pdfByAccessKey.set(key, pdfRec);
      }
    }

    if (num) {
      if (pdfByNumSeries.has(numSerKey)) {
        // Se tem mesmo número e série no PDF, marca duplicidade no PDF
        pdfDuplicateIds.add(pdfRec.id);
      } else {
        pdfByNumSeries.set(numSerKey, pdfRec);
      }
      if (!pdfByNumOnly.has(num)) {
        pdfByNumOnly.set(num, []);
      }
      pdfByNumOnly.get(num).push(pdfRec);
    }
  }

  // Indexa registros do Firebird quando habilitado
  const fbByAccessKey = new Map();
  const fbByNumSeries = new Map();
  const fbByNumOnly = new Map();

  if (firebirdEnabled) {
    for (const fbRec of firebirdRecords) {
      const key = (fbRec.accessKey || '').replace(/\D/g, '');
      const num = normNum(fbRec.number);
      const ser = normSeries(fbRec.series);
      const numSerKey = `${num}|${ser}`;

      if (key.length === 44 && !fbByAccessKey.has(key)) {
        fbByAccessKey.set(key, fbRec);
      }
      if (num) {
        if (!fbByNumSeries.has(numSerKey)) {
          fbByNumSeries.set(numSerKey, fbRec);
        }
        if (!fbByNumOnly.has(num)) {
          fbByNumOnly.set(num, []);
        }
        fbByNumOnly.get(num).push(fbRec);
      }
    }
  }

  /**
   * Localiza registro correspondente no Firebird para um documento XML ou PDF.
   */
  function matchFirebirdRecord(doc) {
    if (!firebirdEnabled) return { fbRec: null, fbMatchCriterion: null };
    const key = (doc.accessKey || '').replace(/\D/g, '');
    const num = normNum(doc.number);
    const ser = normSeries(doc.series);

    if (key.length === 44 && fbByAccessKey.has(key)) {
      const fbRec = fbByAccessKey.get(key);
      usedFbIds.add(fbRec.id);
      return { fbRec, fbMatchCriterion: 'Chave de acesso exata (44 dígitos) no Firebird' };
    }
    if (num && fbByNumSeries.has(`${num}|${ser}`)) {
      const fbRec = fbByNumSeries.get(`${num}|${ser}`);
      usedFbIds.add(fbRec.id);
      return { fbRec, fbMatchCriterion: `Número (${num}) + Série (${ser}) exatos no Firebird` };
    }
    if (num && fbByNumOnly.has(num)) {
      const candidates = fbByNumOnly.get(num);
      const fbRec = candidates[0];
      usedFbIds.add(fbRec.id);
      return {
        fbRec,
        fbMatchCriterion: `Número (${num}) localizado no Firebird com Série divergente (${fbRec.series})`
      };
    }
    return { fbRec: null, fbMatchCriterion: 'Não localizado no Firebird' };
  }

  // 1. Processa todos os documentos vindos dos XMLs
  for (const xmlDoc of xmlDocuments) {
    // 1.a) Duplicidades de XML
    if (xmlDoc.isDuplicate) {
      items.push(
        buildReconciliationItem({
          id: `rec_${xmlDoc.id}`,
          category: 'POSSIVEL_DUPLICIDADE',
          xmlDoc,
          pdfRec: null,
          fbRec: null,
          dateField,
          matchCriterion: 'Detecção de duplicidade entre arquivos XML importados',
          reasons: [xmlDoc.duplicateReason || 'Arquivo XML duplicado no pacote importado.'],
          ambiguousCandidates: [],
          firebirdEnabled,
          manualDecisions
        })
      );
      continue;
    }

    // 1.b) XMLs inválidos, sem protocolo ou que não são de venda (entrada/devolução/remessa/inutilização)
    if (
      !xmlDoc.isValidXml ||
      xmlDoc.recordKind === 'INVALIDO' ||
      xmlDoc.recordKind === 'NAO_VENDA' ||
      xmlDoc.recordKind === 'INUTILIZACAO' ||
      xmlDoc.status === 'INVALIDO' ||
      xmlDoc.status === 'SEM_PROTOCOLO' ||
      xmlDoc.status === 'INUTILIZADO'
    ) {
      const { fbRec, fbMatchCriterion } = matchFirebirdRecord(xmlDoc);
      items.push(
        buildReconciliationItem({
          id: `rec_${xmlDoc.id}`,
          category: 'CANCELADO_DENEGADO_INVALIDO',
          xmlDoc,
          pdfRec: null,
          fbRec,
          dateField,
          matchCriterion:
            xmlDoc.recordKind === 'NAO_VENDA'
              ? 'Filtro de operação fiscal (Não é documento de venda)'
              : xmlDoc.recordKind === 'INUTILIZACAO'
              ? 'Registro de Inutilização de Numeração SEFAZ'
              : 'Validação estrutural e protocolo SEFAZ do XML',
          fbMatchCriterion,
          reasons: [xmlDoc.statusDescription || 'Documento inválido ou não correspondente a venda.'],
          ambiguousCandidates: [],
          firebirdEnabled,
          manualDecisions
        })
      );
      continue;
    }

    // 1.c) Procura correspondência determinística no PDF
    const xmlKey = (xmlDoc.accessKey || '').replace(/\D/g, '');
    const xmlNum = normNum(xmlDoc.number);
    const xmlSer = normSeries(xmlDoc.series);
    const numSerKey = `${xmlNum}|${xmlSer}`;

    let matchedPdf = null;
    let matchCriterion = '';
    let identificationDivergence = null;

    // Tentativa 1: Chave de acesso de 44 dígitos nas duas fontes
    if (xmlKey.length === 44 && pdfByAccessKey.has(xmlKey)) {
      const candidate = pdfByAccessKey.get(xmlKey);
      if (!usedPdfIds.has(candidate.id)) {
        matchedPdf = candidate;
        usedPdfIds.add(candidate.id);
        matchCriterion = 'Correspondência exata por Chave de Acesso (44 dígitos)';

        // Verifica se Número ou Série divergem apesar da mesma chave
        const pdfNum = normNum(candidate.number);
        const pdfSer = normSeries(candidate.series);
        if ((pdfNum && pdfNum !== xmlNum) || (pdfSer && pdfSer !== xmlSer)) {
          identificationDivergence = `Mesma chave de acesso, porém identificação divergente (XML: Nº ${xmlNum} Série ${xmlSer} vs PDF: Nº ${pdfNum} Série ${pdfSer}).`;
        }
      }
    }

    // Tentativa 2: Número + Série exatos
    if (!matchedPdf && xmlNum && pdfByNumSeries.has(numSerKey)) {
      const candidate = pdfByNumSeries.get(numSerKey);
      if (!usedPdfIds.has(candidate.id)) {
        matchedPdf = candidate;
        usedPdfIds.add(candidate.id);
        matchCriterion = `Correspondência exata por Número (${xmlNum}) e Série (${xmlSer})`;

        const pdfKey = (candidate.accessKey || '').replace(/\D/g, '');
        if (pdfKey.length === 44 && xmlKey.length === 44 && pdfKey !== xmlKey) {
          identificationDivergence = `Mesmo Número (${xmlNum}) e Série (${xmlSer}), porém Chave de Acesso divergente entre XML (${xmlKey}) e PDF (${pdfKey}).`;
        }
      }
    }

    // Tentativa 3: Mesmo Número de documento, porém Série diferente no PDF
    if (!matchedPdf && xmlNum && pdfByNumOnly.has(xmlNum)) {
      const candidates = pdfByNumOnly.get(xmlNum).filter((c) => !usedPdfIds.has(c.id));
      if (candidates.length === 1) {
        matchedPdf = candidates[0];
        usedPdfIds.add(matchedPdf.id);
        matchCriterion = `Correspondência por Número do documento (${xmlNum}), com Série divergente (XML: Série ${xmlSer} vs PDF: Série ${normSeries(matchedPdf.series)})`;
        identificationDivergence = `Divergência de Série para o documento Nº ${xmlNum}: no XML consta Série "${xmlSer}", enquanto no relatório PDF consta Série "${normSeries(matchedPdf.series)}".`;
      }
    }

    // Confronta também com Firebird (se ativo)
    const { fbRec, fbMatchCriterion } = matchFirebirdRecord(xmlDoc);

    // Se o XML é CANCELADO ou DENEGADO
    if (xmlDoc.status === 'CANCELADO' || xmlDoc.status === 'DENEGADO') {
      const reasons = [
        `Documento com status ${xmlDoc.status} no XML (${xmlDoc.statusDescription}).`
      ];
      if (matchedPdf) {
        if (matchedPdf.status === 'AUTORIZADO') {
          reasons.push(
            `ALERTA CRÍTICO: Este documento está ${xmlDoc.status} no XML, mas consta como AUTORIZADO (R$ ${Number(
              matchedPdf.totalValue
            ).toFixed(2)}) no relatório em PDF!`
          );
        } else {
          reasons.push(`No relatório PDF o documento também consta como ${matchedPdf.status}.`);
        }
      } else {
        reasons.push('Documento não listado no relatório em PDF.');
      }

      if (firebirdEnabled) {
        if (fbRec) {
          if (fbRec.status === 'AUTORIZADO') {
            reasons.push(
              `ALERTA FIREBIRD: No banco Firebird este documento consta como AUTORIZADO (R$ ${Number(
                fbRec.totalValue
              ).toFixed(2)})!`
            );
          } else {
            reasons.push(`No banco Firebird o documento consta com status ${fbRec.status}.`);
          }
        } else {
          reasons.push('Registro não encontrado no banco Firebird.');
        }
      }

      items.push(
        buildReconciliationItem({
          id: `rec_${xmlDoc.id}`,
          category: 'CANCELADO_DENEGADO_INVALIDO',
          xmlDoc,
          pdfRec: matchedPdf,
          fbRec,
          dateField,
          matchCriterion: matchedPdf
            ? `${matchCriterion} (Status: ${xmlDoc.status})`
            : `Análise de status fiscal do XML (${xmlDoc.status})`,
          fbMatchCriterion,
          reasons,
          ambiguousCandidates: [],
          firebirdEnabled,
          manualDecisions
        })
      );
      continue;
    }

    // Se NÃO encontrou correspondente determinístico no PDF:
    if (!matchedPdf) {
      // Verifica se existe algum candidato ambíguo no PDF (ex: mesmo valor na mesma data, mas número diferente)
      // REGRA: NÃO faz correspondência silenciosa por valor aproximado ou igual sem identificação!
      const xmlTargetDate = xmlDoc[dateField] || xmlDoc.issueDate;
      const ambiguousCandidates = pdfRecords
        .filter((p) => !usedPdfIds.has(p.id))
        .filter((p) => {
          const pDate = p[dateField] || p.issueDate;
          const valDiff = Math.abs(Number(p.totalValue || 0) - Number(xmlDoc.totalValue || 0));
          return (pDate === xmlTargetDate && valDiff <= 1.0) || valDiff < 0.01;
        })
        .slice(0, 3)
        .map((p) => ({
          pdfId: p.id,
          number: p.number,
          series: p.series,
          date: p.issueDate,
          totalValue: p.totalValue,
          paymentMethod: p.paymentMethod,
          reason: `Candidato não vinculado automaticamente: Nº ${p.number} (Série ${p.series}) no PDF possui valor R$ ${Number(
            p.totalValue
          ).toFixed(2)} em ${formatBrDate(p.issueDate)}, mas número/identificação diferente do XML (Nº ${xmlNum}).`
        }));

      const reasons = [
        `Documento Nº ${xmlNum} (Série ${xmlSer}) presente no XML (R$ ${Number(xmlDoc.totalValue).toFixed(
          2
        )}), mas não encontrado no relatório em PDF.`
      ];

      if (firebirdEnabled) {
        if (fbRec) {
          reasons.push(
            `Firebird: Documento localizado no banco Firebird (Nº ${fbRec.number}, Série ${
              fbRec.series
            }, Valor R$ ${Number(fbRec.totalValue).toFixed(2)}, Status ${fbRec.status}).`
          );
        } else {
          reasons.push('Firebird: Documento também não foi localizado no banco Firebird.');
        }
      }

      if (ambiguousCandidates.length > 0) {
        reasons.push(
          `Atenção: Foi identificado ${ambiguousCandidates.length} registro(s) no PDF com valor igual/próximo, porém com número distinto. Conforme regra de auditoria, nenhuma associação silenciosa por valor foi feita.`
        );
      }

      items.push(
        buildReconciliationItem({
          id: `rec_${xmlDoc.id}`,
          category: ambiguousCandidates.length > 0 ? 'REVISAO_MANUAL' : 'SOMENTE_XML',
          xmlDoc,
          pdfRec: null,
          fbRec,
          dateField,
          matchCriterion:
            ambiguousCandidates.length > 0
              ? 'Sem correspondência exata de Número/Chave (Candidato ambíguo retido para Revisão Manual)'
              : 'Busca por Chave de Acesso e Número+Série sem correspondência no PDF',
          fbMatchCriterion,
          reasons,
          ambiguousCandidates,
          firebirdEnabled,
          manualDecisions
        })
      );
      continue;
    }

    // Encontrou par XML <-> PDF! Agora compara todas as dimensões:
    const reasons = [];
    const divergenceFlags = {
      identification: false,
      value: false,
      date: false,
      status: false,
      payment: false,
      firebird: false
    };

    if (identificationDivergence) {
      divergenceFlags.identification = true;
      reasons.push(identificationDivergence);
    }

    // Comparação de Status (ex: Autorizado no XML vs Cancelado no PDF)
    if (matchedPdf.status !== xmlDoc.status) {
      divergenceFlags.status = true;
      reasons.push(
        `Divergência de Status: no XML consta como "${xmlDoc.status}", mas no relatório PDF consta como "${matchedPdf.status}".`
      );
    }

    // Comparação de Valor Total (Tolerância zero para diferenças >= R$ 0,01)
    const xmlVal = Number(Number(xmlDoc.totalValue || 0).toFixed(2));
    const pdfVal = Number(Number(matchedPdf.totalValue || 0).toFixed(2));
    const valDiff = Number((xmlVal - pdfVal).toFixed(2));

    if (Math.abs(valDiff) >= 0.01) {
      divergenceFlags.value = true;
      reasons.push(
        `Divergência de Valor Total: XML = R$ ${xmlVal.toFixed(2)} | PDF = R$ ${pdfVal.toFixed(
          2
        )} (Diferença de R$ ${Math.abs(valDiff).toFixed(2)}).`
      );
      if (
        xmlDoc.discountValue > 0 &&
        Math.abs(xmlDoc.productsValue - pdfVal) < 0.01
      ) {
        reasons.push(
          `Possível causa: O PDF parece exibir o Valor Bruto dos Produtos (R$ ${xmlDoc.productsValue.toFixed(
            2
          )}) sem abater o desconto de R$ ${xmlDoc.discountValue.toFixed(2)} destacado no XML.`
        );
      } else if (
        (xmlDoc.additionValue > 0 || xmlDoc.freightValue > 0) &&
        Math.abs(xmlDoc.productsValue - pdfVal) < 0.01
      ) {
        reasons.push(
          `Possível causa: O PDF exibe o valor sem somar frete/acréscimos (R$ ${(
            xmlDoc.additionValue + xmlDoc.freightValue
          ).toFixed(2)}) presentes no XML.`
        );
      }
    }

    // Comparação de Data (considerando o modo escolhido pelo usuário: issueDate vs authDate)
    const xmlSelectedDate =
      dateField === 'authDate'
        ? xmlDoc.authDate || xmlDoc.issueDate
        : xmlDoc.issueDate;
    const pdfSelectedDate =
      dateField === 'authDate'
        ? matchedPdf.authDate || matchedPdf.issueDate
        : matchedPdf.issueDate;

    if (xmlSelectedDate && pdfSelectedDate && xmlSelectedDate !== pdfSelectedDate) {
      divergenceFlags.date = true;
      const otherXmlDate = dateField === 'authDate' ? xmlDoc.issueDate : xmlDoc.authDate;
      let extraDateHint = '';
      if (otherXmlDate && otherXmlDate === pdfSelectedDate) {
        extraDateHint = ` (Nota: A data do PDF coincide com a ${
          dateField === 'authDate' ? 'Data de Emissão' : 'Data de Autorização'
        } do XML: ${formatBrDate(otherXmlDate)}).`;
      }
      reasons.push(
        `Divergência de Data (${dateFieldLabel}): XML = ${formatBrDate(
          xmlSelectedDate
        )} | PDF = ${formatBrDate(pdfSelectedDate)}.${extraDateHint}`
      );
    }

    // Comparação de Forma de Pagamento (quando informada nas duas fontes)
    if (comparePayment) {
      const xmlPayNorm = normPaymentCompare(xmlDoc.primaryPayment);
      const pdfPayNorm = normPaymentCompare(matchedPdf.paymentMethod);
      if (xmlPayNorm && pdfPayNorm && xmlPayNorm !== pdfPayNorm) {
        divergenceFlags.payment = true;
        reasons.push(
          `Divergência na Forma de Pagamento: no XML consta "${xmlDoc.primaryPayment}", enquanto no PDF consta "${matchedPdf.paymentMethod}".`
        );
      }
    }

    // Confronto com Firebird (quando ativo)
    if (firebirdEnabled) {
      if (!fbRec) {
        divergenceFlags.firebird = true;
        reasons.push(
          `Divergência Firebird: Documento Nº ${xmlNum} está presente no XML e no PDF, mas NÃO existe no banco de dados Firebird.`
        );
      } else {
        const fbVal = Number(Number(fbRec.totalValue || 0).toFixed(2));
        if (Math.abs(xmlVal - fbVal) >= 0.01) {
          divergenceFlags.firebird = true;
          divergenceFlags.value = true;
          reasons.push(
            `Divergência de Valor no Firebird: XML = R$ ${xmlVal.toFixed(
              2
            )} | Firebird = R$ ${fbVal.toFixed(2)} (Diferença de R$ ${Math.abs(
              xmlVal - fbVal
            ).toFixed(2)}).`
          );
        }
        if (fbRec.status !== xmlDoc.status) {
          divergenceFlags.firebird = true;
          reasons.push(
            `Divergência de Status no Firebird: XML = ${xmlDoc.status} | Firebird = ${fbRec.status}.`
          );
        }
        const fbDate = dateField === 'authDate' ? fbRec.authDate || fbRec.issueDate : fbRec.issueDate;
        if (xmlSelectedDate && fbDate && xmlSelectedDate !== fbDate) {
          divergenceFlags.firebird = true;
          reasons.push(
            `Divergência de Data no Firebird (${dateFieldLabel}): XML = ${formatBrDate(
              xmlSelectedDate
            )} | Firebird = ${formatBrDate(fbDate)}.`
          );
        }
        if (normSeries(fbRec.series) !== xmlSer) {
          divergenceFlags.firebird = true;
          divergenceFlags.identification = true;
          reasons.push(
            `Divergência de Série no Firebird: XML = Série ${xmlSer} | Firebird = Série ${fbRec.series}.`
          );
        }
      }
    }

    // Determina a categoria principal de classificação
    let category = 'CONCILIADO';

    if (divergenceFlags.status && matchedPdf.status === 'CANCELADO') {
      category = 'CANCELADO_DENEGADO_INVALIDO';
    } else if (divergenceFlags.identification) {
      category = 'DIVERGENCIA_IDENTIFICACAO';
    } else if (divergenceFlags.value) {
      category = 'DIVERGENCIA_VALOR';
    } else if (divergenceFlags.date) {
      category = 'DIVERGENCIA_DATA';
    } else if (divergenceFlags.payment || divergenceFlags.firebird || divergenceFlags.status) {
      category = 'REVISAO_MANUAL';
    } else {
      category = 'CONCILIADO';
      reasons.push(
        `Conciliado com sucesso (${matchCriterion}). Data (${dateFieldLabel}), valor (R$ ${xmlVal.toFixed(
          2
        )}) e status (${xmlDoc.status}) conferem integralmente${
          firebirdEnabled && fbRec ? ' entre XML, PDF e Firebird' : ' entre XML e PDF'
        }.`
      );
    }

    items.push(
      buildReconciliationItem({
        id: `rec_${xmlDoc.id}`,
        category,
        xmlDoc,
        pdfRec: matchedPdf,
        fbRec,
        dateField,
        matchCriterion,
        fbMatchCriterion,
        reasons,
        ambiguousCandidates: [],
        firebirdEnabled,
        manualDecisions
      })
    );
  }

  // 2. Processa os registros do PDF que não foram vinculados a nenhum XML
  for (const pdfRec of pdfRecords) {
    if (usedPdfIds.has(pdfRec.id)) continue;

    const { fbRec, fbMatchCriterion } = matchFirebirdRecord(pdfRec);
    const isPdfDup = pdfDuplicateIds.has(pdfRec.id);

    const reasons = [];
    if (isPdfDup) {
      reasons.push(
        `Linha duplicada no relatório PDF para o documento Nº ${pdfRec.number} (Série ${pdfRec.series}).`
      );
    } else {
      reasons.push(
        `Venda Nº ${pdfRec.number} (Série ${pdfRec.series}, Valor R$ ${Number(
          pdfRec.totalValue || 0
        ).toFixed(2)}) consta no relatório em PDF, mas nenhum arquivo XML correspondente foi encontrado.`
      );
    }

    if (firebirdEnabled) {
      if (fbRec) {
        reasons.push(
          `Firebird: O documento Nº ${pdfRec.number} EXISTE no banco Firebird (Valor R$ ${Number(
            fbRec.totalValue
          ).toFixed(2)}, Status ${fbRec.status}), indicando provável falta de exportação do arquivo XML.`
        );
      } else {
        reasons.push(
          'Firebird: O documento também NÃO foi localizado no banco Firebird.'
        );
      }
    }

    items.push(
      buildReconciliationItem({
        id: `rec_pdf_${pdfRec.id}`,
        category: isPdfDup ? 'POSSIVEL_DUPLICIDADE' : 'SOMENTE_PDF',
        xmlDoc: null,
        pdfRec,
        fbRec,
        dateField,
        matchCriterion: isPdfDup
          ? 'Detecção de linha repetida no relatório PDF'
          : 'Registro presente no PDF sem correspondente nos arquivos XML',
        fbMatchCriterion,
        reasons,
        ambiguousCandidates: [],
        firebirdEnabled,
        manualDecisions
      })
    );
  }

  // 3. Se Firebird estiver ativo, verifica se há registros no Firebird que não existem nem no XML nem no PDF!
  if (firebirdEnabled) {
    for (const fbRec of firebirdRecords) {
      if (usedFbIds.has(fbRec.id)) continue;

      items.push(
        buildReconciliationItem({
          id: `rec_fb_${fbRec.id}`,
          category: 'REVISAO_MANUAL',
          xmlDoc: null,
          pdfRec: null,
          fbRec,
          dateField,
          matchCriterion: 'Registro existente exclusivamente no banco Firebird (ausente no XML e no PDF)',
          fbMatchCriterion: 'Leitura direta da tabela mapeada no Firebird',
          reasons: [
            `Registro Nº ${fbRec.number} (Série ${fbRec.series}, Valor R$ ${Number(
              fbRec.totalValue || 0
            ).toFixed(2)}, Status ${
              fbRec.status
            }) encontrado no banco Firebird, mas ausente tanto nos XMLs quanto no relatório em PDF.`
          ],
          ambiguousCandidates: [],
          firebirdEnabled,
          manualDecisions
        })
      );
    }
  }

  const summary = buildReconciliationSummary(items, {
    dateField,
    dateFieldLabel,
    firebirdEnabled
  });

  return {
    executedAt: new Date().toISOString(),
    options: {
      dateField,
      dateFieldLabel,
      comparePayment,
      firebirdEnabled
    },
    categoryDefinitions: CATEGORY_DEFINITIONS,
    summary,
    items
  };
}

/**
 * Constrói um item padronizado da conciliação e aplica eventual decisão manual do auditor.
 */
function buildReconciliationItem({
  id,
  category,
  xmlDoc,
  pdfRec,
  fbRec,
  dateField,
  matchCriterion,
  fbMatchCriterion = null,
  reasons = [],
  ambiguousCandidates = [],
  firebirdEnabled = false,
  manualDecisions = {}
}) {
  const xmlDate = xmlDoc ? (dateField === 'authDate' ? xmlDoc.authDate || xmlDoc.issueDate : xmlDoc.issueDate) : null;
  const pdfDate = pdfRec ? (dateField === 'authDate' ? pdfRec.authDate || pdfRec.issueDate : pdfRec.issueDate) : null;
  const fbDate = fbRec ? (dateField === 'authDate' ? fbRec.authDate || fbRec.issueDate : fbRec.issueDate) : null;

  const xmlVal = xmlDoc && xmlDoc.isSaleDocument ? Number(xmlDoc.totalValue || 0) : null;
  const pdfVal = pdfRec ? Number(pdfRec.totalValue || 0) : null;
  const fbVal = fbRec ? Number(fbRec.totalValue || 0) : null;

  const valueDiff =
    xmlVal !== null && pdfVal !== null
      ? Number((xmlVal - pdfVal).toFixed(2))
      : xmlVal !== null && fbVal !== null
      ? Number((xmlVal - fbVal).toFixed(2))
      : null;

  const decision = manualDecisions[id] || null;
  let effectiveCategory = category;
  let reviewStatus =
    category === 'REVISAO_MANUAL' || ambiguousCandidates.length > 0 ? 'PENDENTE' : 'AUTOMATICO';

  const effectiveReasons = [...reasons];

  if (decision) {
    if (decision.action === 'CONFIRM_CONCILIATION') {
      effectiveCategory = 'CONCILIADO';
      reviewStatus = 'CONFIRMADO_MANUALMENTE';
      effectiveReasons.unshift(
        `[DECISÃO DO AUDITOR - CONFIRMADO]: ${
          decision.note || 'Conciliação confirmada manualmente pelo usuário.'
        }`
      );
    } else if (decision.action === 'REJECT_MATCH') {
      effectiveCategory = decision.targetCategory || 'REVISAO_MANUAL';
      reviewStatus = 'REJEITADO_MANUALMENTE';
      effectiveReasons.unshift(
        `[DECISÃO DO AUDITOR - REJEITADO]: ${
          decision.note || 'Correspondência/candidato rejeitado pelo usuário.'
        }`
      );
    } else if (decision.action === 'RECLASSIFY' && decision.targetCategory) {
      effectiveCategory = decision.targetCategory;
      reviewStatus = 'AJUSTADO_MANUALMENTE';
      effectiveReasons.unshift(
        `[DECISÃO DO AUDITOR - RECLASSIFICADO PARA "${
          CATEGORY_DEFINITIONS[decision.targetCategory]?.label || decision.targetCategory
        }"]: ${decision.note || 'Ajuste manual registrado.'}`
      );
    }
  }

  const catDef = CATEGORY_DEFINITIONS[effectiveCategory] || CATEGORY_DEFINITIONS.REVISAO_MANUAL;

  // Valor de referência para totalização da categoria
  const referenceValue =
    xmlVal !== null ? xmlVal : pdfVal !== null ? pdfVal : fbVal !== null ? fbVal : 0;

  return {
    id,
    originalCategory: category,
    category: effectiveCategory,
    categoryLabel: catDef.label,
    badgeClass: catDef.badgeClass,
    severity: catDef.severity,
    reviewStatus,
    manualDecision: decision,

    // Identificação consolidada para busca e filtros
    documentNumber: xmlDoc?.number || pdfRec?.number || fbRec?.number || '—',
    series: xmlDoc?.series || pdfRec?.series || fbRec?.series || '—',
    model: xmlDoc?.model || pdfRec?.model || fbRec?.model || '—',
    docType: xmlDoc?.docType || (pdfRec?.model === '55' ? 'NF-e' : pdfRec?.model === '65' ? 'NFC-e' : 'Doc Fiscal'),
    accessKey: xmlDoc?.accessKey || pdfRec?.accessKey || fbRec?.accessKey || '',
    referenceDate: xmlDate || pdfDate || fbDate || null,
    issueDate: xmlDoc?.issueDate || pdfRec?.issueDate || fbRec?.issueDate || null,
    authDate: xmlDoc?.authDate || pdfRec?.authDate || fbRec?.authDate || null,
    status: xmlDoc?.status || pdfRec?.status || fbRec?.status || 'DESCONHECIDO',
    paymentMethod:
      xmlDoc?.primaryPayment && xmlDoc.primaryPayment !== 'Não informado'
        ? xmlDoc.primaryPayment
        : pdfRec?.paymentMethod || fbRec?.paymentMethod || 'Não informado',

    // Valores comparativos
    xmlValue: xmlVal,
    pdfValue: pdfVal,
    fbValue: fbVal,
    referenceValue: Number(referenceValue.toFixed(2)),
    valueDifference: valueDiff,

    // Critérios e explicações
    matchCriterion,
    fbMatchCriterion: firebirdEnabled ? fbMatchCriterion : null,
    reasons: effectiveReasons,
    ambiguousCandidates,

    // Fontes completas para o modal de inspeção detalhada
    sources: {
      hasXml: Boolean(xmlDoc),
      hasPdf: Boolean(pdfRec),
      hasFirebird: Boolean(fbRec),
      xml: xmlDoc || null,
      pdf: pdfRec || null,
      firebird: fbRec || null
    }
  };
}

/**
 * Calcula os totais e contadores por categoria de conciliação e por fonte.
 */
function buildReconciliationSummary(items = [], meta = {}) {
  const byCategory = {};
  Object.keys(CATEGORY_DEFINITIONS).forEach((code) => {
    byCategory[code] = {
      code,
      label: CATEGORY_DEFINITIONS[code].label,
      badgeClass: CATEGORY_DEFINITIONS[code].badgeClass,
      count: 0,
      totalValue: 0
    };
  });

  let totalXmlCount = 0;
  let totalXmlValue = 0;
  let totalPdfCount = 0;
  let totalPdfValue = 0;
  let totalFbCount = 0;
  let totalFbValue = 0;
  let pendingManualReviewCount = 0;

  for (const item of items) {
    const cat = byCategory[item.category] || byCategory.REVISAO_MANUAL;
    cat.count += 1;
    cat.totalValue = Number((cat.totalValue + (item.referenceValue || 0)).toFixed(2));

    if (item.sources.hasXml && item.xmlValue !== null) {
      totalXmlCount += 1;
      if (item.sources.xml?.status === 'AUTORIZADO' && !item.sources.xml?.isDuplicate) {
        totalXmlValue = Number((totalXmlValue + item.xmlValue).toFixed(2));
      }
    }
    if (item.sources.hasPdf && item.pdfValue !== null) {
      totalPdfCount += 1;
      if (item.sources.pdf?.status === 'AUTORIZADO') {
        totalPdfValue = Number((totalPdfValue + item.pdfValue).toFixed(2));
      }
    }
    if (item.sources.hasFirebird && item.fbValue !== null) {
      totalFbCount += 1;
      if (item.sources.firebird?.status === 'AUTORIZADO') {
        totalFbValue = Number((totalFbValue + item.fbValue).toFixed(2));
      }
    }
    if (item.category === 'REVISAO_MANUAL' || item.reviewStatus === 'PENDENTE') {
      pendingManualReviewCount += 1;
    }
  }

  return {
    totalItems: items.length,
    dateField: meta.dateField,
    dateFieldLabel: meta.dateFieldLabel,
    firebirdEnabled: meta.firebirdEnabled,
    pendingManualReviewCount,
    totalsBySource: {
      xml: { count: totalXmlCount, authorizedValue: totalXmlValue },
      pdf: { count: totalPdfCount, authorizedValue: totalPdfValue },
      firebird: { count: totalFbCount, authorizedValue: totalFbValue },
      diffXmlVsPdf: Number((totalXmlValue - totalPdfValue).toFixed(2)),
      diffXmlVsFirebird: meta.firebirdEnabled
        ? Number((totalXmlValue - totalFbValue).toFixed(2))
        : null
    },
    categories: Object.values(byCategory)
  };
}

module.exports = {
  CATEGORY_DEFINITIONS,
  runReconciliation
};
