# EMAFVG Strategy Documentation

## Overview

The EMAFVG (EMA Fair Value Gap) strategy is a trend-following multi-timeframe trading strategy that combines two core technical analysis concepts:

1. **Fair Value Gaps (FVG)** - Price inefficiencies that act as support/resistance zones
2. **EMA Trend Filter** - Moving average crossover to identify trend direction

This strategy operates on multiple configurable timeframes to ensure entries occur only with the trend, using Fair Value Gaps as precise entry zones and engulfing candles for execution timing.

## Strategy Concept

The EMAFVG strategy identifies trading opportunities by waiting for the confluence of three key conditions:

- **Trend Direction**: EMA crossover on EMA timeframe (default 1m) confirms market direction
- **Market Structure**: Fair Value Gaps on higher timeframe (default 15m) indicate areas of institutional interest
- **Execution Trigger**: Engulfing candles on base timeframe (default 1m) provide precise entry timing

This multi-layered approach ensures that entries occur only when trend, structure, and price action all align in the same direction.

## Core Components

### 1. EMA Trend Filter

The strategy uses two Exponential Moving Averages (EMAs) to determine market trend:

- **EMA Fast (default 50)**: Short-term trend indicator
- **EMA Slow (default 100)**: Long-term trend indicator

#### Trend Rules

**Uptrend (Long Bias)**:
```
EMA50 > EMA100
```
- Fast EMA above slow EMA indicates bullish momentum
- Only long entries allowed in this condition

**Downtrend (Short Bias)**:
```
EMA100 > EMA50
```
- Slow EMA above fast EMA indicates bearish momentum
- Only short entries allowed in this condition

#### Why EMA?

Exponential Moving Averages give more weight to recent prices compared to Simple Moving Averages (SMA), making them more responsive to current market conditions while still filtering noise.

### 2. Fair Value Gaps (FVG)

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
- **Active Period**: FVGs remain active for a configurable number of periods (default 50 bars on 15m timeframe)
- **Mitigation**: 
  - Bullish FVGs are "filled" when price closes **below** the FVG bottom
  - Bearish FVGs are "filled" when price closes **above** the FVG top
- **Zones**: Each FVG has a top and bottom boundary that defines the support/resistance area
- **Touch Detection**: Price is considered "in FVG" when:
  - Candle low ≤ FVG top AND candle high ≥ FVG bottom

#### FVG Tracking

The strategy maintains a dynamic list of active FVGs:
- New FVGs are registered when detected on the FVG timeframe
- FVGs expire after the configured extension period
- FVGs are removed when filled (price closes beyond the zone)
- Optional limit on maximum simultaneous unmitigated FVGs

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
EMA Timeframe (1m):
└─> EMA50 and EMA100 calculated
    └─> EMA50 > EMA100 → Uptrend confirmed
        └─> Long entries enabled

FVG Timeframe (15m):
└─> Bullish FVG detected and tracked
    └─> Gap zone remains active for 50 periods (12.5 hours)
    └─> FVG added to active list
```

#### 2. Entry Conditions (Base Timeframe - 1m)
All conditions must be TRUE simultaneously:
```
✓ trend_long == True         (EMA50 > EMA100)
✓ bull_engulf == 1           (Bullish engulfing candle)
✓ in_bull_fvg == 1           (Price touching/in bullish FVG zone)
✓ volume > 0                 (Valid volume)
```

#### 3. Entry Execution
```
Entry Signal: "ema_bull_fvg_engulf"
Entry Price: Close of engulfing candle
Position Size: Per configured stake amount
Direction: LONG
```

#### 4. Risk Management (Stoploss)
```
Initial Stop: Swing low of candle BEFORE engulfing candle
Structure-based: Protects below support level

Dynamic Trailing:
├─> At 1.5R profit: Move to breakeven (0.01% buffer)
├─> At 3R profit: Trail stop to 2R (lock in 2R profit)
└─> At 4R profit: Trail stop to 3R (lock in 3R profit)

Where R = Initial risk (distance from entry to initial stop)
```

#### 5. Exit Conditions
```
Exit Triggers:
├─> Stoploss hit (initial or trailing)
├─> Profit target: 2R (configurable risk_reward parameter)
├─> ROI table reached (time-based targets)
├─> Trend reversal: EMA100 > EMA50 (downtrend detected)
└─> Manual exit via custom_exit

