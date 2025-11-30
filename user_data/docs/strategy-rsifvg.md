# RSIFVG Strategy Documentation

## Overview

The RSIFVG (RSI Fair Value Gap) strategy is an advanced multi-timeframe trading strategy that combines three core technical analysis concepts:

1. **Fair Value Gaps (FVG)** - Price inefficiencies that act as support/resistance zones
2. **RSI Divergences** - Momentum shifts detected through pivot analysis
3. **Engulfing Candles** - Price action confirmation patterns

This strategy operates on three configurable timeframes to provide a comprehensive market structure analysis while filtering for high-probability entry setups.

## Strategy Concept

The RSIFVG strategy identifies trading opportunities by waiting for the confluence of three key conditions:

- **Market Structure**: Fair Value Gaps on higher timeframe (default 15m) indicate areas of institutional interest
- **Momentum Shift**: RSI divergences on RSI timeframe (default 3m) signal potential reversals
- **Execution Trigger**: Engulfing candles on base timeframe (default 3m) provide precise entry timing

This multi-layered approach ensures that entries occur only when market structure, momentum, and price action all align in the same direction.

## Core Components

### 1. Fair Value Gaps (FVG)

Fair Value Gaps represent price inefficiencies in the market - areas where price moved so quickly that it left a "gap" in normal trading activity.

#### Bullish FVG Detection
A bullish FVG forms when:
- Current candle's low is above the high of the candle 2 periods ago
- The middle candle's close is above the high of the candle 2 periods ago
- The gap size exceeds the configured threshold (relative to price)

```
Candle[i-2]:  ▓▓▓ (high)
Candle[i-1]:  ▒▒▒ (middle candle)
Candle[i]:      ▓▓▓ (low is above previous high)
              ^^^ This gap is the Bullish FVG zone
```

#### Bearish FVG Detection
A bearish FVG forms when:
- Current candle's high is below the low of the candle 2 periods ago
- The middle candle's close is below the low of the candle 2 periods ago
- The gap size exceeds the configured threshold

```
Candle[i-2]:      ▓▓▓ (low)
Candle[i-1]:    ▒▒▒ (middle candle)
Candle[i]:    ▓▓▓ (high is below previous low)
              ^^^ This gap is the Bearish FVG zone
```

#### FVG Properties
- **Active Period**: FVGs remain active for a configurable number of periods (default 20 bars on FVG timeframe)
- **Mitigation**: FVGs are considered "filled" when price revisits the gap zone
- **Zones**: Each FVG has a top and bottom boundary that defines the support/resistance area

### 2. RSI Divergences

RSI divergences detect momentum shifts by comparing price action with RSI indicator behavior.

#### Bullish Divergence
Occurs when:
- Price makes a **lower low** (recent pivot low < previous pivot low)
- RSI makes a **higher low** (recent RSI pivot > previous RSI pivot)
- Signal: Price is falling but momentum is strengthening → potential reversal up

#### Bearish Divergence
Occurs when:
- Price makes a **higher high** (recent pivot high > previous pivot high)
- RSI makes a **lower high** (recent RSI pivot < previous RSI pivot)
- Signal: Price is rising but momentum is weakening → potential reversal down

#### Pivot Detection
The strategy uses a pivot detection algorithm that requires:
- **Left bars**: Number of bars to the left that must be lower/higher than the pivot (default 5)
- **Right bars**: Number of bars to the right that must be lower/higher than the pivot (default 5)
- **Lookback range**: Divergences are detected within a configurable lookback window (5-60 pivot periods)

#### Divergence Expiry
Divergence signals remain "active" for a configurable period (default 20 RSI timeframe bars) to allow for trade execution before the signal becomes stale.

### 3. Engulfing Candles

Engulfing patterns provide the final confirmation for trade entry on the base timeframe.

#### Bullish Engulfing
- Previous candle: bearish (close < open)
- Current candle: bullish (close > open)
- Current candle's body completely engulfs previous candle's body
- Current body top > Previous body top
- Current body bottom ≤ Previous body bottom

#### Bearish Engulfing
- Previous candle: bullish (close > open)
- Current candle: bearish (close < open)
- Current candle's body completely engulfs previous candle's body
- Current body top ≥ Previous body top
- Current body bottom < Previous body bottom

## Trade Flow

### Long Trade Flow

