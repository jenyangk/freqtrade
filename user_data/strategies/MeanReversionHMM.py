import numpy as np
import pandas as pd
import pandas_ta as pta
import talib.abstract as ta
from pandas import DataFrame
from freqtrade.strategy import IStrategy, merge_informative_pair
from freqtrade.persistence import Trade
from datetime import datetime, timedelta
from hmmlearn.hmm import GaussianHMM
import logging

logger = logging.getLogger(__name__)

class MeanReversionHMM(IStrategy):
    """
    MeanReversionHMM Strategy - Final 2023-2025 Optimization
    
    Focus: High-conviction Bear Short and Bull Long entries.
    Risk: 1% per trade, 4x ATR stop, Trailing Stop enabled.
    """
    INTERFACE_VERSION = 3

    timeframe = '1h'
    informative_timeframe = '4h'

    minimal_roi = {
        "0": 0.10,
        "120": 0.05,
        "240": 0.02,
        "720": 0.0
    }

    stoploss = -0.10 # Safety stop
    
    trailing_stop = True
    trailing_stop_positive = 0.01
    trailing_stop_positive_offset = 0.02
    trailing_only_offset_is_reached = True

    process_only_new_candles = True
    use_custom_stoploss = True
    startup_candle_count = 200
    can_short = True

    # Strategy Parameters
    zscore_window = 30
    max_trade_duration_candles = 48 
    
    # HMM Parameters
    use_regime_filter = True
    _btc_hmm_cache = None

    def informative_pairs(self):
        pairs = self.dp.current_whitelist()
        informative = [(pair, self.informative_timeframe) for pair in pairs]
        btc_pair = "BTC/USDT"
        for p in pairs:
            if p.startswith("BTC/"):
                btc_pair = p
                break
        if (btc_pair, self.timeframe) not in informative:
            informative.append((btc_pair, self.timeframe))
        return informative

    def get_hmm_regime(self, dataframe: DataFrame) -> DataFrame:
        df = dataframe.copy()
        df['returns'] = np.log(df['close'] / df['close'].shift(1)).rolling(window=5).mean()
        df['volatility'] = df['returns'].rolling(window=40).std()
        df['range'] = (df['high'] - df['low']) / df['close']
        df = df.dropna()
        if len(df) < self.startup_candle_count:
            df = dataframe.copy()
            df['regime'] = 1
            return df[['date', 'regime']]
        features = df[['returns', 'volatility', 'range']].values
        model = GaussianHMM(n_components=3, covariance_type="diag", n_iter=500, random_state=42)
        model.fit(features)
        regimes = model.predict(features)
        means = model.means_[:, 0]
        sorted_indices = np.argsort(means)
        mapping = {sorted_indices[0]: 0, sorted_indices[1]: 1, sorted_indices[2]: 2}
        df['regime'] = [mapping[r] for r in regimes]
        return df[['date', 'regime']]

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe['sma'] = ta.SMA(dataframe, timeperiod=self.zscore_window)
        dataframe['stddev'] = dataframe['close'].rolling(window=self.zscore_window).std()
        dataframe['zscore'] = (dataframe['close'] - dataframe['sma']) / dataframe['stddev']
        dataframe['atr'] = ta.ATR(dataframe, timeperiod=14)
        dataframe['volume_sma'] = ta.SMA(dataframe['volume'], timeperiod=20)
        dataframe['adx'] = ta.ADX(dataframe, timeperiod=14)
        dataframe['ema_50'] = ta.EMA(dataframe, timeperiod=50)
        dataframe['ema_200'] = ta.EMA(dataframe, timeperiod=200)
        dataframe['roc'] = ta.ROC(dataframe, timeperiod=5)

        dataframe['bullish_reversal'] = (
            (ta.CDLENGULFING(dataframe) == 100) |
            (ta.CDLHAMMER(dataframe) == 100)
        ).astype(int)
        
        dataframe['bearish_reversal'] = (
            (ta.CDLENGULFING(dataframe) == -100) |
            (ta.CDLSHOOTINGSTAR(dataframe) == -100)
        ).astype(int)

        if self.use_regime_filter:
            if self._btc_hmm_cache is None:
                btc_pair = "BTC/USDT"
                for p in self.dp.current_whitelist():
                    if p.startswith("BTC/"):
                        btc_pair = p
                        break
                btc_df = self.dp.get_pair_dataframe(pair=btc_pair, timeframe=self.timeframe)
                if not btc_df.empty:
                    self._btc_hmm_cache = self.get_hmm_regime(btc_df)
            if self._btc_hmm_cache is not None:
                dataframe = pd.merge(dataframe, self._btc_hmm_cache, on='date', how='left')
                dataframe['market_regime'] = dataframe['regime'].ffill().fillna(1)
            else:
                dataframe['market_regime'] = 1
        else:
            dataframe['market_regime'] = 1

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # Bull Long (Ultra Strict)
        dataframe.loc[
            (dataframe['market_regime'] == 2) & 
            (dataframe['zscore'].shift(1) < -3.0) & # Extreme oversold
            (dataframe['zscore'] > dataframe['zscore'].shift(1)) & 
            (dataframe['adx'] > 30) & 
            (dataframe['ema_50'] > dataframe['ema_200']) & 
            (dataframe['volume'] > dataframe['volume_sma'] * 1.2) & # Volume spike
            (dataframe['bullish_reversal'] == 1),
            ['enter_long', 'enter_tag']
        ] = (1, 'bull_long')

        # Bear Short (Proven)
        dataframe.loc[
            (dataframe['market_regime'] == 0) & 
            (dataframe['zscore'].shift(1) > 2.0) & 
            (dataframe['zscore'] < dataframe['zscore'].shift(1)) & 
            (dataframe['adx'] > 20) & 
            (dataframe['close'] < dataframe['ema_200']) &
            (dataframe['bearish_reversal'] == 1),
            ['enter_short', 'enter_tag']
        ] = (1, 'bear_short')

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return dataframe

    def custom_exit(self, pair: str, trade: Trade, current_time: datetime, current_rate: float,
                    current_profit: float, **kwargs) -> bool:
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        last_candle = dataframe.iloc[-1].squeeze()
        
        if trade.is_short:
            if last_candle['zscore'] <= -1.0 and current_profit > 0.01:
                return "zscore_reversion"
        else:
            if last_candle['zscore'] >= 1.0 and current_profit > 0.01:
                return "zscore_reversion"
            
        return False

    def custom_stoploss(self, pair: str, trade: Trade, current_time: datetime,
                        current_rate: float, current_profit: float, **kwargs) -> float:
        if current_profit > 0.02:
            return (trade.open_rate * (1.005 if not trade.is_short else 0.995) / current_rate) - 1
            
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        entry_candle = dataframe.loc[dataframe['date'] <= trade.open_date_utc]
        if entry_candle.empty:
            return -0.04
            
        atr = entry_candle.iloc[-1]['atr']
        
        if trade.is_short:
            sl_price = trade.open_rate + min(atr * 4, trade.open_rate * 0.08)
            if current_rate >= sl_price:
                return -0.0001
            return (sl_price / current_rate) - 1
        else:
            sl_price = trade.open_rate - min(atr * 4, trade.open_rate * 0.08)
            if current_rate <= sl_price:
                return -0.0001
            return (sl_price / current_rate) - 1

    def custom_stake_amount(self, pair: str, current_time: datetime, current_rate: float,
                            proposed_stake: float, min_stake: float, max_stake: float,
                            leverage: float, entry_tag: str, side: str,
                            **kwargs) -> float:
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if dataframe.empty:
            return proposed_stake
        atr = dataframe.iloc[-1]['atr']
        if atr == 0 or np.isnan(atr):
            return proposed_stake
        total_capital = self.wallets.get_total_stake_amount()
        risk_per_trade = total_capital * 0.01 
        sl_distance = 4 * atr
        position_size = (risk_per_trade / sl_distance) * current_rate
        return min(max(position_size, min_stake or 0), max_stake)

    def leverage(self, pair: str, current_time: datetime, current_rate: float,
                 proposed_leverage: float, max_leverage: float, entry_tag: str, side: str,
                 **kwargs) -> float:
        return 2.0
