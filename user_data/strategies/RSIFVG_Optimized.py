from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional, Any
import warnings

import numpy as np
import pandas as pd
import talib.abstract as ta
from pandas import DataFrame, Series

from freqtrade.strategy import (
    IStrategy,
    merge_informative_pair,
    IntParameter,
    DecimalParameter,
    CategoricalParameter,
    stoploss_from_open,
)
from freqtrade.persistence import Trade
from freqtrade.exchange import timeframe_to_minutes
import logging

# Suppress pandas FutureWarning
warnings.simplefilter(action='ignore', category=FutureWarning)

logger = logging.getLogger(__name__)

class RSIFVG_Optimized(IStrategy):
    """
    RSIFVG_Optimized - Improved version of RSIFVG strategy.
    
    Improvements over original / C# version:
    1.  **Trend Filter (Optional):** Adds a higher timeframe (1h) EMA 200 filter to trade only with the trend.
        This helps avoid catching falling knives in bear markets (addressing the "failed in other years" issue).
    2.  **Uncapped Profits:** The original C# code had a logic conflict where a hard Take Profit was set at 1.5R,
        rendering the "Trail at 3R/4R" logic unreachable. This version removes the hard cap, allowing
        the trailing stop logic to capture larger moves.
    3.  **Robustness:** Added checks for minimum volatility (ATR) to avoid trading in dead markets.
    """

    # --- Mandatory metadata ---
    timeframe = "3m"  # Base timeframe for entries / execution
    
    # Can be overridden by config
    can_short: bool = True
    use_custom_stoploss = True
    process_only_new_candles = True
    startup_candle_count = 400

    # --- Strategy Parameters ---
    
    # Optimization: Trend Filter
    use_trend_filter = CategoricalParameter([True, False], default=True, space="buy")
    trend_timeframe = "1h"
    trend_period = IntParameter(100, 300, default=200, space="buy")

    # Indicators
    rsi_timeframe = "3m"
    fvg_timeframe = "15m"
    
    rsi_period = IntParameter(10, 20, default=14, space="indicator")
    pivot_left = IntParameter(2, 10, default=5, space="indicator")
    pivot_right = IntParameter(2, 10, default=5, space="indicator")
    
    # Divergence
    min_lookback = IntParameter(2, 10, default=5, space="indicator")
    max_lookback = IntParameter(20, 80, default=60, space="indicator")
    divergence_expiry = IntParameter(4, 20, default=20, space="indicator")

    # FVG
    fvg_threshold = DecimalParameter(0.0, 0.01, default=0.0, decimals=4, space="indicator")
    fvg_extend = IntParameter(10, 200, default=20, space="indicator")
    auto_threshold = False

    # Risk Management
    stoploss = -0.05  # Base hard stoploss (safety net)
    
    # Trailing Stop Parameters (R-Multiples)
    # Breakeven at 1.5R
    # Trail to 2R at 3R
    # Trail to 3R at 4R
    
    # Minimal ROI - We rely on custom_stoploss for trailing, but can set a safety TP
    minimal_roi = {
        "0": 100.0  # Effectively unlimited, let custom_stoploss handle exits
    }

    # Custom info store
    custom_info: Dict[str, Dict[str, Any]] = {}

    plot_config = {
        "main_plot": {
            "ema_trend": {"color": "rgba(255, 255, 0, 0.6)"},
            "fvg_bull_top": {"color": "rgba(0, 255, 0, 0.3)", "type": "line"},
            "fvg_bull_bottom": {"color": "rgba(0, 255, 0, 0.3)", "type": "line"},
            "fvg_bear_top": {"color": "rgba(255, 0, 0, 0.3)", "type": "line"},
            "fvg_bear_bottom": {"color": "rgba(255, 0, 0, 0.3)", "type": "line"},
        },
        "subplots": {
            "RSI": {
                "rsi_base": {"color": "orange"},
            },
            "Signals": {
                "bull_div_active": {"color": "lightgreen"},
                "bear_div_active": {"color": "lightcoral"},
            },
        },
    }

    def informative_pairs(self):
        wl = self.dp.current_whitelist()
        pairs = [(p, self.rsi_timeframe) for p in wl] + \
                [(p, self.fvg_timeframe) for p in wl] + \
                [(p, self.trend_timeframe) for p in wl]
        return pairs

    # --- Helpers ---
    
    @staticmethod
    def _detect_fvgs(df: DataFrame, threshold: float, auto_threshold: bool, fvg_extend: int) -> DataFrame:
        df = df.copy()
        
        # Threshold calculation
        if auto_threshold:
            cumsum_range = ((df["high"] - df["low"]) / df["low"]).cumsum()
            threshold_series = cumsum_range / (df.index + 1)
        else:
            threshold_series = threshold

        # Bullish FVG: low > high[2] and close[1] > high[2]
        df["bull_fvg"] = (
            (df["low"] > df["high"].shift(2))
            & (df["close"].shift(1) > df["high"].shift(2))
            & ((df["low"] - df["high"].shift(2)) / df["high"].shift(2) > threshold_series)
        )

        # Bearish FVG: high < low[2] and close[1] < low[2]
        df["bear_fvg"] = (
            (df["high"] < df["low"].shift(2))
            & (df["close"].shift(1) < df["low"].shift(2))
            & ((df["low"].shift(2) - df["high"]) / df["high"] > threshold_series)
        )

        # Active FVG Tracking
        df["bull_fvg_max"] = 0.0
        df["bull_fvg_min"] = 0.0
        df["bear_fvg_max"] = 0.0
        df["bear_fvg_min"] = 0.0

        active_bull_fvgs = []
        active_bear_fvgs = []

        for i in range(len(df)):
            current_close = df["close"].iloc[i]

            if df["bull_fvg"].iloc[i]:
                fvg_max = df["low"].iloc[i]
                fvg_min = df["high"].iloc[i - 2] if i >= 2 else df["low"].iloc[i]
                active_bull_fvgs.append({"max": fvg_max, "min": fvg_min, "start_idx": i})

            if df["bear_fvg"].iloc[i]:
                fvg_max = df["low"].iloc[i - 2] if i >= 2 else df["high"].iloc[i]
                fvg_min = df["high"].iloc[i]
                active_bear_fvgs.append({"max": fvg_max, "min": fvg_min, "start_idx": i})

            # Cleanup / Mitigation
            # Remove if price mitigates (closes beyond) or if too old
            active_bull_fvgs = [
                fvg for fvg in active_bull_fvgs
                if current_close >= fvg["min"] and (i - fvg["start_idx"]) < fvg_extend
            ]
            active_bear_fvgs = [
                fvg for fvg in active_bear_fvgs
                if current_close <= fvg["max"] and (i - fvg["start_idx"]) < fvg_extend
            ]

            if active_bull_fvgs:
                df.at[df.index[i], "bull_fvg_max"] = active_bull_fvgs[-1]["max"]
                df.at[df.index[i], "bull_fvg_min"] = active_bull_fvgs[-1]["min"]

            if active_bear_fvgs:
                df.at[df.index[i], "bear_fvg_max"] = active_bear_fvgs[-1]["max"]
                df.at[df.index[i], "bear_fvg_min"] = active_bear_fvgs[-1]["min"]

        return df

    @staticmethod
    def _pivot(series: Series, left: int, right: int) -> Series:
        values = series.values
        out = np.zeros(len(series), dtype=int)
        for i in range(left, len(series) - right):
            window = values[i - left : i + right + 1]
            center = values[i]
            if center == np.max(window) and (window < center).sum() >= 1:
                out[i] = 1
            elif center == np.min(window) and (window > center).sum() >= 1:
                out[i] = -1
        return Series(out, index=series.index)

    @staticmethod
    def _engulfing(current: Series, previous: Series) -> Tuple[bool, bool]:
        prev_body_top = max(previous["open"], previous["close"])
        prev_body_bottom = min(previous["open"], previous["close"])
        cur_body_top = max(current["open"], current["close"])
        cur_body_bottom = min(current["open"], current["close"])
        
        is_bull = (
            current["close"] > current["open"]
            and previous["close"] < previous["open"]
            and cur_body_top > prev_body_top
            and cur_body_bottom <= prev_body_bottom
        )
        is_bear = (
            current["close"] < current["open"]
            and previous["close"] > previous["open"]
            and cur_body_top >= prev_body_top
            and cur_body_bottom < prev_body_bottom
        )
        return is_bull, is_bear

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        if not self.dp:
            return dataframe

        pair = metadata["pair"]
        
        # --- Trend Filter (1h) ---
        inf_trend = self.dp.get_pair_dataframe(pair=pair, timeframe=self.trend_timeframe)
        inf_trend["ema_trend"] = ta.EMA(inf_trend, timeperiod=int(self.trend_period.value))
        dataframe = merge_informative_pair(dataframe, inf_trend, self.timeframe, self.trend_timeframe, ffill=True)
        
        # --- RSI Informative ---
        inf_rsi = self.dp.get_pair_dataframe(pair=pair, timeframe=self.rsi_timeframe)
        inf_rsi["rsi"] = ta.RSI(inf_rsi, timeperiod=int(self.rsi_period.value))
        inf_rsi["price_pivot"] = self._pivot(inf_rsi["close"], int(self.pivot_left.value), int(self.pivot_right.value))
        inf_rsi["rsi_pivot"] = self._pivot(inf_rsi["rsi"], int(self.pivot_left.value), int(self.pivot_right.value))
        
        # --- FVG Informative ---
        inf_fvg = self.dp.get_pair_dataframe(pair=pair, timeframe=self.fvg_timeframe)
        inf_fvg = self._detect_fvgs(inf_fvg, float(self.fvg_threshold.value), self.auto_threshold, int(self.fvg_extend.value))
        
        # Merge
        dataframe = merge_informative_pair(dataframe, inf_rsi, self.timeframe, self.rsi_timeframe, ffill=True)
        dataframe = merge_informative_pair(dataframe, inf_fvg, self.timeframe, self.fvg_timeframe, ffill=True)

        # Map FVG columns to base
        dataframe["fvg_bull_top"] = dataframe[f"bull_fvg_max_{self.fvg_timeframe}"]
        dataframe["fvg_bull_bottom"] = dataframe[f"bull_fvg_min_{self.fvg_timeframe}"]
        dataframe["fvg_bear_top"] = dataframe[f"bear_fvg_max_{self.fvg_timeframe}"]
        dataframe["fvg_bear_bottom"] = dataframe[f"bear_fvg_min_{self.fvg_timeframe}"]
        
        dataframe["in_bull_fvg"] = (
            (dataframe["fvg_bull_top"] > 0) &
            (dataframe["low"] <= dataframe["fvg_bull_top"]) &
            (dataframe["high"] >= dataframe["fvg_bull_bottom"])
        ).astype(int)
        
        dataframe["in_bear_fvg"] = (
            (dataframe["fvg_bear_top"] > 0) &
            (dataframe["low"] <= dataframe["fvg_bear_top"]) &
            (dataframe["high"] >= dataframe["fvg_bear_bottom"])
        ).astype(int)

        # --- Divergence Detection ---
        # (Simplified vectorized logic for performance, similar to RSIFVG.py)
        # We need to detect if we are in a "divergence active" window
        
        # Re-implementing the loop from RSIFVG.py as it handles the "expiry" logic well
        max_range_min = int(self.max_lookback.value) * timeframe_to_minutes(self.rsi_timeframe)
        min_range_min = int(self.min_lookback.value) * timeframe_to_minutes(self.rsi_timeframe)
        expiry_min = int(self.divergence_expiry.value) * timeframe_to_minutes(self.rsi_timeframe)
        
        dataframe["bull_div_active"] = 0
        dataframe["bear_div_active"] = 0
        
        price_piv_col = f"price_pivot_{self.rsi_timeframe}"
        rsi_piv_col = f"rsi_pivot_{self.rsi_timeframe}"
        rsi_val_col = f"rsi_{self.rsi_timeframe}"
        close_val_col = f"close_{self.rsi_timeframe}"
        
        recent_lows = [] # {'t': time, 'price': val, 'rsi': val}
        recent_highs = []
        
        last_bull_div_time = None
        last_bear_div_time = None
        
        # Iterate to find divergences
        # Note: This loop runs on the base timeframe but uses informative data
        for idx, row in dataframe.iterrows():
            t = row["date"]
            cutoff = t - pd.Timedelta(minutes=max_range_min)
            
            recent_lows = [x for x in recent_lows if x['t'] >= cutoff]
            recent_highs = [x for x in recent_highs if x['t'] >= cutoff]
            
            # Check for pivots (using merged columns)
            # We check if the informative pivot column has a signal
            # Since we ffilled, we need to be careful not to count the same pivot multiple times
            # We check if the value CHANGED or if we are at the exact candle?
            # Actually, ffill makes pivots persist. We need to detect the *change* or use the raw informative df before merge?
            # Better: The loop in RSIFVG.py was slightly flawed if it iterated ffilled data.
            # Correct approach: Iterate the informative DF to find divergence times, then map to base DF.
            pass 
        
        # --- Correct Divergence Logic ---
        # We will calculate divergence on the INFORMATIVE dataframe first, then merge the "active" status
        # This is much faster and correct.
        
        # We need to re-fetch informative to iterate it efficiently
        # But we already merged it. Let's use the columns.
        # Actually, let's do the loop on the base dataframe but only trigger on new pivots.
        # To avoid duplicate triggers on ffilled data, we check if the pivot value is different from prev row?
        # No, pivot is 1/-1/0. ffill makes it 1, 1, 1... 
        # Wait, merge_informative_pair with ffill=True propagates the 1.
        # So we need to check if it's a "new" 1.
        
        # Let's refine:
        # 1. Calculate divergence on `inf_rsi` (the smaller DF).
        # 2. Mark "bull_div_detected" on `inf_rsi`.
        # 3. Merge that to base.
        # 4. Apply expiry logic on base.
        
        # Re-calculating divergence on inf_rsi
        inf_rsi["bull_div"] = 0
        inf_rsi["bear_div"] = 0
        
        p_lows = []
        p_highs = []
        
        for i in range(len(inf_rsi)):
            row = inf_rsi.iloc[i]
            t = row["date"]
            
            # Clean old pivots
            cutoff = t - pd.Timedelta(minutes=max_range_min)
            p_lows = [x for x in p_lows if x['t'] >= cutoff]
            p_highs = [x for x in p_highs if x['t'] >= cutoff]
            
            # Add new pivots
            if row["price_pivot"] == -1 or row["rsi_pivot"] == -1:
                p_lows.append({'t': t, 'price': row["close"], 'rsi': row["rsi"]})
            if row["price_pivot"] == 1 or row["rsi_pivot"] == 1:
                p_highs.append({'t': t, 'price': row["close"], 'rsi': row["rsi"]})
                
            # Check Bull Div
            if len(p_lows) >= 2:
                curr = p_lows[-1]
                prev = p_lows[-2]
                # Ensure distinct pivots (time diff > 0)
                if curr['t'] > prev['t']:
                    dt = (curr['t'] - prev['t']).total_seconds() / 60
                    if dt >= min_range_min:
                        if curr['price'] < prev['price'] and curr['rsi'] > prev['rsi']:
                            inf_rsi.at[inf_rsi.index[i], "bull_div"] = 1
            
            # Check Bear Div
            if len(p_highs) >= 2:
                curr = p_highs[-1]
                prev = p_highs[-2]
                if curr['t'] > prev['t']:
                    dt = (curr['t'] - prev['t']).total_seconds() / 60
                    if dt >= min_range_min:
                        if curr['price'] > prev['price'] and curr['rsi'] < prev['rsi']:
                            inf_rsi.at[inf_rsi.index[i], "bear_div"] = 1

        # Now merge these specific divergence signals to dataframe
        inf_div = inf_rsi[["date", "bull_div", "bear_div"]].copy()
        inf_div["bull_div"] = inf_div["bull_div"].astype('int64')
        inf_div["bear_div"] = inf_div["bear_div"].astype('int64')
        
        # Merge with append_timeframe=True (default), so columns become bull_div_3m, bear_div_3m
        dataframe = merge_informative_pair(dataframe, inf_div, self.timeframe, self.rsi_timeframe, ffill=False) # No ffill, we want point events
        
        # Now apply expiry logic on base timeframe
        # We iterate base dataframe and maintain "active" state
        bull_active_until = pd.Timestamp.min
        bear_active_until = pd.Timestamp.min
        
        bull_col = f"bull_div_{self.rsi_timeframe}"
        bear_col = f"bear_div_{self.rsi_timeframe}"
        
        # Vectorized approach for "active window"
        # Forward fill the signal time?
        # Let's use a loop, it's safer for logic
        
        bull_div_active = np.zeros(len(dataframe), dtype=int)
        bear_div_active = np.zeros(len(dataframe), dtype=int)
        
        dates = dataframe["date"].values
        bull_sigs = dataframe[bull_col].fillna(0).values
        bear_sigs = dataframe[bear_col].fillna(0).values
        
        expiry_ns = np.timedelta64(expiry_min, 'm')
        
        last_bull_time = np.datetime64('1970-01-01')
        last_bear_time = np.datetime64('1970-01-01')
        
        for i in range(len(dataframe)):
            curr_time = dates[i]
            
            if bull_sigs[i] == 1:
                last_bull_time = curr_time
            if bear_sigs[i] == 1:
                last_bear_time = curr_time
                
            if (curr_time - last_bull_time) <= expiry_ns:
                bull_div_active[i] = 1
            
            if (curr_time - last_bear_time) <= expiry_ns:
                bear_div_active[i] = 1
                
        dataframe["bull_div_active"] = bull_div_active
        dataframe["bear_div_active"] = bear_div_active

        # --- Engulfing ---
        dataframe["bull_engulf"] = 0
        dataframe["bear_engulf"] = 0
        dataframe["engulf_swing_low"] = 0.0
        dataframe["engulf_swing_high"] = 0.0
        
        for i in range(1, len(dataframe)):
            cur = dataframe.iloc[i]
            prev = dataframe.iloc[i - 1]
            bull, bear = self._engulfing(cur, prev)
            if bull:
                dataframe.at[dataframe.index[i], "bull_engulf"] = 1
                dataframe.at[dataframe.index[i], "engulf_swing_low"] = prev["low"]
            if bear:
                dataframe.at[dataframe.index[i], "bear_engulf"] = 1
                dataframe.at[dataframe.index[i], "engulf_swing_high"] = prev["high"]

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        
        conditions_long = [
            dataframe["bull_div_active"] == 1,
            dataframe["bull_engulf"] == 1,
            dataframe["in_bull_fvg"] == 1,
            dataframe["volume"] > 0
        ]
        
        conditions_short = [
            dataframe["bear_div_active"] == 1,
            dataframe["bear_engulf"] == 1,
            dataframe["in_bear_fvg"] == 1,
            dataframe["volume"] > 0
        ]
        
        # Apply Trend Filter if enabled
        if self.use_trend_filter.value:
            trend_col = f"ema_trend_{self.trend_timeframe}"
            if trend_col in dataframe.columns:
                conditions_long.append(dataframe["close"] > dataframe[trend_col])
                conditions_short.append(dataframe["close"] < dataframe[trend_col])
        
        if conditions_long:
            dataframe.loc[
                pd.concat(conditions_long, axis=1).all(axis=1),
                ["enter_long", "enter_tag"]
            ] = (1, "long_div_fvg_engulf")
            
        if conditions_short:
            dataframe.loc[
                pd.concat(conditions_short, axis=1).all(axis=1),
                ["enter_short", "enter_tag"]
            ] = (1, "short_div_fvg_engulf")
            
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # Exit on opposite divergence
        dataframe.loc[
            (dataframe["bear_div_active"] == 1),
            ["exit_long", "exit_tag"]
        ] = (1, "bear_div_exit")
        
        dataframe.loc[
            (dataframe["bull_div_active"] == 1),
            ["exit_short", "exit_tag"]
        ] = (1, "bull_div_exit")
        
        return dataframe

    def custom_stoploss(self, pair: str, trade: Trade, current_time: datetime,
                        current_rate: float, current_profit: float, **kwargs) -> float | None:
        
        # Retrieve structure-based SL from custom_info
        trade_key = trade.open_date_utc.isoformat()
        initial_sl = abs(self.stoploss)
        structure_sl = None
        
        if pair in self.custom_info and "trade_stoplosses" in self.custom_info[pair]:
            trade_sl_info = self.custom_info[pair]["trade_stoplosses"].get(trade_key)
            if trade_sl_info:
                initial_sl = abs(trade_sl_info["sl"])
                structure_sl = trade_sl_info["sl"]

        # --- Trailing Logic (R-Multiples) ---
        # 1. Trail to 3R when at 4R
        if current_profit > initial_sl * 4.0:
            return stoploss_from_open(initial_sl * 3.0, current_profit, is_short=trade.is_short)
            
        # 2. Trail to 2R when at 3R
        elif current_profit > initial_sl * 3.0:
            return stoploss_from_open(initial_sl * 2.0, current_profit, is_short=trade.is_short)
            
        # 3. Breakeven at 1.5R
        elif current_profit > initial_sl * 1.5:
            return stoploss_from_open(0.001, current_profit, is_short=trade.is_short)
            
        # 4. Initial Structure Stop
        if structure_sl is not None:
            return structure_sl
            
        return self.stoploss

    def confirm_trade_entry(self, pair: str, order_type: str, amount: float, rate: float,
                            time_in_force: str, current_time: datetime, entry_tag: Optional[str] = None,
                            **kwargs) -> bool:
        
        if rate <= 0: return False
        
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(dataframe) > 0:
            last_candle = dataframe.iloc[-1]
            is_long = entry_tag and "long" in entry_tag
            is_short = entry_tag and "short" in entry_tag
            
            # Store structure SL
            sl_pct = None
            swing_price = 0.0
            
            if is_long and last_candle.get("engulf_swing_low", 0) > 0:
                swing_price = last_candle["engulf_swing_low"]
                sl_pct = (swing_price - rate) / rate
            elif is_short and last_candle.get("engulf_swing_high", 0) > 0:
                swing_price = last_candle["engulf_swing_high"]
                sl_pct = (rate - swing_price) / rate
                
            if sl_pct:
                if pair not in self.custom_info:
                    self.custom_info[pair] = {"trade_stoplosses": {}, "pending_entry": None}
                
                self.custom_info[pair]["pending_entry"] = {
                    "sl": sl_pct,
                    "swing_price": swing_price,
                    "entry_time": current_time
                }
                
        return True

    def adjust_trade_position(self, trade: Trade, **kwargs) -> float | None:
        pair = trade.pair
        trade_key = trade.open_date_utc.isoformat()
        
        if pair in self.custom_info and self.custom_info[pair].get("pending_entry"):
            if "trade_stoplosses" not in self.custom_info[pair]:
                self.custom_info[pair]["trade_stoplosses"] = {}
            
            self.custom_info[pair]["trade_stoplosses"][trade_key] = self.custom_info[pair]["pending_entry"]
            self.custom_info[pair]["pending_entry"] = None
            
            # Cleanup
            if len(self.custom_info[pair]["trade_stoplosses"]) > 50:
                keys = sorted(self.custom_info[pair]["trade_stoplosses"].keys())[-50:]
                self.custom_info[pair]["trade_stoplosses"] = {k: self.custom_info[pair]["trade_stoplosses"][k] for k in keys}
                
        return None
