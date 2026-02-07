# MCPT Analysis & Strategy Improvement Plan

## 1. Current Findings (MeanReversionRSI)
Based on Monte Carlo Permutation Tests (2020-2025, BTC/ETH Futures):

### Statistical Significance
- **Standard P-values:** 0.11 - 0.19 (Failed to reach 95% confidence)
- **Block P-values:** 0.10 - 0.14 (Better, but still not statistically significant)
- **Max Drawdown P-values:** 0.22 - 0.54 (Poor risk profile, indistinguishable from noise)

### Diagnosis
The strategy suffers from "Logic Conflict":
- It identifies strong parabolic trends (RSI > 70, ADX > 40) but attempts to enter at the extreme opposite end of the volatility envelope (BB Lower).
- In strong trends, price rarely hits the BB Lower without the trend actually failing.
- This leads to "adverse selection": you only get filled when the trade is likely to lose.

---

## 2. Phase 1: Trend-Following Mean Reversion (TFMR)
**Goal:** Capture pullbacks in strong trends without waiting for extreme (and unlikely) mean reversion.

### Key Logic Changes
1. **Entry Point:** Move entry from BB Lower to **BB Middleband** (20-period SMA).
2. **Regime Filter:** Relax ADX requirements from 40 to **25** to capture more of the trend lifecycle.
3. **Volatility Filter:** Add a Bollinger Band Width filter to ensure we are trading in expanding volatility, not sideways chop.
4. **Exit Strategy:** Implement a more dynamic exit or trailing stop to capture trend extensions.

---

## 3. Roadmap
### Phase 1: Strategy Evolution (TrendFollowingMeanReversion) - IN PROGRESS
- **Strategy**: `TrendFollowingMeanReversion`
- **Logic**: Pullback entry at BB Middleband during strong 4H trends (RSI > 60, ADX > 25).
- **Risk Management**: 5x leverage, ATR-based position sizing (2% risk).
- **Current Test**: 50-permutation Block MCPT on 5 pairs (BTC, ETH, SOL, BNB, XRP).
- **Real Data Baseline**: 49.43% profit, 1.02 Sharpe, 70.8% Win Rate (89 trades).
- **Status**: Running permutations to verify statistical significance.

### Phase 2: Final Validation & Refinement
- [ ] Analyze p-values from Phase 1.
- [ ] If p < 0.05: Strategy is validated.
- [ ] If p > 0.05: Further refine entry/exit logic (e.g., add Supertrend filter, tighten trend requirements).
- [ ] Compare Standard vs. Block permutations to identify dependency on price structure.
