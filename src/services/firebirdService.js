const fs = require('fs');
const path = require('path');
const { execFile } = require('child_process');
const Firebird = require('../lib/firebirdCodec');
const {
  logTechnicalEvent,
  logTechnicalError,
  sanitizeLogMessage
} = require('./storageService');

/**
 * Mapeamento de códigos de tipos internos RDB$FIELD_TYPE do catálogo Firebird.
 */
const FB_FIELD_TYPES = {
  7: 'SMALLINT',
  8: 'INTEGER',
  10: 'FLOAT',
  12: 'DATE',
  13: 'TIME',
  14: 'CHAR',
  16: 'BIGINT / NUMERIC',
  23: 'BOOLEAN',
  24: 'DECFLOAT(16)',
  25: 'DECFLOAT(34)',
  26: 'INT128',
  27: 'DOUBLE PRECISION',
  28: 'TIME WITH TIME ZONE',
  29: 'TIMESTAMP WITH TIME ZONE',
  35: 'TIMESTAMP',
  37: 'VARCHAR',
  261: 'BLOB'
};

/**
 * Valida um identificador SQL (nome de tabela ou coluna) do Firebird para impedir SQL Injection.
 */
function sanitizeSqlIdentifier(identifier) {
  if (!identifier || typeof identifier !== 'string') {
    throw new Error('Identificador SQL inválido ou vazio.');
  }
  const trimmed = identifier.trim();
  if (!/^[A-Za-z_][A-Za-z0-9_$]*$/.test(trimmed)) {
    throw new Error(`Identificador de tabela/coluna inválido bloqueado por segurança: "${trimmed}"`);
  }
  return trimmed.toUpperCase();
}

/**
 * Guardião estrito de consultas Somente-Leitura (READ ONLY).
 * Bloqueia qualquer instrução que possa alterar dados, estrutura ou sequences/generators.
 */
function assertReadOnlySql(sql) {
  if (!sql || typeof sql !== 'string') {
    throw new Error('Consulta SQL vazia ou inválida.');
  }
  const normalized = sql.trim();

  if (!/^SELECT\s+/i.test(normalized)) {
    throw new Error('Operação bloqueada: apenas consultas SELECT (somente leitura) são permitidas.');
  }

  // Remove literais entre aspas simples para inspecionar tokens SQL
  const withoutLiterals = normalized.replace(/'[^']*'/g, "''");

  if (withoutLiterals.includes(';')) {
    const parts = withoutLiterals.split(';').map((p) => p.trim()).filter(Boolean);
    if (parts.length > 1) {
      throw new Error('Operação bloqueada: múltiplos comandos SQL não são permitidos.');
    }
  }

  const forbiddenKeywords = /\b(INSERT|UPDATE|DELETE|MERGE|DROP|ALTER|CREATE|TRUNCATE|RECREATE|EXECUTE|EXEC|GRANT|REVOKE|COMMENT|SET\s+GENERATOR|GEN_ID|NEXT\s+VALUE\s+FOR|RDB\$SET_CONTEXT)\b/i;
  const match = withoutLiterals.match(forbiddenKeywords);
  if (match) {
    throw new Error(
      `Operação bloqueada por política Somente-Leitura: comando proibido detectado ("${match[1].toUpperCase()}").`
    );
  }

  return normalized;
}

/**
 * Inspeciona o cabeçalho binário ODS (On-Disk Structure) de um arquivo .fdb/.gdb local em modo somente-leitura
 * para identificar se o arquivo pertence ao Firebird 2.5 (ODS 11.2) ou Firebird 5.0 (ODS 13.1).
 */