#### 1. Setup Phase (Higher Timeframes)
```
FVG Timeframe (15m):
└─> Bullish FVG detected and tracked
    └─> Gap zone remains active for 20 periods

RSI Timeframe (3m):
└─> RSI monitored for pivot points
    └─> Bullish divergence detected
        └─> Divergence active flag set (expires after 20 bars)
```

#### 2. Entry Conditions (Base Timeframe - 3m)
All conditions must be TRUE simultaneously:
```
✓ bull_div_active == 1       (Active bullish divergence)
✓ bull_engulf == 1           (Bullish engulfing candle)
✓ in_bull_fvg == 1           (Price touching bullish FVG zone)
✓ volume > 0                 (Valid volume)
```

#### 3. Entry Execution
```
Entry Signal: "bull_div_fvg_engulf"
Entry Price: Close of engulfing candle
Position Size: Per configured stake amount
Direction: LONG
```

#### 4. Risk Management (Stoploss)
```
Initial Stop: Swing low of candle BEFORE engulfing candle
Structure-based: Protects below support level

Dynamic Trailing:
├─> At 1.5R profit: Move to breakeven (0% loss)
├─> At 3R profit: Trail stop to 2R (lock in 2R profit)
└─> At 4R profit: Trail stop to 3R (lock in 3R profit)

Where R = Initial risk (distance from entry to initial stop)
```

#### 5. Exit Conditions
```
Exit Triggers:
├─> Stoploss hit (initial or trailing)
├─> Opposite signal: bear_div_active == 1
├─> Profit target: 5R+ (optional)
└─> ROI table reached (if configured)

Exit Tag Examples:
├─> "stoploss" - Initial stop hit
├─> "trailing_stop_loss" - Trailing stop hit
├─> "bear_divergence_signal" - Opposite divergence
└─> "profit_target_5R_XX%" - Extreme profit target
```

### Short Trade Flow

#### 1. Setup Phase (Higher Timeframes)
```
FVG Timeframe (15m):
└─> Bearish FVG detected and tracked
    └─> Gap zone remains active for 20 periods

RSI Timeframe (3m):
└─> RSI monitored for pivot points
    └─> Bearish divergence detected
        └─> Divergence active flag set (expires after 20 bars)
```

#### 2. Entry Conditions (Base Timeframe - 3m)
All conditions must be TRUE simultaneously:
```
✓ bear_div_active == 1       (Active bearish divergence)
✓ bear_engulf == 1           (Bearish engulfing candle)
✓ in_bear_fvg == 1           (Price touching bearish FVG zone)
✓ volume > 0                 (Valid volume)
```

#### 3. Entry Execution
```
Entry Signal: "bear_div_fvg_engulf"
Entry Price: Close of engulfing candle
Position Size: Per configured stake amount
Direction: SHORT
```

#### 4. Risk Management (Stoploss)
```
Initial Stop: Swing high of candle BEFORE engulfing candle
Structure-based: Protects above resistance level

Dynamic Trailing:
├─> At 1.5R profit: Move to breakeven (0% loss)
├─> At 3R profit: Trail stop to 2R (lock in 2R profit)
└─> At 4R profit: Trail stop to 3R (lock in 3R profit)

Where R = Initial risk (distance from entry to initial stop)
```

#### 5. Exit Conditions
```
Exit Triggers:
├─> Stoploss hit (initial or trailing)
├─> Opposite signal: bull_div_active == 1
├─> Profit target: 5R+ (optional)
└─> ROI table reached (if configured)

Exit Tag Examples:
├─> "stoploss" - Initial stop hit
├─> "trailing_stop_loss" - Trailing stop hit
├─> "bull_divergence_signal" - Opposite divergence
└─> "profit_target_5R_XX%" - Extreme profit target
```

## Complete Trade Lifecycle Example

### Long Trade Example

