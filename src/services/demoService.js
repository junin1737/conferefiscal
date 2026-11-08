const { AdmZip } = require('../lib/zipCodec');
const { PDFDocument } = require('../lib/pdfCodec');

/**
 * Constrói um XML de NF-e (55) ou NFC-e (65) realista seguindo o layout SEFAZ 4.00.
 */
function buildSampleNFeXml({
  mod = '65',
  serie = '1',
  nNF = '1001',
  chNFe = '35261012345678000199650010000010011000010015',
  dhEmi = '2026-10-15T09:15:00-03:00',
  dhRecbto = '2026-10-15T09:15:04-03:00',
  nProt = '135260000100101',
  cStat = '100',
  xMotivo = 'Autorizado o uso da NF-e',
  tpNF = '1',
  finNFe = '1',
  natOp = 'VENDA DE MERCADORIA ADQUIRIDA DE TERCEIROS',
  emitCnpj = '12345678000199',
  emitNome = 'COMERCIAL ALFA DISTRIBUIDORA LTDA',
  destDoc = '12345678909',
  destDocType = 'CPF',
  destNome = 'CLIENTE EXEMPLO CONSUMIDOR',
  vProd = '150.00',
  vDesc = '0.00',
  vFrete = '0.00',
  vOutro = '0.00',
  vNF = '150.00',
  vTotTrib = '24.50',
  tPag = '17',
  vPag = '150.00',
  cfop = '5102',
  includeProt = true,
  items = null
}) {
  const destBlock = destDoc
    ? `<dest><${destDocType}>${destDoc}</${destDocType}><xNome>${destNome}</xNome><indIEDest>9</indIEDest></dest>`
    : '';

  const itemList = items || [
    {
      nItem: 1,
      cProd: `PRD-${nNF}-01`,
      xProd: `MERCADORIA FISCAL ITEM PRINCIPAL DOC ${nNF}`,
      ncm: '84716052',
      cfop,
      uCom: 'UN',
      qCom: '2.0000',
      vUnCom: (Number(vProd) / 2).toFixed(2),
      vProd,
      vDesc,
      vFrete,
      vOutro,
      vICMS: (Number(vNF) * 0.18).toFixed(2),
      vPIS: (Number(vNF) * 0.0165).toFixed(2),
      vCOFINS: (Number(vNF) * 0.076).toFixed(2),
      vTotTrib
    }
  ];

  const detXml = itemList
    .map(
      (it) => `
      <det nItem="${it.nItem}">
        <prod>
          <cProd>${it.cProd}</cProd>
          <cEAN>SEM GTIN</cEAN>
          <xProd>${it.xProd}</xProd>
          <NCM>${it.ncm}</NCM>
          <CFOP>${it.cfop}</CFOP>
          <uCom>${it.uCom}</uCom>
          <qCom>${it.qCom}</qCom>
          <vUnCom>${it.vUnCom}</vUnCom>
          <vProd>${it.vProd}</vProd>
          <vDesc>${it.vDesc || '0.00'}</vDesc>
          <vFrete>${it.vFrete || '0.00'}</vFrete>
          <vOutro>${it.vOutro || '0.00'}</vOutro>
          <indTot>1</indTot>
        </prod>
        <imposto>
          <vTotTrib>${it.vTotTrib || '0.00'}</vTotTrib>
          <ICMS>
            <ICMS00>
              <orig>0</orig>
              <CST>00</CST>
              <modBC>3</modBC>
              <vBC>${it.vProd}</vBC>
              <pICMS>18.00</pICMS>
              <vICMS>${it.vICMS}</vICMS>
            </ICMS00>
          </ICMS>
          <PIS>
            <PISAliq>
              <CST>01</CST>
              <vBC>${it.vProd}</vBC>
              <pPIS>1.65</pPIS>
              <vPIS>${it.vPIS}</vPIS>
            </PISAliq>
          </PIS>
          <COFINS>
            <COFINSAliq>
              <CST>01</CST>
              <vBC>${it.vProd}</vBC>
              <pCOFINS>7.60</pCOFINS>
              <vCOFINS>${it.vCOFINS}</vCOFINS>
            </COFINSAliq>
          </COFINS>
        </imposto>
      </det>`
    )
    .join('');

  const nfeBody = `
  <NFe xmlns="http://www.portalfiscal.inf.br/nfe">
    <infNFe Id="NFe${chNFe}" versao="4.00">
      <ide>
        <cUF>35</cUF>
        <cNF>0000${nNF}</cNF>
        <natOp>${natOp}</natOp>
        <mod>${mod}</mod>
        <serie>${serie}</serie>
        <nNF>${nNF}</nNF>
        <dhEmi>${dhEmi}</dhEmi>
        <tpNF>${tpNF}</tpNF>
        <idDest>1</idDest>
        <cMunFG>3550308</cMunFG>
        <tpImp>${mod === '65' ? '4' : '1'}</tpImp>
        <tpEmis>1</tpEmis>
        <cDV>${chNFe.slice(-1)}</cDV>
        <tpAmb>1</tpAmb>
        <finNFe>${finNFe}</finNFe>
        <indFinal>1</indFinal>
        <indPres>1</indPres>
        <procEmi>0</procEmi>
        <verProc>ConfereFiscal-1.0</verProc>
      </ide>
      <emit>
        <CNPJ>${emitCnpj}</CNPJ>
        <xNome>${emitNome}</xNome>
        <xFant>ALFA VAREJO E DISTRIBUICAO</xFant>
        <enderEmit>
          <xLgr>AVENIDA PAULISTA</xLgr>
          <nro>1000</nro>
          <xBairro>BELA VISTA</xBairro>
          <cMun>3550308</cMun>
          <xMun>SAO PAULO</xMun>
          <UF>SP</UF>
          <CEP>01310100</CEP>
        </enderEmit>
        <IE>110042490114</IE>
        <CRT>3</CRT>
      </emit>
      ${destBlock}
      ${detXml}
      <total>
        <ICMSTot>
          <vBC>${vProd}</vBC>
          <vICMS>${(Number(vNF) * 0.18).toFixed(2)}</vICMS>
          <vICMSDeson>0.00</vICMSDeson>
          <vFCP>0.00</vFCP>
          <vBCST>0.00</vBCST>
          <vST>0.00</vST>
          <vFCPST>0.00</vFCPST>
          <vFCPSTRet>0.00</vFCPSTRet>
          <vProd>${vProd}</vProd>
          <vFrete>${vFrete}</vFrete>
          <vSeg>0.00</vSeg>
          <vDesc>${vDesc}</vDesc>
          <vII>0.00</vII>
          <vIPI>0.00</vIPI>
          <vIPIDevol>0.00</vIPIDevol>
          <vPIS>${(Number(vNF) * 0.0165).toFixed(2)}</vPIS>
          <vCOFINS>${(Number(vNF) * 0.076).toFixed(2)}</vCOFINS>
          <vOutro>${vOutro}</vOutro>
          <vNF>${vNF}</vNF>
          <vTotTrib>${vTotTrib}</vTotTrib>
        </ICMSTot>
      </total>
      <transp>
        <modFrete>9</modFrete>
      </transp>
      <pag>
        <detPag>
          <tPag>${tPag}</tPag>
          <vPag>${vPag}</vPag>
        </detPag>
        <vTroco>0.00</vTroco>
      </pag>
    </infNFe>
  </NFe>`;

  if (!includeProt) {
    return `<?xml version="1.0" encoding="UTF-8"?>${nfeBody.trim()}`;
  }

  return `<?xml version="1.0" encoding="UTF-8"?>
<nfeProc versao="4.00" xmlns="http://www.portalfiscal.inf.br/nfe">
  ${nfeBody.trim()}
  <protNFe versao="4.00">
    <infProt>
      <tpAmb>1</tpAmb>
      <verAplic>SP_NFCE_PL_009_V400</verAplic>
      <chNFe>${chNFe}</chNFe>
      <dhRecbto>${dhRecbto}</dhRecbto>
      <nProt>${nProt}</nProt>
      <digVal>dGVzdGVkaWd2YWw=</digVal>
      <cStat>${cStat}</cStat>
      <xMotivo>${xMotivo}</xMotivo>
    </infProt>
  </protNFe>
</nfeProc>`;
}

