const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const ROOT_DIR = path.resolve(__dirname, '..', '..');
const LOCAL_DATA_DIR = path.join(ROOT_DIR, '.local_data');
const DIRS = {
  root: LOCAL_DATA_DIR,
  uploads: path.join(LOCAL_DATA_DIR, 'uploads'),
  extracted: path.join(LOCAL_DATA_DIR, 'extracted'),
  config: path.join(LOCAL_DATA_DIR, 'config'),
  logs: path.join(LOCAL_DATA_DIR, 'logs'),
  exports: path.join(LOCAL_DATA_DIR, 'exports')
};

const KEY_FILE = path.join(DIRS.config, '.secret.key');
const FB_CONFIG_FILE = path.join(DIRS.config, 'firebird.enc.json');
const TECH_LOG_FILE = path.join(DIRS.logs, 'technical.log');

// Limites de segurança para arquivos importados
const SECURITY_LIMITS = {
  MAX_SINGLE_FILE_BYTES: 150 * 1024 * 1024, // 150 MB
  MAX_EXTRACTED_TOTAL_BYTES: 300 * 1024 * 1024, // 300 MB (proteção anti-ZipBomb)
  MAX_EXTRACTED_FILES: 10000, // Máximo de arquivos num pacote compactado
  MAX_COMPRESSION_RATIO: 120 // Proteção contra bombas de descompressão
};

const recentLogs = [];

function ensureDirectories() {
  Object.values(DIRS).forEach((dir) => {
    if (!fs.existsSync(dir)) {
      fs.mkdirSync(dir, { recursive: true });
    }
  });
}

ensureDirectories();

/**
 * Obtém ou gera uma chave simétrica local AES-256 para proteger credenciais em repouso.
 */
function getOrCreateLocalKey() {
  ensureDirectories();
  if (fs.existsSync(KEY_FILE)) {
    const hex = fs.readFileSync(KEY_FILE, 'utf8').trim();
    if (hex.length === 64) {
      return Buffer.from(hex, 'hex');
    }
  }
  const newKey = crypto.randomBytes(32);
  fs.writeFileSync(KEY_FILE, newKey.toString('hex'), { mode: 0o600 });
  return newKey;
}

/**
 * Criptografa um texto sensível (ex: senha do banco) usando AES-256-GCM.
 */
function encryptSecret(plainText) {
  if (!plainText) return null;
  const key = getOrCreateLocalKey();
  const iv = crypto.randomBytes(12);
  const cipher = crypto.createCipheriv('aes-256-gcm', key, iv);
  const encrypted = Buffer.concat([cipher.update(String(plainText), 'utf8'), cipher.final()]);
  const authTag = cipher.getAuthTag();
  return {
    alg: 'aes-256-gcm',
    iv: iv.toString('hex'),
    tag: authTag.toString('hex'),
    data: encrypted.toString('hex')
  };
}

/**
 * Descriptografa um segredo protegido com AES-256-GCM.
 */
function decryptSecret(payload) {
  if (!payload || !payload.iv || !payload.tag || !payload.data) return '';
  try {
    const key = getOrCreateLocalKey();
    const decipher = crypto.createDecipheriv(
      'aes-256-gcm',
      key,
      Buffer.from(payload.iv, 'hex')
    );
    decipher.setAuthTag(Buffer.from(payload.tag, 'hex'));
    const decrypted = Buffer.concat([
      decipher.update(Buffer.from(payload.data, 'hex')),
      decipher.final()
    ]);
    return decrypted.toString('utf8');
  } catch (err) {
    logTechnicalError('storageService.decryptSecret', err);
    return '';
  }
}

/**
 * Remove qualquer menção a senhas ou tokens antes de gravar logs.
 */
function sanitizeLogMessage(input, extraSecrets = []) {
  if (!input) return '';
  let text = typeof input === 'string' ? input : JSON.stringify(input);
  // Mascara padrões de senha em JSON, query string ou texto
  text = text.replace(
    /("?(?:password|senha|secret|passwd|pwd|isc_dpb_password)"?\s*[:=]\s*["']?)([^"',;\s}]+)(["']?)/gi,
    '$1[PROTEGIDO]$3'
  );
  for (const secret of extraSecrets) {
    if (secret && typeof secret === 'string' && secret.trim().length >= 2) {
      const escaped = secret.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      text = text.replace(new RegExp(escaped, 'g'), '[PROTEGIDO]');
    }
  }
  return text;
}

/**
 * Registra erro ou evento técnico sem expor credenciais.
 */
function logTechnicalEvent(level, context, message, details = null, secretsToScrub = []) {
  ensureDirectories();
  const timestamp = new Date().toISOString();
  const safeMsg = sanitizeLogMessage(message, secretsToScrub);
  const safeDetails = details ? sanitizeLogMessage(details, secretsToScrub) : undefined;

  const entry = {
    timestamp,
    level,
    context,
    message: safeMsg,
    details: safeDetails
  };

  recentLogs.unshift(entry);
  if (recentLogs.length > 200) recentLogs.pop();

  const line = `[${timestamp}] [${level.toUpperCase()}] [${context}] ${safeMsg}${
    safeDetails ? ' | ' + safeDetails : ''
  }\n`;

  try {
    fs.appendFileSync(TECH_LOG_FILE, line, 'utf8');
  } catch (_) {
    // Ignora falha de escrita de log secundário
  }
  return entry;
}