```
Time: 12:00 - FVG detected on 15m timeframe
└─> Bullish FVG: Zone between 45,280 and 45,320
    └─> Active for next 20 periods (5 hours on 15m)

Time: 12:15 - Bullish divergence detected on 3m
├─> Previous pivot low: Price 45,250, RSI 28
├─> Current pivot low: Price 45,240, RSI 32
└─> Divergence active flag set (expires 13:15)

Time: 12:18 - Engulfing candle forms on 3m
├─> Previous candle: Bearish (45,250 → 45,245)
├─> Current candle: Bullish (45,245 → 45,295)
├─> Price inside FVG zone (45,280-45,320)
└─> ALL CONDITIONS MET → ENTRY SIGNAL

ENTRY:
├─> Price: 45,295
├─> Stop: 45,240 (swing low before engulfing)
├─> Risk: 55 points (0.12%)
└─> Tag: "bull_div_fvg_engulf"

Time: 12:24 - Price moves to 45,378
├─> Profit: 83 points (0.18%)
├─> Risk multiple: 1.5R
└─> Stop moved to BREAKEVEN (45,295)

Time: 12:36 - Price reaches 45,460
├─> Profit: 165 points (0.36%)
├─> Risk multiple: 3R
└─> Stop trailed to 2R (45,405) - locks in 110 points

Time: 12:45 - Bearish divergence detected
├─> bear_div_active flag set
└─> EXIT SIGNAL triggered

EXIT:
├─> Price: 45,440
├─> Profit: 145 points (0.32%)
├─> Risk multiple: 2.6R
├─> Tag: "bear_divergence_signal"
└─> Trade Duration: 27 minutes
```

## Strategy Parameters

### Timeframe Configuration

```python
timeframe = '3m'           # Base timeframe for execution
rsi_timeframe = '3m'       # Timeframe for RSI divergence detection
fvg_timeframe = '15m'      # Timeframe for Fair Value Gap detection
```

**Recommendation**: Use higher timeframes for RSI and FVG to reduce noise:
- Conservative: Base 5m, RSI 15m, FVG 1h
- Moderate: Base 3m, RSI 5m, FVG 15m (default)
- Aggressive: Base 1m, RSI 3m, FVG 5m

### RSI & Divergence Parameters

| Parameter | Type | Range | Default | Description |
|-----------|------|-------|---------|-------------|
| `rsi_period` | IntParameter | 10-20 | 14 | RSI calculation period |
| `pivot_left` | IntParameter | 2-10 | 5 | Bars to left of pivot for confirmation |
| `pivot_right` | IntParameter | 2-10 | 5 | Bars to right of pivot for confirmation |
| `min_lookback` | IntParameter | 2-10 | 5 | Minimum pivot distance for divergence (in RSI timeframe bars) |
| `max_lookback` | IntParameter | 20-80 | 60 | Maximum pivot distance for divergence (in RSI timeframe bars) |
| `divergence_expiry` | IntParameter | 4-20 | 20 | Periods before divergence signal expires (in RSI timeframe bars) |

### FVG Parameters

| Parameter | Type | Range | Default | Description |
|-----------|------|-------|---------|-------------|
| `fvg_threshold` | DecimalParameter | 0.0-0.01 | 0.0 | Minimum gap size as % of price (when auto_threshold=False) |
| `fvg_extend` | IntParameter | 10-200 | 20 | Periods FVG remains active (in FVG timeframe bars) |
| `fvg_max_unmitigated` | IntParameter | 0-5 | 0 | Max simultaneous unmitigated FVGs (0=unlimited) |
| `auto_threshold` | Boolean | - | False | Use dynamic threshold calculation based on ATR |

### Risk Management Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `stoploss` | Float | -0.05 | Base stoploss percentage (5%) - overridden by structure-based stops |
| `minimal_roi` | Dict | {} | ROI table - commented out to use dynamic trailing only |
| `use_custom_stoploss` | Boolean | True | Enable dynamic stoploss management |
| `can_short` | Boolean | True | Enable short positions |

## Configuration Example

### Basic Configuration

```json
{
  "strategy": "RSIFVG",
  "timeframe": "3m",
  "stake_amount": "unlimited",
  "stake_currency": "USDT",
  "dry_run": true,
  
  "exchange": {
    "name": "binance",
    "key": "",
    "secret": "",
    "pair_whitelist": [
      "BTC/USDT",
      "ETH/USDT",
      "BNB/USDT"
    ]
  },
  
  "entry_pricing": {
    "price_side": "same",
    "use_order_book": false
  },
  
  "exit_pricing": {
    "price_side": "same",
    "use_order_book": false
  }
}
```

### Advanced Configuration with Parameter Overrides

```json
{
  "strategy": "RSIFVG",
  "timeframe": "3m",
  
  "strategy_config": {
    "rsi_timeframe": "5m",
    "fvg_timeframe": "15m",
    "rsi_period": 14,
    "pivot_left": 5,
    "pivot_right": 5,
    "min_lookback": 5,
    "max_lookback": 60,
    "divergence_expiry": 20,
    "fvg_threshold": 0.0,
    "fvg_extend": 20,
    "auto_threshold": false
  },
  
  "max_open_trades": 3,
  "stake_amount": "unlimited",
  "tradable_balance_ratio": 0.99,
  "dry_run": false
}
```

