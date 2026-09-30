#!/usr/bin/env python3
"""Emit a Graphviz .dot control-flow graph from Gigahorse output.

    python3 cfg.py [out-dir] > cfg.dot
    dot -Tpng cfg.dot -o cfg.png

Default out-dir is ../.temp/Max/out.

Each node is a basic block showing its full statement list, in the same
notation contract.tac uses, so a block here can be read against the matching
"Begin block" section there.

    solid black    jump
    dashed black   fallthrough
    bold blue      call into a private function
    dotted blue    return from a private function
    dashed red     edge that can never be taken
    grey box       block that can never execute

Dead code is greyed rather than removed, so that a wrong reachability result is
visible instead of silently absent, and so the JUMPI that killed a block stays
next to it as the evidence. Unreachable code is found here, from the
decompiler's own relations, and not by reading any client's output -- so this
picture stays an independent check on the analysis rather than a restatement
of it.

This script computes nothing. It reads Gigahorse's CSV relations, joins them,
and prints text in the DOT language.
"""
import csv
import os
import re
import sys

# Directory holding Gigahorse's output relations, i.e. this script's input.
OUT = sys.argv[1] if len(sys.argv) > 1 else "../.temp/Max/out"


def load(name):
    """Read one tab-separated relation into a list of rows.

    Returns an empty list if the file is absent, so a missing relation
    degrades the picture instead of crashing the script.
    """
    path = os.path.join(OUT, name)
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [r for r in csv.reader(f, delimiter="\t") if r]


# --- load the relations -----------------------------------------------------
# Each is shaped by how it will be queried: a list to iterate, a set to test
# membership, a dict to look up by key.

edges = [(a, b) for a, b in load("LocalBlockEdge.csv")]          # the CFG edges
fallthrough = {(a, b) for a, b in load("IRFallthroughEdge.csv")}  # which are not-taken
in_func = {b: f for b, f in load("InFunction.csv")}              # block -> function
names = {f: n for f, n in load("HighLevelFunctionName.csv")}     # function -> "max()"
op_of = {s: o for s, o in load("TAC_Op.csv")}                    # statement -> opcode
block_of = {s: b for s, b in load("TAC_Block.csv")}              # statement -> block
value_of = {v: val for v, val in load("TAC_Variable_Value.csv")}  # variable -> constant
calls = [(caller, func) for caller, func in load("IRFunctionCall.csv")]
call_returns = load("IRFunctionCallReturn.csv")                  # caller, callee, continuation

# function -> its return blocks
fun_returns = {}
for func, ret in load("IRFunction_Return.csv"):
    fun_returns.setdefault(func, []).append(ret)

# statement -> variables it defines, and -> (position, variable) it uses.
# Position is stored first so that sorting puts operands in the right order.
defs_by_stmt = {}
for s, v, _ in load("TAC_Def.csv"):
    defs_by_stmt.setdefault(s, []).append(v)
uses_by_stmt = {}
for s, v, n in load("TAC_Use.csv"):
    uses_by_stmt.setdefault(s, []).append((int(n), v))

# --- invert two relations, because drawing needs the opposite direction -----

# block -> its statements (block_of answers the reverse question)
stmts_by_block = {}
for stmt, block in block_of.items():
    stmts_by_block.setdefault(block, []).append(stmt)

# function -> its blocks (in_func answers the reverse question)
funcs = {}
for block, func in in_func.items():
    funcs.setdefault(func, []).append(block)


def address(stmt):
    """Sort key for a statement or block identifier.

    Identifiers are bytecode offsets, but the inliner appends a suffix to
    cloned code ("0x9eS0x35"), so int(..., 16) would fail. Take the leading
    hex run only. Without this, statements would print in dictionary order
    and not match contract.tac.
    """
    m = re.match(r"0x[0-9a-fA-F]+", stmt)
    return int(m.group(0), 16) if m else 0


def operand(var):
    """Render a variable the way contract.tac does.

    "0x4f" becomes "v4f", and if the variable has a known constant value the
    value is appended: "v4f(0x2)". Only constants appear in
    TAC_Variable_Value.csv, so the lookup failing is what marks a
    runtime-computed value.
    """
    name = "v" + var.replace("0x", "")
    val = value_of.get(var)
    return f"{name}({val})" if val else name