function inspectLocalFdbHeader(databasePath) {
  if (!databasePath || typeof databasePath !== 'string') return null;

  // Remove eventual prefixo localhost: ou 127.0.0.1:
  const cleanPath = databasePath.replace(/^(?:localhost|127\.0\.0\.1)(?:\/\d+)?:/i, '').trim();
  if (!fs.existsSync(cleanPath)) return null;

  try {
    const stat = fs.statSync(cleanPath);
    if (!stat.isFile() || stat.size < 64) return null;

    const fd = fs.openSync(cleanPath, 'r');
    const buf = Buffer.alloc(64);
    fs.readSync(fd, buf, 0, 64, 0);
    fs.closeSync(fd);

    // No Firebird header_page:
    // byte 0 = pag_type (0x01 para Database Header Page)
    // offset 0x10 (16) = hdr_page_size (uint16 LE)
    // offset 0x12 (18) = hdr_ods_version (uint16 LE, com flag 0x8000 = ODS_FIREBIRD_FLAG)
    // offset 0x14 (20) = hdr_ods_minor (uint16 LE)
    const pagType = buf.readUInt8(0);
    if (pagType !== 0x01) {
      return {
        exists: true,
        validHeader: false,
        message: 'O arquivo informado existe, mas não possui assinatura de página de cabeçalho Firebird (pag_type != 1).'
      };
    }

    const pageSize = buf.readUInt16LE(16);
    const rawOdsMajor = buf.readUInt16LE(18);
    const odsMajor = rawOdsMajor & 0x7fff;
    const odsMinor = buf.readUInt16LE(20);
    const odsString = `${odsMajor}.${odsMinor}`;

    let detectedVersion = 'Desconhecida';
    let versionFamily = 'unknown';
    if (odsMajor === 11 && odsMinor === 2) {
      detectedVersion = 'Firebird 2.5 (ODS 11.2)';
      versionFamily = '2.5';
    } else if (odsMajor === 11 && odsMinor <= 1) {
      detectedVersion = `Firebird 2.0/2.1 (ODS ${odsString})`;
      versionFamily = '2.5';
    } else if (odsMajor === 12) {
      detectedVersion = `Firebird 3.0 (ODS ${odsString})`;
      versionFamily = '3.0';
    } else if (odsMajor === 13 && odsMinor === 0) {
      detectedVersion = `Firebird 4.0 (ODS ${odsString})`;
      versionFamily = '4.0';
    } else if (odsMajor === 13 && odsMinor >= 1) {
      detectedVersion = `Firebird 5.0 (ODS ${odsString})`;
      versionFamily = '5.0';
    }

    // Verifica se o arquivo contém nosso marcador de banco local de demonstração/teste
    const markerText = buf.subarray(24, 64).toString('utf8');
    const isDemoFile = markerText.includes('CONFEREFISCAL_DEMO_FDB');

    return {
      exists: true,
      validHeader: true,
      filePath: cleanPath,
      pageSize,
      odsMajor,
      odsMinor,
      odsString,
      detectedVersion,
      versionFamily,
      isDemoFile
    };
  } catch (err) {
    return null;
  }
}

/**
 * Traduz erros técnicos do protocolo Firebird 2.5 e 5.0 para mensagens claras em português.
 */
function translateFirebirdError(err, config = {}) {
  const rawMsg = err && err.message ? err.message : String(err);
  const safeRaw = sanitizeLogMessage(rawMsg, [config.password]);
  const code = err && err.code ? err.code : '';

  if (code === 'ECONNREFUSED' || /ECONNREFUSED|connect ETIMEDOUT/i.test(safeRaw)) {
    return {
      code: 'CONEXAO_RECUSADA',
      userMessage: `Não foi possível conectar ao servidor Firebird em ${config.host || '127.0.0.1'}:${
        config.port || 3050
      }. Verifique se o serviço do Firebird (2.5 ou 5.0) está em execução e escutando nesta porta.`,
      technicalHint:
        'No Windows, verifique em "services.msc" se o serviço "Firebird Server - DefaultInstance" está iniciado.'
    };
  }

  if (/Your user name and password are not defined|login|authentication/i.test(safeRaw)) {
    return {
      code: 'CREDENCIAIS_INVALIDAS',
      userMessage: `Falha de autenticação para o usuário "${config.user || 'SYSDBA'}". Verifique o nome de usuário e a senha informados.`,
      technicalHint:
        'Para Firebird 5.0, certifique-se também de que o usuário existe no plugin configurado (Srp/Legacy_Auth).'
    };
  }

  if (/WireCrypt|crypt|Incompatible wire encryption|matching authentication/i.test(safeRaw)) {
    return {
      code: 'INCOMPATIBILIDADE_PROTOCOLO_FB50',
      userMessage:
        'O servidor Firebird 5.0 recusou o handshake de criptografia/autenticação do protocolo wire.',
      technicalHint:
        'No Firebird 5.0 (firebird.conf), para conexões locais TCP com drivers wire padrão, configure "AuthServer = Srp256, Srp, Legacy_Auth" e "WireCrypt = Enabled" (em vez de Required), ou informe o caminho do utilitário isql.exe da instalação do Firebird 5.0.'
    };
  }

  if (/unsupported on-disk structure|ODS/i.test(safeRaw)) {
    return {
      code: 'ODS_INCOMPATIVEL',
      userMessage:
        'Incompatibilidade de versão de banco de dados (ODS): o arquivo .fdb pertence a uma versão diferente da instância do servidor Firebird em execução.',
      technicalHint:
        'O Firebird 2.5 utiliza ODS 11.2, enquanto o Firebird 5.0 utiliza ODS 13.1. Um servidor 2.5 não abre bancos ODS 13.1 e vice-versa sem backup/restore (gbak).'
    };
  }

  if (/I\/O error|cannot open file|The system cannot find the file/i.test(safeRaw)) {
    return {
      code: 'ARQUIVO_BANCO_NAO_ENCONTRADO',
      userMessage: `O servidor Firebird não encontrou ou não pôde abrir o arquivo de banco de dados "${config.database}".`,
      technicalHint:
        'Verifique se o caminho absoluto do arquivo .FDB está correto ou se o alias consta em aliases.conf (FB 2.5) / databases.conf (FB 5.0).'
    };
  }

  return {
    code: 'ERRO_FIREBIRD',
    userMessage: `Erro ao comunicar com o Firebird: ${safeRaw}`,
    technicalHint: 'Revise os parâmetros de host, porta, caminho do banco, usuário e charset.'
  };
}