/**
 * Constrói um XML de Evento de Cancelamento (procEventoNFe tpEvento=110111).
 */
function buildCancelEventXml({
  chNFe = '35261012345678000199650010000010071000010078',
  dhEvento = '2026-10-16T15:18:00-03:00',
  nProtOrig = '135260000100107',
  nProtCanc = '135260000999107',
  xJust = 'Cancelamento por erro de digitacao no caixa'
}) {
  return `<?xml version="1.0" encoding="UTF-8"?>
<procEventoNFe versao="1.00" xmlns="http://www.portalfiscal.inf.br/nfe">
  <evento versao="1.00">
    <infEvento Id="ID110111${chNFe}01">
      <cOrgao>35</cOrgao>
      <tpAmb>1</tpAmb>
      <CNPJ>12345678000199</CNPJ>
      <chNFe>${chNFe}</chNFe>
      <dhEvento>${dhEvento}</dhEvento>
      <tpEvento>110111</tpEvento>
      <nSeqEvento>1</nSeqEvento>
      <verEvento>1.00</verEvento>
      <detEvento versao="1.00">
        <descEvento>Cancelamento</descEvento>
        <nProt>${nProtOrig}</nProt>
        <xJust>${xJust}</xJust>
      </detEvento>
    </infEvento>
  </evento>
  <retEvento versao="1.00">
    <infEvento>
      <tpAmb>1</tpAmb>
      <verAplic>SP_EVENTOS_PL_100</verAplic>
      <cOrgao>35</cOrgao>
      <cStat>135</cStat>
      <xMotivo>Evento registrado e vinculado a NF-e</xMotivo>
      <chNFe>${chNFe}</chNFe>
      <tpEvento>110111</tpEvento>
      <xEvento>Cancelamento registrado</xEvento>
      <nSeqEvento>1</nSeqEvento>
      <dhRegEvento>${dhEvento}</dhRegEvento>
      <nProt>${nProtCanc}</nProt>
    </infEvento>
  </retEvento>
</procEventoNFe>`;
}