Exit Tag Examples:
├─> "stoploss" - Initial stop hit
├─> "trailing_stop_loss" - Trailing stop hit
├─> "target_2R" - Risk/reward target reached
├─> "roi" - ROI table target hit
└─> "exit_long" - Trend reversal exit
```

### Short Trade Flow

#### 1. Setup Phase (Higher Timeframes)
```
EMA Timeframe (1m):
└─> EMA50 and EMA100 calculated
    └─> EMA100 > EMA50 → Downtrend confirmed
        └─> Short entries enabled

FVG Timeframe (15m):
└─> Bearish FVG detected and tracked
    └─> Gap zone remains active for 50 periods (12.5 hours)
    └─> FVG added to active list
```

#### 2. Entry Conditions (Base Timeframe - 1m)
All conditions must be TRUE simultaneously:
```
✓ trend_short == True        (EMA100 > EMA50)
✓ bear_engulf == 1           (Bearish engulfing candle)
✓ in_bear_fvg == 1           (Price touching/in bearish FVG zone)
✓ volume > 0                 (Valid volume)
```

#### 3. Entry Execution
```
Entry Signal: "ema_bear_fvg_engulf"
Entry Price: Close of engulfing candle
Position Size: Per configured stake amount
Direction: SHORT
```

#### 4. Risk Management (Stoploss)
```
Initial Stop: Swing high of candle BEFORE engulfing candle
Structure-based: Protects above resistance level

Dynamic Trailing:
├─> At 1.5R profit: Move to breakeven (0.01% buffer)
├─> At 3R profit: Trail stop to 2R (lock in 2R profit)
└─> At 4R profit: Trail stop to 3R (lock in 3R profit)

Where R = Initial risk (distance from entry to initial stop)
```

#### 5. Exit Conditions
```
Exit Triggers:
├─> Stoploss hit (initial or trailing)
├─> Profit target: 2R (configurable risk_reward parameter)
├─> ROI table reached (time-based targets)
├─> Trend reversal: EMA50 > EMA100 (uptrend detected)
└─> Manual exit via custom_exit

Exit Tag Examples:
├─> "stoploss" - Initial stop hit
├─> "trailing_stop_loss" - Trailing stop hit
├─> "target_2R" - Risk/reward target reached
├─> "roi" - ROI table target hit
└─> "exit_short" - Trend reversal exit
```

## Complete Trade Lifecycle Example

### Long Trade Example

```
Time: 09:00 - EMA trend check on 1m timeframe
├─> EMA50: 45,320
├─> EMA100: 45,280
└─> EMA50 > EMA100 → UPTREND CONFIRMED

Time: 09:15 - FVG detected on 15m timeframe
└─> Bullish FVG: Zone between 45,280 and 45,320
    └─> Active for next 50 periods (12.5 hours on 15m)
    └─> Added to active_fvgs list

Time: 09:18 - Engulfing candle forms on 1m
├─> Previous candle: Bearish (45,310 → 45,305)
├─> Current candle: Bullish (45,305 → 45,335)
├─> Price inside FVG zone (45,280-45,320)
├─> Trend still long (EMA50 > EMA100)
└─> ALL CONDITIONS MET → ENTRY SIGNAL

ENTRY:
├─> Price: 45,335
├─> Stop: 45,300 (swing low before engulfing)
├─> Risk: 35 points (0.077%)
├─> Tag: "ema_bull_fvg_engulf"
└─> Target: 2R = 45,405 (70 points profit)

Time: 09:22 - Price moves to 45,388
├─> Profit: 53 points (0.117%)
├─> Risk multiple: 1.5R
└─> Stop moved to BREAKEVEN (45,335)

Time: 09:30 - Price reaches 45,440
├─> Profit: 105 points (0.231%)
├─> Risk multiple: 3R
└─> Stop trailed to 2R (45,405) - locks in 70 points

Time: 09:34 - Price hits 2R target
├─> Price: 45,405 (exactly 2R target)
└─> EXIT SIGNAL: "target_2R"

EXIT:
├─> Price: 45,405
├─> Profit: 70 points (0.154%)
├─> Risk multiple: 2.0R (perfect target hit)
├─> Tag: "target_2R"
└─> Trade Duration: 16 minutes
```

### Short Trade Example

```
Time: 14:00 - EMA trend check on 1m timeframe
├─> EMA50: 46,120
├─> EMA100: 46,180
└─> EMA100 > EMA50 → DOWNTREND CONFIRMED