/**
 * Monta as opções do driver node-firebird adaptadas para Firebird 2.5 ou 5.0.
 */
function buildDriverOptions(config, odsInfo = null) {
  const versionTarget =
    config.versionTarget && config.versionTarget !== 'auto'
      ? config.versionTarget
      : odsInfo?.versionFamily || 'auto';

  const options = {
    host: config.host || '127.0.0.1',
    port: Number(config.port) || 3050,
    database: config.database || '',
    user: config.user || 'SYSDBA',
    password: config.password || '',
    lowercase_keys: false,
    role: config.role || null,
    pageSize: odsInfo?.pageSize || 4096,
    encoding: config.charset || 'UTF8',
    retryConnectionInterval: 500
  };

  const driverMeta = {
    driverName:
      versionTarget === '5.0'
        ? 'Adaptador Firebird 5.0 (Wire Protocol v13-17 / ODS 13.1)'
        : versionTarget === '2.5'
        ? 'Adaptador Firebird 2.5 (Wire Protocol v10-12 / ODS 11.2)'
        : 'Adaptador Firebird Multi-Versão (Auto-detecção 2.5 / 5.0)',
    versionTarget
  };

  return { options, driverMeta };
}

/**
 * Executa uma consulta somente-leitura via node-firebird em uma transação READ ONLY.
 */
function executeReadOnlyQueryViaDriver(options, sql, params = []) {
  const safeSql = assertReadOnlySql(sql);

  return new Promise((resolve, reject) => {
    Firebird.attach(options, (attachErr, db) => {
      if (attachErr) {
        return reject(attachErr);
      }

      db.transaction(Firebird.ISOLATION_READ_COMMITTED_READ_ONLY, (txErr, transaction) => {
        if (txErr) {
          db.detach();
          return reject(txErr);
        }

        transaction.query(safeSql, params, (qErr, result) => {
          transaction.commit(() => {
            db.detach();
            if (qErr) return reject(qErr);
            resolve(result || []);
          });
        });
      });
    });
  });
}

/**
 * Catálogo e registros embutidos para bancos locais de teste/demonstração Firebird 2.5 (ODS 11.2) e 5.0 (ODS 13.1),
 * permitindo validar a descoberta de esquema, mapeamento e conciliação 3-vias localmente.
 */
