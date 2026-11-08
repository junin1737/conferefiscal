const path = require('path');
const crypto = require('crypto');
const { AdmZip } = require('../lib/zipCodec');
const { extractRarEntries } = require('../lib/rarCodec');
const {
  DIRS,
  SECURITY_LIMITS,
  validateUploadedBuffer,
  logTechnicalError,
  logTechnicalEvent
} = require('./storageService');

/**
 * Normaliza e valida um caminho interno de arquivo compactado contra Path Traversal (ZipSlip).
 */
function sanitizeInternalPath(entryName) {
  const normalized = String(entryName || '')
    .replace(/\\/g, '/')
    .replace(/^\/+/, '');
  const parts = normalized.split('/');
  if (parts.some((p) => p === '..' || p.includes('\0'))) {
    throw new Error(`Caminho interno inseguro bloqueado (Path Traversal): "${entryName}"`);
  }
  return normalized;
}

/**
 * Extrai todos os arquivos XML de um buffer .zip (incluindo subpastas).
 */
function extractFromZipBuffer(buffer, archiveName) {
  const extractedFiles = [];
  const fileErrors = [];
  let totalUncompressedBytes = 0;

  let zip;
  try {
    zip = new AdmZip(buffer);
  } catch (err) {
    logTechnicalError('archiveService.extractFromZipBuffer', err);
    return {
      extractedFiles: [],
      fileErrors: [
        {
          fileName: archiveName,
          internalPath: archiveName,
          stage: 'descompactacao_zip',
          error: `Arquivo ZIP corrompido ou ilegível: ${err.message}`
        }
      ],
      ignoredNonXmlCount: 0
    };
  }

  let entries = [];
  try {
    entries = zip.getEntries();
  } catch (err) {
    return {
      extractedFiles: [],
      fileErrors: [
        {
          fileName: archiveName,
          internalPath: archiveName,
          stage: 'descompactacao_zip',
          error: `Não foi possível ler o índice do arquivo ZIP: ${err.message}`
        }
      ],
      ignoredNonXmlCount: 0
    };
  }

  let ignoredNonXmlCount = 0;

  for (const entry of entries) {
    if (entry.isDirectory) continue;

    let internalPath;
    try {
      internalPath = sanitizeInternalPath(entry.entryName);
    } catch (secErr) {
      fileErrors.push({
        fileName: entry.entryName,
        internalPath: entry.entryName,
        stage: 'seguranca_zip',
        error: secErr.message
      });
      continue;
    }

    const ext = path.extname(internalPath).toLowerCase();
    if (ext !== '.xml') {
      ignoredNonXmlCount += 1;
      continue;
    }

    if (extractedFiles.length >= SECURITY_LIMITS.MAX_EXTRACTED_FILES) {
      fileErrors.push({
        fileName: internalPath,
        internalPath,
        stage: 'limite_seguranca',
        error: `Limite máximo de ${SECURITY_LIMITS.MAX_EXTRACTED_FILES} arquivos por pacote atingido.`
      });
      break;
    }

    try {
      const uncompressedSize = entry.header ? entry.header.size : 0;
      const compressedSize = entry.header ? entry.header.compressedSize : 1;

      if (
        compressedSize > 0 &&
        uncompressedSize > 10 * 1024 * 1024 &&
        uncompressedSize / compressedSize > SECURITY_LIMITS.MAX_COMPRESSION_RATIO
      ) {
        fileErrors.push({
          fileName: path.basename(internalPath),
          internalPath: `${archiveName}::${internalPath}`,
          stage: 'seguranca_zipbomb',
          error: 'Arquivo interno bloqueado por taxa de compressão suspeita (proteção anti-ZipBomb).'
        });
        continue;
      }

      const data = entry.getData();
      totalUncompressedBytes += data.length;

      if (totalUncompressedBytes > SECURITY_LIMITS.MAX_EXTRACTED_TOTAL_BYTES) {
        fileErrors.push({
          fileName: path.basename(internalPath),
          internalPath: `${archiveName}::${internalPath}`,
          stage: 'limite_memoria',
          error: 'Volume total descomprimido excedeu o limite de segurança de 300 MB.'
        });
        break;
      }

      extractedFiles.push({
        archiveName,
        internalPath,
        displayPath: `${archiveName} / ${internalPath}`,
        fileName: path.basename(internalPath),
        subfolder: path.dirname(internalPath) === '.' ? '' : path.dirname(internalPath),
        sizeBytes: data.length,
        sha256: crypto.createHash('sha256').update(data).digest('hex'),
        buffer: data
      });
    } catch (entryErr) {
      fileErrors.push({
        fileName: path.basename(internalPath),
        internalPath: `${archiveName}::${internalPath}`,
        stage: 'extracao_item_zip',
        error: `Falha ao extrair "${internalPath}" do ZIP: ${entryErr.message}`
      });
    }
  }

  return { extractedFiles, fileErrors, ignoredNonXmlCount };
}

/**
 * Extrai todos os arquivos XML de um buffer .rar (RAR v4 e RAR v5, incluindo subpastas).
 */