Time: 14:15 - FVG detected on 15m timeframe
└─> Bearish FVG: Zone between 46,100 and 46,140
    └─> Active for next 50 periods (12.5 hours on 15m)
    └─> Added to active_fvgs list

Time: 14:19 - Engulfing candle forms on 1m
├─> Previous candle: Bullish (46,125 → 46,130)
├─> Current candle: Bearish (46,130 → 46,095)
├─> Price inside FVG zone (46,100-46,140)
├─> Trend still short (EMA100 > EMA50)
└─> ALL CONDITIONS MET → ENTRY SIGNAL

ENTRY:
├─> Price: 46,095
├─> Stop: 46,135 (swing high before engulfing)
├─> Risk: 40 points (0.087%)
├─> Tag: "ema_bear_fvg_engulf"
└─> Target: 2R = 46,015 (80 points profit)

Time: 14:24 - Price drops to 46,055
├─> Profit: 40 points (0.087%)
├─> Risk multiple: 1.0R
└─> Stop still at swing high (46,135)

Time: 14:28 - Price reaches 46,035
├─> Profit: 60 points (0.130%)
├─> Risk multiple: 1.5R
└─> Stop moved to BREAKEVEN (46,095)

Time: 14:32 - Price hits 46,000
├─> Profit: 95 points (0.206%)
├─> Risk multiple: 2.4R
└─> Beyond 2R target

EXIT:
├─> Price: 46,010 (slightly past 2R target due to execution)
├─> Profit: 85 points (0.184%)
├─> Risk multiple: 2.1R
├─> Tag: "target_2R"
└─> Trade Duration: 13 minutes
```

## Strategy Parameters

### Timeframe Configuration

```python
timeframe = '1m'           # Base timeframe for execution
ema_timeframe = '1m'       # Timeframe for EMA trend filter
fvg_timeframe = '15m'      # Timeframe for Fair Value Gap detection
```

**Recommendation**: Higher timeframes reduce noise but generate fewer signals:
- **Scalping (high frequency)**: Base 1m, EMA 1m, FVG 5m
- **Intraday (balanced)**: Base 1m, EMA 1m, FVG 15m (default)
- **Swing (position)**: Base 5m, EMA 15m, FVG 1h

### EMA Parameters

| Parameter | Type | Range | Default | Description |
|-----------|------|-------|---------|-------------|
| `ema_fast` | IntParameter | 30-70 | 50 | Fast EMA period (short-term trend) |
| `ema_slow` | IntParameter | 80-150 | 100 | Slow EMA period (long-term trend) |

**Common EMA Combinations**:
- Conservative: 50/100 (default) - slower trend changes
- Moderate: 50/200 - classic combination
- Aggressive: 20/50 - faster trend changes
- Very Aggressive: 9/21 - very responsive to price changes

### FVG Parameters

| Parameter | Type | Range | Default | Description |
|-----------|------|-------|---------|-------------|
| `fvg_threshold` | DecimalParameter | 0.0-0.01 | 0.0 | Minimum gap size as % of price |
| `fvg_extend_15m` | IntParameter | 20-100 | 50 | Periods FVG remains active (in FVG timeframe bars) |
| `fvg_max_unmitigated` | IntParameter | 0-5 | 0 | Max simultaneous unmitigated FVGs (0=unlimited) |

**FVG Extension Calculation**:
```
Active Duration = fvg_extend_15m × FVG Timeframe
Example: 50 bars × 15 minutes = 750 minutes (12.5 hours)
```

### Risk Management Parameters

| Parameter | Type | Range | Default | Description |
|-----------|------|-------|---------|-------------|
| `risk_reward` | DecimalParameter | 1.5-3.0 | 2.0 | Risk/reward ratio for profit target |
| `stoploss` | Float | - | -0.05 | Base stoploss percentage (5%) - overridden by structure |
| `minimal_roi` | Dict | - | See below | Time-based ROI table |
| `use_custom_stoploss` | Boolean | - | True | Enable dynamic stoploss management |
| `can_short` | Boolean | - | True | Enable short positions |

**Default ROI Table**:
```python
minimal_roi = {
    "0": 0.20,      # 20% profit target from start
    "30": 0.10,     # 10% profit target after 30 minutes
    "120": 0.05,    # 5% profit target after 2 hours
    "360": 0        # Breakeven after 6 hours
}
```

## Configuration Example

### Basic Configuration

```json
{
  "strategy": "EMAFVG",
  "timeframe": "1m",
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
      "BNB/USDT",
      "SOL/USDT"
    ]
  },
  
  "entry_pricing": {
    "price_side": "same",
    "use_order_book": false
  },
  
  "exit_pricing": {
    "price_side": "same",
    "use_order_book": false
  },
  
  "max_open_trades": 5,
  "tradable_balance_ratio": 0.99
}
```

### Advanced Configuration with Parameter Overrides

```json
{
  "strategy": "EMAFVG",
  "timeframe": "1m",
  
  "strategy_config": {
    "ema_timeframe": "1m",
    "fvg_timeframe": "15m",
    "ema_fast": 50,
    "ema_slow": 100,
    "fvg_threshold": 0.0,
    "fvg_extend_15m": 50,
    "fvg_max_unmitigated": 3,
    "risk_reward": 2.0
  },
  
  "max_open_trades": 5,
  "stake_amount": "unlimited",
  "tradable_balance_ratio": 0.99,
  "dry_run": false,
  
  "telegram": {
    "enabled": true,
    "token": "YOUR_TOKEN",
    "chat_id": "YOUR_CHAT_ID"
  }
}
```

### Futures/Leverage Configuration

```json
{
  "strategy": "EMAFVG",
  "timeframe": "1m",
  "trading_mode": "futures",
  "margin_mode": "isolated",
  
  "exchange": {
    "name": "binance",
    "pair_whitelist": [
      "BTC/USDT:USDT",
      "ETH/USDT:USDT"
    ]
  },
  
  "stake_amount": "unlimited",
  "max_open_trades": 3,
  "tradable_balance_ratio": 0.5,
  
  "strategy_config": {
    "risk_reward": 2.5
  }
}
```

## Hyperopt Optimization

The EMAFVG strategy exposes key parameters for optimization via Hyperopt.

### Running Hyperopt

```bash
freqtrade hyperopt \
  --strategy EMAFVG \
  --hyperopt-loss SharpeHyperOptLoss \
  --epochs 1000 \
  --spaces indicator sell \
  --timerange 20240101-20240301
