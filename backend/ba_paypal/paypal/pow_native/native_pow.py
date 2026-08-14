"""
native_pow.py — Ghost in the Browser · direction A 参考实现
========================================================
把验证方的 PoW WASM 放进【原生 WASM 运行时】（wasmer / wasmtime / 自研），
在运行时内【重写它 import 的浏览器宿主函数】，并让这些函数返回与真 Chrome
在同一声称画像下一致的环境值，同时用高精度时钟驱动 ~16.6ms 渲染帧聚合，
对齐 rAF / rIC 时序侧信道。

这是 zero-browser 解法的核心：不启动任何浏览器引擎，只在原生代码里"扮演"
浏览器宿主。运行的是题方原样 WASM（黑盒），只替换 host imports，因此
连 WASM 逆向都省了，且与服务端校验 100% 一致。

依赖（任选其一，缺省走 demo 模式）：
  - pywasmer        : pip install wasmer wasmer_compiler_cranelift
  - wasmtime-py     : pip install wasmtime
  - 纯 Python demo  : 无依赖，仅演示协议封包（host_sum 用参考公式算，非真 WASM）

使用前把抓到的 PoW WASM 放到同目录 po.wasm（见 README_solver.md）。
"""

from __future__ import annotations
import ctypes
import json
import math
import os
import struct
import time
from dataclasses import dataclass, field, asdict
from typing import Callable, Optional


# ----------------------------------------------------------------------------
# 1) 设备画像：与你在 TLS/JA4/UA 里声明的桌面浏览器画像【必须自洽】
#    这里以 "中配桌面 Chrome" 为例。换画像就改这里 + UA。
# ----------------------------------------------------------------------------
@dataclass
class BrowserProfile:
    hardware_concurrency: int = 8      # navigator.hardwareConcurrency
    device_memory: float = 8.0         # navigator.deviceMemory (GB)
    screen_width: int = 1920
    screen_height: int = 1080
    device_pixel_ratio: float = 1.0
    avail_width: int = 1920
    avail_height: int = 1040
    platform: str = "Win32"
    languages: list[str] = field(default_factory=lambda: ["en-US", "en"])
    max_touch_points: int = 0
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )


# 16.6ms ≈ 60fps 渲染帧；rAF 触发节律的标称周期（方向 B 时序侧信道对齐）
RAF_FRAME_MS = 1000.0 / 60.0
# rIC（requestIdleCallback）宏任务节律抖动的标称标准差（真 Chrome 观测区间）
RIC_SIGMA_MS = 1.8


