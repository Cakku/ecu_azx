# Top-level entry points (brief H6, 2026-09-24).  Patch builds live in
# patches/<name>/Makefile; this file only wraps repo-wide tools.
#
#   make help        list the targets
#   make bench-kit   build the four bench-day images + MANIFEST.json + README.md
#                    into work/bench_kit (docs/08 step 5); OUT=work/<dir> to move it
#
# Nothing here writes to an ECU, and nothing here writes to data/.

PYTHON ?= ./.venv/bin/python3
OUT    ?= work/bench_kit

.PHONY: help bench-kit
help:
	@echo "make bench-kit [OUT=work/bench_kit]"
	@echo "    tools/bench_kit.py: 00_stock_resaved, 10_ff_counter_both,"
	@echo "    11_ff_counter_external, 20_ff_fuel_shipped + MANIFEST.json + README.md"
	@echo "    (checksum verify, bindiff, expected flash CRC per image; docs/08 step 5)"
	@echo "make help"
	@echo "    this list"

bench-kit:
	$(PYTHON) tools/bench_kit.py --out $(OUT)
