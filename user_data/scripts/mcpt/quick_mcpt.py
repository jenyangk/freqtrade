#!/usr/bin/env python3
"""
Monte Carlo Permutation Test - Quick Analysis Script

This script provides a simplified MCPT that calculates returns-based metrics
directly without running full backtests. This is much faster for initial
overfitting detection.

For full strategy MCPT (with proper entry/exit logic), use run_mcpt.py

The idea:
1. Load your strategy's trade history from a backtest
2. Calculate the performance metric
3. Permute the price data and see what random performance looks like
4. Calculate p-value
"""

import json
import sys
from pathlib import Path
from typing import Dict, List, Optional
import numpy as np
import pandas as pd
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).parent))
from bar_permute import get_permutation

# Paths
USER_DATA = Path(__file__).parent.parent.parent
DATA_DIR = USER_DATA / 'data'
BINANCE_DATA = DATA_DIR / 'binance' / 'futures'
BACKTEST_RESULTS = USER_DATA / 'backtest_results'


def load_backtest_results(strategy: str) -> Optional[Dict]:
    """
    Load the most recent backtest results for a strategy.
    """
    results_files = list(BACKTEST_RESULTS.glob(f'backtest-result-*.json'))
    
    if not results_files:
        print(f"No backtest results found in {BACKTEST_RESULTS}")
        return None
    
    # Sort by modification time, newest first
    results_files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
    
    for rf in results_files:
        with open(rf) as f:
            data = json.load(f)
        
        # Check if this is for our strategy
        if 'strategy' in data:
            if data['strategy'].get('strategy_name') == strategy:
                print(f"Loaded backtest results from {rf.name}")
                return data
        
        # Also check in strategy_comparison format
        if 'strategy_comparison' in data:
            for strat_data in data['strategy_comparison']:
                if strat_data.get('key') == strategy:
                    return data
    
    print(f"No backtest results found for strategy '{strategy}'")
    return None


def calculate_trade_metrics(trades: List[Dict]) -> Dict:
    """
    Calculate performance metrics from a list of trades.
    """
    if not trades:
        return {
            'profit_total_pct': 0.0,
            'profit_factor': 0.0,
            'win_rate': 0.0,
            'total_trades': 0
        }
    
    profits = [t.get('profit_ratio', t.get('profit_abs', 0)) for t in trades]
    profits = [p for p in profits if p is not None]
    
    if not profits:
        return {
            'profit_total_pct': 0.0,
            'profit_factor': 0.0,
            'win_rate': 0.0,
            'total_trades': 0
        }
    
    gains = sum(p for p in profits if p > 0)
    losses = abs(sum(p for p in profits if p < 0))
    
    return {
        'profit_total_pct': sum(profits) * 100,
        'profit_factor': gains / losses if losses > 0 else float('inf') if gains > 0 else 0.0,
        'win_rate': len([p for p in profits if p > 0]) / len(profits) * 100,
        'total_trades': len(trades),
        'avg_profit': np.mean(profits) * 100,
        'sharpe_ratio': np.mean(profits) / np.std(profits) if np.std(profits) > 0 else 0.0
    }


def simple_momentum_strategy(df: pd.DataFrame, lookback: int = 24) -> pd.Series:
    """
    A simple momentum strategy for testing permutation effects.
    Returns 1 for long, -1 for short, 0 for no position.
    """
    returns = np.log(df['close']).diff()
    momentum = returns.rolling(lookback).sum()
    
    signal = pd.Series(0, index=df.index)
    signal[momentum > 0] = 1
    signal[momentum < 0] = -1
    
    return signal


def calculate_strategy_pf(df: pd.DataFrame, signal: pd.Series) -> float:
    """
    Calculate profit factor for a signal series.
    """
    returns = np.log(df['close']).diff().shift(-1)
    strat_returns = signal * returns
    strat_returns = strat_returns.dropna()
    
    gains = strat_returns[strat_returns > 0].sum()
    losses = abs(strat_returns[strat_returns < 0].sum())
    
    if losses == 0:
        return float('inf') if gains > 0 else 0.0
    
    return gains / losses


