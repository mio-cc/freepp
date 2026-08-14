
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');
const nodeCrypto = require('crypto');
const polyfill = require('./sandbox_polyfill');
const ROOT = path.join(__dirname, '..');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
let hswCode = fs.readFileSync(input.hswPath, 'utf8');
const anchor = 'Gu=typeof ek=="boolean"';
const idx = hswCode.indexOf(anchor);
if (idx >= 0) {
  hswCode = hswCode.slice(0, idx) + 'globalThis.__gu_dump = null;' + hswCode.slice(idx);
  // insert capture after the Gu definition closing '}' of the function -> find the end of the definition
}
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
      const G = globalThis.__gu_dump;
      const o = { haveGu: typeof G };
      if (typeof G === 'function') {
        o.want = {};
        for (const k of [0,12,15,18,19,21,32,39,42,43,51,52,56,59,60,63,64,65,66,70,71,76,77,78,85,87,89,92,94,98,99,100,105,113,116,118,123,129,133,136,137,139,140,141,144,146,147,148,149,154,210,211,212,213,214,215,216,217,218,219,220,221,222,223,224,225,226,227,228,229,230,231,232,233,234,235,236,237,238,239,240,241,242,243,244,245,246,247,248]) {
          try { o.want[k] = G(k); } catch (e) { o.want[k] = '<err:' + e.message + '>'; }
        }
      }
      return o;
    })()`, ctx);
  } catch (e) { out = { err: String(e && (e.stack || e)) }; }
  process.stdout.write(JSON.stringify(out, null, 1));
  process.exit(0);
})();
