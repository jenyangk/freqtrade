# BBFVG Sniper Strategy

## Overview
The **BBFVG Sniper** strategy is an advanced evolution of the standard BBFVG strategy. It incorporates "Smart Money Concepts" and dynamic risk management to adapt to changing market conditions.

## Key Features

### 1. Volatility Breathing Exit (Dynamic R:R)
- **Concept**: In a strong trend (volatility expansion), fixed targets cut winners short.
- **Logic**: 
  - Calculates Bollinger Bandwidth Slope.
  - If Bandwidth is **increasing** (expanding), the fixed 2:1 Risk:Reward target is **disabled**.
  - Instead, the strategy trails the **EMA 21** as a dynamic stop loss.
  - If Bandwidth is **contracting** or flat, the standard 2:1 R:R or Opposite Band exit applies.

### 2. Liquidity Sweep Filter
- **Concept**: Avoid buying into "fake" moves designed to trap traders.
- **Logic**:
  - **Volume**: Requires Volume < Volume MA (Exhaustion) OR
  - **RSI**: Requires RSI Oversold (<30) for Longs / Overbought (>70) for Shorts.
  - This confirms that the push into the FVG is running out of steam.

### 3. Regime Filtering (The "Kill Switch")
- **Concept**: Avoid chopping up in sideways markets.
- **Logic**:
  - Uses **ADX (Average Directional Index)**.
  - Only takes trades if **ADX > 25** (Trending Market).

### 4. Tiered Entry System
- **Concept**: Balance between aggressive entries (FOMO) and sniper entries (Precision).
- **Logic**:
  - **Entry 1 (Front-run)**: Enters with **0.5x Stake** when price touches the edge of the FVG.
  - **Entry 2 (Sniper)**: Adds **1.0x Stake** (DCA) if price pushes deeper to touch the Bollinger Band inside the FVG.
  - This ensures exposure to strong moves while saving the bulk of the capital for the perfect setup.

## Configuration

### Indicators
- **Bollinger Bands**: 20 period, 2.0 std dev.
- **EMAs**: 21 (Fast) and 38 (Slow).
- **FVG**: 4H timeframe, 0.1% threshold.
- **ADX**: Threshold 25.
- **RSI**: 14 period, 30/70 levels.

### Risk Management
- **Stop Loss**: Dynamic, based on FVG structure (Top/Bottom of the gap).
- **Take Profit**: Dynamic (Volatility Breathing) or 2:1 R:R.

## Usage
Run with:
```bash
freqtrade trade --strategy BBFVG_Sniper
```
