#!/usr/bin/env node
/**
 * 通用捕获环境采集器输出: 定位 async 采集器定义 (,\w+=function(aza){return $X(this,...)),
 * 改名原函数为 __orig_<name>, 新函数包装记录返回 Promise 解析值。
 * 不改变任何调用语义。
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

// 定位采集器定义: ,<NAME>=function(aza){return $X(this,void 0,void 0,function(){
const PATT = /(,\w+=)function\(aza\)\{return \$X\(this,void 0,void 0,function\(\)\{/;
const m0 = hswCode.match(PATT);
if (!m0) {
    console.log('COLLECTOR NOT FOUND');
    process.exit(1);
}
const fnName = m0[1].replace(/^,/, '');
const defStart = m0.index;
// 括号计数找定义结束 (函数体 }, 最外层 )
let depth = 0, i = defStart, end = -1;
let inStr = false, strCh = '';
for (; i < hswCode.length; i++) {
    const ch = hswCode[i];
    if (inStr) {
        if (ch === '\\') { i++; continue; }
        if (ch === strCh) inStr = false;
        continue;
    }
    if (ch === '"' || ch === "'" || ch === '`') { inStr = true; strCh = ch; continue; }
    if (ch === '{') depth++;
    else if (ch === '}') {
        depth--;
        if (depth <= 0) { end = i + 1; break; }
    }
}
if (end < 0) {
    console.log('DEF END NOT FOUND');
    process.exit(1);
}
const origDef = hswCode.slice(defStart + 1, end); // <NAME>=function(aza){...}
const nameEq = origDef.indexOf('=');
const fnBody = origDef.slice(nameEq + 1);
const wrappedDef = `__orig_${fnName}=` + fnBody +
    `,${fnName}=function(aza){var __p=__orig_${fnName}.call(this,aza);if(__p&&__p.then){__p.then(function(v){window.__usCapture=JSON.stringify(v)},function(e){window.__usCapture='REJ:'+String(e&&e.message)})}else{window.__usCapture='sync:'+String(__p)}return __p}`;
hswCode = hswCode.slice(0, defStart + 1) + wrappedDef + hswCode.slice(end);

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
        await new Promise((r) => setTimeout(r, 300));
        const us = win.__usCapture || 'NO_CAPTURE';
        process.stdout.write(JSON.stringify({ ok: true, n_len: n.length, us_len: us.length, us: us.slice(0, 30000), n: n, ms: Date.now() - t0 }));
    } catch (e) {
        process.stdout.write(JSON.stringify({ ok: false, error: String(e && (e.stack || e)), n_len: n.length, us: win.__usCapture || '' }));
        process.exit(1);
    }
    process.exit(0);
})();