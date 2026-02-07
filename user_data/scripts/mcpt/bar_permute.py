"""
Bar Permutation Module for Monte Carlo Permutation Tests (MCPT)

Based on the approach from neurotrader888/mcpt
This module creates synthetic price data by permuting relative price movements
while preserving statistical properties like mean, std, skewness, and kurtosis.

The key idea:
1. Convert OHLC to log prices
2. Calculate relative movements (open gap, high/low/close relative to open)
3. Shuffle these relative movements
4. Reconstruct new OHLC bars

This preserves the distribution of returns while destroying any predictable patterns.
"""

import numpy as np
import pandas as pd
from pathlib import Path
from typing import List, Union, Optional


def get_permutation(
    ohlc: Union[pd.DataFrame, List[pd.DataFrame]], 
    start_index: int = 0, 
    seed: Optional[int] = None
) -> Union[pd.DataFrame, List[pd.DataFrame]]:
    """
    Generate a permuted version of OHLC data while preserving statistical properties.
    
    The permutation shuffles the relative price movements (gaps, high/low/close relative to open)
    while keeping bars before start_index unchanged. This is useful for walk-forward tests
    where you want to preserve the training period.
    
    Parameters
    ----------
    ohlc : DataFrame or List[DataFrame]
        OHLC data with columns: open, high, low, close
        Can pass multiple markets for synchronized permutation (preserves correlation structure)
    start_index : int
        Index to start permutation from (bars before this are kept intact)
    seed : int, optional
        Random seed for reproducibility
        
    Returns
    -------
    DataFrame or List[DataFrame]
        Permuted OHLC data with same structure as input
    """
    assert start_index >= 0, "start_index must be non-negative"

    if seed is not None:
        np.random.seed(seed)

    # Handle single vs multiple markets
    if isinstance(ohlc, list):
        time_index = ohlc[0].index
        for mkt in ohlc:
            assert np.all(time_index == mkt.index), "Indexes do not match across markets"
        n_markets = len(ohlc)
    else:
        n_markets = 1
        time_index = ohlc.index
        ohlc = [ohlc]

    n_bars = len(ohlc[0])
    
    if start_index >= n_bars - 1:
        # Nothing to permute
        return ohlc if n_markets > 1 else ohlc[0]

    perm_index = start_index + 1
    perm_n = n_bars - perm_index

    # Storage for relative price components
    start_bar = np.empty((n_markets, 4))
    relative_open = np.empty((n_markets, perm_n))
    relative_high = np.empty((n_markets, perm_n))
    relative_low = np.empty((n_markets, perm_n))
    relative_close = np.empty((n_markets, perm_n))

    # Extract relative price movements for each market
    for mkt_i, reg_bars in enumerate(ohlc):
        log_bars = np.log(reg_bars[['open', 'high', 'low', 'close']])

        # Store the starting bar (anchor point)
        start_bar[mkt_i] = log_bars.iloc[start_index].to_numpy()

        # Open relative to previous close (gap)
        r_o = (log_bars['open'] - log_bars['close'].shift()).to_numpy()
        
        # Intrabar movements relative to open
        r_h = (log_bars['high'] - log_bars['open']).to_numpy()
        r_l = (log_bars['low'] - log_bars['open']).to_numpy()
        r_c = (log_bars['close'] - log_bars['open']).to_numpy()

        # Store only the portion to be permuted
        relative_open[mkt_i] = r_o[perm_index:]
        relative_high[mkt_i] = r_h[perm_index:]
        relative_low[mkt_i] = r_l[perm_index:]
        relative_close[mkt_i] = r_c[perm_index:]

    # Generate permutation indices
    idx = np.arange(perm_n)

    # Shuffle intrabar relative values together (preserves high/low/close relationship within bar)
    perm1 = np.random.permutation(idx)
    relative_high = relative_high[:, perm1]
    relative_low = relative_low[:, perm1]
    relative_close = relative_close[:, perm1]

    # Shuffle gaps separately (open relative to previous close)
    perm2 = np.random.permutation(idx)
    relative_open = relative_open[:, perm2]

    # Reconstruct OHLC from permuted relative prices
    perm_ohlc = []
    for mkt_i, reg_bars in enumerate(ohlc):
        perm_bars = np.zeros((n_bars, 4))
        log_bars = np.log(reg_bars[['open', 'high', 'low', 'close']]).to_numpy().copy()
        
        # Copy real data before start_index
        perm_bars[:start_index] = log_bars[:start_index]
        
        # Copy start bar
        perm_bars[start_index] = start_bar[mkt_i]

        # Reconstruct bars from relative movements
        for i in range(perm_index, n_bars):
            k = i - perm_index
            perm_bars[i, 0] = perm_bars[i - 1, 3] + relative_open[mkt_i][k]  # Open = prev close + gap
            perm_bars[i, 1] = perm_bars[i, 0] + relative_high[mkt_i][k]      # High = open + rel_high
            perm_bars[i, 2] = perm_bars[i, 0] + relative_low[mkt_i][k]       # Low = open + rel_low
            perm_bars[i, 3] = perm_bars[i, 0] + relative_close[mkt_i][k]     # Close = open + rel_close

        # Convert back from log prices
        perm_bars = np.exp(perm_bars)
        perm_df = pd.DataFrame(perm_bars, index=time_index, columns=['open', 'high', 'low', 'close'])
        
        # Copy volume if present (unchanged)
        if 'volume' in reg_bars.columns:
            # Shuffle volume with the same permutation as intrabar movements
            vol = reg_bars['volume'].to_numpy().copy()
            perm_vol = np.zeros(n_bars)
            perm_vol[:perm_index] = vol[:perm_index]
            perm_vol[perm_index:] = vol[perm_index:][perm1]
            perm_df['volume'] = perm_vol

        perm_ohlc.append(perm_df)

    if n_markets > 1:
        return perm_ohlc
    else:
        return perm_ohlc[0]


