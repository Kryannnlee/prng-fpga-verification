@echo off
REM PRNG-371 FPGA Verification Framework — Windows one-click wrapper
REM Usage: verify.bat              (quick verification)
REM        verify.bat full         (full test suite)
REM        verify.bat csv <path>   (FPGA physical validation)

cd /d "%~dp0.."

if "%1"=="" (
    python tests/run_all_tests.py --quick
) else if "%1"=="full" (
    python tests/run_all_tests.py
) else if "%1"=="csv" (
    python tests/run_all_tests.py --csv "%2"
) else (
    echo Usage: verify.bat [full ^| csv ^<path^>]
    echo.
    echo   verify.bat            Quick verification (unit + integration)
    echo   verify.bat full        Full test suite
    echo   verify.bat csv file    FPGA physical validation with ILA CSV
)
