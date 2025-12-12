import numpy as np
import pandas as pd
import pandas_ta as pta
import talib.abstract as ta
from pandas import DataFrame
from freqtrade.strategy import (
    IStrategy,
    IntParameter,
    DecimalParameter,
    BooleanParameter,
    merge_informative_pair,
)
from freqtrade.persistence import Trade
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, Tuple


class MeanReversion(IStrategy):
    """
    Enhanced Mean Reversion Strategy with Quant Improvements.

    Novel Features:
    1. Volatility Regime Filter - Trade only in favorable volatility conditions
    2. Trailing Stop Loss - Lock in profits dynamically
    3. Partial Take Profits - Scale out at middle BB (50%) and opposite BB (50%)
    4. Volume Spike Confirmation - Require above-average volume
    5. RSI Divergence Detection - Higher probability entries
    6. Entry Quality Score - Filter weak setups
    7. Drawdown-Aware Position Sizing - Anti-martingale approach
    8. Mean Reversion Z-Score - Quantify deviation from mean

    Rules:
    - Timeframe: 4H for Trend/Momentum, Current (e.g., 1H) for Entry/Exit.
    - Long Entry:
        - RSI (4H) > threshold (trend confirmation)
        - SuperTrend (4H) is Uptrend
        - ADX (4H) > strong threshold
        - ADX (Current) > weak threshold
        - Price < Lower Bollinger Band (Current)
        - Volume > SMA(Volume) * multiplier
        - BB Width in acceptable range
        - Entry quality score > threshold
    - Short Entry: (inverse conditions)
    - Stop Loss: Entry +/- ATR multiplier (with trailing)
    - Take Profit: 50% at Middle BB, 50% at Opposite BB
    - Risk: Dynamic based on recent drawdown
    """

    INTERFACE_VERSION = 3

    # Minimal ROI - We use custom exit, so set this high
    minimal_roi = {"0": 100}

    # Stoploss - We use custom stoploss
    stoploss = -0.99

    # Timeframes
    timeframe = '1h'
    informative_timeframe = '4h'

    # Enable shorting
    can_short: bool = True

    # Enable position adjustment for partial exits
    position_adjustment_enable = True

    # Parameters
    # BB
    bb_window = IntParameter(10, 30, default=20, space="indicator", optimize=True)
    bb_std = DecimalParameter(1.5, 3.0, default=2.0, space="indicator", optimize=True)

    # RSI
    rsi_length = IntParameter(10, 20, default=14, space="indicator", optimize=True)
    rsi_long_threshold = IntParameter(50, 70, default=55, space="buy", optimize=True)
    rsi_short_threshold = IntParameter(30, 50, default=45, space="buy", optimize=True)

    # ADX
    adx_length = IntParameter(10, 20, default=14, space="indicator", optimize=True)
    adx_strong_threshold = IntParameter(20, 40, default=25, space="buy", optimize=True)
    adx_weak_threshold = IntParameter(15, 30, default=20, space="buy", optimize=True)

    # SuperTrend
    st_length = IntParameter(7, 14, default=10, space="indicator", optimize=True)
    st_multiplier = DecimalParameter(2.0, 4.0, default=3.0, space="indicator", optimize=True)

    # === NEW QUANT PARAMETERS ===
    # Volume filter
    volume_sma_length = IntParameter(10, 30, default=20, space="indicator", optimize=True)
    volume_multiplier = DecimalParameter(1.0, 2.0, default=1.1, space="buy", optimize=True)

    # Volatility regime (BB width as % of price)
    bb_width_min = DecimalParameter(0.01, 0.03, default=0.015, space="buy", optimize=True)
    bb_width_max = DecimalParameter(0.06, 0.15, default=0.10, space="buy", optimize=True)

    # Entry quality score threshold (0-100)
    min_entry_score = IntParameter(30, 80, default=40, space="buy", optimize=True)

    # Trailing stop activation (profit % to start trailing)
    trailing_profit_trigger = DecimalParameter(0.01, 0.05, default=0.02, space="sell", optimize=True)
    trailing_profit_offset = DecimalParameter(0.005, 0.03, default=0.01, space="sell", optimize=True)

    # Partial exit settings
    partial_exit_enabled = BooleanParameter(default=True, space="sell", optimize=True)
    partial_exit_ratio = DecimalParameter(0.2, 0.5, default=0.3, space="sell", optimize=True)

    # Risk management
    base_risk_per_trade = 0.03  # 3% base risk
    min_risk_per_trade = 0.01  # 1% minimum during drawdown
    atr_sl_multiplier = 6.0
    drawdown_risk_reduction = True  # Enable anti-martingale

    # Z-score threshold for mean reversion
    zscore_threshold = DecimalParameter(1.0, 2.5, default=1.5, space="buy", optimize=True)

    # Divergence lookback
    divergence_lookback = IntParameter(5, 15, default=10, space="indicator", optimize=True)

    # Custom storage for trade data
    custom_info: Dict[str, Any] = {}

    # Track recent performance for dynamic sizing
    recent_trades_count = 10
    
    # Run "populate_indicators" only for new candle
    process_only_new_candles = True

    # These values can be overridden in the config file
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    # Number of candles the strategy requires before producing valid signals
    startup_candle_count: int = 100

    # Optional order type mapping.
    order_types = {
        'entry': 'limit',
        'exit': 'limit',
        'stoploss': 'market',
        'stoploss_on_exchange': False
    }

    # Order time in force.
    order_time_in_force = {
        'entry': 'gtc',
        'exit': 'gtc'
    }

    def informative_pairs(self):
        pairs = self.dp.current_whitelist()
        return [(pair, self.informative_timeframe) for pair in pairs]

    def calculate_zscore(self, series: pd.Series, window: int = 20) -> pd.Series:
        """Calculate Z-score for mean reversion strength."""
        mean = series.rolling(window=window).mean()
        std = series.rolling(window=window).std()
        return (series - mean) / std

    def detect_bullish_divergence(self, price: pd.Series, indicator: pd.Series, lookback: int) -> pd.Series:
        """
        Detect bullish divergence: Price makes lower low, indicator makes higher low.
        This is a strong mean reversion signal.
        """
        price_ll = (price < price.rolling(lookback).min().shift(1))
        indicator_hl = (indicator > indicator.rolling(lookback).min().shift(1))
        return (price_ll & indicator_hl).astype(int)

    def detect_bearish_divergence(self, price: pd.Series, indicator: pd.Series, lookback: int) -> pd.Series:
        """
        Detect bearish divergence: Price makes higher high, indicator makes lower high.
        This is a strong mean reversion signal for shorts.
        """
        price_hh = (price > price.rolling(lookback).max().shift(1))
        indicator_lh = (indicator < indicator.rolling(lookback).max().shift(1))
        return (price_hh & indicator_lh).astype(int)

    def calculate_entry_score(self, dataframe: DataFrame, side: str) -> pd.Series:
        """
        Calculate entry quality score (0-100) based on multiple factors.
        Higher score = better quality entry.
        """
        score = pd.Series(0.0, index=dataframe.index)

        # 1. Z-score strength (0-25 points) - how far from mean
        zscore_abs = dataframe['zscore'].abs()
        score += np.clip(zscore_abs / 3 * 25, 0, 25)

        # 2. Volume confirmation (0-25 points)
        vol_ratio = dataframe['volume'] / dataframe['volume_sma']
        score += np.clip((vol_ratio - 1) * 25, 0, 25)

        # 3. BB width in sweet spot (0-20 points)
        bb_width = dataframe['bb_width']
        width_score = np.where(
            (bb_width >= self.bb_width_min.value) & (bb_width <= self.bb_width_max.value),
            20, 5
        )
        score += width_score

        # 4. Divergence bonus (0-20 points)
        if side == 'long':
            score += dataframe['bullish_divergence'] * 20
        else:
            score += dataframe['bearish_divergence'] * 20

        # 5. ADX strength bonus (0-10 points)
        score += np.clip((dataframe['adx'] - 20) / 3, 0, 10)

        return score

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # --- Current Timeframe Indicators ---

        # Bollinger Bands
        bollinger = ta.BBANDS(dataframe, timeperiod=self.bb_window.value, nbdevup=self.bb_std.value, nbdevdn=self.bb_std.value)
        dataframe['bb_upperband'] = bollinger['upperband']
        dataframe['bb_middleband'] = bollinger['middleband']
        dataframe['bb_lowerband'] = bollinger['lowerband']

        # BB Width as percentage of price (volatility regime indicator)
        dataframe['bb_width'] = (dataframe['bb_upperband'] - dataframe['bb_lowerband']) / dataframe['bb_middleband']

        # ADX (Current)
        dataframe['adx'] = ta.ADX(dataframe, timeperiod=self.adx_length.value)

        # ATR (Current) for Stoploss
        dataframe['atr'] = ta.ATR(dataframe, timeperiod=14)

        # RSI (Current) for divergence detection
        dataframe['rsi'] = ta.RSI(dataframe, timeperiod=self.rsi_length.value)

        # Volume SMA for volume filter
        dataframe['volume_sma'] = ta.SMA(dataframe['volume'], timeperiod=self.volume_sma_length.value)

        # Z-score of price relative to BB middle (mean reversion strength)
        dataframe['zscore'] = self.calculate_zscore(dataframe['close'], self.bb_window.value)

        # Divergence detection
        dataframe['bullish_divergence'] = self.detect_bullish_divergence(
            dataframe['close'], dataframe['rsi'], self.divergence_lookback.value
        )
        dataframe['bearish_divergence'] = self.detect_bearish_divergence(
            dataframe['close'], dataframe['rsi'], self.divergence_lookback.value
        )

        # Entry quality scores
        dataframe['entry_score_long'] = self.calculate_entry_score(dataframe, 'long')
        dataframe['entry_score_short'] = self.calculate_entry_score(dataframe, 'short')

        # --- Informative Timeframe Indicators (4h) ---
        informative = self.dp.get_pair_dataframe(pair=metadata['pair'], timeframe=self.informative_timeframe)

        # RSI (4h)
        informative['rsi'] = ta.RSI(informative, timeperiod=self.rsi_length.value)

        # ADX (4h)
        informative['adx'] = ta.ADX(informative, timeperiod=self.adx_length.value)

        # SuperTrend (4h)
        st = pta.supertrend(informative['high'], informative['low'], informative['close'], length=self.st_length.value, multiplier=self.st_multiplier.value)
        st_col = f"SUPERT_{self.st_length.value}_{self.st_multiplier.value}"
        st_dir_col = f"SUPERTd_{self.st_length.value}_{self.st_multiplier.value}"

        informative['supertrend'] = st[st_col]
        informative['supertrend_direction'] = st[st_dir_col]  # 1 = Up, -1 = Down

        # Merge informative into dataframe
        dataframe = merge_informative_pair(dataframe, informative, self.timeframe, self.informative_timeframe, ffill=True)

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:

        # Informative columns have suffix _{informative_timeframe}
        rsi_4h = f"rsi_{self.informative_timeframe}"
        adx_4h = f"adx_{self.informative_timeframe}"
        st_dir_4h = f"supertrend_direction_{self.informative_timeframe}"

        # Long Entry with enhanced filters
        dataframe.loc[
            (
                # Original conditions
                (dataframe[rsi_4h] > self.rsi_long_threshold.value) &
                (dataframe[st_dir_4h] == 1) &  # Uptrend
                (dataframe[adx_4h] > self.adx_strong_threshold.value) &
                (dataframe['adx'] > self.adx_weak_threshold.value) &
                (dataframe['close'] < dataframe['bb_lowerband']) &
                (dataframe['volume'] > 0) &
                # NEW: Volume spike confirmation
                (dataframe['volume'] > dataframe['volume_sma'] * self.volume_multiplier.value) &
                # NEW: Volatility regime filter
                (dataframe['bb_width'] >= self.bb_width_min.value) &
                (dataframe['bb_width'] <= self.bb_width_max.value) &
                # NEW: Z-score threshold for mean reversion strength
                (dataframe['zscore'] < -self.zscore_threshold.value) &
                # NEW: Entry quality score filter
                (dataframe['entry_score_long'] >= self.min_entry_score.value)
            ),
            'enter_long'] = 1

        # Short Entry with enhanced filters
        dataframe.loc[
            (
                # Original conditions
                (dataframe[rsi_4h] < self.rsi_short_threshold.value) &
                (dataframe[st_dir_4h] == -1) &  # Downtrend
                (dataframe[adx_4h] > self.adx_strong_threshold.value) &
                (dataframe['adx'] > self.adx_weak_threshold.value) &
                (dataframe['close'] > dataframe['bb_upperband']) &
                (dataframe['volume'] > 0) &
                # NEW: Volume spike confirmation
                (dataframe['volume'] > dataframe['volume_sma'] * self.volume_multiplier.value) &
                # NEW: Volatility regime filter
                (dataframe['bb_width'] >= self.bb_width_min.value) &
                (dataframe['bb_width'] <= self.bb_width_max.value) &
                # NEW: Z-score threshold for mean reversion strength
                (dataframe['zscore'] > self.zscore_threshold.value) &
                # NEW: Entry quality score filter
                (dataframe['entry_score_short'] >= self.min_entry_score.value)
            ),
            'enter_short'] = 1

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:

        # Long Exit: Close > Upper BB (full exit signal for remaining position)
        dataframe.loc[
            (dataframe['close'] > dataframe['bb_upperband']),
            'exit_long'] = 1

        # Short Exit: Close < Lower BB (full exit signal for remaining position)
        dataframe.loc[
            (dataframe['close'] < dataframe['bb_lowerband']),
            'exit_short'] = 1

        return dataframe

    def custom_exit(self, pair: str, trade: Trade, current_time: datetime,
                    current_rate: float, current_profit: float, **kwargs) -> Optional[str]:
        """
        Partial exit at middle BB for profit taking.
        Exit remaining at opposite BB.
        """
        if not self.partial_exit_enabled.value:
            return None

        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(dataframe) == 0:
            return None

        last_candle = dataframe.iloc[-1]
        trade_key = trade.open_date_utc.isoformat()

        # Initialize partial exit tracking
        if pair not in self.custom_info:
            self.custom_info[pair] = {}
        if 'partial_exits' not in self.custom_info[pair]:
            self.custom_info[pair]['partial_exits'] = {}

        # Check if we already did partial exit for this trade
        partial_done = self.custom_info[pair]['partial_exits'].get(trade_key, False)

        if not partial_done:
            is_short = trade.is_short if hasattr(trade, 'is_short') else (trade.trade_direction == 'short')
            
            # Long: Partial exit at middle BB
            if not is_short and current_rate >= last_candle['bb_middleband']:
                self.custom_info[pair]['partial_exits'][trade_key] = True
                return "partial_profit_middle_bb"

            # Short: Partial exit at middle BB
            if is_short and current_rate <= last_candle['bb_middleband']:
                self.custom_info[pair]['partial_exits'][trade_key] = True
                return "partial_profit_middle_bb"

        return None

    def adjust_trade_position(self, trade: Trade, current_time: datetime,
                              current_rate: float, current_profit: float,
                              min_stake: Optional[float], max_stake: float,
                              current_entry_rate: float, current_exit_rate: float,
                              current_entry_profit: float, current_exit_profit: float,
                              **kwargs) -> Optional[float]:
        """
        Reduce position by partial_exit_ratio at middle BB.
        Returns negative value to reduce position.
        """
        if not self.partial_exit_enabled.value:
            return None

        pair = trade.pair
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(dataframe) == 0:
            return None

        last_candle = dataframe.iloc[-1]
        trade_key = trade.open_date_utc.isoformat()

        # Initialize tracking
        if pair not in self.custom_info:
            self.custom_info[pair] = {}
        if 'position_adjusted' not in self.custom_info[pair]:
            self.custom_info[pair]['position_adjusted'] = {}

        # Check if already adjusted this trade
        if self.custom_info[pair]['position_adjusted'].get(trade_key, False):
            return None

        is_short = trade.is_short if hasattr(trade, 'is_short') else (trade.trade_direction == 'short')

        # Long: Reduce at middle BB
        if not is_short and current_rate >= last_candle['bb_middleband']:
            if current_profit > 0:  # Only if in profit
                self.custom_info[pair]['position_adjusted'][trade_key] = True
                # Return negative stake to reduce position
                reduction = -(trade.stake_amount * self.partial_exit_ratio.value)
                return reduction

        # Short: Reduce at middle BB
        if is_short and current_rate <= last_candle['bb_middleband']:
            if current_profit > 0:  # Only if in profit
                self.custom_info[pair]['position_adjusted'][trade_key] = True
                reduction = -(trade.stake_amount * self.partial_exit_ratio.value)
                return reduction

        return None

    def get_current_drawdown(self) -> float:
        """
        Calculate current drawdown from recent trades.
        Returns drawdown as positive percentage (e.g., 0.10 = 10% drawdown).
        """
        try:
            closed_trades = Trade.get_trades_proxy(is_open=False)
            if len(closed_trades) < 2:
                return 0.0

            # Get last N trades
            recent = closed_trades[-self.recent_trades_count:]
            profits = [t.close_profit or 0 for t in recent]

            # Calculate cumulative equity curve
            equity = [1.0]
            for p in profits:
                equity.append(equity[-1] * (1 + p))

            # Calculate max drawdown
            peak = equity[0]
            max_dd = 0.0
            for e in equity:
                if e > peak:
                    peak = e
                dd = (peak - e) / peak
                if dd > max_dd:
                    max_dd = dd

            return max_dd
        except Exception:
            return 0.0

    def get_dynamic_risk(self) -> float:
        """
        Adjust risk based on recent drawdown (anti-martingale).
        Reduce risk during drawdowns, increase during winning streaks.
        """
        if not self.drawdown_risk_reduction:
            return self.base_risk_per_trade

        current_dd = self.get_current_drawdown()

        # Linear reduction: at 20% DD, reduce risk to minimum
        if current_dd >= 0.20:
            return self.min_risk_per_trade

        # Scale risk linearly between base and min
        risk_reduction = (current_dd / 0.20) * (self.base_risk_per_trade - self.min_risk_per_trade)
        dynamic_risk = self.base_risk_per_trade - risk_reduction

        return max(dynamic_risk, self.min_risk_per_trade)

    def custom_stake_amount(self, pair: str, current_time: datetime, current_rate: float,
                            proposed_stake: float, min_stake: Optional[float], max_stake: float,
                            leverage: float, entry_tag: Optional[str], side: str,
                            **kwargs) -> float:

        # Calculate stake based on dynamic risk (anti-martingale)
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(dataframe) == 0:
            return proposed_stake

        last_candle = dataframe.iloc[-1]
        atr = last_candle['atr']

        if np.isnan(atr) or atr == 0:
            return proposed_stake

        # SL Distance
        sl_distance = self.atr_sl_multiplier * atr

        # SL Percentage
        sl_pct = sl_distance / current_rate

        # Total Capital
        total_capital = self.wallets.get_total(self.config['stake_currency'])

        # Dynamic risk based on drawdown
        risk_pct = self.get_dynamic_risk()

        # Risk Amount
        risk_amount = total_capital * risk_pct

        # Stake Amount = Risk Amount / SL Percentage
        if sl_pct == 0:
            return proposed_stake

        calculated_stake = risk_amount / sl_pct

        # Entry score bonus: Increase stake for high-quality entries
        entry_score = last_candle['entry_score_long'] if side == 'long' else last_candle['entry_score_short']
        if entry_score >= 80:  # High quality entry
            calculated_stake *= 1.25  # 25% bonus

        # Ensure stake is within limits
        if min_stake:
            calculated_stake = max(calculated_stake, min_stake)
        if max_stake:
            calculated_stake = min(calculated_stake, max_stake)

        return calculated_stake

    def confirm_trade_entry(self, pair: str, order_type: str, amount: float, rate: float,
                            time_in_force: str, current_time: datetime, entry_tag: Optional[str],
                            side: str, **kwargs) -> bool:

        # Store the ATR-based SL price for this trade
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(dataframe) > 0:
            last_candle = dataframe.iloc[-1]
            atr = last_candle['atr']

            sl_price = 0.0
            if side == "long":
                sl_price = rate - (self.atr_sl_multiplier * atr)
            else:
                sl_price = rate + (self.atr_sl_multiplier * atr)

            if pair not in self.custom_info:
                self.custom_info[pair] = {}

            self.custom_info[pair]['next_sl_price'] = sl_price

        return True

    def custom_stoploss(self, pair: str, trade: Trade, current_time: datetime,
                        current_rate: float, current_profit: float, **kwargs) -> float:

        # Retrieve stored SL price
        trade_key = trade.open_date_utc.isoformat()

        if pair in self.custom_info:
            if 'next_sl_price' in self.custom_info[pair]:
                if 'trades' not in self.custom_info[pair]:
                    self.custom_info[pair]['trades'] = {}

                if trade_key not in self.custom_info[pair]['trades']:
                    self.custom_info[pair]['trades'][trade_key] = {
                        'sl_price': self.custom_info[pair]['next_sl_price'],
                        'highest_profit': 0.0
                    }

        trade_data = None
        if pair in self.custom_info and 'trades' in self.custom_info[pair]:
            trade_data = self.custom_info[pair]['trades'].get(trade_key)

        if not trade_data:
            return self.stoploss

        sl_price = trade_data['sl_price'] if isinstance(trade_data, dict) else trade_data
        highest_profit = trade_data.get('highest_profit', 0.0) if isinstance(trade_data, dict) else 0.0

        # === TRAILING STOP LOGIC ===
        # Once we hit trailing_profit_trigger profit, start trailing
        if current_profit > self.trailing_profit_trigger.value:
            # Update highest profit
            if current_profit > highest_profit:
                if isinstance(trade_data, dict):
                    self.custom_info[pair]['trades'][trade_key]['highest_profit'] = current_profit

                # Calculate new trailing stop
                # Lock in profit minus the offset
                new_sl_profit = current_profit - self.trailing_profit_offset.value

                if new_sl_profit > 0:
                    is_short = trade.is_short if hasattr(trade, 'is_short') else (trade.trade_direction == 'short')
                    
                    # Convert profit-based SL to price-based SL
                    if not is_short:
                        new_sl_price = trade.open_rate * (1 + new_sl_profit)
                    else:
                        new_sl_price = trade.open_rate * (1 - new_sl_profit)

                    is_short = trade.is_short if hasattr(trade, 'is_short') else (trade.trade_direction == 'short')
                    
                    # Only move stop up, never down
                    if not is_short and new_sl_price > sl_price:
                        sl_price = new_sl_price
                        if isinstance(trade_data, dict):
                            self.custom_info[pair]['trades'][trade_key]['sl_price'] = sl_price
                    elif is_short and new_sl_price < sl_price:
                        sl_price = new_sl_price
                        if isinstance(trade_data, dict):
                            self.custom_info[pair]['trades'][trade_key]['sl_price'] = sl_price

        is_short = trade.is_short if hasattr(trade, 'is_short') else (trade.trade_direction == 'short')
        
        # Calculate percentage difference from current price
        if not is_short:
            pct = (sl_price - current_rate) / current_rate
        else:
            pct = (current_rate - sl_price) / current_rate

        return pct  # Returns negative when SL is below price (long) or above (short)

