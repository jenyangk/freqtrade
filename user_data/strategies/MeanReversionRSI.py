import numpy as np
import pandas as pd
import pandas_ta as pta
import talib.abstract as ta
from pandas import DataFrame
from freqtrade.strategy import IStrategy, merge_informative_pair
from freqtrade.persistence import Trade
from datetime import datetime

class MeanReversionRSI(IStrategy):
    """
    MeanReversionRSI Strategy based on Jesse Trade strategy.
    
    Logic:
    - Long when RSI(4h) > 70, Supertrend(4h) is UP, ADX > 20, ADX(4h) > 40.
    - Short when RSI(4h) < 30, Supertrend(4h) is DOWN, ADX > 20, ADX(4h) > 40.
    - Entry: Limit order at BB Lower (Long) or BB Upper (Short).
    - Exit: Take Profit at BB Upper (Long) or BB Lower (Short).
    - Stop Loss: Fixed at Entry +/- 6 * ATR.
    """
    INTERFACE_VERSION = 3

    # Timeframe
    timeframe = '1h'

    # ROI table: We use dynamic exit, so we set this to infinity
    minimal_roi = {
        "0": 100
    }

    # Stoploss: We use custom stoploss
    stoploss = -0.99

    # Trailing stop:
    trailing_stop = False

    # Run "populate_indicators" only for new candle
    process_only_new_candles = True

    # These values can be overridden in the "ask_strategy" section in the config.
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    # Number of candles the strategy requires before producing valid signals
    startup_candle_count = 100

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
    
    # Custom dictionary to store trade-specific data (like fixed SL)
    custom_info = {}

    # Plot configuration
    plot_config = {
        'main_plot': {
            'bb_upperband': {'color': 'blue'},
            'bb_middleband': {'color': 'orange'},
            'bb_lowerband': {'color': 'blue'},
        },
        'subplots': {
            "RSI": {
                'rsi_4h': {'color': 'red'},
            },
            "ADX": {
                'adx': {'color': 'green'},
                'adx_4h': {'color': 'purple'},
            },
            "Supertrend": {
                'supertrend_4h': {'color': 'yellow'},
            }
        }
    }

    def leverage(self, pair: str, current_time: datetime, current_rate: float,
                 proposed_leverage: float, max_leverage: float, entry_tag: str, side: str,
                 **kwargs) -> float:
        """
        Use 5x leverage as per strategy description.
        """
        return 5.0

    def custom_stake_amount(self, pair: str, current_time: datetime, current_rate: float,
                            proposed_stake: float, min_stake: float, max_stake: float,
                            leverage: float, entry_tag: str, side: str,
                            **kwargs) -> float:
        """
        Calculate stake amount to risk 3% of total capital per trade.
        """
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(dataframe) == 0:
            return proposed_stake
            
        last_candle = dataframe.iloc[-1]
        atr = last_candle['atr']
        
        if atr == 0:
            return proposed_stake
            
        # Risk 3% of total capital
        risk_pct = 0.03
        total_capital = self.wallets.get_total_stake_amount()
        risk_amount = total_capital * risk_pct
        
        # Stoploss is 6 * ATR
        sl_distance = 7.424 * atr
        
        # Calculate position size such that if SL is hit, loss is risk_amount
        # Loss = Position_Size * (SL_Distance / Price)
        # Position_Size = Risk_Amount / (SL_Distance / Price)
        # Position_Size = Risk_Amount * Price / SL_Distance
        
        # We use current_rate as approximation of entry price
        position_size = risk_amount * current_rate / sl_distance
        
        # Adjust for leverage
        # Freqtrade 'stake_amount' is the margin amount.
        # Actual Position Size = stake_amount * leverage
        # So stake_amount = position_size / leverage
        
        if leverage:
            return position_size / leverage
        else:
            return position_size

    def informative_pairs(self):
        pairs = self.dp.current_whitelist()
        return [(pair, '4h') for pair in pairs]

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # 1. Calculate indicators for current timeframe
        dataframe['adx'] = ta.ADX(dataframe, timeperiod=14)
        dataframe['atr'] = ta.ATR(dataframe, timeperiod=14)
        
        bollinger = ta.BBANDS(dataframe, timeperiod=19, nbdevup=2.273, nbdevdn=2.273, matype=0)
        dataframe['bb_upperband'] = bollinger['upperband']
        dataframe['bb_middleband'] = bollinger['middleband']
        dataframe['bb_lowerband'] = bollinger['lowerband']
        
        # BB Width (optional, but good for debugging)
        dataframe['bbw'] = (dataframe['bb_upperband'] - dataframe['bb_lowerband']) / dataframe['bb_middleband']

        # 2. Calculate indicators for informative timeframe (4h)
        informative = self.dp.get_pair_dataframe(pair=metadata['pair'], timeframe='4h')
        
        # RSI 4h
        informative['rsi'] = ta.RSI(informative, timeperiod=14)
        
        # ADX 4h
        informative['adx'] = ta.ADX(informative, timeperiod=14)
        
        # Supertrend 4h
        # Jesse uses default supertrend: period=10, multiplier=3.
        # We use pandas_ta which defaults to the same logic (ATR-based).
        st = pta.supertrend(informative['high'], informative['low'], informative['close'], length=10, multiplier=3)
        
        # pta.supertrend returns columns like SUPERT_10_3.0, SUPERTd_10_3.0, etc.
        # We need the direction (1 or -1). We dynamically find the column starting with SUPERTd.
        st_dir_col = [col for col in st.columns if col.startswith('SUPERTd')][0]
        informative['supertrend'] = st[st_dir_col]

        # Merge informative
        dataframe = merge_informative_pair(dataframe, informative, self.timeframe, '4h', ffill=True)

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # Informative columns have suffix _4h
        
        conditions_long = (
            (dataframe['rsi_4h'] > 65) &
            (dataframe['supertrend_4h'] == 1) & # Uptrend
            (dataframe['adx'] > 10) &
            (dataframe['adx_4h'] > 45)
        )
        
        conditions_short = (
            (dataframe['rsi_4h'] < 30) &
            (dataframe['supertrend_4h'] == -1) & # Downtrend
            (dataframe['adx'] > 10) &
            (dataframe['adx_4h'] > 45)
        )

        dataframe.loc[conditions_long, 'enter_long'] = 1
        dataframe.loc[conditions_short, 'enter_short'] = 1

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # Exit logic:
        # Long: Take profit at BB Upper
        # Short: Take profit at BB Lower
        
        # Signal exit if price crosses the band
        dataframe.loc[
            (dataframe['close'] >= dataframe['bb_upperband']),
            'exit_long'
        ] = 1

        dataframe.loc[
            (dataframe['close'] <= dataframe['bb_lowerband']),
            'exit_short'
        ] = 1
        
        return dataframe

    def custom_entry_price(self, pair: str, current_time: datetime, proposed_rate: float,
                           entry_tag: str, side: str, **kwargs) -> float:
        # Jesse: 
        # Long Entry: BB Lower
        # Short Entry: BB Upper
        
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        last_candle = dataframe.iloc[-1].squeeze()
        
        if side == 'long':
            return last_candle['bb_lowerband']
        else:
            return last_candle['bb_upperband']

    def confirm_trade_entry(self, pair: str, order_type: str, amount: float, rate: float,
                            time_in_force: str, current_time: datetime, entry_tag: str,
                            side: str, **kwargs) -> bool:
        # Store the ATR-based SL price for this trade
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(dataframe) > 0:
            last_candle = dataframe.iloc[-1]
            atr = last_candle['atr']
            
            # Jesse: stop_loss_price = entry_price - (self.atr * 6)
            # We use 'rate' as entry price
            
            sl_price = 0.0
            if side == "long":
                sl_price = rate - (atr * 7.424)
            else:
                sl_price = rate + (atr * 7.424)

            if pair not in self.custom_info:
                self.custom_info[pair] = {}

            self.custom_info[pair]['next_sl_price'] = sl_price

        return True

    def custom_stoploss(self, pair: str, trade: Trade, current_time: datetime,
                        current_rate: float, current_profit: float, **kwargs) -> float:
        
        # Retrieve stored SL price
        if pair in self.custom_info and 'next_sl_price' in self.custom_info[pair]:
             sl_price = self.custom_info[pair]['next_sl_price']
             
             if trade.is_short:
                 # Short: SL is above price.
                 diff = sl_price - current_rate
                 if diff < 0: 
                     return -0.01 # Default if something wrong
                 
                 return -(diff / current_rate)
                 
             else:
                 # Long: SL is below price.
                 diff = current_rate - sl_price
                 if diff < 0:
                     return -0.01
                 
                 return -(diff / current_rate)
                 
        return self.stoploss
