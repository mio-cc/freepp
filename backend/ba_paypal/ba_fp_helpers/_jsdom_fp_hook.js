#!/usr/bin/env node
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
const hswCode = fs.readFileSync(input.hswPath || path.join(ROOT, '_hsw_semi.js'), 'utf8');
const UA0 = input.userAgent || 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36';
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
Object.defineProperty(win.navigator, 'deviceMemory', { get: () => undefined, configurable: true });
Object.defineProperty(win.navigator, 'hardwareConcurrency', { get: () => 8, configurable: true });
Object.defineProperty(win.navigator, 'maxTouchPoints', { get: () => 0, configurable: true });
Object.defineProperty(win.navigator, 'connection', { get: () => ({ effectiveType: '4g', saveData: false, downlink: 10, rtt: 100, type: 'wifi', addEventListener() {}, removeEventListener() {} }), configurable: true });
Object.defineProperty(win.navigator, 'userActivation', { get: () => ({ hasBeenActive: true, isActive: true }), configurable: true });
const UA_DATA = { brands: [], getHighEntropyValues: () => Promise.resolve({ architecture: 'x86', bitness: '64', model: '', platformVersion: '15.0.0', fullVersionList: [{ brand: 'Chromium', version: '146.0.694.152' }, { brand: 'Google Chrome', version: '146.0.694.152' }, { brand: 'Not(A:Brand', version: '99.0.0.0' }] }), getValues: () => Promise.resolve({ brands: [], mobile: false, platform: 'Windows' }) };
Object.defineProperty(win.navigator, 'appVersion', { get: () => '5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36', configurable: true });
Object.defineProperty(win.navigator, 'userAgentData', { get: () => UA_DATA, configurable: true });
Object.defineProperty(win.navigator, 'mediaDevices', { get: () => ({ enumerateDevices: () => Promise.resolve([{ kind: 'audioinput', label: '', deviceId: '', groupId: '' }, { kind: 'videoinput', label: '', deviceId: '', groupId: '' }]), getDisplayMedia: () => Promise.reject(new Error('not allowed')), getUserMedia: () => Promise.reject(new Error('not allowed')) }), configurable: true });
Object.defineProperty(win.navigator, 'mimeTypes', { get: () => [
    { type: 'application/pdf', suffixes: 'pdf', description: 'Portable Document Format' },
    { type: 'application/x-google-chrome-pdf', suffixes: 'pdf', description: 'Portable Document Format' },
], configurable: true });
Object.defineProperty(win.navigator, 'plugins', { get: () => [
    { name: 'PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
    { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: 'Portable Document Format' },
    { name: 'Chromium PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: 'Portable Document Format' },
    { name: 'Microsoft Edge PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: 'Portable Document Format' },
    { name: 'WebKit built-in PDF', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
], configurable: true });
Object.defineProperty(win.navigator, 'pdfViewerEnabled', { get: () => true, configurable: true });
try { polyfill.install(win); } catch (e) {}
const PERF = win.performance;
try {
    if (PERF) {
        Object.defineProperty(PERF, 'memory', { get: () => ({ usedJSHeapSize: 42000000, totalJSHeapSize: 64000000, jsHeapSizeLimit: 4294705152 }), configurable: true });
        const navEntry = { name: 'about:blank', entryType: 'navigation', startTime: 0, duration: 13.6,
            initiatorType: 'navigation', nextHopProtocol: '', renderBlockingStatus: 'non-blocking',
            loadEventEnd: 13.6, domContentLoadedEventEnd: 13.6, transferSize: 0, encodedBodySize: 0, decodedBodySize: 0,
            fetchStart: 0, domainLookupStart: 0, domainLookupEnd: 0, connectStart: 0, connectEnd: 0, secureConnectionStart: 0, requestStart: 0, responseStart: 0, responseEnd: 13.6 };
        PERF.getEntriesByType = function (t) {
            if (t === 'navigation') return [navEntry];
            if (t === 'resource') return [];
            try { return []; } catch (e) { return []; }
        };
        PERF.getEntries = function () { return [navEntry]; };
    }
} catch (_) {}
function makeFakeAnalyser(fftSize) {
    const binCount = fftSize / 2;
    const freq = new Float32Array(binCount);
    const time = new Float32Array(fftSize);
    for (let i = 0; i < binCount; i++) {
        const f = i / binCount;
        freq[i] = -140 + 100 * Math.exp(-1.5 * f) * (1 + 0.35 * Math.sin(i * 12.9898) * Math.cos(i * 78.233));
    }
    for (let i = 0; i < fftSize; i++) {
        time[i] = 0.6 * Math.sin(2 * Math.PI * 10000 * i / 48000) * Math.exp(-6 * i / fftSize);
    }
    return {
        connect() {}, disconnect() {}, fftSize, frequencyBinCount: binCount,
        getFloatFrequencyData(arr) { arr.set(freq); },
        getByteFrequencyData(arr) { for (let i = 0; i < arr.length; i++) arr[i] = Math.max(0, Math.min(255, Math.round(255 * Math.exp(-2.2 * i / arr.length)))); },
        getFloatTimeDomainData(arr) { arr.set(time); },
    };
}
const audioEngine = {
    sampleRate: 48000,
    _mk() {
        return {
            sampleRate: 48000, currentTime: 0, destination: {},
            createOscillator() { return { connect() {}, disconnect() {}, start() {}, stop() {}, frequency: { value: 10000 }, type: 'triangle' }; },
            createAnalyser() { return makeFakeAnalyser(2048); },
            createGain() { return { connect() {}, gain: { value: 1 } }; },
            createScriptProcessor() { return { connect() {}, disconnect() {}, onaudioprocess: null }; },
            createBuffer() { return { getChannelData: () => new Float32Array(48000) }; },
            createDynamicsCompressor() { return { threshold: { value: -50 }, knee: { value: 40 }, attack: { value: 0 }, release: { value: 0.25 }, ratio: { value: 12 }, reduction: { value: 0 }, connect() {}, disconnect() {} }; },
            resume: () => Promise.resolve(), close: () => Promise.resolve(), state: 'running',
        };
    },
};
if (typeof win.AudioContext !== 'function') win.AudioContext = function () { return audioEngine._mk(); };
if (typeof win.webkitAudioContext !== 'function') win.webkitAudioContext = win.AudioContext;
if (typeof win.OfflineAudioContext !== 'function') {
    win.OfflineAudioContext = function (channels, length, sampleRate) {
        const ctx = audioEngine._mk();
        ctx.startRendering = function () {
            const data = new Float32Array(length || 5000);
            const sr = sampleRate || 44100;
            for (let i = 0; i < data.length; i++) {
                data[i] = 0.8 * Math.sin(2 * Math.PI * 10000 * i / sr) * Math.exp(-8 * i / data.length) + 0.05 * Math.sin(i * 0.33);
            }
            setTimeout(() => { if (typeof ctx.oncomplete === 'function') ctx.oncomplete({ renderedBuffer: { length: data.length, getChannelData: () => data } }); }, 50);
            return Promise.resolve();
        };
        return ctx;
    };
}
const fakeWebGL = {
    VERSION: 1, canvas: { width: 300, height: 150 },
    getParameter(p) {
        switch (p) {
            case 0x1F00: return 'WebKit WebGL';
            case 0x1F01: return 'OpenGL ES 2.0 (WebGL 1.0 (OpenGL ES 2.0 Chromium))';
            case 0x1F02: return 'WebGL 1.0 (OpenGL ES 2.0 Chromium)';
            case 0x1F03: return 'Google Inc. (NVIDIA)';
            case 0x1F04: return 'ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero) (0x0000C0DE)), NVIDIA)';
            case 0x9246: return 'Google Inc. (NVIDIA)';
            case 0x9245: return 'ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero) (0x0000C0DE)), NVIDIA)';
            case 0x9240: return 'WebGL GLSL ES 1.0 (OpenGL ES GLSL ES 1.0 Chromium)';
            case 0x8B8C: return 'WebGL GLSL ES 1.0 (OpenGL ES GLSL ES 1.0 Chromium)';
            case 0x821B: return 0;
            case 0x821C: return 0;
            default: return 0;
        }
    },
    getExtension() { return null; },
    getSupportedExtensions() { return ['ANGLE_instanced_arrays', 'EXT_blend_minmax', 'WEBGL_debug_renderer_info']; },
    getContextAttributes() { return { alpha: true, antialias: true, depth: true, failIfMajorPerformanceCaveat: false, powerPreference: 'default', premultipliedAlpha: true, preserveDrawingBuffer: false, stencil: false }; },
    getError() { return 0; },
    getShaderPrecisionFormat() { return { rangeMin: 127, rangeMax: 127, precision: 23 }; },
    clearColor() {}, clear() {}, viewport() {}, enable() {}, disable() {}, blendFunc() {}, useProgram() {},
    createBuffer: () => ({ bindBuffer() {} }), createShader: () => ({ getShaderParameter: () => true }),
    shaderSource() {}, compileShader() {}, getShaderInfoLog: () => '', createProgram: () => ({ getProgramParameter: () => true }),
    attachShader() {}, linkProgram() {}, getProgramInfoLog: () => '', getUniformLocation: () => null,
    uniform1f() {}, uniform2f() {}, uniform4f() {}, vertexAttribPointer() {}, enableVertexAttribArray() {},
    drawArrays() {}, drawElements() {}, getParameterFn: () => null, isContextLost: () => false,
};
const origGetContext2 = win.HTMLCanvasElement.prototype.getContext;
win.HTMLCanvasElement.prototype.getContext = function (type) {
    if (type === 'webgl' || type === 'experimental-webgl') return fakeWebGL;
    return origGetContext2.call(this, type);
};
const vm = require('vm');
const ctx = dom.getInternalVMContext();
if (input.patchedWasmB64) {
    vm.runInContext(`globalThis.__patched_wasm_b64 = ${JSON.stringify(input.patchedWasmB64)};`, ctx);
    const HOOK2 = `
(function () {
  function _b64ToU8(s) {
    if (typeof Buffer !== "undefined") {
      const b = Buffer.from(s, "base64");
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
      globalThis.__hsw_imp = imp;
      const log = globalThis.__fp_log;
      function snap(v) {
        if (typeof v === 'string') return 's:' + v.slice(0, 200);
        if (typeof v === 'number' || typeof v === 'boolean' || v === null || v === undefined) return String(v);
        if (typeof v === 'function') return 'fn';
        if (ArrayBuffer.isView(v) || v instanceof ArrayBuffer) {
          const u = new Uint8Array(v.buffer || v, v.byteOffset || 0, Math.min(v.byteLength || v.length, 64));
          return 'buf:' + Array.from(u).map(b => b.toString(16).padStart(2, '0')).join('');
        }
        if (typeof v === 'object') {
          try { return 'obj:' + JSON.stringify(v).slice(0, 200); } catch (e) { return 'obj:<unser>'; }
        }
        return typeof v;
      }
      try {
        if (imp) {
          const log = globalThis.__fp_log;
          for (const mname in imp) {
            const m = imp[mname];
            if (m && typeof m === 'object') {
              const fnames = Object.keys(m).filter(k => typeof m[k] === 'function');
              log.push(['IMPMOD', mname, fnames.join(',')]);
              for (const fname in m) {
                const fn = m[fname];
                if (typeof fn !== 'function') continue;
                m[fname] = function () {
                  try {
                    const a = Array.prototype.slice.call(arguments);
                    const r = fn.apply(this, arguments);
                    const log = globalThis.__fp_log;
                    if (a.length >= 2 && typeof a[0] === 'number') {
                      log.push(['CALL', mname + '.' + fname, 'a=' + a.slice(0, 4).join(','), 'r=' + (r === undefined ? 'u' : typeof r)]);
                    }
                    return r;
                  } catch (e) { throw e; }
                };
              }
            }
          }
        }
      } catch (e) {}
      let useBuf = buf;
      const dbg = globalThis.__hsw_dbg || (globalThis.__hsw_dbg = []);
      try {
        if (buf && buf.byteLength != null) {
          useBuf = _b64ToU8(globalThis.__patched_wasm_b64);
          dbg.push('SWAP in=' + buf.byteLength + '/out=' + useBuf.byteLength);
        } else {
          dbg.push('NOSWAP in-kind=' + Object.prototype.toString.call(buf));
        }
      } catch (e) { dbg.push('SWAPERR ' + String(e).slice(0, 120)); }
      return origInstantiate.call(this, useBuf, imp).then(r => {
        const inst = r.instance || r;
        if (inst && inst.exports) {
          globalThis.__hsw_exports = inst.exports;
          dbg.push('EXPORTS ' + Object.keys(inst.exports).join(','));
          for (const k of Object.keys(inst.exports)) {
            const v = inst.exports[k];
            if (v && typeof v === "object" && v.buffer && typeof v.grow === "function") {
              globalThis.__hsw_memory = v;
              break;
            }
          }
        }
        return r;
      });
    };
    t.WebAssembly.instantiateStreaming = async function (source, imp) {
      const resp = await source;
      const buf = await resp.arrayBuffer();
      return t.WebAssembly.instantiate(buf, imp);
    };
  }
  _install(globalThis);
  _install(typeof window !== "undefined" ? window : null);
})();
`;
    vm.runInContext(HOOK2, ctx);
    vm.runInContext(`
(function () {
  const s = String(WebAssembly.instantiate);
  const sw = String(window.WebAssembly.instantiate);
  const patchedB64 = typeof globalThis.__patched_wasm_b64;
  globalThis.__hook_state = 'inst=' + s.slice(0, 30) + '|wininst=' + sw.slice(0, 30) + '|pb64=' + patchedB64;
})();
`, ctx);
}
const fpLog = [];
ctx.__fp_log = fpLog;
vm.runInContext('globalThis.__hsw_dbg = globalThis.__hsw_dbg || [];', ctx);
vm.runInContext(hswCode, ctx, { timeout: 60000 });
vm.runInContext(`
(function () {
  const origS = JSON.stringify;
  const log = globalThis.__fp_log;
  JSON.stringify = function (v, ...rest) {
    try {
      if (v && typeof v === 'object' && !Array.isArray(v)) {
        const keys = Object.keys(v);
        log.push(['SKEYS', keys.join(',')]);
      }
    } catch (e) {}
    return origS.apply(this, [v, ...rest]);
  };
})();
`, ctx);
const FP_HOOK = `
(function () {
  const log = globalThis.__fp_log;
  function snap(v) {
    if (typeof v === 'string') return 's:' + v.slice(0, 200);
    if (typeof v === 'number' || typeof v === 'boolean' || v === null || v === undefined) return String(v);
    if (typeof v === 'function') return 'fn';
    if (ArrayBuffer.isView(v) || v instanceof ArrayBuffer) {
      const u = new Uint8Array(v.buffer || v, v.byteOffset || 0, Math.min(v.byteLength || v.length, 64));
      return 'buf:' + Array.from(u).map(b => b.toString(16).padStart(2, '0')).join('');
    }
    if (typeof v === 'object') {
      try { return 'obj:' + JSON.stringify(v).slice(0, 200); } catch (e) { return 'obj:<unser>'; }
    }
    return typeof v;
  }
  const i = globalThis.__hsw_imp;
  if (!i) { log.push(['NO_IMP']); return; }
  const names = [];
  for (const mname in i) {
    const m = i[mname];
    if (m && typeof m === 'object') {
      for (const fname in m) {
        names.push(mname + '.' + fname);
        const fn = m[fname];
        if (typeof fn === 'function') {
          const wrapped = function () {
            try {
              const a = Array.prototype.slice.call(arguments);
              if (a.length >= 2 && (typeof a[0] === 'number')) {
                log.push([mname + '.' + fname, 'FPID=' + a[0], snap(a[1])]);
              } else {
                log.push([mname + '.' + fname, 'args=' + a.map(snap).join('|').slice(0, 300)]);
              }
            } catch (e) { log.push(['ERR', String(e)]); }
            return fn.apply(this, arguments);
          };
          try { m[fname] = wrapped; } catch (e) { log.push(['NOREPLACE', mname + '.' + fname, String(e).slice(0, 80)]); }
        }
      }
    }
  }
  log.push(['IMPORTS', names.join(',')]);
})();
`;
vm.runInContext(FP_HOOK, ctx);
(async () => {
    try {
        await new Promise((r) => setTimeout(r, 80));
        try { await Promise.race([
            Promise.resolve(win.hsw(1, new Uint8Array(0))).then((x) => String(x)),
            new Promise((_, rej) => setTimeout(() => rej(new Error('warmup timeout')), 15000)),
        ]); } catch (e) {}
        const ex = win.__hsw_exports;
        const impt = win.__hsw_imp;
        const dumpImps = {};
        try {
            if (impt) {
                for (const mname in impt) {
                    const m = impt[mname];
                    if (m && typeof m === 'object') {
                        dumpImps[mname] = {};
                        for (const fname in m) {
                            const fn = m[fname];
                            if (typeof fn === 'function') dumpImps[mname][fname] = String(fn);
                        }
                    }
                }
            }
        } catch (e) { dumpImps._err = String(e); }
        process.stdout.write('IMP_DUMP ' + JSON.stringify(dumpImps) + '\n');
        if (!ex) throw new Error('no __hsw_exports after warmup');
        fpLog.push(['EXPORT_KEYS', Object.keys(ex).join(',')]);
        fpLog.push(['WARMUP_DONE']);
        ex.__poke32(50016, 0);
        ex.__poke32(50000, 1);
        const n = String(await Promise.race([
            Promise.resolve(win.hsw(req, input.fp || undefined)).then((x) => String(x)),
            new Promise((_, rej) => setTimeout(() => rej(new Error('hsw pow timeout')), 60000)),
        ]));
        ex.__poke32(50000, 0);
        const mem = win.__hsw_memory;
        const out = { ok: true, n, n_len: n.length, fp_log: fpLog, hsw_dbg: ctx.__hsw_dbg, hook_state: ctx.__hook_state, imp_keys: (ctx.__hsw_imp ? Object.keys(ctx.__hsw_imp).join(',') : 'NOIMP') };
        if (mem) {
            const buf = Buffer.from(new Uint8Array(mem.buffer, 1000000, Math.min(mem.buffer.byteLength - 1000000, 250000)));
            out.buf_b64 = buf.toString('base64');
            out.buf_marker = countMarkers(buf);
        }
        process.stdout.write(JSON.stringify(out) + '\n');
    } catch (e) {
        process.stdout.write(JSON.stringify({ ok: false, error: String(e && (e.stack || e)).slice(0, 400),
            fp_log: fpLog, hook_state: ctx.__hook_state, hsw_dbg: ctx.__hsw_dbg,
            imp_keys: (ctx.__hsw_imp ? Object.keys(ctx.__hsw_imp).join(',') : 'NOIMP') }) + '\n');
    }
    setTimeout(() => process.exit(0), 300);
    function countMarkers(buf) {
        const needle = Buffer.from([1, 0, 0, 0, 0, 0, 0, 0]);
        let c = 0, i = 0;
        while ((i = buf.indexOf(needle, i)) >= 0) { c++; i += 1; }
        return c;
    }
})();
