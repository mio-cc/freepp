const fs = require('fs');
const { JSDOM } = require('jsdom');
const ROOT = process.cwd();
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
(async () => {
  const dom = new JSDOM('', { url: 'https://newassets.hcaptcha.com/', pretendToBeVisual: true, runScripts: 'outside-only' });
  const win = dom.window;
  win.WebAssembly = WebAssembly;
  const { TextDecoder, TextEncoder } = require('util');
  win.TextDecoder = TextDecoder; win.TextEncoder = TextEncoder;
  win.globalThis = win; win.self = win; win.global = win;
  try { Object.defineProperty(win, 'crypto', { value: require('crypto').webcrypto, writable: true, configurable: true }); } catch (_) {}
  win.atob = (s) => Buffer.from(s, 'base64').toString('binary');
  win.btoa = (s) => Buffer.from(s, 'binary').toString('base64');
  const vm = require('vm');
  const ctx = dom.getInternalVMContext();
  const hswCode = fs.readFileSync(input.hswPath, 'utf8');
  vm.runInContext(hswCode, ctx, { timeout: 60000 });
  for (const respB64 of input.resps) {
    try {
      const out = await win.hsw(0, respB64);
      process.stdout.write(JSON.stringify({ ok: true, type: out.constructor.name, out: String(out).slice(0, 120) }) + '\n');
    } catch (e) {
      process.stdout.write(JSON.stringify({ ok: false, error: String(e && e.stack || e).slice(0, 200) }) + '\n');
    }
  }
  process.exit(0);
})().catch(e => { process.stdout.write(JSON.stringify({ ok: false, error: String(e) }) + '\n'); process.exit(1); });
