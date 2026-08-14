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

// ===== G-BUILD: 精确 chrome 序虚拟全局对象 =====
// hsw 的 PJ(2504291356)= getOwnPropertyNames(window) 原生序, 而 jsdom window 原生序(x86 内部属性插入
// 序 + non-config 锚点)无法与 Chrome IDL 序对齐。解法: 从 win 拷贝描述符, 按 chrome 985 键序构建全新
// 对象 G, G 的 getOwnPropertyNames == chrome 键序; 7 徽章按 chrome 语义追加到尾 50 进 Kv/nn。
const CHROME_WIN_KEYS = (() => {
    const p = input.chromeKeysPath || path.join(__dirname, 'chrome_window_keys.json');
    const raw = fs.readFileSync(p, 'utf8');
    return JSON.parse(raw.replace(/^\uFEFF/, ''));
})();
function nativeToString(fn, name) {
    try { Object.defineProperty(fn, 'toString', { value: () => 'function ' + name + '() { [native code] }', configurable: true }); } catch (e) {}
    return fn;
}
function alignWindowKeys(win, keys) {
    const proto = Object.getPrototypeOf(win);
    const own = Object.getOwnPropertyNames(win);
    const saved = {};
    for (const k of own) {
        const d = Object.getOwnPropertyDescriptor(win, k);
        if (!d) continue;
        if (keys.indexOf(k) === -1) { try { Object.defineProperty(proto, k, d); } catch (e) {} continue; }
        if (d.value && typeof d.value === 'function') nativeToString(d.value, d.value.name);
        if (d.get && typeof d.get === 'function') nativeToString(d.get, d.get.name);
        if (d.set && typeof d.set === 'function') nativeToString(d.set, d.set.name);
        saved[k] = d;
    }
    for (const k of own) { if (keys.indexOf(k) === -1) { try { delete win[k]; } catch (e) {} } }
    for (const k of keys) {
        if (saved[k]) { try { Object.defineProperty(win, k, saved[k]); } catch (e) {} }
        else if (!(k in win)) {
            const f = function () {};
            try { Object.defineProperty(f, 'name', { value: k, configurable: true }); } catch (e) {}
            nativeToString(f, k);
            Object.defineProperty(win, k, { value: f, writable: true, enumerable: false, configurable: true });
        }
    }
    return {};
}
function ownDesc(o, k) { try { return Object.getOwnPropertyDescriptor(o, k); } catch (e) { return null; } }
function alignDocumentG(doc) {
    const own = Object.getOwnPropertyNames(doc);
    for (const k of own) {
        const d = Object.getOwnPropertyDescriptor(doc, k);
        if (!d) continue;
        if (d.value && typeof d.value === 'function') { nativeToString(d.value, d.value.name); continue; }
        if (d.get) { nativeToString(d.get, d.get.name); continue; }
        const v = d.value;
        const g = function () { return v; };
        Object.defineProperty(g, 'name', { value: 'get ' + k, configurable: true });
        nativeToString(g, 'get ' + k);
        try { Object.defineProperty(doc, k, { get: g, enumerable: d.enumerable, configurable: true }); } catch (e) {}
    }
}
function buildGlobalG(win, keys) {
    const badges = ['__patched_wasm_b64', '__GOwrap', '__AGLOG', 'hsw', '__hsw_exports', '__hsw_memory', '__caps'];
    const badgeSet = new Set(badges);
    const SELF = new Set(['window', 'top', 'self', 'parent', 'frames', 'opener', 'globalThis']);
    const G = {};
    // 关键: 所有键 enumerable:true, 否则 vm.createContext 拷贝时会丢失属性。
    // vm 沙箱内 globalThis 只暴露 enumerable 属性 (实测全 enum Gown=992 -> vm len=993)。
    for (const k of keys) {
        if (badgeSet.has(k)) continue;
        let d = ownDesc(win, k);
        if (k in win && !d) d = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(win), k);
        if (d) {
            if (SELF.has(k)) {
                // Chrome: window/top/self/parent/frames/opener/globalThis 均 enumerable:false
                try { Object.defineProperty(G, k, { value: G, writable: true, enumerable: false, configurable: true }); } catch (e) {}
            } else if (d.get || d.set) {
                try { Object.defineProperty(G, k, { get: d.get, set: d.set, enumerable: false, configurable: true }); } catch (e) { console.error('fail acc', k, String(e)); }
            } else {
                try { Object.defineProperty(G, k, { value: d.value, writable: !!d.writable, enumerable: false, configurable: true }); } catch (e) { console.error('fail val', k, String(e)); }
            }
        } else {
            const f = function () {};
            try { Object.defineProperty(f, 'name', { value: k, configurable: true }); } catch (e) {}
            nativeToString(f, k);
            Object.defineProperty(G, k, { value: f, writable: true, enumerable: false, configurable: true });
        }
    }
    for (const k of badges) {
        const d = ownDesc(win, k);
        if (d) {
            try { Object.defineProperty(G, k, { value: d.value, writable: true, enumerable: true, configurable: true }); } catch (e) {}
        } else {
            const f = function () {}; nativeToString(f, k);
            Object.defineProperty(G, k, { value: f, writable: true, enumerable: true, configurable: true });
        }
    }
    for (const k of ['location']) {
        if (keys.indexOf(k) >= 0 && k in win) {
            const d = ownDesc(win, k);
            if (d) { try { Object.defineProperty(G, k, { get: d.get, set: d.set, enumerable: false, configurable: true }); } catch (e) {} }
        }
    }
    return G;
}
try {
    alignWindowKeys(win, CHROME_WIN_KEYS);
    // 徽章: 先给 win 建好, 由 buildGlobalG 拷贝 (hsw 内部可能自己建, 但先占位 harmless)
    for (const k of ['__patched_wasm_b64', '__GOwrap', '__AGLOG', '__hsw_exports', '__hsw_memory', '__caps']) {
        if (!(k in win)) win[k] = k === '__AGLOG' ? [] : (k === '__GOwrap' ? {} : (k === '__patched_wasm_b64' ? patchedWasmB64 : {}));
    }
    alignDocumentG(win.document);
    const G = buildGlobalG(win, CHROME_WIN_KEYS);
    G.__import_logs = win.__import_logs || null;
    const ctx = vm.createContext(G);
    try { vm.runInContext('delete globalThis.SharedArrayBuffer', ctx); } catch (e) {}
    // 全 enum G 构建后, vm 内把 chrome985 改回 enumerable:false (属性仍在, gOPN 序不变);
    // 使 Object.keys(window) 仅剩 7 徽章+少量, ds 的尾50 过滤不再把大写接口收集进 Kv。
    try {
        vm.runInContext(`(function(){
            var chromeKeys = ${JSON.stringify(CHROME_WIN_KEYS)};
            var badges = ${JSON.stringify(['__patched_wasm_b64', '__GOwrap', '__AGLOG', 'hsw', '__hsw_exports', '__hsw_memory', '__caps'])};
            var badgeSet = {}; badges.forEach(function(b){ badgeSet[b]=1; });
            chromeKeys.forEach(function(k){
                try {
                    var d = Object.getOwnPropertyDescriptor(window, k);
                    if (d && badgeSet[k] !== 1) {
                        var nd = { enumerable: false };
                        if (d.get || d.set) { if (d.get) nd.get = d.get; if (d.set) nd.set = d.set; }
                        else { nd.value = d.value; nd.writable = d.writable === undefined ? true : d.writable; }
                        nd.configurable = d.configurable === undefined ? true : d.configurable;
                        Object.defineProperty(window, k, nd);
                    }
                } catch (e) {}
            });
        })()`, ctx, { timeout: 5000 });
    } catch (e) {}
    if (patchedWasmB64 && hookJS) {
        win.__patched_wasm_b64 = patchedWasmB64;
        try { vm.runInContext(hookJS, ctx, { timeout: 5000 }); } catch (e) {}
    }
    vm.runInContext(hswCode, ctx, { timeout: 60000 });
    // hsw 在 G 上暴露接口
    win.__hsw = G.hsw;
    win.__hsw_exports = G.__hsw_exports;
    win.hsw = G.hsw;
    win.__usCapture = G.__usCapture;
    (async () => {
        const t0 = Date.now();
        let n = '';
        try {
            await new Promise((r) => setTimeout(r, 80));
            const isFp = !!fpB64;
            let gateOn = false;
            if (rings.length) {
                try { await Promise.race([
                    Promise.resolve(G.hsw(1, new Uint8Array(0))).catch(() => {}),
                    new Promise((r2) => setTimeout(r2, 15000)),
                ]); } catch (e) {}
                try { G.__hsw_exports.__poke32(200256, 1); gateOn = true; } catch (e) {}
            }
            try {
                if (input.decN) {
                    const s = String(input.decN);
                    const bytes = new Uint8Array(s.length);
                    for (let i = 0; i < s.length; i++) bytes[i] = s.charCodeAt(i) & 0xff;
                    let out;
                    try {
                        out = await Promise.race([
                            Promise.resolve(G.hsw(0, bytes)).then((x) => x instanceof Uint8Array ? String.fromCharCode.apply(null, Array.prototype.slice.call(x, 0, Math.min(x.length, 500000))) : String(x)),
                            new Promise((_, rej) => setTimeout(() => rej(new Error('dec timeout')), 30000)),
                        ]);
                        process.stdout.write(JSON.stringify({ ok: true, dec: String(out), dec_len: String(out).length, ms: Date.now() - t0 }));
                    } catch (e) {
                        process.stdout.write(JSON.stringify({ ok: false, decErr: String(e && (e.stack || e.message || e)), ms: Date.now() - t0 }));
                    }
                    return;
                }
                n = String(await Promise.race([
                    Promise.resolve(G.hsw(req, isFp ? fpB64 : undefined)).then((x) => String(x)),
                    new Promise((_, rej) => setTimeout(() => rej(new Error('hsw pow timeout')), 60000)),
                ]));
            } finally {
                if (gateOn) { try { G.__hsw_exports.__poke32(200256, 0); } catch (e) {} }
            }
            await new Promise((r) => setTimeout(r, 300));
            const us = win.__usCapture || G.__usCapture || 'NO_CAPTURE';
            const trace = input.trace && win.__trace ? win.__trace() : null;
            const ringsOut = {};
            try {
                if (rings.length && G.__hsw_exports) {
                    const e = G.__hsw_exports;
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
                caps: G.__caps || null,
                aglogKeys: G.__AGLOG ? (Array.isArray(G.__AGLOG) ? null : Object.keys(G.__AGLOG)) : null,
                wasmOK: win.__wasmOK, wasmErr: win.__wasmErr, wasmSizes: win.__wasmSizes, wasmExportsCount: win.__wasmExportsCount, wasmKeyExports: win.__wasmKeyExports }));
        } catch (e) {
            process.stdout.write(JSON.stringify({ ok: false, isFp: !!fpB64, error: String(e && (e.stack || e)), n_len: n.length, us: win.__usCapture || '' }));
            process.exit(1);
        }
        process.exit(0);
    })();
} catch (e) {
    process.stdout.write(JSON.stringify({ ok: false, error: 'gbuild: ' + String(e && (e.stack || e)) }));
    process.exit(1);
}