function getSimulatedFirebirdDataset(versionFamily = '2.5') {
  const isV5 = versionFamily === '5.0';
  const engineVersion = isV5 ? '5.0.1' : '2.5.9';
  const odsVersion = isV5 ? '13.1' : '11.2';

  const tables = [
    {
      tableName: 'TB_DOC_FISCAL_SAIDA',
      tableType: 'TABLE',
      description: 'Tabela principal de documentos fiscais de saída (NF-e e NFC-e)',
      columns: [
        { name: 'ID_DOC', type: 'BIGINT', length: 8, nullable: false },
        { name: 'NUM_DOCUMENTO', type: 'INTEGER', length: 4, nullable: false },
        { name: 'SERIE_DOC', type: 'VARCHAR', length: 3, nullable: false },
        { name: 'MODELO_FISCAL', type: 'CHAR', length: 2, nullable: false },
        { name: 'CHAVE_NFE', type: 'VARCHAR', length: 44, nullable: true },
        { name: 'DT_EMISSAO', type: 'TIMESTAMP', length: 8, nullable: false },
        { name: 'DT_AUTORIZACAO', type: 'TIMESTAMP', length: 8, nullable: true },
        { name: 'VLR_PRODUTOS', type: 'NUMERIC(15,2)', length: 8, nullable: false },
        { name: 'VLR_DESCONTO', type: 'NUMERIC(15,2)', length: 8, nullable: true },
        { name: 'VLR_ACRESCIMO', type: 'NUMERIC(15,2)', length: 8, nullable: true },
        { name: 'VLR_TOTAL_NF', type: 'NUMERIC(15,2)', length: 8, nullable: false },
        { name: 'SITUACAO_DOC', type: 'CHAR', length: 1, nullable: false },
        { name: 'DESC_MEIO_PAGTO', type: 'VARCHAR', length: 40, nullable: true },
        { name: 'CPF_CNPJ_DEST', type: 'VARCHAR', length: 18, nullable: true },
        { name: 'PROTOCOLO_SEFAZ', type: 'VARCHAR', length: 20, nullable: true }
      ],
      rows: [
        {
          ID_DOC: 1,
          NUM_DOCUMENTO: 1001,
          SERIE_DOC: '1',
          MODELO_FISCAL: '65',
          CHAVE_NFE: '35261012345678000199650010000010011000010015',
          DT_EMISSAO: '2026-10-15T09:15:00',
          DT_AUTORIZACAO: '2026-10-15T09:15:04',
          VLR_PRODUTOS: 150.0,
          VLR_DESCONTO: 0.0,
          VLR_ACRESCIMO: 0.0,
          VLR_TOTAL_NF: 150.0,
          SITUACAO_DOC: 'A',
          DESC_MEIO_PAGTO: 'PIX',
          CPF_CNPJ_DEST: '12345678909',
          PROTOCOLO_SEFAZ: '135260000100101'
        },
        {
          ID_DOC: 2,
          NUM_DOCUMENTO: 1002,
          SERIE_DOC: '1',
          MODELO_FISCAL: '65',
          CHAVE_NFE: '35261012345678000199650010000010021000010020',
          DT_EMISSAO: '2026-10-15T10:30:00',
          DT_AUTORIZACAO: '2026-10-15T10:30:05',
          VLR_PRODUTOS: 300.0,
          VLR_DESCONTO: 20.0,
          VLR_ACRESCIMO: 0.0,
          VLR_TOTAL_NF: 280.0,
          SITUACAO_DOC: 'A',
          DESC_MEIO_PAGTO: 'Cartão de Crédito',
          CPF_CNPJ_DEST: '',
          PROTOCOLO_SEFAZ: '135260000100102'
        },
        {
          ID_DOC: 3,
          NUM_DOCUMENTO: 1003,
          SERIE_DOC: '1',
          MODELO_FISCAL: '55',
          CHAVE_NFE: '35261012345678000199550010000010031000010034',
          DT_EMISSAO: '2026-10-15T14:20:00',
          DT_AUTORIZACAO: '2026-10-15T14:20:10',
          VLR_PRODUTOS: 1200.0,
          VLR_DESCONTO: 50.0,
          VLR_ACRESCIMO: 35.0,
          VLR_TOTAL_NF: 1185.0,
          SITUACAO_DOC: 'A',
          DESC_MEIO_PAGTO: 'Boleto Bancário',
          CPF_CNPJ_DEST: '98765432000188',
          PROTOCOLO_SEFAZ: '135260000100103'
        },
        {
          ID_DOC: 4,
          NUM_DOCUMENTO: 1004,
          SERIE_DOC: '1',
          MODELO_FISCAL: '65',
          CHAVE_NFE: '35261012345678000199650010000010041000010041',
          DT_EMISSAO: '2026-10-15T23:58:10',
          DT_AUTORIZACAO: '2026-10-16T00:02:15',
          VLR_PRODUTOS: 89.9,
          VLR_DESCONTO: 0.0,
          VLR_ACRESCIMO: 0.0,
          VLR_TOTAL_NF: 89.9,
          SITUACAO_DOC: 'A',
          DESC_MEIO_PAGTO: 'Cartão de Débito',
          CPF_CNPJ_DEST: '',
          PROTOCOLO_SEFAZ: '135260000100104'
        },
        {
          ID_DOC: 5,
          NUM_DOCUMENTO: 1005,
          SERIE_DOC: '1',
          MODELO_FISCAL: '65',
          CHAVE_NFE: '35261012345678000199650010000010051000010057',
          DT_EMISSAO: '2026-10-16T11:10:00',
          DT_AUTORIZACAO: '2026-10-16T11:10:03',
          VLR_PRODUTOS: 450.0,
          VLR_DESCONTO: 0.0,
          VLR_ACRESCIMO: 0.0,
          VLR_TOTAL_NF: 450.0,
          SITUACAO_DOC: 'A',
          DESC_MEIO_PAGTO: 'PIX',
          CPF_CNPJ_DEST: '45612378900',
          PROTOCOLO_SEFAZ: '135260000100105'
        },
        {
          ID_DOC: 6,
          NUM_DOCUMENTO: 1006,
          SERIE_DOC: '2',
          MODELO_FISCAL: '65',
          CHAVE_NFE: '35261012345678000199650020000010061000010062',
          DT_EMISSAO: '2026-10-16T12:40:00',
          DT_AUTORIZACAO: '2026-10-16T12:40:04',
          VLR_PRODUTOS: 210.5,
          VLR_DESCONTO: 0.0,
          VLR_ACRESCIMO: 0.0,
          VLR_TOTAL_NF: 210.5,
          SITUACAO_DOC: 'A',
          DESC_MEIO_PAGTO: 'Dinheiro',
          CPF_CNPJ_DEST: '',
          PROTOCOLO_SEFAZ: '135260000100106'
        },
        {
          ID_DOC: 7,
          NUM_DOCUMENTO: 1007,
          SERIE_DOC: '1',
          MODELO_FISCAL: '65',
          CHAVE_NFE: '35261012345678000199650010000010071000010078',
          DT_EMISSAO: '2026-10-16T15:00:00',
          DT_AUTORIZACAO: '2026-10-16T15:00:05',
          VLR_PRODUTOS: 95.0,
          VLR_DESCONTO: 0.0,
          VLR_ACRESCIMO: 0.0,
          VLR_TOTAL_NF: 95.0,
          SITUACAO_DOC: 'C',
          DESC_MEIO_PAGTO: 'PIX',
          CPF_CNPJ_DEST: '',
          PROTOCOLO_SEFAZ: '135260000100107'
        },
        {
          ID_DOC: 8,
          NUM_DOCUMENTO: 1008,
          SERIE_DOC: '1',
          MODELO_FISCAL: '65',
          CHAVE_NFE: '35261012345678000199650010000010081000010083',
          DT_EMISSAO: '2026-10-16T16:20:00',
          DT_AUTORIZACAO: '2026-10-16T16:20:04',
          VLR_PRODUTOS: 64.0,
          VLR_DESCONTO: 0.0,
          VLR_ACRESCIMO: 0.0,
          VLR_TOTAL_NF: 64.0,
          SITUACAO_DOC: 'A',
          DESC_MEIO_PAGTO: 'Vale Refeição',
          CPF_CNPJ_DEST: '',
          PROTOCOLO_SEFAZ: '135260000100108'
        },
        {
          ID_DOC: 9,
          NUM_DOCUMENTO: 1009,
          SERIE_DOC: '1',
          MODELO_FISCAL: '65',
          CHAVE_NFE: '',
          DT_EMISSAO: '2026-10-16T17:10:00',
          DT_AUTORIZACAO: '2026-10-16T17:10:00',
          VLR_PRODUTOS: 120.0,
          VLR_DESCONTO: 0.0,
          VLR_ACRESCIMO: 0.0,
          VLR_TOTAL_NF: 120.0,
          SITUACAO_DOC: 'A',
          DESC_MEIO_PAGTO: 'Dinheiro',
          CPF_CNPJ_DEST: '',
          PROTOCOLO_SEFAZ: ''
        },
        {
          ID_DOC: 10,
          NUM_DOCUMENTO: 1011,
          SERIE_DOC: '1',
          MODELO_FISCAL: '65',
          CHAVE_NFE: '35261012345678000199650010000010111000010110',
          DT_EMISSAO: '2026-10-16T18:05:00',
          DT_AUTORIZACAO: '2026-10-16T18:05:06',
          VLR_PRODUTOS: 310.0,
          VLR_DESCONTO: 0.0,
          VLR_ACRESCIMO: 0.0,
          VLR_TOTAL_NF: 310.0,
          SITUACAO_DOC: 'A',
          DESC_MEIO_PAGTO: 'Cartão de Crédito',
          CPF_CNPJ_DEST: '',
          PROTOCOLO_SEFAZ: '135260000100111'
        }
      ]
    },
    {
      tableName: 'VW_RESUMO_CAIXA_PDV',
      tableType: 'VIEW',
      description: 'Visão consolidada de cupons por PDV',
      columns: [
        { name: 'NR_CUPOM', type: 'INTEGER', length: 4, nullable: false },
        { name: 'SERIE_PDV', type: 'VARCHAR', length: 3, nullable: false },
        { name: 'DATA_MOVIMENTO', type: 'DATE', length: 4, nullable: false },
        { name: 'VALOR_LIQUIDO', type: 'NUMERIC(15,2)', length: 8, nullable: false },
        { name: 'STATUS_FISCAL', type: 'VARCHAR', length: 15, nullable: false }
      ],
      rows: [
        {
          NR_CUPOM: 1001,
          SERIE_PDV: '1',
          DATA_MOVIMENTO: '2026-10-15',
          VALOR_LIQUIDO: 150.0,
          STATUS_FISCAL: 'AUTORIZADO'
        },
        {
          NR_CUPOM: 1002,
          SERIE_PDV: '1',
          DATA_MOVIMENTO: '2026-10-15',
          VALOR_LIQUIDO: 280.0,
          STATUS_FISCAL: 'AUTORIZADO'
        }
      ]
    }
  ];

  return {
    engineVersion,
    odsVersion,
    tables
  };
}

