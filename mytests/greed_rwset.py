#!/usr/bin/env python3
"""Run greed on a Gigahorse output folder and emit a per-path storage read/write set.

Prints, for every storage access greed actually reached, the source-level condition
under which it happens (decoded from greed's path condition by greed_cond.py).

Also writes <target>/GreedAccess.csv with one row per (path, op, slot, statement),
which rw_client.dl reads back as an input relation and joins on the statement id.
"""
import argparse, logging, os, re, sys, time
logging.basicConfig(level=logging.ERROR, format="%(levelname)s | %(name)s | %(message)s")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import yices
import greed_cond
from greed import Project, options
from greed.solver.shortcuts import BVV, NotEqual, is_concrete, bv_unsigned_value
from greed.utils.extra import gen_exec_id


def identify(state, word, cand):
    """Name a 256-bit term by PROVING it equals a candidate (not by sampling a model).

    Returns a hex literal if the term is pinned to a constant, else "argN" / "stor0xN"
    when it provably equals that quantity, else None.
    """
    try:
        model = state.solver.eval(word)
        if state.solver.is_formula_unsat(NotEqual(word, BVV(model, 256))):
            return hex(model)                      # uniquely determined
    except Exception:
        pass
    for name, term in cand.items():
        try:
            if state.solver.is_formula_unsat(NotEqual(word, term)):
                return name
        except Exception:
            continue
    return None


def candidates(state):
    """Named quantities a keccak preimage word might be: calldata args and slots."""
    out = {}
    for i in range(4):
        try:
            out[f"arg{i}"] = state.calldata.readn(BVV(4 + 32 * i, 256), BVV(32, 256))
        except Exception:
            break
    for slot in range(8):
        try:
            out[f"stor{hex(slot)}"] = state.storage[BVV(slot, 256)]
        except Exception:
            break
    return out


def sha_names(state):
    """Map each SHA3_n symbol to a readable description of its keccak preimage.

    A 32-byte preimage is a dynamic-array base, keccak256(slot). A 64-byte preimage is
    a mapping entry, keccak256(key . slot). Each word is identified by proof, so a
    caller-chosen key shows as argN rather than a sampled value.
    """
    out = {}
    try:
        state = state.copy()          # reads instantiate lambda constraints; keep them
    except Exception:                 # off the live state
        pass
    cand = candidates(state)
    for sha in (getattr(state, "sha_observed", []) or []):
        try:
            name = re.search(r"SHA3_\d+", str(sha.symbol)).group(0)
            if not is_concrete(sha.size):
                continue
            nwords = bv_unsigned_value(sha.size) // 32
            if nwords < 1:
                continue
            parts = []
            for w in range(nwords):
                word = sha.readn(BVV(w * 32, 256), BVV(32, 256))
                parts.append(identify(state, word, cand) or "?")
        except Exception:
            continue
        out[name] = f"keccak256({'|'.join(parts)})"
    return out


def slot_of(state, stmt):
    """The slot address: a concrete number when the solver pinned it, otherwise the
    symbolic expression rendered in source terms (e.g. "keccak256(2) + arg0")."""
    term = None
    for getter in (lambda: stmt.arg1_val,
                   lambda: state.registers.get(stmt.arg1_var, None)):
        try:
            v = getter()
        except Exception:
            continue
        if v is None:
            continue
        if is_concrete(v):
            return hex(bv_unsigned_value(v))
        term = v
        break
    if term is None:
        return "?"
    try:
        raw = yices.Terms.to_string(term.id, width=100000)
        text = re.sub(r"\s+", " ", greed_cond.pretty(raw)).strip()
        for name, desc in sha_names(state).items():
            text = text.replace(name, desc)
        return text or "SYMBOLIC"
    except Exception:
        return "SYMBOLIC"


# "stor0x1 != int256.max" / "arg0 == int256.min" -> a Solidity 0.8 bound check
BOUND = re.compile(r"^(?P<var>[\w.]+)\s*(?P<op>!=|==)\s*(?P<lim>int256\.(?:max|min))$")

# "(SHA3_1 == keccak256(0))" / "(keccak256(2) == SHA3_3)" -> sha_resolver pinning a
# hash symbol to its computed value. Definitional bookkeeping, not a condition.
SHA_DEF = re.compile(r"^\(\s*(?:SHA3_\d+\s*==\s*[^=]+|[^=]+\s*==\s*SHA3_\d+)\s*\)$")


def path_condition(state, keep_all=False):
    """Decode greed's path condition, split into (guard, reverts, n_hidden).

    guard    : the real source-level condition
    reverts  : bound checks this path HITS, i.e. why it reverted
    n_hidden : bound checks this path AVOIDS (pure preconditions)
    """
    guard, reverts, hidden = [], [], 0
    for c in state.constraints:
        try:
            raw = yices.Terms.to_string(c.id, width=100000)
        except Exception:
            continue
        if greed_cond.is_definition(raw):      # calldata byte-concat definition
            continue
        pretty = greed_cond.pretty(raw)
        if not keep_all and ("selector" in pretty or "msg.value" in pretty):
            continue                           # dispatcher / callvalue boilerplate
        if pretty in ("true", "false"):
            continue           # vacuous conjunct
        if not keep_all and SHA_DEF.match(pretty):
            continue           # sha_resolver pinning SHA3_n to its value
        m = BOUND.match(pretty)
        if m and not keep_all:
            if m.group("op") == "==":
                reverts.append(f"{m.group('var')} at {m.group('lim')}")
            else:
                hidden += 1
            continue
        guard.append(pretty)
    return sorted(set(guard), key=len), sorted(set(reverts)), hidden