```

### Recommended Parameter Spaces

**Indicator Space** (EMA and FVG parameters):
```bash
--spaces indicator
```

**Sell Space** (risk_reward parameter):
```bash
--spaces sell
```

**Combined Optimization** (all parameters):
```bash
freqtrade hyperopt \
  --strategy EMAFVG \
  --hyperopt-loss SortinoHyperOptLoss \
  --epochs 1500 \
  --spaces indicator sell \
  --timerange 20240101-20240301 \
  --hyperopt-min-trades 150
```

**Fast Optimization** (EMA only):
```bash
freqtrade hyperopt \
  --strategy EMAFVG \
  --hyperopt-loss CalmarHyperOptLoss \
  --epochs 500 \
  --spaces indicator \
  --timerange 20240101-20240301 \
  --hyperopt-min-trades 100 \
  --hyperopt-ignore-missing-spaces
```

### Key Parameters to Optimize

1. **High Impact**: `ema_fast`, `ema_slow`, `risk_reward`
2. **Medium Impact**: `fvg_extend_15m`, `fvg_max_unmitigated`
3. **Low Impact**: `fvg_threshold`

### Hyperopt Results Analysis

After optimization, analyze the results:

```bash
# View best results
freqtrade hyperopt-list --best 10 --no-details

# Show specific result details
freqtrade hyperopt-show -n 1

# Apply best parameters
freqtrade hyperopt-show -n 1 --export-csv best_params.csv
```

## Backtesting

### Basic Backtest

```bash
freqtrade backtesting \
  --strategy EMAFVG \
  --timerange 20240101-20240301 \
  --export trades
```

### Detailed Backtest with Analysis

```bash
# Run backtest
freqtrade backtesting \
  --strategy EMAFVG \
  --timerange 20240101-20240301 \
  --export trades \
  --breakdown month week

# Analyze results
freqtrade backtesting-analysis \
  --analysis-groups 0 1 2 3 4 5 \
  --enter-reason-list ema_bull_fvg_engulf ema_bear_fvg_engulf \
  --exit-reason-list stoploss trailing_stop_loss target_2R roi