/**
 * Gera a lista completa de arquivos XML fictícios (organizados em subpastas) para o pacote de demonstração/teste.
 */
function buildSampleXmlFileList() {
  const xml1008 = buildSampleNFeXml({
    mod: '65',
    serie: '1',
    nNF: '1008',
    chNFe: '35261012345678000199650010000010081000010083',
    dhEmi: '2026-10-16T16:20:00-03:00',
    dhRecbto: '2026-10-16T16:20:04-03:00',
    nProt: '135260000100108',
    destDoc: '',
    vProd: '64.00',
    vNF: '64.00',
    tPag: '11',
    vPag: '64.00'
  });

  return [
    {
      internalPath: '2026-10/PDV01_NFCe/NFCe_1001_Autorizada.xml',
      content: buildSampleNFeXml({
        mod: '65',
        serie: '1',
        nNF: '1001',
        chNFe: '35261012345678000199650010000010011000010015',
        dhEmi: '2026-10-15T09:15:00-03:00',
        dhRecbto: '2026-10-15T09:15:04-03:00',
        nProt: '135260000100101',
        destDoc: '12345678909',
        vProd: '150.00',
        vNF: '150.00',
        tPag: '17',
        vPag: '150.00'
      })
    },
    {
      internalPath: '2026-10/PDV01_NFCe/NFCe_1002_ComDesconto.xml',
      content: buildSampleNFeXml({
        mod: '65',
        serie: '1',
        nNF: '1002',
        chNFe: '35261012345678000199650010000010021000010020',
        dhEmi: '2026-10-15T10:30:00-03:00',
        dhRecbto: '2026-10-15T10:30:05-03:00',
        nProt: '135260000100102',
        destDoc: '', // Consumidor não identificado (para testar aviso de campo ausente no XML)
        vProd: '300.00',
        vDesc: '20.00',
        vNF: '280.00',
        tPag: '03',
        vPag: '280.00'
      })
    },
    {
      internalPath: '2026-10/Matriz_NFe/NFe_1003_DivergenciaValor.xml',
      content: buildSampleNFeXml({
        mod: '55',
        serie: '1',
        nNF: '1003',
        chNFe: '35261012345678000199550010000010031000010034',
        dhEmi: '2026-10-15T14:20:00-03:00',
        dhRecbto: '2026-10-15T14:20:10-03:00',
        nProt: '135260000100103',
        destDoc: '98765432000188',
        destDocType: 'CNPJ',
        destNome: 'INDUSTRIA BETA COMERCIO S.A.',
        vProd: '1200.00',
        vDesc: '50.00',
        vFrete: '20.00',
        vOutro: '15.00',
        vNF: '1185.00',
        tPag: '15',
        vPag: '1185.00'
      })
    },
    {
      internalPath: '2026-10/PDV01_Noturno/NFCe_1004_ContingenciaMeiaNoite.xml',
      content: buildSampleNFeXml({
        mod: '65',
        serie: '1',
        nNF: '1004',
        chNFe: '35261012345678000199650010000010041000010041',
        dhEmi: '2026-10-15T23:58:10-03:00',
        dhRecbto: '2026-10-16T00:02:15-03:00',
        nProt: '135260000100104',
        destDoc: '',
        vProd: '89.90',
        vNF: '89.90',
        tPag: '04',
        vPag: '89.90'
      })
    },
    {
      internalPath: '2026-10/PDV01_NFCe/NFCe_1005_SomenteNoXml.xml',
      content: buildSampleNFeXml({
        mod: '65',
        serie: '1',
        nNF: '1005',
        chNFe: '35261012345678000199650010000010051000010057',
        dhEmi: '2026-10-16T11:10:00-03:00',
        dhRecbto: '2026-10-16T11:10:03-03:00',
        nProt: '135260000100105',
        destDoc: '45612378900',
        vProd: '450.00',
        vNF: '450.00',
        tPag: '17',
        vPag: '450.00'
      })
    },
    {
      internalPath: '2026-10/PDV02_SerieDivergente/NFCe_1006_Serie1.xml',
      content: buildSampleNFeXml({
        mod: '65',
        serie: '1',
        nNF: '1006',
        chNFe: '35261012345678000199650010000010061000010062',
        dhEmi: '2026-10-16T12:40:00-03:00',
        dhRecbto: '2026-10-16T12:40:04-03:00',
        nProt: '135260000100106',
        destDoc: '',
        vProd: '210.50',
        vNF: '210.50',
        tPag: '01',
        vPag: '210.50'
      })
    },
    {
      internalPath: '2026-10/PDV01_NFCe/NFCe_1007_OriginalAntesCancelamento.xml',
      content: buildSampleNFeXml({
        mod: '65',
        serie: '1',
        nNF: '1007',
        chNFe: '35261012345678000199650010000010071000010078',
        dhEmi: '2026-10-16T15:00:00-03:00',
        dhRecbto: '2026-10-16T15:00:05-03:00',
        nProt: '135260000100107',
        destDoc: '',
        vProd: '95.00',
        vNF: '95.00',
        tPag: '17',
        vPag: '95.00'
      })
    },
    {
      internalPath: '2026-10/Eventos_Cancelamento/procEvento_110111_Canc_1007.xml',
      content: buildCancelEventXml({
        chNFe: '35261012345678000199650010000010071000010078',
        dhEvento: '2026-10-16T15:18:00-03:00',
        nProtOrig: '135260000100107',
        nProtCanc: '135260000999107'
      })
    },
    {
      internalPath: '2026-10/PDV01_NFCe/NFCe_1008_Original.xml',
      content: xml1008
    },
    {
      internalPath: '2026-10/Backup_Duplicados/NFCe_1008_CopiaDuplicada.xml',
      content: xml1008
    },
    {
      internalPath: '2026-10/PDV01_NFCe/NFCe_1010_CandidatoAmbiguo.xml',
      content: buildSampleNFeXml({
        mod: '65',
        serie: '1',
        nNF: '1010',
        chNFe: '35261012345678000199650010000010101000010104',
        dhEmi: '2026-10-16T18:05:00-03:00',
        dhRecbto: '2026-10-16T18:05:06-03:00',
        nProt: '135260000100110',
        destDoc: '',
        vProd: '310.00',
        vNF: '310.00',
        tPag: '17',
        vPag: '310.00'
      })
    },
    {
      internalPath: '2026-10/Matriz_NFe/NFe_2050_UsoDenegado.xml',
      content: buildSampleNFeXml({
        mod: '55',
        serie: '1',
        nNF: '2050',
        chNFe: '35261012345678000199550010000020501000020501',
        dhEmi: '2026-10-16T14:00:00-03:00',
        dhRecbto: '2026-10-16T14:00:08-03:00',
        nProt: '135260000205001',
        cStat: '302',
        xMotivo: 'Uso Denegado: Irregularidade fiscal do destinatario na SEFAZ',
        destDoc: '11222333000144',
        destDocType: 'CNPJ',
        vProd: '890.00',
        vNF: '890.00',
        tPag: '15',
        vPag: '890.00'
      })
    },
    {
      internalPath: '2026-10/Entradas_Devolucoes/NFe_3090_EntradaMercadoria_NaoVenda.xml',
      content: buildSampleNFeXml({
        mod: '55',
        serie: '1',
        nNF: '3090',
        chNFe: '35261012345678000199550010000030901000030908',
        dhEmi: '2026-10-15T08:00:00-03:00',
        dhRecbto: '2026-10-15T08:00:09-03:00',
        nProt: '135260000309001',
        tpNF: '0', // 0 = Entrada (Não é venda!)
        natOp: 'COMPRA PARA COMERCIALIZACAO',
        cfop: '1102',
        destDoc: '12345678000199',
        destDocType: 'CNPJ',
        vProd: '2500.00',
        vNF: '2500.00',
        tPag: '90',
        vPag: '0.00'
      })
    },
    {
      internalPath: '2026-10/Corrompidos/XML_Malformado_Invalido.xml',
      content:
        '<?xml version="1.0" encoding="UTF-8"?><nfeProc><NFe><infNFe Id="NFe123"><ide><nNF>9999</nNF></ide><!-- ARQUIVO TRUNCADO SEM FECHAMENTO'
    }
  ];
}

