#!/usr/bin/env node
/**
 * 捕获环境采集器输出: hook WebAssembly.instantiate, 包装 import 函数,
 * 记录传给 WASM 导出函数 (ec 等) 的实参 (第4参是 us 环境采集器 JSON)。
 * 不改 hsw.js 任何调用逻辑。
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

const argsLog = [];
const origInst = WebAssembly.instantiate.bind(WebAssembly);
const origInstSync = WebAssembly.instantiateStreaming ? WebAssembly.instantiateStreaming.bind(WebAssembly) : null;

function hookExports(inst) {
    try {
        const ex = inst.instance.exports;
        window.__wasmExports = Object.keys(ex);
        for (const k of Object.keys(ex)) {
            if (typeof ex[k] === 'function') {
                const orig = ex[k];
                ex[k] = function (...args) {
                    if (args.length >= 4) {
                        window.__ecArgs = args.map((a, i) => {
                            try {
                                if (typeof a === 'string') return { t: 'str', v: a.slice(0, 3000) };
                                if (a instanceof Uint8Array) return { t: 'u8', v: Array.from(a.slice(0, 200)) };
                                return { t: typeof a, v: String(a).slice(0, 500) };
                            } catch (e) { return { t: 'err', v: String(e).slice(0, 100) }; }
                        });
                    }
                    return orig.apply(this, args);
                };
            }
        }
    } catch (e) { window.__wasmErr = String(e && e.message); }
    return inst;
}

// hook instantiate (hsw 用这个)
WebAssembly.instantiate = async function (src, imports) {
    const inst = await origInst(src, imports);
    return hookExports(inst);
};

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
        process.stdout.write(JSON.stringify({
            ok: true, n_len: n.length, n,
            exports: win.__wasmExports || null,
            ecArgs: win.__ecArgs || null,
            wasmErr: win.__wasmErr || null,
            ms: Date.now() - t0,
        }));
    } catch (e) {
        process.stdout.write(JSON.stringify({ ok: false, error: String(e && (e.stack || e)), exports: win.__wasmExports, ecArgs: win.__ecArgs, wasmErr: win.__wasmErr }));
        process.exit(1);
    }
    process.exit(0);
})();