"""Render greed/yices path-condition s-expressions in source-level terms."""
import re
M = 1 << 256

# keccak256(bytes32(k)) for small k -- the base address of dynamic array `k`
def _keccak_table(n=64):
    try:
        from Crypto.Hash import keccak
    except ImportError:
        return {}
    t = {}
    for k in range(n):
        h = keccak.new(digest_bits=256)
        h.update(k.to_bytes(32, "big"))
        t[int(h.hexdigest(), 16)] = k
    return t

KECCAK = _keccak_table()

def tokenize(s):
    return s.replace("(", " ( ").replace(")", " ) ").split()

def parse(toks, i=0):
    if toks[i] != "(":
        return toks[i], i + 1
    out, i = [], i + 1
    while toks[i] != ")":
        node, i = parse(toks, i)
        out.append(node)
    return out, i + 1

def num(x):
    if isinstance(x, str):
        if x.startswith("0b"): return int(x[2:], 2)
        if x.startswith("0x"): return int(x[2:], 16)
    return None

def sgn(v):
    return v - M if v >> 255 else v

def named(x):
    """atom -> source-level name, or None"""
    if not isinstance(x, str): return None
    m = re.fullmatch(r"READN_CALLDATA_\d+_BASE\d+_(\d+)_32", x)
    if m: return f"arg{(int(m.group(1)) - 4) // 32}"
    if x.startswith("CALLVALUE"): return "msg.value"
    if x.startswith("CALLDATASIZE"): return "calldatasize"
    return None

def lit(v):
    if v in KECCAK:
        return f"keccak256({KECCAK[v]})"
    s = sgn(v)
    if s == -1: return "-1"
    if s == -(1 << 255): return "int256.min"
    if s == (1 << 255) - 1: return "int256.max"
    return hex(v) if abs(s) > 0xffffff else str(s)

OPS = {"bv-slt": "<", "bv-sle": "<=", "bv-sgt": ">", "bv-sge": ">=",
       "bv-lt": "<u", "bv-gt": ">u", "bv-le": "<=u", "bv-ge": ">=u",
       "=": "==", "/=": "!="}

def render(n):
    if isinstance(n, str):
        nm = named(n)
        if nm: return nm
        v = num(n)
        return lit(v) if v is not None else n
    if not n: return "()"
    head = n[0]

    # storage select: (STORAGE_x_y_z 0xN) -> stor0xN
    if isinstance(head, str) and head.startswith("STORAGE"):
        k = num(n[1])
        return f"stor{hex(k)}" if k is not None else f"stor[{render(n[1])}]"

    # selector extraction -> "selector"
    if head == "bv-zero-extend" or head == "bv-extract":
        inner = " ".join(str(x) for x in n)
        if "arg-1" in inner or "CALLDATA" in inner:
            return "selector"

    if head == "bv-add":
        # (/= 0x0 (bv-add C X)) style handled by caller; here just infix
        parts = [render(x) for x in n[1:]]
        consts = [num(x) for x in n[1:] if num(x) is not None]
        if len(n) == 3 and consts:
            c = consts[0]
            other = [x for x in n[1:] if num(x) is None]
            if other:
                if c in KECCAK:
                    return f"(keccak256({KECCAK[c]}) + {render(other[0])})"
                s = sgn(c)
                sign = "-" if s < 0 else "+"
                return f"({render(other[0])} {sign} {abs(s) if abs(s)<0xffffff else hex(c)})"
        return "(" + " + ".join(parts) + ")"

    if head in OPS:
        a, b = n[1], n[2]
        # (/= 0x0 (bv-add C X))  ->  X != -C
        if head in ("=", "/=") and num(a) == 0 and isinstance(b, list) and b[0] == "bv-add":
            cs = [num(x) for x in b[1:] if num(x) is not None]
            xs = [x for x in b[1:] if num(x) is None]
            if len(cs) == 1 and len(xs) == 1:
                return f"{render(xs[0])} {OPS[head]} {lit((-cs[0]) % M)}"
        return f"({render(a)} {OPS[head]} {render(b)})"

    return "(" + " ".join(render(x) for x in n) + ")"

def pretty(text):
    toks = tokenize(text)
    try:
        tree, _ = parse(toks)
    except Exception:
        return text[:200]
    return render(tree)

def is_definition(text):
    """Constraints that merely DEFINE a symbol, not conditions on execution."""
    if "bv-concat" in text and ("CALLDATA_" in text or "_MEMORY_" in text):
        return True
    return "_MEMORY_" in text          # SHA3_n_MEMORY byte constraints