function logTechnicalError(context, err, secretsToScrub = []) {
  const msg = err && err.message ? err.message : String(err);
  const code = err && err.code ? `code=${err.code}` : '';
  return logTechnicalEvent('error', context, msg, code, secretsToScrub);
}

/**
 * Salva configuração de conexão do Firebird com a senha criptografada (AES-256-GCM).
 */
function saveFirebirdConfig(config) {
  ensureDirectories();
  const safeConfig = {
    host: config.host || '127.0.0.1',
    port: Number(config.port) || 3050,
    database: config.database || '',
    user: config.user || 'SYSDBA',
    charset: config.charset || 'UTF8',
    role: config.role || '',
    versionTarget: config.versionTarget || 'auto',
    clientLibPath: config.clientLibPath || '',
    tableMapping: config.tableMapping || null,
    updatedAt: new Date().toISOString(),
    encryptedPassword: config.password ? encryptSecret(config.password) : null
  };
  fs.writeFileSync(FB_CONFIG_FILE, JSON.stringify(safeConfig, null, 2), { mode: 0o600 });
  logTechnicalEvent('info', 'FirebirdConfig', 'Configuração de conexão Firebird salva com criptografia AES-256-GCM.');
  return getFirebirdConfigPublic();
}

/**
 * Carrega configuração do Firebird para uso interno (com senha descriptografada apenas em memória).
 */
function loadFirebirdConfigInternal() {
  if (!fs.existsSync(FB_CONFIG_FILE)) return null;
  try {
    const raw = JSON.parse(fs.readFileSync(FB_CONFIG_FILE, 'utf8'));
    const password = raw.encryptedPassword ? decryptSecret(raw.encryptedPassword) : '';
    return {
      ...raw,
      password,
      hasSavedPassword: Boolean(password)
    };
  } catch (err) {
    logTechnicalError('loadFirebirdConfigInternal', err);
    return null;
  }
}

/**
 * Retorna os dados públicos da configuração do Firebird (sem a senha!).
 */
function getFirebirdConfigPublic() {
  const internal = loadFirebirdConfigInternal();
  if (!internal) return null;
  const { password, encryptedPassword, ...publicData } = internal;
  return {
    ...publicData,
    hasSavedPassword: Boolean(password)
  };
}

/**
 * Valida magic bytes, tamanho e segurança de um arquivo enviado.
 */
function validateUploadedBuffer(buffer, originalName) {
  if (!Buffer.isBuffer(buffer)) {
    return { valid: false, reason: 'Conteúdo do arquivo inválido ou ausente.' };
  }
  if (buffer.length === 0) {
    return { valid: false, reason: `O arquivo "${originalName}" está vazio (0 bytes).` };
  }
  if (buffer.length > SECURITY_LIMITS.MAX_SINGLE_FILE_BYTES) {
    return {
      valid: false,
      reason: `O arquivo "${originalName}" excede o limite de segurança de ${Math.round(
        SECURITY_LIMITS.MAX_SINGLE_FILE_BYTES / (1024 * 1024)
      )} MB.`
    };
  }

  const ext = path.extname(originalName || '').toLowerCase();

  if (ext === '.zip') {
    // Magic bytes PK\x03\x04 ou PK\x05\x06 (zip vazio) ou PK\x07\x08
    const isPk =
      buffer.length >= 4 &&
      buffer[0] === 0x50 &&
      buffer[1] === 0x4b &&
      (buffer[2] === 0x03 || buffer[2] === 0x05 || buffer[2] === 0x07);
    if (!isPk) {
      return {
        valid: false,
        reason: `O arquivo "${originalName}" possui extensão .zip, mas sua assinatura binária não corresponde a um arquivo ZIP válido.`
      };
    }
    return { valid: true, detectedType: 'zip' };
  }

  if (ext === '.rar') {
    // RAR v4: 52 61 72 21 1A 07 00 | RAR v5: 52 61 72 21 1A 07 01 00
    const isRar =
      buffer.length >= 7 &&
      buffer[0] === 0x52 &&
      buffer[1] === 0x61 &&
      buffer[2] === 0x72 &&
      buffer[3] === 0x21 &&
      buffer[4] === 0x1a &&
      buffer[5] === 0x07;
    if (!isRar) {
      return {
        valid: false,
        reason: `O arquivo "${originalName}" possui extensão .rar, mas sua assinatura binária ("Rar!") não foi encontrada.`
      };
    }
    return { valid: true, detectedType: 'rar' };
  }

  if (ext === '.pdf') {
    // %PDF- pode aparecer nos primeiros 1024 bytes segundo a especificação PDF
    const head = buffer.subarray(0, Math.min(buffer.length, 1024)).toString('latin1');
    if (!head.includes('%PDF-')) {
      return {
        valid: false,
        reason: `O arquivo "${originalName}" possui extensão .pdf, mas não contém o cabeçalho "%PDF-" válido.`
      };
    }
    return { valid: true, detectedType: 'pdf' };
  }

  if (ext === '.xml') {
    const sample = buffer.subarray(0, Math.min(buffer.length, 4096)).toString('utf8');
    // Proteção contra XXE (XML External Entity)
    if (/<!ENTITY\s+/i.test(sample) || /<!DOCTYPE[^>]+SYSTEM/i.test(sample)) {
      return {
        valid: false,
        reason: `O arquivo XML "${originalName}" foi bloqueado por conter declarações de entidades externas (proteção anti-XXE).`
      };
    }
    if (!sample.includes('<')) {
      return {
        valid: false,
        reason: `O arquivo "${originalName}" não parece conter marcação XML válida.`
      };
    }
    return { valid: true, detectedType: 'xml' };
  }

  return {
    valid: false,
    reason: `Formato de arquivo não permitido ("${ext || 'sem extensão'}"). São aceitos apenas arquivos .zip, .rar, .xml e .pdf.`
  };
}