def fmt_condition(triple):
    guard, reverts, _hidden = triple
    text = "  AND  ".join(guard) or "(unconditional)"
    if reverts:
        text += "   [OVERFLOW REVERT: " + "; ".join(reverts) + "]"
    return text


def reverted(triple):
    return bool(triple[1])


def main(args):
    options.STATE_INSPECT = True          # must precede entry_state
    options.SOLVER_TIMEOUT = args.timeout
    options.MAX_CALLDATA_SIZE = 1024
    options.GREEDY_SHA = True
    options.MAX_SHA_SIZE = 512
    options.OPTIMISTIC_CALL_RESULTS = True
    options.DEFAULT_EXTCODESIZE = True

    p = Project(target_dir=args.target)
    init_ctx = {"CALLDATASIZE": options.MAX_CALLDATA_SIZE,
                "CALLER":  "0xaaA4a5495988E18c036436953AC87aadEa074550",
                "ORIGIN":  "0xaaA4a5495988E18c036436953AC87aadEa074550",
                "ADDRESS": "0x42"}
    entry = p.factory.entry_state(xid=gen_exec_id(), init_ctx=init_ctx)

    # accumulate per state; globals.copy() deep-copies, so forks do not share
    def make_cb(op):
        def cb(simgr, state):
            rec = (op, slot_of(state, state.curr_stmt), state.curr_stmt.id)
            state.globals["rw"] = state.globals.get("rw", ()) + (rec,)
        return cb

    entry.inspect.stop_at_stmt(stmt_name="SLOAD",  func=make_cb("R"))
    entry.inspect.stop_at_stmt(stmt_name="SSTORE", func=make_cb("W"))

    simgr = p.factory.simgr(entry_state=entry)
    t0 = time.time()
    simgr.run()
    elapsed = time.time() - t0

    rows, sels, acc_of, cond_of = [], {}, {}, {}
    print(f"explored {len(simgr.deadended)} paths in {elapsed:.2f}s")
    for i, s in enumerate(simgr.deadended):
        seen = list(dict.fromkeys(s.globals.get("rw", ())))   # dedupe, keep order
        acc_of[i] = seen
        for op, slot, stmt in seen:
            rows.append((i, op, slot, stmt))
        try:
            assert s.solver.is_sat()
            cd = str(s.solver.eval_memory(s.calldata, BVV(36, 256)))
            sel = "0x" + cd[:8]
        except Exception:
            cd, sel = "", "?"
        sels[i] = sel
        cond_of[i] = path_condition(s, args.all_constraints)
        if args.paths:
            acc = ", ".join(f"{o} {sl}" for o, sl, _ in seen) or "(none)"
            print(f"  path {i}: selector={sel}  accesses={acc}")
            if args.witness and cd:
                print(f"           witness calldata=0x{cd}")

    stashes = {k: len(v) for k, v in simgr.stashes.items()}
    exhaustive = all(n == 0 for k, n in stashes.items() if k != "deadended")
    print(f"\nstashes: {stashes}")
    print(f"exhaustive (nothing left unexplored): {exhaustive}")

    # ---- read/write set with conditions, grouped per (function, op, slot) ----
    grouped = {}
    for i, sel in sels.items():
        for op, slot, _stmt in acc_of[i]:
            grouped.setdefault((sel, op, slot), []).append(i)

    print("\n=== R/W SET with conditions (greed only) ===")
    print(f"{'function':<12} {'op':<3} {'slot':<8} condition")
    # paths per function that ran to completion (no overflow revert)
    live = {}
    for i, sel in sels.items():
        if not reverted(cond_of[i]) or args.include_reverts:
            live.setdefault(sel, set()).add(i)
    dropped = 0
    for (sel, op, slot), paths in sorted(grouped.items()):
        keep = set()
        for i in paths:
            if reverted(cond_of[i]) and not args.include_reverts:
                dropped += 1
            else:
                keep.add(i)
        if not keep:
            continue
        if keep == live.get(sel, set()):          # happens on every live path
            print(f"{sel:<12} {op:<3} {slot:<8} always")
            continue
        conds = {}
        for i in sorted(keep):
            conds.setdefault(fmt_condition(cond_of[i]), i)
        for cond in sorted(conds, key=len):
            print(f"{sel:<12} {op:<3} {slot:<8} {cond}")
    if dropped and not args.include_reverts:
        print(f"\n({dropped} path-accesses on reverting paths hidden; "
              f"--include-reverts to show)")

    silent = sorted({s for s in sels.values() if not any(k[0] == s for k in grouped)})
    if silent:
        note = "provably no storage access" if exhaustive \
               else "none found WITHIN BUDGET - not a proof"
        print(f"reached but no storage access ({note}):")
        for s in silent:
            print(f"  {s}")

    out = os.path.join(args.target, "GreedAccess.csv")
    with open(out, "w") as f:
        for r in rows:
            f.write("\t".join(str(x) for x in r) + "\n")
    print(f"\nwrote {len(rows)} rows to {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("target")
    ap.add_argument("--timeout", type=int, default=60)
    ap.add_argument("--paths", action="store_true", help="also list every path")
    ap.add_argument("--witness", action="store_true", help="with --paths, show calldata")
    ap.add_argument("--include-reverts", action="store_true",
                    help="also show accesses on paths that revert on overflow")
    ap.add_argument("--all-constraints", action="store_true",
                    help="keep every raw conjunct, including bound checks")
    main(ap.parse_args())
