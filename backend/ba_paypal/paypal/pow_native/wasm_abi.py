"""
wasm_abi.py — PoW WASM 静态 ABI 解析器（零依赖，纯标准库）
========================================================
给 gpt-5.6-sol 批评的"不要猜 import 名 / 定位 host_sum 首次出处"补工具链。

它枚举（不执行、不实例化）：
  - custom sections（常含 wasm 构建版本、调试名 producer/sourceMappingURL）
  - import 表：module/field 名 + kind（func/table/mem/global/tag）+ func 的参数/返回
  - export 表：name + kind + index
  - start section、data/element 段计数
  - 若携带 names 自定义段（name section），可解析局部/函数名

这样在拿到真实 po.wasm 后，无需 wasmer/wasmtime 就能先把 ABI 完整枚举出来，
再据此决定：数据是走 import 还是走 JS 写入的 memory input buffer（gpt-5.6-sol A.1）。

用法：
    python wasm_abi.py po.wasm
    python wasm_abi.py po.wasm --json > abi.json
"""
from __future__ import annotations
import json
import struct
import sys
from dataclasses import dataclass, field, asdict
from typing import Optional


VALTYPE = {
    0x7F: "i32", 0x7E: "i64", 0x7D: "f32", 0x7C: "f64",
    0x7B: "v128", 0x70: "funcref", 0x6F: "externref",
}
IMPORT_KIND = {0: "func", 1: "table", 2: "mem", 3: "global", 4: "tag"}
EXPORT_KIND = {0: "func", 1: "table", 2: "mem", 3: "global", 4: "tag"}
SECTION_NAME = {
    0: "custom", 1: "type", 2: "import", 3: "function", 4: "table",
    5: "memory", 6: "global", 7: "export", 8: "start", 9: "element",
    10: "code", 11: "data", 12: "datacount",
}


class Reader:
    def __init__(self, buf: bytes):
        self.b = buf
        self.i = 0

    def byte(self) -> int:
        v = self.b[self.i]
        self.i += 1
        return v

    def u32_leb(self) -> int:
        result = 0
        shift = 0
        while True:
            byte = self.byte()
            result |= (byte & 0x7F) << shift
            if not (byte & 0x80):
                break
            shift += 7
        return result

    def bytes(self, n: int) -> bytes:
        out = self.b[self.i:self.i + n]
        self.i += n
        return out

    def name(self) -> str:
        n = self.u32_leb()
        return self.bytes(n).decode("utf-8", "replace")

    def vec(self, fn):
        n = self.u32_leb()
        return [fn() for _ in range(n)]

    def left(self) -> int:
        return len(self.b) - self.i


@dataclass
class ImportEntry:
    module: str
    field: str
    kind: str
    # func: 参数/返回 valtype 列表；其余 kind 仅记 kind
    params: list[str] = field(default_factory=list)
    results: list[str] = field(default_factory=list)


@dataclass
class ExportEntry:
    name: str
    kind: str
    index: int


@dataclass
class AbiResult:
    file: str
    size: int
    sha256: str
    imports: list[ImportEntry] = field(default_factory=list)
    exports: list[ExportEntry] = field(default_factory=list)
    custom_sections: list[str] = field(default_factory=list)
    custom_detail: dict = field(default_factory=dict)
    start: Optional[int] = None
    data_segments: int = 0
    element_segments: int = 0
    func_count: int = 0          # from function section (definitions)
    import_func_count: int = 0
    named_functions: dict = field(default_factory=dict)  # index -> name (from name section)
    notes: list[str] = field(default_factory=list)


def parse_valtype(r: Reader) -> str:
    return VALTYPE.get(r.byte(), "unknown")


def parse_type_section(r: Reader) -> list[tuple[list[str], list[str]]]:
    """返回 list of (params, results)。"""
    types = []
    def functype():
        assert r.byte() == 0x60, "expected functype 0x60"
        params = r.vec(lambda: parse_valtype(r))
        results = r.vec(lambda: parse_valtype(r))
        return (params, results)
    return r.vec(functype)


def parse_import_section(r: Reader, type_map) -> list[ImportEntry]:
    out: list[ImportEntry] = []
    def one():
        module = r.name()
        field = r.name()
        kind = IMPORT_KIND.get(r.byte(), "unknown")
        if kind == "func":
            tidx = r.u32_leb()
            params, results = type_map[tidx] if tidx < len(type_map) else ([], [])
            return ImportEntry(module, field, kind, params, results)
        elif kind == "table":
            r.byte()           # elemtype
            _ = r.u32_leb()     # limits flag + min (+max)
            if _ & 0x01:
                r.u32_leb()
            return ImportEntry(module, field, kind)
        elif kind == "mem":
            _ = r.u32_leb()
            if _ & 0x01:
                r.u32_leb()
            return ImportEntry(module, field, kind)
        elif kind == "global":
            r.byte()           # valtype
            r.byte()           # mut
            return ImportEntry(module, field, kind)
        else:  # tag
            r.u32_leb()        # typeidx
            return ImportEntry(module, field, kind)
    return r.vec(one)


