# RSIFVG Optimized Strategy

## Overview
This strategy is an optimized version of the `RSIFVG` strategy, which itself is a port of a QuantConnect C# strategy. The original strategy showed strong performance in 2023 (trending market) but struggled in other market conditions. This optimized version introduces robustness features to adapt to different market regimes.

## Core Logic
The strategy combines three concepts:
1.  **Fair Value Gaps (FVG):** Identifies inefficiencies in price action on a higher timeframe (15m).
2.  **RSI Divergence:** Identifies potential reversals on the RSI timeframe (3m).
3.  **Engulfing Patterns:** Confirms entry on the base timeframe (3m) when price is inside an FVG.

## Improvements & Optimizations

### 1. Trend Filter (Regime Detection)
*   **Problem:** The original strategy is a reversal strategy. In strong trending markets (especially crashes), catching reversals can be dangerous ("catching falling knives").
*   **Solution:** Added an optional **Trend Filter** using a 200-period EMA on the 1h timeframe.
    *   **Longs:** Only allowed if Price > 1h EMA 200.
    *   **Shorts:** Only allowed if Price < 1h EMA 200.
*   **Benefit:** This filters out counter-trend trades during strong trends, significantly improving robustness across different years.

### 2. Uncapped Profit Potential (Trailing Stop Fix)
*   **Problem:** The original C# strategy had a logic conflict where a hard "Take Profit" limit order was set at 1.5R, but the code also contained logic to trail stops at 3R and 4R. The hard TP prevented the strategy from ever reaching the trailing stages, capping potential winnings.
*   **Solution:** Removed the hard Take Profit cap. The strategy now relies entirely on the dynamic trailing stop logic:
    *   **Breakeven:** Move Stop Loss to Entry Price when profit reaches **1.5R**.
    *   **Trail 2R:** Move Stop Loss to 2R profit when price reaches **3R**.
    *   **Trail 3R:** Move Stop Loss to 3R profit when price reaches **4R**.
*   **Benefit:** Allows winning trades to run much further during strong moves, potentially capturing the "1000%" gains seen in favorable conditions while protecting capital.

### 3. Structure-Based Stop Loss
*   **Logic:** Stop Loss is placed at the swing low (for longs) or swing high (for shorts) of the candle *preceding* the engulfing candle.
*   **Benefit:** Adapts risk to the specific volatility of the setup. Tighter stops for small setups, wider stops for volatile ones.

## Configuration
The strategy exposes several parameters for Hyperopt optimization:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `use_trend_filter` | `True` | Enable/Disable the 1h EMA 200 filter. |
| `rsi_period` | 14 | Period for RSI calculation. |
| `fvg_threshold` | 0.0 | Minimum size of FVG to be considered valid. |
| `divergence_expiry` | 20 | How long (in bars) a divergence signal remains valid. |

## Installation
1.  Ensure `RSIFVG_Optimized.py` is in your `user_data/strategies/` folder.
2.  Run backtesting:
    ```bash
    freqtrade backtesting --strategy RSIFVG_Optimized --timerange 20230101-20231231 -i 3m
    ```
3.  To optimize parameters:
    ```bash
    freqtrade hyperopt --strategy RSIFVG_Optimized --spaces buy indicator -i 3m
    ```
