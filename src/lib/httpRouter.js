const http = require('http');
const fs = require('fs');
const path = require('path');

const MIME_TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.js': 'application/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.svg': 'image/svg+xml',
  '.ico': 'image/x-icon'
};

class PureMulterError extends Error {
  constructor(code, message) {
    super(message || code);
    this.code = code;
  }
}

/**
 * Parser nativo de multipart/form-data para buffers binários (arquivos .zip, .rar, .xml, .pdf).
 */
function parseMultipartBuffer(bodyBuffer, boundary, limits = {}) {
  const files = [];
  const fields = {};
  const boundaryBuf = Buffer.from(`--${boundary}`, 'latin1');
  const headerSep = Buffer.from('\r\n\r\n', 'latin1');

  let pos = bodyBuffer.indexOf(boundaryBuf, 0);
  while (pos !== -1) {
    const partStart = pos + boundaryBuf.length;
    if (bodyBuffer[partStart] === 0x2d && bodyBuffer[partStart + 1] === 0x2d) {
      // Fim "--boundary--"
      break;
    }

    let contentStart = partStart;
    if (bodyBuffer[contentStart] === 0x0d && bodyBuffer[contentStart + 1] === 0x0a) {
      contentStart += 2;
    }

    const nextBoundaryPos = bodyBuffer.indexOf(boundaryBuf, contentStart);
    if (nextBoundaryPos === -1) break;

    const headerEndPos = bodyBuffer.indexOf(headerSep, contentStart);
    if (headerEndPos !== -1 && headerEndPos < nextBoundaryPos) {
      const rawHeaders = bodyBuffer.subarray(contentStart, headerEndPos).toString('utf8');
      let dataStart = headerEndPos + 4;
      let dataEnd = nextBoundaryPos;
      if (bodyBuffer[dataEnd - 2] === 0x0d && bodyBuffer[dataEnd - 1] === 0x0a) {
        dataEnd -= 2;
      }

      const fileBuf = Buffer.from(bodyBuffer.subarray(dataStart, dataEnd));
      const nameMatch = rawHeaders.match(/name="([^"]+)"/i);
      const filenameMatch = rawHeaders.match(/filename="([^"]*)"/i);
      const ctMatch = rawHeaders.match(/Content-Type:\s*([^\r\n]+)/i);

      const fieldName = nameMatch ? nameMatch[1] : 'file';
      if (filenameMatch) {
        if (limits.fileSize && fileBuf.length > limits.fileSize) {
          throw new PureMulterError('LIMIT_FILE_SIZE', 'File too large');
        }
        files.push({
          fieldname: fieldName,
          originalname: path.basename(filenameMatch[1]),
          mimetype: ctMatch ? ctMatch[1].trim() : 'application/octet-stream',
          size: fileBuf.length,
          buffer: fileBuf
        });
      } else {
        fields[fieldName] = fileBuf.toString('utf8');
      }
    }

    pos = nextBoundaryPos;
  }

  return { files, fields };
}

function readRawRequestBody(req, maxBytes = 160 * 1024 * 1024) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let total = 0;
    req.on('data', (chunk) => {
      total += chunk.length;
      if (total > maxBytes) {
        reject(new PureMulterError('LIMIT_FILE_SIZE', 'Payload excede o limite permitido.'));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on('end', () => resolve(Buffer.concat(chunks)));
    req.on('error', reject);
  });
}

function createPureMulter(opts = {}) {
  const limits = opts.limits || {};

  function buildMiddleware(mode, fieldName) {
    return async (req, res, next) => {
      try {
        const ct = req.headers['content-type'] || '';
        const bMatch = ct.match(/boundary=(?:"([^"]+)"|([^;]+))/i);
        if (!bMatch) {
          return next();
        }
        const boundary = (bMatch[1] || bMatch[2]).trim();
        const rawBody = await readRawRequestBody(req, (limits.fileSize || 150 * 1024 * 1024) + 1024 * 1024);
        const parsed = parseMultipartBuffer(rawBody, boundary, limits);
        req.body = { ...(req.body || {}), ...parsed.fields };
        if (mode === 'single') {
          req.file = parsed.files.find((f) => f.fieldname === fieldName) || parsed.files[0] || null;
        } else {
          req.files = parsed.files;
        }
        next();
      } catch (err) {
        next(err);
      }
    };
  }

  return {
    single: (field) => buildMiddleware('single', field),
    array: (field) => buildMiddleware('array', field)
  };
}

createPureMulter.memoryStorage = () => ({});
createPureMulter.MulterError = PureMulterError;

/**
 * Roteador HTTP nativo compatível com Express.
 */