## Hyperopt Optimization

The RSIFVG strategy exposes all key parameters for optimization via Hyperopt.

### Running Hyperopt

```bash
freqtrade hyperopt \
  --strategy RSIFVG \
  --hyperopt-loss SharpeHyperOptLoss \
  --epochs 1000 \
  --spaces indicator \
  --timerange 20240101-20240301
```

### Recommended Parameter Spaces

**Indicator Space** (all RSI, divergence, and FVG parameters):
```bash
--spaces indicator
```

**Conservative Optimization** (fewer epochs, focus on core parameters):
```bash
freqtrade hyperopt \
  --strategy RSIFVG \
  --hyperopt-loss SortinoHyperOptLoss \
  --epochs 500 \
  --spaces indicator \
  --timerange 20240101-20240301 \
  --hyperopt-min-trades 100
```

**Aggressive Optimization** (all parameters, longer timerange):
```bash
freqtrade hyperopt \
  --strategy RSIFVG \
  --hyperopt-loss CalmarHyperOptLoss \
  --epochs 2000 \
  --spaces indicator \
  --timerange 20230101-20240301 \
  --hyperopt-min-trades 200
```

### Key Parameters to Optimize

1. **High Impact**: `divergence_expiry`, `fvg_extend`, `max_lookback`
2. **Medium Impact**: `rsi_period`, `pivot_left`, `pivot_right`, `min_lookback`
3. **Low Impact**: `fvg_threshold`, `fvg_max_unmitigated`

## Backtesting

### Basic Backtest

```bash
freqtrade backtesting \
  --strategy RSIFVG \
  --timerange 20240101-20240301 \
  --export trades
```

### Detailed Backtest with Analysis

```bash
# Run backtest
freqtrade backtesting \
  --strategy RSIFVG \
  --timerange 20240101-20240301 \
  --export trades \
  --breakdown month

# Analyze results
freqtrade backtesting-analysis \
  --analysis-groups 0 1 2 3 4 5 \
  --enter-reason-list bull_div_fvg_engulf bear_div_fvg_engulf \
  --exit-reason-list stoploss trailing_stop_loss bear_divergence_signal bull_divergence_signal
```

### Plotting Results

```bash
freqtrade plot-dataframe \
  --strategy RSIFVG \
  --pairs BTC/USDT \
  --timerange 20240101-20240115 \
  --indicators1 fvg_bull_top fvg_bull_bottom fvg_bear_top fvg_bear_bottom \
  --indicators2 rsi_base \
  --trade-ids 1 2 3
```

## Plot Configuration

The strategy includes a comprehensive plot configuration showing:

### Main Chart
- **FVG Zones**: Bullish (green) and bearish (red) Fair Value Gap boundaries
  - `fvg_bull_top` / `fvg_bull_bottom`: Active bullish FVG zones
  - `fvg_bear_top` / `fvg_bear_bottom`: Active bearish FVG zones

### Subplots

1. **RSI Subplot**: Base timeframe RSI indicator
2. **Signals Subplot**: Divergence detection and active status
   - `bull_div`: Bullish divergence detected (green scatter)
   - `bear_div`: Bearish divergence detected (red scatter)
   - `bull_div_active`: Active bullish divergence (light green)
   - `bear_div_active`: Active bearish divergence (light coral)
3. **FVG Status Subplot**: Price position relative to FVG zones
   - `in_bull_fvg`: Price inside bullish FVG
   - `in_bear_fvg`: Price inside bearish FVG
4. **Entry Signals Subplot**: Engulfing pattern detection
   - `bull_engulf`: Bullish engulfing candles (blue scatter)
   - `bear_engulf`: Bearish engulfing candles (purple scatter)

## Risk Management Details

### Structure-Based Initial Stop

Unlike fixed percentage stops, RSIFVG uses structure-based stops that adapt to market volatility:

**Long Trades**: Stop placed below the swing low (low of candle before engulfing)
**Short Trades**: Stop placed above the swing high (high of candle before engulfing)

This approach:
- ✓ Respects market structure
- ✓ Adapts to volatility automatically
- ✓ Prevents premature stops in volatile conditions
- ✓ Tightens stops in calm markets

### Dynamic Trailing System (R-Multiple Based)

The strategy implements a sophisticated trailing stop system based on risk multiples (R):

