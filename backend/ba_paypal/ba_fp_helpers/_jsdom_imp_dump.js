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

const impDump = [];
const origInst = win.WebAssembly.instantiate;
win.WebAssembly.instantiate = function (buf, imp) {
    try {
        if (imp) {
            const mod = {};
            for (const mname in imp) {
                const m = imp[mname];
                if (m && typeof m === 'object') {
                    mod[mname] = {};
                    for (const fname in m) {
                        const fn = m[fname];
                        if (typeof fn === 'function') {
                            try { mod[mname][fname] = String(fn).slice(0, 4000); } catch (e) { mod[mname][fname] = '<strerr>'; }
                        } else {
                            mod[mname][fname] = 'NONFN:' + Object.prototype.toString.call(fn);
                        }
                    }
                }
            }
            impDump.push({ instCount: impDump.length, names: mod });
        }
    } catch (e) { impDump.push({ instCount: impDump.length, err: String(e).slice(0, 300) }); }
    return origInst.call(this, buf, imp);
};
if (win.WebAssembly.instantiateStreaming) {
    const ois = win.WebAssembly.instantiateStreaming;
    win.WebAssembly.instantiateStreaming = async function (source, imp) {
        const resp = await source;
        const buf = await resp.arrayBuffer();
        return win.WebAssembly.instantiate(buf, imp);
    };
}

const srcInjected = hswCode.replace(/\}\}\(\);\s*$/, '} ;globalThis.__aqo = aQo; globalThis.__aZS=aZS; globalThis.__ard=ard; globalThis.__wr=wr; globalThis.__ba$=ba$; globalThis.__sE=sE; globalThis.__aop=aop; globalThis.__Ld=Ld; globalThis.__ff=ff; globalThis.__aps=aps;}();');
vm.runInContext(srcInjected, ctx, { timeout: 60000 });
(async () => {
    try {
        await new Promise((r) => setTimeout(r, 80));
        try { await Promise.race([
            Promise.resolve(win.hsw(1, new Uint8Array(0))).then((x) => String(x)),
            new Promise((_, rej) => setTimeout(() => rej(new Error('warmup timeout')), 15000)),
        ]); } catch (e) {}
        const aqo = win.__aqo;
        const aqoVals = {};
        if (typeof aqo === 'function') {
            for (let n = 0; n < 2000; n++) {
                try {
                    const v = aqo(n);
                    if (v !== undefined && v !== null && v !== '') aqoVals[n] = typeof v === 'string' ? v : ('NUM:' + v);
                } catch (e) { }
            }
        }
        const helpers = {};
        for (const h of ['aZS', 'ard', 'wr', 'ba$', 'sE', 'aop', 'Ld', 'ff', 'aps']) {
            const v = win['__' + h];
            helpers[h] = typeof v === 'function' ? String(v).slice(0, 3000) : (typeof v === 'object' ? ('OBJ:' + JSON.stringify(v).slice(0, 2000)) : String(v));
        }
        const out = { ok: true, instDump: impDump, aqoVals, helpers };
        process.stdout.write(JSON.stringify(out) + '\n');
    } catch (e) {
        process.stdout.write(JSON.stringify({ ok: false, error: String(e && (e.stack || e)).slice(0, 400), instDump: impDump }) + '\n');
    }
    setTimeout(() => process.exit(0), 300);
})();
