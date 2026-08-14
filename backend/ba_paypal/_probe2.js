
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');
const { TextDecoder, TextEncoder } = require('util');
const ROOT = path.join(__dirname, '..');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const hswCode = fs.readFileSync(input.hswPath, 'utf8');
const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>', { url: 'https://newassets.hcaptcha.com/', pretendToBeVisual: true, runScripts: 'outside-only' });
const win = dom.window;
win.WebAssembly = WebAssembly;
win.TextDecoder = TextDecoder;
win.TextEncoder = TextEncoder;
win.globalThis = win; win.self = win; win.global = win;
win.atob = (s) => Buffer.from(s, 'base64').toString('binary');
win.btoa = (s) => Buffer.from(s, 'binary').toString('base64');
const vm = require('vm');
const ctx = vm.createContext(win);
try { vm.runInContext(hswCode, ctx, { timeout: 60000 }); console.log('HSW EVAL OK, window.hsw =', typeof win.hsw); }
catch (e) { console.log('HSW EVAL ERR: ' + String(e && (e.stack || e)).slice(0, 600)); process.exit(1); }
