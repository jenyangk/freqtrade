import logging
from freqtrade.strategy import IStrategy, IntParameter
from pandas import DataFrame
import talib.abstract as ta
import freqtrade.vendor.qtpylib.indicators as qtpylib

logger = logging.getLogger(__name__)

class TFMR_FreqAI(IStrategy):
    """
    TFMR_FreqAI: A FreqAI-ready strategy template.
    
    This strategy uses FreqAI to predict price movements.
    It requires a FreqAI configuration to run.
    """
    INTERFACE_VERSION = 3

    timeframe = '1h'
    minimal_roi = {"0": 0.10}
    stoploss = -0.05
    
    # FreqAI specific parameters
    can_predict = True
    
    def feature_engineering_expand_all(self, dataframe: DataFrame, period: int,
                                       metadata: dict, **kwargs) -> DataFrame:
        """
        Define the features that the model will use to learn.
        """
        dataframe["%-rsi-period"] = ta.RSI(dataframe, timeperiod=period)
        dataframe["%-mfi-period"] = ta.MFI(dataframe, timeperiod=period)
        dataframe["%-adx-period"] = ta.ADX(dataframe, timeperiod=period)
        dataframe["%-sma-period"] = ta.SMA(dataframe, timeperiod=period)
        dataframe["%-ema-period"] = ta.EMA(dataframe, timeperiod=period)

        bollinger = ta.BBANDS(dataframe, timeperiod=period)
        dataframe["%-bb_lower-period"] = bollinger["lowerband"]
        dataframe["%-bb_middle-period"] = bollinger["middleband"]
        dataframe["%-bb_upper-period"] = bollinger["upperband"]
        
        return dataframe

    def feature_engineering_expand_basic(self, dataframe: DataFrame, metadata: dict, **kwargs) -> DataFrame:
        """
        Define basic features.
        """
        dataframe["%-pct-change"] = dataframe["close"].pct_change()
        dataframe["%-raw_volume"] = dataframe["volume"]
        return dataframe

    def feature_engineering_standard_filters(self, dataframe: DataFrame, metadata: dict, **kwargs) -> DataFrame:
        """
        Standard filters for feature engineering.
        """
        return dataframe

    def set_freqai_targets(self, dataframe: DataFrame, metadata: dict, **kwargs) -> DataFrame:
        """
        Define the target that the model will try to predict.
        In this case, we predict the price change 10 candles into the future.
        """
        dataframe["&-s-price_change"] = (
            dataframe["close"].shift(-10) / dataframe["close"] - 1
        )
        return dataframe

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # FreqAI will handle feature engineering and target setting
        dataframe = self.freqai.start(dataframe, metadata, self)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        Use the model's prediction to enter trades.
        """
        # Enter long if predicted price change is > 1%
        dataframe.loc[
            (dataframe["&-s-price_change"] > 0.01) &
            (dataframe["do_predict"] == 1),
            "enter_long"
        ] = 1
        
        # Enter short if predicted price change is < -1%
        dataframe.loc[
            (dataframe["&-s-price_change"] < -0.01) &
            (dataframe["do_predict"] == 1),
            "enter_short"
        ] = 1
        
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        Exit based on model's prediction or other logic.
        """
        dataframe.loc[
            (dataframe["&-s-price_change"] < 0) &
            (dataframe["do_predict"] == 1),
            "exit_long"
        ] = 1
        
        dataframe.loc[
            (dataframe["&-s-price_change"] > 0) &
            (dataframe["do_predict"] == 1),
            "exit_short"
        ] = 1
        
        return dataframe
