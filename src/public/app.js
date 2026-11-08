(() => {
  const state = {
    currentStep: 1,
    isDemoMode: false,
    xmlData: null,
    pdfData: null,
    firebirdData: null,
    firebirdSavedConfig: null,
    reconciliation: null,
    categoryDefinitions: {},
    storage: null,
    filters: {
      category: 'ALL',
      status: 'ALL',
      dateFrom: '',
      dateTo: '',
      number: '',
      series: '',
      payment: 'ALL',
      search: ''
    }
  };

  // Utilitários de formatação
  function formatMoney(val) {
    if (val === null || val === undefined || val === '') return '—';
    const n = Number(val);
    if (!Number.isFinite(n)) return '—';
    return `R$ ${n.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  }

  function formatDateBr(str) {
    if (!str) return '—';
    const m = String(str).match(/^(\d{4})-(\d{2})-(\d{2})/);
    if (m) return `${m[3]}/${m[2]}/${m[1]}`;
    return str;
  }

  function escapeHtml(str) {
    if (str === null || str === undefined) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function showNotification(message, type = 'info') {
    const area = document.getElementById('notificationArea');
    if (!area) return;
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.innerHTML = `
      <span>${escapeHtml(message)}</span>
      <button type="button" style="background:none;border:none;color:#fff;cursor:pointer;font-weight:bold;">&times;</button>
    `;
    toast.querySelector('button').addEventListener('click', () => toast.remove());
    area.appendChild(toast);
    setTimeout(() => {
      if (toast.parentNode) toast.remove();
    }, 5500);
  }

  function setProgress(visible, text = 'Processando...', percent = 60) {
    const bar = document.getElementById('globalProgressBar');
    const txt = document.getElementById('globalProgressText');
    const fill = document.getElementById('globalProgressFill');
    if (!bar) return;
    if (visible) {
      bar.classList.remove('hidden');
      txt.textContent = text;
      fill.style.width = `${percent}%`;
    } else {
      bar.classList.add('hidden');
    }
  }

  // Navegação de Etapas
  function goToStep(stepNumber) {
    const num = Number(stepNumber);
    state.currentStep = num;

    document.querySelectorAll('.step-btn').forEach((btn) => {
      btn.classList.toggle('active', Number(btn.dataset.step) === num);
    });

    document.querySelectorAll('.step-panel').forEach((panel) => {
      panel.classList.toggle('active', panel.id === `step-${num}`);
    });

    if (num === 3) {
      renderStep3ReviewAndMapping();
    } else if (num === 5) {
      renderStep5PreReconcile();
    } else if (num === 6) {
      renderStep6ReconciliationResults();
    } else if (num === 7) {
      renderStep7ExportMetadata();
    }
  }

  // Sincroniza estado inicial com o backend
  async function fetchInitialStatus() {
    try {
      const res = await fetch('/api/status');
      const data = await res.json();
      applyServerState(data);
    } catch (err) {
      showNotification('Erro ao conectar ao servidor local: ' + err.message, 'error');
    }
  }

  function applyServerState(data) {
    if (data.isDemoMode !== undefined) state.isDemoMode = data.isDemoMode;
    if (data.xmlData) state.xmlData = data.xmlData;
    if (data.pdfData) state.pdfData = data.pdfData;
    if (data.firebirdData) state.firebirdData = data.firebirdData;
    if (data.firebirdSavedConfig) state.firebirdSavedConfig = data.firebirdSavedConfig;
    if (data.reconciliation) state.reconciliation = data.reconciliation;
    if (data.auditDashboard) state.auditDashboard = data.auditDashboard;
    else if (data.reconciliation?.result?.auditDashboard) state.auditDashboard = data.reconciliation.result.auditDashboard;
    if (data.categoryDefinitions) state.categoryDefinitions = data.categoryDefinitions;
    if (data.storage) state.storage = data.storage;

    updateGlobalBannersAndSidebar();
    renderStep1XmlSummary();
    renderStep2PdfSummary();
    renderStep3ReviewAndMapping();
    renderStep4FirebirdState();
    renderStep5PreReconcile();
    renderStep6ReconciliationResults();
    renderStep7ExportMetadata();
    renderAuditExecutiveDashboard();
  }

  function updateGlobalBannersAndSidebar() {
    const demoBanner = document.getElementById('demoBanner');
    demoBanner.classList.toggle('hidden', !state.isDemoMode);

    const xmlCount = state.xmlData?.documents?.length || 0;
    document.getElementById('step1Meta').textContent =
      xmlCount > 0 ? `${xmlCount} documento(s) lido(s)` : 'Arquivos .zip, .rar ou .xml';

    const pdfCount = state.pdfData?.records?.length || 0;
    document.getElementById('step2Meta').textContent =
      state.pdfData?.fileName
        ? `${pdfCount} linha(s) (${state.pdfData.fileName.slice(0, 18)})`
        : 'Texto selecionável ou OCR';

    document.getElementById('step3Meta').textContent =
      xmlCount > 0 || pdfCount > 0
        ? `XML (${xmlCount}) • PDF (${pdfCount})`
        : 'Colunas PDF e prévia XML';

    document.getElementById('step4Meta').textContent = state.firebirdData?.enabled
      ? `Ativo (Firebird ${state.firebirdData.engineVersion || ''})`
      : 'Opcional (Versões 2.5 e 5.0)';

    const recCount = state.reconciliation?.result?.items?.length || 0;
    document.getElementById('step6Meta').textContent =
      recCount > 0 ? `${recCount} itens classificados` : 'Painel, filtros e auditoria';
  }

  // ==========================================================================
  // ETAPA 1: UPLOAD E RENDERIZAÇÃO DOS XMLs
  // ==========================================================================

  async function handleXmlFilesUpload(fileList) {
    if (!fileList || fileList.length === 0) return;
    const formData = new FormData();
    Array.from(fileList).forEach((f) => formData.append('files', f));

    setProgress(true, `Extraindo e analisando ${fileList.length} arquivo(s) (.zip, .rar, .xml)...`, 50);
    try {
      const res = await fetch('/api/xml/upload', {
        method: 'POST',
        body: formData
      });
      const data = await res.json();
      setProgress(false);

      if (!data.success) {
        showNotification(data.message || 'Falha ao importar arquivos XML.', 'error');
        return;
      }

      state.isDemoMode = false;
      state.xmlData = data.xmlData;
      if (data.auditDashboard) state.auditDashboard = data.auditDashboard;
      updateGlobalBannersAndSidebar();
      renderStep1XmlSummary();
      renderAuditExecutiveDashboard();
      showNotification(data.message, 'success');
    } catch (err) {
      setProgress(false);
      showNotification('Erro no processamento dos XMLs: ' + err.message, 'error');
    }
  }

  function renderStep1XmlSummary() {
    const container = document.getElementById('xmlResultContainer');
    const summary = state.xmlData?.summary;
    if (!summary || (state.xmlData?.documents?.length === 0 && (state.xmlData?.validationAlerts?.length || 0) === 0)) {
      container.classList.add('hidden');
      return;
    }

    container.classList.remove('hidden');
    const kpiGrid = document.getElementById('xmlKpiGrid');
    kpiGrid.innerHTML = `
      <div class="kpi-card">
        <div class="kpi-label">Arquivos XML Extraídos</div>
        <div class="kpi-value">${summary.totalFilesExtracted}</div>
        <div class="kpi-sub">${summary.totalDocumentsParsed} documentos identificados</div>
      </div>
      <div class="kpi-card kpi-ok">
        <div class="kpi-label">Vendas Autorizadas (Líquidas)</div>
        <div class="kpi-value">${summary.authorizedSalesCount}</div>
        <div class="kpi-sub">${formatMoney(summary.authorizedSalesTotalValue)}</div>
      </div>
      <div class="kpi-card kpi-warn">
        <div class="kpi-label">Cancelados / Denegados</div>
        <div class="kpi-value">${summary.canceledCount + summary.deniedCount}</div>
        <div class="kpi-sub">${summary.linkedCancelEventsCount} evento(s) 110111 vinculado(s)</div>
      </div>
      <div class="kpi-card kpi-danger">
        <div class="kpi-label">Duplicados / Inválidos / Não-Venda</div>
        <div class="kpi-value">${summary.duplicateCount + summary.invalidCount + summary.nonSaleCount}</div>
        <div class="kpi-sub">Duplicados: ${summary.duplicateCount} | Não-venda: ${summary.nonSaleCount} | Inválidos: ${summary.invalidCount}</div>
      </div>
    `;

    // Tabela de pacotes
    const tbodyContainers = document.querySelector('#xmlContainersTable tbody');
    tbodyContainers.innerHTML = (state.xmlData.containerSummaries || [])
      .map(
        (c) => `
        <tr>
          <td><strong>${escapeHtml(c.name)}</strong></td>
          <td><span class="badge badge-info">${escapeHtml(c.type)}</span></td>
          <td>${(Number(c.sizeBytes || 0) / 1024).toFixed(1)} KB</td>
          <td>${c.extractedCount || 0}</td>
          <td>${c.ignoredNonXmlCount || 0}</td>
          <td>${c.errorCount || 0}</td>
          <td>
            <span class="badge ${c.status === 'PROCESSADO' ? 'badge-success' : 'badge-danger'}">
              ${escapeHtml(c.status)}
            </span>
          </td>
        </tr>
      `
      )
      .join('');

    // Tabela de alertas/erros por arquivo
    const alerts = state.xmlData.validationAlerts || [];
    document.getElementById('xmlAlertsCountBadge').textContent = `${alerts.length} ocorrência(s)`;
    const tbodyAlerts = document.querySelector('#xmlAlertsTable tbody');
    if (alerts.length === 0) {
      tbodyAlerts.innerHTML = `<tr><td colspan="3" class="text-muted">Nenhum erro estrutural ou arquivo não-venda detectado.</td></tr>`;
    } else {
      tbodyAlerts.innerHTML = alerts
        .map(
          (a) => `
          <tr>
            <td><strong>${escapeHtml(a.internalPath || a.fileName)}</strong></td>
            <td><span class="badge badge-warning">${escapeHtml(a.stage)}</span></td>
            <td>${escapeHtml(a.error)}</td>
          </tr>
        `
        )
        .join('');
    }
  }

  // ==========================================================================
  // ETAPA 2: UPLOAD DO RELATÓRIO PDF E OCR LOCAL
  // ==========================================================================

  async function handlePdfFileUpload(file) {
    if (!file) return;
    const formData = new FormData();
    formData.append('file', file);

    setProgress(true, `Lendo relatório em PDF "${file.name}"...`, 55);
    try {
      const res = await fetch('/api/pdf/upload', {
        method: 'POST',
        body: formData
      });
      const data = await res.json();
      setProgress(false);

      if (!data.success) {
        showNotification(data.message || 'Erro ao importar PDF.', 'error');
        return;
      }

      state.isDemoMode = false;
      state.pdfData = data.pdfData;
      if (data.auditDashboard) state.auditDashboard = data.auditDashboard;
      updateGlobalBannersAndSidebar();
      renderStep2PdfSummary();
      renderStep3ReviewAndMapping();
      renderAuditExecutiveDashboard();
      showNotification(data.message, data.pdfData.isScannedImagePdf ? 'warning' : 'success');
    } catch (err) {
      setProgress(false);
      showNotification('Erro ao importar relatório PDF: ' + err.message, 'error');
    }
  }

  async function handleRunPdfOcr() {
    setProgress(true, 'Executando OCR Local sobre o PDF digitalizado...', 70);
    try {
      const res = await fetch('/api/pdf/ocr', { method: 'POST' });
      const data = await res.json();
      setProgress(false);

      if (!data.success) {
        showNotification(data.message || 'Falha no OCR local.', 'error');
        return;
      }

      state.pdfData = data.pdfData;
      if (data.auditDashboard) state.auditDashboard = data.auditDashboard;
      updateGlobalBannersAndSidebar();
      renderStep2PdfSummary();
      renderStep3ReviewAndMapping();
      renderAuditExecutiveDashboard();
      showNotification(data.message, 'success');
    } catch (err) {
      setProgress(false);
      showNotification('Erro ao executar OCR: ' + err.message, 'error');
    }
  }

  function renderStep2PdfSummary() {
    const scannedAlert = document.getElementById('pdfScannedAlert');
    const resultContainer = document.getElementById('pdfResultContainer');
    const pdf = state.pdfData;

    if (!pdf || !pdf.fileName) {
      scannedAlert.classList.add('hidden');
      resultContainer.classList.add('hidden');
      return;
    }

    if (pdf.isScannedImagePdf) {
      scannedAlert.classList.remove('hidden');
      document.getElementById('pdfScannedMessage').textContent =
        pdf.scannedWarning ||
        'PDF digitalizado como imagem detectado. Clique no botão ao lado para extrair os dados via OCR Local.';
    } else {
      scannedAlert.classList.add('hidden');
    }

    resultContainer.classList.remove('hidden');
    const sum = pdf.summary || {
      pageCount: pdf.pageCount || 1,
      totalRowsExtracted: (pdf.records || []).length,
      authorizedCount: 0,
      canceledCount: 0,
      totalAuthorizedValue: 0
    };

    document.getElementById('pdfKpiGrid').innerHTML = `
      <div class="kpi-card">
        <div class="kpi-label">Arquivo PDF Importado</div>
        <div class="kpi-value" style="font-size:15px;">${escapeHtml(pdf.fileName)}</div>
        <div class="kpi-sub">${sum.pageCount} página(s) • ${(Number(pdf.sizeBytes || 0) / 1024).toFixed(1)} KB</div>
      </div>
      <div class="kpi-card kpi-ok">
        <div class="kpi-label">Linhas de Venda Extraídas</div>
        <div class="kpi-value">${sum.totalRowsExtracted}</div>
        <div class="kpi-sub">Autorizadas: ${sum.authorizedCount} | Canceladas: ${sum.canceledCount}</div>
      </div>
      <div class="kpi-card kpi-ok">
        <div class="kpi-label">Valor Total Autorizado (PDF)</div>
        <div class="kpi-value">${formatMoney(sum.totalAuthorizedValue)}</div>
        <div class="kpi-sub">Total bruto listado: ${formatMoney(sum.totalAllValue)}</div>
      </div>
      <div class="kpi-card ${pdf.isScannedImagePdf && !pdf.ocrExecuted ? 'kpi-warn' : ''}">
        <div class="kpi-label">Modo de Leitura</div>
        <div class="kpi-value" style="font-size:15px;">${
          pdf.ocrExecuted
            ? 'OCR Local Aplicado'
            : pdf.isScannedImagePdf
            ? 'Digitalizado (Aguardando OCR)'
            : 'Camada de Texto Nativa'
        }</div>
        <div class="kpi-sub">Mapeamento revisável na Etapa 3</div>
      </div>
    `;

    document.getElementById('pdfMethodBadge').textContent = `Método: ${pdf.extractionMethod || 'Padrão'}`;
    document.getElementById('pdfRawTextPreview').textContent =
      pdf.rawTextPreview || '(Sem texto selecionável direto — utilize o botão Executar OCR Local acima)';
  }

  // ==========================================================================
  // ETAPA 3: MAPEAMENTO DE COLUNAS DO PDF E PRÉVIA EDITÁVEL + PRÉVIA XML
  // ==========================================================================

  function renderStep3ReviewAndMapping() {
    const pdfRecords = state.pdfData?.records || [];
    const xmlDocs = state.xmlData?.documents || [];

    document.getElementById('tabPdfCount').textContent = pdfRecords.length;
    document.getElementById('tabXmlCount').textContent = xmlDocs.length;

    // 1. Popula seletores de mapeamento de colunas do PDF
    const colsMeta = state.pdfData?.columnsMeta || [];
    const mapping = state.pdfData?.activeMapping || state.pdfData?.detectedMapping || {};

    const selectMapIds = [
      { id: 'mapPdfDate', key: 'dateCol' },
      { id: 'mapPdfNumber', key: 'numberCol' },
      { id: 'mapPdfSeries', key: 'seriesCol' },
      { id: 'mapPdfModel', key: 'modelCol' },
      { id: 'mapPdfValue', key: 'valueCol' },
      { id: 'mapPdfPayment', key: 'paymentCol' },
      { id: 'mapPdfStatus', key: 'statusCol' },
      { id: 'mapPdfKey', key: 'accessKeyCol' }
    ];

    selectMapIds.forEach(({ id, key }) => {
      const sel = document.getElementById(id);
      if (!sel) return;
      const optionsHtml = [
        `<option value="-1">-- Não mapeado / Ausente --</option>`,
        ...colsMeta.map(
          (c) =>
            `<option value="${c.index}" ${Number(mapping[key]) === c.index ? 'selected' : ''}>Col ${
              c.index + 1
            }: ${escapeHtml(c.label)} (${escapeHtml(String(c.sample || '').slice(0, 18))})</option>`
        )
      ].join('');
      sel.innerHTML = optionsHtml;
    });

    // 2. Renderiza tabela editável de prévia do PDF
    const pdfTbody = document.querySelector('#pdfEditableTable tbody');
    if (pdfRecords.length === 0) {
      pdfTbody.innerHTML = `<tr><td colspan="10" class="text-muted">Nenhum registro extraído do PDF ainda. Importe um PDF na Etapa 2 ou adicione linhas manualmente.</td></tr>`;
    } else {
      pdfTbody.innerHTML = pdfRecords
        .map(
          (r, idx) => `
          <tr data-row-index="${idx}">
            <td>${idx + 1}</td>
            <td><input type="text" class="table-input pdf-edit-date" value="${escapeHtml(r.issueDate || '')}" placeholder="AAAA-MM-DD" /></td>
            <td><input type="text" class="table-input pdf-edit-number" value="${escapeHtml(r.number || '')}" /></td>
            <td><input type="text" class="table-input pdf-edit-series" value="${escapeHtml(r.series || '1')}" style="width:55px;" /></td>
            <td><input type="text" class="table-input pdf-edit-model" value="${escapeHtml(r.model || '')}" style="width:55px;" /></td>
            <td><input type="number" step="0.01" class="table-input pdf-edit-value" value="${Number(r.totalValue || 0).toFixed(2)}" /></td>
            <td><input type="text" class="table-input pdf-edit-payment" value="${escapeHtml(r.paymentMethod || '')}" /></td>
            <td>
              <select class="table-input pdf-edit-status">
                <option value="AUTORIZADO" ${r.status === 'AUTORIZADO' ? 'selected' : ''}>AUTORIZADO</option>
                <option value="CANCELADO" ${r.status === 'CANCELADO' ? 'selected' : ''}>CANCELADO</option>
                <option value="DENEGADO" ${r.status === 'DENEGADO' ? 'selected' : ''}>DENEGADO</option>
                <option value="INUTILIZADO" ${r.status === 'INUTILIZADO' ? 'selected' : ''}>INUTILIZADO</option>
              </select>
            </td>
            <td><input type="text" class="table-input pdf-edit-key" value="${escapeHtml(r.accessKey || '')}" placeholder="44 dígitos" /></td>
            <td>
              <button type="button" class="btn btn-sm btn-danger btn-delete-pdf-row" data-idx="${idx}" title="Remover esta linha">&times;</button>
            </td>
          </tr>
        `
        )
        .join('');

      pdfTbody.querySelectorAll('.btn-delete-pdf-row').forEach((btn) => {
        btn.addEventListener('click', () => {
          const i = Number(btn.dataset.idx);
          state.pdfData.records.splice(i, 1);
          renderStep3ReviewAndMapping();
        });
      });
    }

    // 3. Renderiza prévia dos XMLs extraídos
    const xmlTbody = document.querySelector('#xmlPreviewTable tbody');
    if (xmlDocs.length === 0) {
      xmlTbody.innerHTML = `<tr><td colspan="10" class="text-muted">Nenhum documento XML importado ainda.</td></tr>`;
    } else {
      xmlTbody.innerHTML = xmlDocs
        .map((d) => {
          const badges = [];
          if (d.isDuplicate) badges.push(`<span class="badge badge-duplicate">Duplicado</span>`);
          if (d.recordKind === 'NAO_VENDA') badges.push(`<span class="badge badge-warning">Não é Venda</span>`);
          if (!d.isValidXml || d.status === 'INVALIDO') badges.push(`<span class="badge badge-danger">XML Inválido</span>`);
          const missingText =
            d.missingFields && d.missingFields.length > 0
              ? `<small style="display:block;color:#b45309;">Ausente: ${escapeHtml(d.missingFields.join(', '))}</small>`
              : '<small class="text-muted">Completo</small>';

          return `
            <tr>
              <td>
                <strong>${escapeHtml(d.fileName)}</strong>
                ${d.subfolder ? `<small style="display:block;color:#64748b;">Subpasta: ${escapeHtml(d.subfolder)}</small>` : ''}
              </td>
              <td>${escapeHtml(d.docType || '—')} ${d.model ? `(${escapeHtml(d.model)})` : ''}</td>
              <td><strong>${escapeHtml(d.number || '—')}</strong> / Série ${escapeHtml(d.series || '—')}</td>
              <td>${escapeHtml(d.issueDisplay || formatDateBr(d.issueDate))}</td>
              <td>${escapeHtml(d.authDisplay || formatDateBr(d.authDate))}</td>
              <td>
                <span class="badge ${
                  d.status === 'AUTORIZADO'
                    ? 'badge-success'
                    : d.status === 'CANCELADO' || d.status === 'DENEGADO'
                    ? 'badge-danger'
                    : 'badge-neutral-alert'
                }">${escapeHtml(d.status)}</span>
              </td>
              <td><strong>${formatMoney(d.totalValue)}</strong></td>
              <td>Desc: ${formatMoney(d.discountValue)}<br/>Acrésc/Frete: ${formatMoney((d.additionValue || 0) + (d.freightValue || 0))}</td>
              <td>${escapeHtml(d.primaryPayment || '—')}</td>
              <td>${badges.join(' ')} ${missingText}</td>
            </tr>
          `;
        })
        .join('');
    }
  }

  async function handleApplyPdfColumnMapping() {
    const mapping = {
      dateCol: Number(document.getElementById('mapPdfDate').value),
      authDateCol: -1,
      numberCol: Number(document.getElementById('mapPdfNumber').value),
      seriesCol: Number(document.getElementById('mapPdfSeries').value),
      modelCol: Number(document.getElementById('mapPdfModel').value),
      valueCol: Number(document.getElementById('mapPdfValue').value),
      paymentCol: Number(document.getElementById('mapPdfPayment').value),
      statusCol: Number(document.getElementById('mapPdfStatus').value),
      accessKeyCol: Number(document.getElementById('mapPdfKey').value),
      customerCol: -1
    };

    try {
      const res = await fetch('/api/pdf/remap', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mapping })
      });
      const data = await res.json();
      if (!data.success) {
        showNotification(data.message || 'Erro ao aplicar mapeamento.', 'error');
        return;
      }
      state.pdfData = data.pdfData;
      renderStep2PdfSummary();
      renderStep3ReviewAndMapping();
      showNotification(data.message, 'success');
    } catch (err) {
      showNotification('Erro ao remapear colunas do PDF: ' + err.message, 'error');
    }
  }

  function collectEditedPdfRowsFromDom() {
    const rows = [];
    document.querySelectorAll('#pdfEditableTable tbody tr[data-row-index]').forEach((tr, idx) => {
      rows.push({
        id: `pdf_row_${idx + 1}`,
        issueDate: tr.querySelector('.pdf-edit-date').value.trim(),
        authDate: tr.querySelector('.pdf-edit-date').value.trim(),
        number: tr.querySelector('.pdf-edit-number').value.trim(),
        series: tr.querySelector('.pdf-edit-series').value.trim() || '1',
        model: tr.querySelector('.pdf-edit-model').value.trim(),
        totalValue: Number(tr.querySelector('.pdf-edit-value').value || 0),
        paymentMethod: tr.querySelector('.pdf-edit-payment').value.trim(),
        status: tr.querySelector('.pdf-edit-status').value,
        accessKey: tr.querySelector('.pdf-edit-key').value.trim(),
        manuallyEdited: true
      });
    });
    return rows;
  }

  async function handleSavePdfEdits() {
    const rows = collectEditedPdfRowsFromDom();
    try {
      const res = await fetch('/api/pdf/records', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ records: rows })
      });
      const data = await res.json();
      if (!data.success) {
        showNotification(data.message || 'Falha ao salvar revisão.', 'error');
        return;
      }
      state.pdfData = data.pdfData;
      updateGlobalBannersAndSidebar();
      renderStep2PdfSummary();
      renderStep3ReviewAndMapping();
      showNotification(data.message, 'success');
    } catch (err) {
      showNotification('Erro ao salvar revisão do PDF: ' + err.message, 'error');
    }
  }

  // ==========================================================================
  // ETAPA 4: CONFIGURAÇÃO, DESCOBERTA DE ESQUEMA E PRÉVIA DO FIREBIRD (2.5 / 5.0)
  // ==========================================================================

  function getFirebirdConfigFromForm() {
    return {
      versionTarget: document.getElementById('fbVersionTarget').value,
      host: document.getElementById('fbHost').value.trim() || '127.0.0.1',
      port: Number(document.getElementById('fbPort').value || 3050),
      charset: document.getElementById('fbCharset').value,
      database: document.getElementById('fbDatabase').value.trim(),
      user: document.getElementById('fbUser').value.trim() || 'SYSDBA',
      password: document.getElementById('fbPassword').value
    };
  }

  function renderStep4FirebirdState() {
    const fb = state.firebirdData;
    const saved = state.firebirdSavedConfig;

    if (saved && !document.getElementById('fbDatabase').value) {
      document.getElementById('fbHost').value = saved.host || '127.0.0.1';
      document.getElementById('fbPort').value = saved.port || 3050;
      document.getElementById('fbDatabase').value = saved.database || '';
      document.getElementById('fbUser').value = saved.user || 'SYSDBA';
      document.getElementById('fbCharset').value = saved.charset || 'UTF8';
      document.getElementById('fbVersionTarget').value = saved.versionTarget || 'auto';
    }

    if (!fb) return;

    document.getElementById('fbEnableToggle').checked = Boolean(fb.enabled);

    if (fb.tables && fb.tables.length > 0) {
      document.getElementById('fbSchemaSection').classList.remove('hidden');
      document.getElementById('fbDetectedVersionBadge').textContent = `Motor: Firebird ${
        fb.engineVersion || 'Detectado'
      } (ODS ${fb.odsVersion || '—'})`;

      const tableSelect = document.getElementById('fbTableSelect');
      const currentTable = fb.mapping?.tableName || fb.tables[0].tableName;
      tableSelect.innerHTML = fb.tables
        .map(
          (t) =>
            `<option value="${escapeHtml(t.tableName)}" ${
              t.tableName === currentTable ? 'selected' : ''
            }>${escapeHtml(t.tableName)} (${escapeHtml(t.tableType)} - ${t.columns.length} colunas)</option>`
        )
        .join('');

      populateFirebirdColumnSelects(currentTable, fb.mapping);
    }

    if (fb.previewSql && fb.previewRecords && fb.previewRecords.length > 0) {
      renderFirebirdPreviewBox(fb.previewSql, fb.previewRecords);
    }
  }

  function populateFirebirdColumnSelects(tableName, activeMapping = null) {
    const fb = state.firebirdData;
    const tableObj = (fb?.tables || []).find((t) => t.tableName === tableName);
    const cols = tableObj ? tableObj.columns : [];

    const colSelectDefs = [
      { id: 'fbColNumber', key: 'numberColumn', hints: ['NUM', 'DOC', 'NF', 'CUPOM', 'COO'] },
      { id: 'fbColSeries', key: 'seriesColumn', hints: ['SERIE', 'SER'] },
      { id: 'fbColModel', key: 'modelColumn', hints: ['MOD'] },
      { id: 'fbColAccessKey', key: 'accessKeyColumn', hints: ['CHAVE', 'KEY', 'NFE'] },
      { id: 'fbColIssueDate', key: 'issueDateColumn', hints: ['EMISS', 'DATA', 'DT_EMI', 'MOV'] },
      { id: 'fbColAuthDate', key: 'authDateColumn', hints: ['AUT', 'RECBTO'] },
      { id: 'fbColTotalValue', key: 'totalValueColumn', hints: ['TOTAL', 'LIQ', 'VALOR', 'VLR_TOTAL'] },
      { id: 'fbColStatus', key: 'statusColumn', hints: ['SIT', 'STAT'] },
      { id: 'fbColPayment', key: 'paymentColumn', hints: ['PAG', 'MEIO'] },
      { id: 'fbColCustomer', key: 'customerColumn', hints: ['CPF', 'CNPJ', 'DEST', 'CLI'] }
    ];

    colSelectDefs.forEach(({ id, key, hints }) => {
      const sel = document.getElementById(id);
      if (!sel) return;

      let chosen = activeMapping && activeMapping[key] ? activeMapping[key] : '';
      if (!chosen) {
        const autoCol = cols.find((c) => hints.some((h) => c.name.toUpperCase().includes(h)));
        if (autoCol) chosen = autoCol.name;
      }

      sel.innerHTML = [
        `<option value="">-- Não mapeado --</option>`,
        ...cols.map(
          (c) =>
            `<option value="${escapeHtml(c.name)}" ${
              c.name === chosen ? 'selected' : ''
            }>${escapeHtml(c.name)} (${escapeHtml(c.type)})</option>`
        )
      ].join('');
    });
  }

  async function handleTestFirebirdConnection() {
    const config = getFirebirdConfigFromForm();
    const saveConfig = document.getElementById('fbSaveEncrypted').checked;
    const feedback = document.getElementById('fbConnectionFeedback');

    setProgress(true, 'Testando conexão com o Firebird e verificando versão/ODS...', 50);
    try {
      const res = await fetch('/api/firebird/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ config, saveConfig })
      });
      const data = await res.json();
      setProgress(false);

      feedback.classList.remove('hidden', 'alert-success', 'alert-danger');
      if (data.success) {
        feedback.classList.add('alert-success');
        feedback.innerHTML = `
          <div>
            <strong>&#10004; ${escapeHtml(data.message)}</strong>
            <div style="font-size:12px;margin-top:4px;">
              Driver: <code>${escapeHtml(data.driverUsed)}</code> | Versão Motor: <strong>${escapeHtml(
          data.engineVersion
        )}</strong> | ODS: <strong>${escapeHtml(data.odsVersion)}</strong> | Modo: <strong>READ ONLY</strong>
            </div>
          </div>
        `;
        showNotification(data.message, 'success');
      } else {
        feedback.classList.add('alert-danger');
        feedback.innerHTML = `
          <div>
            <strong>&#10060; ${escapeHtml(data.userMessage || 'Falha na conexão com o Firebird.')}</strong>
            ${
              data.technicalHint
                ? `<div style="font-size:12px;margin-top:4px;">Dica técnica: ${escapeHtml(data.technicalHint)}</div>`
                : ''
            }
          </div>
        `;
        showNotification(data.userMessage || 'Erro ao conectar no Firebird.', 'error');
      }
    } catch (err) {
      setProgress(false);
      showNotification('Erro ao testar conexão Firebird: ' + err.message, 'error');
    }
  }

  async function handleDiscoverFirebirdSchema() {
    const config = getFirebirdConfigFromForm();
    setProgress(true, 'Consultando catálogo RDB$RELATIONS e RDB$RELATION_FIELDS do Firebird...', 65);
    try {
      const res = await fetch('/api/firebird/schema', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ config })
      });
      const data = await res.json();
      setProgress(false);

      if (!data.success) {
        showNotification(data.userMessage || 'Não foi possível descobrir o esquema.', 'error');
        return;
      }

      state.firebirdData.tables = data.tables;
      state.firebirdData.engineVersion = data.engineVersion;
      state.firebirdData.odsVersion = data.odsVersion;
      renderStep4FirebirdState();
      showNotification(`Esquema descoberto: ${data.tables.length} tabela(s)/view(s) localizada(s).`, 'success');
    } catch (err) {
      setProgress(false);
      showNotification('Erro na descoberta de esquema: ' + err.message, 'error');
    }
  }

  async function handlePreviewFirebirdMapping() {
    const config = getFirebirdConfigFromForm();
    const mapping = {
      tableName: document.getElementById('fbTableSelect').value,
      numberColumn: document.getElementById('fbColNumber').value,
      seriesColumn: document.getElementById('fbColSeries').value,
      modelColumn: document.getElementById('fbColModel').value,
      accessKeyColumn: document.getElementById('fbColAccessKey').value,
      issueDateColumn: document.getElementById('fbColIssueDate').value,
      authDateColumn: document.getElementById('fbColAuthDate').value,
      totalValueColumn: document.getElementById('fbColTotalValue').value,
      statusColumn: document.getElementById('fbColStatus').value,
      paymentColumn: document.getElementById('fbColPayment').value,
      customerColumn: document.getElementById('fbColCustomer').value
    };

    setProgress(true, 'Executando consulta SELECT somente-leitura de prévia no Firebird...', 70);
    try {
      const res = await fetch('/api/firebird/preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          config,
          mapping,
          enabled: document.getElementById('fbEnableToggle').checked || true
        })
      });
      const data = await res.json();
      setProgress(false);

      if (!data.success) {
        showNotification(data.userMessage || 'Erro ao gerar prévia.', 'error');
        return;
      }

      document.getElementById('fbEnableToggle').checked = true;
      state.firebirdData.enabled = true;
      state.firebirdData.mapping = mapping;
      state.firebirdData.previewSql = data.generatedSql;
      state.firebirdData.previewRecords = data.records;

      updateGlobalBannersAndSidebar();
      renderFirebirdPreviewBox(data.generatedSql, data.records);
      showNotification(`Prévia somente-leitura carregada (${data.records.length} registro(s)).`, 'success');
    } catch (err) {
      setProgress(false);
      showNotification('Erro na prévia do Firebird: ' + err.message, 'error');
    }
  }

  function renderFirebirdPreviewBox(sql, records) {
    const container = document.getElementById('fbPreviewContainer');
    container.classList.remove('hidden');
    document.getElementById('fbPreviewSqlBox').textContent = sql || '';
    const tbody = document.querySelector('#fbPreviewTable tbody');
    tbody.innerHTML = (records || [])
      .map(
        (r) => `
        <tr>
          <td><strong>${escapeHtml(r.number)}</strong></td>
          <td>${escapeHtml(r.series)}</td>
          <td>${escapeHtml(r.model || '—')}</td>
          <td>${formatDateBr(r.issueDate)}</td>
          <td>${formatDateBr(r.authDate)}</td>
          <td><strong>${formatMoney(r.totalValue)}</strong></td>
          <td><span class="badge ${r.status === 'AUTORIZADO' ? 'badge-success' : 'badge-danger'}">${escapeHtml(r.status)}</span></td>
          <td>${escapeHtml(r.paymentMethod)}</td>
          <td><small>${escapeHtml(r.accessKey || '—')}</small></td>
        </tr>
      `
      )
      .join('');
  }

  // ==========================================================================
  // ETAPA 5 & 6: EXECUÇÃO DA CONCILIAÇÃO E PAINEL DE RESULTADOS
  // ==========================================================================

  function renderStep5PreReconcile() {
    const xmlDocs = state.xmlData?.documents || [];
    const pdfRows = state.pdfData?.records || [];
    const fbEnabled = Boolean(state.firebirdData?.enabled);

    document.getElementById('recUseFirebirdSelect').value = fbEnabled ? 'true' : 'false';
    if (state.reconciliation?.options?.dateField) {
      document.getElementById('recDateFieldSelect').value = state.reconciliation.options.dateField;
    }

    const grid = document.getElementById('preReconcileSummaryGrid');
    grid.innerHTML = `
      <div class="kpi-card">
        <div class="kpi-label">Fonte 1: Arquivos XML</div>
        <div class="kpi-value">${xmlDocs.length} doc(s)</div>
        <div class="kpi-sub">Total Autorizado: ${formatMoney(state.xmlData?.summary?.authorizedSalesTotalValue || 0)}</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Fonte 2: Relatório PDF</div>
        <div class="kpi-value">${pdfRows.length} linha(s)</div>
        <div class="kpi-sub">Total Autorizado: ${formatMoney(state.pdfData?.summary?.totalAuthorizedValue || 0)}</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Fonte 3: Banco Firebird (2.5 / 5.0)</div>
        <div class="kpi-value">${fbEnabled ? 'Habilitado' : 'Opcional (Desativado)'}</div>
        <div class="kpi-sub">${
          fbEnabled
            ? `Tabela: ${escapeHtml(state.firebirdData?.mapping?.tableName || 'Mapeada')}`
            : 'Configure na Etapa 4 se desejar confronto 3-vias'
        }</div>
      </div>
    `;
  }

  async function executeReconciliationRequest(overrideDateField = null) {
    const dateField =
      overrideDateField || document.getElementById('recDateFieldSelect').value || 'issueDate';
    const comparePayment = document.getElementById('recComparePaymentSelect').value !== 'false';
    const firebirdEnabled = document.getElementById('recUseFirebirdSelect').value === 'true';

    setProgress(true, 'Executando motor de conciliação determinística...', 75);
    try {
      const res = await fetch('/api/reconcile', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          dateField,
          comparePayment,
          firebirdEnabled,
          firebirdConfig: getFirebirdConfigFromForm()
        })
      });
      const data = await res.json();
      setProgress(false);

      if (!data.success) {
        showNotification(data.message || 'Erro na conciliação.', 'error');
        return;
      }

      state.reconciliation = data.reconciliation;
      if (data.auditDashboard) state.auditDashboard = data.auditDashboard;
      else if (data.reconciliation?.result?.auditDashboard) state.auditDashboard = data.reconciliation.result.auditDashboard;
      document.getElementById('recDateFieldSelect').value = dateField;
      document.getElementById('quickDateModeSelect').value = dateField;

      updateGlobalBannersAndSidebar();
      renderAuditExecutiveDashboard();
      goToStep(6);
      showNotification(
        `Conciliação concluída (${state.reconciliation.result.options.dateFieldLabel}).`,
        'success'
      );
    } catch (err) {
      setProgress(false);
      showNotification('Erro ao executar conciliação: ' + err.message, 'error');
    }
  }

  function getFilteredItems() {
    const items = state.reconciliation?.result?.items || [];
    const f = state.filters;

    return items.filter((item) => {
      if (f.category !== 'ALL' && item.category !== f.category) return false;
      if (f.status !== 'ALL' && item.status !== f.status) return false;
      if (f.series && String(item.series) !== f.series.trim()) return false;
      if (f.number && !String(item.documentNumber).includes(f.number.trim())) return false;
      if (f.payment !== 'ALL' && !String(item.paymentMethod || '').toLowerCase().includes(f.payment.toLowerCase())) {
        return false;
      }
      if (f.dateFrom && item.referenceDate && item.referenceDate < f.dateFrom) return false;
      if (f.dateTo && item.referenceDate && item.referenceDate > f.dateTo) return false;
      if (f.search) {
        const q = f.search.toLowerCase().trim();
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
          item.sources?.xml?.destCnpjCpf || ''
        ]
          .join(' ')
          .toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }

  function renderStep6ReconciliationResults() {
    const recResult = state.reconciliation?.result;
    const compBar = document.getElementById('sourceComparisonBar');
    const cardsGrid = document.getElementById('categoryCardsGrid');
    const tbody = document.querySelector('#reconciliationTable tbody');

    if (!recResult || !recResult.summary) {
      compBar.innerHTML = `<div class="source-stat-item"><strong>Nenhuma conciliação executada ainda</strong><small>Importe os arquivos ou carregue a Demonstração Guiada no topo da página.</small></div>`;
      cardsGrid.innerHTML = '';
      tbody.innerHTML = `<tr><td colspan="12" class="text-muted">Execute a conciliação na Etapa 5 para visualizar os resultados.</td></tr>`;
      return;
    }

    const sum = recResult.summary;
    document.getElementById('quickDateModeSelect').value = recResult.options.dateField;

    // Popula seletor de categorias no filtro
    const catSelect = document.getElementById('filterCategory');
    catSelect.innerHTML = [
      `<option value="ALL">Todas as categorias (${sum.totalItems})</option>`,
      ...(sum.categories || []).map(
        (c) =>
          `<option value="${c.code}" ${state.filters.category === c.code ? 'selected' : ''}>${escapeHtml(
            c.label
          )} (${c.count})</option>`
      )
    ].join('');

    // Barra comparativa de totais por fonte
    const tbs = sum.totalsBySource;
    compBar.innerHTML = `
      <div class="source-stat-item">
        <small>Total Vendas Autorizadas (XML)</small>
        <strong>${formatMoney(tbs.xml.authorizedValue)}</strong>
        <small>${tbs.xml.count} documento(s) com XML</small>
      </div>
      <div class="source-stat-item">
        <small>Total Vendas Autorizadas (PDF)</small>
        <strong>${formatMoney(tbs.pdf.authorizedValue)}</strong>
        <small>${tbs.pdf.count} registro(s) no PDF</small>
      </div>
      <div class="source-stat-item">
        <small>Diferença Líquida (XML vs PDF)</small>
        <strong style="color:${Math.abs(tbs.diffXmlVsPdf) < 0.01 ? '#4ade80' : '#fbbf24'};">
          ${formatMoney(tbs.diffXmlVsPdf)}
        </strong>
        <small>Critério ativo: ${escapeHtml(sum.dateFieldLabel)}</small>
      </div>
      <div class="source-stat-item">
        <small>Banco Firebird (${sum.firebirdEnabled ? 'Ativo' : 'Inativo'})</small>
        <strong>${sum.firebirdEnabled ? formatMoney(tbs.firebird.authorizedValue) : 'Não habilitado'}</strong>
        <small>${
          sum.firebirdEnabled
            ? `${tbs.firebird.count} registro(s) confrontado(s)`
            : 'Ative na Etapa 4/5 para confronto 3-vias'
        }</small>
      </div>
    `;

    // Cards das 9 Categorias
    cardsGrid.innerHTML = (sum.categories || [])
      .map(
        (c) => `
        <button type="button" class="category-card ${
          state.filters.category === c.code ? 'selected' : ''
        }" data-cat-code="${c.code}">
          <div class="category-card-top">
            <span class="category-card-title">${escapeHtml(c.label)}</span>
            <span class="badge ${c.badgeClass}">${c.count}</span>
          </div>
          <div class="category-card-value">${formatMoney(c.totalValue)}</div>
        </button>
      `
      )
      .join('');

    cardsGrid.querySelectorAll('.category-card').forEach((card) => {
      card.addEventListener('click', () => {
        const code = card.dataset.catCode;
        state.filters.category = state.filters.category === code ? 'ALL' : code;
        document.getElementById('filterCategory').value = state.filters.category;
        renderStep6ReconciliationResults();
      });
    });

    // Tabela de itens filtrados
    const filtered = getFilteredItems();
    document.getElementById('filteredItemsCount').textContent = filtered.length;

    if (filtered.length === 0) {
      tbody.innerHTML = `<tr><td colspan="12" class="text-muted">Nenhum registro corresponde aos filtros selecionados.</td></tr>`;
      return;
    }

    tbody.innerHTML = filtered
      .map((item) => {
        const diffVal = item.valueDifference;
        const diffHtml =
          diffVal === null
            ? '—'
            : Math.abs(diffVal) < 0.01
            ? '<span style="color:#15803d;font-weight:700;">R$ 0,00</span>'
            : `<span style="color:#b91c1c;font-weight:700;">${formatMoney(diffVal)}</span>`;

        const firstReason = (item.reasons || [])[0] || '';
        const manualBadge =
          item.reviewStatus && item.reviewStatus !== 'AUTOMATICO'
            ? `<small style="display:block;color:#0e7490;font-weight:700;">[${escapeHtml(item.reviewStatus)}]</small>`
            : '';

        return `
          <tr>
            <td>
              <span class="badge ${item.badgeClass}">${escapeHtml(item.categoryLabel)}</span>
              ${manualBadge}
            </td>
            <td><strong>${escapeHtml(item.documentNumber)}</strong> / Série ${escapeHtml(item.series)}</td>
            <td>${escapeHtml(item.docType)} (${escapeHtml(item.model)})</td>
            <td>${formatDateBr(item.referenceDate)}</td>
            <td>
              <span class="badge ${
                item.status === 'AUTORIZADO'
                  ? 'badge-success'
                  : item.status === 'CANCELADO' || item.status === 'DENEGADO'
                  ? 'badge-danger'
                  : 'badge-neutral-alert'
              }">${escapeHtml(item.status)}</span>
            </td>
            <td>${escapeHtml(item.paymentMethod)}</td>
            <td><strong>${formatMoney(item.xmlValue)}</strong></td>
            <td><strong>${formatMoney(item.pdfValue)}</strong></td>
            <td>${formatMoney(item.fbValue)}</td>
            <td>${diffHtml}</td>
            <td>
              <strong style="display:block;font-size:11.5px;color:#1e293b;">${escapeHtml(
                item.matchCriterion || ''
              )}</strong>
              <span style="color:#475569;">${escapeHtml(firstReason)}</span>
            </td>
            <td>
              <button type="button" class="btn btn-sm btn-outline btn-inspect-item" data-item-id="${escapeHtml(
                item.id
              )}">
                Inspecionar / Revisar
              </button>
            </td>
          </tr>
        `;
      })
      .join('');

    tbody.querySelectorAll('.btn-inspect-item').forEach((btn) => {
      btn.addEventListener('click', () => openItemDetailModal(btn.dataset.itemId));
    });
  }

  // ==========================================================================
  // MODAL DE DETALHES DA VENDA E DECISÃO MANUAL SOBRE CASOS AMBÍGUOS
  // ==========================================================================

  function openItemDetailModal(itemId) {
    const items = state.reconciliation?.result?.items || [];
    const item = items.find((i) => i.id === itemId);
    if (!item) return;

    const modal = document.getElementById('itemDetailModal');
    document.getElementById('modalItemTitle').textContent = `Venda Nº ${item.documentNumber} (Série ${item.series}) — ${item.categoryLabel}`;
    document.getElementById('modalItemSubtitle').textContent = `Critério de correspondência: ${
      item.matchCriterion || 'Análise individual'
    }${item.accessKey ? ` | Chave: ${item.accessKey}` : ''}`;

    const xml = item.sources?.xml;
    const pdf = item.sources?.pdf;
    const fb = item.sources?.firebird;

    const reasonsHtml = (item.reasons || [])
      .map((r) => `<li>${escapeHtml(r)}</li>`)
      .join('');

    const candidatesHtml =
      item.ambiguousCandidates && item.ambiguousCandidates.length > 0
        ? `
          <div class="alert-box alert-warning mt-12">
            <div>
              <strong>Candidatos Ambíguos Identificados (Não vinculados silenciosamente):</strong>
              <ul style="margin-top:6px;padding-left:18px;">
                ${item.ambiguousCandidates.map((c) => `<li>${escapeHtml(c.reason)}</li>`).join('')}
              </ul>
            </div>
          </div>
        `
        : '';

    const itemsTableHtml =
      xml && xml.items && xml.items.length > 0
        ? `
          <div class="card mt-16">
            <h4>Itens e Tributos Extraídos do XML (${xml.items.length} item(ns))</h4>
            <div class="table-responsive mt-12">
              <table class="data-table">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>Código</th>
                    <th>Descrição</th>
                    <th>NCM / CFOP</th>
                    <th>Qtd / Un</th>
                    <th>Vlr. Unit</th>
                    <th>Vlr. Bruto</th>
                    <th>Desc / Outros</th>
                    <th>ICMS / PIS / COFINS</th>
                  </tr>
                </thead>
                <tbody>
                  ${xml.items
                    .map(
                      (it) => `
                      <tr>
                        <td>${escapeHtml(it.itemNumber)}</td>
                        <td>${escapeHtml(it.code)}</td>
                        <td>${escapeHtml(it.description)}</td>
                        <td>${escapeHtml(it.ncm)} / <strong>${escapeHtml(it.cfop)}</strong></td>
                        <td>${it.quantity} ${escapeHtml(it.unit)}</td>
                        <td>${formatMoney(it.unitPrice)}</td>
                        <td><strong>${formatMoney(it.grossValue)}</strong></td>
                        <td>Desc: ${formatMoney(it.discount)} | Frete/Out: ${formatMoney(
                        (it.freight || 0) + (it.otherExpenses || 0)
                      )}</td>
                        <td>ICMS: ${formatMoney(it.taxes?.icmsVal)} | PIS: ${formatMoney(
                        it.taxes?.pisVal
                      )} | COFINS: ${formatMoney(it.taxes?.cofinsVal)}</td>
                      </tr>
                    `
                    )
                    .join('')}
                </tbody>
              </table>
            </div>
          </div>
        `
        : '';

    const categoryOptionsHtml = Object.values(state.categoryDefinitions || {})
      .map(
        (cat) =>
          `<option value="${cat.code}" ${item.category === cat.code ? 'selected' : ''}>${escapeHtml(
            cat.label
          )}</option>`
      )
      .join('');

    const body = document.getElementById('modalItemBody');
    body.innerHTML = `
      <div class="policy-callout">
        <strong>Motivo(s) da Classificação (${escapeHtml(item.categoryLabel)}):</strong>
        <ul>${reasonsHtml}</ul>
      </div>
      ${candidatesHtml}

      <div class="comparison-sources-grid mt-16">
        <!-- Origem 1: XML -->
        <div class="source-box">
          <h4>1. Dados no Arquivo XML</h4>
          ${
            xml
              ? `
                <dl class="kv-list">
                  <dt>Arquivo:</dt><dd>${escapeHtml(xml.displayPath)}</dd>
                  <dt>Tipo / Modelo:</dt><dd>${escapeHtml(xml.docType)} (${escapeHtml(xml.modelLabel || xml.model)})</dd>
                  <dt>Número / Série:</dt><dd>${escapeHtml(xml.number)} / Série ${escapeHtml(xml.series)}</dd>
                  <dt>Chave (44 díg.):</dt><dd>${escapeHtml(xml.accessKey || 'Não informada')}</dd>
                  <dt>Emissão:</dt><dd>${escapeHtml(xml.issueDisplay || formatDateBr(xml.issueDate))}</dd>
                  <dt>Autorização:</dt><dd>${escapeHtml(xml.authDisplay || formatDateBr(xml.authDate))}</dd>
                  <dt>Protocolo / Status:</dt><dd>${escapeHtml(xml.protocol || 'Ausente')} (${escapeHtml(xml.status)})</dd>
                  <dt>Emitente:</dt><dd>${escapeHtml(xml.emitName)} (${escapeHtml(xml.emitCnpjCpf || '—')})</dd>
                  <dt>Destinatário:</dt><dd>${escapeHtml(xml.destName)} (${escapeHtml(xml.destCnpjCpf || 'Não informado')})</dd>
                  <dt>Vlr. Produtos:</dt><dd>${formatMoney(xml.productsValue)}</dd>
                  <dt>Descontos:</dt><dd>${formatMoney(xml.discountValue)}</dd>
                  <dt>Frete / Outras:</dt><dd>${formatMoney(xml.freightValue)} / ${formatMoney(xml.additionValue)}</dd>
                  <dt>Valor Total NF:</dt><dd><strong>${formatMoney(xml.totalValue)}</strong></dd>
                  <dt>Pagamento:</dt><dd>${escapeHtml(xml.primaryPayment)}</dd>
                  <dt>Campos Ausentes:</dt><dd>${
                    xml.missingFields && xml.missingFields.length > 0
                      ? `<span style="color:#b45309;">${escapeHtml(xml.missingFields.join('; '))}</span>`
                      : 'Nenhum campo obrigatório ausente'
                  }</dd>
                </dl>
              `
              : `<p class="text-muted">Registro não encontrado nos arquivos XML importados.</p>`
          }
        </div>

        <!-- Origem 2: Relatório PDF -->
        <div class="source-box">
          <h4>2. Dados no Relatório em PDF</h4>
          ${
            pdf
              ? `
                <dl class="kv-list">
                  <dt>Linha no PDF:</dt><dd>#${escapeHtml(pdf.lineNumber)}</dd>
                  <dt>Número / Série:</dt><dd>${escapeHtml(pdf.number)} / Série ${escapeHtml(pdf.series)}</dd>
                  <dt>Modelo:</dt><dd>${escapeHtml(pdf.model || 'Não informado')}</dd>
                  <dt>Data Emissão:</dt><dd>${formatDateBr(pdf.issueDate)}</dd>
                  <dt>Valor Total:</dt><dd><strong>${formatMoney(pdf.totalValue)}</strong></dd>
                  <dt>Pagamento:</dt><dd>${escapeHtml(pdf.paymentMethod)}</dd>
                  <dt>Status no PDF:</dt><dd>${escapeHtml(pdf.status)}</dd>
                  <dt>Chave no PDF:</dt><dd>${escapeHtml(pdf.accessKey || 'Não consta no relatório')}</dd>
                </dl>
              `
              : `<p class="text-muted">Registro não encontrado no relatório em PDF.</p>`
          }
        </div>

        <!-- Origem 3: Banco Firebird -->
        <div class="source-box">
          <h4>3. Dados no Banco Firebird (2.5 / 5.0)</h4>
          ${
            fb
              ? `
                <dl class="kv-list">
                  <dt>Vínculo Firebird:</dt><dd>${escapeHtml(item.fbMatchCriterion || 'Mapeado')}</dd>
                  <dt>Número / Série:</dt><dd>${escapeHtml(fb.number)} / Série ${escapeHtml(fb.series)}</dd>
                  <dt>Data Emissão:</dt><dd>${formatDateBr(fb.issueDate)}</dd>
                  <dt>Data Autoriz.:</dt><dd>${formatDateBr(fb.authDate)}</dd>
                  <dt>Valor Total:</dt><dd><strong>${formatMoney(fb.totalValue)}</strong></dd>
                  <dt>Status no Banco:</dt><dd>${escapeHtml(fb.status)} (Código: ${escapeHtml(fb.rawStatus)})</dd>
                  <dt>Pagamento:</dt><dd>${escapeHtml(fb.paymentMethod)}</dd>
                  <dt>Chave no Banco:</dt><dd>${escapeHtml(fb.accessKey || 'Não informada')}</dd>
                </dl>
              `
              : `<p class="text-muted">${
                  state.reconciliation?.result?.options?.firebirdEnabled
                    ? 'Registro não localizado na tabela mapeada do Firebird.'
                    : 'Confronto com Firebird não estava ativo nesta execução.'
                }</p>`
          }
        </div>
      </div>

      ${itemsTableHtml}

      <!-- Bloco de Revisão Manual / Decisão do Auditor -->
      <div class="card mt-16">
        <h4>Decisão de Revisão Manual / Auditoria</h4>
        <p class="card-desc">
          Confirme, rejeite ou reclassifique este registro registrando a justificativa técnica para constar nos relatórios exportados:
        </p>
        <div class="form-grid-3">
          <div class="form-group">
            <label for="modalDecisionCategory">Categoria Destino (para Ajuste/Reclassificação)</label>
            <select id="modalDecisionCategory" class="form-control">${categoryOptionsHtml}</select>
          </div>
          <div class="form-group span-2">
            <label for="modalDecisionNote">Observação / Justificativa do Auditor</label>
            <input type="text" id="modalDecisionNote" class="form-control" value="${escapeHtml(
              item.manualDecision?.note || ''
            )}" placeholder="Ex: Divergência de centavos justificada por arredondamento / Confirmado após análise de nota..." />
          </div>
        </div>
        <div class="form-actions-bar mt-12">
          <div class="btn-group">
            <button type="button" class="btn btn-primary" id="btnModalConfirmConciliation">
              &#10004; Confirmar como Conciliado
            </button>
            <button type="button" class="btn btn-warning" id="btnModalReclassify">
              &#9998; Aplicar Categoria Selecionada
            </button>
            <button type="button" class="btn btn-danger" id="btnModalRejectMatch">
              &#10060; Rejeitar Correspondência
            </button>
            ${
              item.manualDecision
                ? `<button type="button" class="btn btn-outline" id="btnModalClearDecision">Restaurar Classificação Automática</button>`
                : ''
            }
          </div>
        </div>
      </div>
    `;

    document.getElementById('btnModalConfirmConciliation').addEventListener('click', () =>
      submitManualDecision(item.id, 'CONFIRM_CONCILIATION', 'CONCILIADO')
    );
    document.getElementById('btnModalReclassify').addEventListener('click', () =>
      submitManualDecision(
        item.id,
        'RECLASSIFY',
        document.getElementById('modalDecisionCategory').value
      )
    );
    document.getElementById('btnModalRejectMatch').addEventListener('click', () =>
      submitManualDecision(item.id, 'REJECT_MATCH', 'REVISAO_MANUAL')
    );
    const clearBtn = document.getElementById('btnModalClearDecision');
    if (clearBtn) {
      clearBtn.addEventListener('click', () => submitManualDecision(item.id, 'CLEAR_DECISION', null));
    }

    modal.classList.remove('hidden');
  }

  async function submitManualDecision(itemId, action, targetCategory) {
    const noteEl = document.getElementById('modalDecisionNote');
    const note = noteEl ? noteEl.value.trim() : '';

    try {
      const res = await fetch('/api/reconcile/decision', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ itemId, action, targetCategory, note })
      });
      const data = await res.json();
      if (!data.success) {
        showNotification(data.message || 'Erro ao salvar decisão.', 'error');
        return;
      }

      state.reconciliation = data.reconciliation;
      document.getElementById('itemDetailModal').classList.add('hidden');
      renderStep6ReconciliationResults();
      showNotification(data.message, 'success');
    } catch (err) {
      showNotification('Erro ao registrar decisão manual: ' + err.message, 'error');
    }
  }

  // ==========================================================================
  // ETAPA 7: EXPORTAÇÃO E METADADOS DE AUDITORIA
  // ==========================================================================

  function renderStep7ExportMetadata() {
    const container = document.getElementById('exportMetadataPreview');
    const rec = state.reconciliation?.result;
    const filteredCount = getFilteredItems().length;

    const xmlNames =
      (state.xmlData?.containerSummaries || []).map((c) => c.name).join(', ') || 'Nenhum arquivo XML';
    const pdfName = state.pdfData?.fileName || 'Nenhum relatório PDF';
    const fbStatus = rec?.options?.firebirdEnabled
      ? `Ativo (Firebird ${state.firebirdData?.engineVersion || ''} - Tabela ${
          state.firebirdData?.mapping?.tableName || ''
        })`
      : 'Não utilizado nesta conciliação';

    container.innerHTML = `
      <div class="meta-item">
        <small>Data e Hora da Análise</small>
        <strong>${rec?.executedAt ? new Date(rec.executedAt).toLocaleString('pt-BR') : 'Pendente'}</strong>
      </div>
      <div class="meta-item">
        <small>Pacotes / Arquivos XML</small>
        <strong>${escapeHtml(xmlNames)}</strong>
      </div>
      <div class="meta-item">
        <small>Relatório de Vendas PDF</small>
        <strong>${escapeHtml(pdfName)}</strong>
      </div>
      <div class="meta-item">
        <small>Conexão Firebird (2.5 / 5.0)</small>
        <strong>${escapeHtml(fbStatus)}</strong>
      </div>
      <div class="meta-item">
        <small>Critério de Comparação</small>
        <strong>${escapeHtml(rec?.options?.dateFieldLabel || 'Data de Emissão')} (Sem vínculo aproximado)</strong>
      </div>
      <div class="meta-item">
        <small>Registros Selecionados pelos Filtros Ativos</small>
        <strong>${filteredCount} de ${rec?.items?.length || 0} registro(s)</strong>
      </div>
    `;
  }

  async function handleExportDownload(format) {
    setProgress(true, `Gerando arquivo de exportação (${format.toUpperCase()})...`, 80);
    try {
      const res = await fetch(`/api/export/${format}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filters: state.filters })
      });

      if (!res.ok) {
        const errData = await res.json();
        setProgress(false);
        showNotification(errData.message || 'Erro ao exportar.', 'error');
        return;
      }

      const blob = await res.blob();
      const disp = res.headers.get('Content-Disposition') || '';
      const match = disp.match(/filename="([^"]+)"/);
      const filename = match ? match[1] : `conciliacao-fiscal.${format}`;

      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);

      setProgress(false);
      showNotification(`Exportação "${filename}" concluída com sucesso.`, 'success');
    } catch (err) {
      setProgress(false);
      showNotification('Erro ao baixar exportação: ' + err.message, 'error');
    }
  }

  // ==========================================================================
  // MODO DE DEMONSTRAÇÃO E MODAL DE SEGURANÇA/ARMAZENAMENTO LOCAL
  // ==========================================================================

  async function activateDemoMode(firebirdVersion = '2.5') {
    setProgress(
      true,
      `Carregando dados fictícios de demonstração (XMLs em subpastas + PDF + Firebird ${firebirdVersion})...`,
      75
    );
    try {
      const res = await fetch('/api/demo/activate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ firebirdVersion })
      });
      const data = await res.json();
      setProgress(false);

      if (!data.success) {
        showNotification(data.message || 'Erro ao carregar demonstração.', 'error');
        return;
      }

      applyServerState(data);
      goToStep(6);
      showNotification(data.message, 'success');
    } catch (err) {
      setProgress(false);
      showNotification('Erro ao ativar demonstração: ' + err.message, 'error');
    }
  }

  async function exitDemoMode() {
    if (!window.confirm('Deseja sair do Modo de Demonstração e limpar os dados fictícios da sessão?')) {
      return;
    }
    try {
      await fetch('/api/demo/exit', { method: 'POST' });
      await fetchInitialStatus();
      goToStep(1);
      showNotification('Modo de demonstração encerrado. Sessão pronta para dados reais.', 'info');
    } catch (err) {
      showNotification('Erro ao encerrar demonstração: ' + err.message, 'error');
    }
  }

  async function openStorageModal() {
    const modal = document.getElementById('storageModal');
    const container = document.getElementById('storageDetailsContainer');
    modal.classList.remove('hidden');

    try {
      const res = await fetch('/api/storage');
      const st = await res.json();
      state.storage = st;

      const dirsHtml = Object.values(st.directories || {})
        .map(
          (d) => `
          <tr>
            <td><code>${escapeHtml(d.path)}</code></td>
            <td>${escapeHtml(d.description)}</td>
            <td>${d.fileCount} arquivo(s)</td>
            <td>${(Number(d.totalBytes || 0) / 1024).toFixed(1)} KB</td>
          </tr>
        `
        )
        .join('');

      const logsHtml =
        st.recentLogs && st.recentLogs.length > 0
          ? st.recentLogs
              .slice(0, 12)
              .map(
                (l) =>
                  `[${l.timestamp.slice(11, 19)}] [${l.level.toUpperCase()}] [${l.context}] ${l.message}`
              )
              .join('\n')
          : 'Nenhum evento técnico registrado.';

      container.innerHTML = `
        <p style="margin-bottom:8px;">
          Diretório base local: <code>${escapeHtml(st.baseDirectory)}</code> (Uso total: <strong>${escapeHtml(
        st.totalFormatted
      )}</strong> em ${st.totalFiles} arquivo(s))
        </p>
        <div class="table-responsive">
          <table class="data-table">
            <thead>
              <tr>
                <th>Caminho Local no Computador</th>
                <th>Finalidade</th>
                <th>Arquivos</th>
                <th>Espaço</th>
              </tr>
            </thead>
            <tbody>${dirsHtml}</tbody>
          </table>
        </div>
        <h4 class="mt-16">Logs Técnicos Locais (Com Sanitização Automática de Credenciais)</h4>
        <pre class="code-preview mt-12">${escapeHtml(logsHtml)}</pre>
      `;
    } catch (err) {
      container.innerHTML = `<p class="text-muted">Erro ao consultar diretórios locais: ${escapeHtml(err.message)}</p>`;
    }
  }

  async function handleConfirmClearStorage() {
    const keepConfig = document.getElementById('chkKeepFbConfigOnClear').checked;
    const confirmed = window.confirm(
      'Confirma a remoção imediata dos dados temporários locais e limpeza da sessão atual? (Seus arquivos originais fora da aplicação nunca são alterados).'
    );
    if (!confirmed) return;

    try {
      const res = await fetch('/api/storage/clear', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ keepConfig })
      });
      const data = await res.json();
      if (data.success) {
        await fetchInitialStatus();
        await openStorageModal();
        goToStep(1);
        showNotification(data.message, 'success');
      }
    } catch (err) {
      showNotification('Erro ao remover dados locais: ' + err.message, 'error');
    }
  }

  // ==========================================================================
  // DASHBOARD DE AUDITORIA FISCAL CONTÁBIL (55 / 65 / CFOP / NCM / 101-102 / MATRIZ)
  // ==========================================================================

  async function handleSidebarConnectDb() {
    const host = document.getElementById('sbDbHost')?.value || '127.0.0.1';
    const port = Number(document.getElementById('sbDbPort')?.value || 3050);
    const database = document.getElementById('sbDbPath')?.value || '';
    const user = document.getElementById('sbDbUser')?.value || 'SYSDBA';
    const password = document.getElementById('sbDbPass')?.value || 'masterkey';

    setProgress(true, `Conectando e sincronizando base Firebird (${database})...`, 65);
    try {
      const res = await fetch('/api/db/connect-clipp', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ host, port, database, user, password })
      });
      const data = await res.json();
      setProgress(false);
      if (data.auditDashboard) state.auditDashboard = data.auditDashboard;
      renderAuditExecutiveDashboard();
      const sbTxt = document.getElementById('sbDbStatusText');
      if (sbTxt) sbTxt.textContent = data.message || 'Base conectada.';
      showNotification(data.message || 'Base sincronizada.', data.success ? 'success' : 'warning');
    } catch (err) {
      setProgress(false);
      showNotification('Erro ao conectar base: ' + err.message, 'error');
    }
  }

  async function handleSidebarApplyCompetence() {
    const startDate = document.getElementById('sbStartDate')?.value || '';
    const endDate = document.getElementById('sbEndDate')?.value || '';
    const radio = document.querySelector('input[name="sbDateBaseRadio"]:checked');
    const dateField = radio ? radio.value : 'issueDate';

    setProgress(true, 'Recalculando competência fiscal (Emissão vs Autorização SEFAZ)...', 70);
    try {
      const res = await fetch('/api/audit/dashboard', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ startDate, endDate, dateField })
      });
      const data = await res.json();
      setProgress(false);
      if (data.auditDashboard) state.auditDashboard = data.auditDashboard;
      renderAuditExecutiveDashboard();
      showNotification('Filtro de competência e base temporal atualizados.', 'success');
    } catch (err) {
      setProgress(false);
      showNotification('Erro ao aplicar competência: ' + err.message, 'error');
    }
  }

  async function handleImportXmlFolder() {
    const folderPath = document.getElementById('xmlFolderInputPath')?.value?.trim() || '';
    if (!folderPath) {
      showNotification('Informe o caminho local da pasta de XMLs.', 'warning');
      return;
    }
    setProgress(true, `Lendo pasta local de XMLs (${folderPath})...`, 60);
    try {
      const res = await fetch('/api/xml/import-folder', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ folderPath })
      });
      const data = await res.json();
      setProgress(false);
      if (!data.success) {
        showNotification(data.message || 'Erro ao ler pasta de XMLs.', 'error');
        return;
      }
      state.xmlData = data.xmlData;
      if (data.auditDashboard) state.auditDashboard = data.auditDashboard;
      updateGlobalBannersAndSidebar();
      renderStep1XmlSummary();
      renderAuditExecutiveDashboard();
      showNotification(data.message, 'success');
    } catch (err) {
      setProgress(false);
      showNotification('Erro ao ler pasta de XMLs: ' + err.message, 'error');
    }
  }

  function renderAuditExecutiveDashboard() {
    const ad = state.auditDashboard;
    if (!ad) return;

    const k = ad.kpiCards || {};
    const bat = k.statusBatimento || {};
    const comp = ad.competenceFilter || {};

    const subEl = document.getElementById('execAuditCompetenceSubtitle');
    if (subEl) {
      const perTxt =
        comp.startDate || comp.endDate
          ? ` | Período: ${formatDateBr(comp.startDate)} até ${formatDateBr(comp.endDate)}`
          : ' | Período: Todas as competências';
      subEl.textContent = `Base ativa: ${comp.dateFieldLabel || 'Por Data de Emissão'}${perTxt}`;
    }

    // 1. Top 5 KPI Cards
    const kpiGrid = document.getElementById('execTopKpiCardsGrid');
    if (kpiGrid) {
      const batClass =
        bat.badge === 'success' ? 'kpi-ok' : bat.badge === 'danger' ? 'kpi-danger' : 'kpi-warn';
      kpiGrid.innerHTML = `
        <div class="kpi-card">
          <div class="kpi-label">Total Banco (Emissão)</div>
          <div class="kpi-value">${formatMoney(k.totalBancoEmissao || 0)}</div>
          <div class="kpi-sub">${k.qtdBancoEmissao || 0} notas (<code>tb_nfvenda.dt_emissao</code>)</div>
        </div>
        <div class="kpi-card">
          <div class="kpi-label">Total Banco (Autorização SEFAZ)</div>
          <div class="kpi-value">${formatMoney(k.totalBancoAutorizacao || 0)}</div>
          <div class="kpi-sub">${k.qtdBancoAutorizacao || 0} notas | D+1: ${k.qtdDPlus1 || 0} (${formatMoney(
        k.divergenciaEmissaoVsAutorizacao || 0
      )})</div>
        </div>
        <div class="kpi-card">
          <div class="kpi-label">Total XMLs Importados</div>
          <div class="kpi-value">${formatMoney(k.totalXmlImportados || 0)}</div>
          <div class="kpi-sub">${k.qtdXmlImportados || 0} XMLs autorizados (Status 100)</div>
        </div>
        <div class="kpi-card">
          <div class="kpi-label">Total Relatório PDF</div>
          <div class="kpi-value">${formatMoney(k.totalRelatorioPdf || 0)}</div>
          <div class="kpi-sub">${k.qtdRelatorioPdf || 0} registros no relatório</div>
        </div>
        <div class="kpi-card ${batClass}">
          <div class="kpi-label">Status de Batimento</div>
          <div class="kpi-value" style="font-size:16px;">${escapeHtml(bat.label || 'Aguardando')}</div>
          <div class="kpi-sub">${escapeHtml(bat.detail || '')}</div>
        </div>
      `;
    }

    // 2. Comparativo Triplo (Geral, 55, 65)
    const tcBody = document.querySelector('#execTripleComparisonTable tbody');
    if (tcBody && ad.tripleComparison) {
      tcBody.innerHTML = ['ALL', '55', '65']
        .map((mKey) => {
          const r = ad.tripleComparison[mKey];
          if (!r) return '';
          const bBadge =
            r.batimento?.badge === 'success'
              ? 'badge-success'
              : r.batimento?.badge === 'danger'
              ? 'badge-danger'
              : 'badge-info';
          return `
            <tr>
              <td><strong>${escapeHtml(r.label)}</strong></td>
              <td><strong>${formatMoney(r.totalBancoEmissao)}</strong> <small class="text-muted">(${r.qtdBancoEmissao})</small></td>
              <td><strong>${formatMoney(r.totalBancoAutorizacao)}</strong> <small class="text-muted">(${r.qtdBancoAutorizacao})</small></td>
              <td>${formatMoney(r.divergenciaDPlus1)}</td>
              <td><strong>${formatMoney(r.totalXml)}</strong> <small class="text-muted">(${r.qtdXml})</small></td>
              <td><strong>${formatMoney(r.totalPdf)}</strong> <small class="text-muted">(${r.qtdPdf})</small></td>
              <td><span class="badge ${bBadge}">${escapeHtml(r.batimento?.label || '')}</span></td>
            </tr>
          `;
        })
        .join('');
    }

    // Atualiza badges do menu lateral
    const tc55 = ad.tripleComparison?.['55'];
    const tc65 = ad.tripleComparison?.['65'];
    const sep = ad.separateEvents || {};
    const divMat = ad.divergenceMatrix || [];

    const s8 = document.getElementById('step8Meta');
    if (s8 && tc55) s8.textContent = `${tc55.qtdBancoEmissao} notas (${formatMoney(tc55.totalBancoEmissao)})`;
    const s9 = document.getElementById('step9Meta');
    if (s9 && tc65) s9.textContent = `${tc65.qtdBancoEmissao} cupons (${formatMoney(tc65.totalBancoEmissao)})`;
    const s10 = document.getElementById('step10Meta');
    if (s10) s10.textContent = `101: ${sep.qtdCanceladas101 || 0} | 102: ${sep.qtdInutilizadas102 || 0}`;
    const s11 = document.getElementById('step11Meta');
    if (s11) s11.textContent = `${divMat.length} divergência(s) / D+1`;

    // 3. Tabelas Dinâmicas CFOP e NCM para Modelo 55 e Modelo 65
    ['55', '65'].forEach((mod) => {
      const fg = ad.fiscalGroupings?.[mod] || { cfop: [], ncm: [] };
      const cfopTbody = document.querySelector(`#cfopTable${mod} tbody`);
      if (cfopTbody) {
        cfopTbody.innerHTML =
          (fg.cfop || []).length === 0
            ? `<tr><td colspan="11" class="text-muted">Nenhum registro CFOP para o modelo ${mod} no período.</td></tr>`
            : (fg.cfop || [])
                .map(
                  (c) => `
                  <tr>
                    <td><strong>${escapeHtml(c.cfop)}</strong></td>
                    <td>${c.qtdNotasBanco}</td>
                    <td>${formatMoney(c.baseCalculoBanco)}</td>
                    <td>${formatMoney(c.valorIcmsBanco)}</td>
                    <td><strong>${formatMoney(c.valorContabilBanco)}</strong></td>
                    <td>${c.qtdNotasXml}</td>
                    <td>${formatMoney(c.baseCalculoXml)}</td>
                    <td>${formatMoney(c.valorIcmsXml)}</td>
                    <td><strong>${formatMoney(c.valorContabilXml)}</strong></td>
                    <td>${formatMoney(c.difValorContabil)}</td>
                    <td><span class="badge ${c.status === 'Bateu' ? 'badge-success' : 'badge-warning'}">${escapeHtml(
                    c.status
                  )}</span></td>
                  </tr>
                `
                )
                .join('');
      }

      const ncmTbody = document.querySelector(`#ncmTable${mod} tbody`);
      if (ncmTbody) {
        ncmTbody.innerHTML =
          (fg.ncm || []).length === 0
            ? `<tr><td colspan="10" class="text-muted">Nenhum item NCM para o modelo ${mod} no período.</td></tr>`
            : (fg.ncm || [])
                .slice(0, 250)
                .map(
                  (n) => `
                  <tr>
                    <td><strong>${escapeHtml(n.ncm)}</strong></td>
                    <td>${escapeHtml(n.descricao)}</td>
                    <td>${n.qtdItensBanco}</td>
                    <td>${formatMoney(n.baseCalculoBanco)}</td>
                    <td><strong>${formatMoney(n.valorContabilBanco)}</strong></td>
                    <td>${n.qtdItensXml}</td>
                    <td>${formatMoney(n.baseCalculoXml)}</td>
                    <td><strong>${formatMoney(n.valorContabilXml)}</strong></td>
                    <td>${formatMoney(n.difValorContabil)}</td>
                    <td><span class="badge ${n.status === 'Bateu' ? 'badge-success' : 'badge-warning'}">${escapeHtml(
                    n.status
                  )}</span></td>
                  </tr>
                `
                )
                .join('');
      }
    });

    // 4. Eventos Separados (Canceladas 101 e Inutilizadas 102)
    const evKpi = document.getElementById('eventsKpiGrid');
    if (evKpi) {
      evKpi.innerHTML = `
        <div class="kpi-card kpi-danger">
          <div class="kpi-label">Canceladas (Status 101)</div>
          <div class="kpi-value">${sep.qtdCanceladas101 || 0}</div>
          <div class="kpi-sub">Total isolado: ${formatMoney(sep.valorCanceladas101 || 0)}</div>
        </div>
        <div class="kpi-card kpi-warn">
          <div class="kpi-label">Inutilizadas (Status 102)</div>
          <div class="kpi-value">${sep.qtdInutilizadas102 || 0}</div>
          <div class="kpi-sub">Total isolado: ${formatMoney(sep.valorInutilizadas102 || 0)}</div>
        </div>
        <div class="kpi-card kpi-ok">
          <div class="kpi-label">Isolamento do Faturamento Líquido</div>
          <div class="kpi-value">100% Ativo</div>
          <div class="kpi-sub">Notas 101 e 102 não somam no faturamento Status 100</div>
        </div>
      `;
    }
    renderSeparateEventsTable();
    renderDivergenceMatrixTable();
  }

  function renderSeparateEventsTable() {
    const ad = state.auditDashboard;
    const tbody = document.querySelector('#separateEventsTable tbody');
    if (!tbody || !ad) return;
    const sep = ad.separateEvents || {};
    const filterVal = document.getElementById('eventsFilterSelect')?.value || 'ALL';
    let rows = sep.allEvents || [];
    if (filterVal === '101') rows = sep.canceladas101 || [];
    else if (filterVal === '102') rows = sep.inutilizadas102 || [];

    if (rows.length === 0) {
      tbody.innerHTML = `<tr><td colspan="10" class="text-muted">Nenhum evento Status 101 ou 102 encontrado para o filtro selecionado.</td></tr>`;
      return;
    }

    tbody.innerHTML = rows
      .map((ev) => {
        const fontes = [];
        if (ev.constaBanco) fontes.push('Banco');
        if (ev.constaXml) fontes.push('XML');
        if (ev.constaPdf) fontes.push('PDF');
        return `
          <tr>
            <td><span class="badge badge-danger">${escapeHtml(ev.status_codigo)}</span></td>
            <td><strong>${escapeHtml(ev.status)}</strong></td>
            <td><strong>${escapeHtml(ev.nf_numero)}</strong></td>
            <td>Série ${escapeHtml(ev.nf_serie)} / Mod ${escapeHtml(ev.nf_modelo)}</td>
            <td><code>${escapeHtml(ev.id_nfe)}</code></td>
            <td>${formatDateBr(ev.dt_emissao)}</td>
            <td>${escapeHtml(ev.data_hora_sefaz || '—')}</td>
            <td>${escapeHtml(ev.status_des)}</td>
            <td><strong>${formatMoney(ev.valor)}</strong></td>
            <td>${escapeHtml(fontes.join(' + ') || '—')}</td>
          </tr>
        `;
      })
      .join('');
  }

  function renderDivergenceMatrixTable() {
    const ad = state.auditDashboard;
    const tbody = document.querySelector('#divergenceMatrixTable tbody');
    if (!tbody || !ad) return;
    const filterCode = document.getElementById('matrixOriginFilterSelect')?.value || 'ALL';
    const allRows = ad.divergenceMatrix || [];
    const filtered = allRows.filter((dv) => {
      const c = dv.codigo_falha || '';
      if (filterCode === 'ALL') return true;
      if (filterCode === 'RELATORIO_SEM_XML') return c === 'RELATORIO_SEM_XML';
      if (filterCode === 'XML_SEM_RELATORIO_OU_BANCO')
        return c === 'XML_SEM_RELATORIO_E_BANCO' || c === 'XML_SEM_BANCO' || c === 'XML_SEM_RELATORIO';
      if (filterCode === 'BANCO_SEM_XML') return c === 'BANCO_SEM_XML';
      if (filterCode === 'DIVERGENCIA_D_MAIS_1') return c === 'DIVERGENCIA_D_MAIS_1';
      if (filterCode === 'OUTRAS') return c === 'DIVERGENCIA_VALOR' || c === 'CHAVE_INVALIDA_OU_AUSENTE';
      return true;
    });

    const badgeEl = document.getElementById('matrixCountBadge');
    if (badgeEl) badgeEl.textContent = filtered.length;

    if (filtered.length === 0) {
      tbody.innerHTML = `<tr><td colspan="8" class="text-muted">Nenhuma divergência encontrada para o filtro selecionado.</td></tr>`;
      return;
    }

    tbody.innerHTML = filtered
      .slice(0, 500)
      .map(
        (dv) => `
        <tr>
          <td><strong>${escapeHtml(dv.nf_numero)}</strong></td>
          <td>Mod ${escapeHtml(dv.nf_modelo)} / Série ${escapeHtml(dv.nf_serie)}</td>
          <td><code>${escapeHtml(dv.id_nfe)}</code></td>
          <td>${formatDateBr(dv.dt_emissao)}</td>
          <td>${escapeHtml(dv.data_hora_sefaz || '—')}</td>
          <td>${escapeHtml(dv.status_des)}</td>
          <td><strong>${formatMoney(dv.valor)}</strong></td>
          <td><span class="badge badge-warning">${escapeHtml(dv.origem_falha)}</span></td>
        </tr>
      `
      )
      .join('');
  }

  // ==========================================================================
  // REGISTRO DE EVENTOS DA INTERFACE
  // ==========================================================================

  function bindEvents() {
    // Botões do Stepper lateral e botões data-goto
    document.querySelectorAll('.step-btn').forEach((btn) => {
      btn.addEventListener('click', () => goToStep(btn.dataset.step));
    });
    document.querySelectorAll('[data-goto]').forEach((btn) => {
      btn.addEventListener('click', () => goToStep(btn.dataset.goto));
    });

    // Sub-abas da Etapa 3
    document.querySelectorAll('.subtab-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        const target = btn.dataset.subtab;
        document.querySelectorAll('.subtab-btn').forEach((b) => b.classList.toggle('active', b === btn));
        document.querySelectorAll('.subtab-content').forEach((c) => c.classList.toggle('active', c.id === target));
      });
    });

    // Upload XMLs (Input e Drag & Drop)
    const xmlInput = document.getElementById('xmlFileInput');
    const xmlDropzone = document.getElementById('xmlDropzone');
    xmlInput.addEventListener('change', (e) => handleXmlFilesUpload(e.target.files));
    ['dragover', 'dragenter'].forEach((ev) =>
      xmlDropzone.addEventListener(ev, (e) => {
        e.preventDefault();
        xmlDropzone.classList.add('dragover');
      })
    );
    ['dragleave', 'drop'].forEach((ev) =>
      xmlDropzone.addEventListener(ev, (e) => {
        e.preventDefault();
        xmlDropzone.classList.remove('dragover');
      })
    );
    xmlDropzone.addEventListener('drop', (e) => {
      if (e.dataTransfer?.files?.length) handleXmlFilesUpload(e.dataTransfer.files);
    });

    // Upload PDF (Input e Drag & Drop)
    const pdfInput = document.getElementById('pdfFileInput');
    const pdfDropzone = document.getElementById('pdfDropzone');
    pdfInput.addEventListener('change', (e) => {
      if (e.target.files?.[0]) handlePdfFileUpload(e.target.files[0]);
    });
    ['dragover', 'dragenter'].forEach((ev) =>
      pdfDropzone.addEventListener(ev, (e) => {
        e.preventDefault();
        pdfDropzone.classList.add('dragover');
      })
    );
    ['dragleave', 'drop'].forEach((ev) =>
      pdfDropzone.addEventListener(ev, (e) => {
        e.preventDefault();
        pdfDropzone.classList.remove('dragover');
      })
    );
    pdfDropzone.addEventListener('drop', (e) => {
      if (e.dataTransfer?.files?.[0]) handlePdfFileUpload(e.dataTransfer.files[0]);
    });

    document.getElementById('btnRunPdfOcr').addEventListener('click', handleRunPdfOcr);

    // Etapa 3: Mapeamento e edição do PDF
    document.getElementById('btnApplyPdfMapping').addEventListener('click', handleApplyPdfColumnMapping);
    document.getElementById('btnSavePdfEdits').addEventListener('click', handleSavePdfEdits);
    document.getElementById('btnAddPdfRow').addEventListener('click', () => {
      if (!state.pdfData) {
        state.pdfData = { fileName: 'Manual', records: [], columnsMeta: [] };
      }
      state.pdfData.records = collectEditedPdfRowsFromDom();
      state.pdfData.records.push({
        id: `pdf_row_${Date.now()}`,
        issueDate: new Date().toISOString().slice(0, 10),
        number: '',
        series: '1',
        model: '65',
        totalValue: 0,
        paymentMethod: 'PIX',
        status: 'AUTORIZADO',
        accessKey: '',
        manuallyEdited: true
      });
      renderStep3ReviewAndMapping();
    });

    // Etapa 4: Firebird
    document.getElementById('btnTestFirebird').addEventListener('click', handleTestFirebirdConnection);
    document.getElementById('btnDiscoverSchema').addEventListener('click', handleDiscoverFirebirdSchema);
    document.getElementById('btnPreviewFirebird').addEventListener('click', handlePreviewFirebirdMapping);
    document.getElementById('fbTableSelect').addEventListener('change', (e) => {
      populateFirebirdColumnSelects(e.target.value, null);
    });
    document.getElementById('fbEnableToggle').addEventListener('change', async (e) => {
      const enabled = e.target.checked;
      if (state.firebirdData) state.firebirdData.enabled = enabled;
      await fetch('/api/firebird/toggle', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled })
      });
      updateGlobalBannersAndSidebar();
    });

    // Etapa 5 & 6: Conciliação e Alternância de Data
    document
      .getElementById('btnExecuteReconciliation')
      .addEventListener('click', () => executeReconciliationRequest(null));

    document.getElementById('quickDateModeSelect').addEventListener('change', (e) => {
      executeReconciliationRequest(e.target.value);
    });

    // Filtros da Etapa 6
    const filterBindings = [
      { id: 'filterCategory', key: 'category', event: 'change' },
      { id: 'filterStatus', key: 'status', event: 'change' },
      { id: 'filterDateFrom', key: 'dateFrom', event: 'change' },
      { id: 'filterDateTo', key: 'dateTo', event: 'change' },
      { id: 'filterNumber', key: 'number', event: 'input' },
      { id: 'filterSeries', key: 'series', event: 'input' },
      { id: 'filterPayment', key: 'payment', event: 'change' },
      { id: 'filterSearch', key: 'search', event: 'input' }
    ];

    filterBindings.forEach(({ id, key, event }) => {
      const el = document.getElementById(id);
      if (el) {
        el.addEventListener(event, (e) => {
          state.filters[key] = e.target.value;
          renderStep6ReconciliationResults();
        });
      }
    });

    document.getElementById('btnClearFilters').addEventListener('click', () => {
      state.filters = {
        category: 'ALL',
        status: 'ALL',
        dateFrom: '',
        dateTo: '',
        number: '',
        series: '',
        payment: 'ALL',
        search: ''
      };
      filterBindings.forEach(({ id, key }) => {
        const el = document.getElementById(id);
        if (el) el.value = state.filters[key];
      });
      renderStep6ReconciliationResults();
    });

    // Etapa 7: Botões de Exportação
    document.querySelectorAll('[data-export]').forEach((btn) => {
      btn.addEventListener('click', () => handleExportDownload(btn.dataset.export));
    });

    // Modo Demonstração
    document.getElementById('btnLoadDemo').addEventListener('click', () => activateDemoMode('2.5'));
    document.getElementById('btnSwitchDemoFb25').addEventListener('click', () => activateDemoMode('2.5'));
    document.getElementById('btnSwitchDemoFb50').addEventListener('click', () => activateDemoMode('5.0'));
    document.getElementById('btnExitDemo').addEventListener('click', exitDemoMode);

    // Modais
    document.getElementById('btnCloseDetailModal').addEventListener('click', () => {
      document.getElementById('itemDetailModal').classList.add('hidden');
    });
    document.getElementById('btnOpenStorageModal').addEventListener('click', openStorageModal);
    document.getElementById('btnCloseStorageModal').addEventListener('click', () => {
      document.getElementById('storageModal').classList.add('hidden');
    });
    document.getElementById('btnConfirmClearStorage').addEventListener('click', handleConfirmClearStorage);

    // Eventos do Menu Lateral e Módulos de Auditoria (55, 65, 101/102, Matriz)
    const btnSbDb = document.getElementById('btnSidebarConnectDb');
    if (btnSbDb) btnSbDb.addEventListener('click', handleSidebarConnectDb);

    const btnSbComp = document.getElementById('btnSidebarApplyCompetence');
    if (btnSbComp) btnSbComp.addEventListener('click', handleSidebarApplyCompetence);

    document.querySelectorAll('input[name="sbDateBaseRadio"]').forEach((r) => {
      r.addEventListener('change', handleSidebarApplyCompetence);
    });

    const btnImpFolder = document.getElementById('btnImportXmlFolder');
    if (btnImpFolder) btnImpFolder.addEventListener('click', handleImportXmlFolder);

    const evSel = document.getElementById('eventsFilterSelect');
    if (evSel) evSel.addEventListener('change', renderSeparateEventsTable);

    const matSel = document.getElementById('matrixOriginFilterSelect');
    if (matSel) matSel.addEventListener('change', renderDivergenceMatrixTable);
  }

  document.addEventListener('DOMContentLoaded', () => {
    bindEvents();
    fetchInitialStatus();
  });
})();
