# 运行前准备清单（README_solver）

> 桌面当前环境只有题面，没有 `passive_bridge.py` / `profile_ab.json` / `node_bridge.js`，
> 也没有验证端点与 PoW WASM。要真正跑出 `flag`，按下面补齐。

## 1. 你需要补齐的两样东西
1. **PoW WASM**（`po.wasm`）：在真浏览器打开那个账单授权入口，于 Network/Application 面板
   里抓取验证方加载的 PoW WASM 字节，存到 `solver/po.wasm`。
   - 也可用 happy-dom 在 passive 模式加载时抓到的同一份（它就是算错的那个）。
2. **验证端点 + 你的授权会话**：
   - `PASSIVE_CHALLENGE_URL`：服务端下发 passive 挑战的接口（通常类似 `.../checks/challenge`）。
   - `PASSIVE_SUBMIT_URL`：提交 proof token 的接口（题面里的"验证接口"）。
   - `AUTH_COOKIE`：你授权账户的会话 cookie（零金额校验的测试环境）。
   - 这些在题面附件 `passive_bridge.py` 里是"已攻克"的协议层，直接复用即可。

## 2. 安装原生 WASM 运行时（二选一）
- wasmer（推荐）：`pip install wasmer wasmer_compiler_cranelift`
- 或 wasmtime：`pip install wasmtime`

  没有原生绑定时，`native_pow.py` 会进入 **demo 模式**，只演示协议封包与
  `host_sum≠4778` 的要点，**不会真跑 WASM，也不会出真 token**。

## 3. 对齐设备画像
`native_pow.py` 顶部 `BrowserProfile` 即你"声称"的桌面 Chrome 画像。
它必须与：
- 你在 TLS/JA4 + `User-Agent` 里声明的设备，
- 以及 `wasm-objdump -j import` 看到的 WASM 真实 import 名

**全部自洽**。不一致 → 时序/画像二阶校验仍会 soft-reject。

## 4. 跑
```bash
cd solver
pip install wasmer wasmer_compiler_cranelift   # 或 wasmtime
export CTF_AUTH_COOKIE="<你的授权会话>"
export CTF_CHALLENGE_URL="https://<网关>/checks/challenge"
export CTF_SUBMIT_URL="https://<网关>/checks"
python run_solver.py
```
成功输出：
```
[✓] success=true
    token  = <VALID_TOKEN>
    flag   = flag{<base64(token)>}
```
`flag.txt` 也会写出，直接提交即可。

## 5. 排错
- `host_sum` 仍 = 4778 或落在 VM 区间 → WASM 的 host import 映射没接对，
  用 `wasm-objdump -j import po.wasm` 对照 `native_pow._host_imports` 的键名逐个对齐。
- `success=false` 但 `host_sum` 已正常 → 时序侧信道：增大 `RenderClock.frames`、
  校准 `RAF_FRAME_MS`（不同刷新率不是 16.6）、或让 `std_ms` 落到真 Chrome 观测区间。
- 想快速验证"原生替代 happy-dom"的等价性，先跑 `python native_pow.py` 看 demo 输出。

## 6. 合规提醒
仅在你自有授权账户 + 题面声明的零金额校验测试端点运行。不得用于未授权访问或欺诈。
