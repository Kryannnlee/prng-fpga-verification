#!/usr/bin/env python3
"""Compare FPGA-captured PRNG-371 output against a golden reference."""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple


HEX_RE = re.compile(r"(?i)(?<![0-9a-f])(?:0x)?([0-9a-f]{16})(?![0-9a-f])")


class CompareError(Exception):
    """User-facing comparison error."""


@dataclass
class CompareResult:
    golden_path: str
    capture_path: str
    input_format: str
    skip_golden: int
    skip_capture: int
    total_compared: int
    matched_words: int
    mismatched_words: int
    bit_errors: int
    ber: float
    first_mismatch_index: Optional[int]
    first_mismatch_golden: Optional[int]
    first_mismatch_capture: Optional[int]
    passed: bool


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bit-exact comparison between PRNG-371 golden words and FPGA capture."
    )
    parser.add_argument("--golden", required=True, help="Golden reference file path.")
    parser.add_argument("--capture", required=True, help="FPGA capture file path.")
    parser.add_argument(
        "--format",
        choices=("txt", "uart_hex", "ila_csv"),
        default="txt",
        help="Capture input format. Golden is always parsed as text hex.",
    )
    parser.add_argument("--max_words", type=int, default=None, help="Compare only the first N words.")
    parser.add_argument("--skip_capture", type=int, default=0, help="Skip N words from capture.")
    parser.add_argument("--skip_golden", type=int, default=0, help="Skip N words from golden.")
    parser.add_argument("--output_report", default=None, help="Optional report output path.")
    parser.add_argument("--ila_column", default=None, help="ILA CSV column containing 64-bit PRNG output.")
    return parser.parse_args(argv)


def validate_args(args: argparse.Namespace) -> None:
    for name in ("golden", "capture"):
        path = getattr(args, name)
        if not os.path.exists(path):
            raise CompareError(f"{name} file does not exist: {path}")
        if not os.path.isfile(path):
            raise CompareError(f"{name} path is not a file: {path}")
    for name in ("skip_capture", "skip_golden"):
        value = getattr(args, name)
        if value < 0:
            raise CompareError(f"{name} must be >= 0")
    if args.max_words is not None and args.max_words <= 0:
        raise CompareError("--max_words must be > 0")


def extract_words_from_text_lines(lines: Iterable[str], source_name: str) -> List[int]:
    words: List[int] = []
    for line_no, line in enumerate(lines, 1):
        for match in HEX_RE.finditer(line):
            word_text = match.group(1)
            if len(word_text) != 16:
                raise CompareError(
                    f"Hex word length error in {source_name}:{line_no}: {word_text!r}"
                )
            words.append(int(word_text, 16))
    return words


def parse_text_file(path: str) -> List[int]:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        words = extract_words_from_text_lines(f, path)
    if not words:
        raise CompareError(f"No 64-bit hex words could be parsed from {path}")
    return words


def cell_to_word(cell: str) -> Optional[int]:
    match = HEX_RE.search(cell.strip())
    if not match:
        return None
    word_text = match.group(1)
    if len(word_text) != 16:
        raise CompareError(f"Hex word length error in ILA CSV cell: {cell!r}")
    return int(word_text, 16)


