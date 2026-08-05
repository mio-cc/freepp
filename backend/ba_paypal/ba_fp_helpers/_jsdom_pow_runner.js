#!/usr/bin/env node
/**
 * jsdom + sandbox_polyfill + @napi-rs/canvas pure PoW runner.
 * Mirrors CircuitSavage/hcaptcha-hsj-hsw-reversed sandbox approach:
 * minimal clean realm (NO process scrub, NO forced gate returns) so the
 * hsw.js fingerprint path reads real-ish browser values.
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

// Real canvas (2d) via @napi-rs/canvas; WebGL handled by polyfill stub
win.HTMLCanvasElement.prototype.getContext = function (type) {
    const cv = createCanvas(this.width || 300, this.height || 150);
    return cv.getContext(type);
};
for (const name of ['CanvasRenderingContext2D', 'CanvasGradient', 'CanvasPattern', 'ImageData']) {
    try { if (!win[name]) win[name] = win.HTMLCanvasElement.prototype.getContext('2d').constructor; } catch (_) {}
}

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
Object.defineProperty(win.navigator, 'platform', { get: () => 'Windows', configurable: true });
Object.defineProperty(win.navigator, 'language', { get: () => 'en-US', configurable: true });
Object.defineProperty(win.navigator, 'languages', { get: () => ['en-US', 'en'], configurable: true });
Object.defineProperty(win.navigator, 'webdriver', { get: () => false, configurable: true });

try { polyfill.install(win); } catch (e) {}

win.__unhandled = [];
process.on('uncaughtException', (e) => {
    try { win.__unhandled.push('uncaught: ' + (e && e.message ? e.message : String(e))); } catch (_) {}
});
process.on('unhandledRejection', (r) => {
    try { win.__unhandled.push('rejection: ' + (r && r.message ? r.message : String(r))); } catch (_) {}
});

const t0 = Date.now();
const vm = require('vm');
const ctx = vm.createContext(win);
try {
    vm.runInContext(hswCode, ctx, { timeout: 60000 });
} catch (e) {
    process.stdout.write(JSON.stringify({ ok: false, error: 'load: ' + String(e && (e.stack || e)) }));
    process.exit(1);
}

(async () => {
    let n = '';
    try {
        await new Promise((r) => setTimeout(r, 80));
        if (typeof win.hsw !== 'function') throw new Error('no hsw after load');
        n = String(await Promise.race([
            Promise.resolve(win.hsw(req)).then((x) => String(x)),
            new Promise((_, rej) => setTimeout(() => rej(new Error('hsw timeout')), 60000)),
        ]));
    } catch (e) {
        process.stdout.write(JSON.stringify({ ok: false, error: String(e && (e.stack || e)), unhandled: win.__unhandled }));
        process.exit(1);
    }
    process.stdout.write(JSON.stringify({
        ok: true,
        n,
        n_len: n.length,
        ms: Date.now() - t0,
        mode: 'jsdom_polyfill',
        unhandled: win.__unhandled,
    }));
    process.exit(0);
})();