def parse_export_section(r: Reader) -> list[ExportEntry]:
    def one():
        name = r.name()
        kind = EXPORT_KIND.get(r.byte(), "unknown")
        idx = r.u32_leb()
        return ExportEntry(name, kind, idx)
    return r.vec(one)


def parse_name_section(payload: bytes) -> dict:
    """解析 name 自定义段，返回 funcidx->name。"""
    r = Reader(payload)
    out: dict = {}
    def sub():
        t = r.byte()
        _ = r.u32_leb()  # sub-len（未用，内容直接消费）
        if t in (1, 2):  # module/function/... 我们只关心 function(2)
            pass
        # 简化：仅支持 function names (type 2)
    # 只解析 function names (type=2)
    names: dict = {}
    try:
        while r.left() > 0:
            t = r.byte()
            sub_len = r.u32_leb()
            end = r.i + sub_len
            if t == 2:
                cnt = r.u32_leb()
                for _ in range(cnt):
                    idx = r.u32_leb()
                    nm = r.name()
                    names[idx] = nm
            r.i = end  # 跳到下一个子段
    except Exception:
        pass
    return names


def analyze(path: str) -> AbiResult:
    with open(path, "rb") as f:
        data = f.read()
    import hashlib
    sha = hashlib.sha256(data).hexdigest()

    res = AbiResult(file=path, size=len(data), sha256=sha)
    if data[:4] != b"\x00asm":
        res.notes.append("不是合法 WASM（magic 不匹配）")
        return res

    r = Reader(data)
    r.i = 8  # 跳过 magic+version
    type_map: list = []
    # 先扫描各段
    while r.left() > 0:
        sid = r.byte()
        slen = r.u32_leb()
        start = r.i
        if sid == 1:  # type
            type_map = parse_type_section(r)
        elif sid == 2:  # import
            imps = parse_import_section(r, type_map)
            res.imports.extend(imps)
            res.import_func_count = sum(1 for x in imps if x.kind == "func")
        elif sid == 3:  # function (defs)
            res.func_count = r.u32_leb()
            # 跳过函数索引列表
            cnt = res.func_count
            for _ in range(cnt):
                r.u32_leb()
        elif sid == 7:  # export
            res.exports.extend(parse_export_section(r))
        elif sid == 8:  # start
            res.start = r.u32_leb()
        elif sid == 9:  # element
            res.element_segments = r.u32_leb()
            # 不深入解析
            r.i = start + slen
        elif sid == 11:  # data
            res.data_segments = r.u32_leb()
            r.i = start + slen
        elif sid == 0:  # custom
            cname = r.name()
            payload = data[r.i:start + slen]
            if cname == "name":
                res.named_functions = parse_name_section(payload)
            else:
                res.custom_sections.append(cname)
                res.custom_detail.setdefault(cname, len(payload))
            res.custom_detail.setdefault("custom_section_count", 0)
            res.custom_detail["custom_section_count"] += 1
        # 其余段（4,5,6,10,12）跳过
        r.i = start + slen  # 用段长度对齐，避免解析错误拖累整体

    # 给 export 函数补名字
    for e in res.exports:
        if e.kind == "func" and e.index in res.named_functions:
            e.name = e.name  # name 已存在；此处仅用于说明可关联
    res.notes.append(f"import 函数数={res.import_func_count}, 定义函数数={res.func_count}")
    res.notes.append("提示：gpt-5.6-sol A.1 — 若画像数据由 JS 写入 memory，"
                     "则 import 表里可能根本无环境探针；先确认数据入口。")
    res.notes.append("提示：E.3 — 拿 export 表对照 loader 调用顺序，定位 host_sum "
                     "首次写入位置（RAM 段/导出函数返回值/服务端响应）。")
    return res


def main():
    if len(sys.argv) < 2:
        print("usage: python wasm_abi.py <po.wasm> [--json]", file=sys.stderr)
        raise SystemExit(2)
    path = sys.argv[1]
    as_json = "--json" in sys.argv
    res = analyze(path)
    if as_json:
        print(json.dumps(asdict(res), ensure_ascii=False, indent=2))
    else:
        print(f"file   : {res.file}  ({res.size} bytes)")
        print(f"sha256 : {res.sha256}")
        print(f"imports({len(res.imports)}):")
        for im in res.imports:
            if im.kind == "func":
                print(f"  [func] {im.module}.{im.field}  ({','.join(im.params)} -> {','.join(im.results)})")
            else:
                print(f"  [{im.kind}] {im.module}.{im.field}")
        print(f"exports({len(res.exports)}):")
        for ex in res.exports:
            nm = res.named_functions.get(ex.index, "")
            extra = f"  (local name: {nm})" if nm else ""
            print(f"  [{ex.kind}] {ex.name} -> #{ex.index}{extra}")
        if res.custom_sections:
            print(f"custom sections: {res.custom_sections}")
        print(f"start={res.start}  data_seg={res.data_segments}  "
              f"element_seg={res.element_segments}")
        for n in res.notes:
            print(f"  · {n}")


if __name__ == "__main__":
    main()