def get_block_permutation(
    ohlc: pd.DataFrame,
    block_size: int = 24,
    start_index: int = 0,
    seed: Optional[int] = None
) -> pd.DataFrame:
    """
    Block permutation - shuffles blocks of bars instead of individual bars.
    
    This preserves short-term dependencies (momentum, mean-reversion within blocks)
    while destroying long-term patterns. Use this when your strategy exploits
    patterns shorter than block_size.
    
    Parameters
    ----------
    ohlc : DataFrame
        OHLC data with columns: open, high, low, close
    block_size : int
        Size of blocks to shuffle (default: 24 = 1 day for hourly data)
    start_index : int
        Index to start permutation from
    seed : int, optional
        Random seed for reproducibility
    
    Returns
    -------
    DataFrame
        Block-permuted OHLC data
    """
    if seed is not None:
        np.random.seed(seed)
    
    df = ohlc.copy()
    n = len(df)
    
    if start_index >= n - block_size:
        return df
    
    # Calculate relative movements
    log_close = np.log(df['close']).values
    log_open = np.log(df['open']).values
    log_high = np.log(df['high']).values
    log_low = np.log(df['low']).values
    
    # Gap: open relative to previous close
    gap = np.zeros(n)
    gap[1:] = log_open[1:] - log_close[:-1]
    
    # Intrabar movements relative to open
    high_wick = log_high - log_open
    low_wick = log_low - log_open
    close_move = log_close - log_open
    
    # Create blocks from the permutable region
    perm_start = start_index
    perm_region = n - perm_start
    n_full_blocks = perm_region // block_size
    
    if n_full_blocks < 2:
        # Not enough for meaningful block permutation, fall back to regular
        return get_permutation(ohlc, start_index, seed)
    
    # Extract blocks
    blocks_gap = []
    blocks_high = []
    blocks_low = []
    blocks_close = []
    blocks_volume = [] if 'volume' in df.columns else None
    
    for i in range(n_full_blocks):
        block_start = perm_start + i * block_size
        block_end = block_start + block_size
        
        blocks_gap.append(gap[block_start:block_end].copy())
        blocks_high.append(high_wick[block_start:block_end].copy())
        blocks_low.append(low_wick[block_start:block_end].copy())
        blocks_close.append(close_move[block_start:block_end].copy())
        
        if blocks_volume is not None:
            blocks_volume.append(df['volume'].values[block_start:block_end].copy())
    
    # Shuffle block indices
    block_order = np.random.permutation(n_full_blocks)
    
    # Reconstruct shuffled relative movements
    new_gap = gap.copy()
    new_high = high_wick.copy()
    new_low = low_wick.copy()
    new_close = close_move.copy()
    new_volume = df['volume'].values.copy() if 'volume' in df.columns else None
    
    for new_idx, old_idx in enumerate(block_order):
        new_start = perm_start + new_idx * block_size
        new_end = new_start + block_size
        
        new_gap[new_start:new_end] = blocks_gap[old_idx]
        new_high[new_start:new_end] = blocks_high[old_idx]
        new_low[new_start:new_end] = blocks_low[old_idx]
        new_close[new_start:new_end] = blocks_close[old_idx]
        
        if new_volume is not None:
            new_volume[new_start:new_end] = blocks_volume[old_idx]
    
    # Fix first gap of each block to connect properly
    for i in range(1, n_full_blocks):
        block_start = perm_start + i * block_size
        # Adjust gap to connect to previous block's close
        # This slightly modifies the gap distribution but maintains continuity
        new_gap[block_start] = np.random.choice(gap[perm_start:])
    
    # Reconstruct OHLC from relative movements
    result_close = np.zeros(n)
    result_open = np.zeros(n)
    result_high = np.zeros(n)
    result_low = np.zeros(n)
    
    # Copy unchanged portion
    result_open[:perm_start] = log_open[:perm_start]
    result_high[:perm_start] = log_high[:perm_start]
    result_low[:perm_start] = log_low[:perm_start]
    result_close[:perm_start] = log_close[:perm_start]
    
    # Reconstruct from relative movements
    for i in range(perm_start, n):
        if i == 0:
            result_open[i] = log_open[0]
        else:
            result_open[i] = result_close[i-1] + new_gap[i]
        
        result_high[i] = result_open[i] + new_high[i]
        result_low[i] = result_open[i] + new_low[i]
        result_close[i] = result_open[i] + new_close[i]
    
    # Convert back from log
    result = pd.DataFrame({
        'open': np.exp(result_open),
        'high': np.exp(result_high),
        'low': np.exp(result_low),
        'close': np.exp(result_close)
    }, index=df.index)
    
    if new_volume is not None:
        result['volume'] = new_volume
    
    return result