class VirtualTimeline:
    """统一虚拟时间域（回应 gpt-5.6-sol C.1 / E.7 / E.8）。

    关键修正：rAF 是渲染调度语义，不是每 16.6ms 返回的时钟。上一版的
    '固定种子 LCG + 真实 sleep'既无法复制帧调度的因果语义，固定抖动反而
    成为更强指纹（C.2）。这里改用：

      - 内部时间：确定性虚拟时钟，由调用/事件状态推进，不随意真实 sleep，
        满足模块自身一致性检查；
      - 服务端可见时间：仅在发送前按需决定是否需要匹配真实墙钟耗时，
        与内部时间解耦（E.8）。
    """
    # time origin 固定为 0，与浏览器 performance.timeOrigin 语义对齐（仅内部用）
    def __init__(self, frame_ms: float = RAF_FRAME_MS, time_origin: float = 0.0):
        self.frame_ms = frame_ms
        self.time_origin = time_origin
        self._mono = time.perf_counter_ns() / 1_000_000.0  # 真实单调起点
        self._virtual = time_origin                       # 虚拟现在（ms）
        self.frame_index = 0
        # 帧推进采用确定性增量（三角波式节流，避免固定种子成指纹）
        self._phase = 0

    def now(self) -> float:
        """performance.now() 等价（虚拟，ms）。"""
        return self._virtual - self.time_origin

    def wall_now(self) -> float:
        """真实墙钟（ms，相对 mono 起点），供必要时对比服务端可见时间。"""
        return time.perf_counter_ns() / 1_000_000.0 - self._mono

    def advance_computation(self, busy_ms: float):
        """用真实 busy-wait 推进内部虚拟时间，使'工作量—耗时'联合分布真实
        （回应 C.4：匹配工作量相关耗时，而非独立加抖动）。"""
        busy = max(0.0, busy_ms)
        # 用单调时钟测量真实忙等，避免 OS 调度造成的虚假睡眠
        tgt = time.perf_counter_ns() + int(busy * 1_000_000)
        while time.perf_counter_ns() < tgt:
            pass
        self._virtual += busy

    def tick_frame(self) -> float:
        """推进一帧：帧间隔随时间做确定性节流（掉帧/回复），模拟可见/节流，
        不向服务端裸报固定抖动。返回本帧虚拟延迟(ms)。"""
        self.frame_index += 1
        self._phase = (self._phase + 1) % 7
        # 三角波：在某些帧制造 0/± 抖动，体现可见性/节流，而非高斯固定指纹
        jitter = (self._phase - 3) * 0.6  # -1.8 .. +1.8 ms 周期变化
        self._virtual += max(0.0, self.frame_ms + jitter)
        return self.frame_ms + jitter

    @property
    def expected_frame(self) -> float:
        return self.frame_index * self.frame_ms


