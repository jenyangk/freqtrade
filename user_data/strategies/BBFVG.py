from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional, Any, Set

import numpy as np
import pandas as pd
import talib.abstract as ta
import freqtrade.vendor.qtpylib.indicators as qtpylib

from pandas import DataFrame, Series
from freqtrade.strategy import (
    IStrategy,
    merge_informative_pair,
    IntParameter,
    DecimalParameter,
    stoploss_from_open,
)
from freqtrade.exchange import timeframe_to_minutes
from freqtrade.persistence import Trade


class BBFVG(IStrategy):
    """
    BBFVG Strategy
    - 4H FVG
    - 1H Bollinger Band
    - EMA 21 and EMA 38 at 1H
    - Entry at the upper or lower band
    - Take Profit at 2:1 or the opposite band
    """

    # --- Mandatory metadata ---
    timeframe = "1h"  # Base timeframe for BB and EMAs

    # Configurable timeframes
    fvg_timeframe = "4h"  # Timeframe for FVG detection

    startup_candle_count = 100

    can_short: bool = True
    use_custom_stoploss = True

    order_types = {
        "entry": "limit",
        "exit": "limit",
        "stoploss": "market",
        "stoploss_on_exchange": False,
    }

    # --- Strategy Parameters ---
    # Bollinger Bands
    bb_window = IntParameter(10, 50, default=20, space="indicator")
    bb_std = DecimalParameter(1.5, 3.0, default=2.0, decimals=1, space="indicator")

    # EMAs
    ema_fast_period = IntParameter(10, 30, default=21, space="indicator")
    ema_slow_period = IntParameter(30, 60, default=38, space="indicator")

    # FVG
    fvg_threshold = DecimalParameter(0.0, 0.01, default=0.001, decimals=4, space="indicator")
    fvg_extend = IntParameter(
        10, 100, default=40, space="indicator"
    )  # How many periods FVG stays active

    # Risk Management
    risk_reward = DecimalParameter(1.5, 4.0, default=2.0, decimals=1, space="sell")

    # Position Adjustment (Multiple Entries)
    position_adjustment_enable = True
    max_entry_position_adjustment = 2  # Allow up to 3 entries total (initial + 2 adjustments)
    max_dca_multiplier = 1.5  # DCA multiplier

    # Default Stoploss (will be overridden by custom_stoploss)
    stoploss = -0.05

    # ROI (using custom exit mostly, but safety net)
    minimal_roi = {
        "0": 100  # Let custom exit handle it
    }

    process_only_new_candles = True

    # Custom info store
    custom_info: Dict[str, Dict[str, Any]] = {}

    plot_config = {
        "main_plot": {
            "bb_upperband": {"color": "rgba(0, 0, 255, 0.5)"},
            "bb_lowerband": {"color": "rgba(0, 0, 255, 0.5)"},
            "ema_fast": {"color": "orange"},
            "ema_slow": {"color": "yellow"},
            # Bullish FVG (Green Fill)
            "fvg_bull_top": {
                "color": "rgba(0, 255, 0, 0.5)",
                "fill_to": "fvg_bull_bottom",
                "fill_color": "rgba(0, 255, 0, 0.2)",
                "fill_label": "Bull FVG",
            },
            "fvg_bull_bottom": {"color": "rgba(0, 255, 0, 0.5)"},
            # Bearish FVG (Red Fill)
            "fvg_bear_top": {
                "color": "rgba(255, 0, 0, 0.5)",
                "fill_to": "fvg_bear_bottom",
                "fill_color": "rgba(255, 0, 0, 0.2)",
                "fill_label": "Bear FVG",
            },
            "fvg_bear_bottom": {"color": "rgba(255, 0, 0, 0.5)"},
        },
        "subplots": {
            "Signals": {
                "enter_long": {"color": "green", "type": "scatter"},
                "enter_short": {"color": "red", "type": "scatter"},
            }
        },
    }

    def informative_pairs(self):
        wl = self.dp.current_whitelist()
        return [(p, self.fvg_timeframe) for p in wl]

    # --- Helper: FVG detection ---
    @staticmethod
    def _detect_fvgs(df: DataFrame, threshold: float, extend: int) -> DataFrame:
        """Detect Fair Value Gaps and mark active zones"""
        df = df.copy()

        # Detect Bullish FVG: low > high[2] and close[1] > high[2]
        df["bull_fvg"] = (
            (df["low"] > df["high"].shift(2))
            & (df["close"].shift(1) > df["high"].shift(2))
            & ((df["low"] - df["high"].shift(2)) / df["high"].shift(2) > threshold)
        )

        # Detect Bearish FVG: high < low[2] and close[1] < low[2]
        df["bear_fvg"] = (
            (df["high"] < df["low"].shift(2))
            & (df["close"].shift(1) < df["low"].shift(2))
            & ((df["low"].shift(2) - df["high"]) / df["high"] > threshold)
        )

        # Track active FVGs
        active_bull_fvgs = []
        active_bear_fvgs = []

        df["fvg_bull_top"] = np.nan
        df["fvg_bull_bottom"] = np.nan
        df["fvg_bear_top"] = np.nan
        df["fvg_bear_bottom"] = np.nan

        for i in range(len(df)):
            current_close = df["close"].iloc[i]

            # Add new bullish FVG
            if df["bull_fvg"].iloc[i]:
                fvg_max = df["low"].iloc[i]
                fvg_min = df["high"].iloc[i - 2] if i >= 2 else df["low"].iloc[i]
                active_bull_fvgs.append({"max": fvg_max, "min": fvg_min, "start_idx": i})

            # Add new bearish FVG
            if df["bear_fvg"].iloc[i]:
                fvg_max = df["low"].iloc[i - 2] if i >= 2 else df["high"].iloc[i]
                fvg_min = df["high"].iloc[i]
                active_bear_fvgs.append({"max": fvg_max, "min": fvg_min, "start_idx": i})

            # Check mitigation and remove old FVGs
            # Bullish FVG is mitigated if price closes below it? Or just touches?
            # Standard FVG logic: mitigated if price trades through it.
            # Here we keep it simple: expire by time or if price closes beyond it.

            active_bull_fvgs = [
                fvg
                for fvg in active_bull_fvgs
                if (i - fvg["start_idx"]) < extend and current_close >= fvg["min"]
            ]

            active_bear_fvgs = [
                fvg
                for fvg in active_bear_fvgs
                if (i - fvg["start_idx"]) < extend and current_close <= fvg["max"]
            ]

            # Store the most recent active FVG for plotting/logic
            if active_bull_fvgs:
                df.at[df.index[i], "fvg_bull_top"] = active_bull_fvgs[-1]["max"]
                df.at[df.index[i], "fvg_bull_bottom"] = active_bull_fvgs[-1]["min"]

            if active_bear_fvgs:
                df.at[df.index[i], "fvg_bear_top"] = active_bear_fvgs[-1]["max"]
                df.at[df.index[i], "fvg_bear_bottom"] = active_bear_fvgs[-1]["min"]

        return df

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        if not self.dp:
            return dataframe

        pair = metadata["pair"]

        # Initialize custom info
        if pair not in self.custom_info:
            self.custom_info[pair] = {
                "trade_data": {}  # Store trade specific data like initial stoploss
            }

        # --- 1H Indicators (Base Timeframe) ---
        # Bollinger Bands
        bollinger = qtpylib.bollinger_bands(
            qtpylib.typical_price(dataframe), window=self.bb_window.value, stds=self.bb_std.value
        )
        dataframe["bb_upperband"] = bollinger["upper"]
        dataframe["bb_mid"] = bollinger["mid"]
        dataframe["bb_lowerband"] = bollinger["lower"]

        # EMAs
        dataframe["ema_fast"] = ta.EMA(dataframe, timeperiod=int(self.ema_fast_period.value))
        dataframe["ema_slow"] = ta.EMA(dataframe, timeperiod=int(self.ema_slow_period.value))

        # --- 4H Indicators (Informative) ---
        inf_fvg = self.dp.get_pair_dataframe(pair=pair, timeframe=self.fvg_timeframe)
        inf_fvg = self._detect_fvgs(
            inf_fvg, float(self.fvg_threshold.value), int(self.fvg_extend.value)
        )

        # Merge FVG info to base timeframe
        dataframe = merge_informative_pair(
            dataframe, inf_fvg, self.timeframe, self.fvg_timeframe, ffill=True
        )

        # Map merged columns to cleaner names
        dataframe["fvg_bull_top"] = dataframe[f"fvg_bull_top_{self.fvg_timeframe}"]
        dataframe["fvg_bull_bottom"] = dataframe[f"fvg_bull_bottom_{self.fvg_timeframe}"]
        dataframe["fvg_bear_top"] = dataframe[f"fvg_bear_top_{self.fvg_timeframe}"]
        dataframe["fvg_bear_bottom"] = dataframe[f"fvg_bear_bottom_{self.fvg_timeframe}"]

        # Check if price is in FVG
        # For Long: Price should be in/near Bullish FVG
        dataframe["in_bull_fvg"] = (
            (dataframe["fvg_bull_top"].notna())
            & (dataframe["low"] <= dataframe["fvg_bull_top"])
            & (dataframe["high"] >= dataframe["fvg_bull_bottom"])
        ).astype(int)

        # For Short: Price should be in/near Bearish FVG
        dataframe["in_bear_fvg"] = (
            (dataframe["fvg_bear_top"].notna())
            & (dataframe["high"] >= dataframe["fvg_bear_bottom"])
            & (dataframe["low"] <= dataframe["fvg_bear_top"])
        ).astype(int)

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # Long Entry
        # 1. Price touches Lower BB
        # 2. EMA 21 > EMA 38 (Uptrend context)
        # 3. Inside Bullish FVG
        dataframe.loc[
            (
                (dataframe["low"] <= dataframe["bb_lowerband"])
                & (dataframe["ema_fast"] > dataframe["ema_slow"])
                & (dataframe["in_bull_fvg"] == 1)
                & (dataframe["volume"] > 0)
            ),
            ["enter_long", "enter_tag"],
        ] = (1, "long_bb_fvg")

        # Short Entry
        # 1. Price touches Upper BB
        # 2. EMA 21 < EMA 38 (Downtrend context)
        # 3. Inside Bearish FVG
        dataframe.loc[
            (
                (dataframe["high"] >= dataframe["bb_upperband"])
                & (dataframe["ema_fast"] < dataframe["ema_slow"])
                & (dataframe["in_bear_fvg"] == 1)
                & (dataframe["volume"] > 0)
            ),
            ["enter_short", "enter_tag"],
        ] = (1, "short_bb_fvg")

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # Exits are handled by custom_exit (Opposite Band) and ROI/Stoploss

        # We can signal exit here if we want to use standard exit signals,
        # but "Opposite Band" is dynamic.

        # Exit Long if price touches Upper Band
        dataframe.loc[
            (dataframe["high"] >= dataframe["bb_upperband"]), ["exit_long", "exit_tag"]
        ] = (1, "long_exit_bb_upper")

        # Exit Short if price touches Lower Band
        dataframe.loc[
            (dataframe["low"] <= dataframe["bb_lowerband"]), ["exit_short", "exit_tag"]
        ] = (1, "short_exit_bb_lower")

        return dataframe

    def custom_stoploss(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        **kwargs,
    ) -> float:
        # Calculate dynamic stoploss based on FVG structure at entry
        # We store this in custom_info when trade is confirmed/adjusted

        trade_key = trade.open_date_utc.isoformat()
        
        # Ensure trade_data is populated from pending_entry if needed
        if pair in self.custom_info:
            if "trade_data" not in self.custom_info[pair]:
                self.custom_info[pair]["trade_data"] = {}
            
            # Try to migrate pending_entry if it matches this trade
            if trade_key not in self.custom_info[pair]["trade_data"] and "pending_entry" in self.custom_info[pair]:
                pending = self.custom_info[pair]["pending_entry"]
                # Simple check: if pending entry was recent (within 10 mins)
                if pending["entry_time"] >= trade.open_date_utc - timedelta(minutes=10):
                     self.custom_info[pair]["trade_data"][trade_key] = pending

        if pair in self.custom_info and "trade_data" in self.custom_info[pair]:
            trade_data = self.custom_info[pair]["trade_data"].get(trade_key)
            if trade_data and "sl_price" in trade_data:
                sl_price = trade_data["sl_price"]
                entry_price = trade.open_rate
                
                # Calculate Risk
                risk = abs(entry_price - sl_price)
                if risk == 0: return self.stoploss

                current_diff = (current_rate - entry_price) if not trade.is_short else (entry_price - current_rate)
                r_multiple = current_diff / risk
                
                new_sl_price = sl_price # Default to initial SL
                
                # Dynamic TP / Trailing Logic
                # 1:2 minimum then 1:3 and 1:4
                if r_multiple >= 3:
                    # Move SL to 1:2 (Entry + 2R)
                    if not trade.is_short:
                        new_sl_price = entry_price + (2 * risk)
                    else:
                        new_sl_price = entry_price - (2 * risk)
                elif r_multiple >= 2:
                    # Move SL to Breakeven (Entry)
                    new_sl_price = entry_price

                # Calculate percentage for Freqtrade
                # Formula: (new_sl_price - current_rate) / current_rate
                return (new_sl_price - current_rate) / current_rate

        return self.stoploss

    def custom_exit(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        **kwargs,
    ):
        # Check for 1:4 Target
        trade_key = trade.open_date_utc.isoformat()
        if pair in self.custom_info and "trade_data" in self.custom_info[pair]:
            trade_data = self.custom_info[pair]["trade_data"].get(trade_key)
            if trade_data and "sl_price" in trade_data:
                sl_price = trade_data["sl_price"]
                entry_price = trade.open_rate
                risk = abs(entry_price - sl_price)
                if risk > 0:
                    current_diff = (current_rate - entry_price) if not trade.is_short else (entry_price - current_rate)
                    r_multiple = current_diff / risk
                    
                    if r_multiple >= 4:
                        return "target_1_4"

        # Exit when price touches the opposite band
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        last_candle = dataframe.iloc[-1]

        if trade.is_short:
            # Short: Exit at Lower Band
            if current_rate <= last_candle["bb_lowerband"]:
                return "short_exit_bb_lower"
        else:
            # Long: Exit at Upper Band
            if current_rate >= last_candle["bb_upperband"]:
                return "long_exit_bb_upper"

        return None

    def custom_entry_price(
        self,
        pair: str,
        trade: Optional[Trade],
        current_time: datetime,
        proposed_rate: float,
        entry_tag: Optional[str],
        side: str,
        **kwargs,
    ) -> float:
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(dataframe) > 0:
            last_candle = dataframe.iloc[-1]

            if side == "long":
                fvg_top = last_candle.get("fvg_bull_top")
                # Ensure we enter within the FVG (below or at top)
                if pd.notna(fvg_top) and proposed_rate > fvg_top:
                    return fvg_top

            elif side == "short":
                fvg_bottom = last_candle.get("fvg_bear_bottom")
                # Ensure we enter within the FVG (above or at bottom)
                if pd.notna(fvg_bottom) and proposed_rate < fvg_bottom:
                    return fvg_bottom

        return proposed_rate

    def confirm_trade_entry(
        self,
        pair: str,
        order_type: str,
        amount: float,
        rate: float,
        time_in_force: str,
        current_time: datetime,
        entry_tag: Optional[str] = None,
        **kwargs,
    ):
        # Calculate and store Stop Loss price based on fixed 5% risk
        # We still store it to allow custom_stoploss to calculate R-multiples
        
        sl_price = None

        if entry_tag == "long_bb_fvg":
            # Fixed 5% Stop Loss
            sl_price = rate * 0.95

        elif entry_tag == "short_bb_fvg":
            # Fixed 5% Stop Loss
            sl_price = rate * 1.05

        if sl_price:
            # Calculate risk percentage
            risk = abs(rate - sl_price) / rate

            # Store
            if pair not in self.custom_info:
                self.custom_info[pair] = {"trade_data": {}}

            # We don't have trade object yet, so we use a temporary pending store?
            # Or we can use adjust_trade_position to finalize it.
            # But confirm_trade_entry is called BEFORE trade creation.
            # We can store it in a 'pending' slot.
            self.custom_info[pair]["pending_entry"] = {
                "sl_price": sl_price,
                "risk": risk,
                "entry_time": current_time,
            }

        return True

    def adjust_trade_position(
        self,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        min_stake: Optional[float],
        max_stake: float,
        current_entry_rate: float,
        current_exit_rate: float,
        current_entry_profit: float,
        current_exit_profit: float,
        **kwargs,
    ):
        pair = trade.pair
        trade_key = trade.open_date_utc.isoformat()

        # 1. Transfer pending entry data to trade_data
        if pair in self.custom_info and "pending_entry" in self.custom_info[pair]:
            pending = self.custom_info[pair]["pending_entry"]
            # Verify it's recent (within 1 candle)
            if pending["entry_time"] >= current_time - timedelta(
                minutes=timeframe_to_minutes(self.timeframe) * 2
            ):
                if "trade_data" not in self.custom_info[pair]:
                    self.custom_info[pair]["trade_data"] = {}
                self.custom_info[pair]["trade_data"][trade_key] = pending
                del self.custom_info[pair]["pending_entry"]

        # 2. Handle DCA / Multiple Entries
        if self.position_adjustment_enable:
            count = trade.nr_of_successful_entries
            if count <= self.max_entry_position_adjustment:
                # Get analyzed dataframe
                dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
                if len(dataframe) > 0:
                    last_candle = dataframe.iloc[-1]
                    last_candle_date = last_candle["date"]

                    # Check if we already bought on this candle to avoid double DCA
                    # We use custom_info to track the last DCA candle time
                    if pair not in self.custom_info:
                        self.custom_info[pair] = {}

                    last_dca_time = self.custom_info[pair].get("last_dca_time")
                    if last_dca_time == last_candle_date:
                        return None

                    # Check for valid signal on the new candle
                    # We also ensure this is a NEW signal (after the initial entry)
                    if last_candle_date > trade.open_date_utc:
                        if trade.is_short:
                            # Short: Check for short entry signal (which enforces Upper Band + Bear FVG + EMA)
                            if last_candle["enter_short"] == 1:
                                self.custom_info[pair]["last_dca_time"] = last_candle_date
                                return trade.stake_amount * self.max_dca_multiplier
                        else:
                            # Long: Check for long entry signal (which enforces Lower Band + Bull FVG + EMA)
                            if last_candle["enter_long"] == 1:
                                self.custom_info[pair]["last_dca_time"] = last_candle_date
                                return trade.stake_amount * self.max_dca_multiplier

        return None