def get_permutation_with_resample(
    df_base: pd.DataFrame,
    higher_timeframe: str = '4h',
    permutation_type: str = 'standard',
    block_size: int = 24,
    start_index: int = 0,
    seed: Optional[int] = None
) -> tuple:
    """
    Permute base timeframe data, then resample to higher timeframe.
    
    This maintains the natural relationship between timeframes that would
    exist in real data, which is important for strategies using multiple
    timeframes (like informative pairs).
    
    Parameters
    ----------
    df_base : DataFrame
        Base timeframe OHLC data (e.g., 1h)
    higher_timeframe : str
        Target higher timeframe (e.g., '4h', '1d')
    permutation_type : str
        'standard' for bar permutation, 'block' for block permutation
    block_size : int
        Block size for block permutation
    start_index : int
        Index to start permutation from
    seed : int, optional
        Random seed
    
    Returns
    -------
    tuple of (DataFrame, DataFrame)
        (permuted_base, permuted_higher)
    """
    # Permute base timeframe
    if permutation_type == 'block':
        df_perm = get_block_permutation(df_base, block_size, start_index, seed)
    else:
        df_perm = get_permutation(df_base, start_index, seed)
    
    # Resample to higher timeframe
    df_higher = df_perm.resample(higher_timeframe).agg({
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum' if 'volume' in df_perm.columns else 'first'
    }).dropna()
    
    return df_perm, df_higher


