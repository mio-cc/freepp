# -*- coding: utf-8 -*-
"""从 hsw.js 运行时捕获内嵌 WASM (hook WebAssembly.compile/instantiate)。

用法: python extract_hsw_wasm.py <hsw_js_path> <out_wasm_path>
依赖: node (hsw.js 在 node vm 里加载以触发 wasm 初始化)
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

CAPTURE_JS = r"""
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const GJ = process.argv[1];
const SRC = process.argv[2];
const OUT = process.argv[3];
const code = fs.readFileSync(SRC, 'utf-8');
const origCompile = WebAssembly.compile.bind(WebAssembly);
const origInst = WebAssembly.instantiate.bind(WebAssembly);
let saved = null;
const myWasm = Object.create(WebAssembly);
myWasm.compile = async function (bytes) {
  if (bytes instanceof ArrayBuffer || ArrayBuffer.isView(bytes)) {
    const buf = bytes instanceof ArrayBuffer ? bytes : bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
    if (!saved && buf.byteLength > 100000) {
      saved = Buffer.from(buf);
      fs.writeFileSync(OUT, saved);
      console.error('[capture] wasm len=' + saved.length);
    }
  }
  return origCompile(bytes);
};
myWasm.instantiate = async function (mod, imports) {
  if (mod && typeof mod !== 'function' && (mod instanceof ArrayBuffer || ArrayBuffer.isView(mod))) {
    const buf = mod instanceof ArrayBuffer ? mod : mod.buffer.slice(mod.byteOffset, mod.byteOffset + mod.byteLength);
    if (!saved && buf.byteLength > 100000) {
      saved = Buffer.from(buf);
      fs.writeFileSync(OUT, saved);
      console.error('[capture] wasm len=' + saved.length);
    }
  }
  return origInst(mod, imports);
};
const ctx = {
  self: null, postMessage: () => {}, onmessage: null,
  addEventListener: (t, fn) => { if (t === 'message') ctx.onmessage = fn; },
  removeEventListener: () => {},
  navigator: { userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36', platform: 'Win32', hardwareConcurrency: 8, deviceMemory: 8, language: 'en-US', languages: ['en-US'], vendor: 'Google Inc.', maxTouchPoints: 0 },
  location: { href: 'https://newassets.hcaptcha.com/c/x/hsw.js', origin: 'https://newassets.hcaptcha.com', hash: '' },
  document: { documentElement: { style: {} }, createElement: () => ({ style: {}, setAttribute: () => {} }), querySelector: () => null, cookie: '' },
  screen: { width: 1536, height: 864, availWidth: 1536, availHeight: 824, colorDepth: 24, pixelDepth: 24 },
  WebAssembly: myWasm,
  Worker: require('worker_threads').Worker,
  setTimeout, clearTimeout, setInterval, clearInterval,
  crypto: require('crypto').webcrypto,
  performance: { now: () => Date.now() },
  Math, Date, JSON, Object, Array, String, Number, Boolean, RegExp, Error, Promise,
  Uint8Array, Uint16Array, Uint32Array, Int8Array, Int16Array, Int32Array, Float32Array, Float64Array,
  ArrayBuffer, DataView, TextEncoder, TextDecoder,
  atob: (s) => Buffer.from(s, 'base64').toString('binary'),
  btoa: (s) => Buffer.from(s, 'binary').toString('base64'),
  fetch: global.fetch,
};
ctx.self = ctx; ctx.window = ctx; ctx.globalThis = ctx;
const sandbox = vm.createContext(ctx);
vm.runInContext(code, sandbox, { filename: 'hsw.js' });
const hsw = vm.runInContext('this.hsw', sandbox);
(async () => {
  try { await hsw(1, new Uint8Array([1, 2, 3])); } catch (e) {}
  await new Promise((r) => setTimeout(r, 1500));
  console.log('saved:', saved ? saved.length : 'NOT CAPTURED');
  process.exit(0);
})();
"""


def extract(hsw_path: str, out_path: str) -> bool:
    hsw_path = os.path.abspath(hsw_path)
    out_path = os.path.abspath(out_path)
    script = os.path.join(tempfile.gettempdir(), "_hsw_wasm_capture.js")
    Path(script).write_text(CAPTURE_JS, encoding="utf-8")
    proc = subprocess.run(
        ["node", script, os.path.dirname(script), hsw_path, out_path],
        capture_output=True, text=True, timeout=120,
    )
    ok = os.path.exists(out_path) and os.path.getsize(out_path) > 100000
    print(proc.stdout.strip())
    if proc.stderr.strip():
        print(proc.stderr.strip()[-300:])
    return ok


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else r"gj\_hsw_live.js"
    dst = sys.argv[2] if len(sys.argv) > 2 else r"backend\ba_paypal\paypal\pow_native\hsw_real.wasm"
    extract(src, dst)