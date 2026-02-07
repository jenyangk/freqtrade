# Monte Carlo Permutation Test (MCPT) Module

This module provides tools to detect overfitting in trading strategies using 
Monte Carlo Permutation Tests, based on the methodology from [neurotrader888/mcpt](https://github.com/neurotrader888/mcpt).

## What is MCPT?

Monte Carlo Permutation Tests help determine if a trading strategy's performance
is due to genuine market patterns or simply overfitting to random noise in the data.

The key insight: if you shuffle the price bars while preserving their statistical
properties (mean, std, kurtosis, etc.), any "real" pattern should disappear.
If your strategy still performs well on shuffled data, it's likely overfit.

## How it works

1. **Bar Permutation**: We shuffle relative price movements (gaps, intrabar high/low/close)
   while preserving the distribution of returns. This creates synthetic price data
   that "looks" statistically similar but has no predictable patterns.

2. **Backtest on Permutations**: Run your strategy on N permutated datasets.

3. **Calculate P-value**: Count how many permutations achieve >= real performance.
   - P-value < 0.05: Strategy has significant edge (unlikely due to chance)
   - P-value > 0.10: Strategy may be overfit (random data performs similarly)

## Files

- `bar_permute.py`: Core permutation algorithm
- `generate_permutations.py`: Generate and save permutated datasets
- `run_mcpt.py`: Run full freqtrade backtests on permutations
- `quick_mcpt.py`: Fast MCPT using simple strategy logic (no full backtest)

## Quick Start

### 1. Generate Permutated Data

```bash
cd /home/kamikaze/freqtrade

# Generate 100 permutations for BTC on 1h and 4h timeframes
python user_data/scripts/mcpt/generate_permutations.py \
    --pairs BTC/USDT:USDT ETH/USDT:USDT \
    --timeframes 1h 4h \
    -n 100
```

This creates permutated data in `user_data/data/mcpt_permutations/`.

### 2. Run MCPT on Your Strategy

```bash
# Run full MCPT (slower, but uses actual strategy logic)
python user_data/scripts/mcpt/run_mcpt.py \
    --strategy MeanReversionRSI \
    --pairs BTC/USDT:USDT \
    --timerange 20230101-20241201 \
    -n 50
```

### 3. Quick Analysis (Optional)

For faster initial analysis without running full backtests:

```bash
python user_data/scripts/mcpt/quick_mcpt.py \
    --pair BTC/USDT:USDT \
    --timeframe 1h \
    -n 100
```

## Interpreting Results

### P-value < 0.05 ✅
Your strategy shows statistically significant edge. The performance is unlikely
to be achieved on random data.

### P-value 0.05-0.10 ⚠️
Marginal significance. The strategy may have some edge, but be cautious.
Consider more out-of-sample testing.

### P-value > 0.10 ❌
No significant edge detected. The strategy's performance could easily be
achieved on random shuffled data. This is a strong indicator of overfitting.

## Best Practices

1. **Use sufficient permutations**: 100+ for initial testing, 500+ for publication.

2. **Match your backtest period**: Use the same timerange for MCPT as your backtest.

3. **Test multiple metrics**: Don't just test profit - also test Sharpe, Sortino, etc.

4. **Walk-forward variant**: For strategies with training periods, use `start_index`
   in the permutation to only permute out-of-sample data.

5. **Multiple pairs**: Test on individual pairs AND combined to check robustness.

## Example Output

```
==================================================
MCPT Results
==================================================
Real profit_total_pct:  45.23
Permuted mean:          12.34
Permuted std:           28.91
Permuted range:         [-42.11, 89.23]
Times perm >= real:     3

*** P-VALUE: 0.0297 ***
==================================================

✅ P-value < 0.05: Strategy shows statistically significant edge
   The performance is unlikely to be due to random chance.
```

## References

- [neurotrader888/mcpt](https://github.com/neurotrader888/mcpt) - Original implementation
- White, H. (2000). A Reality Check for Data Snooping. Econometrica.
- Bailey, D.H. et al. (2015). Pseudo-Mathematics and Financial Charlatanism.
