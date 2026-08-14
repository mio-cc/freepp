const fs = require('fs');
const { JSDOM } = require('jsdom');
const nodeCrypto = require('crypto');
const path = require('path');
const vm = require('vm');
const polyfill = require('./sandbox_polyfill');
let hswCode = fs.readFileSync('C:/Users/Administrator/AppData/Local/Temp/opencode/hsw_latest.js', 'utf8');
const anchor = 'Gu=typeof ek=="boolean"?"g":function(aum){';
const idx = hswCode.indexOf(anchor);
console.log('anchor idx:', idx);
if (idx >= 0) {
  hswCode = hswCode.slice(0, idx) + 'Gu=typeof ek=="boolean"?"g":function(aum){try{globalThis.__gu_calls=(globalThis.__gu_calls||[]);globalThis.__gu_calls.push(aum)}catch(e){}' + hswCode.slice(idx + anchor.length);
}
const dumpAnchor = 'return uW[aum]=ae$},Kg=!xn';
const didx = hswCode.indexOf(dumpAnchor);
console.log('dumpAnchor idx:', didx);
if (didx >= 0) {
  hswCode = hswCode.slice(0, didx) + 'return uW[aum]=ae$},globalThis.__gu_dump=Gu,Kg=!xn' + hswCode.slice(didx + dumpAnchor.length);
}
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
Object.defineProperty(win.navigator, 'userAgent', { get: () => 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36', configurable: true });
Object.defineProperty(win.navigator, 'platform', { get: () => 'Win32', configurable: true });
Object.defineProperty(win.navigator, 'language', { get: () => 'zh-CN', configurable: true });
Object.defineProperty(win.navigator, 'languages', { get: () => ['zh-CN', 'zh'], configurable: true });
Object.defineProperty(win.navigator, 'webdriver', { get: () => false, configurable: true });
Object.defineProperty(win.navigator, 'vendor', { get: () => 'Google Inc.', configurable: true });
try { polyfill.install(win); } catch (e) {}
const ctx = vm.createContext(win);
try {
  vm.runInContext(hswCode, ctx, { timeout: 60000 });
  console.log('hsw eval ok');
} catch (e) {
  console.log('hsw eval ERR msg:', e && e.message);
  console.log('hsw eval ERR name:', e && e.name);
  console.log('hsw eval ERR stack:', String(e && e.stack).slice(0, 3000));
  process.exit(1);
}
setTimeout(() => {
  try {
    const out = vm.runInContext(`(() => {
      const calls = globalThis.__gu_calls || [];
      const seen = Array.from(new Set(calls));
      const G = globalThis.__gu_dump;
      const o = { haveGu: typeof G, n_calls: calls.length, seen: seen };
      const want = {};
      if (typeof G === 'function') {
        for (const k of seen) {
          try { want[k] = G(k); } catch (e) { want[k] = '<err:' + e.message + '>'; }
        }
      }
      o.want = want;
      return o;
    })()`, ctx);
    process.stdout.write(JSON.stringify(out, null, 1));
  } catch (e) {
    console.log('dump ERR:', String(e && (e.stack || e)).slice(0, 800));
  }
  process.exit(0);
}, 300);
