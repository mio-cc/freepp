#!/usr/bin/env node
/**
 * jsdom + sandbox_polyfill 一体化 mint runner:
 *  1) hsw(req) -> n (PoW)
 *  2) hsw(1, encode(body)) -> encrypt_req_data (ExtType-18)
 *  3) msgpack 打包 [[cJSON, ext18]] 输出 packed_b64
 * 完全替代 happy-dom 两段式，统一环境。
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
const hswCode = fs.readFileSync(input.hswPath || path.join(ROOT, '_hsw_semi.js'), 'utf8');
const UA0 = input.userAgent || 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36';

const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>', {
    url: 'https://newassets.hcaptcha.com/',
    pretendToBeVisual: true,
    runScripts: 'outside-only',
});
const win = dom.window;

win.HTMLCanvasElement.prototype.getContext = function (type) {
    const cv = createCanvas(this.width || 300, this.height || 150);
    if (type === '2d' || !type) {
        return cv.getContext('2d');
    }
    return cv.getContext(type);
};
win.HTMLCanvasElement.prototype.toDataURL = function (type, quality) {
    const cv = createCanvas(this.width || 300, this.height || 150);
    try { return cv.toDataURL(type, quality); } catch (e) { return ''; }
};
win.HTMLCanvasElement.prototype.toBlob = function (cb, type, quality) {
    const cv = createCanvas(this.width || 300, this.height || 150);
    try { cv.toBuffer(type === 'image/jpeg' ? 'image/jpeg' : 'image/png', quality); cb(new win.Blob([cv.toBuffer(type === 'image/jpeg' ? 'image/jpeg' : 'image/png')], { type: type || 'image/png' })); } catch (e) { cb(null); }
};
win.HTMLCanvasElement.prototype.getImageData = function (sx, sy, sw, sh) {
    const cv = createCanvas(this.width || 300, this.height || 150);
    try { return cv.getContext('2d').getImageData(sx, sy, sw, sh); } catch (e) { return null; }
};

win.WebAssembly = WebAssembly;
win.globalThis = win;
win.self = win;
win.global = win;
try {
    Object.defineProperty(win, 'crypto', { value: nodeCrypto.webcrypto, writable: true, configurable: true });
} catch (_) {}
win.atob = (s) => Buffer.from(s, 'base64').toString('binary');
win.btoa = (s) => Buffer.from(s, 'binary').toString('base64');

Object.defineProperty(win.navigator, 'userAgent', { get: () => UA0, configurable: true });
Object.defineProperty(win.navigator, 'platform', { get: () => 'Win32', configurable: true });
Object.defineProperty(win.navigator, 'appVersion', { get: () => UA0.replace('Mozilla/', ''), configurable: true });
Object.defineProperty(win.navigator, 'appName', { get: () => 'Netscape', configurable: true });
Object.defineProperty(win.navigator, 'product', { get: () => 'Gecko', configurable: true });
Object.defineProperty(win.navigator, 'appCodeName', { get: () => 'Mozilla', configurable: true });
try { Object.defineProperty(win.navigator, 'pdfViewerEnabled', { get: () => true, configurable: true }); } catch (_) {}
Object.defineProperty(win.navigator, 'deviceMemory', { get: () => 8, configurable: true });
Object.defineProperty(win.navigator, 'hardwareConcurrency', { get: () => 8, configurable: true });
Object.defineProperty(win.navigator, 'language', { get: () => 'zh-CN', configurable: true });
Object.defineProperty(win.navigator, 'languages', { get: () => ['zh-CN', 'zh'], configurable: true });
Object.defineProperty(win.navigator, 'webdriver', { get: () => false, configurable: true });
Object.defineProperty(win.navigator, 'vendor', { get: () => 'Google Inc.', configurable: true });
try {
    Object.defineProperty(win.navigator, 'plugins', { get: () => [
        { name: 'PDF Viewer', filename: 'internal-pdf-viewer' },
        { name: 'Chrome PDF Viewer', filename: 'internal-pdf-viewer' },
        { name: 'Chromium PDF Viewer', filename: 'internal-pdf-viewer' },
        { name: 'Microsoft Edge PDF Viewer', filename: 'internal-pdf-viewer' },
        { name: 'WebKit built-in PDF', filename: 'internal-pdf-viewer' },
    ], configurable: true });
} catch (_) {}
try {
    Object.defineProperty(win.navigator, 'mimeTypes', { get: () => { const m = { length: 2 }; return m; }, configurable: true });
} catch (_) {}
try {
    Object.defineProperty(win.navigator, 'productSub', { get: () => '20030107', configurable: true });
} catch (_) {}
try {
    Object.defineProperty(win.navigator, 'userAgentData', { get: () => ({
        brands: [
            { brand: 'Not=A?Brand', version: '99' },
            { brand: 'Google Chrome', version: '151' },
            { brand: 'Chromium', version: '151' },
        ],
        mobile: false,
        platform: 'Windows',
        getHighEntropyValues: (hints) => Promise.resolve({
            architecture: 'x86', bitness: '64', model: '', platformVersion: '10.0.0',
            uaFullVersion: '151.0.7922.72', fullVersionList: [
                { brand: 'Not=A?Brand', version: '99.0.0.0' },
                { brand: 'Google Chrome', version: '151.0.7922.72' },
                { brand: 'Chromium', version: '151.0.7922.72' },
            ], wow64: false,
        }),
        toJSON: () => ({ brands: [
            { brand: 'Not=A?Brand', version: '99' },
            { brand: 'Google Chrome', version: '151' },
            { brand: 'Chromium', version: '151' },
        ], mobile: false, platform: 'Windows' }),
    }), configurable: true });
} catch (_) {}

Object.defineProperty(win.screen, 'width', { get: () => 1536, configurable: true });
Object.defineProperty(win.screen, 'height', { get: () => 864, configurable: true });
Object.defineProperty(win.screen, 'availWidth', { get: () => 1536, configurable: true });
Object.defineProperty(win.screen, 'availHeight', { get: () => 824, configurable: true });
Object.defineProperty(win.screen, 'colorDepth', { get: () => 24, configurable: true });
Object.defineProperty(win.screen, 'pixelDepth', { get: () => 24, configurable: true });
Object.defineProperty(win, 'devicePixelRatio', { get: () => 1.25, configurable: true });
Object.defineProperty(win, 'innerWidth', { get: () => 1540, configurable: true });
Object.defineProperty(win, 'innerHeight', { get: () => 788, configurable: true });
Object.defineProperty(win, 'outerWidth', { get: () => 1554, configurable: true });
Object.defineProperty(win, 'outerHeight', { get: () => 882, configurable: true });
Object.defineProperty(win, 'screenX', { get: () => 10, configurable: true });
Object.defineProperty(win, 'screenY', { get: () => 10, configurable: true });
Object.defineProperty(win, 'screenLeft', { get: () => 10, configurable: true });
Object.defineProperty(win, 'screenTop', { get: () => 10, configurable: true });

const fakeAudioCtx = {
    sampleRate: 48000,
    currentTime: 0,
    destination: {},
    createOscillator: () => ({ connect() {}, start() {}, stop() {}, frequency: { value: 0 }, type: 'sine' }),
    createAnalyser: () => ({
        connect() {}, disconnect() {}, getFloatFrequencyData(arr) { arr.fill(-127); }, getByteFrequencyData(arr) { arr.fill(0); }, fftSize: 2048, frequencyBinCount: 1024,
    }),
    createGain: () => ({ connect() {}, gain: { value: 1 } }),
    createScriptProcessor: () => ({ connect() {}, disconnect() {}, onaudioprocess: null }),
    createBuffer: () => ({ getChannelData: () => new Float32Array(48000) }),
    resume: () => Promise.resolve(),
    close: () => Promise.resolve(),
    state: 'running',
};
if (typeof win.AudioContext !== 'function') win.AudioContext = function () { return fakeAudioCtx; };
if (typeof win.webkitAudioContext !== 'function') win.webkitAudioContext = win.AudioContext;
if (typeof win.OfflineAudioContext !== 'function') win.OfflineAudioContext = function () { return fakeAudioCtx; };

try { polyfill.install(win); } catch (e) {}
try { Object.defineProperty(win.document, 'characterSet', { get: () => 'windows-1252', configurable: true }); } catch (_) {}
try { Object.defineProperty(win.document, 'compatMode', { get: () => 'BackCompat', configurable: true }); } catch (_) {}
try { Object.defineProperty(win.document, 'domain', { get: () => 'newassets.hcaptcha.com', configurable: true }); } catch (_) {}
try {
    if (!win.document.all) {
        Object.defineProperty(win.document, 'all', { get: () => win.document.querySelectorAll('*'), configurable: true });
    }
} catch (_) {}
try { Object.defineProperty(win.document, 'currentScript', { get: () => null, configurable: true }); } catch (_) {}
try { Object.defineProperty(win.document, 'fullscreenElement', { get: () => null, configurable: true }); } catch (_) {}
try { Object.defineProperty(win.document, 'pictureInPictureElement', { get: () => null, configurable: true }); } catch (_) {}
try { Object.defineProperty(win.document, 'pointerLockElement', { get: () => null, configurable: true }); } catch (_) {}
try { Object.defineProperty(win.document, 'scrollingElement', { get: () => win.document.body, configurable: true }); } catch (_) {}
try { Object.defineProperty(win.document, 'font', { get: () => ({ check: () => true, load: () => Promise.resolve([]) }), configurable: true }); } catch (_) {}

win.__unhandled = [];
process.on('uncaughtException', (e) => {
    try { win.__unhandled.push('uncaught: ' + (e && e.message ? e.message : String(e))); } catch (_) {}
});
process.on('unhandledRejection', (r) => {
    try { win.__unhandled.push('rejection: ' + (r && r.message ? r.message : String(r))); } catch (_) {}
});

const vm = require('vm');
const ctx = vm.createContext(win);

// ---- fingerprint reads snapshot (after polyfill, before hsw) ----
const reads = { navigator: {}, screen: {}, window: {}, document: {}, perf: {} };
function track(target, cat, keys) {
    const rec = reads[cat];
    for (const k of keys) {
        if (!(k in target)) { rec[k] = '<missing>'; continue; }
        try {
            let v = target[k];
            if (typeof v === 'function') { rec[k] = '<fn>'; continue; }
            if (v && typeof v === 'object') { rec[k] = '<obj:' + (v.constructor ? v.constructor.name : '?') + '>'; continue; }
            rec[k] = String(v);
        } catch (e) { rec[k] = '<err:' + e.message + '>'; }
    }
}
const NAV_KEYS = ['userAgent', 'platform', 'deviceMemory', 'hardwareConcurrency', 'language', 'languages', 'webdriver', 'vendor', 'maxTouchPoints', 'cookieEnabled', 'onLine', 'plugins', 'mimeTypes', 'userAgentData', 'connection', 'keyboard', 'storage', 'credentials', 'serial', 'scheduling', 'appVersion', 'appName', 'product', 'productSub', 'appCodeName', 'pdfViewerEnabled'];
const SCR_KEYS = ['width', 'height', 'availWidth', 'availHeight', 'colorDepth', 'pixelDepth', 'orientation', 'availLeft', 'availTop', 'left', 'top'];
const WIN_KEYS = ['devicePixelRatio', 'innerWidth', 'innerHeight', 'outerWidth', 'outerHeight', 'screenX', 'screenY', 'screenLeft', 'screenTop', 'scrollX', 'scrollY', 'pageXOffset', 'pageYOffset', 'location', 'history', 'sessionStorage', 'localStorage', 'origin', 'isSecureContext', 'crossOriginIsolated', 'visualViewport', 'matchMedia', 'getComputedStyle', 'requestAnimationFrame', 'cancelAnimationFrame', 'setTimeout', 'setInterval', 'addEventListener', 'removeEventListener', 'dispatchEvent', 'fetch', 'XMLHttpRequest', 'WebSocket', 'indexedDB', 'caches', 'navigation', 'clientInformation', 'netscape', 'chrome', 'performance', 'navigator', 'screen', 'document', 'top', 'parent', 'opener', 'frames', 'self', 'frameElement', 'length', 'closed', 'customElements', 'MutationObserver', 'ResizeObserver', 'IntersectionObserver', 'FontFace', 'FontFaceSet', 'AudioContext', 'OfflineAudioContext', 'WebGLRenderingContext', 'WebGL2RenderingContext', 'CanvasRenderingContext2D', 'ImageData', 'speechSynthesis'];
const DOC_KEYS = ['hidden', 'visibilityState', 'readyState', 'documentElement', 'body', 'cookie', 'referrer', 'characterSet', 'compatMode', 'title', 'URL', 'domain', 'location', 'fonts', 'activeElement', 'fullscreenElement', 'pictureInPictureElement', 'currentScript', 'hasFocus', 'doctype', 'createElement', 'getElementById', 'querySelector', 'querySelectorAll', 'getElementsByTagName', 'addEventListener', 'removeEventListener', 'pointerLockElement', 'scrollingElement', 'elementFromPoint', 'caretPositionFromPoint', 'styleSheets', 'head', 'contentType', 'lastModified', 'images', 'scripts', 'links', 'forms', 'applets', 'embeds', 'plugins', 'all'];
const PERF_KEYS = ['now', 'timing', 'navigation', 'memory', 'getEntries', 'getEntriesByType', 'getEntriesByName', 'timeOrigin', 'eventCounts', 'mark', 'measure', 'clearMarks', 'clearMeasures'];
track(win.navigator, 'navigator', NAV_KEYS);
track(win.screen, 'screen', SCR_KEYS);
track(win, 'window', WIN_KEYS);
track(win.document, 'document', DOC_KEYS);
track(win.performance, 'perf', PERF_KEYS);
try {
    const uad = win.navigator.userAgentData;
    reads.uad = {};
    for (const k of ['brands', 'mobile', 'platform', 'getHighEntropyValues', 'toJSON']) {
        try {
            let v = uad[k];
            if (typeof v === 'function') { reads.uad[k] = '<fn>'; continue; }
            reads.uad[k] = JSON.stringify(v);
        } catch (e) { reads.uad[k] = '<err>'; }
    }
} catch (e) { reads.uad = '<err:' + e.message + '>'; }
try { reads.pluginsList = Array.from(win.navigator.plugins || []).map((p) => p.name + '|' + p.filename); } catch (e) { reads.pluginsList = '<err>'; }
try { reads.mimeTypesCount = (win.navigator.mimeTypes || { length: -1 }).length; } catch (e) { reads.mimeTypesCount = '<err>'; }
try { reads.conn = JSON.stringify(win.navigator.connection || {}); } catch (e) { reads.conn = '<err>'; }
try { reads.tz = new Date().getTimezoneOffset(); } catch (e) {}
try {
    reads.webgl = (() => { const c = win.document.createElement('canvas'); c.width = 128; c.height = 128; const gl = c.getContext('webgl'); if (!gl) return '<no-webgl>'; const dbg = gl.getExtension('WEBGL_debug_renderer_info'); return { unmasked: gl.getParameter(dbg ? dbg.UNMASKED_RENDERER_WEBGL : gl.RENDERER), vendor: gl.getParameter(dbg ? dbg.UNMASKED_VENDOR_WEBGL : gl.VENDOR), maxTex: gl.getParameter(gl.MAX_TEXTURE_SIZE), maxAA: typeof gl.MAX_SAMPLES !== 'undefined' ? gl.getParameter(gl.MAX_SAMPLES) : -1 }; })();
} catch (e) { reads.webgl = '<err:' + e.message + '>'; }

try {
    vm.runInContext(hswCode, ctx, { timeout: 60000 });
} catch (e) {
    process.stdout.write(JSON.stringify({ ok: false, error: 'load: ' + String(e && (e.stack || e)) }));
    process.exit(1);
}

const mp = require('@msgpack/msgpack');
const encode = mp.encode.bind(mp);
const ExtData = mp.ExtData;

function asExt18(enc) {
    const buf = Buffer.isBuffer(enc) ? enc : Buffer.from(enc);
    return new ExtData(18, Uint8Array.from(buf));
}

(async () => {
    const t0 = Date.now();
    let n = '';
    let packed_b64 = '';
    let encSource = '';
    try {
        await new Promise((r) => setTimeout(r, 80));
        if (typeof win.hsw !== 'function') throw new Error('no hsw after load');
        n = String(await Promise.race([
            Promise.resolve(win.hsw(req)).then((x) => String(x)),
            new Promise((_, rej) => setTimeout(() => rej(new Error('hsw pow timeout')), 60000)),
        ]));

        const now = Date.now();
        const body = {
            v: input.v || '1',
            sitekey: input.sitekey,
            host: input.host,
            hl: input.hl || 'es',
            n: input.n || n,
            motionData: JSON.stringify({
                st: now - 1400,
                dct: now - 1300,
                mm: Array.from({ length: 100 }, (_, i) => [30 + i * 3, 50 + (i % 20), i * 12]),
                'mm-mp': 12.5,
                md: [[100, 80, 500]],
                mu: [[100, 80, 540]],
                topLevel: {
                    st: now - 2000,
                    sc: {
                        width: 1536,
                        height: 864,
                        availWidth: 1536,
                        availHeight: 864,
                        colorDepth: 24,
                    },
                    nv: {
                        userAgent: input.userAgent,
                        platform: 'Windows',
                        webdriver: false,
                        hardwareConcurrency: 8,
                        deviceMemory: 8,
                    },
                    dr: 'https://b.stripecdn.com/',
                    inv: true,
                    exec: true,
                },
                v: 1,
            }),
            pem: JSON.stringify({
                csc: 180,
                csch: 'api.hcaptcha.com',
                cscrt: 40,
                cscft: 200,
            }),
            pst: false,
            p_e: JSON.stringify({
                st: now - 2500,
                sc: { width: 1536, height: 864 },
                nv: {
                    userAgent: input.userAgent,
                    platform: 'Windows',
                    webdriver: false,
                },
                dr: '',
                inv: false,
                exec: false,
            }),
        };
        const enc = await Promise.race([
            Promise.resolve(win.hsw(1, encode(body))),
            new Promise((_, rej) => setTimeout(() => rej(new Error('hsw enc timeout')), 60000)),
        ]);
        const packed = encode([JSON.stringify(input.cObj), asExt18(enc)]);
        packed_b64 = Buffer.from(packed instanceof Uint8Array ? packed : new Uint8Array(packed)).toString('base64');
        encSource = enc && enc.constructor && enc.constructor.name || 'bytes';
    } catch (e) {
        process.stdout.write(JSON.stringify({ ok: false, error: String(e && (e.stack || e)), unhandled: win.__unhandled, n_len: n.length }));
        process.exit(1);
    }
    process.stdout.write(JSON.stringify({
        ok: true,
        n,
        n_len: n.length,
        packed_b64,
        packed_len: Buffer.from(packed_b64, 'base64').length,
        ms: Date.now() - t0,
        mode: 'jsdom_polyfill_unified',
        enc_source: encSource,
        unhandled: win.__unhandled,
        reads,
    }));
    process.exit(0);
})();