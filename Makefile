# PRNG-371 FPGA Verification Framework
# ==========================================================================
# Targets:
#   make verify     Fast offline verification (unit + integration tests)
#   make test       Full test suite (unit + integration + system)
#   make validate   FPGA physical validation (requires ILA CSV)
#   make clean      Remove bytecode cache and temp files
#
# Environment variables:
#   ILA_CSV         Path to Vivado ILA CSV for 'make validate'
# ==========================================================================

PYTHON := python3
RUN_ALL := tests/run_all_tests.py

.PHONY: verify test validate clean help

help:
	@echo "PRNG-371 FPGA Verification Framework"
	@echo ""
	@echo "  make verify     Fast offline verification (unit + integration)"
	@echo "  make test       Full test suite (all layers)"
	@echo "  make validate   FPGA physical validation (set ILA_CSV=<path>)"
	@echo "  make clean      Remove bytecode cache"
	@echo ""
	@echo "Examples:"
	@echo "  make verify"
	@echo "  make test"
	@echo "  make validate ILA_CSV=ila_capture.csv"

verify:
	$(PYTHON) $(RUN_ALL) --quick

test:
	$(PYTHON) $(RUN_ALL)

validate:
ifndef ILA_CSV
	@echo "ERROR: Set ILA_CSV=<path> to the Vivado ILA CSV file"
	@echo "Usage: make validate ILA_CSV=ila_capture.csv"
	@exit 1
endif
	$(PYTHON) $(RUN_ALL) --csv "$(ILA_CSV)"

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name '*.pyc' -delete 2>/dev/null || true
	find . -type f -name '*.pyo' -delete 2>/dev/null || true
	@echo "Cleaned."