async function extractFromRarBuffer(buffer, archiveName) {
  const extractedFiles = [];
  const fileErrors = [];
  let ignoredNonXmlCount = 0;
  let totalUncompressedBytes = 0;

  try {
    const rawEntries = await extractRarEntries(buffer, DIRS.extracted);

    for (const file of rawEntries) {
      if (file.isDirectory) continue;

      let internalPath;
      try {
        internalPath = sanitizeInternalPath(file.name);
      } catch (secErr) {
        fileErrors.push({
          fileName: file.name,
          internalPath: `${archiveName}::${file.name}`,
          stage: 'seguranca_rar',
          error: secErr.message
        });
        continue;
      }

      const ext = path.extname(internalPath).toLowerCase();
      if (ext !== '.xml') {
        ignoredNonXmlCount += 1;
        continue;
      }

      const data = Buffer.from(file.data);
      totalUncompressedBytes += data.length;
      if (totalUncompressedBytes > SECURITY_LIMITS.MAX_EXTRACTED_TOTAL_BYTES) {
        fileErrors.push({
          fileName: path.basename(internalPath),
          internalPath: `${archiveName}::${internalPath}`,
          stage: 'limite_memoria',
          error: 'Volume total descomprimido do RAR excedeu o limite de segurança de 300 MB.'
        });
        break;
      }

      extractedFiles.push({
        archiveName,
        internalPath,
        displayPath: `${archiveName} / ${internalPath}`,
        fileName: path.basename(internalPath),
        subfolder: path.dirname(internalPath) === '.' ? '' : path.dirname(internalPath),
        sizeBytes: data.length,
        sha256: crypto.createHash('sha256').update(data).digest('hex'),
        buffer: data
      });
    }
  } catch (err) {
    logTechnicalError('archiveService.extractFromRarBuffer', err);
    fileErrors.push({
      fileName: archiveName,
      internalPath: archiveName,
      stage: 'descompactacao_rar',
      error: `Arquivo RAR corrompido, protegido por senha ou formato inválido: ${err.message}`
    });
  }

  return { extractedFiles, fileErrors, ignoredNonXmlCount };
}

/**
 * Processa um conjunto de arquivos enviados (.zip, .rar, .xml) sem alterar os originais.
 */
async function processUploadedXmlContainers(files = []) {
  const allXmlEntries = [];
  const allErrors = [];
  const containerSummaries = [];

  for (const file of files) {
    const originalName = file.originalname || file.name || 'arquivo_desconhecido';
    const buffer = file.buffer;

    const validation = validateUploadedBuffer(buffer, originalName);
    if (!validation.valid) {
      allErrors.push({
        fileName: originalName,
        internalPath: originalName,
        stage: 'validacao_arquivo',
        error: validation.reason
      });
      containerSummaries.push({
        name: originalName,
        sizeBytes: buffer ? buffer.length : 0,
        type: path.extname(originalName).toLowerCase(),
        status: 'ERRO',
        extractedCount: 0,
        errorCount: 1,
        message: validation.reason
      });
      continue;
    }

    if (validation.detectedType === 'zip') {
      const res = extractFromZipBuffer(buffer, originalName);
      allXmlEntries.push(...res.extractedFiles);
      allErrors.push(...res.fileErrors);
      containerSummaries.push({
        name: originalName,
        sizeBytes: buffer.length,
        sha256: crypto.createHash('sha256').update(buffer).digest('hex'),
        type: 'ZIP',
        status: res.fileErrors.length > 0 && res.extractedFiles.length === 0 ? 'ERRO' : 'PROCESSADO',
        extractedCount: res.extractedFiles.length,
        ignoredNonXmlCount: res.ignoredNonXmlCount,
        errorCount: res.fileErrors.length
      });
    } else if (validation.detectedType === 'rar') {
      const res = await extractFromRarBuffer(buffer, originalName);
      allXmlEntries.push(...res.extractedFiles);
      allErrors.push(...res.fileErrors);
      containerSummaries.push({
        name: originalName,
        sizeBytes: buffer.length,
        sha256: crypto.createHash('sha256').update(buffer).digest('hex'),
        type: 'RAR',
        status: res.fileErrors.length > 0 && res.extractedFiles.length === 0 ? 'ERRO' : 'PROCESSADO',
        extractedCount: res.extractedFiles.length,
        ignoredNonXmlCount: res.ignoredNonXmlCount,
        errorCount: res.fileErrors.length
      });
    } else if (validation.detectedType === 'xml') {
      allXmlEntries.push({
        archiveName: '(Arquivo XML avulso)',
        internalPath: originalName,
        displayPath: originalName,
        fileName: path.basename(originalName),
        subfolder: '',
        sizeBytes: buffer.length,
        sha256: crypto.createHash('sha256').update(buffer).digest('hex'),
        buffer
      });
      containerSummaries.push({
        name: originalName,
        sizeBytes: buffer.length,
        sha256: crypto.createHash('sha256').update(buffer).digest('hex'),
        type: 'XML',
        status: 'PROCESSADO',
        extractedCount: 1,
        ignoredNonXmlCount: 0,
        errorCount: 0
      });
    } else {
      allErrors.push({
        fileName: originalName,
        internalPath: originalName,
        stage: 'tipo_incompativel',
        error: `O arquivo "${originalName}" não é um pacote .zip/.rar nem um documento .xml.`
      });
    }
  }

  logTechnicalEvent(
    'info',
    'ArchiveProcessor',
    `Extração concluída: ${allXmlEntries.length} XML(s) extraído(s), ${allErrors.length} erro(s) registrado(s).`
  );

  return {
    xmlEntries: allXmlEntries,
    extractionErrors: allErrors,
    containerSummaries
  };
}

module.exports = {
  extractFromZipBuffer,
  extractFromRarBuffer,
  processUploadedXmlContainers
};
