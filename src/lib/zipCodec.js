const zlib = require('zlib');

// Tabela CRC32 padrão IEEE 802.3
const CRC_TABLE = new Uint32Array(256);
for (let n = 0; n < 256; n++) {
  let c = n;
  for (let k = 0; k < 8; k++) {
    c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  }
  CRC_TABLE[n] = c >>> 0;
}

function crc32(buf) {
  let crc = 0xffffffff;
  for (let i = 0; i < buf.length; i++) {
    crc = CRC_TABLE[(crc ^ buf[i]) & 0xff] ^ (crc >>> 8);
  }
  return (crc ^ 0xffffffff) >>> 0;
}

/**
 * Implementação nativa em Node.js do leitor e escritor de arquivos .ZIP (suporta métodos Store=0 e Deflate=8 e subpastas).
 */
class PureZip {
  constructor(buffer = null) {
    this._entries = [];
    if (buffer) {
      this._parseBuffer(buffer);
    }
  }

  _parseBuffer(buffer) {
    if (!Buffer.isBuffer(buffer) || buffer.length < 22) {
      throw new Error('Buffer ZIP inválido ou muito pequeno.');
    }

    // Localiza End of Central Directory Record (assinatura 0x06054b50)
    let eocdOffset = -1;
    const minSearch = Math.max(0, buffer.length - 65557);
    for (let i = buffer.length - 22; i >= minSearch; i--) {
      if (buffer.readUInt32LE(i) === 0x06054b50) {
        eocdOffset = i;
        break;
      }
    }

    if (eocdOffset === -1) {
      throw new Error('Índice Central (EOCD) do arquivo ZIP não encontrado.');
    }

    const totalEntries = buffer.readUInt16LE(eocdOffset + 10);
    const cdSize = buffer.readUInt32LE(eocdOffset + 12);
    const cdOffset = buffer.readUInt32LE(eocdOffset + 16);

    if (cdOffset + cdSize > buffer.length) {
      throw new Error('Índice Central do arquivo ZIP truncado ou corrompido.');
    }

    let ptr = cdOffset;
    for (let idx = 0; idx < totalEntries; idx++) {
      if (ptr + 46 > buffer.length) break;
      const sig = buffer.readUInt32LE(ptr);
      if (sig !== 0x02014b50) {
        throw new Error('Cabeçalho de diretório central do ZIP corrompido.');
      }

      const method = buffer.readUInt16LE(ptr + 10);
      const crc = buffer.readUInt32LE(ptr + 16);
      const compressedSize = buffer.readUInt32LE(ptr + 20);
      const uncompressedSize = buffer.readUInt32LE(ptr + 24);
      const nameLen = buffer.readUInt16LE(ptr + 28);
      const extraLen = buffer.readUInt16LE(ptr + 30);
      const commentLen = buffer.readUInt16LE(ptr + 32);
      const localHeaderOffset = buffer.readUInt32LE(ptr + 42);

      const entryName = buffer
        .subarray(ptr + 46, ptr + 46 + nameLen)
        .toString('utf8')
        .replace(/\\/g, '/');
      const isDirectory = entryName.endsWith('/');

      this._entries.push({
        entryName,
        isDirectory,
        header: {
          method,
          crc,
          compressedSize,
          size: uncompressedSize
        },
        getData: () => {
          if (isDirectory) return Buffer.alloc(0);
          if (localHeaderOffset + 30 > buffer.length) {
            throw new Error(`Cabeçalho local inválido para "${entryName}".`);
          }
          const localSig = buffer.readUInt32LE(localHeaderOffset);
          if (localSig !== 0x04034b50) {
            throw new Error(`Assinatura local inválida para "${entryName}".`);
          }
          const lNameLen = buffer.readUInt16LE(localHeaderOffset + 26);
          const lExtraLen = buffer.readUInt16LE(localHeaderOffset + 28);
          const dataStart = localHeaderOffset + 30 + lNameLen + lExtraLen;
          const dataEnd = dataStart + compressedSize;

          if (dataEnd > buffer.length) {
            throw new Error(`Dados comprimidos truncados para "${entryName}".`);
          }

          const rawSlice = buffer.subarray(dataStart, dataEnd);
          if (method === 0) {
            return Buffer.from(rawSlice);
          }
          if (method === 8) {
            return zlib.inflateRawSync(rawSlice);
          }
          throw new Error(`Método de compressão ZIP não suportado (${method}) em "${entryName}".`);
        }
      });

      ptr += 46 + nameLen + extraLen + commentLen;
    }
  }

