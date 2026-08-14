const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');
const { TextDecoder, TextEncoder } = require('util');
const ROOT = path.join(__dirname, '..');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const hswCode = fs.readFileSync(input.hswPath, 'utf8');
const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>', { url: 'https://newassets.hcaptcha.com/', pretendToBeVisual: true, runScripts: 'outside-only' });
const win = dom.window;
win.TextDecoder = TextDecoder;
win.TextEncoder = TextEncoder;
win.atob = (s) => Buffer.from(s, 'base64').toString('binary');
win.btoa = (s) => Buffer.from(s, 'binary').toString('base64');
const vm = require('vm');
const ctx = dom.getInternalVMContext();
process.exit = function (code) { console.log('>> process.exit INTERCEPTED code=' + code); throw new Error('exit intercepted'); };
vm.runInContext('window.close = function(){ console.log(">> window.close CALLED"); };', ctx);
try {
  vm.runInContext(hswCode, ctx, { timeout: 60000 });
  console.log('>> HSW EVAL OK, hsw type:', typeof win.hsw);
} catch (e) {
  console.log('>> HSW EVAL ERR: ' + String(e && (e.stack || e)).slice(0, 400));
}
(async () => {
  try {
    const n = String(await Promise.race([
      Promise.resolve(win.hsw(input.req, undefined)).then((x) => String(x)),
      new Promise((_, rej) => setTimeout(() => rej(new Error('hsw timeout')), 45000)),
    ]));
    console.log('>> HSW CALL OK, n_len=' + n.length);
  } catch (e) {
    console.log('>> HSW CALL ERR: ' + String(e && (e.stack || e)).slice(0, 500));
  }
})();