def parse_ila_csv(path: str, ila_column: Optional[str]) -> Tuple[List[int], str]:
    with open(path, "r", encoding="utf-8-sig", errors="replace", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise CompareError(f"ILA CSV has no header row: {path}")
        fieldnames = [name for name in reader.fieldnames if name is not None]
        rows = list(reader)

    if not rows:
        raise CompareError(f"ILA CSV has no data rows: {path}")

    if ila_column:
        if ila_column not in fieldnames:
            raise CompareError(
                f"ILA CSV column {ila_column!r} not found. Available columns: {', '.join(fieldnames)}"
            )
        words = [word for row in rows if (word := cell_to_word(row.get(ila_column, ""))) is not None]
        if not words:
            raise CompareError(f"Column {ila_column!r} contains no 64-bit hex words")
        return words, ila_column

    best_column = None
    best_words: List[int] = []
    for column in fieldnames:
        column_words = [
            word for row in rows if (word := cell_to_word(row.get(column, ""))) is not None
        ]
        if len(column_words) > len(best_words):
            best_column = column
            best_words = column_words

    if best_column is None or not best_words:
        raise CompareError("ILA CSV auto-detection failed: no column contains 64-bit hex words")
    return best_words, best_column


def parse_capture(path: str, input_format: str, ila_column: Optional[str]) -> Tuple[List[int], Optional[str]]:
    if input_format in ("txt", "uart_hex"):
        return parse_text_file(path), None
    if input_format == "ila_csv":
        return parse_ila_csv(path, ila_column)
    raise CompareError(f"Unsupported input format: {input_format}")


def apply_skip_and_limit(words: List[int], skip: int, max_words: Optional[int], label: str) -> List[int]:
    if skip > len(words):
        raise CompareError(f"{label} skip value {skip} exceeds parsed word count {len(words)}")
    sliced = words[skip:]
    if max_words is not None:
        if len(sliced) < max_words:
            raise CompareError(
                f"{label} has only {len(sliced)} words after skip, fewer than --max_words={max_words}"
            )
        sliced = sliced[:max_words]
    return sliced


def compare_words(args: argparse.Namespace, golden_words: List[int], capture_words: List[int]) -> CompareResult:
    golden = apply_skip_and_limit(golden_words, args.skip_golden, args.max_words, "golden")
    capture = apply_skip_and_limit(capture_words, args.skip_capture, args.max_words, "capture")

    if not golden:
        raise CompareError("No golden words remain after skip/limit")
    if not capture:
        raise CompareError("No capture words remain after skip/limit")

    if len(capture) < len(golden):
        raise CompareError(
            f"Capture word count after skip/limit ({len(capture)}) is smaller than golden ({len(golden)})"
        )

    total = len(golden)
    capture = capture[:total]

    matched = 0
    mismatched = 0
    bit_errors = 0
    first_index: Optional[int] = None
    first_golden: Optional[int] = None
    first_capture: Optional[int] = None

    for idx, (g_word, c_word) in enumerate(zip(golden, capture)):
        if g_word == c_word:
            matched += 1
            continue
        mismatched += 1
        bit_errors += bin(g_word ^ c_word).count('1')
        if first_index is None:
            first_index = idx
            first_golden = g_word
            first_capture = c_word

    ber = bit_errors / (total * 64) if total else 0.0
    passed = mismatched == 0 and bit_errors == 0

    return CompareResult(
        golden_path=args.golden,
        capture_path=args.capture,
        input_format=args.format,
        skip_golden=args.skip_golden,
        skip_capture=args.skip_capture,
        total_compared=total,
        matched_words=matched,
        mismatched_words=mismatched,
        bit_errors=bit_errors,
        ber=ber,
        first_mismatch_index=first_index,
        first_mismatch_golden=first_golden,
        first_mismatch_capture=first_capture,
        passed=passed,
    )


def fmt_word(word: Optional[int]) -> str:
    return "N/A" if word is None else f"0x{word:016x}"


def render_report(result: CompareResult, ila_auto_column: Optional[str]) -> str:
    lines = [
        "# FPGA Capture Comparison Report",
        "",
        f"- golden file path: `{result.golden_path}`",
        f"- capture file path: `{result.capture_path}`",
        f"- format: `{result.input_format}`",
        f"- skip_golden: {result.skip_golden}",
        f"- skip_capture: {result.skip_capture}",
    ]
    if ila_auto_column:
        lines.append(f"- ila_column_used: `{ila_auto_column}`")
    lines.extend(
        [
            f"- total compared words: {result.total_compared}",
            f"- matched words: {result.matched_words}",
            f"- mismatched words: {result.mismatched_words}",
            f"- bit errors: {result.bit_errors}",
            f"- BER: {result.ber:.12g}",
            f"- first mismatch index: {result.first_mismatch_index if result.first_mismatch_index is not None else 'N/A'}",
            f"- golden word at first mismatch: {fmt_word(result.first_mismatch_golden)}",
            f"- captured word at first mismatch: {fmt_word(result.first_mismatch_capture)}",
            f"- PASS/FAIL: {'PASS' if result.passed else 'FAIL'}",
            "",
        ]
    )
    return "\n".join(lines)


def write_report(path: str, text: str) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def main(argv: Optional[Sequence[str]] = None) -> int:
    try:
        args = parse_args(argv)
        validate_args(args)
        golden_words = parse_text_file(args.golden)
        capture_words, ila_auto_column = parse_capture(args.capture, args.format, args.ila_column)
        result = compare_words(args, golden_words, capture_words)
        report = render_report(result, ila_auto_column)
        print(report)
        if args.output_report:
            write_report(args.output_report, report)
        return 0 if result.passed else 1
    except CompareError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"ERROR: file I/O failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