```

### Performance Metrics

Focus on these key metrics:
- **Win Rate**: Should be 45-60% for trend-following strategies
- **Profit Factor**: Target > 1.5
- **Expectancy**: Average profit per trade (target > 0.5%)
- **Sharpe Ratio**: Risk-adjusted returns (target > 1.0)
- **Max Drawdown**: Keep below 15-20%

### Plotting Results

```bash
# Plot specific pairs
freqtrade plot-dataframe \
  --strategy EMAFVG \
  --pairs BTC/USDT \
  --timerange 20240115-20240120 \
  --indicators1 ema50 ema100 \
  --trade-ids 1 2 3 4 5

# Plot all trades for a pair
freqtrade plot-dataframe \
  --strategy EMAFVG \
  --pairs ETH/USDT \
  --timerange 20240201-20240207
```

## Plot Configuration

The strategy includes a basic plot configuration showing:

### Main Chart
- **EMA Lines**: Fast EMA (50) and slow EMA (100)
  - When fast > slow: Green zone (uptrend)
  - When slow > fast: Red zone (downtrend)

### Subplots

1. **Signals Subplot**: Engulfing pattern detection
   - `bull_engulf`: Bullish engulfing candles (green scatter)
   - `bear_engulf`: Bearish engulfing candles (red scatter)

### Custom Plot Extensions

You can enhance the plot config by adding FVG zones:

```python
plot_config = {
    'main_plot': {
        'ema50': {'color': 'blue', 'type': 'line'},
        'ema100': {'color': 'orange', 'type': 'line'},
    },
    'subplots': {
        'Trend': {
            'trend_long': {'color': 'green'},
            'trend_short': {'color': 'red'},
        },
        'FVG Status': {
            'in_bull_fvg': {'color': 'lightgreen', 'type': 'scatter'},
            'in_bear_fvg': {'color': 'lightcoral', 'type': 'scatter'},
        },
        'Signals': {
            'bull_engulf': {'color': 'green', 'type': 'scatter'},
            'bear_engulf': {'color': 'red', 'type': 'scatter'},
        }
    }
}
```

## Risk Management Details

### Structure-Based Initial Stop

Unlike fixed percentage stops, EMAFVG uses structure-based stops that adapt to market volatility:

**Long Trades**: Stop placed below the swing low (low of candle before engulfing)
**Short Trades**: Stop placed above the swing high (high of candle before engulfing)

**Benefits**:
- ✓ Respects market structure
- ✓ Adapts to volatility automatically
- ✓ Prevents premature stops in volatile conditions
- ✓ Tightens stops in calm markets
- ✓ Logical exit point based on invalidation

### Dynamic Trailing System (R-Multiple Based)

The strategy implements a sophisticated trailing stop system based on risk multiples (R):

```
R = Initial Risk = abs(Entry Price - Initial Stop)