def statement(stmt):
    """Reassemble one IR line, e.g. "0x51: v51 = SLOAD v4f(0x2)".

    Operands are sorted by position, which matters because SUB a, b is not
    SUB b, a. Statements that define nothing, such as SSTORE, print without
    the "=" part.
    """
    op = op_of.get(stmt, "?")
    args = " ".join(operand(v) for _, v in sorted(uses_by_stmt.get(stmt, [])))
    out = ", ".join(operand(v) for v in defs_by_stmt.get(stmt, []))
    body = f"{op} {args}".strip()
    return f"{stmt}: {out} = {body}" if out else f"{stmt}: {body}"


# --- reachability ------------------------------------------------------------
# A JUMPI whose condition is a known constant has one successor that can never
# be taken. Everything only reachable through such an edge is dead code.

successors = {}
for a, b in edges:
    successors.setdefault(a, []).append(b)

# block -> True if the jump is always taken, False if it is never taken
const_cond = {}
for stmt, op in op_of.items():
    if op != "JUMPI":
        continue
    operands = dict(uses_by_stmt.get(stmt, []))
    cond = operands.get(1)          # operand 0 is the target, operand 1 the condition
    val = value_of.get(cond) if cond else None
    if val is not None:
        const_cond[block_of[stmt]] = int(val, 16) != 0

infeasible = set()
for a, b in edges:
    if a not in const_cond:
        continue
    is_fallthrough = (a, b) in fallthrough
    # condition always true -> the fallthrough dies; always false -> the jump dies
    if const_cond[a] == is_fallthrough:
        infeasible.add((a, b))

# Every function id is also that function's entry block, so the function ids are
# the roots. Private functions are entered by CALLPRIVATE, not a block edge,
# which is why each one needs to be a root of its own.
reachable = set(funcs)
stack = list(reachable)
while stack:
    block = stack.pop()
    for nxt in successors.get(block, []):
        if (block, nxt) in infeasible or nxt in reachable:
            continue
        reachable.add(nxt)
        stack.append(nxt)

dead = {b for b in in_func if b not in reachable}

# --- emit the DOT file ------------------------------------------------------

print("digraph CFG {")
print("  compound=true;")
print('  node [shape=box fontname="Courier" fontsize=9];')

# One cluster per function. A subgraph only draws a visible box if its name
# begins with "cluster" -- a Graphviz convention, not a naming choice.
for i, (func, blocks) in enumerate(sorted(funcs.items())):
    print(f"  subgraph cluster_{i} {{")
    print(f'    label="{names.get(func, func)}"; color=gray; style=rounded;')
    for block in sorted(blocks, key=address):
        lines = [statement(s) for s in sorted(stmts_by_block.get(block, []), key=address)]
        # \l is "newline, left-justified"; plain \n would centre every line.
        body = "\\l".join(lines)
        extra = ' style="filled,dashed" fillcolor="#ebebeb" color="gray50"' if block in dead else ""
        print(f'    "{block}" [label="{block}\\l{body}\\l"{extra}];')
    print("  }")

# Edges are printed after the clusters. A node belongs to whichever cluster
# declared it, so edges may appear anywhere in the file.
for a, b in edges:
    if (a, b) in infeasible:
        style = ' [style=dashed color=red]'
    elif (a, b) in fallthrough:
        style = " [style=dashed]"
    else:
        style = ""
    print(f'  "{a}" -> "{b}"{style};')

# Call edges: caller block -> callee's entry block.
# The guard skips functions the inliner absorbed entirely; without it Graphviz
# would invent a node floating outside every cluster.
for caller, func in calls:
    if caller in in_func and func in in_func:
        print(f'  "{caller}" -> "{func}" [style=bold color=blue label="call"];')

# Return edges: the callee's return block -> the caller's continuation.
for caller, func, cont in call_returns:
    for ret in fun_returns.get(func, []):
        if ret in in_func and cont in in_func:
            print(f'  "{ret}" -> "{cont}" [style=dotted color=blue label="return"];')

print("}")
