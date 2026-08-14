
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
const UA0 = input.userAgent || 'Mozilla/5.0';
const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>', {
  url: 'https://newassets.hcaptcha.com/', pretendToBeVisual: true, runScripts: 'outside-only',
});
const win = dom.window;
win.HTMLCanvasElement.prototype.getContext = function (type) {
  const cv = createCanvas(this.width || 300, this.height || 150);
  if (type === '2d' || !type) return cv.getContext('2d');
  return cv.getContext(type);
};
win.HTMLCanvasElement.prototype.toDataURL = function (type, quality) {
  const cv = createCanvas(this.width || 300, this.height || 150);
  try { return cv.toDataURL(type, quality); } catch (e) { return ''; }
};
win.HTMLCanvasElement.prototype.toBlob = function (cb, type, quality) {
  const cv = createCanvas(this.width || 300, this.height || 150);
  try { const buf = cv.toBuffer(type === 'image/jpeg' ? 'image/jpeg' : 'image/png', quality); cb(new win.Blob([buf], { type: type || 'image/png' })); } catch (e) { cb(null); }
};
win.HTMLCanvasElement.prototype.getImageData = function (sx, sy, sw, sh) {
  const cv = createCanvas(this.width || 300, this.height || 150);
  try { return cv.getContext('2d').getImageData(sx, sy, sw, sh); } catch (e) { return null; }
};
win.WebAssembly = WebAssembly;
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
Object.defineProperty(win.navigator, 'deviceMemory', { get: () => 8, configurable: true });
Object.defineProperty(win.navigator, 'hardwareConcurrency', { get: () => 8, configurable: true });
Object.defineProperty(win.navigator, 'maxTouchPoints', { get: () => 0, configurable: true });
Object.defineProperty(win.navigator, 'connection', { get: () => ({ effectiveType: '4g', saveData: false, downlink: 10, rtt: 100, type: 'wifi', addEventListener() {}, removeEventListener() {} }), configurable: true });
Object.defineProperty(win.navigator, 'userActivation', { get: () => ({ hasBeenActive: true, isActive: true }), configurable: true });
Object.defineProperty(win.navigator, 'appVersion', { get: () => UA0.replace('Mozilla/', ''), configurable: true });
Object.defineProperty(win.navigator, 'appName', { get: () => 'Netscape', configurable: true });
Object.defineProperty(win.navigator, 'product', { get: () => 'Gecko', configurable: true });
Object.defineProperty(win.navigator, 'appCodeName', { get: () => 'Mozilla', configurable: true });
try { Object.defineProperty(win.navigator, 'pdfViewerEnabled', { get: () => true, configurable: true }); } catch (_) {}
const UA_DATA = { brands: [{ brand: 'Not=A?Brand', version: '99' }, { brand: 'Google Chrome', version: '151' }, { brand: 'Chromium', version: '151' }], mobile: false, platform: 'Windows', getHighEntropyValues: () => Promise.resolve({ architecture: 'x86', bitness: '64', model: '', platformVersion: '10.0.0', uaFullVersion: '151.0.7922.72', fullVersionList: [{ brand: 'Not=A?Brand', version: '99.0.0.0' }, { brand: 'Google Chrome', version: '151.0.7922.72' }, { brand: 'Chromium', version: '151.0.7922.72' }], wow64: false }), getValues: () => Promise.resolve({ brands: [{ brand: 'Not=A?Brand', version: '99' }, { brand: 'Google Chrome', version: '151' }, { brand: 'Chromium', version: '151' }], mobile: false, platform: 'Windows' }) };
Object.defineProperty(win.navigator, 'userAgentData', { get: () => UA_DATA, configurable: true });
Object.defineProperty(win.navigator, 'mediaDevices', { get: () => ({ enumerateDevices: () => Promise.resolve([{ kind: 'audioinput', label: '', deviceId: '', groupId: '' }, { kind: 'videoinput', label: '', deviceId: '', groupId: '' }]), getDisplayMedia: () => Promise.reject(new Error('not allowed')), getUserMedia: () => Promise.reject(new Error('not allowed')) }), configurable: true });
Object.defineProperty(win.navigator, 'mimeTypes', { get: () => [{ type: 'application/pdf', suffixes: 'pdf', description: 'Portable Document Format' }, { type: 'application/x-google-chrome-pdf', suffixes: 'pdf', description: 'Portable Document Format' }], configurable: true });
Object.defineProperty(win.navigator, 'plugins', { get: () => [
  { name: 'PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
  { name: 'Chrome PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
  { name: 'Chromium PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
  { name: 'Microsoft Edge PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
  { name: 'WebKit built-in PDF', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
], configurable: true });
try { polyfill.install(win); } catch (e) {}
try { Object.defineProperty(win.document, 'characterSet', { get: () => 'windows-1252', configurable: true }); } catch (_) {}
try { Object.defineProperty(win.document, 'compatMode', { get: () => 'BackCompat', configurable: true }); } catch (_) {}
try { Object.defineProperty(win.document, 'domain', { get: () => 'newassets.hcaptcha.com', configurable: true }); } catch (_) {}
try { Object.defineProperty(win.document, 'currentScript', { get: () => null, configurable: true }); } catch (_) {}
const fakeAudioCtx = {
  sampleRate: 48000, currentTime: 0, destination: {},
  createOscillator: () => ({ connect() {}, start() {}, stop() {}, frequency: { value: 0 }, type: 'sine' }),
  createAnalyser: () => ({ connect() {}, disconnect() {}, getFloatFrequencyData(arr) { arr.fill(-127); }, getByteFrequencyData(arr) { arr.fill(0); }, fftSize: 2048, frequencyBinCount: 1024 }),
  createGain: () => ({ connect() {}, gain: { value: 1 } }),
  createScriptProcessor: () => ({ connect() {}, disconnect() {}, onaudioprocess: null }),
  createBuffer: () => ({ getChannelData: () => new Float32Array(48000) }),
  resume: () => Promise.resolve(), close: () => Promise.resolve(), state: 'running',
};
if (typeof win.AudioContext !== 'function') win.AudioContext = function () { return fakeAudioCtx; };
if (typeof win.webkitAudioContext !== 'function') win.webkitAudioContext = win.AudioContext;
if (typeof win.OfflineAudioContext !== 'function') win.OfflineAudioContext = function () { return fakeAudioCtx; };

const vm = require('vm');
const ctx = vm.createContext(win);
vm.runInContext(hswCode, ctx, { timeout: 60000 });
(async () => {
  await new Promise((r) => setTimeout(r, 300));
  let imp = null;
  try { imp = vm.runInContext('globalThis.__hsw_imp', ctx); } catch (e) {}
  const want = process.argv[2] ? process.argv[2].split(',') : null;
  const out = {};
  if (imp) {
    for (const mname in imp) {
      const m = imp[mname];
      if (m && typeof m === 'object') {
        out[mname] = {};
        for (const fname in m) {
          if (typeof m[fname] === 'function') {
            if (want && !want.includes(fname)) continue;
            let src = '';
            try { src = m[fname].toString(); } catch (e) { src = '<no toString>'; }
            out[mname][fname] = src.slice(0, 900);
          }
        }
      }
    }
  }
  process.stdout.write(JSON.stringify(out, null, 1));
  process.exit(0);
})();
