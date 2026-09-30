# mytests — storage read/write sets from greed

Extracts, for every storage slot a contract reads or writes, **which slot**, **under
what condition**, and **on how many execution paths**, by running greed's symbolic
execution and collecting `SLOAD`/`SSTORE` per path.

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

## Files

| file | purpose |
|---|---|
| `Max.sol` | test contract: plain slots, storage-to-storage flow, chained PHI, arrays, mapping |
| `greed_rwset.py` | runs greed, hooks `SLOAD`/`SSTORE`, prints the read/write set |
| `greed_cond.py` | renders yices path conditions in source-level terms |
| `cfg.py` | draws the Gigahorse CFG (useful for mapping statement ids to blocks) |
| `Makefile` | the three pipeline stages |

`greed_rwset.py` also writes `gtarget/GreedAccess.csv` as
`(path, op, slot, statement)`. The statement ids match Gigahorse's own, so that file
can be joined against a Gigahorse Datalog client if you want the static side too.

## Prerequisites

A Gigahorse checkout, `souffle`, `solc`, `graphviz`, and a Python virtualenv with
greed installed. **Do not run greed's `setup.sh` on macOS** — it is built around
`dpkg`/`apt` and will fail at the dependency check. Replicate it instead.

### The yices trap

greed pins yices2 to commit `8e6297e` (a 2.6.x tree). **Homebrew's `yices2` formula
installs 2.7.0 and does not work**, and neither does `pip install yices` on its own.
Release 2.7.0 inserted `YICES_ARITH_FF_CONSTANT` into libyices' `term_constructor`
enum, shifting `YICES_BV_CONSTANT` from 2 to 3, while the Python bindings still
hardcode 2. Nothing crashes at import — instead every concreteness check silently
fails and greed dies deep inside parsing with:

```
AssertionError: Invalid bv_unsigned_value of non constant bitvector
```

on a value that is plainly a constant. Build the pinned version:

```bash
brew install autoconf gperf gmp
python3 -m venv ~/greed-venv && source ~/greed-venv/bin/activate
pip install --upgrade pip && pip install yices
pip install -e ~/greed

cd ~/greed
git clone https://github.com/SRI-CSL/yices2.git
cd yices2 && git checkout 8e6297e
autoconf
./configure CPPFLAGS=-I/opt/homebrew/include LDFLAGS=-L/opt/homebrew/lib
make -j8
```

The `CPPFLAGS`/`LDFLAGS` are macOS-specific: on Ubuntu `libgmp-dev` is on the default
search path, whereas Homebrew keeps gmp under `/opt/homebrew`.

The build produces `libyices.2.dylib` but no unversioned name, and the bindings load
`libyices.dylib` by bare name, so add the symlink and put the directory on the
fallback path:

```bash
L=$(echo ~/greed/yices2/build/*-release/lib)
ln -sf $L/libyices.2.dylib $L/libyices.dylib
echo "export DYLD_FALLBACK_LIBRARY_PATH=$L:\$DYLD_FALLBACK_LIBRARY_PATH" >> ~/greed-venv/bin/activate
deactivate && source ~/greed-venv/bin/activate
python -c "import yices; print(yices.Yices.version)"      # expect 2.6.5
```

`DYLD_FALLBACK_LIBRARY_PATH` is preferred over `DYLD_LIBRARY_PATH` because the
fallback list is consulted after the normal search and so cannot shadow a system
library.

## Usage

```bash
source ~/greed-venv/bin/activate
cd ~/greed/mytests

make                 # decompile + prep + greed
make decompile       # solc + gigahorse + CFG
make prep            # greed's Datalog client -> gtarget/
make greed           # greed alone; "time make greed" is greed's own cost
make clean
```

After editing `Max.sol`, rerun `make decompile && make prep`. `make greed` reuses
`gtarget/` on purpose so it can be timed, and refuses to run against a stale one
rather than reporting results for the previous contract.

Analyse something else with `make NAME=Other`, and point elsewhere for Gigahorse with
`make GIGAHORSE=/path/to/gigahorse-toolchain`.

### Flags

```bash
python greed_rwset.py gtarget --paths --witness   # every path, with concrete calldata
python greed_rwset.py gtarget --include-reverts   # keep paths that revert on overflow
python greed_rwset.py gtarget --all-constraints   # raw conjuncts, nothing filtered
```

## Reading the output

**`always`** means the access happens on every path of that function that did not
revert, so no condition distinguishes it.

**Overflow conjuncts are filtered by default.** Solidity 0.8 reverts on overflow, so
a write is also conditional on no operand sitting at a type boundary. Those read as
`stor0x1 != int256.max` and are hidden, with a count; a path that *hits* a boundary is
kept and flagged `[OVERFLOW REVERT: …]`, since that is why the access did not happen.
This matters: the source-level guard is necessary but **not sufficient**. For
`foo(int y)` the guard `c + 1 > y - 1` holds on four paths while the write occurs on
one; the other three revert because `c` is at `int256.max`, `y` at `int256.min`, or
`b` at `int256.max`.

**"provably no storage access" is only a proof when the exploration was exhaustive.**
The line above it reports the stashes; if `active` is not 0, greed stopped early and
an absent access means "not reached within budget", not "impossible". The wording
changes to say so.

## Caveats

greed consumes Gigahorse's TAC, so it inherits every decompilation gap: an unresolved
dynamic jump or `DELEGATECALL` target is invisible to both. Check Gigahorse's own
`Analytics_BlockInNoFunctions`, `Analytics_JumpToMany` and
`Analytics_NonModeledSLOAD`/`SSTORE` before trusting a result on a real contract.

Symbolic execution **under**-approximates: it reports the paths it explored, so a
missing access is not evidence of absence unless the run was exhaustive.

greed's `setup.sh` pins gigahorse to `10de8a71ca7b12f657e0de18e455e02d408089b8`. This
setup has been run against a checkout several hundred commits newer, which works but
is a deviation worth suspecting first if something breaks inexplicably.

Selectors come from the solver's model of the first four calldata bytes, not a symbol
table, so an occasional entry is a dispatcher comparison-failure path rather than a
real function — `0x0c970b2f` next to the real `0x4c970b2f`, for instance.

## macOS notes

Two things bite repeatedly. `make` and `/usr/bin/time` are Apple-signed, so SIP strips
`DYLD_*` before they start; the Makefile therefore sets the library path on the recipe
line, and `time make greed` works only because the shell builtin runs first. And
`options.STATE_INSPECT = True` must be set **before** the entry state is created, or
`state.inspect` is never registered and the `SLOAD`/`SSTORE` hooks silently never fire.
