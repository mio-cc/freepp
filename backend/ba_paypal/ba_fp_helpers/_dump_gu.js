
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');
const nodeCrypto = require('crypto');
const polyfill = require('./sandbox_polyfill');
const ROOT = path.join(__dirname, '..');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const hswCode = fs.readFileSync(input.hswPath, 'utf8');
const UA0 = input.userAgent || 'Mozilla/5.0';
const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>', {
  url: 'https://newassets.hcaptcha.com/', pretendToBeVisual: true, runScripts: 'outside-only',
});
const win = dom.window;
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
try { polyfill.install(win); } catch (e) {}
const vm = require('vm');
const ctx = vm.createContext(win);
vm.runInContext(hswCode, ctx, { timeout: 60000 });
(async () => {
  await new Promise((r) => setTimeout(r, 300));
  let out = null;
  try {
    out = vm.runInContext(`(() => {
      const o = {};
      for (let i = 0; i < 300; i++) {
        try { o[i] = Gu(i); } catch (e) {}
      }
      return o;
    })()`, ctx);
  } catch (e) { out = { err: String(e) }; }
  process.stdout.write(JSON.stringify(out));
  process.exit(0);
})();
