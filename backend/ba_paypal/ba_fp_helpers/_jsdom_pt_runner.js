#!/usr/bin/env node
/**
 * jsdom n-token plaintext capture with injectable extra polyfills.
 * Input: { req, hswPath?, patchedWasmB64, extraPolyfill, userAgent }
 * Output: { ok, n, N, records, head_hex, ptB64 }
 */
'use strict';
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');
const { createCanvas } = require('@napi-rs/canvas');
const nodeCrypto = require('crypto');
const polyfill = require('./sandbox_polyfill');

const ROOT = path.join(__dirname, '..');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const req = String(input.req || '').trim();
const UA0 = input.userAgent || 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36';
const patchedB64 = String(input.patchedWasmB64 || '');
const extra = String(input.extraPolyfill || '');
const hswPath = input.hswPath || path.join(ROOT, '_hsw_protocol_live.js');

const GRIND = 5; // PoW difficulty override placeholder

const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>', {
    url: 'https://newassets.hcaptcha.com/',
    pretendToBeVisual: true,
    runScripts: 'outside-only',
});
const win = dom.window;

win.HTMLCanvasElement.prototype.getContext = function (type) {
    const cv = createCanvas(this.width || 300, this.height || 150);
    if (type === '2d' || !type) return cv.getContext('2d');
    return cv.getContext(type);
};
win.HTMLCanvasElement.prototype.toDataURL = function () {
    const cv = createCanvas(this.width || 300, this.height || 150);
    try { return cv.toDataURL(); } catch (e) { return ''; }
};
win.HTMLCanvasElement.prototype.getImageData = function (sx, sy, sw, sh) {
    const cv = createCanvas(this.width || 300, this.height || 150);
    try { return cv.getContext('2d').getImageData(sx, sy, sw, sh); } catch (e) { return null; }
};

win.WebAssembly = WebAssembly;
win.globalThis = win; win.self = win; win.global = win;
try { Object.defineProperty(win, 'crypto', { value: nodeCrypto.webcrypto, writable: true, configurable: true }); } catch (_) {}
win.atob = (s) => Buffer.from(s, 'base64').toString('binary');
win.btoa = (s) => Buffer.from(s, 'binary').toString('base64');

Object.defineProperty(win.navigator, 'userAgent', { get: () => UA0, configurable: true });
Object.defineProperty(win.navigator, 'platform', { get: () => 'Win32', configurable: true });
Object.defineProperty(win.navigator, 'language', { get: () => 'en-US', configurable: true });
Object.defineProperty(win.navigator, 'languages', { get: () => ['en-US', 'en'], configurable: true });
Object.defineProperty(win.navigator, 'webdriver', { get: () => false, configurable: true });
Object.defineProperty(win.navigator, 'vendor', { get: () => 'Google Inc.', configurable: true });
Object.defineProperty(win.screen, 'width', { get: () => 1920, configurable: true });
Object.defineProperty(win.screen, 'height', { get: () => 1080, configurable: true });
Object.defineProperty(win.screen, 'availWidth', { get: () => 1920, configurable: true });
Object.defineProperty(win.screen, 'availHeight', { get: () => 1040, configurable: true });
Object.defineProperty(win.screen, 'colorDepth', { get: () => 24, configurable: true });
Object.defineProperty(win.screen, 'pixelDepth', { get: () => 24, configurable: true });
Object.defineProperty(win, 'devicePixelRatio', { get: () => 1, configurable: true });
Object.defineProperty(win, 'innerWidth', { get: () => 1920, configurable: true });
Object.defineProperty(win, 'innerHeight', { get: () => 1080, configurable: true });
Object.defineProperty(win, 'outerWidth', { get: () => 1920, configurable: true });
Object.defineProperty(win, 'outerHeight', { get: () => 1080, configurable: true });

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

try { polyfill.install(win); } catch (e) { console.error('polyfill fail', e); }

if (extra) {
    try { require('vm').runInContext(extra, require('vm').createContext(win), { timeout: 10000 }); }
    catch (e) { console.error('extra polyfill fail', e); }
}

