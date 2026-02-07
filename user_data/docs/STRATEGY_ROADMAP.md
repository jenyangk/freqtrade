# Strategy Roadmap: MeanReversionRSIHMM

This document outlines the current status, validation methodology, and future considerations for the HMM-RSI Hybrid strategy.

## 1. What Needs to be Done Next
*   **Complete the Rigorous Suite**: The Monte Carlo Permutation Test (MCPT) was interrupted. The first priority is to let the `./user_data/scripts/rigorous_test.sh` run to completion.
*   **Analyze the "Robustness Score"**:
    *   **OOS Ratio**: Out-of-Sample (2024-2025) profit should be at least 50% of In-Sample (2023) profit.
    *   **MCPT P-Value**: Must be < 0.05 (indicating the strategy's edge is statistically significant and not luck).
    *   **Stability**: The shifted-window test should show results within 20% of the baseline.
*   **Dry Run Validation**: Once the backtests pass, deploy to a 2-week Dry Run to verify that the HMM rolling window performs efficiently in real-time without latency issues.

## 2. Handling Pair Selection
In quantitative trading, pair selection is a major source of "Selection Bias."
*   **Backtesting (Static)**: We use a `StaticPairList` of 30+ diverse pairs (BTC, ETH, Large-caps, Mid-caps). This ensures the strategy works across different market structures, not just on one "lucky" coin.
*   **Live/Dry Run (Dynamic)**:
    *   **VolumePairList**: Focus on the top 30-50 coins by volume to ensure liquidity.
    *   **Filters**: Always use `AgeFilter` (avoid new coins with no history for HMM) and `SpreadFilter` (avoid losing profit to high bid-ask spreads).
*   **Sector Diversification**: Ensure the whitelist includes coins from different sectors (DeFi, L1, AI, Memes) to prevent the strategy from being 100% correlated to a single narrative.

## 3. Handling Overfitting (The "Quant" Way)
Overfitting is the "silent killer" of strategies. We mitigate it via:
*   **Walk-Forward Analysis (WFA)**: Instead of one big backtest, we test in "chunks." If the strategy needs different parameters for every month to stay profitable, it is overfit.
*   **Complexity Penalty**: We keep the HMM simple (2-3 states). Adding more states or more RSI levels might increase backtest profit but usually destroys live performance.
*   **Monte Carlo Permutation**: We shuffle the price data. If the strategy still "makes money" on random data, the logic is flawed. It should only make money on the original structure.
*   **Anchored Training**: The HMM uses a rolling window (e.g., 1000 candles). This "anchors" the model to recent volatility, preventing it from being stuck in 2021 logic during a 2025 market.

## 4. Expert Quant Opinion: The "Full Coverage" Checklist
To move from a "script" to a "professional trading system," consider these factors:

### A. Execution & Friction
*   **Slippage**: In backtesting, entries are "perfect." In live trading, a 10-trade limit on mid-caps will cause slippage. Model this by adding a 0.1% "check" to your exit prices.
*   **Funding Rates**: Since we are trading Futures, holding a position for days can be expensive if the funding rate is high. Consider adding `funding_rate` to the HMM features.

### B. Risk Management
*   **Kelly Criterion**: We've implemented a basic version. Ensure it scales down during "Equity Drawdown" to protect the bankroll.
*   **Correlation Risk**: If BTC drops 10%, all 10 of your "Mean Reversion" trades will likely hit their Stop Loss at once. Consider a `MaxPositionSizePerSector` or a global `MaxDrawdown` stop.

### C. Market Regimes
*   **Volatility Clustering**: Mean reversion works best in "Low to Medium" volatility. In "High Volatility" (Black Swan events), mean reversion is dangerous. Use the HMM to detect "High Volatility" states and **disable trading** during those times.

### D. Capacity & Scalability
*   **Liquidity**: How much can you trade before you become the market? For this strategy, a $50k - $100k account is likely the ceiling before slippage eats the edge on smaller altcoins.

---
**Status**: Strategy is currently in the **Validation Phase**. Do not increase capital until the MCPT P-Value is confirmed.
