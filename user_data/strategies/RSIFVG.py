from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional, Any, Set

import numpy as np
import pandas as pd
import talib.abstract as ta
import freqtrade.vendor.qtpylib.indicators as qtpylib

from pandas import DataFrame, Series
from freqtrade.strategy import IStrategy, merge_informative_pair, IntParameter, DecimalParameter, stoploss_from_open, stoploss_from_absolute
from freqtrade.persistence import Trade
from freqtrade.exchange import timeframe_to_minutes


class RSIFVG(IStrategy):
	"""RSI Fair Value Gap strategy ported from QuantConnect C# implementation.

	Core concept:
	- Detect Fair Value Gaps (FVG) on configurable timeframe (default 4h) - 3-candle pattern leaving an inefficiency
	- Detect RSI divergences on configurable timeframe (default 15m) using pivot highs/lows
	- Confirm entries on base timeframe (default 3m) with engulfing candle inside matching-direction active FVG.

	Implementation notes / Simplifications:
	- Timeframes are configurable: set rsi_timeframe, fvg_timeframe, and base timeframe in class attributes
	- FVGs tracked via a rolling list based on FVG timeframe candles; extended forward a configurable number of periods.
	- Divergence expires after N * RSI timeframe periods (configurable).
	- Engulfing definition uses candle bodies only (can be refined).
	- Risk management simplified: static stoploss + dynamic ROI table approximating R-multiple trailing concept.
	- Because freqtrade operates per-pair, multi-symbol logic from QC not required.
	"""

	# --- Mandatory metadata ---
	timeframe = '3m'  # Base timeframe for entries / execution (Engulfing candles)
	
	# Configurable timeframes - can be changed via config or hyperopt
	# Base timeframe is used for engulfing candle detection
	# RSI timeframe is used for divergence detection
	# FVG timeframe is used for Fair Value Gap detection
	rsi_timeframe = '3m'  # Timeframe for RSI divergence detection (should be higher than base)
	fvg_timeframe = '15m'   # Timeframe for FVG detection (should be higher than RSI)

	startup_candle_count = 400  # Need enough history for pivots & FVG

	can_short: bool = True  # Allow both directions similar to QC version
	use_custom_stoploss = True  # Enable custom stoploss for breakeven and trailing

	# --- Strategy Parameters (exposed for optimization) ---
	rsi_period = IntParameter(10, 20, default=14, space='indicator')
	pivot_left = IntParameter(2, 10, default=5, space='indicator')
	pivot_right = IntParameter(2, 10, default=5, space='indicator')
	min_lookback = IntParameter(2, 10, default=5, space='indicator')  # in pivot counts (rsi_timeframe bars)
	max_lookback = IntParameter(20, 80, default=60, space='indicator')  # in pivot counts (rsi_timeframe bars)
	divergence_expiry = IntParameter(4, 20, default=20, space='indicator')  # rsi_timeframe periods before divergence invalid

	fvg_threshold = DecimalParameter(0.0, 0.01, default=0.0, decimals=4, space='indicator')  # min relative gap size (used when auto_threshold=False)
	fvg_extend = IntParameter(10, 200, default=20, space='indicator')  # how many fvg_timeframe bars FVG stays active
	fvg_max_unmitigated = IntParameter(0, 5, default=0, space='indicator')  # 0 = unlimited
	auto_threshold = False  # Use automatic threshold calculation

	# --- Order / risk parameters ---
	stoploss = -0.05  # base stoploss (5%)

	# ROI table approximating R-multiple trailing updates
	# Remove minimal_roi to use dynamic trailing based on custom_stoploss
	minimal_roi = {
		# "0": 0.40,
		# "15": 0.25,
		# "60": 0.15,
		# "180": 0.05,
		# "360": 0
	}

	process_only_new_candles = True

	# Custom info store (not persistent across restarts)
	custom_info: Dict[str, Dict[str, Any]] = {}

	plot_config = {
		'main_plot': {
			'fvg_bull_top': {'color': 'rgba(0, 255, 0, 0.3)', 'type': 'line'},
			'fvg_bull_bottom': {'color': 'rgba(0, 255, 0, 0.3)', 'type': 'line'},
			'fvg_bear_top': {'color': 'rgba(255, 0, 0, 0.3)', 'type': 'line'},
			'fvg_bear_bottom': {'color': 'rgba(255, 0, 0, 0.3)', 'type': 'line'},
		},
		'subplots': {
			'RSI': {
				'rsi_base': {'color': 'orange'},
			},
			'Signals': {
				'bull_div': {'color': 'green', 'type': 'scatter'},
				'bear_div': {'color': 'red', 'type': 'scatter'},
				'bull_div_active': {'color': 'lightgreen'},
				'bear_div_active': {'color': 'lightcoral'},
			},
			'FVG Status': {
				'in_bull_fvg': {'color': 'green', 'type': 'scatter'},
				'in_bear_fvg': {'color': 'red', 'type': 'scatter'},
			},
			'Entry Signals': {
				'bull_engulf': {'color': 'blue', 'type': 'scatter'},
				'bear_engulf': {'color': 'purple', 'type': 'scatter'},
			}
		}
	}

	def informative_pairs(self):
		# Collect both informative timeframes for every whitelisted pair
		wl = self.dp.current_whitelist()
		pairs = ([(p, self.rsi_timeframe) for p in wl] +
				 [(p, self.fvg_timeframe) for p in wl])
		return pairs

	# --- Helper: FVG detection ---
	@staticmethod
	def _detect_fvgs(df: DataFrame, threshold: float, auto_threshold: bool, fvg_extend: int) -> DataFrame:
		"""Detect Fair Value Gaps and mark active zones"""
		df = df.copy()
		
		# Calculate threshold
		if auto_threshold:
			cumsum_range = ((df['high'] - df['low']) / df['low']).cumsum()
			threshold_series = cumsum_range / (df.index + 1)
		else:
			threshold_series = threshold
		
		# Detect Bullish FVG
		# Bull FVG occurs when: low > high[2] and close[1] > high[2]
		df['bull_fvg'] = (
			(df['low'] > df['high'].shift(2)) &
			(df['close'].shift(1) > df['high'].shift(2)) &
			((df['low'] - df['high'].shift(2)) / df['high'].shift(2) > threshold_series)
		)
		
		# Detect Bearish FVG  
		# Bear FVG occurs when: high < low[2] and close[1] < low[2]
		df['bear_fvg'] = (
			(df['high'] < df['low'].shift(2)) &
			(df['close'].shift(1) < df['low'].shift(2)) &
			((df['low'].shift(2) - df['high']) / df['high'] > threshold_series)
		)
		
		# Store FVG zones
		df['bull_fvg_max'] = 0.0
		df['bull_fvg_min'] = 0.0
		df['bear_fvg_max'] = 0.0
		df['bear_fvg_min'] = 0.0
		
		# Track active FVGs
		active_bull_fvgs = []
		active_bear_fvgs = []
		
		for i in range(len(df)):
			current_close = df['close'].iloc[i]
			
			# Add new bullish FVG
			if df['bull_fvg'].iloc[i]:
				fvg_max = df['low'].iloc[i]
				fvg_min = df['high'].iloc[i-2] if i >= 2 else df['low'].iloc[i]
				active_bull_fvgs.append({
					'max': fvg_max,
					'min': fvg_min,
					'start_idx': i
				})
			
			# Add new bearish FVG
			if df['bear_fvg'].iloc[i]:
				fvg_max = df['low'].iloc[i-2] if i >= 2 else df['high'].iloc[i]
				fvg_min = df['high'].iloc[i]
				active_bear_fvgs.append({
					'max': fvg_max,
					'min': fvg_min,
					'start_idx': i
				})
			
			# Check mitigation and remove old FVGs
			active_bull_fvgs = [
				fvg for fvg in active_bull_fvgs 
				if current_close >= fvg['min'] and (i - fvg['start_idx']) < fvg_extend
			]
			
			active_bear_fvgs = [
				fvg for fvg in active_bear_fvgs 
				if current_close <= fvg['max'] and (i - fvg['start_idx']) < fvg_extend
			]
			
			# Store the most recent active FVG for plotting
			if active_bull_fvgs:
				df.at[df.index[i], 'bull_fvg_max'] = active_bull_fvgs[-1]['max']
				df.at[df.index[i], 'bull_fvg_min'] = active_bull_fvgs[-1]['min']
			
			if active_bear_fvgs:
				df.at[df.index[i], 'bear_fvg_max'] = active_bear_fvgs[-1]['max']
				df.at[df.index[i], 'bear_fvg_min'] = active_bear_fvgs[-1]['min']
		
		# Check if current candle is in or touching FVG
		df['in_bull_fvg'] = (
			(df['bull_fvg_max'] > 0) &
			# Candle low is at or below FVG max and high is at or above FVG min
			(df['low'] <= df['bull_fvg_max']) &
			(df['high'] >= df['bull_fvg_min'])
		)
		
		df['in_bear_fvg'] = (
			(df['bear_fvg_max'] > 0) &
			# Candle low is at or below FVG max and high is at or above FVG min
			(df['low'] <= df['bear_fvg_max']) &
			(df['high'] >= df['bear_fvg_min'])
		)
		
		return df

	@staticmethod
	def _pivot(series: Series, left: int, right: int) -> Series:
		"""Return +1 for pivot high, -1 for pivot low, 0 otherwise.
		Requires strict dominance - at least one surrounding value must be lower/higher."""
		values = series.values
		out = np.zeros(len(series), dtype=int)
		for i in range(left, len(series)-right):
			window = values[i-left:i+right+1]
			center = values[i]
			# Require at least one value to be strictly lower/higher (prevents multiple pivots in flat markets)
			if center == np.max(window) and (window < center).sum() >= 1:
				out[i] = 1
			elif center == np.min(window) and (window > center).sum() >= 1:
				out[i] = -1
		return Series(out, index=series.index)

	@staticmethod
	def _engulfing(current: Series, previous: Series) -> Tuple[bool, bool]:
		prev_body_top = max(previous['open'], previous['close'])
		prev_body_bottom = min(previous['open'], previous['close'])
		cur_body_top = max(current['open'], current['close'])
		cur_body_bottom = min(current['open'], current['close'])
		is_bull = current['close'] > current['open'] and previous['close'] < previous['open'] and cur_body_top > prev_body_top and cur_body_bottom <= prev_body_bottom
		is_bear = current['close'] < current['open'] and previous['close'] > previous['open'] and cur_body_top >= prev_body_top and cur_body_bottom < prev_body_bottom
		return is_bull, is_bear

	def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
		if not self.dp:
			return dataframe

		pair = metadata['pair']
		# Initialize custom info storage for pair
		if pair not in self.custom_info:
			self.custom_info[pair] = {
				'last_indicator_hash': None,
				'breakeven_exits': set(),  # track trade IDs that hit breakeven to prevent re-entry
				'trade_stoplosses': {},  # open_date_utc -> {'sl': stoploss_pct, 'swing_price': price}
				'pending_entry': None,  # temporary storage for next trade entry
			}

		# --- Simple caching: if last candle timestamp unchanged, skip recomputation ---
		if len(dataframe) > 0:
			last_time = dataframe.iloc[-1]['date']
			indicator_hash = (last_time, len(dataframe))
			if self.custom_info[pair].get('last_indicator_hash') == indicator_hash and \
				{'bull_div_active','bear_div_active','in_bull_fvg','in_bear_fvg'}.issubset(dataframe.columns):
				return dataframe

		# --- RSI informative for RSI divergence ---
		inf_rsi = self.dp.get_pair_dataframe(pair=pair, timeframe=self.rsi_timeframe)
		inf_rsi['rsi'] = ta.RSI(inf_rsi, timeperiod=int(self.rsi_period.value))

		# Add pivots on RSI timeframe closes and RSI
		inf_rsi['price_pivot'] = self._pivot(inf_rsi['close'], int(self.pivot_left.value), int(self.pivot_right.value))
		inf_rsi['rsi_pivot'] = self._pivot(inf_rsi['rsi'], int(self.pivot_left.value), int(self.pivot_right.value))

		# Track pivot points for divergence detection
		# We'll build rolling last two highs/lows logic after merge.

		# --- FVG informative for FVG ---
		inf_fvg = self.dp.get_pair_dataframe(pair=pair, timeframe=self.fvg_timeframe)
		inf_fvg = self._detect_fvgs(
			inf_fvg, 
			float(self.fvg_threshold.value), 
			self.auto_threshold, 
			int(self.fvg_extend.value)
		)
		
		# Debug: Log FVG detection
		import logging
		logger = logging.getLogger(__name__)
		bull_fvgs_detected = inf_fvg['bull_fvg'].sum()
		bear_fvgs_detected = inf_fvg['bear_fvg'].sum()
		active_bull = (inf_fvg['bull_fvg_max'] > 0).sum()
		active_bear = (inf_fvg['bear_fvg_max'] > 0).sum()
		logger.info(f"{pair} FVG Detection ({self.fvg_timeframe}): {bull_fvgs_detected} bullish, {bear_fvgs_detected} bearish FVGs found | "
				   f"Active: {active_bull} bull zones, {active_bear} bear zones")

		# Extend FVGs forward: forward-fill custom active windows
		extend = int(self.fvg_extend.value)
		# Create columns marking active ranges
		for col in ['fvg_bull_top', 'fvg_bull_bottom', 'fvg_bear_top', 'fvg_bear_bottom']:
			# Nothing extra; will ffill after merge
			pass

		# Merge informative pairs safely
		dataframe = merge_informative_pair(dataframe, inf_rsi, self.timeframe, self.rsi_timeframe, ffill=True)
		dataframe = merge_informative_pair(dataframe, inf_fvg, self.timeframe, self.fvg_timeframe, ffill=True)

		# Create plottable FVG columns on base timeframe
		# These will show the FVG zones from 15m on the 3m chart
		dataframe['fvg_bull_top'] = dataframe[f'bull_fvg_max_{self.fvg_timeframe}']
		dataframe['fvg_bull_bottom'] = dataframe[f'bull_fvg_min_{self.fvg_timeframe}']
		dataframe['fvg_bear_top'] = dataframe[f'bear_fvg_max_{self.fvg_timeframe}']
		dataframe['fvg_bear_bottom'] = dataframe[f'bear_fvg_min_{self.fvg_timeframe}']
		
		# Get in_bull_fvg and in_bear_fvg from merged data
		dataframe['in_bull_fvg'] = dataframe[f'in_bull_fvg_{self.fvg_timeframe}'].fillna(0).astype(int)
		dataframe['in_bear_fvg'] = dataframe[f'in_bear_fvg_{self.fvg_timeframe}'].fillna(0).astype(int)

		# Base timeframe RSI
		dataframe['rsi_base'] = ta.RSI(dataframe, timeperiod=int(self.rsi_period.value))

		# --- Divergence detection (using merged RSI timeframe pivots) ---
		# Collect recent pivot lows/highs
		max_range_minutes = int(self.max_lookback.value) * timeframe_to_minutes(self.rsi_timeframe)
		min_range_minutes = int(self.min_lookback.value) * timeframe_to_minutes(self.rsi_timeframe)
		# Synchronize expiry with max_lookback to keep divergence active as long as pivots are tracked
		expiry_minutes = int(self.divergence_expiry.value) * timeframe_to_minutes(self.rsi_timeframe)

		dataframe['bull_div'] = 0
		dataframe['bear_div'] = 0

		# We'll iterate through RSI timeframe pivot signals mapped onto base timeframe rows (vectorization complex due to multi-timeframe)
		# Simpler loop (acceptable given small timeframe) - optimize later if needed.
		price_piv = 'price_pivot_' + self.rsi_timeframe
		rsi_piv = 'rsi_pivot_' + self.rsi_timeframe
		rsi_col = 'rsi_' + self.rsi_timeframe  # merged column has suffix
		last_bull_div_time: Optional[pd.Timestamp] = None
		last_bear_div_time: Optional[pd.Timestamp] = None
		# Store synchronized pivots: both price AND RSI pivot at same time
		recent_pivot_lows: List[Dict[str, Any]] = []  # {'t': timestamp, 'price': float, 'rsi': float}
		recent_pivot_highs: List[Dict[str, Any]] = []

		for idx, row in dataframe.iterrows():
			t = row['date']
			# Maintain sliding windows (drop outside max range)
			cutoff = t - pd.Timedelta(minutes=max_range_minutes)
			recent_pivot_lows = [x for x in recent_pivot_lows if x['t'] >= cutoff]
			recent_pivot_highs = [x for x in recent_pivot_highs if x['t'] >= cutoff]
			
			# Register pivots when EITHER price OR RSI pivot (more flexible)
			# Store both price and RSI values at pivot points
			if row.get(price_piv) == -1 or row.get(rsi_piv) == -1:
				# Only add if this is a distinct pivot (not duplicate)
				if not recent_pivot_lows or recent_pivot_lows[-1]['t'] != t:
					recent_pivot_lows.append({
						't': t,
						'price': row['close_' + self.rsi_timeframe],
						'rsi': row[rsi_col]
					})
			if row.get(price_piv) == 1 or row.get(rsi_piv) == 1:
				# Only add if this is a distinct pivot (not duplicate)
				if not recent_pivot_highs or recent_pivot_highs[-1]['t'] != t:
					recent_pivot_highs.append({
						't': t,
						'price': row['close_' + self.rsi_timeframe],
						'rsi': row[rsi_col]
					})

			# Need at least two synchronized pivots for divergence
			if len(recent_pivot_lows) >= 2:
				latest = recent_pivot_lows[-1]
				prev = recent_pivot_lows[-2]
				span_minutes = (latest['t'] - prev['t']).total_seconds() / 60
				# Bullish divergence: price makes lower low, RSI makes higher low
				if latest['price'] < prev['price'] and latest['rsi'] > prev['rsi'] and span_minutes >= min_range_minutes:
					dataframe.at[idx, 'bull_div'] = 1
					last_bull_div_time = t
			
			if len(recent_pivot_highs) >= 2:
				latest = recent_pivot_highs[-1]
				prev = recent_pivot_highs[-2]
				span_minutes = (latest['t'] - prev['t']).total_seconds() / 60
				# Bearish divergence: price makes higher high, RSI makes lower high
				if latest['price'] > prev['price'] and latest['rsi'] < prev['rsi'] and span_minutes >= min_range_minutes:
					dataframe.at[idx, 'bear_div'] = 1
					last_bear_div_time = t

			# Set active flags immediately (will be used in combined loop below)
			dataframe.at[idx, 'bull_div_active'] = 1 if last_bull_div_time and (t - last_bull_div_time).total_seconds()/60 <= expiry_minutes else 0
			dataframe.at[idx, 'bear_div_active'] = 1 if last_bear_div_time and (t - last_bear_div_time).total_seconds()/60 <= expiry_minutes else 0
		
		# Debug: Log divergence detection summary
		bull_divs = (dataframe['bull_div'] == 1).sum()
		bear_divs = (dataframe['bear_div'] == 1).sum()
		bull_div_active_count = (dataframe['bull_div_active'] == 1).sum()
		bear_div_active_count = (dataframe['bear_div_active'] == 1).sum()
		logger.info(f"{pair} Divergence Detection ({self.rsi_timeframe}): {bull_divs} bull divs, {bear_divs} bear divs detected | "
				   f"Active: {bull_div_active_count} bull, {bear_div_active_count} bear")

		# Debug: Log FVG status on base timeframe
		in_bull_fvg_count = (dataframe['in_bull_fvg'] == 1).sum()
		in_bear_fvg_count = (dataframe['in_bear_fvg'] == 1).sum()
		logger.info(f"{pair} Base Timeframe FVG Status: {in_bull_fvg_count} candles in bull FVG, {in_bear_fvg_count} candles in bear FVG")
		
		# Cache hash for next call
		if len(dataframe) > 0:
			self.custom_info[pair]['last_indicator_hash'] = (dataframe.iloc[-1]['date'], len(dataframe))

		# --- Engulfing signal at base timeframe ---
		dataframe['bull_engulf'] = 0
		dataframe['bear_engulf'] = 0
		dataframe['engulf_swing_low'] = 0.0  # For long entries: low of candle before engulfing
		dataframe['engulf_swing_high'] = 0.0  # For short entries: high of candle before engulfing
		for i in range(1, len(dataframe)):
			cur = dataframe.iloc[i]
			prev = dataframe.iloc[i-1]
			bull, bear = self._engulfing(cur, prev)
			if bull:
				dataframe.at[dataframe.index[i], 'bull_engulf'] = 1
				# Store swing low (low of previous candle) for stoploss
				dataframe.at[dataframe.index[i], 'engulf_swing_low'] = prev['low']
			if bear:
				dataframe.at[dataframe.index[i], 'bear_engulf'] = 1
				# Store swing high (high of previous candle) for stoploss
				dataframe.at[dataframe.index[i], 'engulf_swing_high'] = prev['high']
		
		# Debug: Log engulfing patterns
		bull_engulf_count = (dataframe['bull_engulf'] == 1).sum()
		bear_engulf_count = (dataframe['bear_engulf'] == 1).sum()
		logger.info(f"{pair} Engulfing Patterns ({self.timeframe}): {bull_engulf_count} bullish, {bear_engulf_count} bearish")

		return dataframe

	def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
		# Long entry conditions: divergence + engulfing + price touches FVG
		dataframe.loc[
			(
				(dataframe['bull_div_active'] == 1) &
				(dataframe['bull_engulf'] == 1) &
				(dataframe['in_bull_fvg'] == 1) &
				(dataframe['volume'] > 0)
			),
			['enter_long', 'enter_tag']
		] = (1, 'bull_div_fvg_engulf')

		# Short entry conditions: divergence + engulfing + price touches FVG
		dataframe.loc[
			(
				(dataframe['bear_div_active'] == 1) &
				(dataframe['bear_engulf'] == 1) &
				(dataframe['in_bear_fvg'] == 1) &
				(dataframe['volume'] > 0)
			),
			['enter_short', 'enter_tag']
		] = (1, 'bear_div_fvg_engulf')

		# Debug: Log comprehensive entry analysis
		if len(dataframe) > 0:
			pair = metadata.get('pair', 'unknown')
			import logging
			logger = logging.getLogger(__name__)
			
			# Count signals in entire dataframe
			bull_div_count = (dataframe['bull_div_active'] == 1).sum()
			bear_div_count = (dataframe['bear_div_active'] == 1).sum()
			bull_engulf_count = (dataframe['bull_engulf'] == 1).sum()
			bear_engulf_count = (dataframe['bear_engulf'] == 1).sum()
			bull_fvg_count = (dataframe['in_bull_fvg'] == 1).sum()
			bear_fvg_count = (dataframe['in_bear_fvg'] == 1).sum()
			long_entries = (dataframe['enter_long'] == 1).sum() if 'enter_long' in dataframe.columns else 0
			short_entries = (dataframe['enter_short'] == 1).sum() if 'enter_short' in dataframe.columns else 0
						
			logger.info(f"\n{'='*80}")
			logger.info(f"{pair} ENTRY ANALYSIS")
			logger.info(f"{'='*80}")
			logger.info(f"Individual Conditions:")
			logger.info(f"  Bull Divergence Active: {bull_div_count} candles")
			logger.info(f"  Bear Divergence Active: {bear_div_count} candles")
			logger.info(f"  Bull Engulfing: {bull_engulf_count} candles")
			logger.info(f"  Bear Engulfing: {bear_engulf_count} candles")
			logger.info(f"  In Bull FVG: {bull_fvg_count} candles")
			logger.info(f"  In Bear FVG: {bear_fvg_count} candles")
			logger.info(f"\nFinal Entries:")
			logger.info(f"  Long Entries: {long_entries}")
			logger.info(f"  Short Entries: {short_entries}")
			logger.info(f"{'='*80}\n")

		return dataframe

	def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
		# Exit on opposite RSI divergence signal (on base 3m timeframe)
		dataframe['exit_long'] = 0
		dataframe['exit_short'] = 0
		dataframe['exit_tag'] = ''
		
		# Exit long positions when bearish divergence appears
		dataframe.loc[
			(dataframe['bear_div_active'] == 1),
			['exit_long', 'exit_tag']
		] = (1, 'bear_divergence_signal')
		
		# Exit short positions when bullish divergence appears
		dataframe.loc[
			(dataframe['bull_div_active'] == 1),
			['exit_short', 'exit_tag']
		] = (1, 'bull_divergence_signal')
		
		return dataframe

	# --- Custom stoploss: structure-based initial, then breakeven at 1.5R, trailing at 2R/3R levels ---
	def custom_stoploss(self, pair: str, trade: Trade, current_time: datetime, current_rate: float,
						current_profit: float, after_fill: bool, **kwargs) -> float | None:
		"""
		Stoploss logic:
		- Initial stop: swing low (long) or swing high (short) of candle before engulfing
		- Move to breakeven (0% profit) when price reaches 1.5R
		- Trail stop at 2R when price reaches 3R
		- Trail stop at 3R when price reaches 4R
		"""
		# Use trade open_date_utc as unique identifier
		trade_key = trade.open_date_utc.isoformat()
		
		# On first call after fill, check if we have pending_entry and transfer it immediately
		if after_fill and pair in self.custom_info and self.custom_info[pair].get('pending_entry'):
			if trade_key not in self.custom_info[pair].get('trade_stoplosses', {}):
				if 'trade_stoplosses' not in self.custom_info[pair]:
					self.custom_info[pair]['trade_stoplosses'] = {}
				self.custom_info[pair]['trade_stoplosses'][trade_key] = self.custom_info[pair]['pending_entry']
				self.custom_info[pair]['pending_entry'] = None
		
		# Get structure-based stoploss for this specific trade
		initial_sl = abs(self.stoploss)  # Default
		structure_sl = None
		if pair in self.custom_info and 'trade_stoplosses' in self.custom_info[pair]:
			trade_sl_info = self.custom_info[pair]['trade_stoplosses'].get(trade_key)
			if trade_sl_info:
				initial_sl = abs(trade_sl_info['sl'])
				structure_sl = trade_sl_info['sl']
		
		# Evaluate highest to lowest, so that highest possible stop is used
		# At 4R profit, trail stop at 3R (lock in 3R profit)
		if current_profit > initial_sl * 4.0:
			return stoploss_from_open(initial_sl * 3.0, current_profit, is_short=trade.is_short, leverage=trade.leverage)
		
		# At 3R profit, trail stop at 2R (lock in 2R profit)
		elif current_profit > initial_sl * 3.0:
			return stoploss_from_open(initial_sl * 2.0, current_profit, is_short=trade.is_short, leverage=trade.leverage)
		
		# At 1.5R profit, move to breakeven (lock in entry price)
		elif current_profit > initial_sl * 1.5:
			return stoploss_from_open(0.001, current_profit, is_short=trade.is_short, leverage=trade.leverage)
		
		# Below 1.5R, use structure-based initial stoploss if available
		if structure_sl is not None:
			return structure_sl
		
		# Return base stoploss as fallback
		return self.stoploss

	# Transfer pending entry info to active trade tracking
	def adjust_trade_position(self, trade: Trade, current_time: datetime, current_rate: float,
							  current_profit: float, min_stake: Optional[float],
							  max_stake: float, current_entry_rate: float, current_exit_rate: float,
							  current_entry_profit: float, current_exit_profit: float, **kwargs) -> float | None:
		"""
		Called on first iteration after trade is opened.
		Transfer pending_entry info to trade_stoplosses dict using trade.open_date_utc as key.
		"""
		pair = trade.pair
		trade_key = trade.open_date_utc.isoformat()
		
		if pair in self.custom_info and self.custom_info[pair].get('pending_entry'):
			# Transfer pending entry to trade_stoplosses
			if trade_key not in self.custom_info[pair]['trade_stoplosses']:
				self.custom_info[pair]['trade_stoplosses'][trade_key] = self.custom_info[pair]['pending_entry']
				# Clear pending entry after transfer
				self.custom_info[pair]['pending_entry'] = None
				
				# Cleanup old trades (keep only last 20 to prevent memory bloat)
				if len(self.custom_info[pair]['trade_stoplosses']) > 20:
					# Keep only the most recent 20 trades
					sorted_keys = sorted(self.custom_info[pair]['trade_stoplosses'].keys())[-20:]
					self.custom_info[pair]['trade_stoplosses'] = {
						k: v for k, v in self.custom_info[pair]['trade_stoplosses'].items() if k in sorted_keys
					}
		
		return None  # No position adjustment, just using this for bookkeeping
	
	# Optional custom leverage (for futures mode)
	def leverage(self, pair: str, current_time: datetime, current_rate: float,
				 proposed_leverage: float, max_leverage: float, side: str, **kwargs) -> float:
		return min(1.0, max_leverage)  # modest leverage

	# Filters out invalid candles and prevents re-entry after breakeven exit
	def confirm_trade_entry(self, pair: str, order_type: str, amount: float, rate: float,
							time_in_force: str, current_time: datetime, entry_tag: Optional[str] = None, **kwargs):
		if rate <= 0:
			return False
		
		# Get the current dataframe to find swing structure for stoploss
		dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
		if len(dataframe) > 0:
			last_candle = dataframe.iloc[-1]
			
			# Determine if this is a long or short entry based on entry_tag
			is_long = entry_tag and 'bull' in entry_tag.lower()
			is_short = entry_tag and 'bear' in entry_tag.lower()
			
			# Calculate structure-based stoploss
			if is_long and last_candle.get('engulf_swing_low', 0) > 0:
				# Long entry: stop below the swing low (candle before engulfing)
				swing_low = last_candle['engulf_swing_low']
				stoploss_pct = (swing_low - rate) / rate  # Negative value
				# Store in pending_entry - will be moved to trade_stoplosses in adjust_trade_position
				if pair not in self.custom_info:
					self.custom_info[pair] = {'trade_stoplosses': {}, 'breakeven_exits': set(), 'pending_entry': None}
				self.custom_info[pair]['pending_entry'] = {
					'sl': stoploss_pct,
					'swing_price': swing_low,
					'entry_time': current_time,
					'entry_rate': rate
				}
				
			elif is_short and last_candle.get('engulf_swing_high', 0) > 0:
				# Short entry: stop above the swing high (candle before engulfing)
				swing_high = last_candle['engulf_swing_high']
				stoploss_pct = (rate - swing_high) / rate  # Negative value for short
				if pair not in self.custom_info:
					self.custom_info[pair] = {'trade_stoplosses': {}, 'breakeven_exits': set(), 'pending_entry': None}
				self.custom_info[pair]['pending_entry'] = {
					'sl': stoploss_pct,
					'swing_price': swing_high,
					'entry_time': current_time,
					'entry_rate': rate
				}
		
		# Check if we recently exited at breakeven for this pair
		# Allow re-entry after sufficient time has passed (e.g., 1 hour)
		if pair in self.custom_info:
			breakeven_exits = self.custom_info[pair].get('breakeven_exits', set())
			# Clean up old breakeven exit records (older than 1 hour)
			# Since we only have trade IDs, we'll allow re-entry after clearing the set periodically
			# This is a simplified approach - in production you'd track timestamps
			if len(breakeven_exits) > 0:
				# Clear breakeven exits older than a threshold
				# For now, limit to last 5 trades to prevent memory bloat
				if len(breakeven_exits) > 5:
					self.custom_info[pair]['breakeven_exits'] = set()
		
		return True

	def custom_exit(self, pair: str, trade: Trade, current_time: datetime, current_rate: float,
					current_profit: float, **kwargs) -> str | None:
		"""
		Custom exit logic with descriptive tags.
		Returns exit signal name if trade should exit, None otherwise.
		This should NOT force exits - let custom_stoploss handle stop management.
		Only use this for profit targets or special exit conditions.
		"""
		trade_key = trade.open_date_utc.isoformat()
		
		# Get initial SL for R-multiple calculations
		initial_sl = abs(self.stoploss)
		if pair in self.custom_info and 'trade_stoplosses' in self.custom_info[pair]:
			trade_sl_info = self.custom_info[pair]['trade_stoplosses'].get(trade_key)
			if trade_sl_info:
				initial_sl = abs(trade_sl_info['sl'])
		
		# Optional: Force exit at extreme profit levels (e.g., 5R)
		if current_profit >= initial_sl * 5.0:
			return f'profit_target_5R_{current_profit:.1%}'
		
		# Don't force any exits based on stop levels - let custom_stoploss handle that
		# The stoploss will naturally cause exits when hit, with proper exit_reason
		return None
	
	def confirm_trade_exit(self, pair: str, trade, order_type: str, amount: float, rate: float,
							time_in_force: str, exit_reason: str, current_time: datetime, **kwargs) -> bool:
		return True

