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
const fakeAudioCtx = {
    sampleRate: 48000, currentTime: 0, destination: {},
    createOscillator: () => ({ connect() {}, start() {}, stop() {}, frequency: { value: 0 }, type: 'sine' }),
    createAnalyser: () => ({ connect() {}, disconnect() {}, fftSize: 2048, frequencyBinCount: 1024,
        getFloatFrequencyData(arr) { for (let i = 0; i < arr.length; i++) { const f = i / arr.length; arr[i] = -120 + 90 * Math.exp(-3 * f) + 6 * Math.sin(i * 12.9898) * Math.cos(i * 78.233); } },
        getByteFrequencyData(arr) { for (let i = 0; i < arr.length; i++) { const f = i / arr.length; arr[i] = Math.max(0, Math.min(255, Math.round(255 * Math.exp(-2.2 * f) + 8 * Math.sin(i * 0.21)))); } } }),
    createGain: () => ({ connect() {}, gain: { value: 1 } }),
    createScriptProcessor: () => ({ connect() {}, disconnect() {}, onaudioprocess: null }),
    createBuffer: () => ({ getChannelData: () => new Float32Array(48000) }),
    resume: () => Promise.resolve(), close: () => Promise.resolve(), state: 'running',
};
if (typeof win.AudioContext !== 'function') win.AudioContext = function () { return fakeAudioCtx; };
if (typeof win.webkitAudioContext !== 'function') win.webkitAudioContext = win.AudioContext;
if (typeof win.OfflineAudioContext !== 'function') win.OfflineAudioContext = function () { return fakeAudioCtx; };
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
if (input.extraStubs) {
    try {
        Object.defineProperty(win.navigator, 'deviceMemory', { get: () => 8, configurable: true });
    } catch (e) {}
    try {
        win.OffscreenCanvas = function (w, h) { this.width = w; this.height = h; };
        win.OffscreenCanvas.prototype.getContext = function (type) {
            if (type === 'webgl' || type === 'webgl2' || type === 'experimental-webgl') return fakeWebGL;
            const cv = createCanvas(this.width || 300, this.height || 150);
            if (type === '2d' || !type) return cv.getContext('2d');
            return cv.getContext(type);
        };
        win.OffscreenCanvas.prototype.convertToBlob = function () { return Promise.resolve(new win.Blob([])); };
        win.OffscreenCanvas.prototype.transferToImageBitmap = function () { return { close() {} }; };
    } catch (e) {}
    try {
        win.WebGL2RenderingContext = function () {};
        const origGetContext3 = win.HTMLCanvasElement.prototype.getContext;
        win.HTMLCanvasElement.prototype.getContext = function (type) {
            if (type === 'webgl2') {
                const gl2 = Object.create(fakeWebGL);
                gl2.VERSION = 2;
                gl2.getParameter = function (p) {
                    if (p === 0x1F01) return 'OpenGL ES 3.0 (WebGL 2.0 (OpenGL ES 3.0 Chromium))';
                    if (p === 0x1F02) return 'WebGL 2.0 (OpenGL ES 3.0 Chromium)';
                    return fakeWebGL.getParameter(p);
                };
                return gl2;
            }
            return origGetContext3.call(this, type);
        };
    } catch (e) {}
    try {
        win.FontFace = function () {};
        win.document.fonts = { check: () => true, load: () => Promise.resolve([]), ready: Promise.resolve(), add() {}, delete() {}, forEach() {}, has: () => false, values() { return [][Symbol.iterator](); }, entries() { return [][Symbol.iterator](); }, keys() { return [][Symbol.iterator](); } };
    } catch (e) {}
    try {
        win.CSS = { supports: () => false, escape: (s) => s, ppx: {} };
    } catch (e) {}
    try {
        win.Worker = function () { return { postMessage() {}, terminate() {}, addEventListener() {}, removeEventListener() {}, onmessage: null, onerror: null }; };
    } catch (e) {}
    try {
        win.MediaRecorder = function () { return { start() {}, stop() {}, pause() {}, resume() {}, requestData() {}, addEventListener() {}, ondataavailable: null, onstop: null, state: 'inactive' }; };
        win.RTCPeerConnection = function () { return { createOffer: () => Promise.resolve({}), createDataChannel: () => ({}), addEventListener() {}, close() {} }; };
    } catch (e) {}
    try {
        win.SpeechSynthesis = function () {};
        win.speechSynthesis = { speak() {}, cancel() {}, getVoices: () => [] };
    } catch (e) {}
    try {
        win.indexedDB = { open: () => { const r = { onsuccess: null, onerror: null, result: null, error: null }; setTimeout(() => { r.result = {}; if (r.onsuccess) r.onsuccess(); }, 0); return r; } };
    } catch (e) {}
    try {
        win.structuredClone = (v) => JSON.parse(JSON.stringify(v));
    } catch (e) {}
}
const vm = require('vm');
const ctx = dom.getInternalVMContext();
if (input.patchedWasmB64) {
    vm.runInContext(`globalThis.__patched_wasm_b64 = ${JSON.stringify(input.patchedWasmB64)};`, ctx);
    if (input.hookJS) vm.runInContext(input.hookJS, ctx);
}
vm.runInContext(hswCode, ctx, { timeout: 60000 });
(async () => {
    try {
        await new Promise((r) => setTimeout(r, 80));
        try { await Promise.race([
            Promise.resolve(win.hsw(1, new Uint8Array(0))).then((x) => String(x)),
            new Promise((_, rej) => setTimeout(() => rej(new Error('warmup timeout')), 15000)),
        ]); } catch (e) {}
        const ex = win.__hsw_exports;
        if (!ex) throw new Error('no __hsw_exports after warmup');
        ex.__poke32(50016, 0);
        ex.__poke32(50000, 1);
        const n = String(await Promise.race([
            Promise.resolve(win.hsw(req, input.fp || undefined)).then((x) => String(x)),
            new Promise((_, rej) => setTimeout(() => rej(new Error('hsw pow timeout')), 60000)),
        ]));
        ex.__poke32(50000, 0);
        const mem = win.__hsw_memory;
        const out = { ok: true, n, n_len: n.length };
        if (mem) {
            const buf = Buffer.from(new Uint8Array(mem.buffer, 50032, 30000));
            out.buf_b64 = buf.toString('base64');
            out.buf_marker = countMarkers(buf);
        }
        if (input.double) {
            ex.__poke32(50016, 0);
            ex.__poke32(50000, 1);
            const n2 = String(await Promise.race([
                Promise.resolve(win.hsw(req, input.fp || undefined)).then((x) => String(x)),
                new Promise((_, rej) => setTimeout(() => rej(new Error('hsw2 timeout')), 60000)),
            ]));
            ex.__poke32(50000, 0);
            out.n2 = n2;
            out.n2_len = n2.length;
            if (mem) {
                const buf2 = Buffer.from(new Uint8Array(mem.buffer, 50032, 30000));
                out.buf2_b64 = buf2.toString('base64');
                out.buf2_marker = countMarkers(buf2);
            }
        }
        process.stdout.write(JSON.stringify(out) + '\n');
    } catch (e) {
        process.stdout.write(JSON.stringify({ ok: false, error: String(e && (e.stack || e)).slice(0, 400) }) + '\n');
    }
    setTimeout(() => process.exit(0), 300);
    function countMarkers(buf) {
        const needle = Buffer.from([1, 0, 0, 0, 0, 0, 0, 0]);
        let c = 0, i = 0;
        while ((i = buf.indexOf(needle, i)) >= 0) { c++; i += 1; }
        return c;
    }
})();
