#!/usr/bin/env node
/**
 * 实验: 以 hsw(req, fp_json_b64) 双参数调用 (Implex 纯指纹方案)。
 * 若 hsw 接受外部 fp, 则设备指纹完全由 Python 构建, 无需浏览器/jsdom 采集。
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
const fpB64 = String(input.fp || '');
const skip = new Set((input.skip || []));
const patchedWasmB64 = String(input.patchedWasmB64 || '');
const hookJS = String(input.hookJS || '');
const rings = input.rings || [];
const hswCode = fs.readFileSync(input.hswPath || path.join(ROOT, '_hsw_protocol_live.js'), 'utf8');
const UA0 = input.userAgent || 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36';

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
Object.defineProperty(win.navigator, 'language', { get: () => 'zh-CN', configurable: true });
Object.defineProperty(win.navigator, 'languages', { get: () => ['zh-CN', 'zh'], configurable: true });
Object.defineProperty(win.navigator, 'webdriver', { get: () => false, configurable: true });
Object.defineProperty(win.navigator, 'vendor', { get: () => 'Google Inc.', configurable: true });
Object.defineProperty(win.navigator, 'deviceMemory', { get: () => undefined, configurable: true });
Object.defineProperty(win.navigator, 'hardwareConcurrency', { get: () => 8, configurable: true });
Object.defineProperty(win.navigator, 'maxTouchPoints', { get: () => 0, configurable: true });
Object.defineProperty(win.navigator, 'connection', { get: () => ({ effectiveType: '4g', saveData: false, downlink: 10, rtt: 100, type: 'wifi', addEventListener() {}, removeEventListener() {} }), configurable: true });
Object.defineProperty(win.navigator, 'userActivation', { get: () => ({ hasBeenActive: true, isActive: true }), configurable: true });
const MEDIA_TRUTH = new Set([
    'monochrome:0', 'color-gamut:srgb', 'any-hover:hover', 'hover:hover',
    'any-pointer:fine', 'pointer:fine', 'display-mode:browser',
    'forced-colors:none', 'prefers-color-scheme:light',
    'prefers-contrast:no-preference', 'prefers-reduced-motion:no-preference',
    'prefers-reduced-transparency:no-preference',
]);
function mediaMatches(query) {
    let q = String(query || '').trim();
    let negate = false;
    if (q.startsWith('not ')) { negate = true; q = q.slice(4).trim(); }
    const parts = q.split(/ and /).map((p) => p.trim()).filter(Boolean);
    let ok = parts.length > 0;
    for (const part of parts) {
        const m = /^\((.+)\)$/.exec(part);
        const inner = m ? m[1].trim() : part;
        const kv = /^([a-zA-Z-]+)\s*:\s*(.+)$/.exec(inner);
        if (!kv) { ok = false; break; }
        const feat = kv[1].toLowerCase();
        const val = kv[2].trim();
        if (feat === 'device-width') { if (val !== String((win.screen && win.screen.width) || 0) + 'px') ok = false; }
        else if (feat === 'device-height') { if (val !== String((win.screen && win.screen.height) || 0) + 'px') ok = false; }
        else if (feat === '-webkit-device-pixel-ratio') { if (parseFloat(val) !== (win.devicePixelRatio || 1)) ok = false; }
        else if (feat === 'resolution') { if (parseFloat(val) !== (win.devicePixelRatio || 1)) ok = false; }
        else if (feat === '-moz-device-pixel-ratio') { ok = false; }
        else if (!MEDIA_TRUTH.has(feat + ':' + val)) ok = false;
        if (!ok) break;
    }
    return negate ? !ok : ok;
}
Object.defineProperty(win, 'matchMedia', { value: function (q) { const matches = mediaMatches(q); return { matches, media: q, onchange: null, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {}, dispatchEvent: () => true }; }, configurable: true });
Object.defineProperty(win.screen, 'width', { get: () => skip.has('screen') ? 1920 : 1280, configurable: true });
Object.defineProperty(win.screen, 'height', { get: () => skip.has('screen') ? 1080 : 720, configurable: true });
Object.defineProperty(win.screen, 'availWidth', { get: () => skip.has('screen') ? 1920 : 1280, configurable: true });
Object.defineProperty(win.screen, 'availHeight', { get: () => skip.has('screen') ? 1040 : 720, configurable: true });
Object.defineProperty(win.screen, 'colorDepth', { get: () => 24, configurable: true });
Object.defineProperty(win.screen, 'pixelDepth', { get: () => 24, configurable: true });
Object.defineProperty(win, 'devicePixelRatio', { get: () => 1, configurable: true });
Object.defineProperty(win, 'innerWidth', { get: () => skip.has('screen') ? 1920 : 1280, configurable: true });
Object.defineProperty(win, 'innerHeight', { get: () => skip.has('screen') ? 1080 : 720, configurable: true });
Object.defineProperty(win, 'outerWidth', { get: () => skip.has('screen') ? 1920 : 1280, configurable: true });
Object.defineProperty(win, 'outerHeight', { get: () => skip.has('screen') ? 1080 : 720, configurable: true });

const UA_DATA = { brands: [], getHighEntropyValues: () => Promise.resolve({ architecture: 'x86', bitness: '64', model: '', platformVersion: '15.0.0', fullVersionList: [{ brand: 'Chromium', version: '146.0.694.152' }, { brand: 'Google Chrome', version: '146.0.694.152' }, { brand: 'Not(A:Brand', version: '99.0.0.0' }] }), getValues: () => Promise.resolve({ brands: [], mobile: false, platform: 'Windows' }) };
Object.defineProperty(win.navigator, 'appVersion', { get: () => '5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36', configurable: true });
Object.defineProperty(win.navigator, 'userAgentData', { get: () => UA_DATA, configurable: true });
Object.defineProperty(win.navigator, 'mediaDevices', { get: () => skip.has('devices') ? { enumerateDevices: () => Promise.resolve([]), getUserMedia: () => Promise.reject(new Error('not allowed')) } : ({ enumerateDevices: () => Promise.resolve([{ kind: 'audioinput', label: '', deviceId: '', groupId: '' }, { kind: 'videoinput', label: '', deviceId: '', groupId: '' }]), getDisplayMedia: () => Promise.reject(new Error('not allowed')), getUserMedia: () => Promise.reject(new Error('not allowed')) }), configurable: true });
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
Object.defineProperty(win.navigator, 'permissions', { get: () => ({ query: () => Promise.resolve({ state: 'prompt' }) }), configurable: true });
Object.defineProperty(win.navigator, 'storage', { get: () => ({ estimate: () => Promise.resolve({ quota: 100000000000, usage: 5000000 }), persist: () => Promise.resolve(true) }), configurable: true });
Object.defineProperty(win.navigator, 'webkitTemporaryStorage', { get: () => ({ queryUsageAndQuota: (cb) => { if (typeof cb === 'function') cb(0, 0, 100000000000); } }), configurable: true });

try { Object.defineProperty(win, 'clientInformation', { value: win.navigator, configurable: true }); } catch (e) {}
try { delete win.ontouchstart; } catch (e) {}
try {
    const _prompt = win.prompt;
    Object.defineProperty(win, 'prompt', { value: function prompt() { return typeof _prompt === 'function' ? _prompt.apply(this, arguments) : undefined; }, configurable: true });
    Object.defineProperty(win.prompt, 'toString', { value: () => 'function prompt() { [native code] }', configurable: true });
} catch (e) {}
try {
    const _close = win.close;
    Object.defineProperty(win, 'close', { value: function close() { return typeof _close === 'function' ? _close.apply(this, arguments) : undefined; }, configurable: true });
    Object.defineProperty(win.close, 'toString', { value: () => 'function close() { [native code] }', configurable: true });
} catch (e) {}
if (!('SharedWorker' in win)) win.SharedWorker = function SharedWorker() {};
if (!('VisualViewport' in win)) win.VisualViewport = function VisualViewport() {};
if (!('ReportingObserver' in win)) win.ReportingObserver = function ReportingObserver() {};
if (!('RTCRtpTransceiver' in win)) win.RTCRtpTransceiver = function RTCRtpTransceiver() {};
try {
    if (typeof win.HTMLVideoElement.prototype.getVideoPlaybackQuality !== 'function') {
        win.HTMLVideoElement.prototype.getVideoPlaybackQuality = function () {
            return { creationTime: 0, totalVideoFrames: 0, droppedVideoFrames: 0, corruptedVideoFrames: 0 };
        };
    }
} catch (e) {}
try {
    win.Crypto = function Crypto() {};
} catch (e) {}
const MEDIA_PLAY = {
    'audio/ogg; codecs="vorbis"': 'probably',
    'audio/mpeg': 'probably',
    'audio/mpegurl': 'maybe',
    'audio/wav; codecs="1"': 'probably',
    'audio/x-m4a': 'maybe',
    'audio/aac': 'probably',
    'video/ogg; codecs="theora"': '',
    'video/quicktime': '',
    'video/mp4; codecs="avc1.42E01E"': 'probably',
    'video/webm; codecs="vp8"': 'probably',
    'video/webm; codecs="vp9"': 'probably',
    'video/x-matroska': 'maybe',
};
const MS_SUPPORT = new Set(['audio/mpeg', 'audio/aac', 'video/mp4; codecs="avc1.42E01E"', 'video/webm; codecs="vp8"', 'video/webm; codecs="vp9"']);
const MR_SUPPORT = new Set(['video/mp4; codecs="avc1.42E01E"', 'video/webm; codecs="vp8"', 'video/webm; codecs="vp9"', 'video/x-matroska']);
try {
    const cpt = win.HTMLMediaElement.prototype.canPlayType;
    win.HTMLMediaElement.prototype.canPlayType = function (type) {
        if (MEDIA_PLAY.hasOwnProperty(type)) return MEDIA_PLAY[type];
        try { return cpt.call(this, type); } catch (e) { return ''; }
    };
} catch (e) {}
if (!('MediaSource' in win)) {
    win.MediaSource = function MediaSource() {};
    win.MediaSource.isTypeSupported = function (type) { return MS_SUPPORT.has(type); };
}
if (!('MediaRecorder' in win)) {
    win.MediaRecorder = function MediaRecorder() {};
    win.MediaRecorder.isTypeSupported = function (type) { return MR_SUPPORT.has(type); };
}

const PERF = win.performance;
try {
    if (PERF && !skip.has('perf')) {
        Object.defineProperty(PERF, 'memory', { get: () => ({ usedJSHeapSize: 42000000, totalJSHeapSize: 64000000, jsHeapSizeLimit: 4294705152 }), configurable: true });
    }
} catch (_) {}
try {
    if (PERF && !skip.has('perf')) {
        const navEntry = { name: 'about:blank', entryType: 'navigation', startTime: 0, duration: 13.6,
            initiatorType: 'navigation', nextHopProtocol: '', renderBlockingStatus: 'non-blocking',
            loadEventEnd: 13.6, domContentLoadedEventEnd: 13.6, transferSize: 0, encodedBodySize: 0, decodedBodySize: 0,
            fetchStart: 0, domainLookupStart: 0, domainLookupEnd: 0, connectStart: 0, connectEnd: 0, secureConnectionStart: 0, requestStart: 0, responseStart: 0, responseEnd: 13.6 };
        const resEntries = [];
        const origGetEbt = (PERF.getEntriesByType || (() => () => [])).bind(PERF);
        const origGetE = (PERF.getEntries || (() => () => [])).bind(PERF);
        const origMark = (PERF.mark || (() => () => {})).bind(PERF);
        PERF.getEntriesByType = function (t) {
            if (t === 'navigation') return [navEntry];
            if (t === 'resource') return resEntries;
            try { return origGetEbt(t); } catch (e) { return []; }
        };
        PERF.getEntries = function () { return [navEntry].concat(resEntries); };
        PERF.mark = origMark;
    }
} catch (e) {}

const fakeAudioCtx = {
    sampleRate: 48000,
    currentTime: 0,
    destination: {},
    createOscillator: () => ({ connect() {}, start() {}, stop() {}, frequency: { value: 0 }, type: 'sine' }),
    createAnalyser: () => ({
        connect() {}, disconnect() {}, fftSize: 2048, frequencyBinCount: 1024,
        getFloatFrequencyData(arr) {
            for (let i = 0; i < arr.length; i++) {
                const f = i / arr.length;
                arr[i] = -120 + 90 * Math.exp(-3 * f) + 6 * Math.sin(i * 12.9898) * Math.cos(i * 78.233);
            }
        },
        getByteFrequencyData(arr) {
            for (let i = 0; i < arr.length; i++) {
                const f = i / arr.length;
                arr[i] = Math.max(0, Math.min(255, Math.round(255 * Math.exp(-2.2 * f) + 8 * Math.sin(i * 0.21))));
            }
        },
    }),
    createGain: () => ({ connect() {}, gain: { value: 1 } }),
    createScriptProcessor: () => ({ connect() {}, disconnect() {}, onaudioprocess: null }),
    createBuffer: () => ({ getChannelData: () => new Float32Array(48000) }),
    resume: () => Promise.resolve(), close: () => Promise.resolve(), state: 'running',
};
if (!skip.has('audio')) {
    if (typeof win.AudioContext !== 'function') win.AudioContext = function () { return fakeAudioCtx; };
    if (typeof win.webkitAudioContext !== 'function') win.webkitAudioContext = win.AudioContext;
    if (typeof win.OfflineAudioContext !== 'function') win.OfflineAudioContext = function () { return fakeAudioCtx; };
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
if (!skip.has('webgl')) {
    win.HTMLCanvasElement.prototype.getContext = function (type) {
        if (type === 'webgl' || type === 'experimental-webgl') return fakeWebGL;
        return origGetContext2.call(this, type);
    };
}

try { polyfill.install(win); } catch (e) {}

if (input.trace) {
    (function () {
        const seen = {};
        const snap = {};
        const cap = (v) => {
            if (v === null) return 'null';
            const t = typeof v;
            if (t === 'number') return 'num:' + v;
            if (t === 'string') return 'str:' + v.slice(0, 60);
            if (t === 'boolean') return 'bool:' + v;
            if (t === 'function') return 'fn';
            if (t === 'undefined') return 'undef';
            if (Array.isArray(v)) return 'arr:' + v.length;
            if (t === 'object') return 'obj:' + (Object.getOwnPropertyNames(v).slice(0, 4).join(','));
            return '?';
        };
        function hook(obj, name, keys) {
            if (!obj) return;
            for (const key of keys) {
                try {
                    const d = Object.getOwnPropertyDescriptor(obj, key);
                    const pd = Object.getPrototypeOf(obj) ? Object.getOwnPropertyDescriptor(Object.getPrototypeOf(obj), key) : null;
                    const desc = d || pd;
                    if (!desc) continue;
                    if (desc.get) {
                        if (d && !d.configurable) continue;
                        let v0;
                        try { v0 = desc.get.call(obj); } catch (e) { v0 = undefined; }
                        snap[name + '.' + key] = cap(v0);
                        Object.defineProperty(obj, key, {
                            get() { const k = name + '.' + key; if (!seen[k]) seen[k] = new Set(); seen[k].add(snap[k]); const r = desc.get.call(this); return r; },
                            configurable: true,
                        });
                    }
                } catch (e) {}
            }
        }
        function allKeys(o) {
            const s = new Set();
            let cur = o;
            while (cur) {
                try { Object.getOwnPropertyNames(cur).forEach((k) => s.add(k)); } catch (e) {}
                cur = Object.getPrototypeOf(cur);
            }
            return Array.from(s);
        }
        hook(win.navigator, 'navigator', allKeys(win.navigator));
        if (win.screen) hook(win.screen, 'screen', allKeys(win.screen));
        if (win.performance) hook(win.performance, 'performance', allKeys(win.performance));
        try {
            const origInst = win.WebAssembly.instantiate.bind(win.WebAssembly);
            const origInstSync = win.WebAssembly.instantiateStreaming ? win.WebAssembly.instantiateStreaming.bind(win.WebAssembly) : null;
            win.__wasmSizes = [];
            win.WebAssembly.instantiate = function (...args) {
                let sz = 0;
                try {
                    const s = args[0];
                    sz = (s && s.byteLength) || (s && s.buffer && s.buffer.byteLength) || 0;
                } catch (e) {}
                const r = origInst(...args);
                Promise.resolve(r).then((res) => {
                    win.__wasmSizes.push(sz || ((res && res.instance) ? 1 : 0));
                    if (sz > 100000) { win.__wasmOK = true; win.__wasmExportsCount = Object.keys(res.instance.exports || {}).length; }
                }).catch(() => {});
                return r;
            };
            if (origInstSync) {
                win.WebAssembly.instantiateStreaming = async function (...args) {
                    const r = await origInstSync(...args);
                    if (r && r.instance) { win.__wasmSizes.push(600000); win.__wasmOK = true; win.__wasmExportsCount = Object.keys(r.instance.exports || {}).length; }
                    return r;
                };
            }
        } catch (e) {}
        win.__trace = () => {
            const out = {};
            for (const k of Object.keys(seen)) out[k] = Array.from(seen[k]);
            return out;
        };
    })();
}

const vm = require('vm');
// vm.createContext(win) 时 jsdom 的 window/document/location 是 configurable:false
// 且原型 getter 不进 vm 全局解析 -> "window is not defined"。
// 方案: 新建全局对象 g, 把浏览器全局逐一读值挂为数据属性, 再 createContext(g)。
(function buildVmGlobal() {
    const g = {};
    const keys = [
        'navigator', 'location', 'document', 'screen', 'performance', 'devicePixelRatio',
        'innerWidth', 'innerHeight', 'scrollX', 'scrollY', 'crypto', 'origin',
        'isSecureContext', 'clientInformation', 'history', 'localStorage', 'sessionStorage',
        'visualViewport', 'CSS', 'customElements', 'frames', 'opener', 'name',
        'closed', 'status', 'length', 'alert', 'confirm', 'prompt',
        'requestAnimationFrame', 'cancelAnimationFrame', 'fetch',
        'setTimeout', 'clearTimeout', 'setInterval', 'clearInterval',
        'atob', 'btoa', 'Image', 'Audio', 'Blob', 'File', 'FormData',
        'XMLHttpRequest', 'WebSocket', 'URL', 'URLSearchParams',
        'TextEncoder', 'TextDecoder', 'caches', 'indexedDB', 'console',
    ];
    for (const k of keys) {
        try {
            let v = win[k];
            if (k === 'window' || k === 'self' || k === 'top' || k === 'parent') v = win;
            Object.defineProperty(g, k, { value: v, configurable: true, writable: true, enumerable: true });
        } catch (_) {}
    }
    g.globalThis = g;
    g.window = win;
    g.self = win;
    g.top = win;
    g.parent = win;
    g.__win = win;
    global.__VM_GLOBAL__ = g;
})();
const ctx = vm.createContext(global.__VM_GLOBAL__ || win);

if (patchedWasmB64 && hookJS) {
    win.__patched_wasm_b64 = patchedWasmB64;
    try {
        vm.runInContext(hookJS, ctx, { timeout: 5000 });
    } catch (e) {}
}

try {
    vm.runInContext(hswCode, ctx, { timeout: 60000 });
} catch (e) {
    process.stdout.write(JSON.stringify({ ok: false, error: 'load: ' + String(e && (e.stack || e)) }));
    process.exit(1);
}
(async () => {
    const t0 = Date.now();
    let n = '';
    try {
        await new Promise((r) => setTimeout(r, 80));
        const isFp = !!fpB64;
        let gateOn = false;
        if (rings.length) {
            try { await Promise.race([
                Promise.resolve(win.hsw(1, new Uint8Array(0))).catch(() => {}),
                new Promise((r2) => setTimeout(r2, 15000)),
            ]); } catch (e) {}
            try {
                win.__hsw_exports.__poke32(200256, 1);
                gateOn = true;
            } catch (e) {}
        }
        try {
            n = String(await Promise.race([
                Promise.resolve(win.hsw(req, isFp ? fpB64 : undefined)).then((x) => String(x)),
                new Promise((_, rej) => setTimeout(() => rej(new Error('hsw pow timeout')), 60000)),
            ]));
        } finally {
            if (gateOn) { try { win.__hsw_exports.__poke32(200256, 0); } catch (e) {} }
        }
        await new Promise((r) => setTimeout(r, 300));
        const us = win.__usCapture || 'NO_CAPTURE';
        const trace = input.trace && win.__trace ? win.__trace() : null;
        const ringsOut = {};
        try {
            if (rings.length && win.__hsw_exports) {
                const e = win.__hsw_exports;
                for (const rn of rings) {
                    const cnt = e.__peek32(rn.counter) || 0;
                    const recs = [];
                    const nRecs = Math.min(cnt, rn.max || 256);
                    for (let i = 0; i < nRecs; i++) {
                        const base = rn.buf + i * (rn.recSize || 36);
                        let hex = '';
                        for (let j = 0; j < (rn.dumpBytes || 32); j++) hex += e.__peek8(base + 4 + j).toString(16).padStart(2, '0');
                        recs.push(hex);
                    }
                    ringsOut[rn.name] = { count: cnt, recs };
                }
            }
        } catch (e) { ringsOut._err = String(e); }
        process.stdout.write(JSON.stringify({ ok: true, isFp, n_len: n.length, us_len: us.length, n: n, us: us.slice(0, 2000), ms: Date.now() - t0, trace, rings: ringsOut,
            importLogs: win.__import_logs || null,
            wasmOK: win.__wasmOK, wasmErr: win.__wasmErr, wasmSizes: win.__wasmSizes, wasmExportsCount: win.__wasmExportsCount, wasmKeyExports: win.__wasmKeyExports }));
    } catch (e) {
        process.stdout.write(JSON.stringify({ ok: false, isFp: !!fpB64, error: String(e && (e.stack || e)), n_len: n.length, us: win.__usCapture || '' }));
        process.exit(1);
    }
    process.exit(0);
})();