function createPureExpress() {
  const middlewares = [];
  const routes = [];
  const errorHandlers = [];

  function matchRoutePattern(pattern, pathname) {
    const pParts = pattern.split('/').filter(Boolean);
    const uParts = pathname.split('/').filter(Boolean);
    if (pParts.length !== uParts.length) return null;
    const params = {};
    for (let i = 0; i < pParts.length; i++) {
      if (pParts[i].startsWith(':')) {
        params[pParts[i].slice(1)] = decodeURIComponent(uParts[i]);
      } else if (pParts[i] !== uParts[i]) {
        return null;
      }
    }
    return params;
  }

  async function handleRequest(req, res) {
    // Estende res com métodos Express
    res.statusCode = 200;
    res.status = (code) => {
      res.statusCode = code;
      return res;
    };
    res.json = (obj) => {
      if (!res.getHeader('Content-Type')) {
        res.setHeader('Content-Type', 'application/json; charset=utf-8');
      }
      res.end(JSON.stringify(obj));
    };
    res.send = (body) => {
      if (Buffer.isBuffer(body)) {
        res.end(body);
      } else if (typeof body === 'object' && body !== null) {
        res.json(body);
      } else {
        if (!res.getHeader('Content-Type')) {
          res.setHeader('Content-Type', 'text/plain; charset=utf-8');
        }
        res.end(String(body || ''));
      }
    };

    const parsedUrl = new URL(req.url || '/', 'http://127.0.0.1');
    req.path = parsedUrl.pathname;
    req.query = Object.fromEntries(parsedUrl.searchParams.entries());
    req.body = req.body || {};
    req.params = {};

    const chain = [];
    for (const mw of middlewares) {
      chain.push(mw);
    }

    for (const r of routes) {
      if (r.method === req.method) {
        const params = matchRoutePattern(r.pattern, req.path);
        if (params) {
          req.params = params;
          chain.push(...r.handlers);
          break;
        }
      }
    }

    let idx = 0;
    const next = async (err) => {
      if (err) {
        for (const eh of errorHandlers) {
          try {
            eh(err, req, res, () => {});
            if (res.writableEnded) return;
          } catch (_) {}
        }
        if (!res.writableEnded) {
          res.status(500).json({ success: false, message: err.message || 'Erro interno' });
        }
        return;
      }

      if (res.writableEnded) return;
      if (idx >= chain.length) {
        if (!res.writableEnded) {
          res.status(404).json({ success: false, message: `Rota não encontrada: ${req.method} ${req.path}` });
        }
        return;
      }

      const fn = chain[idx++];
      try {
        await fn(req, res, next);
      } catch (e) {
        await next(e);
      }
    };

    await next();
  }

  const app = (req, res) => handleRequest(req, res);

  app.use = (fn) => {
    if (fn.length === 4) {
      errorHandlers.push(fn);
    } else {
      middlewares.push(fn);
    }
  };

  ['get', 'post', 'put', 'delete'].forEach((m) => {
    app[m] = (pattern, ...handlers) => {
      routes.push({
        method: m.toUpperCase(),
        pattern,
        handlers
      });
    };
  });

  app.listen = (port, host, cb) => {
    const server = http.createServer(app);
    if (typeof host === 'function') {
      return server.listen(port, host);
    }
    return server.listen(port, host, cb);
  };

  return app;
}

createPureExpress.json = () => async (req, res, next) => {
  const ct = req.headers['content-type'] || '';
  if (ct.includes('application/json')) {
    try {
      const raw = await readRawRequestBody(req, 30 * 1024 * 1024);
      req.body = raw.length > 0 ? JSON.parse(raw.toString('utf8')) : {};
    } catch (err) {
      return res.status(400).json({ success: false, message: 'JSON inválido no corpo da requisição.' });
    }
  }
  next();
};

createPureExpress.static = (rootDir) => (req, res, next) => {
  if (req.method !== 'GET' && req.method !== 'HEAD') return next();
  let relPath = req.path === '/' ? '/index.html' : req.path;
  const safeRel = path.normalize(relPath).replace(/^(\.\.(\/|\\|$))+/, '');
  const fullPath = path.join(rootDir, safeRel);

  if (!fullPath.startsWith(path.resolve(rootDir))) return next();
  if (fs.existsSync(fullPath) && fs.statSync(fullPath).isFile()) {
    const ext = path.extname(fullPath).toLowerCase();
    res.setHeader('Content-Type', MIME_TYPES[ext] || 'application/octet-stream');
    res.end(fs.readFileSync(fullPath));
    return;
  }
  next();
};

let expressModule = createPureExpress;
let multerModule = createPureMulter;

try {
  expressModule = require('express');
} catch (_) {
  expressModule = createPureExpress;
}

try {
  multerModule = require('multer');
} catch (_) {
  multerModule = createPureMulter;
}

module.exports = {
  express: expressModule,
  multer: multerModule
};
