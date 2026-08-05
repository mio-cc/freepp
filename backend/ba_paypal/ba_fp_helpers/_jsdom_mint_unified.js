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
const UA0 = input.userAgent || 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36';

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
Object.defineProperty(win.navigator, 'language', { get: () => 'zh-CN', configurable: true });
Object.defineProperty(win.navigator, 'languages', { get: () => ['zh-CN', 'zh'], configurable: true });
Object.defineProperty(win.navigator, 'webdriver', { get: () => false, configurable: true });
Object.defineProperty(win.navigator, 'vendor', { get: () => 'Google Inc.', configurable: true });
try {
    Object.defineProperty(win.navigator, 'plugins', { get: () => [
        { name: 'PDF Viewer', filename: 'internal-pdf-viewer' },
        { name: 'Chrome PDF Viewer', filename: 'chrome-pdf-viewer' },
        { name: 'Chromium PDF Viewer', filename: 'chromium-pdf-viewer' },
        { name: 'Microsoft Edge PDF Viewer', filename: 'ms-pdf-viewer' },
        { name: 'WebKit built-in PDF', filename: 'webkit-pdf-viewer' },
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
            architecture: 'x86', bitness: '64', model: '', platformVersion: '15.0.0',
            uaFullVersion: '151.0.0.0', fullVersionList: [
                { brand: 'Not=A?Brand', version: '99.0.0.0' },
                { brand: 'Google Chrome', version: '151.0.0.0' },
                { brand: 'Chromium', version: '151.0.0.0' },
            ], wow64: false,
        }),
        toJSON: () => ({ brands: [
            { brand: 'Not=A?Brand', version: '99' },
            { brand: 'Google Chrome', version: '151' },
            { brand: 'Chromium', version: '151' },
        ], mobile: false, platform: 'Windows' }),
    }), configurable: true });
} catch (_) {}

Object.defineProperty(win.screen, 'width', { get: () => 1440, configurable: true });
Object.defineProperty(win.screen, 'height', { get: () => 900, configurable: true });
Object.defineProperty(win.screen, 'availWidth', { get: () => 1440, configurable: true });
Object.defineProperty(win.screen, 'availHeight', { get: () => 900, configurable: true });
Object.defineProperty(win.screen, 'colorDepth', { get: () => 24, configurable: true });
Object.defineProperty(win.screen, 'pixelDepth', { get: () => 24, configurable: true });
Object.defineProperty(win, 'devicePixelRatio', { get: () => 1, configurable: true });
Object.defineProperty(win, 'innerWidth', { get: () => 1440, configurable: true });
Object.defineProperty(win, 'innerHeight', { get: () => 900, configurable: true });
Object.defineProperty(win, 'outerWidth', { get: () => 1440, configurable: true });
Object.defineProperty(win, 'outerHeight', { get: () => 900, configurable: true });

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

win.__unhandled = [];
process.on('uncaughtException', (e) => {
    try { win.__unhandled.push('uncaught: ' + (e && e.message ? e.message : String(e))); } catch (_) {}
});
process.on('unhandledRejection', (r) => {
    try { win.__unhandled.push('rejection: ' + (r && r.message ? r.message : String(r))); } catch (_) {}
});

const vm = require('vm');
const ctx = vm.createContext(win);
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
                        width: 1440,
                        height: 900,
                        availWidth: 1440,
                        availHeight: 900,
                        colorDepth: 24,
                    },
                    nv: {
                        userAgent: input.userAgent,
                        platform: 'Windows',
                        webdriver: false,
                        hardwareConcurrency: 10,
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
                sc: { width: 1440, height: 900 },
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
    }));
    process.exit(0);
})();