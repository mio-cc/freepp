#!/usr/bin/env node
'use strict';
/* Always-hook wasm trace (jsdom): hook WebAssembly.instantiate, wrap imports, run hsw(req), log all calls. */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');
const { createCanvas } = require('@napi-rs/canvas');
const nodeCrypto = require('crypto');
const polyfill = require('./sandbox_polyfill');
const ROOT = path.join(__dirname, '..');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const req = String(input.req || '').trim();
const hswCode = fs.readFileSync(input.hswPath || path.join(ROOT, '_hsw_semi.js'), 'utf8');
const UA0 = input.userAgent || 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36';
const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>', {
  url: 'https://newassets.hcaptcha.com/', pretendToBeVisual: true, runScripts: 'outside-only',
});
const win = dom.window;
win.HTMLCanvasElement.prototype.getContext = function (type) {
  const cv = createCanvas(this.width || 300, this.height || 150);
  if (type === '2d' || !type) return cv.getContext('2d');
  return cv.getContext(type);
};
win.HTMLCanvasElement.prototype.toDataURL = function (type, quality) {
  const cv = createCanvas(this.width || 300, this.height || 150);
  try { return cv.toDataURL(type, quality); } catch (e) { return ''; }
};
win.HTMLCanvasElement.prototype.toBlob = function (cb, type, quality) {
  const cv = createCanvas(this.width || 300, this.height || 150);
  try { const buf = cv.toBuffer(type === 'image/jpeg' ? 'image/jpeg' : 'image/png', quality); cb(new win.Blob([buf], { type: type || 'image/png' })); } catch (e) { cb(null); }
};
win.HTMLCanvasElement.prototype.getImageData = function (sx, sy, sw, sh) {
  const cv = createCanvas(this.width || 300, this.height || 150);
  try { return cv.getContext('2d').getImageData(sx, sy, sw, sh); } catch (e) { return null; }
};
win.WebAssembly = WebAssembly;
const { TextDecoder: NodeTextDecoder, TextEncoder: NodeTextEncoder } = require('util');
try { win.TextDecoder = NodeTextDecoder; win.TextEncoder = NodeTextEncoder; } catch (e) {}
win.globalThis = win; win.self = win; win.global = win;
try { Object.defineProperty(win, 'crypto', { value: nodeCrypto.webcrypto, writable: true, configurable: true }); } catch (_) {}
win.atob = (s) => Buffer.from(s, 'base64').toString('binary');
win.btoa = (s) => Buffer.from(s, 'binary').toString('base64');
Object.defineProperty(win.navigator, 'userAgent', { get: () => UA0, configurable: true });
Object.defineProperty(win.navigator, 'platform', { get: () => 'Win32', configurable: true });
Object.defineProperty(win.navigator, 'language', { get: () => 'zh-CN', configurable: true });
Object.defineProperty(win.navigator, 'languages', { get: () => ['zh-CN', 'zh'], configurable: true });
Object.defineProperty(win.navigator, 'webdriver', { get: () => false, configurable: true });
Object.defineProperty(win.navigator, 'vendor', { get: () => 'Google Inc.', configurable: true });
Object.defineProperty(win.navigator, 'deviceMemory', { get: () => 8, configurable: true });
Object.defineProperty(win.navigator, 'hardwareConcurrency', { get: () => 8, configurable: true });
Object.defineProperty(win.navigator, 'maxTouchPoints', { get: () => 0, configurable: true });
Object.defineProperty(win.navigator, 'connection', { get: () => ({ effectiveType: '4g', saveData: false, downlink: 10, rtt: 100, type: 'wifi', addEventListener() {}, removeEventListener() {} }), configurable: true });
Object.defineProperty(win.navigator, 'userActivation', { get: () => ({ hasBeenActive: true, isActive: true }), configurable: true });
Object.defineProperty(win.navigator, 'appVersion', { get: () => UA0.replace('Mozilla/', ''), configurable: true });
Object.defineProperty(win.navigator, 'appName', { get: () => 'Netscape', configurable: true });
Object.defineProperty(win.navigator, 'product', { get: () => 'Gecko', configurable: true });
Object.defineProperty(win.navigator, 'appCodeName', { get: () => 'Mozilla', configurable: true });
try { Object.defineProperty(win.navigator, 'pdfViewerEnabled', { get: () => true, configurable: true }); } catch (_) {}
const UA_DATA = { brands: [{ brand: 'Not=A?Brand', version: '99' }, { brand: 'Google Chrome', version: '151' }, { brand: 'Chromium', version: '151' }], mobile: false, platform: 'Windows', getHighEntropyValues: () => Promise.resolve({ architecture: 'x86', bitness: '64', model: '', platformVersion: '10.0.0', uaFullVersion: '151.0.7922.72', fullVersionList: [{ brand: 'Not=A?Brand', version: '99.0.0.0' }, { brand: 'Google Chrome', version: '151.0.7922.72' }, { brand: 'Chromium', version: '151.0.7922.72' }], wow64: false }), getValues: () => Promise.resolve({ brands: [{ brand: 'Not=A?Brand', version: '99' }, { brand: 'Google Chrome', version: '151' }, { brand: 'Chromium', version: '151' }], mobile: false, platform: 'Windows' }) };
Object.defineProperty(win.navigator, 'userAgentData', { get: () => UA_DATA, configurable: true });
Object.defineProperty(win.navigator, 'mediaDevices', { get: () => ({ enumerateDevices: () => Promise.resolve([{ kind: 'audioinput', label: '', deviceId: '', groupId: '' }, { kind: 'videoinput', label: '', deviceId: '', groupId: '' }]), getDisplayMedia: () => Promise.reject(new Error('not allowed')), getUserMedia: () => Promise.reject(new Error('not allowed')) }), configurable: true });
Object.defineProperty(win.navigator, 'mimeTypes', { get: () => [{ type: 'application/pdf', suffixes: 'pdf', description: 'Portable Document Format' }, { type: 'application/x-google-chrome-pdf', suffixes: 'pdf', description: 'Portable Document Format' }], configurable: true });
Object.defineProperty(win.navigator, 'plugins', { get: () => [
  { name: 'PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
  { name: 'Chrome PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
  { name: 'Chromium PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
  { name: 'Microsoft Edge PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
  { name: 'WebKit built-in PDF', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
], configurable: true });
try { polyfill.install(win); } catch (e) {}
try { Object.defineProperty(win.document, 'characterSet', { get: () => 'windows-1252', configurable: true }); } catch (_) {}
try { Object.defineProperty(win.document, 'compatMode', { get: () => 'BackCompat', configurable: true }); } catch (_) {}
try { Object.defineProperty(win.document, 'domain', { get: () => 'newassets.hcaptcha.com', configurable: true }); } catch (_) {}
try { Object.defineProperty(win.document, 'currentScript', { get: () => null, configurable: true }); } catch (_) {}
const fakeAudioCtx = {
  sampleRate: 48000, currentTime: 0, destination: {},
  createOscillator: () => ({ connect() {}, start() {}, stop() {}, frequency: { value: 0 }, type: 'sine' }),
  createAnalyser: () => ({ connect() {}, disconnect() {}, getFloatFrequencyData(arr) { arr.fill(-127); }, getByteFrequencyData(arr) { arr.fill(0); }, fftSize: 2048, frequencyBinCount: 1024 }),
  createGain: () => ({ connect() {}, gain: { value: 1 } }),
  createScriptProcessor: () => ({ connect() {}, disconnect() {}, onaudioprocess: null }),
  createBuffer: () => ({ getChannelData: () => new Float32Array(48000) }),
  resume: () => Promise.resolve(), close: () => Promise.resolve(), state: 'running',
};
if (typeof win.AudioContext !== 'function') win.AudioContext = function () { return fakeAudioCtx; };
if (typeof win.webkitAudioContext !== 'function') win.webkitAudioContext = win.AudioContext;
if (typeof win.OfflineAudioContext !== 'function') win.OfflineAudioContext = function () { return fakeAudioCtx; };

const vm = require('vm');
const ctx = vm.createContext(win);
const fpLog = [];
ctx.__fp_log = fpLog;
vm.runInContext('globalThis.__hsw_dbg = globalThis.__hsw_dbg || [];', ctx);

const HOOK = `
(function () {
  function snap(v) {
    if (typeof v === 'string') return 's:' + v.slice(0, 120);
    if (typeof v === 'number' || typeof v === 'boolean' || v === null || v === undefined) return String(v);
    if (typeof v === 'function') return 'fn';
    if (ArrayBuffer.isView(v) || v instanceof ArrayBuffer) {
      const u = new Uint8Array(v.buffer || v, v.byteOffset || 0, Math.min(v.byteLength || v.length, 96));
      return 'buf(' + (v.byteLength != null ? v.byteLength : v.length) + '):' + Array.from(u).map(b => b.toString(16).padStart(2, '0')).join('');
    }
    if (typeof v === 'object') {
      try { return 'obj:' + JSON.stringify(v).slice(0, 200); } catch (e) { return 'obj:<unser>'; }
    }
    return typeof v;
  }
  globalThis.__imp_orig = {};
  const origInst = WebAssembly.instantiate;
  const log = globalThis.__fp_log;
  WebAssembly.instantiate = function (buf, imp) {
    log.push(['INST', 'buf=' + (buf && buf.byteLength)]);
    globalThis.__hsw_imp = imp;
    if (imp) {
      for (const mname in imp) {
        const m = imp[mname];
        if (m && typeof m === 'object') {
          globalThis.__imp_orig[mname] = globalThis.__imp_orig[mname] || {};
          const fnames = Object.keys(m).filter(k => typeof m[k] === 'function');
          log.push(['IMPMOD', mname, fnames.join(',')]);
          for (const fname in m) {
            const fn = m[fname];
            if (typeof fn !== 'function') continue;
            globalThis.__imp_orig[mname][fname] = fn;
            m[fname] = function () {
              const a = Array.prototype.slice.call(arguments);
              try { log.push(['CALL', mname + '.' + fname, a.map(snap).join('|')]); } catch (e) {}
              return fn.apply(this, arguments);
            };
          }
        }
      }
    }
    return origInst.call(this, buf, imp);
  };
  WebAssembly.instantiateStreaming = async function (source, imp) {
    const resp = await source;
    const buf = await resp.arrayBuffer();
    return WebAssembly.instantiate(buf, imp);
  };
})();
`;
vm.runInContext(HOOK, ctx);
vm.runInContext(hswCode, ctx, { timeout: 60000 });
(async () => {
  const t0 = Date.now();
  let n = '';
  try {
    await new Promise((r) => setTimeout(r, 80));
    n = String(await Promise.race([
      Promise.resolve(win.hsw(req)).then((x) => String(x)),
      new Promise((_, rej) => setTimeout(() => rej(new Error('pow timeout')), 60000)),
    ]));
  } catch (e) {
    process.stdout.write(JSON.stringify({ ok: false, error: String(e && (e.stack || e)).slice(0, 300), fp_log: fpLog, n_len: n.length }));
    process.exit(1);
  }
  const fnCalls = fpLog.filter(e => e[0] === 'CALL').map(e => e[1]);
  const impSrc = {};
  try {
    const imp = vm.runInContext('globalThis.__imp_orig', ctx);
    if (imp) {
      for (const mname in imp) {
        const m = imp[mname];
        if (m && typeof m === 'object') {
          impSrc[mname] = {};
          for (const fname in m) {
            if (typeof m[fname] === 'function') {
              try { impSrc[mname][fname] = m[fname].toString().slice(0, 700); } catch (e) { impSrc[mname][fname] = '<no toString>'; }
            }
          }
        }
      }
    }
  } catch (e) { impSrc.err = String(e); }
  process.stdout.write(JSON.stringify({ ok: true, n_len: n.length, ms: Date.now() - t0, fn_calls: fnCalls, fp_log: fpLog, imp_src: impSrc }));
  process.exit(0);
})();