/**
 * Cria um buffer .ZIP contendo todos os XMLs de exemplo em subpastas.
 */
function buildSampleZipBuffer() {
  const zip = new AdmZip();
  const list = buildSampleXmlFileList();
  for (const item of list) {
    zip.addFile(item.internalPath, Buffer.from(item.content, 'utf8'));
  }
  // Inclui também um arquivo .txt de leia-me dentro do ZIP para testar filtro de não-XML
  zip.addFile(
    '2026-10/LEIAME_EXPORTACAO.txt',
    Buffer.from('Arquivo auxiliar de log do PDV - deve ser ignorado pelo extrator de XML.', 'utf8')
  );
  return zip.toBuffer();
}

/**
 * Linhas do relatório de vendas em PDF para o cenário de exemplo/demonstração.
 */
function getSamplePdfRowsText() {
  return [
    'RELATORIO GERENCIAL DE VENDAS FISCAIS - OUTUBRO/2026',
    'EMPRESA: COMERCIAL ALFA DISTRIBUIDORA LTDA - CNPJ: 12.345.678/0001-99',
    'Data Emissão | Número | Série | Modelo | Valor Total | Forma Pagamento | Status | Chave de Acesso',
    '15/10/2026 | 1001 | 1 | 65 | R$ 150,00 | PIX | AUTORIZADO | 35261012345678000199650010000010011000010015',
    '15/10/2026 | 1002 | 1 | 65 | R$ 280,00 | Cartão de Crédito | AUTORIZADO | 35261012345678000199650010000010021000010020',
    '15/10/2026 | 1003 | 1 | 55 | R$ 1.200,00 | Boleto Bancário | AUTORIZADO | 35261012345678000199550010000010031000010034',
    '16/10/2026 | 1004 | 1 | 65 | R$ 89,90 | Cartão de Débito | AUTORIZADO | 35261012345678000199650010000010041000010041',
    '16/10/2026 | 1006 | 2 | 65 | R$ 210,50 | Dinheiro | AUTORIZADO | 35261012345678000199650020000010061000010062',
    '16/10/2026 | 1007 | 1 | 65 | R$ 95,00 | PIX | AUTORIZADO | 35261012345678000199650010000010071000010078',
    '16/10/2026 | 1008 | 1 | 65 | R$ 64,00 | Vale Refeição | AUTORIZADO | 35261012345678000199650010000010081000010083',
    '16/10/2026 | 1009 | 1 | 65 | R$ 120,00 | Dinheiro | AUTORIZADO | ',
    '16/10/2026 | 1011 | 1 | 65 | R$ 310,00 | Cartão de Crédito | AUTORIZADO | 35261012345678000199650010000010111000010110'
  ];
}