def run_quick_mcpt(
    pair: str = 'BTC/USDT:USDT',
    timeframe: str = '1h',
    n_permutations: int = 100,
    strategy_func=None,
    lookback: int = 24,
    start_date: str = None,
    end_date: str = None
):
    """
    Run a quick MCPT using a simple strategy function.
    
    This is useful for testing whether the BASIC signal logic has any edge,
    before running full freqtrade backtests.
    """
    
    # Load data
    clean_pair = pair.replace('/', '_').replace(':', '_')
    filename = f"{clean_pair}-{timeframe}-futures.feather"
    filepath = BINANCE_DATA / filename
    
    if not filepath.exists():
        print(f"Data file not found: {filepath}")
        return None
    
    print(f"Loading {filepath}...")
    df = pd.read_feather(filepath)
    if 'date' in df.columns:
        df.set_index('date', inplace=True)
    
    # Filter date range
    if start_date:
        df = df[df.index >= start_date]
    if end_date:
        df = df[df.index <= end_date]
    
    print(f"Data range: {df.index[0]} to {df.index[-1]} ({len(df)} bars)")
    
    # Use default strategy if none provided
    if strategy_func is None:
        strategy_func = lambda x: simple_momentum_strategy(x, lookback)
    
    # Calculate real performance
    print("\nCalculating real performance...")
    real_signal = strategy_func(df)
    real_pf = calculate_strategy_pf(df, real_signal)
    print(f"Real Profit Factor: {real_pf:.4f}")
    
    # Run permutations
    print(f"\nRunning {n_permutations} permutations...")
    perm_pfs = []
    perm_better_count = 1
    
    for i in tqdm(range(n_permutations)):
        perm_df = get_permutation(df.copy(), start_index=lookback, seed=i)
        perm_signal = strategy_func(perm_df)
        perm_pf = calculate_strategy_pf(perm_df, perm_signal)
        
        perm_pfs.append(perm_pf)
        if perm_pf >= real_pf:
            perm_better_count += 1
    
    # Calculate p-value
    p_value = perm_better_count / (n_permutations + 1)
    
    # Print results
    print(f"\n{'='*50}")
    print(f"Quick MCPT Results")
    print(f"{'='*50}")
    print(f"Real Profit Factor:    {real_pf:.4f}")
    print(f"Permuted Mean PF:      {np.mean(perm_pfs):.4f}")
    print(f"Permuted Std PF:       {np.std(perm_pfs):.4f}")
    print(f"Times perm >= real:    {perm_better_count}")
    print(f"\n*** P-VALUE: {p_value:.4f} ***")
    print(f"{'='*50}")
    
    if p_value < 0.05:
        print("\n✅ Strategy shows statistically significant edge")
    elif p_value < 0.10:
        print("\n⚠️  Marginal significance - proceed with caution")
    else:
        print("\n❌ No significant edge detected - likely overfit")
    
    # Plot
    try:
        import matplotlib.pyplot as plt
        
        plt.style.use('dark_background')
        fig, ax = plt.subplots(figsize=(10, 6))
        
        ax.hist(perm_pfs, bins=30, color='steelblue', alpha=0.7, 
                label='Permutations', edgecolor='white')
        ax.axvline(real_pf, color='red', linewidth=2, linestyle='--',
                   label=f'Real: {real_pf:.3f}')
        ax.axvline(np.mean(perm_pfs), color='yellow', linewidth=1,
                   linestyle=':', label=f'Mean: {np.mean(perm_pfs):.3f}')
        
        ax.set_xlabel('Profit Factor')
        ax.set_ylabel('Frequency')
        ax.set_title(f'Quick MCPT: {pair} | P-value: {p_value:.4f}')
        ax.legend()
        ax.grid(True, alpha=0.3)
        
        plot_path = Path(__file__).parent / f'quick_mcpt_{clean_pair}.png'
        plt.tight_layout()
        plt.savefig(plot_path, dpi=150)
        plt.close()
        print(f"\nPlot saved to: {plot_path}")
        
    except ImportError:
        pass
    
    return {
        'real_pf': real_pf,
        'perm_pfs': perm_pfs,
        'p_value': p_value
    }


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='Run quick MCPT analysis')
    parser.add_argument('--pair', '-p', default='BTC/USDT:USDT')
    parser.add_argument('--timeframe', '-t', default='1h')
    parser.add_argument('-n', '--n-permutations', type=int, default=100)
    parser.add_argument('--lookback', type=int, default=24)
    parser.add_argument('--start-date', help='YYYY-MM-DD')
    parser.add_argument('--end-date', help='YYYY-MM-DD')
    
    args = parser.parse_args()
    
    run_quick_mcpt(
        pair=args.pair,
        timeframe=args.timeframe,
        n_permutations=args.n_permutations,
        lookback=args.lookback,
        start_date=args.start_date,
        end_date=args.end_date
    )
