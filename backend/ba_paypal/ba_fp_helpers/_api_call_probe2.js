
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');
const { createCanvas } = require('@napi-rs/canvas');
const nodeCrypto = require('crypto');
const polyfill = require('./sandbox_polyfill');
const ROOT = path.join(__dirname, '..');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const req = String(input.req || '').trim();
const hswCode = fs.readFileSync(input.hswPath, 'utf8');
const UA0 = input.userAgent || 'Mozilla/5.0';
const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>', {
  url: 'https://newassets.hcaptcha.com/', pretendToBeVisual: true, runScripts: 'outside-only',
});
const win = dom.window;
const log = [];
function wrap(obj, name, label) {
  const orig = obj && obj[name];
  if (typeof orig !== 'function') return;
  obj[name] = function () {
    try {
      const ctxinfo = (this && this.canvas && this.canvas.width != null) ? 'canvas=' + this.canvas.width + 'x' + this.canvas.height : '';
      log.push([label || name, ctxinfo, Array.from(arguments).map(a => {
        try { return (a && a.constructor && a.constructor.name) || typeof a; } catch (e) { return '?'; }
      }).join(',')]);
    } catch (e) {}
    return orig.apply(this, arguments);
  };
}
let lastCtx = null;
win.HTMLCanvasElement.prototype.getContext = function (type) {
  const cv = createCanvas(this.width || 300, this.height || 150);
  const ctx = (type === '2d' || !type) ? cv.getContext('2d') : cv.getContext(type);
  if (ctx && !ctx.__wrapped) {
    wrap(ctx, 'fillText', 'ctx.fillText');
    wrap(ctx, 'strokeText', 'ctx.strokeText');
    wrap(ctx, 'measureText', 'ctx.measureText');
    wrap(ctx, 'getImageData', 'ctx.getImageData');
    wrap(ctx, 'drawImage', 'ctx.drawImage');
    wrap(ctx, 'fillRect', 'ctx.fillRect');
    wrap(ctx, 'strokeRect', 'ctx.strokeRect');
    wrap(ctx, 'save', 'ctx.save');
    wrap(ctx, 'restore', 'ctx.restore');
    wrap(ctx, 'translate', 'ctx.translate');
    wrap(ctx, 'scale', 'ctx.scale');
    wrap(ctx, 'rotate', 'ctx.rotate');
    wrap(ctx, 'beginPath', 'ctx.beginPath');
    wrap(ctx, 'arc', 'ctx.arc');
    wrap(ctx, 'moveTo', 'ctx.moveTo');
    wrap(ctx, 'lineTo', 'ctx.lineTo');
    wrap(ctx, 'setTransform', 'ctx.setTransform');
    wrap(ctx, 'clearRect', 'ctx.clearRect');
    ctx.__wrapped = true;
  }
  try { log.push(['getContext', 'canvas=' + (this.width || 300) + 'x' + (this.height || 150), type || '']); } catch (e) {}
  return ctx;
};
win.HTMLCanvasElement.prototype.toDataURL = function (type, quality) {
  const cv = createCanvas(this.width || 300, this.height || 150);
  try { log.push(['toDataURL', 'canvas=' + (this.width || 300) + 'x' + (this.height || 150), type || '']); return cv.toDataURL(type, quality); } catch (e) { return ''; }
};
win.HTMLCanvasElement.prototype.toBlob = function (cb, type, quality) {
  const cv = createCanvas(this.width || 300, this.height || 150);
  try { const buf = cv.toBuffer(type === 'image/jpeg' ? 'image/jpeg' : 'image/png', quality); cb(new win.Blob([buf], { type: type || 'image/png' })); } catch (e) { cb(null); }
};
win.HTMLCanvasElement.prototype.getImageData = function (sx, sy, sw, sh) {
  const cv = createCanvas(this.width || 300, this.height || 150);
  try { log.push(['canvas.getImageData', this.width + 'x' + this.height]); return cv.getContext('2d').getImageData(sx, sy, sw, sh); } catch (e) { return null; }
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
const audioLog = [];
function makeFakeCtx(tag) {
  return {
    sampleRate: 48000, currentTime: 0, destination: {},
    createOscillator() { audioLog.push([tag + '.createOscillator']); return { connect() {}, start() {}, stop() {}, frequency: { value: 0 }, type: 'sine', onended: null }; },
    createAnalyser() { audioLog.push([tag + '.createAnalyser']); return { connect() {}, disconnect() {}, getFloatFrequencyData(arr) { arr.fill(-127); }, getByteFrequencyData(arr) { arr.fill(0); }, fftSize: 2048, frequencyBinCount: 1024 }; },
    createGain() { audioLog.push([tag + '.createGain']); return { connect() {}, gain: { value: 1 } }; },
    createScriptProcessor() { audioLog.push([tag + '.createScriptProcessor']); return { connect() {}, disconnect() {}, onaudioprocess: null }; },
    createBuffer(ch, len, rate) { audioLog.push([tag + '.createBuffer', ch, len, rate]); return { getChannelData: () => new Float32Array(len) }; },
    createBufferSource() { audioLog.push([tag + '.createBufferSource']); return { connect() {}, start() {}, buffer: null }; },
    createDynamicsCompressor() { audioLog.push([tag + '.createDynamicsCompressor']); return { connect() {}, threshold: { value: 0 }, knee: { value: 0 }, ratio: { value: 0 }, attack: { value: 0 }, release: { value: 0 } }; },
    resume() { audioLog.push([tag + '.resume']); return Promise.resolve(); },
    close() { audioLog.push([tag + '.close']); return Promise.resolve(); },
    startRendering() { audioLog.push([tag + '.startRendering']); return Promise.resolve(); },
    state: 'running',
  };
}
win.AudioContext = function () { audioLog.push(['AudioContext ctor']); return makeFakeCtx('Audio'); };
win.webkitAudioContext = win.AudioContext;
win.OfflineAudioContext = function () { audioLog.push(['OfflineAudioContext ctor']); return makeFakeCtx('Offline'); };

const vm = require('vm');
const ctx = vm.createContext(win);
vm.runInContext(hswCode, ctx, { timeout: 60000 });
(async () => {
  const t0 = Date.now();
  let n = '';
  try {
    await new Promise((r) => setTimeout(r, 80));
    n = String(await Promise.race([
      Promise.resolve(win.hsw(req)).then((x) => String(x)),
      new Promise((_, rej) => setTimeout(() => rej(new Error('pow timeout')), 60000)),
    ]));
  } catch (e) {
    process.stdout.write(JSON.stringify({ ok: false, error: String(e).slice(0, 300), log, audio_log: audioLog, n_len: n.length }));
    process.exit(1);
  }
  process.stdout.write(JSON.stringify({ ok: true, n_len: n.length, ms: Date.now() - t0, log, audio_log: audioLog }));
  process.exit(0);
})();
