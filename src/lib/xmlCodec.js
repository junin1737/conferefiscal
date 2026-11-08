/**
 * Parser XML puro em JavaScript para documentos fiscais brasileiros (NF-e, NFC-e, CF-e SAT, Eventos e Inutilizações).
 * Compatível com a interface do fast-xml-parser quando node_modules não estiver instalado.
 */

function decodeXmlEntities(str) {
  if (!str || typeof str !== 'string') return str;
  return str
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&apos;/g, "'")
    .replace(/&#(\d+);/g, (_, dec) => String.fromCharCode(Number(dec)))
    .replace(/&#x([0-9a-fA-F]+);/g, (_, hex) => String.fromCharCode(parseInt(hex, 16)))
    .replace(/&amp;/g, '&');
}

function parseAttributes(attrString, prefix = '@_') {
  const attrs = {};
  if (!attrString) return attrs;
  const regex = /([A-Za-z_][A-Za-z0-9_.:-]*)\s*=\s*(?:"([^"]*)"|'([^']*)')/g;
  let match;
  while ((match = regex.exec(attrString)) !== null) {
    const name = match[1];
    const val = match[2] !== undefined ? match[2] : match[3];
    attrs[`${prefix}${name}`] = decodeXmlEntities(val);
  }
  return attrs;
}

class PureXMLParser {
  constructor(options = {}) {
    this.options = {
      ignoreAttributes: false,
      attributeNamePrefix: '@_',
      trimValues: true,
      ...options
    };
  }

  parse(xmlInput) {
    if (!xmlInput || typeof xmlInput !== 'string') {
      throw new Error('Conteúdo XML vazio.');
    }

    // Remove instruções de processamento <?xml ... ?> e comentários <!-- ... -->
    let cleaned = xmlInput
      .replace(/^\uFEFF/, '')
      .replace(/<\?xml[\s\S]*?\?>/gi, '')
      .replace(/<!--[\s\S]*?-->/g, '')
      .trim();

    if (!cleaned.startsWith('<')) {
      throw new Error('Documento XML inválido: não inicia com tag.');
    }

    const root = {};
    const stack = [{ tagName: '__root__', obj: root, text: '' }];

    // Tokenizador de tags e texto
    const tagRegex = /<\/?([A-Za-z_][A-Za-z0-9_.:-]*)([^>]*?)\/?>|([^<]+)/g;
    let match;

    while ((match = tagRegex.exec(cleaned)) !== null) {
      const fullToken = match[0];
      const tagNameRaw = match[1];
      const attrPart = match[2] || '';
      const textPart = match[3];

      if (textPart !== undefined) {
        const top = stack[stack.length - 1];
        if (top) {
          top.text += textPart;
        }
        continue;
      }

      // Remove prefixo de namespace se houver (ex: nfe:NFe -> NFe)
      const tagName = tagNameRaw.includes(':') ? tagNameRaw.split(':').pop() : tagNameRaw;
      const isClosing = fullToken.startsWith('</');
      const isSelfClosing = fullToken.endsWith('/>');

      if (isClosing) {
        if (stack.length <= 1) {
          throw new Error(`Tag de fechamento inesperada: </${tagName}>`);
        }
        const popped = stack.pop();
        if (popped.tagName !== tagName) {
          throw new Error(
            `Estrutura XML malformada: esperado </${popped.tagName}>, mas encontrado </${tagName}>.`
          );
        }

        const parent = stack[stack.length - 1];
        const hasChildKeys = Object.keys(popped.obj).length > 0;
        const textVal = this.options.trimValues ? popped.text.trim() : popped.text;
        const decodedText = decodeXmlEntities(textVal);

        let finalValue;
        if (hasChildKeys) {
          if (decodedText) {
            popped.obj['#text'] = decodedText;
          }
          finalValue = popped.obj;
        } else {
          finalValue = decodedText;
        }

        if (parent.obj[tagName] !== undefined) {
          if (!Array.isArray(parent.obj[tagName])) {
            parent.obj[tagName] = [parent.obj[tagName]];
          }
          parent.obj[tagName].push(finalValue);
        } else {
          parent.obj[tagName] = finalValue;
        }
      } else if (isSelfClosing) {
        const parent = stack[stack.length - 1];
        const attrs = this.options.ignoreAttributes
          ? {}
          : parseAttributes(attrPart, this.options.attributeNamePrefix);
        const finalValue = Object.keys(attrs).length > 0 ? attrs : '';

        if (parent.obj[tagName] !== undefined) {
          if (!Array.isArray(parent.obj[tagName])) {
            parent.obj[tagName] = [parent.obj[tagName]];
          }
          parent.obj[tagName].push(finalValue);
        } else {
          parent.obj[tagName] = finalValue;
        }
      } else {
        // Tag de abertura
        const attrs = this.options.ignoreAttributes
          ? {}
          : parseAttributes(attrPart, this.options.attributeNamePrefix);
        stack.push({
          tagName,
          obj: { ...attrs },
          text: ''
        });
      }
    }

    if (stack.length !== 1) {
      const unclosed = stack[stack.length - 1].tagName;
      throw new Error(`Estrutura XML incompleta: tag <${unclosed}> não foi fechada.`);
    }

    return root;
  }
}

let XMLParserClass = PureXMLParser;
try {
  const fxp = require('fast-xml-parser');
  if (fxp && fxp.XMLParser) {
    XMLParserClass = fxp.XMLParser;
  }
} catch (_) {
  XMLParserClass = PureXMLParser;
}

module.exports = {
  XMLParser: XMLParserClass,
  PureXMLParser
};
