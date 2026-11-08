const { PureZip } = require('./zipCodec');

function escapeXml(str) {
  if (str === null || str === undefined) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&apos;');
}

function colIndexToLetter(idx0) {
  let n = idx0 + 1;
  let str = '';
  while (n > 0) {
    const rem = (n - 1) % 26;
    str = String.fromCharCode(65 + rem) + str;
    n = Math.floor((n - 1) / 26);
  }
  return str;
}

class PureWorksheet {
  constructor(name) {
    this.name = String(name || 'Planilha').slice(0, 31);
    this._columns = [];
    this._rows = [];
  }

  set columns(cols) {
    this._columns = Array.isArray(cols) ? cols : [];
    if (this._columns.length > 0 && this._rows.length === 0) {
      this.addRow(this._columns.map((c) => c.header || c.key || ''));
    }
  }

  get columns() {
    return this._columns;
  }

  addRow(values) {
    let arr = [];
    if (Array.isArray(values)) {
      arr = values;
    } else if (values && typeof values === 'object') {
      arr = this._columns.map((c) => values[c.key]);
    }
    const rowObj = {
      values: arr,
      font: null,
      fill: null,
      getCell: () => ({ numFmt: null, value: null })
    };
    this._rows.push(rowObj);
    return rowObj;
  }

  getRow(oneBasedIdx) {
    return (
      this._rows[oneBasedIdx - 1] || {
        font: null,
        fill: null,
        getCell: () => ({ numFmt: null, value: null })
      }
    );
  }

  toXml() {
    const rowsXml = this._rows
      .map((r, rIdx) => {
        const rowNum = rIdx + 1;
        const cellsXml = (r.values || [])
          .map((val, cIdx) => {
            const ref = `${colIndexToLetter(cIdx)}${rowNum}`;
            if (val === null || val === undefined || val === '') {
              return `<c r="${ref}" t="inlineStr"><is><t></t></is></c>`;
            }
            if (typeof val === 'number' && Number.isFinite(val)) {
              return `<c r="${ref}"><v>${val}</v></c>`;
            }
            return `<c r="${ref}" t="inlineStr"><is><t>${escapeXml(val)}</t></is></c>`;
          })
          .join('');
        return `<row r="${rowNum}">${cellsXml}</row>`;
      })
      .join('');

    return `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData>${rowsXml}</sheetData>
</worksheet>`;
  }
}

class PureWorkbook {
  constructor() {
    this.creator = 'ConfereFiscal';
    this.created = new Date();
    this.worksheets = [];
    this.xlsx = {
      writeBuffer: async () => this._buildBuffer()
    };
  }

  addWorksheet(name) {
    const ws = new PureWorksheet(name);
    this.worksheets.push(ws);
    return ws;
  }

  _buildBuffer() {
    const zip = new PureZip();

    const sheetOverrides = this.worksheets
      .map(
        (_, i) =>
          `<Override PartName="/xl/worksheets/sheet${
            i + 1
          }.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>`
      )
      .join('');

    zip.addFile(
      '[Content_Types].xml',
      `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  ${sheetOverrides}
</Types>`
    );

    zip.addFile(
      '_rels/.rels',
      `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>`
    );

    const sheetsXml = this.worksheets
      .map(
        (ws, i) =>
          `<sheet name="${escapeXml(ws.name)}" sheetId="${i + 1}" r:id="rId${i + 1}"/>`
      )
      .join('');

    zip.addFile(
      'xl/workbook.xml',
      `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>${sheetsXml}</sheets>
</workbook>`
    );

    const wbRelsXml = this.worksheets
      .map(
        (_, i) =>
          `<Relationship Id="rId${
            i + 1
          }" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet${
            i + 1
          }.xml"/>`
      )
      .join('');

    zip.addFile(
      'xl/_rels/workbook.xml.rels',
      `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  ${wbRelsXml}
</Relationships>`
    );

    this.worksheets.forEach((ws, i) => {
      zip.addFile(`xl/worksheets/sheet${i + 1}.xml`, ws.toXml());
    });

    return zip.toBuffer();
  }
}

let ExcelJSModule = { Workbook: PureWorkbook };
try {
  ExcelJSModule = require('exceljs');
} catch (_) {
  ExcelJSModule = { Workbook: PureWorkbook };
}

module.exports = {
  ExcelJS: ExcelJSModule,
  PureWorkbook
};