Trailing Levels:
├─> 0R to 1.5R: Initial structure-based stop (no changes)
├─> 1.5R: Move stop to breakeven (entry price - 0.01% buffer)
├─> 3R: Trail stop to lock in 2R profit
└─> 4R+: Trail stop to lock in 3R profit
```

**Benefits**:
- Protects capital by moving to breakeven quickly
- Locks in profits systematically
- Allows winners to run while managing risk
- Prevents giving back excessive profits
- Works with any initial risk size

### Risk/Reward Target

The `risk_reward` parameter (default 2.0) sets a profit target:

```python
Target Profit = Initial Risk × risk_reward
```

**Example**:
- Entry: 45,335
- Stop: 45,300 (35 points risk = 0.077%)
- Target: 45,335 + (35 × 2) = 45,405

This ensures every trade has a predefined profit objective.

### ROI Table

Time-based exits provide additional profit-taking:

```python
minimal_roi = {
    "0": 0.20,      # Exit at 20% profit immediately
    "30": 0.10,     # Exit at 10% profit after 30 min
    "120": 0.05,    # Exit at 5% profit after 2 hours
    "360": 0        # Exit at breakeven after 6 hours
}
```

Useful for capturing quick profits and avoiding prolonged exposure.

## Performance Considerations

### Computational Efficiency

The strategy implements several optimizations:

1. **Indicator Caching**: Results cached based on last candle timestamp
2. **Selective Recomputation**: Only recalculates when new data available
3. **Efficient FVG Tracking**: Dynamic list management with automatic cleanup
4. **Trade Info Cleanup**: Old trade data automatically pruned (keeps last 20)

### Memory Management

- **FVG Deduplication**: Prevents duplicate FVG entries
- **Time-based Pruning**: Old FVGs removed automatically
- **Optional Limits**: `fvg_max_unmitigated` caps active FVG count
- **Trade Data**: Stoploss info limited to recent 20 trades

### Multi-Pair Considerations

The strategy is designed for multi-pair trading:
- Independent analysis per pair
- Isolated FVG tracking per pair
- Separate trend detection per pair
- Suitable for 5-10 simultaneous pairs

### 1-Minute Timeframe Considerations

Running on 1m timeframe requires:
- **Fast execution**: VPS with low latency recommended
- **Stable connection**: Reliable exchange API access
- **Sufficient resources**: CPU and RAM for real-time processing
- **Quality data**: Clean candle data without gaps

## Strategy Strengths

1. **Clear Trend Filter**: EMA crossover provides unambiguous trend direction
2. **Multi-Timeframe Confirmation**: Reduces false signals through multiple timeframe analysis
3. **Structure-Based Risk**: Adapts stops to market structure rather than arbitrary percentages
4. **Dynamic Trade Management**: Sophisticated trailing system maximizes profit potential
5. **Flexible Configuration**: All key parameters exposed for optimization
6. **Both Directions**: Supports long and short positions for full market opportunities
7. **Fast Execution**: 1m timeframe allows quick entries and exits
8. **Smart Money Concepts**: Uses Fair Value Gaps for institutional-level entries
9. **Time-Based Exits**: ROI table prevents prolonged losing trades

## Strategy Weaknesses

1. **Whipsaw Risk**: EMA crossovers can produce false signals in ranging markets
2. **Lag**: EMAs are lagging indicators - entries may miss early trend moves
3. **High Frequency**: 1m timeframe can generate many signals and transaction costs
4. **Choppy Markets**: Performs poorly in sideways/ranging conditions
5. **FVG Dependency**: Requires clear FVGs to form (may not occur in all markets)
6. **Parameter Sensitivity**: Performance varies significantly with EMA periods
7. **Computation**: More resource-intensive than single-timeframe strategies
8. **Execution Speed**: 1m signals require fast order execution

## Best Practices

### Pair Selection
- **Liquid pairs**: BTC/USDT, ETH/USDT, major altcoins with high volume
- **Volatility**: Moderate to high volatility pairs work best
- **Avoid**: Very low volume pairs, stablecoins, highly correlated pairs
- **Trending pairs**: Works best on pairs with clear directional moves

### Timeframe Selection
- **Scalping** (very high frequency): 1m base, 1m EMA, 5m FVG (default)
- **Intraday** (balanced): 5m base, 5m EMA, 15m FVG
- **Swing** (position): 15m base, 1h EMA, 4h FVG

### Market Conditions
- **Trending**: Optimal - clear directional moves with pullbacks
- **Ranging**: Reduce position sizes or disable strategy
- **High Volatility**: Increase `fvg_extend_15m`, widen EMA periods
- **Low Volatility**: Decrease `fvg_extend_15m`, tighten EMA periods

### Risk Management
- **Position Size**: 1-2% risk per trade recommended (aggressive 3%)
- **Max Open Trades**: 3-5 for proper diversification
- **Stake Amount**: Use "unlimited" with tradable_balance_ratio 0.5-0.99
- **Leverage**: Conservative 1-2x, moderate 3-5x (futures only)
- **Stop Losses**: Always respect structure-based stops

### Optimization Tips
1. Start with EMA optimization (highest impact)
2. Test different risk_reward ratios (2.0-3.0)
3. Adjust `fvg_extend_15m` based on market speed
4. Use `fvg_max_unmitigated` to limit simultaneous FVGs
5. Consider disabling ROI table if using 2R target only

## Troubleshooting

### No Trades Generated

**Problem**: Strategy not entering any trades

**Solutions**:
1. Check EMA trend alignment - plot charts to verify crossovers:
   ```bash
   freqtrade plot-dataframe --strategy EMAFVG --pairs BTC/USDT
   ```
2. Verify FVG formation - check if FVGs are actually forming:
   - Reduce `fvg_threshold` to 0.0
   - Increase `fvg_extend_15m` (try 80-100)
3. Check timeframe alignment - ensure informative pairs loading correctly
4. Verify pair liquidity and volatility
5. Consider using faster EMA periods (e.g., 20/50 instead of 50/100)

### Too Many Trades

**Problem**: Excessive trade frequency, overtrading

**Solutions**:
1. Increase EMA periods for slower trend changes:
   - Try 100/200 or 50/200 combinations
2. Decrease `fvg_extend_15m` (try 20-30)
3. Enable `fvg_max_unmitigated` (try 2-3)
4. Increase `fvg_threshold` (try 0.0005-0.001)
5. Use higher base timeframe (5m instead of 1m)

### High Stoploss Hit Rate

**Problem**: Too many trades hitting initial stoploss

**Solutions**:
1. Verify entry timing - plot charts to see entry quality
2. Check if EMAs are too fast (whipsaw in ranging markets)
3. Adjust `stoploss` base percentage (try -0.07 to -0.10)
4. Consider wider EMA periods (slower trend changes)
5. Add volume filter to engulfing patterns
6. Review market conditions - may be too choppy

### Inconsistent Results

**Problem**: Backtest vs live/paper performance differs significantly

**Solutions**:
1. Use realistic fees in config (0.1% for spot, 0.02-0.04% for futures)
2. Enable `process_only_new_candles = True`
3. Ensure sufficient `startup_candle_count` (400+ recommended)
4. Check for lookahead bias with lookahead-analysis tool
5. Verify exchange candle data consistency
6. Test on paper trading before live

### FVG Not Working

**Problem**: Price never seems to touch FVG zones

**Solutions**:
1. Check FVG detection - add debug logging
2. Increase `fvg_extend_15m` for longer active periods
3. Set `fvg_threshold` to 0.0 to catch all gaps
4. Verify FVG timeframe has sufficient volatility
5. Plot FVG zones to visualize detection

### EMA Whipsaws

**Problem**: Trend changes too frequently causing losses

**Solutions**:
1. Use longer EMA periods (e.g., 100/200)
2. Switch to higher timeframe for EMA calculation
3. Add trend confirmation (e.g., price above/below both EMAs)
4. Reduce position sizes during uncertain periods
5. Consider using EMA on higher timeframe (5m or 15m)

## Advanced Customization

### Adding ATR-Based Stops

To use Average True Range for dynamic stops:

```python
def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
    # ... existing code ...
    
    # Add ATR calculation
    dataframe['atr'] = ta.ATR(dataframe, timeperiod=14)
    
    # Calculate ATR-based stop distance (2x ATR)
    dataframe['atr_stop_long'] = dataframe['close'] - (dataframe['atr'] * 2)
    dataframe['atr_stop_short'] = dataframe['close'] + (dataframe['atr'] * 2)
    
    return dataframe
