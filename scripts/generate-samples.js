const fs = require('fs');
const path = require('path');
const {
  buildSampleXmlFileList,
  buildSampleZipBuffer,
  buildSampleSalesPdfBuffer,
  buildSampleScannedPdfBuffer,
  buildSampleFirebirdHeaderBuffer
} = require('../src/services/demoService');
const { buildSampleRarBuffer } = require('../src/lib/rarCodec');

const EXAMPLES_DIR = path.resolve(__dirname, '..', 'exemplos');
const AVULSOS_DIR = path.join(EXAMPLES_DIR, 'xmls_avulsos');

async function generateAllSamples() {
  if (!fs.existsSync(EXAMPLES_DIR)) {
    fs.mkdirSync(EXAMPLES_DIR, { recursive: true });
  }
  if (!fs.existsSync(AVULSOS_DIR)) {
    fs.mkdirSync(AVULSOS_DIR, { recursive: true });
  }

  // 1. Pacote .ZIP com subpastas
  const zipBuf = buildSampleZipBuffer();
  const zipPath = path.join(EXAMPLES_DIR, 'pacote_vendas_exemplo.zip');
  fs.writeFileSync(zipPath, zipBuf);

  // 2. Pacote .RAR com subpastas
  const xmlList = buildSampleXmlFileList();
  const rarBuf = buildSampleRarBuffer(xmlList);
  const rarPath = path.join(EXAMPLES_DIR, 'pacote_vendas_exemplo.rar');
  fs.writeFileSync(rarPath, rarBuf);

  // 3. Alguns XMLs avulsos
  for (const item of xmlList.slice(0, 3)) {
    const fileName = path.basename(item.internalPath);
    fs.writeFileSync(path.join(AVULSOS_DIR, fileName), item.content, 'utf8');
  }

  // 4. Relatório de vendas em PDF (texto selecionável)
  const pdfBuf = await buildSampleSalesPdfBuffer();
  const pdfPath = path.join(EXAMPLES_DIR, 'relatorio_vendas_exemplo.pdf');
  fs.writeFileSync(pdfPath, pdfBuf);

  // 5. Relatório em PDF digitalizado como imagem (para teste de detecção e OCR local)
  const scannedPdfBuf = await buildSampleScannedPdfBuffer();
  const scannedPdfPath = path.join(EXAMPLES_DIR, 'relatorio_digitalizado_ocr.pdf');
  fs.writeFileSync(scannedPdfPath, scannedPdfBuf);

  // 6. Arquivos de cabeçalho de banco Firebird 2.5 (ODS 11.2) e Firebird 5.0 (ODS 13.1)
  const fb25Path = path.join(EXAMPLES_DIR, 'demo_fb25.fdb');
  fs.writeFileSync(fb25Path, buildSampleFirebirdHeaderBuffer('2.5'));

  const fb50Path = path.join(EXAMPLES_DIR, 'demo_fb50.fdb');
  fs.writeFileSync(fb50Path, buildSampleFirebirdHeaderBuffer('5.0'));

  return {
    zipPath,
    rarPath,
    pdfPath,
    scannedPdfPath,
    fb25Path,
    fb50Path
  };
}

if (require.main === module) {
  generateAllSamples()
    .then((paths) => {
      console.log('Arquivos fictícios de exemplo gerados com sucesso em ./exemplos:');
      Object.entries(paths).forEach(([k, v]) => console.log(` - ${k}: ${v}`));
    })
    .catch((err) => {
      console.error('Erro ao gerar arquivos de exemplo:', err);
      process.exit(1);
    });
}

module.exports = { generateAllSamples };
