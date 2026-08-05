#!/usr/bin/env node
/**
 * 诊断: jsdom 环境跑 hsw(req) 时记录整个采集过程读到的关键指纹值。
 * 用 Proxy 包裹 window/navigator/document 的 getter, 记录哪些属性被读。
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
    return cv.getContext(type);
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

const vm = require('vm');
const ctx = vm.createContext(win);
try {
    vm.runInContext(hswCode, ctx, { timeout: 60000 });
} catch (e) {
    process.stdout.write(JSON.stringify({ ok: false, error: 'load: ' + String(e && (e.stack || e)) }));
    process.exit(1);
}

// 指纹采集记录器: 用 Proxy 包装
const reads = {};
function makeLogProxy(obj, label, depth = 0) {
    if (!obj || typeof obj !== 'object' || depth > 2) return obj;
    if (obj.__logged) return obj;
    return new Proxy(obj, {
        get(target, prop, recv) {
            const key = label + '.' + String(prop);
            reads[key] = (reads[key] || 0) + 1;
            try {
                const v = Reflect.get(target, prop, recv);
                if (v && typeof v === 'object' && depth < 2) {
                    return makeLogProxy(v, key, depth + 1);
                }
                return v;
            } catch (e) { return undefined; }
        },
    });
}

(async () => {
    const t0 = Date.now();
    let n = '';
    try {
        await new Promise((r) => setTimeout(r, 80));
        if (typeof win.hsw !== 'function') throw new Error('no hsw after load');

        // 记录 hsw 执行期间的 window 属性访问
        win.__reads = reads; const proxyWin = win;
        n = String(await Promise.race([
            Promise.resolve(win.hsw.call(proxyWin, req)).then((x) => String(x)),
            new Promise((_, rej) => setTimeout(() => rej(new Error('hsw pow timeout')), 60000)),
        ]));

        // 收集返回值样本 (只取关键属性当前值)
        const sample = {};
        const grab = (path, getter) => { try { sample[path] = getter(); } catch (e) { sample[path] = 'ERR:' + e.message; } };
        grab('ua', () => win.navigator.userAgent);
        grab('platform', () => win.navigator.platform);
        grab('vendor', () => win.navigator.vendor);
        grab('lang', () => win.navigator.language);
        grab('langs', () => JSON.stringify(win.navigator.languages));
        grab('hwc', () => win.navigator.hardwareConcurrency);
        grab('deviceMemory', () => win.navigator.deviceMemory);
        grab('maxTouch', () => win.navigator.maxTouchPoints);
        grab('connection', () => JSON.stringify(win.navigator.connection));
        grab('screen', () => JSON.stringify({w: win.screen.width, h: win.screen.height, aw: win.screen.availWidth, ah: win.screen.availHeight, cd: win.screen.colorDepth, pr: win.screen.pixelDepth}));
        grab('dpr', () => win.devicePixelRatio);
        grab('innerW', () => win.innerWidth);
        grab('innerH', () => win.innerHeight);
        grab('outerW', () => win.outerWidth);
        grab('outerH', () => win.outerHeight);
        grab('timezone', () => new Intl.DateTimeFormat().resolvedOptions().timeZone);
        grab('tzoffset', () => new Date().getTimezoneOffset());
        grab('canvas2d', () => {
            const c = win.document.createElement('canvas'); c.width = 300; c.height = 150;
            const g = c.getContext('2d');
            if (!g) return null;
            const txt = 'hcaptcha-grind-2583295';
            g.font = '14px Arial'; g.fillStyle = '#f00';
            g.fillText(txt, 5, 30); g.fillStyle = '#00f'; g.fillRect(1, 1, 90, 90);
            try { const d = g.getImageData(0, 0, 30, 30).data; return 'imgdata:' + (Array.from(d.slice(0, 60)).join(',')); } catch (e) { return 'imgdata-ERR:' + e.message; }
        });
        grab('toDataURL', () => {
            const c = win.document.createElement('canvas'); c.width = 50; c.height = 20;
            const g = c.getContext('2d'); g.fillStyle = '#f00'; g.fillRect(1, 2, 20, 10);
            try { return 'len:' + (c.toDataURL ? c.toDataURL().length : 'NONE'); } catch (e) { return 'ERR:' + e.message; }
        });
        grab('webgl', () => {
            const c = win.document.createElement('canvas');
            const g = c.getContext('webgl');
            if (!g) return 'NONE';
            try {
                const ext = g.getExtension('WEBGL_debug_renderer_info');
                const urenderer = ext ? g.getParameter(ext.UNMASKED_RENDERER_WEBGL) : null;
                const vendor = ext ? g.getParameter(ext.UNMASKED_VENDOR_WEBGL) : null;
                const renderer = g.getParameter(g.RENDERER);
                const v = g.getParameter(g.VERSION);
                const sv = g.getParameter(g.SHADING_LANGUAGE_VERSION);
                return JSON.stringify({urenderer, vendor, renderer, v, sv});
            } catch (e) { return 'ERR:' + e.message; }
        });
        grab('perf_nav', () => {
            try { const n = win.performance.getEntriesByType('navigation'); return JSON.stringify(n.length ? {len: n.length, keys: Object.keys(n[0]).slice(0, 25)} : 'EMPTY'); } catch (e) { return 'ERR:' + e.message; }
        });
        grab('perf_res', () => {
            try { return win.performance.getEntriesByType('resource').length; } catch (e) { return 'ERR:' + e.message; }
        });
        grab('audio', () => {
            const Ac = win.AudioContext || win.webkitAudioContext;
            if (!Ac) return 'NONE';
            try {
                const a = new Ac();
                const osc = a.createOscillator(); const dest = a.createAnalyser();
                osc.connect(dest); osc.connect(a.destination);
                const b = new Float32Array(1); a.resume && a.resume();
                return 'ctx-time:' + a.currentTime + ' sampleRate:' + a.sampleRate;
            } catch (e) { return 'ERR:' + e.message; }
        });

        const topReads = Object.entries(reads).sort((x, y) => y[1] - x[1]).slice(0, 80);
        process.stdout.write(JSON.stringify({
            ok: true, n_len: n.length, n,
            reads: topReads, sample,
            ms: Date.now() - t0, unhandled: win.__unhandled,
        }));
    } catch (e) {
        process.stdout.write(JSON.stringify({ ok: false, error: String(e && (e.stack || e)), n_len: n.length, reads: Object.entries(reads).sort((x, y) => y[1] - x[1]).slice(0, 80) }));
        process.exit(1);
    }
    process.exit(0);
})();