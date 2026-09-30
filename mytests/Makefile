.PHONY: all decompile prep greed clean

# ---------------------------------------------------------------------------
# Storage read/write-set extraction with greed.
#
# Targets greed's docker image (ubuntu:22.04), where everything lives under
# /home/greed. Run from inside the container with the venv activated:
#     source /home/greed/greed-venv/bin/activate
# ---------------------------------------------------------------------------
GREED     ?= /home/greed
GIGAHORSE ?= $(GREED)/gigahorse-toolchain

# which contract to analyse:  make NAME=Other
NAME = Max

# gigahorse.py -w keeps its scratch dir here rather than in the gigahorse tree
WORK = $(CURDIR)/.temp
OUT  = $(WORK)/$(NAME)/out

all: decompile prep greed

# 1. Solidity -> runtime bytecode -> Gigahorse TAC.
#    cfg.dot is written for reference; graphviz is not in the image, so render
#    it elsewhere with:  dot -Tpng cfg.dot -o cfg.png
decompile:
	solc --bin-runtime --overwrite -o . $(NAME).sol
	cp $(NAME).bin-runtime $(NAME).hex
	cd $(GIGAHORSE) && python3 ./gigahorse.py -C clients/visualizeout.py \
	    -w $(WORK) --restart $(CURDIR)/$(NAME).hex
	python3 cfg.py $(OUT) > cfg.dot

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
	python3 greed_rwset.py gtarget

clean:
	rm -rf .temp out gtarget $(NAME).hex $(NAME).bin-runtime cfg.dot cfg.png