const vm = require('vm');
const ctx = vm.createContext(win);
const HOOK_JS = `
(function () {
  function _b64ToU8(s) {
    if (typeof Buffer !== 'undefined') {
      const b = Buffer.from(s, 'base64');
      return new Uint8Array(b.buffer, b.byteOffset, b.byteLength);
    }
    const bin = atob(s);
    const u8 = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
    return u8;
  }
  function _install(t) {
    if (!t || !t.WebAssembly) return;
    const origInstantiate = t.WebAssembly.instantiate;
    t.WebAssembly.instantiate = function (buf, imp) {
      let useBuf = buf;
      if (buf && buf.byteLength != null) useBuf = _b64ToU8(globalThis.__patched_wasm_b64);
      return origInstantiate.call(this, useBuf, imp).then(r => {
        const inst = r.instance || r;
        if (inst && inst.exports) {
          globalThis.__hsw_exports = inst.exports;
          for (const k of Object.keys(inst.exports)) {
            const v = inst.exports[k];
            if (v && typeof v === 'object' && v.buffer && typeof v.grow === 'function') {
              globalThis.__hsw_memory = v; break;
            }
          }
        }
        return r;
      });
    };
    if (t.WebAssembly.instantiateStreaming) {
      t.WebAssembly.instantiateStreaming = async function (source, imp) {
        const resp = await source; const buf = await resp.arrayBuffer();
        return t.WebAssembly.instantiate(buf, imp);
      };
    }
  }
  _install(globalThis);
  _install(typeof window !== "undefined" ? window : null);
})();
`;
globalThis.__patched_wasm_b64 = patchedB64;
function runIn(js) { return vm.runInContext(js, ctx, { timeout: 60000 }); }
try {
    runIn("globalThis.__patched_wasm_b64 = " + JSON.stringify(patchedB64) + ";");
    runIn(HOOK_JS);
} catch (e) {
    process.stdout.write(JSON.stringify({ ok: false, error: 'hook: ' + String(e && (e.stack || e)) }));
    process.exit(1);
}

(async () => {
    const t0 = Date.now();
    try {
        runIn(fs.readFileSync(hswPath, 'utf8'));
        await new Promise((r) => setTimeout(r, 60));
        runIn("(async()=>{try{await window.hsw(1,new Uint8Array(16));}catch(e){}})();");
        for (let i = 0; i < 120; i++) { await new Promise(r => setTimeout(r, 100)); if (globalThis.__hsw_exports) break; }
        if (!globalThis.__hsw_exports) {
            process.stdout.write(JSON.stringify({ ok: false, error: 'no exports' })); return;
        }
        const e = globalThis.__hsw_exports;
        e.__poke32(50016, 0); e.__poke32(50000, 1);
        let n = '';
        try {
            const r = await Promise.race([
                Promise.resolve(win.hsw(req)).then(x => String(x)),
                new Promise((_, rej) => setTimeout(() => rej(new Error('pow timeout')), 90000)),
            ]);
            n = r;
        } finally {
            e.__poke32(50000, 0);
        }
        const raw = Buffer.from(n, 'base64');
        const N = raw.length - 29;
        const arr = runIn("(function(){return Array.from(new Uint8Array(globalThis.__hsw_memory.buffer,50032,30000));})()") || [];
        const pt = Buffer.from(arr).subarray(0, N > 0 ? N : 0);
        const needle = Buffer.from([1, 0, 0, 0, 0, 0, 0, 0]);
        let m = 0; let pos = 0;
        while ((pos = pt.indexOf(needle, pos)) !== -1) { m++; pos += 1; }
        process.stdout.write(JSON.stringify({
            ok: true, n_len: n.length, N: N, records: m, ms: Date.now() - t0,
            head_hex: pt.subarray(0, 64).toString('hex'),
        }));
    } catch (e) {
        process.stdout.write(JSON.stringify({ ok: false, error: String(e && (e.stack || e)) }));
    }
})();