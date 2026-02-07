import numpy as np
import pandas as pd
import pandas_ta as pta
import talib.abstract as ta
from pandas import DataFrame
from typing import Optional
from freqtrade.strategy import IStrategy, merge_informative_pair, stoploss_from_absolute
from freqtrade.persistence import Trade
from datetime import datetime, timedelta
from hmmlearn.hmm import GaussianHMM
import logging

logger = logging.getLogger(__name__)

class MeanReversionRSIHMM(IStrategy):
    """
    MeanReversionRSIHMM Strategy
    
    Based on MeanReversionRSI but using HMM for regime detection.
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

    # Stoploss: We use a wide default and handle specific SL in custom_exit
    stoploss = -0.99
    use_custom_stoploss = False

    # Trailing stop:
    trailing_stop = False

    # Run "populate_indicators" only for new candle
    process_only_new_candles = True

    # These values can be overridden in the "ask_strategy" section in the config.
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    # Number of candles the strategy requires before producing valid signals
    startup_candle_count = 1000 # Increased for HMM stability

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
    
    # Trailing stop:
    trailing_stop = False # We will implement it in custom_stoploss
    use_kelly = True
    kelly_fraction = 0.3
    kelly_lookback = 30   # Number of trades to look back

    # Regime Detection Parameters
    use_regime_filter = True
    
    # Cache for market regime to avoid re-calculating for every pair
    _btc_regime_cache = None
    
    # Leverage
    leverage_value = 5.0
    
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
            },
            "Regime": {
                'market_regime': {'color': 'lightblue'},
            }
        }
    }

    def leverage(self, pair: str, current_time: datetime, current_rate: float,
                 proposed_leverage: float, max_leverage: float, entry_tag: str, side: str,
                 **kwargs) -> float:
        return self.leverage_value

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
            
        risk_pct = 0.04 # Default risk
        
        if self.use_kelly:
            trades = Trade.get_trades_proxy(is_open=False)
            if len(trades) >= self.kelly_lookback:
                last_trades = trades[-self.kelly_lookback:]
                wins = [t for t in last_trades if t.close_profit > 0]
                losses = [t for t in last_trades if t.close_profit <= 0]
                
                win_rate = len(wins) / len(last_trades)
                if len(losses) > 0 and len(wins) > 0:
                    avg_win = sum(t.close_profit for t in wins) / len(wins)
                    avg_loss = abs(sum(t.close_profit for t in losses) / len(losses))
                    
                    if avg_loss > 0:
                        profit_factor = avg_win / avg_loss
                        kelly_f = win_rate - (1 - win_rate) / profit_factor
                        risk_pct = max(0.01, min(0.05, kelly_f * self.kelly_fraction))
        
        total_capital = self.wallets.get_total_stake_amount()
        risk_amount = total_capital * risk_pct
        sl_distance = 6 * atr
        position_size = risk_amount * current_rate / sl_distance
        
        if leverage:
            return min(position_size / leverage, max_stake)
        else:
            return min(position_size, max_stake)

    def informative_pairs(self):
        pairs = self.dp.current_whitelist()
        informative = [(pair, '4h') for pair in pairs]
        btc_pair = "BTC/USDT"
        for p in pairs:
            if p.startswith("BTC/"):
                btc_pair = p
                break
        if (btc_pair, self.timeframe) not in informative:
            informative.append((btc_pair, self.timeframe))
        return informative

    def get_hmm_regime(self, dataframe: DataFrame) -> DataFrame:
        """
        Identify market regime using Hidden Markov Model with a rolling window.
        Features: Returns, Volatility, Range.
        """
        df = dataframe.copy()
        df['returns'] = np.log(df['close'] / df['close'].shift(1))
        df['volatility'] = df['returns'].rolling(window=20).std()
        df['range'] = (df['high'] - df['low']) / df['close']
        df = df.dropna()
        
        if len(df) < self.startup_candle_count:
            df = dataframe.copy()
            df['regime'] = 1
            return df[['date', 'regime']]
            
        features = df[['returns', 'volatility', 'range']].values
        
        window_size = 1000 # Increased window for more stable regimes
        regimes = np.zeros(len(df))
        regimes[:] = 1 
        step = 100
        
        for i in range(window_size, len(df), step):
            train_data = features[i-window_size:i]
            # Standardize features
            mean = np.mean(train_data, axis=0)
            std = np.std(train_data, axis=0)
            std[std == 0] = 1.0 
            train_data_std = (train_data - mean) / std
            
            model = GaussianHMM(n_components=3, covariance_type="diag", n_iter=100, random_state=42)
            try:
                model.fit(train_data_std)
                future_idx = min(i + step, len(df))
                future_data = features[i:future_idx]
                if len(future_data) > 0:
                    future_data_std = (future_data - mean) / std
                    pred = model.predict(future_data_std)
                    
                    # Map regimes based on mean returns
                    means = model.means_[:, 0]
                    sorted_indices = np.argsort(means)
                    mapping = {sorted_indices[0]: 0, sorted_indices[1]: 1, sorted_indices[2]: 2}
                    regimes[i:future_idx] = [mapping[p] for p in pred]
            except Exception as e:
                logger.error(f"HMM Fit Error: {e}")
                continue
                
        df['regime'] = regimes
        return df[['date', 'regime']]

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe['adx'] = ta.ADX(dataframe, timeperiod=14)
        dataframe['rsi'] = ta.RSI(dataframe, timeperiod=14)
        dataframe['atr'] = ta.ATR(dataframe, timeperiod=14)
        dataframe['ema_200'] = ta.EMA(dataframe, timeperiod=200)
        dataframe['ema_50'] = ta.EMA(dataframe, timeperiod=50)
        
        bollinger = ta.BBANDS(dataframe, timeperiod=20, nbdevup=2.0, nbdevdn=2.0)
        dataframe['bb_upperband'] = bollinger['upperband']
        dataframe['bb_middleband'] = bollinger['middleband']
        dataframe['bb_lowerband'] = bollinger['lowerband']
        
        dataframe['bbw'] = (dataframe['bb_upperband'] - dataframe['bb_lowerband']) / dataframe['bb_middleband']
        dataframe['bbw_sma'] = dataframe['bbw'].rolling(window=50).mean()
        
        # Z-Score
        dataframe['sma_20'] = dataframe['close'].rolling(window=20).mean()
        dataframe['stddev_20'] = dataframe['close'].rolling(window=20).std()
        dataframe['zscore'] = (dataframe['close'] - dataframe['sma_20']) / dataframe['stddev_20']
        
        dataframe['volume_sma'] = dataframe['volume'].rolling(window=20).mean()
        
        # ROC 24h
        dataframe['roc_24h'] = ta.ROC(dataframe, timeperiod=24)

        informative = self.dp.get_pair_dataframe(pair=metadata['pair'], timeframe='4h')
        informative['rsi'] = ta.RSI(informative, timeperiod=14)
        informative['adx'] = ta.ADX(informative, timeperiod=14)
        
        st = pta.supertrend(informative['high'], informative['low'], informative['close'], length=10, multiplier=3)
        st_dir_col = [col for col in st.columns if col.startswith('SUPERTd')][0]
        informative['supertrend'] = st[st_dir_col]

        dataframe = merge_informative_pair(dataframe, informative, self.timeframe, '4h', ffill=True)

        # Clear cache if we are starting a new analysis (e.g. in lookahead-analysis)
        if len(dataframe) < self.startup_candle_count + 100:
            self._btc_regime_cache = None

        if self.use_regime_filter:
            if self._btc_regime_cache is None:
                # Try multiple possible BTC pair names
                for btc_pair in ["BTC/USDT:USDT", "BTC/USDT", "BTC/BUSD"]:
                    btc_df = self.dp.get_pair_dataframe(pair=btc_pair, timeframe='1h')
                    if not btc_df.empty:
                        logger.info(f"Found BTC data for {btc_pair}")
                        self._btc_regime_cache = self.get_hmm_regime(btc_df)
                        break
                
                if self._btc_regime_cache is None:
                    logger.warning("Could not find BTC data for regime detection!")
            
            if self._btc_regime_cache is not None:
                dataframe = pd.merge(dataframe, self._btc_regime_cache, on='date', how='left')
                dataframe['market_regime'] = dataframe['regime'].ffill().fillna(1)
                # Drop the temporary 'regime' column to keep dataframe clean
                dataframe = dataframe.drop(columns=['regime'])
            else:
                dataframe['market_regime'] = 1
        else:
            dataframe['market_regime'] = 1

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # Trend-Following Mean Reversion (Synchronized with Original Strategy)
        
        conditions_long = (
            (dataframe['rsi_4h'] > 70) &
            (dataframe['supertrend_4h'] == 1) & # Uptrend
            (dataframe['adx'] > 20) &
            (dataframe['adx_4h'] > 40) &
            (dataframe['market_regime'] >= 1) & # Bull or Sideways (HMM Filter)
            (dataframe['roc_24h'] > 0) &         # Relative Strength
            (dataframe['bbw'] < dataframe['bbw_sma'] * 1.5) # Volatility filter
        )
        
        conditions_short = (
            (dataframe['rsi_4h'] < 30) &
            (dataframe['supertrend_4h'] == -1) & # Downtrend
            (dataframe['adx'] > 20) &
            (dataframe['adx_4h'] > 40) &
            (dataframe['market_regime'] <= 1) & # Bear or Sideways (HMM Filter)
            (dataframe['roc_24h'] < 0) &         # Relative Weakness
            (dataframe['bbw'] < dataframe['bbw_sma'] * 1.5) # Volatility filter
        )

        dataframe.loc[conditions_long, 'enter_long'] = 1
        dataframe.loc[conditions_short, 'enter_short'] = 1

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # Exit logic from MeanReversionRSI.py
        dataframe.loc[
            (dataframe['close'] >= dataframe['bb_upperband']) |
            (dataframe['supertrend_4h'] == -1), # Exit long if 4h trend turns bearish
            'exit_long'
        ] = 1

        dataframe.loc[
            (dataframe['close'] <= dataframe['bb_lowerband']) |
            (dataframe['supertrend_4h'] == 1), # Exit short if 4h trend turns bullish
            'exit_short'
        ] = 1
        
        return dataframe

    def custom_entry_price(self, pair: str, current_time: datetime, proposed_rate: float,
                           entry_tag: str, side: str, **kwargs) -> float:
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        last_candle = dataframe.iloc[-1].squeeze()
        if side == 'long':
            return last_candle['bb_lowerband']
        else:
            return last_candle['bb_upperband']

    def custom_exit(self, pair: str, trade: Trade, current_time: datetime, current_rate: float,
                    current_profit: float, **kwargs) -> Optional[str]:
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        entry_candle = dataframe.loc[dataframe['date'] <= trade.open_date_utc]
        if entry_candle.empty:
            return None
            
        entry_candle = entry_candle.iloc[-1]
        atr = entry_candle['atr']
        regime = entry_candle['market_regime']
        
        # Regime-Adaptive Stop Loss Multiplier
        # Bull: 8x ATR (Give it room)
        # Sideways: 4x ATR (Tighten up)
        # Bear: 6x ATR (Standard)
        if regime == 2:
            sl_mult = 8.0
        elif regime == 1:
            sl_mult = 4.0
        else:
            sl_mult = 6.0
            
        # Fixed stop loss from entry price
        if trade.is_short:
            sl_price = trade.open_rate + (atr * sl_mult)
            if current_rate >= sl_price:
                return f"atr_stoploss_{sl_mult}x"
        else:
            sl_price = trade.open_rate - (atr * sl_mult)
            if current_rate <= sl_price:
                return f"atr_stoploss_{sl_mult}x"
                
        return None
