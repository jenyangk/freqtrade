#!/bin/bash

# Rigorous Strategy Testing Script
# Usage: ./user_data/scripts/rigorous_test.sh <StrategyName>

STRATEGY=$1
CONFIG="user_data/config_rigorous.json"
VENV_PYTHON="/home/kamikaze/freqtrade/.venv/bin/python"

if [ -z "$STRATEGY" ]; then
    echo "Error: Please provide a strategy name."
    echo "Usage: ./user_data/scripts/rigorous_test.sh MeanReversionRSIHMM"
    exit 1
fi

echo "=========================================================="
echo "Starting Rigorous Testing for: $STRATEGY"
echo "=========================================================="

# 1. In-Sample (IS) Testing (2023)
echo "[1/4] Running In-Sample Backtest (2023)..."
$VENV_PYTHON -m freqtrade backtesting --strategy $STRATEGY --config $CONFIG --timerange 20230101-20231231 --timeframe 1h --export trades > user_data/backtest_results/${STRATEGY}_IS_2023.txt 2>&1
echo "Done. Results saved to user_data/backtest_results/${STRATEGY}_IS_2023.txt"

# 2. Out-of-Sample (OOS) Testing (2024-2025)
echo "[2/4] Running Out-of-Sample Backtest (2024-2025)..."
$VENV_PYTHON -m freqtrade backtesting --strategy $STRATEGY --config $CONFIG --timerange 20240101-20251221 --timeframe 1h --export trades > user_data/backtest_results/${STRATEGY}_OOS_2024_2025.txt 2>&1
echo "Done. Results saved to user_data/backtest_results/${STRATEGY}_OOS_2024_2025.txt"

# 3. Monte Carlo Permutation Test (MCPT)
echo "[3/4] Running Monte Carlo Permutation Test (100 iterations)..."
PAIRS=$($VENV_PYTHON -c "import json; print(' '.join(json.load(open('$CONFIG'))['exchange']['pair_whitelist']))")
$VENV_PYTHON ./user_data/scripts/mcpt/run_advanced_mcpt.py --strategy $STRATEGY --pairs $PAIRS --timerange 20230101-20241231 -n 100 > user_data/backtest_results/${STRATEGY}_MCPT.txt 2>&1
echo "Done. Results saved to user_data/backtest_results/${STRATEGY}_MCPT.txt"

# 4. Stability Test (Slightly different timerange)
echo "[4/4] Running Stability Test (Shifted Timerange)..."
$VENV_PYTHON -m freqtrade backtesting --strategy $STRATEGY --config $CONFIG --timerange 20230215-20240215 --timeframe 1h > user_data/backtest_results/${STRATEGY}_Stability.txt 2>&1
echo "Done. Results saved to user_data/backtest_results/${STRATEGY}_Stability.txt"

echo "=========================================================="
echo "Testing Complete. Please review the files in user_data/backtest_results/"
echo "Look for: "
echo " - OOS Profit should be > 50% of IS Profit."
echo " - MCPT P-Value should be < 0.05."
echo " - Stability results should not deviate more than 20% from IS."
echo "=========================================================="