  getEntries() {
    return this._entries;
  }

  addFile(entryName, dataBuffer) {
    const cleanName = String(entryName).replace(/\\/g, '/').replace(/^\/+/, '');
    const buf = Buffer.isBuffer(dataBuffer) ? dataBuffer : Buffer.from(String(dataBuffer || ''), 'utf8');
    const compressed = zlib.deflateRawSync(buf);
    const fileCrc = crc32(buf);

    this._entries.push({
      entryName: cleanName,
      isDirectory: false,
      _rawUncompressed: buf,
      _rawCompressed: compressed,
      header: {
        method: 8,
        crc: fileCrc,
        compressedSize: compressed.length,
        size: buf.length
      },
      getData: () => buf
    });
  }

  toBuffer() {
    const localParts = [];
    const centralParts = [];
    let currentOffset = 0;

    for (const entry of this._entries) {
      const uncomp = entry._rawUncompressed || entry.getData();
      const comp = entry._rawCompressed || zlib.deflateRawSync(uncomp);
      const entryCrc = entry.header?.crc !== undefined ? entry.header.crc : crc32(uncomp);
      const nameBuf = Buffer.from(entry.entryName, 'utf8');

      // Local file header (30 bytes + name)
      const localHeader = Buffer.alloc(30);
      localHeader.writeUInt32LE(0x04034b50, 0); // Signature
      localHeader.writeUInt16LE(20, 4); // Version needed (2.0)
      localHeader.writeUInt16LE(0x0800, 6); // UTF-8 flag
      localHeader.writeUInt16LE(8, 8); // Method: Deflate (8)
      localHeader.writeUInt16LE(0, 10); // Mod time
      localHeader.writeUInt16LE(0, 12); // Mod date
      localHeader.writeUInt32LE(entryCrc >>> 0, 14); // CRC32
      localHeader.writeUInt32LE(comp.length, 18); // Compressed size
      localHeader.writeUInt32LE(uncomp.length, 22); // Uncompressed size
      localHeader.writeUInt16LE(nameBuf.length, 26); // Filename length
      localHeader.writeUInt16LE(0, 28); // Extra length

      localParts.push(localHeader, nameBuf, comp);

      // Central directory header (46 bytes + name)
      const cdHeader = Buffer.alloc(46);
      cdHeader.writeUInt32LE(0x02014b50, 0); // Signature
      cdHeader.writeUInt16LE(20, 4); // Version made by
      cdHeader.writeUInt16LE(20, 6); // Version needed
      cdHeader.writeUInt16LE(0x0800, 8); // UTF-8 flag
      cdHeader.writeUInt16LE(8, 10); // Method: Deflate
      cdHeader.writeUInt16LE(0, 12); // Time
      cdHeader.writeUInt16LE(0, 14); // Date
      cdHeader.writeUInt32LE(entryCrc >>> 0, 16); // CRC32
      cdHeader.writeUInt32LE(comp.length, 20); // Compressed size
      cdHeader.writeUInt32LE(uncomp.length, 24); // Uncompressed size
      cdHeader.writeUInt16LE(nameBuf.length, 28); // Filename length
      cdHeader.writeUInt16LE(0, 30); // Extra length
      cdHeader.writeUInt16LE(0, 32); // Comment length
      cdHeader.writeUInt16LE(0, 34); // Disk number
      cdHeader.writeUInt16LE(0, 36); // Internal attrs
      cdHeader.writeUInt32LE(0, 38); // External attrs
      cdHeader.writeUInt32LE(currentOffset, 42); // Local header offset

      centralParts.push(cdHeader, nameBuf);
      currentOffset += localHeader.length + nameBuf.length + comp.length;
    }

    const cdBuffer = Buffer.concat(centralParts);
    const eocd = Buffer.alloc(22);
    eocd.writeUInt32LE(0x06054b50, 0);
    eocd.writeUInt16LE(0, 4);
    eocd.writeUInt16LE(0, 6);
    eocd.writeUInt16LE(this._entries.length, 8);
    eocd.writeUInt16LE(this._entries.length, 10);
    eocd.writeUInt32LE(cdBuffer.length, 12);
    eocd.writeUInt32LE(currentOffset, 16);
    eocd.writeUInt16LE(0, 20);

    return Buffer.concat([...localParts, cdBuffer, eocd]);
  }
}

let ZipClass = PureZip;
try {
  ZipClass = require('adm-zip');
} catch (_) {
  ZipClass = PureZip;
}

module.exports = {
  AdmZip: ZipClass,
  PureZip,
  crc32
};