```

### Adding Volume Confirmation

To require volume spike on engulfing candles:

```python
def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
    # Calculate volume moving average
    dataframe['volume_ma'] = dataframe['volume'].rolling(20).mean()
    
    # Long entry with volume filter
    dataframe.loc[
        (
            (dataframe['trend_long'] == True) &
            (dataframe['bull_engulf'] == 1) &
            (dataframe['in_bull_fvg'] == 1) &
            (dataframe['volume'] > dataframe['volume_ma'] * 1.5)  # 50% above average
        ),
        ['enter_long', 'enter_tag']
    ] = (1, 'ema_bull_fvg_engulf_volume')
    
    return dataframe
```

### Adding RSI Filter

To avoid overbought/oversold conditions:

```python
def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
    # ... existing code ...
    
    # Add RSI
    dataframe['rsi'] = ta.RSI(dataframe, timeperiod=14)
    
    return dataframe

def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
    # Long entry with RSI filter
    dataframe.loc[
        (
            (dataframe['trend_long'] == True) &
            (dataframe['bull_engulf'] == 1) &
            (dataframe['in_bull_fvg'] == 1) &
            (dataframe['volume'] > 0) &
            (dataframe['rsi'] < 70)  # Not overbought
        ),
        ['enter_long', 'enter_tag']
    ] = (1, 'ema_bull_fvg_engulf')
    
    # Short entry with RSI filter
    dataframe.loc[
        (
            (dataframe['trend_short'] == True) &
            (dataframe['bear_engulf'] == 1) &
            (dataframe['in_bear_fvg'] == 1) &
            (dataframe['volume'] > 0) &
            (dataframe['rsi'] > 30)  # Not oversold
        ),
        ['enter_short', 'enter_tag']
    ] = (1, 'ema_bear_fvg_engulf')
    
    return dataframe
