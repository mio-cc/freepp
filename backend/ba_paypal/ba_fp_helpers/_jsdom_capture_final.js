#!/usr/bin/env node
/**
 * 最终捕获: 读取已包装的 hsw (由 make_wrapped.js 生成), 跑 hsw(req) 捕获 us。
 * 使用与 _jsdom_mint_unified.js 相同的环境设定。
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

const vm = require('vm');
const ctx = vm.createContext(win);
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
        n = String(await Promise.race([
            Promise.resolve(win.hsw(req)).then((x) => String(x)),
            new Promise((_, rej) => setTimeout(() => rej(new Error('hsw pow timeout')), 60000)),
        ]));
        await new Promise((r) => setTimeout(r, 300));
        const us = win.__usCapture || 'NO_CAPTURE';
        process.stdout.write(JSON.stringify({ ok: true, n_len: n.length, us_len: us.length, us: us.slice(0, 30000), n: n, ms: Date.now() - t0 }));
    } catch (e) {
        process.stdout.write(JSON.stringify({ ok: false, error: String(e && (e.stack || e)), n_len: n.length, us: win.__usCapture || '' }));
        process.exit(1);
    }
    process.exit(0);
})();