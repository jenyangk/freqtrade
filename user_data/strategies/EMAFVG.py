from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional, Any, Set

import numpy as np
import pandas as pd
import talib.abstract as ta
import freqtrade.vendor.qtpylib.indicators as qtpylib

from pandas import DataFrame, Series
from freqtrade.strategy import IStrategy, merge_informative_pair, IntParameter, DecimalParameter
from freqtrade.exchange import timeframe_to_minutes


class EMAFVG(IStrategy):
    """EMA Fair Value Gap strategy ported from QuantConnect C# implementation.

    Core concept:
    - Detect Fair Value Gaps (FVG) on configurable timeframe (default 15m) - 3-candle pattern leaving an inefficiency
    - Use EMA(50) and EMA(100) as trend filter on configurable timeframe (default 1m)
    - Confirm entries on base timeframe (default 3m) with engulfing candle touching/inside matching-direction active FVG.

    Entry Rules:
    - Long: EMA50 > EMA100 (uptrend) + bullish engulfing + price touching/in bullish FVG
    - Short: EMA100 > EMA50 (downtrend) + bearish engulfing + price touching/in bearish FVG

    Implementation notes:
    - Timeframes are configurable: set ema_timeframe, fvg_timeframe, and base timeframe in class attributes
    - FVGs tracked via rolling list; extended forward a configurable number of periods
    - Risk management: structure-based stoploss + R-multiple trailing stops
    """

    # --- Mandatory metadata ---
    timeframe = "1m"  # Base timeframe for entries / execution (Engulfing candles)

    # Configurable timeframes - can be changed via config or hyperopt
    # Base timeframe is used for engulfing candle detection
    # EMA timeframe is used for trend filter
    # FVG timeframe is used for Fair Value Gap detection
    ema_timeframe = "1m"  # Timeframe for EMA trend filter
    fvg_timeframe = "15m"  # Timeframe for FVG detection

    startup_candle_count = 400  # Need enough history for EMAs & FVG

    can_short: bool = True
    use_custom_stoploss = True  # Enable custom stoploss for structure-based and trailing stops

    # --- Strategy Parameters (exposed for optimization) ---
    ema_fast = IntParameter(30, 70, default=50, space="indicator")
    ema_slow = IntParameter(80, 150, default=100, space="indicator")

    fvg_threshold = DecimalParameter(
        0.0, 0.01, default=0.0, decimals=4, space="indicator"
    )  # min relative gap size
    fvg_extend_15m = IntParameter(
        20, 100, default=50, space="indicator"
    )  # how many 15m periods FVG stays active
    fvg_max_unmitigated = IntParameter(0, 5, default=0, space="indicator")  # 0 = unlimited

    risk_reward = DecimalParameter(1.5, 3.0, default=2.0, decimals=1, space="sell")

    # --- Order / risk parameters ---
    stoploss = -0.05  # base stoploss (5% - wider for EMA strategy)
    minimal_roi = {"0": 0.20, "30": 0.10, "120": 0.05, "360": 0}

    process_only_new_candles = True

    # Custom info store (not persistent across restarts)
    custom_info: Dict[str, Dict[str, Any]] = {}

    plot_config = {
        "main_plot": {
            # EMA lines will be named dynamically based on ema_timeframe
        },
        "subplots": {
            "Signals": {
                "bull_engulf": {"color": "green", "type": "scatter"},
                "bear_engulf": {"color": "red", "type": "scatter"},
            }
        },
    }

    def informative_pairs(self):
        # Collect both informative timeframes for every whitelisted pair
        wl = self.dp.current_whitelist()
        pairs = [(p, self.ema_timeframe) for p in wl] + [(p, self.fvg_timeframe) for p in wl]
        return pairs

    # --- Helper: FVG detection ---
    @staticmethod
    def _detect_fvgs(df: DataFrame, threshold: float) -> DataFrame:
        """Identify bullish/bearish Fair Value Gaps.
        A bullish FVG: current.low > previous.high AND middle.close > previous.high.
        A bearish FVG: current.high < previous.low AND middle.close < previous.low.
        Adds columns: fvg_bull_top, fvg_bull_bottom, fvg_bear_top, fvg_bear_bottom."""
        df = df.copy()
        df["fvg_bull_top"] = np.nan
        df["fvg_bull_bottom"] = np.nan
        df["fvg_bear_top"] = np.nan
        df["fvg_bear_bottom"] = np.nan
        for i in range(2, len(df)):
            prev = df.iloc[i - 2]
            mid = df.iloc[i - 1]
            cur = df.iloc[i]
            ref_price = prev["close"] if prev["close"] > 0 else mid["close"]
            if ref_price <= 0:
                continue
            # Bullish
            if cur["low"] > prev["high"] and mid["close"] > prev["high"]:
                gap_top = cur["low"]
                gap_bottom = prev["high"]
                gap_pct = abs(gap_top - gap_bottom) / ref_price
                if gap_pct >= threshold:
                    df.loc[df.index[i], "fvg_bull_top"] = gap_top
                    df.loc[df.index[i], "fvg_bull_bottom"] = gap_bottom
            # Bearish
            if cur["high"] < prev["low"] and mid["close"] < prev["low"]:
                gap_top = prev["low"]
                gap_bottom = cur["high"]
                gap_pct = abs(gap_top - gap_bottom) / ref_price
                if gap_pct >= threshold:
                    df.loc[df.index[i], "fvg_bear_top"] = gap_top
                    df.loc[df.index[i], "fvg_bear_bottom"] = gap_bottom
        return df

    @staticmethod
    def _engulfing(current: Series, previous: Series) -> Tuple[bool, bool]:
        """Check for bullish/bearish engulfing patterns using candle bodies."""
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
        # Initialize custom info storage for pair
        if pair not in self.custom_info:
            self.custom_info[pair] = {
                "active_fvgs": [],  # list of dicts: {'t': timestamp, 'top': float, 'bottom': float, 'bull': bool}
                "last_indicator_hash": None,
                "fvg_seen_bull": set(),  # set of 15m timestamps already registered (bull)
                "fvg_seen_bear": set(),  # set of 15m timestamps already registered (bear)
                "trade_stoplosses": {},  # open_date_utc -> {'sl': stoploss_pct, 'swing_price': price}
                "pending_entry": None,  # temporary storage for next trade entry
            }

        # --- Simple caching: if last candle timestamp unchanged, skip recomputation ---
        if len(dataframe) > 0:
            last_time = dataframe.iloc[-1]["date"]
            indicator_hash = (last_time, len(dataframe))
            if self.custom_info[pair].get("last_indicator_hash") == indicator_hash and {
                "in_bull_fvg",
                "in_bear_fvg",
                "bull_engulf",
                "bear_engulf",
            }.issubset(dataframe.columns):
                return dataframe

        # --- EMA informative for trend filter ---
        inf_ema = self.dp.get_pair_dataframe(pair=pair, timeframe=self.ema_timeframe)
        inf_ema["ema50"] = ta.EMA(inf_ema, timeperiod=int(self.ema_fast.value))
        inf_ema["ema100"] = ta.EMA(inf_ema, timeperiod=int(self.ema_slow.value))

        # --- FVG informative for FVG ---
        inf_fvg = self.dp.get_pair_dataframe(pair=pair, timeframe=self.fvg_timeframe)
        inf_fvg = self._detect_fvgs(inf_fvg, float(self.fvg_threshold.value))

        # Merge informative pairs safely
        dataframe = merge_informative_pair(
            dataframe, inf_ema, self.timeframe, self.ema_timeframe, ffill=True
        )
        dataframe = merge_informative_pair(
            dataframe, inf_fvg, self.timeframe, self.fvg_timeframe, ffill=True
        )

        # Rename for easier access
        dataframe["ema50"] = dataframe["ema50_" + self.ema_timeframe]
        dataframe["ema100"] = dataframe["ema100_" + self.ema_timeframe]

        # Trend filters
        dataframe["trend_long"] = dataframe["ema50"] > dataframe["ema100"]
        dataframe["trend_short"] = dataframe["ema100"] > dataframe["ema50"]

        # --- FVG activation & fill tracking ---
        dataframe["in_bull_fvg"] = 0
        dataframe["in_bear_fvg"] = 0
        bull_top_col = "fvg_bull_top_" + self.fvg_timeframe
        bull_bottom_col = "fvg_bull_bottom_" + self.fvg_timeframe
        bear_top_col = "fvg_bear_top_" + self.fvg_timeframe
        bear_bottom_col = "fvg_bear_bottom_" + self.fvg_timeframe
        extend = int(self.fvg_extend_15m.value)
        window_minutes_fvg = extend * timeframe_to_minutes(self.fvg_timeframe)

        active_fvgs = self.custom_info[pair]["active_fvgs"]
        # Register NEW FVGs from the original FVG timeframe dataframe
        seen_bull: Set[pd.Timestamp] = self.custom_info[pair].get("fvg_seen_bull", set())
        seen_bear: Set[pd.Timestamp] = self.custom_info[pair].get("fvg_seen_bear", set())
        new_bull_rows = inf_fvg[inf_fvg["fvg_bull_top"].notna()][
            ["date", "fvg_bull_top", "fvg_bull_bottom"]
        ]
        for _, r in new_bull_rows.iterrows():
            ts = r["date"]
            if ts not in seen_bull:
                active_fvgs.append(
                    {
                        "t": ts,
                        "top": r["fvg_bull_top"],
                        "bottom": r["fvg_bull_bottom"],
                        "bull": True,
                        "filled": False,
                    }
                )
                seen_bull.add(ts)
        new_bear_rows = inf_fvg[inf_fvg["fvg_bear_top"].notna()][
            ["date", "fvg_bear_top", "fvg_bear_bottom"]
        ]
        for _, r in new_bear_rows.iterrows():
            ts = r["date"]
            if ts not in seen_bear:
                active_fvgs.append(
                    {
                        "t": ts,
                        "top": r["fvg_bear_top"],
                        "bottom": r["fvg_bear_bottom"],
                        "bull": False,
                        "filled": False,
                    }
                )
                seen_bear.add(ts)
        self.custom_info[pair]["fvg_seen_bull"] = seen_bull
        self.custom_info[pair]["fvg_seen_bear"] = seen_bear

        # Deduplicate
        seen = set()
        uniq = []
        for f in reversed(active_fvgs):
            key = (f["t"], f["top"], f["bottom"], f["bull"])
            if key not in seen:
                seen.add(key)
                uniq.append(f)
        active_fvgs = list(reversed(uniq))

        # Prune by time window and remove filled
        latest_time = dataframe.iloc[-1]["date"] if len(dataframe) else None
        cutoff = latest_time - pd.Timedelta(minutes=window_minutes_fvg) if latest_time else None
        active_fvgs = [
            f
            for f in active_fvgs
            if (cutoff is None or f["t"] >= cutoff) and not f.get("filled", False)
        ]

        # Apply max unmitigated limit (if >0 keep most recent subset)
        max_unmit = int(self.fvg_max_unmitigated.value)
        if max_unmit > 0:
            bulls = [f for f in active_fvgs if f["bull"]]
            bears = [f for f in active_fvgs if not f["bull"]]
            bulls = bulls[-max_unmit:]
            bears = bears[-max_unmit:]
            active_fvgs = bulls + bears

        # For each row mark whether price is within/touches any active unfilled FVG, and detect fills.
        # C# logic: bullish filled if close < bottom; bearish filled if close > top
        for idx, row in dataframe.iterrows():
            price_high = row["high"]
            price_low = row["low"]
            price_close = row["close"]
            for f in active_fvgs:
                if f["filled"]:
                    continue
                # Touch or inside check
                touched = price_low <= f["top"] and price_high >= f["bottom"]
                # Beyond check: for bullish, close below bottom; for bearish, close above top
                beyond = (f["bull"] and price_close < f["bottom"]) or (
                    not f["bull"] and price_close > f["top"]
                )
                if touched or beyond:
                    if f["bull"]:
                        dataframe.at[idx, "in_bull_fvg"] = 1
                    else:
                        dataframe.at[idx, "in_bear_fvg"] = 1
                # Fill condition from C#: bullish mitigated if close < bottom; bearish if close > top
                if f["bull"] and price_close < f["bottom"]:
                    f["filled"] = True
                elif not f["bull"] and price_close > f["top"]:
                    f["filled"] = True

        # Remove filled FVGs
        active_fvgs = [f for f in active_fvgs if not f["filled"]]
        self.custom_info[pair]["active_fvgs"] = active_fvgs

        # Cache hash for next call
        if len(dataframe) > 0:
            self.custom_info[pair]["last_indicator_hash"] = (
                dataframe.iloc[-1]["date"],
                len(dataframe),
            )

        # --- Engulfing signal at base timeframe (3m) ---
        dataframe["bull_engulf"] = 0
        dataframe["bear_engulf"] = 0
        dataframe["engulf_swing_low"] = 0.0  # For long entries: low of candle before engulfing
        dataframe["engulf_swing_high"] = 0.0  # For short entries: high of candle before engulfing
        for i in range(1, len(dataframe)):
            cur = dataframe.iloc[i]
            prev = dataframe.iloc[i - 1]
            bull, bear = self._engulfing(cur, prev)
            if bull:
                dataframe.at[dataframe.index[i], "bull_engulf"] = 1
                # Store swing low (low of previous candle) for stoploss
                dataframe.at[dataframe.index[i], "engulf_swing_low"] = prev["low"]
            if bear:
                dataframe.at[dataframe.index[i], "bear_engulf"] = 1
                # Store swing high (high of previous candle) for stoploss
                dataframe.at[dataframe.index[i], "engulf_swing_high"] = prev["high"]

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # Long entry conditions: uptrend + bullish engulfing + in/touch bull FVG
        dataframe.loc[
            (
                (dataframe["trend_long"] == True)
                & (dataframe["bull_engulf"] == 1)
                & (dataframe["in_bull_fvg"] == 1)
                & (dataframe["volume"] > 0)
            ),
            ["enter_long", "enter_tag"],
        ] = (1, "ema_bull_fvg_engulf")

        # Short entry conditions: downtrend + bearish engulfing + in/touch bear FVG
        dataframe.loc[
            (
                (dataframe["trend_short"] == True)
                & (dataframe["bear_engulf"] == 1)
                & (dataframe["in_bear_fvg"] == 1)
                & (dataframe["volume"] > 0)
            ),
            ["enter_short", "enter_tag"],
        ] = (1, "ema_bear_fvg_engulf")

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # Basic timed/ROI exit handled by minimal_roi.
        # Optional early exits when trend reverses
        dataframe["exit_long"] = 0
        dataframe["exit_short"] = 0
        # Early long exit: trend reverses to short
        dataframe.loc[
            ((dataframe["enter_long"].shift(1) == 1) & (dataframe["trend_short"] == True)),
            "exit_long",
        ] = 1
        # Early short exit: trend reverses to long
        dataframe.loc[
            ((dataframe["enter_short"].shift(1) == 1) & (dataframe["trend_long"] == True)),
            "exit_short",
        ] = 1
        return dataframe

    def custom_stoploss(
        self,
        pair: str,
        trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        **kwargs,
    ) -> float:
        """
        Custom stoploss logic:
        - Initial stop: swing low (long) or swing high (short) from candle before engulfing
        - Move to breakeven when price reaches 1.5R
        - Trail stop at 2R when price reaches 3R
        - Trail stop at 3R when price reaches 4R
        """
        # Use trade open_date_utc as unique identifier
        trade_key = trade.open_date_utc.isoformat()

        # Get structure-based stoploss for this specific trade
        initial_sl = abs(self.stoploss)  # Default
        if pair in self.custom_info and "trade_stoplosses" in self.custom_info[pair]:
            trade_sl_info = self.custom_info[pair]["trade_stoplosses"].get(trade_key)
            if trade_sl_info:
                initial_sl = abs(trade_sl_info["sl"])

        # At 4R, trail stop at 3R
        if current_profit >= initial_sl * 4.0:
            return -(initial_sl * 3.0)

        # At 3R, trail stop at 2R
        elif current_profit >= initial_sl * 3.0:
            return -(initial_sl * 2.0)

        # At 1.5R, move to breakeven
        elif current_profit >= initial_sl * 1.5:
            return -0.01  # Small buffer to ensure exit

        # Below 1.5R, use structure-based initial stoploss
        if pair in self.custom_info and "trade_stoplosses" in self.custom_info[pair]:
            trade_sl_info = self.custom_info[pair]["trade_stoplosses"].get(trade_key)
            if trade_sl_info:
                return trade_sl_info["sl"]

        return self.stoploss

    def adjust_trade_position(
        self,
        trade,
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
        """
        Called on first iteration after trade is opened.
        Transfer pending_entry info to trade_stoplosses dict using trade.open_date_utc as key.
        """
        pair = trade.pair
        trade_key = trade.open_date_utc.isoformat()

        if pair in self.custom_info and self.custom_info[pair].get("pending_entry"):
            # Transfer pending entry to trade_stoplosses
            if trade_key not in self.custom_info[pair]["trade_stoplosses"]:
                self.custom_info[pair]["trade_stoplosses"][trade_key] = self.custom_info[pair][
                    "pending_entry"
                ]
                # Clear pending entry after transfer
                self.custom_info[pair]["pending_entry"] = None

                # Cleanup old trades (keep only last 20 to prevent memory bloat)
                if len(self.custom_info[pair]["trade_stoplosses"]) > 20:
                    # Keep only the most recent 20 trades
                    sorted_keys = sorted(self.custom_info[pair]["trade_stoplosses"].keys())[-20:]
                    self.custom_info[pair]["trade_stoplosses"] = {
                        k: v
                        for k, v in self.custom_info[pair]["trade_stoplosses"].items()
                        if k in sorted_keys
                    }

        return None  # No position adjustment, just using this for bookkeeping

    def custom_exit(
        self,
        pair: str,
        trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        **kwargs,
    ):
        """
        Custom exit logic to implement 2R target from C# strategy.
        Calculate if we've hit 2R profit and exit.
        """
        rr = float(self.risk_reward.value)
        # Risk is roughly abs(stoploss)
        risk = abs(self.stoploss)
        target_profit = risk * rr
        if current_profit >= target_profit:
            return "target_2R"
        return None

    def leverage(
        self,
        pair: str,
        current_time: datetime,
        current_rate: float,
        proposed_leverage: float,
        max_leverage: float,
        side: str,
        **kwargs,
    ) -> float:
        return min(3.0, max_leverage)  # modest leverage

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
        if rate <= 0:
            return False

        # Get the current dataframe to find swing structure for stoploss
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(dataframe) > 0:
            last_candle = dataframe.iloc[-1]

            # Determine if this is a long or short entry based on entry_tag
            is_long = entry_tag and "bull" in entry_tag.lower()
            is_short = entry_tag and "bear" in entry_tag.lower()

            # Calculate structure-based stoploss
            if is_long and last_candle.get("engulf_swing_low", 0) > 0:
                # Long entry: stop below the swing low (candle before engulfing)
                swing_low = last_candle["engulf_swing_low"]
                stoploss_pct = (swing_low - rate) / rate  # Negative value
                # Store in pending_entry - will be moved to trade_stoplosses in adjust_trade_position
                if pair not in self.custom_info:
                    self.custom_info[pair] = {
                        "trade_stoplosses": {},
                        "pending_entry": None,
                        "active_fvgs": [],
                        "fvg_seen_bull": set(),
                        "fvg_seen_bear": set(),
                    }
                self.custom_info[pair]["pending_entry"] = {
                    "sl": stoploss_pct,
                    "swing_price": swing_low,
                    "entry_time": current_time,
                    "entry_rate": rate,
                }

            elif is_short and last_candle.get("engulf_swing_high", 0) > 0:
                # Short entry: stop above the swing high (candle before engulfing)
                swing_high = last_candle["engulf_swing_high"]
                stoploss_pct = (rate - swing_high) / rate  # Negative value for short
                if pair not in self.custom_info:
                    self.custom_info[pair] = {
                        "trade_stoplosses": {},
                        "pending_entry": None,
                        "active_fvgs": [],
                        "fvg_seen_bull": set(),
                        "fvg_seen_bear": set(),
                    }
                self.custom_info[pair]["pending_entry"] = {
                    "sl": stoploss_pct,
                    "swing_price": swing_high,
                    "entry_time": current_time,
                    "entry_rate": rate,
                }

        return True

    def confirm_trade_exit(
        self,
        pair: str,
        trade,
        order_type: str,
        amount: float,
        rate: float,
        time_in_force: str,
        exit_reason: str,
        current_time: datetime,
        **kwargs,
    ) -> bool:
        return True