# ----------------------------------------------------------------------------
# 2) 原生 WASM 运行时封装
# ----------------------------------------------------------------------------
class NativePow:
    def __init__(self, profile: BrowserProfile, wasm_path: Optional[str] = None):
        self.profile = profile
        self.clock = VirtualTimeline()
        self.wasm_path = wasm_path or os.path.join(os.path.dirname(__file__), "po.wasm")
        self._backend = self._load_backend()

    # -- 后端选择 -------------------------------------------------------------
    def _load_backend(self) -> str:
        try:
            import wasmer  # noqa: F401
            return "wasmer"
        except Exception:
            pass
        try:
            import wasmtime  # noqa: F401
            return "wasmtime"
        except Exception:
            pass
        if os.path.exists(self.wasm_path):
            # 有 WASM 但没有原生绑定：提示用户安装
            print("[!] 找到 po.wasm 但未安装 wasmer/wasmtime，请 pip install wasmer wasmer_compiler_cranelift")
            return "stub"
        return "demo"

    # -- 浏览器宿主函数导入表（host imports）--------------------------------
    # 这段就是把"快乐的 DOM 空心探针"替换成与真 Chrome 自洽的真值。
    def _host_imports(self) -> dict:
        p = self.profile
        clock = self.clock

        def now_ms() -> float:
            return clock.now()

        def hardware_concurrency() -> int:
            return p.hardware_concurrency

        def device_memory() -> float:
            return p.device_memory

        def screen_w() -> int:
            return p.screen_width

        def screen_h() -> int:
            return p.screen_height

        def avail_w() -> int:
            return p.avail_width

        def avail_h() -> int:
            return p.avail_height

        def dpr() -> float:
            return p.device_pixel_ratio

        def max_touch() -> int:
            return p.max_touch_points

        def platform_ptr() -> int:
            # 真实实现里需要把字符串写进 WASM 线性内存并返回指针；
            # 这里给出契约，具体 offset 取决于 WASM 的导出内存布局。
            raise NotImplementedError("platform 字符串需按 WASM 内存布局对齐；demo 模式忽略")

        # 计数类探针——【DEMO ONLY，绝不混入真实 proof】（回应 gpt-5.6-sol B.1）
        # 真实 WASM 内部有它自己的权重/哈希，本占位公式既解释不了 4778，
        # 也不能预测成功浏览器值；真实模式必须以 WASM 返回值为准。
        def aggregate_host_sum_demo() -> int:
            return (
                p.hardware_concurrency * 137
                + int(p.device_memory) * 53
                + p.screen_width // 10
                + p.screen_height // 10
                + p.max_touch_points * 7
            )

        return {
            "env": {
                "now": now_ms,
                "hardwareConcurrency": hardware_concurrency,
                "deviceMemory": device_memory,
                "screenWidth": screen_w,
                "screenHeight": screen_h,
                "availWidth": avail_w,
                "availHeight": avail_h,
                "devicePixelRatio": dpr,
                "maxTouchPoints": max_touch,
                "platform": platform_ptr,
                "aggregateHostSum_DEMO_ONLY": aggregate_host_sum_demo,
                # 随机数：纯协议可确定性化（与画像/salt 绑定），便于复现
                "randomU32": lambda: (int(clock.now() * 1000) ^ 0x5BD1E995) & 0xFFFFFFFF,
            }
        }

    # -- 真正算 token --------------------------------------------------------
    def solve(self, challenge: dict) -> dict:
        """challenge 至少含 {n, salt}（按真实 endpoint 字段调整）。"""
        if self._backend == "demo":
            return self._demo_solve(challenge)
        # 真实后端（wasmer/wasmtime）加载并调用 WASM 的 solve 导出，
        # 把 host imports 注入。导出名按真实 WASM 调整。
        return self._real_solve(challenge)

    def _real_solve(self, challenge: dict) -> dict:
        # —— 第一步：静态解析真实 ABI，绝不猜名字（gpt-5.6-sol A.6 / E.2）——
        try:
            from wasm_abi import analyze as wasm_analyze
            abi = wasm_analyze(self.wasm_path)
        except Exception as e:
            abi = None
            print(f"[!] 无法静态解析 ABI（将尝试最小 hook）: {e}")
        if abi is not None:
            print(f"[abi] import 函数 = {[f'{i.module}.{i.field}' for i in abi.imports if i.kind=='func']}")
            print(f"[abi] export     = {[f'{e.name}' for e in abi.exports]}")
            # A.1：若 import 表里根本没有环境探针，说明画像数据由 JS 写入 memory，
            # 路线应改为复现 input buffer 而非 hook 这些名的 import。
            probe_names = {"hardwareConcurrency", "deviceMemory", "screenWidth"}
            has_probe_imports = any(
                i.field in probe_names for i in abi.imports if i.kind == "func"
            )
            if not has_probe_imports:
                print("[abi] ⚠ 未检测到环境探针 import —— 数据大概率由 JS loader "
                      "写入 linear memory（A.1）。请改用 wasm_abi 定位数据入口。")
            # E.6：仅 hook 真实存在的 import 名；未知 import 显式报错，不静默返回 0
            real_import_fields = {i.field for i in abi.imports}
        else:
            real_import_fields = set()

        if self._backend == "wasmer":
            import wasmer
            store = wasmer.Store()
            with open(self.wasm_path, "rb") as f:
                module = wasmer.Module(store, f.read())
            import_object = wasmer.ImportObject()
            for mod, fns in self._host_imports().items():
                for name, fn in fns.items():
                    # name 形如 aggregateHostSum_DEMO_ONLY：真实模式不注入 demo 占位
                    if name.endswith("_DEMO_ONLY"):
                        continue
                    # E.6：若静态解析存在且本名不在真实 import 表，显式跳过而非静默返回 0
                    if real_import_fields and name not in real_import_fields:
                        continue
                    import_object.register(mod, {name: fn})
            instance = wasmer.Instance(module, import_object)
            # 对齐渲染帧节律（虚拟时间推进，不裸报固定抖动）：在调用主干前/中按帧推进
            for _ in range(challenge.get("frames", 1)):
                self.clock.tick_frame()
            # 调用真实 export：优先用 ABI 解析出的导出名（A.6 修正）
            export_names = [e.name for e in (abi.exports if abi else [])]
            if not export_names:
                export_names = ["solve", "mint", "compute", "pow"]  # 兜底
            last_err = None
            for export in export_names:
                if hasattr(instance.exports, export):
                    try:
                        result = getattr(instance.exports, export)(
                            challenge["n"], challenge.get("salt", b"")
                        )
                        return self._wrap_token(result, challenge)
                    except Exception as e:  # trap/ABI 错误：先定位，不吞掉伪装成功（E.10）
                        last_err = e
                        # E.6：未知 import 触发 trap 时不要伪造 0，保存现场
                        raise RuntimeError(
                            f"WASM 导出 {export} 调用失败（trap/ABI）：{e} —— "
                            f"先查 wasm_abi 定位缺失 import 的内存/调用顺序"
                        ) from e
            if last_err:
                raise last_err
            raise RuntimeError("WASM 未找到可用导出；请对照 wasm_abi 输出调整")
        # wasmtime 分支略（结构相同），提示用户参照 wasmer 分支实现
        raise NotImplementedError("wasmtime 分支请参照 wasmer 实现 ImportObject 映射")

    def _wrap_token(self, raw, challenge: dict) -> dict:
        """把 WASM 返回值封成验证接口期望的 proof token 包。

        host_sum 必须来自 WASM 内部真实返回值（的位置需经 wasm_abi E.3 定位），
        绝不使用 demo 占位公式。
        """
        # raw 可能是 int（单返回值）或带属性的对象；host_sum 由调用方从正确位置取
        token = {
            "n": challenge.get("n"),
            "salt": challenge.get("salt"),
            "host_sum": getattr(raw, "host_sum", None),
            "timing": {
                "frame_index": self.clock.frame_index,
                "expected_ms": round(self.clock.expected_frame, 2),
                "virtual_now_ms": round(self.clock.now(), 3),
            },
            "profile": asdict(self.profile),
        }
        return token

    # -- demo 模式：无 WASM / 无原生绑定时，演示协议封包 ----------------
    def _demo_solve(self, challenge: dict) -> dict:
        print("[demo] 未加载真实 po.wasm —— 仅演示协议封包；host_sum 为 DEMO 占位，"
              "绝不代入真实 proof（gpt-5.6-sol B.1）")
        p = self.profile
        # DEMO 占位：仅用于说明“若真有环境累加通道，host_sum 应与画像绑定”。
        # 真实值必须来自 WASM 返回值（经 wasm_abi E.3 定位），且 4778 更可能是
        # 失败哨兵/初始化校验和，而非环境累加（gpt-5.6-sol D.4）。
        host_sum = (
            p.hardware_concurrency * 137
            + int(p.device_memory) * 53
            + p.screen_width // 10
            + p.screen_height // 10
            + p.max_touch_points * 7
        )
        # 帧推进走虚拟时间线（不裸报固定抖动；C.1/C.2）
        frames = challenge.get("frames", 8)
        deltas = []
        for _ in range(frames):
            deltas.append(self.clock.tick_frame())
        token = {
            "n": challenge.get("n"),
            "salt": challenge.get("salt"),
            "host_sum": host_sum,
            "timing": {
                "frames": frames,
                "mean_frame_ms": round(sum(deltas) / len(deltas), 3),
                "std_ms": round(self._std(deltas), 3),
                "expected_ms": round(self.clock.expected_frame, 2),
                "virtual_now_ms": round(self.clock.now(), 3),
            },
            "profile": asdict(self.profile),
        }
        print(f"[demo] host_sum(占位)={host_sum}  mean_frame={token['timing']['mean_frame_ms']}ms "
              f"(题面 VM 死值 4778 可能是 sentinel，非空环境累加)")
        return token

    @staticmethod
    def _std(xs):
        n = len(xs)
        if n < 2:
            return 0.0
        m = sum(xs) / n
        return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))


if __name__ == "__main__":
    rt = NativePow(BrowserProfile())
    out = rt.solve({"n": 1, "salt": b"passive", "frames": 8})
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