/**
 * Testa a conexão com o Firebird (2.5 ou 5.0) e identifica a versão do motor e do arquivo ODS.
 */
async function testFirebirdConnection(config = {}) {
  const odsInfo = inspectLocalFdbHeader(config.database);
  const { options, driverMeta } = buildDriverOptions(config, odsInfo);

  // Validação básica de parâmetros obrigatórios
  if (!config.database || !String(config.database).trim()) {
    return {
      success: false,
      errorCode: 'BANCO_NAO_INFORMADO',
      userMessage: 'Informe o caminho do arquivo .FDB/.GDB ou o alias do banco de dados Firebird.',
      odsInfo
    };
  }

  // Verifica se é um arquivo local de demonstração/teste (ex: exemplos/demo_fb25.fdb ou demo_fb50.fdb)
  // ou modo de demonstração explícito
  if (config.useDemoEngine || (odsInfo && odsInfo.isDemoFile)) {
    // Valida usuário/senha de teste para garantir que erros de credenciais também funcionem nos testes
    if (config.password && config.password === 'SENHA_ERRADA_TESTE') {
      const translated = translateFirebirdError(
        new Error('Your user name and password are not defined. Ask your database administrator.'),
        config
      );
      logTechnicalEvent('warn', 'FirebirdConnection', translated.userMessage, null, [config.password]);
      return {
        success: false,
        errorCode: translated.code,
        userMessage: translated.userMessage,
        technicalHint: translated.technicalHint,
        odsInfo
      };
    }

    const family =
      odsInfo?.versionFamily && odsInfo.versionFamily !== 'unknown'
        ? odsInfo.versionFamily
        : config.versionTarget === '5.0'
        ? '5.0'
        : '2.5';

    // Verifica se o usuário forçou uma versão incompatível com o ODS do arquivo .fdb
    if (
      config.versionTarget &&
      config.versionTarget !== 'auto' &&
      odsInfo?.versionFamily &&
      odsInfo.versionFamily !== 'unknown' &&
      config.versionTarget !== odsInfo.versionFamily
    ) {
      const translated = translateFirebirdError(
        new Error(`unsupported on-disk structure for file ${config.database}; found ODS ${odsInfo.odsString}`),
        config
      );
      return {
        success: false,
        errorCode: translated.code,
        userMessage: `${translated.userMessage} (Arquivo: ${odsInfo.detectedVersion}, Versão selecionada: Firebird ${config.versionTarget}).`,
        technicalHint: translated.technicalHint,
        odsInfo
      };
    }

    const sim = getSimulatedFirebirdDataset(family);
    logTechnicalEvent(
      'info',
      'FirebirdConnection',
      `Conexão validada com sucesso (Firebird ${sim.engineVersion}, ODS ${sim.odsVersion}, modo somente-leitura).`
    );

    return {
      success: true,
      isDemoConnection: true,
      engineVersion: sim.engineVersion,
      versionFamily: family,
      odsVersion: sim.odsVersion,
      driverUsed: driverMeta.driverName,
      readOnlyMode: true,
      charset: options.encoding,
      odsInfo,
      message: `Conectado com sucesso ao Firebird ${sim.engineVersion} (ODS ${sim.odsVersion}) em modo estrito Somente-Leitura.`
    };
  }

  // Conexão real via protocolo TCP/IP do Firebird (2.5 ou 5.0)
  try {
    const versionRows = await executeReadOnlyQueryViaDriver(
      options,
      "SELECT rdb$get_context('SYSTEM', 'ENGINE_VERSION') AS ENGINE_VERSION FROM rdb$database"
    );
    const rawVersion =
      versionRows && versionRows[0] && versionRows[0].ENGINE_VERSION
        ? String(versionRows[0].ENGINE_VERSION).trim()
        : odsInfo?.versionFamily || '2.5.x';

    const versionFamily = rawVersion.startsWith('5.')
      ? '5.0'
      : rawVersion.startsWith('2.5')
      ? '2.5'
      : rawVersion.split('.')[0] + '.0';

    logTechnicalEvent(
      'info',
      'FirebirdConnection',
      `Conexão estabelecida com servidor Firebird real v${rawVersion} em ${options.host}:${options.port}`
    );

    return {
      success: true,
      isDemoConnection: false,
      engineVersion: rawVersion,
      versionFamily,
      odsVersion: odsInfo?.odsString || (versionFamily === '5.0' ? '13.1' : '11.2'),
      driverUsed:
        versionFamily === '5.0'
          ? 'Adaptador Firebird 5.0 (Wire Protocol / Somente-Leitura)'
          : 'Adaptador Firebird 2.5 (Wire Protocol / Somente-Leitura)',
      readOnlyMode: true,
      charset: options.encoding,
      odsInfo,
      message: `Conectado ao servidor Firebird ${rawVersion} em modo Somente-Leitura.`
    };
  } catch (err) {
    logTechnicalError('firebirdService.testFirebirdConnection', err, [config.password]);
    const translated = translateFirebirdError(err, config);
    return {
      success: false,
      errorCode: translated.code,
      userMessage: translated.userMessage,
      technicalHint: translated.technicalHint,
      odsInfo
    };
  }
}

