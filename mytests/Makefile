.PHONY: all decompile prep greed clean

# ---------------------------------------------------------------------------
# greed-only storage read/write-set extraction.
#
# Nothing is written into the gigahorse checkout; override either location on
# the command line, e.g.  make GIGAHORSE=/path/to/gigahorse-toolchain
# ---------------------------------------------------------------------------
GIGAHORSE ?= $(HOME)/gigahorse-toolchain
GREED     ?= $(HOME)/greed

# which contract to analyse
NAME = Max

# greed needs libyices 2.6.x (see README: 2.7.0 has an incompatible enum).
# macOS strips DYLD_* from signed binaries such as make, so the path has to be
# set on the recipe line itself rather than inherited from the environment.
YICES_LIB = $(wildcard $(GREED)/yices2/build/*-release/lib)

# uv is not always on PATH (the greed venv prepends its own bin), so resolve it.
UV := $(shell command -v uv 2>/dev/null || echo $(HOME)/.local/bin/uv)

# gigahorse.py -w keeps its scratch dir here rather than in the gigahorse repo
WORK = $(CURDIR)/.temp
OUT  = $(WORK)/$(NAME)/out

all: decompile prep greed

# 1. Solidity -> runtime bytecode -> Gigahorse TAC (+ a CFG picture)
decompile:
	solc --bin-runtime --overwrite -o . $(NAME).sol
	cp $(NAME).bin-runtime $(NAME).hex
	cd $(GIGAHORSE) && $(UV) run ./gigahorse.py -C clients/visualizeout.py \
	    -w $(WORK) --restart $(CURDIR)/$(NAME).hex
	python3 cfg.py $(OUT) > cfg.dot
	dot -Tpng cfg.dot -o cfg.png

# 2. greed's own Datalog client -> the metadata greed needs, in one flat dir.
#    This is Gigahorse/souffle work that greed DEPENDS on, not greed itself.
prep:
	rm -rf gtarget && mkdir -p gtarget
	souffle -F $(OUT) -D gtarget -I $(GIGAHORSE)/clientlib \
	        -L $(GIGAHORSE)/souffle-addon \
	        -M "GIGAHORSE_DIR=$(GIGAHORSE)/ BULK_ANALYSIS=" \
	        $(GREED)/resources/greed_client.dl
	cp $(OUT)/*.csv gtarget/
	cp $(NAME).hex gtarget/contract.hex

# 3. greed alone.  "time make greed" gives greed's own cost.
greed:
	@test -f gtarget/contract.hex || { echo "*** no gtarget/ - run: make prep"; exit 1; }
	@test gtarget/contract.hex -nt $(NAME).sol || \
	  { echo "*** gtarget/ is OLDER than $(NAME).sol - results would be stale."; \
	    echo "*** run:  make decompile && make prep"; exit 1; }
	DYLD_FALLBACK_LIBRARY_PATH=$(YICES_LIB) python greed_rwset.py gtarget

clean:
	rm -rf .temp out gtarget $(NAME).hex $(NAME).bin-runtime cfg.dot cfg.png