/**
 * Gera um arquivo PDF real (com texto selecionável) contendo o relatório de vendas de exemplo.
 */
function buildSampleSalesPdfBuffer() {
  return new Promise((resolve, reject) => {
    const doc = new PDFDocument({ size: 'A4', layout: 'landscape', margin: 36 });
    const chunks = [];
    doc.on('data', (c) => chunks.push(c));
    doc.on('end', () => resolve(Buffer.concat(chunks)));
    doc.on('error', reject);

    const lines = getSamplePdfRowsText();
    doc.font('Helvetica-Bold').fontSize(12).text(lines[0]);
    doc.font('Helvetica').fontSize(9).text(lines[1]);
    doc.moveDown(0.8);

    doc.font('Courier-Bold').fontSize(8.5).text(lines[2]);
    doc.moveDown(0.3);
    doc.font('Courier').fontSize(8.5);
    for (let i = 3; i < lines.length; i++) {
      doc.text(lines[i]);
    }

    doc.end();
  });
}

/**
 * Gera um arquivo PDF simulando um documento digitalizado como imagem (sem camada de texto selecionável direta),
 * contendo os metadados de rasterização para que o fluxo de detecção de PDF digitalizado + OCR local possa ser testado.
 */
function buildSampleScannedPdfBuffer() {
  return new Promise((resolve, reject) => {
    const doc = new PDFDocument({ size: 'A4', margin: 36 });
    const chunks = [];
    const ocrLines = getSamplePdfRowsText().join('\n');
    const b64Payload = Buffer.from(ocrLines, 'utf8').toString('base64');

    doc.on('data', (c) => chunks.push(c));
    doc.on('end', () => {
      const pdfBuf = Buffer.concat(chunks);
      const trailerComment = Buffer.from(`\n% %%OCR_IMAGE_PAYLOAD_B64:${b64Payload}%%\n`, 'latin1');
      resolve(Buffer.concat([pdfBuf, trailerComment]));
    });
    doc.on('error', reject);

    // Desenha apenas elementos vetoriais (retângulos simulando uma página escaneada sem texto selecionável)
    doc.rect(40, 40, 515, 700).stroke('#94A3B8');
    doc.rect(60, 70, 475, 35).fill('#E2E8F0');
    for (let y = 130; y < 420; y += 28) {
      doc.rect(60, y, 475, 16).fill('#F1F5F9');
    }

    doc.end();
  });
}