```

### Adding Time-Based Filters

To avoid low-liquidity hours:

```python
def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
    # Add hour column
    dataframe['hour'] = pd.to_datetime(dataframe['date']).dt.hour
    
    # Long entry with time filter
    dataframe.loc[
        (
            (dataframe['trend_long'] == True) &
            (dataframe['bull_engulf'] == 1) &
            (dataframe['in_bull_fvg'] == 1) &
            (dataframe['volume'] > 0) &
            (dataframe['hour'].between(8, 22))  # Only 8 AM - 10 PM UTC
        ),
        ['enter_long', 'enter_tag']
    ] = (1, 'ema_bull_fvg_engulf')
    
    return dataframe
```

### Using Multiple EMA Timeframes

To add confirmation from higher timeframe trend:

```python
def informative_pairs(self):
    wl = self.dp.current_whitelist()
    pairs = ([(p, self.ema_timeframe) for p in wl] +
             [(p, self.fvg_timeframe) for p in wl] +
             [(p, '1h') for p in wl])  # Add hourly trend
    return pairs

def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
    # ... existing code ...
    
    # Get hourly trend
    inf_1h = self.dp.get_pair_dataframe(pair=pair, timeframe='1h')
    inf_1h['ema50_1h'] = ta.EMA(inf_1h, timeperiod=50)
    inf_1h['ema100_1h'] = ta.EMA(inf_1h, timeperiod=100)
    dataframe = merge_informative_pair(dataframe, inf_1h, self.timeframe, '1h', ffill=True)
    
    # Higher timeframe trend
    dataframe['trend_long_1h'] = dataframe['ema50_1h_1h'] > dataframe['ema100_1h_1h']
    dataframe['trend_short_1h'] = dataframe['ema100_1h_1h'] > dataframe['ema50_1h_1h']
    
    return dataframe

def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
    # Long entry with hourly confirmation
    dataframe.loc[
        (
            (dataframe['trend_long'] == True) &
            (dataframe['trend_long_1h'] == True) &  # Hourly trend confirmation
            (dataframe['bull_engulf'] == 1) &
            (dataframe['in_bull_fvg'] == 1) &
            (dataframe['volume'] > 0)
        ),
        ['enter_long', 'enter_tag']
    ] = (1, 'ema_bull_fvg_engulf')
    
    return dataframe
```

## Comparison with RSIFVG Strategy

| Feature | EMAFVG | RSIFVG |
|---------|--------|--------|
| **Trend Filter** | EMA crossover | RSI divergence |
| **Entry Signal** | Simpler (2 conditions) | Complex (3 conditions) |
| **Trade Frequency** | Higher | Lower |
| **Lagging** | More (EMAs lag) | Less (divergence leads) |
| **Best For** | Strong trends | Reversals & pullbacks |
| **Complexity** | Lower | Higher |
| **Computational** | Lighter | Heavier (pivot detection) |
| **Whipsaw Risk** | Higher | Lower |
| **Early Entries** | No (lag) | Yes (divergence leads) |

**When to Use EMAFVG**:
- Strong trending markets
- Prefer simplicity over precision
- Want more trade opportunities
- Comfortable with some whipsaws

**When to Use RSIFVG**:
- Ranging or choppy markets
- Need precise reversal entries
- Prefer quality over quantity
- Want divergence confirmation

## Further Reading

- [Freqtrade Strategy Customization](strategy-customization.md)
- [Advanced Strategy Features](strategy-advanced.md)
- [Strategy Callbacks](strategy-callbacks.md)
- [Hyperopt Documentation](hyperopt.md)
- [Backtesting Guide](backtesting.md)
- [Trade Object Reference](trade-object.md)
- [EMA Indicator Documentation](https://www.investopedia.com/terms/e/ema.asp)

## Disclaimer

This strategy is provided for educational purposes. Past performance does not guarantee future results. Always test thoroughly in dry-run mode before risking real capital. Adjust parameters to suit your risk tolerance and market conditions. The 1-minute timeframe requires fast execution and low latency - ensure your infrastructure can handle it before going live.
