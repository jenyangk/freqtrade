
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional, Any, Set

import numpy as np
import pandas as pd
import talib.abstract as ta
import freqtrade.vendor.qtpylib.indicators as qtpylib

from pandas import DataFrame, Series
from freqtrade.strategy import IStrategy, merge_informative_pair, IntParameter, DecimalParameter, stoploss_from_open
from freqtrade.exchange import timeframe_to_minutes
from freqtrade.persistence import Trade


class BBFVG_Sniper(IStrategy):
    """
    BBFVG Sniper Strategy
    Advanced version of BBFVG with:
    1. Volatility Breathing Exit (Dynamic R:R)
    2. Liquidity Sweep Filter (Volume/RSI)
    3. Regime Filtering (ADX)
    4. Tiered Entry (Front-run + Sniper)
    """

    # --- Mandatory metadata ---
    timeframe = '1h'
    
    # Configurable timeframes
    fvg_timeframe = '4h'

    startup_candle_count = 100

    can_short: bool = True
    use_custom_stoploss = True

    # --- Strategy Parameters ---
    # Bollinger Bands
    bb_window = IntParameter(10, 50, default=20, space='indicator')
    bb_std = DecimalParameter(1.5, 3.0, default=2.0, decimals=1, space='indicator')
    
    # EMAs
    ema_fast_period = IntParameter(10, 30, default=21, space='indicator')
    ema_slow_period = IntParameter(30, 60, default=38, space='indicator')

    # FVG
    fvg_threshold = DecimalParameter(0.0, 0.01, default=0.001, decimals=4, space='indicator')
    fvg_extend = IntParameter(10, 100, default=40, space='indicator')

    # Regime Filter (ADX)
    adx_threshold = IntParameter(20, 40, default=25, space='indicator')

    # Liquidity Sweep
    rsi_period = IntParameter(10, 20, default=14, space='indicator')
    rsi_oversold = IntParameter(20, 40, default=30, space='indicator')
    rsi_overbought = IntParameter(60, 80, default=70, space='indicator')
    vol_ma_period = IntParameter(10, 30, default=20, space='indicator')

    # Risk Management
    risk_reward = DecimalParameter(1.5, 4.0, default=2.0, decimals=1, space='sell')
    
    # Position Adjustment
    position_adjustment_enable = True
    max_entry_position_adjustment = 1  # Only 1 add-on allowed (Front-run -> Sniper)

    # Default Stoploss
    stoploss = -0.10 

    # ROI
    minimal_roi = {
        "0": 100
    }

    process_only_new_candles = True

    # Custom info store
    custom_info: Dict[str, Dict[str, Any]] = {}

    plot_config = {
        'main_plot': {
            'bb_upperband': {'color': 'rgba(0, 0, 255, 0.5)'},
            'bb_lowerband': {'color': 'rgba(0, 0, 255, 0.5)'},
            'ema_fast': {'color': 'orange'},
            'ema_slow': {'color': 'yellow'},
            'fvg_bull_top': {
                'color': 'rgba(0, 255, 0, 0.5)',
                'fill_to': 'fvg_bull_bottom',
                'fill_color': 'rgba(0, 255, 0, 0.2)',
                'fill_label': 'Bull FVG'
            },
            'fvg_bull_bottom': {'color': 'rgba(0, 255, 0, 0.5)'},
            'fvg_bear_top': {
                'color': 'rgba(255, 0, 0, 0.5)',
                'fill_to': 'fvg_bear_bottom',
                'fill_color': 'rgba(255, 0, 0, 0.2)',
                'fill_label': 'Bear FVG'
            },
            'fvg_bear_bottom': {'color': 'rgba(255, 0, 0, 0.5)'},
        },
        'subplots': {
            'Signals': {
                'enter_long': {'color': 'green', 'type': 'scatter'},
                'enter_short': {'color': 'red', 'type': 'scatter'},
            },
            'Regime': {
                'adx': {'color': 'purple'},
                'bb_width_slope': {'color': 'blue'}
            }
        }
    }

    def informative_pairs(self):
        wl = self.dp.current_whitelist()
        return [(p, self.fvg_timeframe) for p in wl]

    @staticmethod
    def _detect_fvgs(df: DataFrame, threshold: float, extend: int) -> DataFrame:
        """Detect Fair Value Gaps and mark active zones"""
        df = df.copy()
        
        # Detect Bullish FVG
        df['bull_fvg'] = (
            (df['low'] > df['high'].shift(2)) &
            (df['close'].shift(1) > df['high'].shift(2)) &
            ((df['low'] - df['high'].shift(2)) / df['high'].shift(2) > threshold)
        )
        
        # Detect Bearish FVG
        df['bear_fvg'] = (
            (df['high'] < df['low'].shift(2)) &
            (df['close'].shift(1) < df['low'].shift(2)) &
            ((df['low'].shift(2) - df['high']) / df['high'] > threshold)
        )
        
        # Track active FVGs
        active_bull_fvgs = []
        active_bear_fvgs = []
        
        df['fvg_bull_top'] = np.nan
        df['fvg_bull_bottom'] = np.nan
        df['fvg_bear_top'] = np.nan
        df['fvg_bear_bottom'] = np.nan

        for i in range(len(df)):
            current_close = df['close'].iloc[i]
            
            if df['bull_fvg'].iloc[i]:
                fvg_max = df['low'].iloc[i]
                fvg_min = df['high'].iloc[i-2] if i >= 2 else df['low'].iloc[i]
                active_bull_fvgs.append({'max': fvg_max, 'min': fvg_min, 'start_idx': i})
            
            if df['bear_fvg'].iloc[i]:
                fvg_max = df['low'].iloc[i-2] if i >= 2 else df['high'].iloc[i]
                fvg_min = df['high'].iloc[i]
                active_bear_fvgs.append({'max': fvg_max, 'min': fvg_min, 'start_idx': i})
            
            active_bull_fvgs = [f for f in active_bull_fvgs if (i - f['start_idx']) < extend and current_close >= f['min']]
            active_bear_fvgs = [f for f in active_bear_fvgs if (i - f['start_idx']) < extend and current_close <= f['max']]
            
            if active_bull_fvgs:
                df.at[df.index[i], 'fvg_bull_top'] = active_bull_fvgs[-1]['max']
                df.at[df.index[i], 'fvg_bull_bottom'] = active_bull_fvgs[-1]['min']
            
            if active_bear_fvgs:
                df.at[df.index[i], 'fvg_bear_top'] = active_bear_fvgs[-1]['max']
                df.at[df.index[i], 'fvg_bear_bottom'] = active_bear_fvgs[-1]['min']
        
        return df

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        if not self.dp:
            return dataframe

        pair = metadata['pair']
        if pair not in self.custom_info:
            self.custom_info[pair] = {'trade_data': {}}

        # --- 1H Indicators ---
        # Bollinger Bands
        bollinger = qtpylib.bollinger_bands(qtpylib.typical_price(dataframe), window=self.bb_window.value, stds=self.bb_std.value)
        dataframe['bb_upperband'] = bollinger['upper']
        dataframe['bb_mid'] = bollinger['mid']
        dataframe['bb_lowerband'] = bollinger['lower']
        
        # Bandwidth & Slope (Volatility Breathing)
        dataframe['bb_width'] = (dataframe['bb_upperband'] - dataframe['bb_lowerband']) / dataframe['bb_mid']
        dataframe['bb_width_slope'] = dataframe['bb_width'].diff()

        # EMAs
        dataframe['ema_fast'] = ta.EMA(dataframe, timeperiod=int(self.ema_fast_period.value))
        dataframe['ema_slow'] = ta.EMA(dataframe, timeperiod=int(self.ema_slow_period.value))

        # ADX (Regime Filter)
        dataframe['adx'] = ta.ADX(dataframe, timeperiod=14)

        # RSI & Volume (Liquidity Sweep)
        dataframe['rsi'] = ta.RSI(dataframe, timeperiod=int(self.rsi_period.value))
        dataframe['volume_ma'] = ta.SMA(dataframe['volume'], timeperiod=int(self.vol_ma_period.value))

        # --- 4H Indicators ---
        inf_fvg = self.dp.get_pair_dataframe(pair=pair, timeframe=self.fvg_timeframe)
        inf_fvg = self._detect_fvgs(inf_fvg, float(self.fvg_threshold.value), int(self.fvg_extend.value))
        dataframe = merge_informative_pair(dataframe, inf_fvg, self.timeframe, self.fvg_timeframe, ffill=True)

        # Map merged columns
        dataframe['fvg_bull_top'] = dataframe[f'fvg_bull_top_{self.fvg_timeframe}']
        dataframe['fvg_bull_bottom'] = dataframe[f'fvg_bull_bottom_{self.fvg_timeframe}']
        dataframe['fvg_bear_top'] = dataframe[f'fvg_bear_top_{self.fvg_timeframe}']
        dataframe['fvg_bear_bottom'] = dataframe[f'fvg_bear_bottom_{self.fvg_timeframe}']

        # FVG Presence
        dataframe['in_bull_fvg'] = ((dataframe['fvg_bull_top'].notna()) & (dataframe['low'] <= dataframe['fvg_bull_top']) & (dataframe['high'] >= dataframe['fvg_bull_bottom'])).astype(int)
        dataframe['in_bear_fvg'] = ((dataframe['fvg_bear_top'].notna()) & (dataframe['high'] >= dataframe['fvg_bear_bottom']) & (dataframe['low'] <= dataframe['fvg_bear_top'])).astype(int)

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        
        # Common Filters
        # 1. Trend Filter (EMA)
        # 2. Regime Filter (ADX > Threshold)
        # 3. Liquidity Sweep (Volume < MA OR RSI Extreme)
        
        long_trend = dataframe['ema_fast'] > dataframe['ema_slow']
        short_trend = dataframe['ema_fast'] < dataframe['ema_slow']
        
        regime_filter = dataframe['adx'] > self.adx_threshold.value
        
        long_liquidity = (dataframe['volume'] < dataframe['volume_ma']) | (dataframe['rsi'] < self.rsi_oversold.value)
        short_liquidity = (dataframe['volume'] < dataframe['volume_ma']) | (dataframe['rsi'] > self.rsi_overbought.value)

        # --- Long Signals ---
        # Signal 1: Front-run (Touch FVG Top)
        long_frontrun = (
            long_trend & regime_filter & long_liquidity &
            (dataframe['fvg_bull_top'].notna()) &
            (dataframe['low'] <= dataframe['fvg_bull_top']) &
            (dataframe['close'] > dataframe['fvg_bull_bottom']) # Don't buy if already smashed through
        )
        
        # Signal 2: Sniper (Touch Lower Band + Inside FVG)
        long_sniper = (
            long_trend & regime_filter & long_liquidity &
            (dataframe['in_bull_fvg'] == 1) &
            (dataframe['low'] <= dataframe['bb_lowerband'])
        )

        dataframe.loc[long_frontrun, ['enter_long', 'enter_tag']] = (1, 'long_frontrun')
        dataframe.loc[long_sniper, ['enter_long', 'enter_tag']] = (1, 'long_sniper') # Sniper overwrites Frontrun if both true

        # --- Short Signals ---
        # Signal 1: Front-run (Touch FVG Bottom/Top? Bearish FVG is Top-Bottom. Entry at Bottom of Bear FVG?)
        # Bearish FVG: Top is Resistance. Bottom is where price enters from below.
        # Wait, Bearish FVG: Price drops, leaving gap. Retracement comes UP into the gap.
        # So "Front-run" means touching the BOTTOM of the Bearish FVG (first contact).
        # "Sniper" means touching Upper Band inside FVG.
        
        short_frontrun = (
            short_trend & regime_filter & short_liquidity &
            (dataframe['fvg_bear_bottom'].notna()) &
            (dataframe['high'] >= dataframe['fvg_bear_bottom']) &
            (dataframe['close'] < dataframe['fvg_bear_top'])
        )
        
        short_sniper = (
            short_trend & regime_filter & short_liquidity &
            (dataframe['in_bear_fvg'] == 1) &
            (dataframe['high'] >= dataframe['bb_upperband'])
        )

        dataframe.loc[short_frontrun, ['enter_short', 'enter_tag']] = (1, 'short_frontrun')
        dataframe.loc[short_sniper, ['enter_short', 'enter_tag']] = (1, 'short_sniper')

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # Trend Reversal Exit (Kill Switch)
        dataframe.loc[(dataframe['ema_fast'] < dataframe['ema_slow']), ['exit_long', 'exit_tag']] = (1, 'long_exit_trend_reversal')
        dataframe.loc[(dataframe['ema_fast'] > dataframe['ema_slow']), ['exit_short', 'exit_tag']] = (1, 'short_exit_trend_reversal')
        return dataframe

    def custom_stake_amount(self, pair: str, current_time: datetime, current_rate: float,
                            proposed_stake: float, min_stake: float, max_stake: float,
                            leverage: float, entry_tag: Optional[str], side: str,
                            **kwargs) -> float:
        
        # Tiered Entry:
        # Front-run: 0.5x Stake
        # Sniper: 1.0x Stake (Full)
        
        if entry_tag == 'long_frontrun' or entry_tag == 'short_frontrun':
            return proposed_stake * 0.5
        
        return proposed_stake

    def custom_stoploss(self, pair: str, trade: Trade, current_time: datetime, current_rate: float,
                        current_profit: float, **kwargs) -> float:
        
        # 1. Volatility Breathing (Trailing Stop)
        # If Bandwidth is increasing, trail EMA 21
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        last_candle = dataframe.iloc[-1]
        
        if last_candle['bb_width_slope'] > 0:
            # Volatility Expanding - Trail EMA 21
            ema21 = last_candle['ema_fast']
            
            if trade.is_short:
                # Short: Stop is above price. EMA should be above price?
                # In strong downtrend, EMA21 is above price.
                # Stop price = EMA21.
                if ema21 < current_rate: 
                    # EMA is below current price (weird for downtrend stop), ignore or use hard stop
                    pass
                else:
                    # Calculate percentage diff
                    return (ema21 - current_rate) / current_rate
            else:
                # Long: Stop is below price.
                if ema21 > current_rate:
                    pass
                else:
                    return (ema21 - current_rate) / current_rate

        # 2. Hard Stop (Structure Based)
        trade_key = trade.open_date_utc.isoformat()
        if pair in self.custom_info and 'trade_data' in self.custom_info[pair]:
            trade_data = self.custom_info[pair]['trade_data'].get(trade_key)
            if trade_data and 'sl_price' in trade_data:
                sl_price = trade_data['sl_price']
                if trade.is_short:
                    return (sl_price - current_rate) / current_rate
                else:
                    return (sl_price - current_rate) / current_rate
        
        return self.stoploss

    def custom_exit(self, pair: str, trade: Trade, current_time: datetime, current_rate: float,
                    current_profit: float, **kwargs):
        
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        last_candle = dataframe.iloc[-1]

        # Volatility Breathing: If expanding, disable fixed targets
        if last_candle['bb_width_slope'] > 0:
            return None
            
        # Standard Exit: 2:1 R:R or Opposite Band
        trade_key = trade.open_date_utc.isoformat()
        if pair in self.custom_info and 'trade_data' in self.custom_info[pair]:
            trade_data = self.custom_info[pair]['trade_data'].get(trade_key)
            if trade_data and 'risk' in trade_data:
                risk_pct = trade_data['risk']
                target_profit = risk_pct * float(self.risk_reward.value)
                if current_profit >= target_profit:
                    return f"roi_2_1_target ({target_profit:.2%})"
        
        # Opposite Band Exit (if not expanding)
        if trade.is_short:
            if last_candle['low'] <= last_candle['bb_lowerband']:
                return "short_exit_bb_lower"
        else:
            if last_candle['high'] >= last_candle['bb_upperband']:
                return "long_exit_bb_upper"
        
        return None

    def confirm_trade_entry(self, pair: str, order_type: str, amount: float, rate: float,
                            time_in_force: str, current_time: datetime, entry_tag: Optional[str] = None, **kwargs):
        
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(dataframe) > 0:
            last_candle = dataframe.iloc[-1]
            sl_price = None
            
            # Determine Stop Price based on FVG
            if 'long' in str(entry_tag):
                fvg_bottom = last_candle.get('fvg_bull_bottom')
                if pd.notna(fvg_bottom):
                    sl_price = fvg_bottom * 0.995
                else:
                    sl_price = last_candle['bb_lowerband'] * 0.99
            elif 'short' in str(entry_tag):
                fvg_top = last_candle.get('fvg_bear_top')
                if pd.notna(fvg_top):
                    sl_price = fvg_top * 1.005
                else:
                    sl_price = last_candle['bb_upperband'] * 1.01
            
            if sl_price:
                risk = abs(rate - sl_price) / rate
                if pair not in self.custom_info:
                    self.custom_info[pair] = {'trade_data': {}}
                
                self.custom_info[pair]['pending_entry'] = {
                    'sl_price': sl_price,
                    'risk': risk,
                    'entry_time': current_time
                }
        return True

    def adjust_trade_position(self, trade: Trade, current_time: datetime, current_rate: float,
                              current_profit: float, min_stake: Optional[float],
                              max_stake: float, current_entry_rate: float, current_exit_rate: float,
                              current_entry_profit: float, current_exit_profit: float, **kwargs):
        
        pair = trade.pair
        trade_key = trade.open_date_utc.isoformat()
        
        # 1. Transfer pending
        if pair in self.custom_info and 'pending_entry' in self.custom_info[pair]:
            pending = self.custom_info[pair]['pending_entry']
            if pending['entry_time'] >= current_time - timedelta(minutes=timeframe_to_minutes(self.timeframe)*2):
                if 'trade_data' not in self.custom_info[pair]:
                    self.custom_info[pair]['trade_data'] = {}
                self.custom_info[pair]['trade_data'][trade_key] = pending
                del self.custom_info[pair]['pending_entry']

        # 2. Tiered Entry Logic (Front-run -> Sniper)
        if self.position_adjustment_enable:
            # Only add if we have 1 entry (the Front-run)
            if trade.nr_of_successful_entries == 1:
                # Check if the INITIAL entry was a front-run
                if 'frontrun' in str(trade.enter_tag):
                    dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
                    last_candle = dataframe.iloc[-1]
                    
                    # Check if Sniper condition is met now
                    if trade.is_short:
                        if last_candle['enter_short'] == 1 and 'sniper' in str(last_candle.get('enter_tag', '')):
                            # We entered on Front-run (0.5x), now Sniper triggers.
                            # We want to add 1.0x stake.
                            # trade.stake_amount is currently 0.5x.
                            # So we add trade.stake_amount * 2.0
                            return trade.stake_amount * 2.0
                    else:
                        if last_candle['enter_long'] == 1 and 'sniper' in str(last_candle.get('enter_tag', '')):
                            return trade.stake_amount * 2.0
        
        return None
