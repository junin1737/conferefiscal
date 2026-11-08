const zlib = require('zlib');
const EventEmitter = require('events');

/**
 * Decodifica uma string literal PDF (...) tratando sequências de escape e octal e convertendo WinAnsi (Latin1) para UTF-8.
 */
function decodePdfLiteralString(raw) {
  const bytes = [];
  for (let i = 0; i < raw.length; i++) {
    const ch = raw[i];
    if (ch === '\\') {
      i++;
      if (i >= raw.length) break;
      const next = raw[i];
      if (next === 'n') bytes.push(10);
      else if (next === 'r') bytes.push(13);
      else if (next === 't') bytes.push(9);
      else if (next === 'b') bytes.push(8);
      else if (next === 'f') bytes.push(12);
      else if (next === '(') bytes.push(40);
      else if (next === ')') bytes.push(41);
      else if (next === '\\') bytes.push(92);
      else if (next >= '0' && next <= '7') {
        let oct = next;
        if (i + 1 < raw.length && raw[i + 1] >= '0' && raw[i + 1] <= '7') oct += raw[++i];
        if (i + 1 < raw.length && raw[i + 1] >= '0' && raw[i + 1] <= '7') oct += raw[++i];
        bytes.push(parseInt(oct, 8));
      } else {
        bytes.push(next.charCodeAt(0));
      }
    } else {
      bytes.push(ch.charCodeAt(0) & 0xff);
    }
  }
  return Buffer.from(bytes).toString('latin1');
}

/**
 * Extrai texto de um stream de conteúdo PDF descomprimido.
 */
