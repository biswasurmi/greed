# mytests — storage read/write sets from greed

Extracts, for every storage slot a contract reads or writes: **which slot**, **under
what condition**, and **on how many execution paths**. It runs greed's symbolic
execution and collects every `SLOAD`/`SSTORE` per path.

Runs inside greed's docker image. No local Gigahorse, souffle, solc or yices install
is needed — the image has all of them.

Example output for [`Max.sol`](Max.sol):

```
function     op  slot                      condition
0x1c008df9   R   0x0                       always
0x1c008df9   W   0x1                       (5 < arg0)
0x4c970b2f   W   0x0                       ((arg0 - 1) < (stor0x1 + 1))
0x7877b803   W   0x0                       (100 < arg0)
0x3870d7c8   W   (keccak256(2) + arg0)     (arg0 <u stor0x2)
0x9bb15b07   W   keccak256(arg0|0x3)       always
0x007f24a6   W   0x405787fa…5ad0           (2 <u stor0x2)

reached but no storage access (provably no storage access):
  0xfebb0f7e
```

Functions are named by selector. Slots are concrete where the solver pins them, and
otherwise printed as an expression: `keccak256(2) + arg0` is element `arg0` of the
dynamic array whose length lives in slot 2, and `keccak256(arg0|0x3)` is the entry of
the mapping at slot 3 under key `arg0`.

---

## Quick start

Everything below is copy-paste, **one command at a time**.

### Step 1 — build the image (once, ~10 min)

On your host, with Docker running:

```
docker build --progress plain -t greed-env https://github.com/ucsb-seclab/greed.git#main:docker
```

This builds Ubuntu 22.04 with souffle 2.4, Gigahorse, yices 2.6.5 and greed. Build it
from **upstream** greed — the image only supplies the toolchain; your code arrives in
step 3.

### Step 2 — start a container

```
docker run -it greed-env bash
```

Your prompt becomes `root@<id>:/home/greed#`. Every command from here runs inside it.

### Step 3 — activate the environment

```
source /home/greed/greed-venv/bin/activate
```

### Step 4 — get this repository

```
git clone https://github.com/biswasurmi/greed.git myfork
```

### Step 5 — enter the analysis directory

```
cd myfork/mytests
```

### Step 6 — run everything

```
make
```

That runs all three stages and prints the read/write set. Expect roughly 25 seconds,
almost all of it Gigahorse decompilation; greed's own part is about 0.2 s.

---

## The three stages

`make` is `decompile` + `prep` + `greed`. Run them separately when iterating:

| step | command | what it does |
|---|---|---|
| 1 | `make decompile` | `solc` → runtime bytecode → Gigahorse TAC in `.temp/` |
| 2 | `make prep` | runs greed's `greed_client.dl` → `gtarget/` |
| 3 | `make greed` | runs greed, prints the read/write set |

`make prep` is Gigahorse/souffle work that greed *depends on*, not greed itself. Keeping
it separate is what makes `time make greed` a fair measure of greed's own cost.

Analyse a different contract by putting `Other.sol` beside `Max.sol` and running:

```
make NAME=Other
```

After editing the `.sol`, rerun `make decompile` then `make prep`. `make greed` reuses
`gtarget/` on purpose so it can be timed, and refuses to run against a stale one rather
than silently reporting the previous contract's results.

---

## Options

Run these in place of step 6's `make`:

```
python3 greed_rwset.py gtarget --paths --witness
```
lists every path with the concrete calldata that reaches it.

```
python3 greed_rwset.py gtarget --include-reverts
```
also shows accesses on paths that revert on arithmetic overflow.

```
python3 greed_rwset.py gtarget --all-constraints
```
prints every raw conjunct with nothing filtered.

---

## Reading the output

**`always`** means the access happens on every path of that function that did not
revert, so no condition distinguishes it.

**Overflow conjuncts are hidden by default.** Solidity 0.8 reverts on overflow, so a
write is also conditional on no operand sitting at a type boundary. Those read as
`stor0x1 != int256.max` and are suppressed with a count. A path that *hits* a boundary
is kept and flagged `[OVERFLOW REVERT: …]`, because that is precisely why the access did
not happen.

This distinction matters: the source-level guard is necessary but **not sufficient**.
In `foo(int y)` the guard `c + 1 > y - 1` holds on four paths while the write happens on
only one — the other three revert because `c` is at `int256.max`, `y` at `int256.min`,
or `b` at `int256.max`.

**"provably no storage access" is a proof only when the exploration was exhaustive.**
The `stashes:` line above it reports the state counts. If `active` is not `0`, greed
stopped early and an absent access means "not reached within budget", not "impossible".
The wording changes to say so.

---

## What is in this directory

| file | purpose |
|---|---|
| `Max.sol` | test contract: plain slots, storage-to-storage flow, chained PHI, array, mapping |
| `greed_rwset.py` | runs greed, hooks `SLOAD`/`SSTORE`, prints the read/write set |
| `greed_cond.py` | renders yices path conditions in source-level terms |
| `cfg.py` | writes the Gigahorse CFG as `cfg.dot` |
| `Makefile` | the three stages |

`greed_rwset.py` also writes `gtarget/GreedAccess.csv` as
`(path, op, slot, statement)`. Those statement ids are Gigahorse's own, so the file can
be joined against a Gigahorse Datalog client if you also want a static analysis.

`cfg.py` produces `cfg.dot`; graphviz is not installed in the image, so render it on
your host:

```
dot -Tpng cfg.dot -o cfg.png
```

---

## Caveats

greed consumes Gigahorse's TAC, so it inherits every decompilation gap: an unresolved
dynamic jump or `DELEGATECALL` target is invisible to both. On a real contract, check
Gigahorse's own `Analytics_BlockInNoFunctions`, `Analytics_JumpToMany` and
`Analytics_NonModeledSLOAD`/`SSTORE` in `.temp/<NAME>/out/` before trusting a result.

Symbolic execution **under**-approximates: it reports only the paths it explored, so a
missing access is not evidence of absence unless the run was exhaustive.

greed's `setup.sh` pins Gigahorse to `10de8a71ca7b12f657e0de18e455e02d408089b8`, while
the docker image clones a current revision. It works, but it is a deviation worth
suspecting first if something breaks inexplicably.

Selectors come from the solver's model of the first four calldata bytes, not a symbol
table, so an occasional entry is a dispatcher comparison-failure path rather than a real
function — `0x0c970b2f` appearing next to the real `0x4c970b2f`, for instance.

---

## Notes for anyone porting this off docker

The image exists because the native macOS path is painful. Recorded so nobody repeats
it: Apple clang rejects `-fopenmp` in Gigahorse's `souffle-addon`; boost is not on
clang's include path; Apple's `cpp` rejects `-D NAME=value` as separate arguments;
`analyze_hex.sh` uses GNU-only `/usr/bin/time -v`; and SIP strips `DYLD_*` from signed
binaries such as `make`.

The worst of them is silent. Homebrew's `yices2` formula installs **2.7.0**, which
inserted `YICES_ARITH_FF_CONSTANT` into libyices' `term_constructor` enum and shifted
`YICES_BV_CONSTANT` from 2 to 3, while the Python bindings still hardcode 2. Nothing
fails at import — every concreteness check silently returns the wrong answer and greed
dies deep inside parsing with `Invalid bv_unsigned_value of non constant bitvector` on a
value that is plainly a constant. greed pins yices to commit `8e6297e` for this reason.
Inside the image `setup.sh` handles it, so none of this applies.