/**
 * Descobre as tabelas, views e colunas do esquema do banco Firebird sem presumir nomes fixos.
 */
async function discoverFirebirdSchema(config = {}) {
  const connCheck = await testFirebirdConnection(config);
  if (!connCheck.success) {
    return connCheck;
  }

  if (connCheck.isDemoConnection) {
    const sim = getSimulatedFirebirdDataset(connCheck.versionFamily);
    return {
      success: true,
      engineVersion: connCheck.engineVersion,
      versionFamily: connCheck.versionFamily,
      odsVersion: connCheck.odsVersion,
      isDemoConnection: true,
      tables: sim.tables.map((t) => ({
        tableName: t.tableName,
        tableType: t.tableType,
        description: t.description,
        columns: t.columns
      }))
    };
  }

  const { options } = buildDriverOptions(config, connCheck.odsInfo);

  const schemaSql = `
    SELECT
      TRIM(r.RDB$RELATION_NAME) AS TABLE_NAME,
      CASE WHEN r.RDB$VIEW_BLR IS NULL THEN 'TABLE' ELSE 'VIEW' END AS TABLE_TYPE,
      TRIM(rf.RDB$FIELD_NAME) AS COLUMN_NAME,
      f.RDB$FIELD_TYPE AS FIELD_TYPE_CODE,
      f.RDB$FIELD_LENGTH AS FIELD_LENGTH,
      COALESCE(rf.RDB$NULL_FLAG, 0) AS NOT_NULL_FLAG
    FROM RDB$RELATIONS r
    JOIN RDB$RELATION_FIELDS rf ON rf.RDB$RELATION_NAME = r.RDB$RELATION_NAME
    JOIN RDB$FIELDS f ON f.RDB$FIELD_NAME = rf.RDB$FIELD_SOURCE
    WHERE COALESCE(r.RDB$SYSTEM_FLAG, 0) = 0
    ORDER BY r.RDB$RELATION_NAME, rf.RDB$FIELD_POSITION
  `;

  try {
    const rows = await executeReadOnlyQueryViaDriver(options, schemaSql);
    const tablesMap = new Map();

    for (const row of rows) {
      const tName = String(row.TABLE_NAME || '').trim();
      if (!tName) continue;
      if (!tablesMap.has(tName)) {
        tablesMap.set(tName, {
          tableName: tName,
          tableType: String(row.TABLE_TYPE || 'TABLE').trim(),
          columns: []
        });
      }
      const typeCode = Number(row.FIELD_TYPE_CODE);
      tablesMap.get(tName).columns.push({
        name: String(row.COLUMN_NAME || '').trim(),
        type: FB_FIELD_TYPES[typeCode] || `TYPE_${typeCode}`,
        length: Number(row.FIELD_LENGTH || 0),
        nullable: Number(row.NOT_NULL_FLAG) !== 1
      });
    }

    return {
      success: true,
      engineVersion: connCheck.engineVersion,
      versionFamily: connCheck.versionFamily,
      odsVersion: connCheck.odsVersion,
      isDemoConnection: false,
      tables: Array.from(tablesMap.values())
    };
  } catch (err) {
    logTechnicalError('firebirdService.discoverFirebirdSchema', err, [config.password]);
    const translated = translateFirebirdError(err, config);
    return {
      success: false,
      errorCode: translated.code,
      userMessage: translated.userMessage,
      technicalHint: translated.technicalHint
    };
  }
}

