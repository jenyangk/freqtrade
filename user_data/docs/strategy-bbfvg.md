# BBFVG Strategy Documentation

## Overview

The BBFVG (Bollinger Band + Fair Value Gap) strategy is a mean-reversion and trend-following hybrid strategy that combines:

1.  **Fair Value Gaps (FVG)** on a 4H timeframe as key support/resistance zones.
2.  **Bollinger Bands** on a 1H timeframe for dynamic entry/exit levels.
3.  **EMA Trend Filter** (EMA 21 & EMA 38) on a 1H timeframe to ensure trade direction aligns with momentum.

This strategy aims to enter trades when price revisits "fair value" (FVG) while simultaneously touching the outer bands of standard deviation (Bollinger Bands), providing a high-confluence setup.

## Strategy Concept

The strategy looks for a confluence of three factors:
-   **Trend**: EMA 21 > EMA 38 (Bullish) or EMA 21 < EMA 38 (Bearish).
-   **Structure**: Price is inside or near a 4H Fair Value Gap.
-   **Volatility Extremes**: Price touches the Lower Bollinger Band (Long) or Upper Bollinger Band (Short).

## Core Components

### 1. Timeframes
-   **Base Timeframe (1H)**: Used for Bollinger Bands, EMAs, and trade execution.
-   **Informative Timeframe (4H)**: Used for detecting Fair Value Gaps.

### 2. Indicators (1H)
-   **Bollinger Bands**: Window 20, StdDev 2.0 (Configurable).
    -   Acts as dynamic support (Lower Band) and resistance (Upper Band).
-   **EMA 21 & EMA 38**:
    -   Used as a trend filter.
    -   Long: EMA 21 > EMA 38.
    -   Short: EMA 21 < EMA 38.

### 3. Fair Value Gaps (4H)
-   **Bullish FVG**: A price inefficiency where buying pressure was strong. Acts as support.
-   **Bearish FVG**: A price inefficiency where selling pressure was strong. Acts as resistance.
-   The strategy checks if the current 1H candle's Low/High is interacting with these 4H zones.

## Trade Flow

### Long Entry
1.  **Trend**: EMA 21 > EMA 38 (1H).
2.  **Structure**: Price is inside a Bullish FVG (4H).
3.  **Trigger**: Price touches or crosses below the Lower Bollinger Band (1H).

### Short Entry
1.  **Trend**: EMA 21 < EMA 38 (1H).
2.  **Structure**: Price is inside a Bearish FVG (4H).
3.  **Trigger**: Price touches or crosses above the Upper Bollinger Band (1H).

### Exit Conditions
-   **Take Profit**:
    -   **Risk:Reward Ratio**: 2:1 (Target profit is 2x the risk distance).
    -   **Opposite Band**:
        -   Long Exit: Price touches Upper Bollinger Band.
        -   Short Exit: Price touches Lower Bollinger Band.
-   **Stop Loss**:
    -   Fixed Stop Loss of 5%.
    -   Long: Entry Price * 0.95.
    -   Short: Entry Price * 1.05.

## Configuration

### Strategy Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `bb_window` | Int | 20 | Bollinger Band window |
| `bb_std` | Decimal | 2.0 | Bollinger Band standard deviation |
| `ema_fast_period` | Int | 21 | Fast EMA period |
| `ema_slow_period` | Int | 38 | Slow EMA period |
| `fvg_threshold` | Decimal | 0.001 | Minimum FVG size (0.1%) |
| `fvg_extend` | Int | 40 | How long (in 4H bars) an FVG remains active |
| `risk_reward` | Decimal | 2.0 | Target Risk:Reward ratio |

### Position Adjustment (DCA)
The strategy allows for multiple entries (DCA) if the trade moves against the initial entry but the setup remains valid.
-   `max_entry_position_adjustment`: 2 (Total 3 entries).
-   `max_dca_multiplier`: 1.5 (Increase stake on DCA).

## Risk Management

The strategy uses a **fixed 5% stop loss** for initial risk calculation.
-   This "Risk" distance (5%) is then used to calculate the "Reward" targets (1:2, 1:3, 1:4).
-   **Dynamic Trailing**:
    -   At 1:2 Profit (10% gain): Stop Loss moves to Breakeven.
    -   At 1:3 Profit (15% gain): Stop Loss moves to 1:2 level (locking 10% gain).
    -   At 1:4 Profit (20% gain): Immediate Exit.

## Plotting

The strategy is configured to plot:
-   **Main Plot**: Bollinger Bands, EMAs, and FVG Zones (Green/Red lines).
-   **Subplots**: Entry signals.

## Example Usage

```json
"strategy": "BBFVG",
"timeframe": "1h",
"strategy_config": {
    "bb_window": 20,
    "bb_std": 2.0,
    "risk_reward": 2.0
}
```