/**
 * Cria um cabeçalho binário ODS autêntico do Firebird 2.5 (ODS 11.2) ou Firebird 5.0 (ODS 13.1)
 * para testes locais de detecção de versão de arquivo .fdb.
 */
function buildSampleFirebirdHeaderBuffer(version = '2.5') {
  const buf = Buffer.alloc(4096, 0);
  // pag_type = 0x01 (Database header page)
  buf.writeUInt8(0x01, 0);
  // hdr_page_size = 4096 (offset 16)
  buf.writeUInt16LE(4096, 16);

  if (version === '5.0') {
    // ODS 13.1 com flag 0x8000 (0x8000 | 13 = 0x800D)
    buf.writeUInt16LE(0x8000 | 13, 18);
    buf.writeUInt16LE(1, 20);
  } else {
    // ODS 11.2 com flag 0x8000 (0x8000 | 11 = 0x800B)
    buf.writeUInt16LE(0x8000 | 11, 18);
    buf.writeUInt16LE(2, 20);
  }

  buf.write('CONFEREFISCAL_DEMO_FDB', 24, 'utf8');
  return buf;
}

module.exports = {
  buildSampleNFeXml,
  buildCancelEventXml,
  buildSampleXmlFileList,
  buildSampleZipBuffer,
  getSamplePdfRowsText,
  buildSampleSalesPdfBuffer,
  buildSampleScannedPdfBuffer,
  buildSampleFirebirdHeaderBuffer
};