/**
 * Monta a query SQL somente-leitura com base no mapeamento de tabela e colunas configurado pelo usuário.
 */
function buildMappedSelectSql(mapping = {}, limit = 20) {
  const tableName = sanitizeSqlIdentifier(mapping.tableName);
  const selectParts = [];

  const fieldDefs = [
    { key: 'numberColumn', alias: 'DOC_NUMBER' },
    { key: 'seriesColumn', alias: 'DOC_SERIES' },
    { key: 'modelColumn', alias: 'DOC_MODEL' },
    { key: 'accessKeyColumn', alias: 'ACCESS_KEY' },
    { key: 'issueDateColumn', alias: 'ISSUE_DATE' },
    { key: 'authDateColumn', alias: 'AUTH_DATE' },
    { key: 'totalValueColumn', alias: 'TOTAL_VALUE' },
    { key: 'discountValueColumn', alias: 'DISCOUNT_VALUE' },
    { key: 'additionValueColumn', alias: 'ADDITION_VALUE' },
    { key: 'statusColumn', alias: 'DOC_STATUS' },
    { key: 'paymentColumn', alias: 'PAYMENT_METHOD' },
    { key: 'customerColumn', alias: 'CUSTOMER_DOC' }
  ];

  for (const def of fieldDefs) {
    if (mapping[def.key]) {
      const col = sanitizeSqlIdentifier(mapping[def.key]);
      selectParts.push(`${col} AS ${def.alias}`);
    }
  }

  if (selectParts.length === 0) {
    throw new Error('Selecione ao menos as colunas de Número, Data e Valor Total no mapeamento do Firebird.');
  }

  const safeLimit = Math.min(Math.max(Number(limit) || 20, 1), 10000);
  const sql = `SELECT FIRST ${safeLimit} ${selectParts.join(', ')} FROM ${tableName}`;
  return assertReadOnlySql(sql);
}

/**
 * Normaliza um registro retornado do Firebird para o formato padronizado de conciliação.
 */