/**
 * Calcula estatísticas de uso dos diretórios temporários locais.
 */
function getDirectoryStats(dirPath) {
  let fileCount = 0;
  let totalBytes = 0;
  if (!fs.existsSync(dirPath)) return { fileCount: 0, totalBytes: 0 };

  const entries = fs.readdirSync(dirPath, { withFileTypes: true });
  for (const entry of entries) {
    const full = path.join(dirPath, entry.name);
    if (entry.isDirectory()) {
      const sub = getDirectoryStats(full);
      fileCount += sub.fileCount;
      totalBytes += sub.totalBytes;
    } else if (entry.isFile()) {
      fileCount += 1;
      try {
        totalBytes += fs.statSync(full).size;
      } catch (_) {}
    }
  }
  return { fileCount, totalBytes };
}

function getStorageStatus() {
  ensureDirectories();
  const uploadsStats = getDirectoryStats(DIRS.uploads);
  const extractedStats = getDirectoryStats(DIRS.extracted);
  const configStats = getDirectoryStats(DIRS.config);
  const logsStats = getDirectoryStats(DIRS.logs);
  const exportsStats = getDirectoryStats(DIRS.exports);

  const totalBytes =
    uploadsStats.totalBytes +
    extractedStats.totalBytes +
    configStats.totalBytes +
    logsStats.totalBytes +
    exportsStats.totalBytes;

  const totalFiles =
    uploadsStats.fileCount +
    extractedStats.fileCount +
    configStats.fileCount +
    logsStats.fileCount +
    exportsStats.fileCount;

  return {
    baseDirectory: LOCAL_DATA_DIR,
    directories: {
      uploads: { path: DIRS.uploads, ...uploadsStats, description: 'Cópias temporárias de arquivos enviados para processamento' },
      extracted: { path: DIRS.extracted, ...extractedStats, description: 'XMLs extraídos temporariamente de pacotes .zip e .rar' },
      config: { path: DIRS.config, ...configStats, description: 'Configurações locais criptografadas (AES-256-GCM)' },
      logs: { path: DIRS.logs, ...logsStats, description: 'Registros técnicos locais com sanitização de credenciais' },
      exports: { path: DIRS.exports, ...exportsStats, description: 'Cache temporário de relatórios gerados (CSV, XLSX, PDF)' }
    },
    totalFiles,
    totalBytes,
    totalFormatted: `${(totalBytes / 1024).toFixed(1)} KB`,
    recentLogs: recentLogs.slice(0, 50)
  };
}

/**
 * Remove todos os dados temporários, extrações, logs e configurações locais.
 */
function clearAllLocalData(options = { keepConfig: false }) {
  const targets = [DIRS.uploads, DIRS.extracted, DIRS.exports, DIRS.logs];
  if (!options.keepConfig) {
    targets.push(DIRS.config);
  }

  for (const dir of targets) {
    if (fs.existsSync(dir)) {
      fs.rmSync(dir, { recursive: true, force: true });
    }
  }
  recentLogs.length = 0;
  ensureDirectories();
  logTechnicalEvent('info', 'StorageCleanup', 'Dados temporários locais removidos pelo usuário.');
  return getStorageStatus();
}

module.exports = {
  DIRS,
  SECURITY_LIMITS,
  ensureDirectories,
  encryptSecret,
  decryptSecret,
  sanitizeLogMessage,
  logTechnicalEvent,
  logTechnicalError,
  saveFirebirdConfig,
  loadFirebirdConfigInternal,
  getFirebirdConfigPublic,
  validateUploadedBuffer,
  getStorageStatus,
  clearAllLocalData
};
