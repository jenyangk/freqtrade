import numpy as np
import pandas as pd
import pandas_ta as pta
import talib.abstract as ta
from pandas import DataFrame
from freqtrade.strategy import IStrategy, merge_informative_pair
from freqtrade.persistence import Trade
from datetime import datetime

class TrendFollowingMeanReversion(IStrategy):
    """
    TrendFollowingMeanReversion (TFMR)
    
    An evolution of MeanReversionRSI designed to capture pullbacks 
    within strong trends identified on higher timeframes.
    
    Logic:
    - Trend: EMA 20 > EMA 50 > EMA 200 and ADX > 25.
    - Pullback: Low price touches or goes below EMA 20.
    - Entry: Close price is above EMA 20 (Rejection of the pullback).
    - Exit: Close price crosses opposite EMA 20 or BB Upper/Lower.
    - Stop Loss: 4 * ATR.
    """
    INTERFACE_VERSION = 3

    timeframe = '1h'
    minimal_roi = {"0": 100}
    stoploss = -0.10
    use_custom_stoploss = True
    trailing_stop = False
    process_only_new_candles = True
    startup_candle_count = 200

    order_types = {
        'entry': 'limit',
        'exit': 'limit',
        'stoploss': 'market',
        'stoploss_on_exchange': False
    }

    def custom_stoploss(self, pair: str, trade: 'Trade', current_time: datetime,
                        current_rate: float, current_profit: float, **kwargs) -> float:
        """
        ATR-based stoploss (4 * ATR).
        """
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
            
        sl_distance = 4 * atr
        
        if trade.is_short:
            sl_price = trade.open_rate + sl_distance
            return (sl_price / current_rate) - 1 if current_rate > 0 else self.stoploss
        else:
            sl_price = trade.open_rate - sl_distance
            return (sl_price / current_rate) - 1 if current_rate > 0 else self.stoploss

    def leverage(self, pair: str, current_time: datetime, current_rate: float,
                 proposed_leverage: float, max_leverage: float, entry_tag: str, side: str,
                 **kwargs) -> float:
        return 5.0

    def custom_stake_amount(self, pair: str, current_time: datetime, current_rate: float,
                            proposed_stake: float, min_stake: float, max_stake: float,
                            leverage: float, entry_tag: str, side: str,
                            **kwargs) -> float:
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(dataframe) == 0:
            return proposed_stake
            
        last_candle = dataframe.iloc[-1]
        atr = last_candle['atr']
        
        if atr == 0:
            return proposed_stake
            
        risk_pct = 0.02
        total_capital = self.wallets.get_total_stake_amount()
        risk_amount = total_capital * risk_pct
        sl_distance = 4 * atr
        position_size = risk_amount * current_rate / sl_distance
        
        if leverage:
            return position_size / leverage
        return position_size

    def informative_pairs(self):
        pairs = self.dp.current_whitelist()
        return [(pair, '4h') for pair in pairs]

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # 1. Base Indicators
        dataframe['adx'] = ta.ADX(dataframe, timeperiod=14)
        dataframe['atr'] = ta.ATR(dataframe, timeperiod=14)
        dataframe['ema20'] = ta.EMA(dataframe, timeperiod=20)
        dataframe['ema50'] = ta.EMA(dataframe, timeperiod=50)
        dataframe['ema200'] = ta.EMA(dataframe, timeperiod=200)
        
        bollinger = ta.BBANDS(dataframe, timeperiod=20, nbdevup=2.0, nbdevdn=2.0)
        dataframe['bb_upperband'] = bollinger['upperband']
        dataframe['bb_lowerband'] = bollinger['lowerband']
        
        # 2. Informative Indicators (4h)
        informative = self.dp.get_pair_dataframe(pair=metadata['pair'], timeframe='4h')
        informative['rsi'] = ta.RSI(informative, timeperiod=14)
        informative['adx'] = ta.ADX(informative, timeperiod=14)
        
        st = pta.supertrend(informative['high'], informative['low'], informative['close'], length=10, multiplier=3)
        st_dir_col = [col for col in st.columns if col.startswith('SUPERTd')][0]
        informative['supertrend'] = st[st_dir_col]

        dataframe = merge_informative_pair(dataframe, informative, self.timeframe, '4h', ffill=True)

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        conditions_long = (
            (dataframe['ema20'] > dataframe['ema50']) &
            (dataframe['ema50'] > dataframe['ema200']) &
            (dataframe['adx'] > 25) &
            (dataframe['low'] <= dataframe['ema20']) &
            (dataframe['close'] > dataframe['ema20'])
        )
        
        conditions_short = (
            (dataframe['ema20'] < dataframe['ema50']) &
            (dataframe['ema50'] < dataframe['ema200']) &
            (dataframe['adx'] > 25) &
            (dataframe['high'] >= dataframe['ema20']) &
            (dataframe['close'] < dataframe['ema20'])
        )

        dataframe.loc[conditions_long, 'enter_long'] = 1
        dataframe.loc[conditions_short, 'enter_short'] = 1

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[(dataframe['close'] >= dataframe['bb_upperband']), 'exit_long'] = 1
        dataframe.loc[(dataframe['close'] <= dataframe['bb_lowerband']), 'exit_short'] = 1
        return dataframe