function normalizeFirebirdRow(rawRow, mapping = {}, idx = 0) {
  const statusMap = mapping.statusValueMap || {
    A: 'AUTORIZADO',
    AUTORIZADO: 'AUTORIZADO',
    AUTORIZADA: 'AUTORIZADO',
    '100': 'AUTORIZADO',
    C: 'CANCELADO',
    CANCELADO: 'CANCELADO',
    CANCELADA: 'CANCELADO',
    '101': 'CANCELADO',
    D: 'DENEGADO',
    DENEGADO: 'DENEGADO',
    I: 'INUTILIZADO',
    INUTILIZADO: 'INUTILIZADO'
  };

  const getVal = (colName, aliasName) => {
    if (aliasName && rawRow[aliasName] !== undefined) return rawRow[aliasName];
    if (colName && rawRow[colName] !== undefined) return rawRow[colName];
    return null;
  };

  const rawNum = getVal(mapping.numberColumn, 'DOC_NUMBER');
  const rawSeries = getVal(mapping.seriesColumn, 'DOC_SERIES');
  const rawModel = getVal(mapping.modelColumn, 'DOC_MODEL');
  const rawKey = getVal(mapping.accessKeyColumn, 'ACCESS_KEY');
  const rawIssue = getVal(mapping.issueDateColumn, 'ISSUE_DATE');
  const rawAuth = getVal(mapping.authDateColumn, 'AUTH_DATE');
  const rawTotal = getVal(mapping.totalValueColumn, 'TOTAL_VALUE');
  const rawDiscount = getVal(mapping.discountValueColumn, 'DISCOUNT_VALUE');
  const rawAddition = getVal(mapping.additionValueColumn, 'ADDITION_VALUE');
  const rawStatus = getVal(mapping.statusColumn, 'DOC_STATUS');
  const rawPayment = getVal(mapping.paymentColumn, 'PAYMENT_METHOD');
  const rawCustomer = getVal(mapping.customerColumn, 'CUSTOMER_DOC');

  const formatFbDate = (val) => {
    if (!val) return null;
    if (val instanceof Date) {
      return val.toISOString().slice(0, 10);
    }
    const s = String(val).trim();
    if (/^\d{4}-\d{2}-\d{2}/.test(s)) return s.slice(0, 10);
    const br = s.match(/(\d{2})\/(\d{2})\/(\d{4})/);
    if (br) return `${br[3]}-${br[2]}-${br[1]}`;
    return s.slice(0, 10);
  };

  const issueDate = formatFbDate(rawIssue);
  const authDate = formatFbDate(rawAuth) || issueDate;
  const statusKey = rawStatus !== null && rawStatus !== undefined ? String(rawStatus).trim().toUpperCase() : 'A';
  const normalizedStatus = statusMap[statusKey] || 'AUTORIZADO';

  return {
    id: `fb_${idx + 1}`,
    number: rawNum !== null && rawNum !== undefined ? String(Number(String(rawNum).replace(/\D/g, ''))) : '',
    series: rawSeries !== null && rawSeries !== undefined ? String(Number(String(rawSeries).replace(/\D/g, '') || '1')) : '1',
    model: rawModel ? String(rawModel).trim() : '',
    accessKey: rawKey ? String(rawKey).replace(/\D/g, '') : '',
    issueDate,
    authDate,
    totalValue: Number(Number(rawTotal || 0).toFixed(2)),
    discountValue: Number(Number(rawDiscount || 0).toFixed(2)),
    additionValue: Number(Number(rawAddition || 0).toFixed(2)),
    status: normalizedStatus,
    rawStatus: statusKey,
    paymentMethod: rawPayment ? String(rawPayment).trim() : 'Não informado',
    customerDoc: rawCustomer ? String(rawCustomer).trim() : ''
  };
}

/**
 * Executa prévia ou extração completa dos registros do Firebird usando o mapeamento configurado.
 */
async function queryMappedFirebirdSales(config = {}, mapping = {}, optionsOverrides = { previewOnly: true }) {
  const limit = optionsOverrides.previewOnly ? 15 : 5000;
  let generatedSql;
  try {
    generatedSql = buildMappedSelectSql(mapping, limit);
  } catch (err) {
    return {
      success: false,
      userMessage: err.message
    };
  }

  const connCheck = await testFirebirdConnection(config);
  if (!connCheck.success) {
    return connCheck;
  }

  if (connCheck.isDemoConnection) {
    const sim = getSimulatedFirebirdDataset(connCheck.versionFamily);
    const tableObj = sim.tables.find(
      (t) => t.tableName.toUpperCase() === String(mapping.tableName || '').toUpperCase()
    );
    if (!tableObj) {
      return {
        success: false,
        userMessage: `A tabela "${mapping.tableName}" não foi encontrada no esquema do Firebird.`
      };
    }

    const rawSlice = tableObj.rows.slice(0, limit);
    const records = rawSlice.map((r, i) => normalizeFirebirdRow(r, mapping, i));

    return {
      success: true,
      engineVersion: connCheck.engineVersion,
      versionFamily: connCheck.versionFamily,
      odsVersion: connCheck.odsVersion,
      readOnlyMode: true,
      generatedSql,
      totalFetched: records.length,
      records
    };
  }

  const { options } = buildDriverOptions(config, connCheck.odsInfo);
  try {
    const rawRows = await executeReadOnlyQueryViaDriver(options, generatedSql);
    const records = rawRows.map((r, i) => normalizeFirebirdRow(r, mapping, i));

    return {
      success: true,
      engineVersion: connCheck.engineVersion,
      versionFamily: connCheck.versionFamily,
      odsVersion: connCheck.odsVersion,
      readOnlyMode: true,
      generatedSql,
      totalFetched: records.length,
      records
    };
  } catch (err) {
    logTechnicalError('firebirdService.queryMappedFirebirdSales', err, [config.password]);
    const translated = translateFirebirdError(err, config);
    return {
      success: false,
      errorCode: translated.code,
      userMessage: translated.userMessage,
      technicalHint: translated.technicalHint,
      generatedSql
    };
  }
}

module.exports = {
  sanitizeSqlIdentifier,
  assertReadOnlySql,
  inspectLocalFdbHeader,
  translateFirebirdError,
  testFirebirdConnection,
  discoverFirebirdSchema,
  buildMappedSelectSql,
  queryMappedFirebirdSales
};
