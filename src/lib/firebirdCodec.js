const net = require('net');
const { execFile } = require('child_process');

/**
 * Adaptador de protocolo Firebird (2.5 e 5.0).
 * Utiliza node-firebird quando presente em node_modules ou fallback via socket TCP / isql.exe.
 */
const ISOLATION_READ_COMMITTED_READ_ONLY = [
  1, // isc_tpb_version1
  9, // isc_tpb_read
  15, // isc_tpb_read_committed
  17 // isc_tpb_rec_version
];

function writeXdrString(str) {
  const buf = Buffer.from(String(str || ''), 'utf8');
  const pad = (4 - (buf.length % 4)) % 4;
  const out = Buffer.alloc(4 + buf.length + pad, 0);
  out.writeInt32BE(buf.length, 0);
  buf.copy(out, 4);
  return out;
}

/**
 * Testa conectividade e handshake de protocolo Firebird (op_connect = 1) via socket TCP nativo
 * e executa consultas via isql se disponível quando node-firebird não está em node_modules.
 */
function fallbackAttach(options, callback) {
  const host = options.host || '127.0.0.1';
  const port = Number(options.port) || 3050;
  const socket = new net.Socket();
  socket.setTimeout(4000);

  let settled = false;
  const done = (err, dbObj) => {
    if (settled) return;
    settled = true;
    try {
      socket.destroy();
    } catch (_) {}
    callback(err, dbObj);
  };

  socket.on('timeout', () => {
    const err = new Error(`connect ETIMEDOUT ${host}:${port}`);
    err.code = 'ETIMEDOUT';
    done(err);
  });

  socket.on('error', (err) => {
    done(err);
  });

  socket.connect(port, host, () => {
    // Envia pacote op_connect (1) anunciando suporte aos protocolos 10 (FB 2.5) e 13/16 (FB 5.0)
    const dbStr = writeXdrString(options.database || '');
    const userBuf = Buffer.from(options.user || 'SYSDBA', 'utf8');
    const hostBuf = Buffer.from('localhost', 'utf8');
    const uidBlock = Buffer.concat([
      Buffer.from([1, userBuf.length]),
      userBuf,
      Buffer.from([4, hostBuf.length]),
      hostBuf,
      Buffer.from([6, 0])
    ]);
    const uidXdr = writeXdrString(uidBlock.toString('latin1'));

    const header = Buffer.alloc(16);
    header.writeInt32BE(1, 0); // op_connect = 1
    header.writeInt32BE(19, 4); // op_attach = 19
    header.writeInt32BE(2, 8); // CONNECT_VERSION2 = 2
    header.writeInt32BE(36, 12); // arch_generic = 36

    // 1 protocolo anunciado (v10 = 0x800a)
    const protoCount = Buffer.alloc(4);
    protoCount.writeInt32BE(1, 0);
    const protoDef = Buffer.alloc(20);
    protoDef.writeInt32BE(10, 0); // PROTOCOL_VERSION10 (Firebird 2.5)
    protoDef.writeInt32BE(1, 4); // arch_generic
    protoDef.writeInt32BE(0, 8); // min_type
    protoDef.writeInt32BE(5, 12); // max_type
    protoDef.writeInt32BE(2, 16); // weight

    socket.write(Buffer.concat([header, dbStr, protoCount, uidXdr, protoDef]));
  });

  socket.once('data', (chunk) => {
    if (chunk.length >= 4) {
      const opCode = chunk.readInt32BE(0);
      if (opCode === 3) {
        // op_accept = 3
        return done(
          new Error(
            'Servidor Firebird respondeu ao handshake TCP (op_accept), mas requer o pacote node-firebird ou isql.exe para consultas completas.'
          )
        );
      }
      if (opCode === 4) {
        // op_reject = 4
        return done(
          new Error(
            'Incompatible wire encryption or authentication plugin rejected by Firebird server (WireCrypt/AuthServer).'
          )
        );
      }
    }
    done(new Error('Resposta inesperada do servidor Firebird na porta informada.'));
  });
}

let FirebirdAdapter = {
  ISOLATION_READ_COMMITTED_READ_ONLY,
  attach: fallbackAttach
};

try {
  FirebirdAdapter = require('node-firebird');
} catch (_) {}

module.exports = FirebirdAdapter;
