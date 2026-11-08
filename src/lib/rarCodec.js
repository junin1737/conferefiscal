const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');
const { crc32 } = require('./zipCodec');

/**
 * Extrai entradas de um arquivo RAR v4 (52 61 72 21 1A 07 00) em JavaScript puro (método Store 0x30).
 */
function parseRarV4BufferPure(buffer) {
  const signature = Buffer.from([0x52, 0x61, 0x72, 0x21, 0x1a, 0x07, 0x00]);
  if (buffer.length < 14 || !buffer.subarray(0, 7).equals(signature)) {
    return null;
  }

  const files = [];
  let offset = 7;

  while (offset + 7 <= buffer.length) {
    const headCrc = buffer.readUInt16LE(offset);
    const headType = buffer.readUInt8(offset + 2);
    const headFlags = buffer.readUInt16LE(offset + 3);
    const headSize = buffer.readUInt16LE(offset + 5);

    if (headSize < 7 || offset + headSize > buffer.length) break;

    let addSize = 0;
    if (headType === 0x74) {
      // FILE_HEADER
      const packSize = buffer.readUInt32LE(offset + 7);
      const unpSize = buffer.readUInt32LE(offset + 11);
      const method = buffer.readUInt8(offset + 25); // 0x30 = Store (sem compressão)
      const nameSize = buffer.readUInt16LE(offset + 26);
      const isDir = (headFlags & 0xe0) === 0xe0;

      const nameStart = offset + 32;
      const nameEnd = Math.min(nameStart + nameSize, offset + headSize);
      const rawName = buffer
        .subarray(nameStart, nameEnd)
        .toString('utf8')
        .split('\0')[0]
        .replace(/\\/g, '/');

      const dataStart = offset + headSize;
      const dataEnd = dataStart + packSize;
      if (dataEnd > buffer.length) break;

      if (method === 0x30) {
        files.push({
          name: rawName,
          isDirectory: isDir,
          data: isDir ? Buffer.alloc(0) : Buffer.from(buffer.subarray(dataStart, dataEnd))
        });
      } else {
        // Caso o RAR use compressão proprietária (0x31..0x35), delega ao extrator nativo (bsdtar / node-unrar-js)
        return null;
      }

      addSize = packSize;
    } else if (headType === 0x7b) {
      // ENDARC_HEAD
      break;
    } else if (headFlags & 0x8000) {
      addSize = buffer.readUInt32LE(offset + 7);
    }

    offset += headSize + addSize;
  }

  return files;
}

/**
 *Constrói um pacote .RAR v4 válido (assinatura Rar!\x1a\x07\x00) com arquivos e subpastas para validação local.
 */
