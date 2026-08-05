#!/usr/bin/env node
/**
 * Decrypt getcaptcha octet-stream response: hsw(0, bytes) -> msgpack -> JSON.
 * stdin: { respPath, hswPath }
 */
'use strict';
const fs = require('fs');
const path = require('path');
const nc = require('crypto');
const ROOT = __dirname;

const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const hswPath = input.hswPath || path.join(ROOT, '_hsw_protocol_live.js');
const hswCode = fs.readFileSync(hswPath, 'utf8');
const respPath = input.respPath;
const respBytes = fs.readFileSync(respPath);

// Process scrub (same as pack)
(function scrubProcessLeak() {
  const fakeProcess = {
    browser: true,
    version: '',
    versions: {},
    platform: 'win32',
    arch: 'x64',
    title: 'chrome',
    execPath: '',
    argv0: 'chrome',
    argv: ['chrome'],
    pid: 1,
    ppid: 0,
    env: {},
    cwd: () => 'C:\\',
    nextTick: (fn, ...a) => queueMicrotask(() => fn(...a)),
    binding: () => { throw new Error('process.binding is not supported'); },
  };
  try {
    const RF = Function;
    const wrapped = function (...args) {
      const body = String(args[args.length - 1] || '');
      if (/return\s+process/.test(body)) {
        return function () { return fakeProcess; };
      }
      return RF(...args);
    };
    wrapped.prototype = RF.prototype;
    Object.setPrototypeOf(wrapped, RF);
    globalThis.__SafeFunction = wrapped;
  } catch (_) {}
})();

const HOST = {};
const GATE_MAP = {};
const FORCED = {};

function wrapImports(imports) {
  if (!imports || !imports.a) return imports;
  const a = imports.a;
  for (const name of Object.keys(a)) {
    const fn = a[name];
    if (typeof fn !== 'function') continue;
    let src = '';
    try { src = Function.prototype.toString.call(fn); } catch (_) {}
    const m = src.match(/instanceof\s+([A-Za-z0-9_$.]+)/);
    if (m) GATE_MAP[name] = m[1];
  }
  const wrapped = {};
  for (const name of Object.keys(a)) {
    const fn = a[name];
    if (typeof fn !== 'function') { wrapped[name] = fn; continue; }
    const gateType = GATE_MAP[name] || null;
    wrapped[name] = function () {
      HOST[name] = (HOST[name] || 0) + 1;
      if (gateType === 'Window') {
        FORCED.Window = (FORCED.Window || 0) + 1;
        if (FORCED.Window <= 20000) return 1;
      }
      return fn.apply(this, arguments);
    };
  }
  return Object.assign({}, imports, { a: wrapped });
}

const origInst = WebAssembly.instantiate.bind(WebAssembly);
WebAssembly.instantiate = async function (src, imports) {
  return origInst(src, wrapImports(imports));
};

(async () => {
  let happy;
  try {
    happy = require(path.join(ROOT, 'ba_fp_helpers', 'node_modules', 'happy-dom'));
  } catch (_) {
    happy = require('C:/Users/Administrator/Desktop/GPT_PLUS_PP纯协议版/webui/frontend/node_modules/happy-dom');
  }
  const { Browser } = happy;
  const browser = new Browser({
    settings: {
      enableJavaScriptEvaluation: true,
      suppressInsecureJavaScriptEnvironmentWarning: true,
      suppressCodeGenerationFromStringsWarning: true,
      disableCSSFileLoading: true,
      timer: { maxTimeout: 120000, maxIntervalIterations: 1e7 },
      fetch: { disableSameOriginPolicy: true, disableStrictSSL: true },
      navigator: { userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36' },
      device: { prefersColorScheme: 'light', mediaType: 'screen' },
    },
  });
  const page = browser.newPage();
  try { await page.goto('https://newassets.hcaptcha.com/', { timeout: 8000 }); } catch (_) {}
  const w = page.mainFrame.window;

  for (const k of ['process', 'Buffer', 'require', 'module', 'exports', 'global', '__dirname', '__filename', 'setImmediate', 'clearImmediate']) {
    try { delete w[k]; } catch (_) {}
    try {
      Object.defineProperty(w, k, {
        configurable: true,
        get() { return undefined; },
        set() {},
      });
    } catch (_) {}
  }

  const Win = w.constructor;
  try { Object.defineProperty(w, 'Window', { configurable: true, writable: true, value: Win }); } catch (_) {}

  w.WebAssembly = globalThis.WebAssembly;
  w.BigInt = globalThis.BigInt;
  if (globalThis.Atomics) w.Atomics = globalThis.Atomics;
  if (globalThis.SharedArrayBuffer) w.SharedArrayBuffer = globalThis.SharedArrayBuffer;
  w.crypto = {
    subtle: nc.webcrypto.subtle,
    getRandomValues: (a) => { a.set(nc.randomBytes(a.length)); return a; },
  };
  try {
    if (globalThis.__SafeFunction) {
      w.Function = globalThis.__SafeFunction;
      if (w.crypto && w.crypto.constructor) w.crypto.constructor.constructor = globalThis.__SafeFunction;
    }
  } catch (_) {}

  // msgpack
  const mp = require(path.join(ROOT, 'node_modules', '@msgpack', 'msgpack'));
  const decode = mp.decode.bind(mp);

  // load hsw
  const s = w.document.createElement('script');
  s.textContent = hswCode;
  w.document.body.appendChild(s);
  await new Promise((r) => setTimeout(r, 50));
  if (typeof w.hsw !== 'function') throw new Error('no hsw after load');

  // try raw decrypt first: hsw(0, respBytes)
  let plain = null;
  let mode = 'raw';
  try {
    const out = await Promise.race([
      Promise.resolve(w.hsw(0, new Uint8Array(respBytes))),
      new Promise((_, rej) => setTimeout(() => rej(new Error('hsw decrypt timeout')), 60000)),
    ]);
    if (out != null) plain = out;
  } catch (e) {
    throw new Error('hsw(0) fail: ' + e.message);
  }
  if (plain == null) throw new Error('hsw(0) returned null');

  const u8 = plain instanceof Uint8Array ? plain : new Uint8Array(plain);
  let j = null;
  try {
    j = decode(u8);
  } catch (e) {
    // maybe double-wrapped: msgpack [Ext18] -> hsw(0, ext.data)
    try {
      const arr = decode(u8);
      const first = arr && arr[0];
      if (first && typeof first === 'object' && Number(first.type) === 18) {
        const inner = new Uint8Array(first.data);
        const plain2 = await w.hsw(0, inner);
        j = decode(plain2 instanceof Uint8Array ? plain2 : new Uint8Array(plain2));
        mode = 'ext18';
      } else {
        throw new Error('decoded non-ext18: ' + JSON.stringify(arr).slice(0, 120));
      }
    } catch (e2) {
      throw new Error('decode fail: ' + e2.message);
    }
  }

  const tok = String(j.generated_pass_UUID || j.token || '').trim();
  process.stdout.write(JSON.stringify({
    ok: !!tok,
    token: tok,
    pass: j.pass,
    expiration: j.expiration,
    success: j.success,
    error_codes: j['error-codes'] || j.error_codes || null,
    keys: Object.keys(j).slice(0, 20),
    mode: mode,
    plain_len: u8.length,
    resp_len: respBytes.length,
  }));
  clearInterval(undefined);
  await browser.close().catch(() => {});
})().catch((e) => {
  process.stdout.write(JSON.stringify({ ok: false, error: String(e && (e.stack || e)) }));
  process.exit(1);
});
