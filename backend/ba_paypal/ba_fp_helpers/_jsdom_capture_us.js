#!/usr/bin/env node
/**
 * 捕获 hsw(req) 内部环境采集器 (async) 输出 JSON, 用于对比 Chrome。
 * 调用点替换为 await 采集器 + 记录输出 + 原样传 Promise。
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
let hswCode = fs.readFileSync(input.hswPath || path.join(ROOT, '_hsw_protocol_live.js'), 'utf8');
const UA0 = input.userAgent || 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36';

// 匹配: return <FN>(JSON.stringify(<payload>),<unix>,<raw>,<je>)
const PATT = /(return\s+)(\w+)\(JSON\.stringify\((\w+)\),(\w+),(\w+),(\w+)\)/;
const m0 = hswCode.match(PATT);
if (!m0) {
    console.log('CALL NOT FOUND');
    process.exit(1);
}
const fn = m0[2], payload = m0[3], unix = m0[4], raw = m0[5], je = m0[6];
const captureArg = `(function(){try{var p=(typeof ${je}==='function')?${je}():${je};if(p&&typeof p.then==='function'){return p.then(function(v){try{window.__usCapture=JSON.stringify(v)}catch(e){window.__usCapture='SER_ERR:'+String(e&&e.message)}return v},function(e){window.__usCapture='REJ:'+String(e&&e.message);throw e})}try{window.__usCapture=JSON.stringify(p)}catch(e){window.__usCapture='SER_ERR:'+String(e&&e.message)}return p}catch(e){window.__usCapture='EVAL_ERR:'+String(e&&e.message);return ${je}}})()`;
hswCode = hswCode.replace(PATT, `${m0[1]}${fn}(JSON.stringify(${payload}),${unix},${raw},${captureArg})`);

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

win.WebAssembly = WebAssembly;
win.globalThis = win; win.self = win; win.global = win;
try { Object.defineProperty(win, 'crypto', { value: nodeCrypto.webcrypto, writable: true, configurable: true }); } catch (_) {}
win.atob = (s) => Buffer.from(s, 'base64').toString('binary');
win.btoa = (s) => Buffer.from(s, 'binary').toString('base64');

Object.defineProperty(win.navigator, 'userAgent', { get: () => UA0, configurable: true });
Object.defineProperty(win.navigator, 'platform', { get: () => 'MacIntel', configurable: true });
Object.defineProperty(win.navigator, 'language', { get: () => 'en-US', configurable: true });
Object.defineProperty(win.navigator, 'languages', { get: () => ['en-US', 'en'], configurable: true });
Object.defineProperty(win.navigator, 'webdriver', { get: () => false, configurable: true });
Object.defineProperty(win.navigator, 'vendor', { get: () => 'Google Inc.', configurable: true });
Object.defineProperty(win.screen, 'width', { get: () => 1440, configurable: true });
Object.defineProperty(win.screen, 'height', { get: () => 900, configurable: true });
Object.defineProperty(win.screen, 'availWidth', { get: () => 1440, configurable: true });
Object.defineProperty(win.screen, 'availHeight', { get: () => 875, configurable: true });
Object.defineProperty(win, 'devicePixelRatio', { get: () => 2, configurable: true });
Object.defineProperty(win, 'innerWidth', { get: () => 1440, configurable: true });
Object.defineProperty(win, 'innerHeight', { get: () => 900, configurable: true });

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
        const us = win.__usCapture || 'NO_CAPTURE';
        process.stdout.write(JSON.stringify({ ok: true, n_len: n.length, us_len: us.length, us: us.slice(0, 20000), n: n, ms: Date.now() - t0 }));
    } catch (e) {
        process.stdout.write(JSON.stringify({ ok: false, error: String(e && (e.stack || e)), n_len: n.length, us: win.__usCapture || '' }));
        process.exit(1);
    }
    process.exit(0);
})();