function extractTextFromPdfContentStream(contentLatin1) {
  const lines = [];
  let currentLine = '';

  // Percorre blocos BT ... ET ou o stream inteiro
  const btBlocks = contentLatin1.match(/BT[\s\S]*?ET/g) || [contentLatin1];

  for (const block of btBlocks) {
    const tokenRegex = /\((?:\\.|[^\\()])*\)\s*(?:Tj|')|\[((?:\\.|[^\]])*)\]\s*TJ|<([0-9A-Fa-f\s]+)>\s*Tj|(T\*|Td|TD|Tm)/g;
    let m;
    while ((m = tokenRegex.exec(block)) !== null) {
      const full = m[0];
      if (m[3]) {
        // Operador de posicionamento / quebra de linha
        if (currentLine.trim()) {
          lines.push(currentLine.trim());
          currentLine = '';
        }
      } else if (m[1] !== undefined) {
        // Array TJ: [(texto) -20 (outro)] TJ
        const arrContent = m[1];
        const subLiterals = arrContent.match(/\((?:\\.|[^\\()])*\)/g) || [];
        for (const lit of subLiterals) {
          currentLine += decodePdfLiteralString(lit.slice(1, -1));
        }
      } else if (m[2] !== undefined) {
        // Hex string <...> Tj
        const hexClean = m[2].replace(/\s+/g, '');
        const buf = Buffer.from(hexClean, 'hex');
        currentLine += buf.toString('latin1');
      } else {
        // Literal (...) Tj
        const openIdx = full.indexOf('(');
        const closeIdx = full.lastIndexOf(')');
        if (openIdx !== -1 && closeIdx > openIdx) {
          currentLine += decodePdfLiteralString(full.slice(openIdx + 1, closeIdx));
        }
      }
    }
    if (currentLine.trim()) {
      lines.push(currentLine.trim());
      currentLine = '';
    }
  }

  return lines.join('\n');
}

/**
 * Extrator nativo em Node.js de texto e metadados de arquivos PDF (suporta streams /FlateDecode e texto direto).
 */
async function purePdfParse(buffer) {
  if (!Buffer.isBuffer(buffer)) {
    throw new Error('Buffer PDF inválido.');
  }
  const head = buffer.subarray(0, Math.min(buffer.length, 1024)).toString('latin1');
  if (!head.includes('%PDF-')) {
    throw new Error('Cabeçalho %PDF- não encontrado.');
  }

  const rawLatin1 = buffer.toString('latin1');
  const pageMatches = rawLatin1.match(/\/Type\s*\/Page\b(?!s)/g);
  const numpages = pageMatches && pageMatches.length > 0 ? pageMatches.length : 1;

  const extractedChunks = [];

  // Localiza todos os blocos stream ... endstream
  let searchPos = 0;
  while (searchPos < buffer.length) {
    const streamMarkerIdx = rawLatin1.indexOf('stream', searchPos);
    if (streamMarkerIdx === -1) break;

    // Certifica-se de que não é "endstream"
    if (streamMarkerIdx >= 3 && rawLatin1.slice(streamMarkerIdx - 3, streamMarkerIdx) === 'end') {
      searchPos = streamMarkerIdx + 6;
      continue;
    }

    let dataStart = streamMarkerIdx + 6;
    if (buffer[dataStart] === 0x0d && buffer[dataStart + 1] === 0x0a) dataStart += 2;
    else if (buffer[dataStart] === 0x0a || buffer[dataStart] === 0x0d) dataStart += 1;
    else {
      searchPos = streamMarkerIdx + 6;
      continue;
    }

    const endStreamIdx = rawLatin1.indexOf('endstream', dataStart);
    if (endStreamIdx === -1) break;

    let dataEnd = endStreamIdx;
    if (buffer[dataEnd - 2] === 0x0d && buffer[dataEnd - 1] === 0x0a) dataEnd -= 2;
    else if (buffer[dataEnd - 1] === 0x0a || buffer[dataEnd - 1] === 0x0d) dataEnd -= 1;

    const dictSnippet = rawLatin1.slice(Math.max(0, streamMarkerIdx - 250), streamMarkerIdx);
    const streamBuf = buffer.subarray(dataStart, dataEnd);

    let decodedContent = null;
    if (/\/FlateDecode\b/.test(dictSnippet)) {
      try {
        decodedContent = zlib.inflateSync(streamBuf).toString('latin1');
      } catch (_) {
        try {
          decodedContent = zlib.inflateRawSync(streamBuf).toString('latin1');
        } catch (__) {}
      }
    } else {
      decodedContent = streamBuf.toString('latin1');
    }

    if (decodedContent && (decodedContent.includes('Tj') || decodedContent.includes('TJ'))) {
      const txt = extractTextFromPdfContentStream(decodedContent);
      if (txt.trim()) extractedChunks.push(txt);
    }

    searchPos = endStreamIdx + 9;
  }

  return {
    numpages,
    text: extractedChunks.join('\n')
  };
}

/**
 * Converte cor hexadecimal (#0F172A) para componentes RGB de 0.0 a 1.0 do PDF.
 */
function hexToPdfRgb(hex) {
  const clean = String(hex || '#000000').replace('#', '');
  if (clean.length !== 6) return '0 0 0';
  const r = (parseInt(clean.slice(0, 2), 16) / 255).toFixed(3);
  const g = (parseInt(clean.slice(2, 4), 16) / 255).toFixed(3);
  const b = (parseInt(clean.slice(4, 6), 16) / 255).toFixed(3);
  return `${r} ${g} ${b}`;
}

/**
 * Codifica string UTF-8 em literal PDF WinAnsi (Latin1) escapando (, ) e \.
 */
function encodePdfLiteral(text) {
  const latinBuf = Buffer.from(String(text || ''), 'latin1');
  let out = '(';
  for (let i = 0; i < latinBuf.length; i++) {
    const b = latinBuf[i];
    if (b === 40) out += '\\(';
    else if (b === 41) out += '\\)';
    else if (b === 92) out += '\\\\';
    else if (b < 32 || b > 126) {
      out += '\\' + b.toString(8).padStart(3, '0');
    } else {
      out += String.fromCharCode(b);
    }
  }
  out += ')';
  return out;
}

/**
 * Gerador PDF 1.4 nativo em Node.js compatível com a interface essencial do PDFKit.
 */
class PurePDFDocument extends EventEmitter {
  constructor(options = {}) {
    super();
    this.options = options;
    this.pages = [];
    this.currentFont = 'F1';
    this.currentFontSize = 10;
    this.fillRgb = '0 0 0';
    this.strokeRgb = '0 0 0';
    this._pendingRect = null;
    this._pendingLine = null;
    this.addPage(options);
  }

  addPage(opts = {}) {
    const isLandscape = (opts.layout || this.options.layout) === 'landscape';
    const width = isLandscape ? 841.89 : 595.28;
    const height = isLandscape ? 595.28 : 841.89;
    const margin = opts.margin || this.options.margin || 36;

    this.currentPage = {
      width,
      height,
      margin,
      ops: []
    };
    this.pages.push(this.currentPage);
    this.x = margin;
    this.y = margin;
    return this;
  }

  font(fontName) {
    const map = {
      Helvetica: 'F1',
      'Helvetica-Bold': 'F2',
      Courier: 'F3',
      'Courier-Bold': 'F4'
    };
    this.currentFont = map[fontName] || 'F1';
    return this;
  }

  fontSize(sz) {
    this.currentFontSize = Number(sz) || 10;
    return this;
  }

  fillColor(hex) {
    this.fillRgb = hexToPdfRgb(hex);
    return this;
  }

  strokeColor(hex) {
    this.strokeRgb = hexToPdfRgb(hex);
    return this;
  }

  rect(x, y, w, h) {
    const pdfY = this.currentPage.height - y - h;
    this._pendingRect = { x, pdfY, w, h };
    return this;
  }

  fill(hex) {
    if (hex) this.fillColor(hex);
    if (this._pendingRect) {
      const { x, pdfY, w, h } = this._pendingRect;
      this.currentPage.ops.push(`q ${this.fillRgb} rg ${x.toFixed(2)} ${pdfY.toFixed(2)} ${w.toFixed(2)} ${h.toFixed(2)} re f Q`);
      this._pendingRect = null;
    }
    return this;
  }

  stroke(hex) {
    if (hex) this.strokeColor(hex);
    if (this._pendingRect) {
      const { x, pdfY, w, h } = this._pendingRect;
      this.currentPage.ops.push(`q ${this.strokeRgb} RG ${x.toFixed(2)} ${pdfY.toFixed(2)} ${w.toFixed(2)} ${h.toFixed(2)} re S Q`);
      this._pendingRect = null;
    } else if (this._pendingLine) {
      const { x1, y1, x2, y2 } = this._pendingLine;
      const py1 = this.currentPage.height - y1;
      const py2 = this.currentPage.height - y2;
      this.currentPage.ops.push(`q ${this.strokeRgb} RG ${x1.toFixed(2)} ${py1.toFixed(2)} m ${x2.toFixed(2)} ${py2.toFixed(2)} l S Q`);
      this._pendingLine = null;
    }
    return this;
  }

  moveTo(x, y) {
    this._pendingLine = { x1: x, y1: y, x2: x, y2: y };
    return this;
  }

  lineTo(x, y) {
    if (this._pendingLine) {
      this._pendingLine.x2 = x;
      this._pendingLine.y2 = y;
    }
    return this;
  }

  moveDown(lines = 1) {
    this.y += this.currentFontSize * 1.35 * Number(lines);
    return this;
  }

  text(str, xArg, yArg, opts = {}) {
    let drawX = this.x;
    let drawY = this.y;
    let options = opts;

    if (typeof xArg === 'number') {
      drawX = xArg;
      if (typeof yArg === 'number') {
        drawY = yArg;
      }
    } else if (typeof xArg === 'object' && xArg !== null) {
      options = xArg;
    }

    const pdfY = this.currentPage.height - drawY - this.currentFontSize;
    const literal = encodePdfLiteral(str);
    this.currentPage.ops.push(
      `BT /${this.currentFont} ${this.currentFontSize} Tf ${this.fillRgb} rg 1 0 0 1 ${drawX.toFixed(
        2
      )} ${pdfY.toFixed(2)} Tm ${literal} Tj ET`
    );

    if (typeof xArg !== 'number' || options.lineBreak !== false) {
      this.y = drawY + this.currentFontSize * 1.35;
    }
    return this;
  }

  end() {
    setImmediate(() => {
      try {
        const buf = this._compilePdfBuffer();
        this.emit('data', buf);
        this.emit('end');
      } catch (err) {
        this.emit('error', err);
      }
    });
  }

  _compilePdfBuffer() {
    const objects = [];
    const addObj = (body) => {
      objects.push(body);
      return objects.length;
    };

    // Obj 1: Catalog, Obj 2: Pages, Obj 3..6: Fontes padrão PDF Type1
    const catalogId = addObj('');
    const pagesId = addObj('');
    const f1Id = addObj('<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>');
    const f2Id = addObj('<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>');
    const f3Id = addObj('<< /Type /Font /Subtype /Type1 /BaseFont /Courier /Encoding /WinAnsiEncoding >>');
    const f4Id = addObj('<< /Type /Font /Subtype /Type1 /BaseFont /Courier-Bold /Encoding /WinAnsiEncoding >>');

    const pageIds = [];
    for (const p of this.pages) {
      const contentStr = p.ops.join('\n');
      const compressed = zlib.deflateSync(Buffer.from(contentStr, 'latin1'));
      const contentObjBody = Buffer.concat([
        Buffer.from(`<< /Length ${compressed.length} /Filter /FlateDecode >>\nstream\r\n`, 'latin1'),
        compressed,
        Buffer.from('\r\nendstream', 'latin1')
      ]);
      const contentId = addObj(contentObjBody);

      const pageId = addObj(
        `<< /Type /Page /Parent ${pagesId} 0 R /MediaBox [0 0 ${p.width.toFixed(2)} ${p.height.toFixed(
          2
        )}] /Resources << /Font << /F1 ${f1Id} 0 R /F2 ${f2Id} 0 R /F3 ${f3Id} 0 R /F4 ${f4Id} 0 R >> >> /Contents ${contentId} 0 R >>`
      );
      pageIds.push(pageId);
    }

    objects[catalogId - 1] = `<< /Type /Catalog /Pages ${pagesId} 0 R >>`;
    objects[pagesId - 1] = `<< /Type /Pages /Kids [${pageIds.map((id) => `${id} 0 R`).join(' ')}] /Count ${
      pageIds.length
    } >>`;

    const chunks = [Buffer.from('%PDF-1.4\n%\xE2\xE3\xCF\xD3\n', 'latin1')];
    const offsets = [0];
    let currentOffset = chunks[0].length;

    objects.forEach((obj, idx) => {
      const objNum = idx + 1;
      offsets.push(currentOffset);
      const headerBuf = Buffer.from(`${objNum} 0 obj\n`, 'latin1');
      const bodyBuf = Buffer.isBuffer(obj) ? obj : Buffer.from(String(obj), 'latin1');
      const footerBuf = Buffer.from('\nendobj\n', 'latin1');
      chunks.push(headerBuf, bodyBuf, footerBuf);
      currentOffset += headerBuf.length + bodyBuf.length + footerBuf.length;
    });

    const xrefOffset = currentOffset;
    let xref = `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
    for (let i = 1; i <= objects.length; i++) {
      xref += `${String(offsets[i]).padStart(10, '0')} 00000 n \n`;
    }
    xref += `trailer\n<< /Size ${objects.length + 1} /Root ${catalogId} 0 R >>\nstartxref\n${xrefOffset}\n%%EOF\n`;
    chunks.push(Buffer.from(xref, 'latin1'));

    return Buffer.concat(chunks);
  }
}

let pdfParseFn = purePdfParse;
try {
  pdfParseFn = require('pdf-parse');
} catch (_) {
  pdfParseFn = purePdfParse;
}

let PDFDocumentClass = PurePDFDocument;
try {
  PDFDocumentClass = require('pdfkit');
} catch (_) {
  PDFDocumentClass = PurePDFDocument;
}

module.exports = {
  pdfParse: pdfParseFn,
  purePdfParse,
  PDFDocument: PDFDocumentClass,
  PurePDFDocument
};