```
R = Initial Risk = abs(Entry Price - Initial Stop)

Trailing Levels:
├─> 0R to 1.5R: Initial structure-based stop (no changes)
├─> 1.5R: Move stop to breakeven (entry price)
├─> 3R: Trail stop to lock in 2R profit
└─> 4R+: Trail stop to lock in 3R profit
```

**Benefits**:
- Protects capital by moving to breakeven quickly
- Locks in profits systematically as trade moves in your favor
- Allows winners to run while managing risk
- Prevents giving back excessive profits

### Trade Entry Validation

The `confirm_trade_entry` callback performs several validation checks:

1. **Price Validation**: Ensures entry rate > 0
2. **Structure Stop Calculation**: Computes structure-based stoploss
3. **Stop Storage**: Saves stoploss info for custom_stoploss callback
4. **Breakeven Exit Check**: Prevents re-entry after breakeven exits (configurable)

### Exit Confirmation

The strategy tracks exit reasons comprehensively:
- Stoploss hits (initial or trailing)
- Opposite divergence signals
- Profit targets (5R+)
- ROI table levels (if configured)

## Performance Considerations

### Computational Efficiency

The strategy implements several optimizations:

1. **Indicator Caching**: Results cached based on last candle timestamp
2. **Selective Recomputation**: Only recalculates when new data available
3. **Efficient Pivot Detection**: Vectorized where possible
4. **Trade Info Cleanup**: Old trade data automatically pruned (keeps last 20)

### Memory Management

- Custom info dictionary per pair
- Automatic cleanup of old divergence signals
- Limited FVG tracking (configurable max unmitigated)
- Trade stoploss data pruned to prevent bloat

### Multi-Pair Considerations

The strategy is designed for multi-pair trading:
- Independent analysis per pair
- No cross-pair dependencies
- Isolated custom info storage
- Suitable for 3-10 simultaneous pairs

## Logging and Debugging

The strategy includes comprehensive logging for debugging:

```python
# FVG Detection Logging
logger.info(f"{pair} FVG Detection ({self.fvg_timeframe}): 
  {bull_fvgs_detected} bullish, {bear_fvgs_detected} bearish FVGs found | 
  Active: {active_bull} bull zones, {active_bear} bear zones")

# Divergence Detection Logging
logger.info(f"{pair} Divergence Detection ({self.rsi_timeframe}): 
  {bull_divs} bull divs, {bear_divs} bear divs detected | 
  Active: {bull_div_active_count} bull, {bear_div_active_count} bear")

# Entry Analysis Logging
logger.info(f"{pair} ENTRY ANALYSIS
  Bull Divergence Active: {bull_div_count} candles
  Bull Engulfing: {bull_engulf_count} candles
  In Bull FVG: {bull_fvg_count} candles
  Long Entries: {long_entries}")
```

Enable detailed logging:
```bash
freqtrade trade --strategy RSIFVG --log-level INFO
```

## Strategy Strengths

1. **Multi-Timeframe Confirmation**: Reduces false signals through multiple timeframe analysis
2. **Structure-Based Risk**: Adapts stops to market structure rather than arbitrary percentages
3. **Dynamic Trade Management**: Sophisticated trailing system maximizes profit potential
4. **Flexible Configuration**: All key parameters exposed for optimization
5. **Both Directions**: Supports long and short positions for full market opportunities
6. **Smart Position Sizing**: Works with dynamic stake amounts and leverage
7. **Institutional Concepts**: Uses Fair Value Gaps (Smart Money Concepts)

## Strategy Weaknesses

1. **Lagging Nature**: Requires multiple confirmations which can delay entries
2. **Choppy Markets**: Multiple timeframes can generate conflicting signals in ranging conditions
3. **Complexity**: Three-component system requires all elements to align (fewer trades)
4. **Computation**: More resource-intensive than single-timeframe strategies
5. **Parameter Sensitivity**: Performance varies significantly with parameter choices
6. **Low Liquidity Pairs**: May struggle with pairs that don't form clear FVGs

## Best Practices

### Pair Selection
- **Liquid pairs**: BTC/USDT, ETH/USDT, major altcoins
- **Volatility**: Moderate to high volatility pairs work best
- **Avoid**: Very low volume pairs, stablecoins, highly correlated pairs

### Timeframe Selection
- **Scalping** (high frequency): 1m base, 3m RSI, 5m FVG
- **Intraday** (balanced): 3m base, 5m RSI, 15m FVG (default)
- **Swing** (position): 15m base, 1h RSI, 4h FVG

