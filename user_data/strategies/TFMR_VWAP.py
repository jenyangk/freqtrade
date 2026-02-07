import numpy as np
import pandas as pd
import pandas_ta as pta
import talib.abstract as ta
from pandas import DataFrame
from freqtrade.strategy import IStrategy, merge_informative_pair
from freqtrade.persistence import Trade
from datetime import datetime

class TFMR_VWAP(IStrategy):
    """
    TFMR_VWAP: 15-minute strategy using VWAP and Volume.
    
    Logic:
    - Trend: 4H EMA 200.
    - Entry: Price is below VWAP (Long) or above VWAP (Short) 
             and shows a volume spike (> 1.5x average volume).
    - Exit: Price touches VWAP Upper/Lower bands (2 StdDev).
    - Stop Loss: ATR-based (5 * ATR).
    """
    INTERFACE_VERSION = 3

    timeframe = '15m'
    minimal_roi = {"0": 0.10}
    stoploss = -0.05
    use_custom_stoploss = True
    
    process_only_new_candles = True
    startup_candle_count = 200

    def custom_stoploss(self, pair: str, trade: 'Trade', current_time: datetime,
                        current_rate: float, current_profit: float, **kwargs) -> float:
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        trade_date = trade.open_date_utc
        if dataframe['date'].dt.tz is None and trade_date.tzinfo is not None:
            trade_date = trade_date.replace(tzinfo=None)
        trade_candle = dataframe.loc[dataframe['date'] <= trade_date]
        if trade_candle.empty:
            return self.stoploss
        last_candle = trade_candle.iloc[-1]
        atr = last_candle['atr']
        if atr == 0:
            return self.stoploss
        sl_distance = 5 * atr
        if trade.is_short:
            sl_price = trade.open_rate + sl_distance
            return (current_rate - sl_price) / current_rate if current_rate > 0 else self.stoploss
        else:
            sl_price = trade.open_rate - sl_distance
            return (sl_price - current_rate) / current_rate if current_rate > 0 else self.stoploss

    def leverage(self, pair: str, current_time: datetime, current_rate: float,
                 proposed_leverage: float, max_leverage: float, entry_tag: str, side: str,
                 **kwargs) -> float:
        return 5.0

    def informative_pairs(self):
        pairs = self.dp.current_whitelist()
        return [(pair, '4h') for pair in pairs]

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # 15m Indicators
        dataframe['atr'] = ta.ATR(dataframe, timeperiod=14)
        
        # VWAP (requires DatetimeIndex)
        df_vwap = dataframe.copy()
        df_vwap = df_vwap.set_index(pd.to_datetime(df_vwap['date'], unit='ms'))
        dataframe['vwap'] = pta.vwap(df_vwap['high'], df_vwap['low'], df_vwap['close'], df_vwap['volume']).values
        
        # VWAP Bands (Approximate using StdDev of price from VWAP)
        dataframe['vwap_std'] = dataframe['close'].rolling(window=20).std()
        dataframe['vwap_upper'] = dataframe['vwap'] + (dataframe['vwap_std'] * 2)
        dataframe['vwap_lower'] = dataframe['vwap'] - (dataframe['vwap_std'] * 2)
        
        # Volume Average
        dataframe['volume_avg'] = dataframe['volume'].rolling(window=20).mean()

        # 4h Informative
        informative = self.dp.get_pair_dataframe(pair=metadata['pair'], timeframe='4h')
        informative['ema200'] = ta.EMA(informative, timeperiod=200)
        
        dataframe = merge_informative_pair(dataframe, informative, self.timeframe, '4h', ffill=True)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        conditions_long = (
            (dataframe['close'] > dataframe['ema200_4h']) &
            (dataframe['close'] < dataframe['vwap']) &
            (dataframe['volume'] > dataframe['volume_avg'] * 1.5) &
            (dataframe['close'] > dataframe['open'])
        )
        
        conditions_short = (
            (dataframe['close'] < dataframe['ema200_4h']) &
            (dataframe['close'] > dataframe['vwap']) &
            (dataframe['volume'] > dataframe['volume_avg'] * 1.5) &
            (dataframe['close'] < dataframe['open'])
        )

        dataframe.loc[conditions_long, 'enter_long'] = 1
        dataframe.loc[conditions_short, 'enter_short'] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[(dataframe['close'] >= dataframe['vwap_upper']), 'exit_long'] = 1
        dataframe.loc[(dataframe['close'] <= dataframe['vwap_lower']), 'exit_short'] = 1
        return dataframe
