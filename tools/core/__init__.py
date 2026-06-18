# PRNG-371 FPGA Verification Framework — Core Library
# ==========================================================================
# tools/core/ provides reusable, testable verification primitives.
# All modules are free of CLI dependencies — they accept data, return results.
#
# Public API surface:
#   from tools.core import (
#       # utils
#       force_utf8, parse_hex64, parse_hex_any, find_column,
#       PASS_MARK, FAIL_MARK,
#       # ila_parser
#       parse_ila_csv,
#       # golden_model (SSOT)
#       GoldenModel, generate_golden,
#       # rtl_model
#       RTLModel,
#       # axi_checker
#       check_axi_writes, AxiWrite, AxiReport,
#       # prng_checker
#       AlignmentInfo, CompareResult,
#       align_to_cfg_done, extract_prng_sequence, compare_sequences,
#   )
# ==========================================================================