def validate_permutation(real_df: pd.DataFrame, perm_df: pd.DataFrame) -> dict:
    """
    Validate that the permutation preserves key statistical properties.
    
    Parameters
    ----------
    real_df : DataFrame
        Original OHLC data
    perm_df : DataFrame
        Permuted OHLC data
        
    Returns
    -------
    dict
        Dictionary with statistical comparisons
    """
    real_r = np.log(real_df['close']).diff().dropna()
    perm_r = np.log(perm_df['close']).diff().dropna()
    
    # Also check autocorrelation (should be destroyed by permutation)
    real_autocorr = real_r.autocorr(lag=1) if len(real_r) > 1 else 0
    perm_autocorr = perm_r.autocorr(lag=1) if len(perm_r) > 1 else 0
    
    return {
        'real_mean': real_r.mean(),
        'perm_mean': perm_r.mean(),
        'real_std': real_r.std(),
        'perm_std': perm_r.std(),
        'real_skew': real_r.skew(),
        'perm_skew': perm_r.skew(),
        'real_kurt': real_r.kurt(),
        'perm_kurt': perm_r.kurt(),
        'real_autocorr': real_autocorr,
        'perm_autocorr': perm_autocorr,
        'correlation': real_r.corr(perm_r) if len(real_r) == len(perm_r) else None,
    }


def validate_block_permutation(real_df: pd.DataFrame, perm_df: pd.DataFrame, block_size: int = 24) -> dict:
    """
    Validate block permutation - should preserve short-term but destroy long-term patterns.
    """
    real_r = np.log(real_df['close']).diff().dropna()
    perm_r = np.log(perm_df['close']).diff().dropna()
    
    base_stats = validate_permutation(real_df, perm_df)
    
    # Check autocorrelation at different lags
    # Short-term autocorr should be somewhat preserved
    # Long-term autocorr should be destroyed
    
    short_lag = min(block_size // 2, 12)
    long_lag = block_size * 2
    
    if len(real_r) > long_lag:
        base_stats['real_autocorr_short'] = real_r.autocorr(lag=short_lag)
        base_stats['perm_autocorr_short'] = perm_r.autocorr(lag=short_lag)
        base_stats['real_autocorr_long'] = real_r.autocorr(lag=long_lag)
        base_stats['perm_autocorr_long'] = perm_r.autocorr(lag=long_lag)
    
    return base_stats


if __name__ == '__main__':
    # Test the permutation on sample data
    import matplotlib.pyplot as plt
    
    # Load sample data
    data_path = Path(__file__).parent.parent.parent / 'data' / 'binance' / 'futures'
    btc_file = data_path / 'BTC_USDT_USDT-1h-futures.feather'
    
    if btc_file.exists():
        df = pd.read_feather(btc_file)
        df.set_index('date', inplace=True)
        
        # Get last 2 years
        df = df.tail(365 * 24 * 2)
        
        # Generate permutation
        perm_df = get_permutation(df.copy(), seed=42)
        
        # Validate
        stats = validate_permutation(df, perm_df)
        print("Statistical Comparison:")
        print(f"Mean  - Real: {stats['real_mean']:.6f}, Perm: {stats['perm_mean']:.6f}")
        print(f"Std   - Real: {stats['real_std']:.6f}, Perm: {stats['perm_std']:.6f}")
        print(f"Skew  - Real: {stats['real_skew']:.6f}, Perm: {stats['perm_skew']:.6f}")
        print(f"Kurt  - Real: {stats['real_kurt']:.6f}, Perm: {stats['perm_kurt']:.6f}")
        
        # Plot comparison
        plt.style.use('dark_background')
        fig, axes = plt.subplots(2, 1, figsize=(14, 8))
        
        np.log(df['close']).diff().cumsum().plot(ax=axes[0], color='orange', label='Real')
        axes[0].set_title('Real BTC Cumulative Log Returns')
        axes[0].set_ylabel('Cumulative Log Return')
        axes[0].legend()
        
        np.log(perm_df['close']).diff().cumsum().plot(ax=axes[1], color='purple', label='Permuted')
        axes[1].set_title('Permuted BTC Cumulative Log Returns')
        axes[1].set_ylabel('Cumulative Log Return')
        axes[1].legend()
        
        plt.tight_layout()
        plt.savefig(Path(__file__).parent / 'permutation_comparison.png', dpi=150)
        print("\nPlot saved to permutation_comparison.png")
    else:
        print(f"Data file not found: {btc_file}")