### Market Conditions
- **Trending**: Works best in trending markets with pullbacks
- **Ranging**: Reduce position sizes, increase divergence_expiry
- **High Volatility**: Increase fvg_extend, decrease min_lookback
- **Low Volatility**: Decrease fvg_extend, increase min_lookback

### Risk Management
- **Position Size**: 1-3% risk per trade recommended
- **Max Open Trades**: 3-5 for proper diversification
- **Stake Amount**: Use "unlimited" with tradable_balance_ratio 0.9-0.99
- **Leverage**: Conservative 1-2x, moderate 3-5x (futures only)

## Troubleshooting

### No Trades Generated

**Problem**: Strategy not entering any trades

**Solutions**:
1. Check if all three conditions ever align:
   ```bash
   freqtrade plot-dataframe --strategy RSIFVG --pairs BTC/USDT
   ```
2. Reduce `divergence_expiry` (try 30-40)
3. Reduce `fvg_extend` (try 30-50)
4. Increase `max_lookback` for more divergences (try 80-100)
5. Check pair liquidity and volatility

### Too Many Trades

**Problem**: Excessive trade frequency, overtrading

**Solutions**:
1. Increase `min_lookback` (try 10-15)
2. Decrease `divergence_expiry` (try 10-15)
3. Decrease `fvg_extend` (try 10-15)
4. Increase `pivot_left` and `pivot_right` (try 7-10)
5. Enable `fvg_max_unmitigated` (try 2-3)

### High Stoploss Hit Rate

**Problem**: Too many trades hitting initial stoploss

**Solutions**:
1. Verify entry timing - plot charts to see entry quality
2. Consider using `minimal_roi` table for quick exits
3. Adjust `stoploss` base percentage (try -0.07 to -0.10)
4. Reduce `max_lookback` for fresher divergences
5. Review market conditions - may not suit current regime

### Inconsistent Results

**Problem**: Backtest vs live/paper performance differs significantly

**Solutions**:
1. Use realistic fees in config (0.1% for spot, 0.02-0.04% for futures)
2. Enable `process_only_new_candles = True`
3. Ensure sufficient `startup_candle_count` (400+ recommended)
4. Check for lookahead bias with lookahead-analysis tool
5. Verify exchange candle data consistency

## Advanced Customization

### Modifying FVG Detection Logic

To use ATR-based threshold instead of fixed:

```python
# In _detect_fvgs method, replace threshold calculation:
if auto_threshold:
    atr = ta.ATR(df, timeperiod=14)
    threshold_series = atr / df['close'] * 0.5  # 50% of ATR
```

### Adding Volume Confirmation

To require volume spike on engulfing candles:

```python
# In populate_entry_trend, add volume condition:
dataframe.loc[
    (
        (dataframe['bull_div_active'] == 1) &
        (dataframe['bull_engulf'] == 1) &
        (dataframe['in_bull_fvg'] == 1) &
        (dataframe['volume'] > dataframe['volume'].rolling(20).mean() * 1.5)  # 50% above average
    ),
    ['enter_long', 'enter_tag']
] = (1, 'bull_div_fvg_engulf_volume')
```

### Adding Time-Based Filters

To avoid low-liquidity hours:

```python
# In populate_entry_trend, add time filter:
from datetime import time

dataframe['hour'] = pd.to_datetime(dataframe['date']).dt.hour

dataframe.loc[
    (
        (dataframe['bull_div_active'] == 1) &
        (dataframe['bull_engulf'] == 1) &
        (dataframe['in_bull_fvg'] == 1) &
        (dataframe['volume'] > 0) &
        (dataframe['hour'].between(8, 22))  # Only trade 8 AM - 10 PM UTC
    ),
    ['enter_long', 'enter_tag']
] = (1, 'bull_div_fvg_engulf')
```

## Further Reading

- [Freqtrade Strategy Customization](strategy-customization.md)
- [Advanced Strategy Features](strategy-advanced.md)
- [Strategy Callbacks](strategy-callbacks.md)
- [Hyperopt Documentation](hyperopt.md)
- [Backtesting Guide](backtesting.md)
- [Trade Object Reference](trade-object.md)

## Disclaimer

This strategy is provided for educational purposes. Past performance does not guarantee future results. Always test thoroughly in dry-run mode before risking real capital. Adjust parameters to suit your risk tolerance and market conditions.