function buildSampleRarBuffer(entries = []) {
  const parts = [];

  // 1. MARK_HEAD (7 bytes): 52 61 72 21 1A 07 00
  parts.push(Buffer.from([0x52, 0x61, 0x72, 0x21, 0x1a, 0x07, 0x00]));

  // 2. MAIN_HEAD (13 bytes): tipo 0x73
  const mainHead = Buffer.alloc(13, 0);
  mainHead.writeUInt8(0x73, 2); // head_type = MAIN_HEAD
  mainHead.writeUInt16LE(0x0000, 3); // flags
  mainHead.writeUInt16LE(13, 5); // head_size = 13
  const mainCrc = crc32(mainHead.subarray(2)) & 0xffff;
  mainHead.writeUInt16LE(mainCrc, 0);
  parts.push(mainHead);

  // 3. FILE_HEAD (tipo 0x74) para cada arquivo
  for (const item of entries) {
    const internalName = String(item.internalPath || item.name).replace(/\//g, '\\');
    const nameBuf = Buffer.from(internalName, 'utf8');
    const dataBuf = Buffer.isBuffer(item.content)
      ? item.content
      : Buffer.from(String(item.content || ''), 'utf8');

    const headSize = 32 + nameBuf.length;
    const fileHead = Buffer.alloc(headSize, 0);

    fileHead.writeUInt8(0x74, 2); // head_type = FILE_HEAD
    fileHead.writeUInt16LE(0x8000, 3); // flags (LONG_BLOCK)
    fileHead.writeUInt16LE(headSize, 5); // head_size
    fileHead.writeUInt32LE(dataBuf.length, 7); // pack_size
    fileHead.writeUInt32LE(dataBuf.length, 11); // unp_size
    fileHead.writeUInt8(0x02, 15); // host_os = Win32
    fileHead.writeUInt32LE(crc32(dataBuf) >>> 0, 16); // file_crc
    fileHead.writeUInt32LE(0x54210000, 20); // ftime
    fileHead.writeUInt8(20, 24); // unp_ver = 2.0
    fileHead.writeUInt8(0x30, 25); // method = 0x30 (Store)
    fileHead.writeUInt16LE(nameBuf.length, 26); // name_size
    fileHead.writeUInt32LE(0x00000020, 28); // attr = Archive

    nameBuf.copy(fileHead, 32);

    const headCrc = crc32(fileHead.subarray(2)) & 0xffff;
    fileHead.writeUInt16LE(headCrc, 0);

    parts.push(fileHead, dataBuf);
  }

  // 4. ENDARC_HEAD (7 bytes): tipo 0x7B
  const endHead = Buffer.from([0xc4, 0x3d, 0x7b, 0x00, 0x40, 0x07, 0x00]);
  parts.push(endHead);

  return Buffer.concat(parts);
}

/**
 * Extrai arquivos de um buffer .RAR (suportando RAR v4/v5 via parser puro, node-unrar-js ou bsdtar do Windows).
 */
async function extractRarEntries(buffer, tempStagingDir = null) {
  // 1. Tenta node-unrar-js se estiver instalado
  try {
    const { createExtractorFromData } = require('node-unrar-js');
    const arrayBuffer = buffer.buffer.slice(
      buffer.byteOffset,
      buffer.byteOffset + buffer.byteLength
    );
    const extractor = await createExtractorFromData({ data: arrayBuffer });
    const extracted = extractor.extract();
    const out = [];
    for (const f of extracted.files) {
      if (f.fileHeader && !f.fileHeader.flags.directory && f.extraction) {
        out.push({
          name: f.fileHeader.name.replace(/\\/g, '/'),
          isDirectory: false,
          data: Buffer.from(f.extraction)
        });
      }
    }
    if (out.length > 0) return out;
  } catch (_) {}

  // 2. Tenta parser RAR v4 puro em memória
  const pureFiles = parseRarV4BufferPure(buffer);
  if (pureFiles && pureFiles.length > 0) {
    return pureFiles;
  }

  // 3. Fallback: utilitário nativo tar.exe (libarchive do Windows 10/11 que suporta RAR4 e RAR5)
  const sysTar = 'C:\\Windows\\System32\\tar.exe';
  if (fs.existsSync(sysTar) && tempStagingDir) {
    const workDir = path.join(tempStagingDir, `rar_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`);
    const rarFile = path.join(workDir, 'input.rar');
    const outDir = path.join(workDir, 'out');
    fs.mkdirSync(outDir, { recursive: true });

    try {
      fs.writeFileSync(rarFile, buffer);
      execFileSync(sysTar, ['-xf', rarFile, '-C', outDir], { stdio: 'ignore', timeout: 15000 });

      const collected = [];
      function walk(dir, relBase = '') {
        for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
          const full = path.join(dir, entry.name);
          const rel = relBase ? `${relBase}/${entry.name}` : entry.name;
          if (entry.isDirectory()) {
            walk(full, rel);
          } else if (entry.isFile()) {
            collected.push({
              name: rel,
              isDirectory: false,
              data: fs.readFileSync(full)
            });
          }
        }
      }
      walk(outDir);
      fs.rmSync(workDir, { recursive: true, force: true });
      return collected;
    } catch (err) {
      try {
        fs.rmSync(workDir, { recursive: true, force: true });
      } catch (_) {}
      throw new Error(`Falha ao descompactar arquivo RAR: ${err.message}`);
    }
  }

  throw new Error('Formato RAR corrompido ou método de compressão não suportado.');
}

module.exports = {
  parseRarV4BufferPure,
  buildSampleRarBuffer,
  extractRarEntries
};
