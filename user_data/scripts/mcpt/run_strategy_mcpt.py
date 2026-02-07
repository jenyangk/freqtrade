#!/usr/bin/env python3
"""
Strategy-Specific MCPT for MeanReversionRSI

This script runs MCPT specifically for your MeanReversionRSI strategy.
It uses freqtrade's backtesting command to run proper backtests on
permutated data.

Usage:
    python run_strategy_mcpt.py --pair BTC/USDT:USDT --timerange 20230101-20241201 -n 50
"""

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
import numpy as np
import pandas as pd
from tqdm import tqdm

# Paths
FREQTRADE_ROOT = Path(__file__).parent.parent.parent.parent
USER_DATA = FREQTRADE_ROOT / 'user_data'
DATA_DIR = USER_DATA / 'data'
MCPT_DATA = DATA_DIR / 'mcpt_permutations'
MCPT_RESULTS = USER_DATA / 'backtest_results' / 'mcpt'


def run_backtest_and_extract_metrics(
    strategy: str,
    pairs: List[str],
    timeframe: str,
    timerange: str,
    data_dir: str,
    config_path: str = 'user_data/config.json'
) -> Optional[Dict]:
    """
    Run freqtrade backtest and extract key metrics from output.
    """
    
    # Build command
    pairs_str = ' '.join(pairs)
    cmd = (
        f"cd {FREQTRADE_ROOT} && "
        f"source .venv/bin/activate && "
        f"freqtrade backtesting "
        f"--strategy {strategy} "
        f"-c {config_path} "
        f"--datadir {data_dir} "
        f"--timeframe {timeframe} "
        f"--timerange {timerange} "
        f"--pairs {pairs_str} "
        f"--export none "
        f"--cache none "
        f"2>&1"
    )
    
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            executable='/bin/bash',
            capture_output=True,
            text=True,
            timeout=300
        )
        
        output = result.stdout + result.stderr
        return parse_backtest_output(output)
        
    except subprocess.TimeoutExpired:
        return None
    except Exception as e:
        print(f"Error: {e}")
        return None


def parse_backtest_output(output: str) -> Dict:
    """
    Parse freqtrade backtest output to extract metrics.
    """
    metrics = {
        'profit_total_pct': None,
        'profit_factor': None,
        'sharpe_ratio': None,
        'sortino_ratio': None,
        'max_drawdown_pct': None,
        'win_rate': None,
        'total_trades': None,
        'avg_profit_pct': None,
        'calmar_ratio': None,
    }
    
    lines = output.split('\n')
    
    for line in lines:
        # Look for STRATEGY SUMMARY table - most reliable
        # Format: │ StrategyName │ Trades │ Avg Profit % │ Tot Profit USDT │ Tot Profit % │ ...
        if '│' in line and 'MeanReversionRSI' in line:
            parts = [p.strip() for p in line.split('│') if p.strip()]
            if len(parts) >= 5:
                try:
                    metrics['total_trades'] = int(parts[1])
                    metrics['avg_profit_pct'] = float(parts[2])
                    metrics['profit_total_pct'] = float(parts[4])
                except (ValueError, IndexError):
                    pass
        
        # Also check the TOTAL line in results table
        if '│ TOTAL │' in line or '│         TOTAL │' in line:
            parts = [p.strip() for p in line.split('│') if p.strip()]
            if len(parts) >= 5:
                try:
                    metrics['total_trades'] = int(parts[1])
                    metrics['avg_profit_pct'] = float(parts[2])
                    metrics['profit_total_pct'] = float(parts[4])
                except (ValueError, IndexError):
                    pass
        
        # Profit factor - look for explicit line
        if '│ Profit factor' in line:
            match = re.search(r'│\s*(\d+\.?\d*)\s*│', line.split('Profit factor')[-1])
            if match:
                pf = float(match.group(1))
                if pf >= 0 and pf < 1000:
                    metrics['profit_factor'] = pf
        
        # Sharpe ratio
        if '│ Sharpe' in line:
            match = re.search(r'│\s*(-?\d+\.?\d*)\s*│', line.split('Sharpe')[-1])
            if match:
                metrics['sharpe_ratio'] = float(match.group(1))
        
        # Sortino ratio
        if '│ Sortino' in line:
            match = re.search(r'│\s*(-?\d+\.?\d*)\s*│', line.split('Sortino')[-1])
            if match:
                metrics['sortino_ratio'] = float(match.group(1))
        
        # Max drawdown (from absolute drawdown line)
        if '│ Absolute Drawdown' in line or '│ Absolute drawdown' in line:
            match = re.search(r'\((\d+\.?\d*)%\)', line)
            if match:
                metrics['max_drawdown_pct'] = float(match.group(1))
        
        # Also from strategy summary Drawdown column
        if 'USDT' in line and '%' in line and '│' in line:
            drawdown_match = re.search(r'(\d+\.?\d*)\s*%\s*│\s*$', line)
            if drawdown_match and metrics['max_drawdown_pct'] is None:
                metrics['max_drawdown_pct'] = float(drawdown_match.group(1))
        
        # Win/Draw/Loss - look in strategy summary
        if 'Win%' in line or 'Win  Draw  Loss' in line:
            continue  # Header line
        
        # Win rate from pattern like "6     0     0   100"
        win_match = re.search(r'(\d+)\s+(\d+)\s+(\d+)\s+(\d+\.?\d*)', line)
        if win_match and metrics['win_rate'] is None:
            wins = int(win_match.group(1))
            losses = int(win_match.group(3))
            if wins + losses > 0:
                metrics['win_rate'] = float(win_match.group(4))
        
        # Calmar
        if '│ Calmar' in line:
            match = re.search(r'│\s*(-?\d+\.?\d*)\s*│', line.split('Calmar')[-1])
            if match:
                metrics['calmar_ratio'] = float(match.group(1))
    
    return metrics


def run_strategy_mcpt(
    strategy: str,
    pairs: List[str],
    timeframe: str,
    timerange: str,
    n_permutations: int = 50,
    metric: str = 'profit_total_pct',
    config_path: str = 'user_data/config.json'
) -> Dict:
    """
    Run MCPT for a strategy using freqtrade backtests.
    """
    
    print(f"\n{'='*60}")
    print(f"Monte Carlo Permutation Test for {strategy}")
    print(f"{'='*60}")
    print(f"Pairs:         {', '.join(pairs)}")
    print(f"Timeframe:     {timeframe}")
    print(f"Timerange:     {timerange}")
    print(f"Permutations:  {n_permutations}")
    print(f"Metric:        {metric}")
    print(f"{'='*60}\n")
    
    # Check permutations exist
    if not MCPT_DATA.exists():
        print(f"ERROR: Permutation data not found at {MCPT_DATA}")
        print("Please run generate_permutations.py first")
        sys.exit(1)
    
    # Count available permutations
    available_perms = len(list(MCPT_DATA.glob('perm_*')))
    n_permutations = min(n_permutations, available_perms)
    print(f"Using {n_permutations} of {available_perms} available permutations")
    
    # Run on real data
    print("\n[1/2] Running backtest on REAL data...")
    real_data_dir = str(DATA_DIR / 'binance')
    real_results = run_backtest_and_extract_metrics(
        strategy=strategy,
        pairs=pairs,
        timeframe=timeframe,
        timerange=timerange,
        data_dir=real_data_dir,
        config_path=config_path
    )
    
    if real_results is None:
        print("ERROR: Failed to run backtest on real data")
        sys.exit(1)
    
    real_metric = real_results.get(metric)
    if real_metric is None:
        print(f"ERROR: Could not extract {metric} from results")
        print(f"Available metrics: {real_results}")
        sys.exit(1)
    
    print(f"\nReal data results:")
    for k, v in real_results.items():
        if v is not None:
            print(f"  {k}: {v}")
    
    # Run on permuted data
    print(f"\n[2/2] Running backtests on {n_permutations} permuted datasets...")
    
    perm_metrics = []
    perm_results_all = []
    perm_better_count = 1  # Start at 1 for conservative estimate
    
    for perm_id in tqdm(range(n_permutations), desc="Permutation backtests"):
        perm_data_dir = str(MCPT_DATA / f'perm_{perm_id:04d}' / 'binance')
        
        if not Path(perm_data_dir).exists():
            continue
        
        result = run_backtest_and_extract_metrics(
            strategy=strategy,
            pairs=pairs,
            timeframe=timeframe,
            timerange=timerange,
            data_dir=perm_data_dir,
            config_path=config_path
        )
        
        if result is not None:
            perm_results_all.append(result)
            perm_metric = result.get(metric)
            
            if perm_metric is not None:
                perm_metrics.append(perm_metric)
                if perm_metric >= real_metric:
                    perm_better_count += 1
    
    if len(perm_metrics) == 0:
        print("ERROR: No valid permutation results")
        sys.exit(1)
    
    # Calculate p-value
    p_value = perm_better_count / (len(perm_metrics) + 1)
    
    # Compile results
    results = {
        'strategy': strategy,
        'pairs': pairs,
        'timeframe': timeframe,
        'timerange': timerange,
        'metric': metric,
        'real_metric_value': real_metric,
        'real_results': real_results,
        'n_permutations': len(perm_metrics),
        'perm_better_count': perm_better_count,
        'p_value': p_value,
        'perm_metrics': perm_metrics,
        'perm_mean': float(np.mean(perm_metrics)),
        'perm_std': float(np.std(perm_metrics)),
        'perm_median': float(np.median(perm_metrics)),
        'perm_min': float(np.min(perm_metrics)),
        'perm_max': float(np.max(perm_metrics)),
        'timestamp': datetime.now().isoformat()
    }
    
    # Print summary
    print(f"\n{'='*60}")
    print(f"MCPT RESULTS")
    print(f"{'='*60}")
    print(f"Real {metric}:       {real_metric:.4f}")
    print(f"Permuted mean:       {results['perm_mean']:.4f}")
    print(f"Permuted std:        {results['perm_std']:.4f}")
    print(f"Permuted range:      [{results['perm_min']:.4f}, {results['perm_max']:.4f}]")
    print(f"Times perm >= real:  {perm_better_count} / {len(perm_metrics)+1}")
    print(f"\n{'*'*60}")
    print(f"*** P-VALUE: {p_value:.4f} ***")
    print(f"{'*'*60}")
    
    if p_value < 0.05:
        print("\n✅ P-value < 0.05: Strategy shows SIGNIFICANT EDGE")
        print("   Performance is unlikely to be due to random chance.")
        print("   This is GOOD - the strategy may have genuine predictive power.")
    elif p_value < 0.10:
        print("\n⚠️  P-value between 0.05-0.10: MARGINAL SIGNIFICANCE")
        print("   The strategy may have some edge, but results are borderline.")
        print("   Consider more out-of-sample testing and/or more permutations.")
    else:
        print("\n❌ P-value >= 0.10: NO SIGNIFICANT EDGE DETECTED")
        print("   Performance could be achieved on random/shuffled data.")
        print("   This suggests OVERFITTING to specific price patterns.")
        print("   Consider simplifying the strategy or using walk-forward optimization.")
    
    print(f"{'='*60}\n")
    
    # Save results
    MCPT_RESULTS.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    result_file = MCPT_RESULTS / f'mcpt_{strategy}_{timestamp}.json'
    
    with open(result_file, 'w') as f:
        json.dump(results, f, indent=2, default=str)
    
    print(f"Results saved to: {result_file}")
    
    # Generate plot
    try:
        import matplotlib.pyplot as plt
        
        plt.style.use('dark_background')
        fig, ax = plt.subplots(figsize=(12, 6))
        
        ax.hist(perm_metrics, bins=30, color='steelblue', alpha=0.7,
                label='Permutation Distribution', edgecolor='white')
        ax.axvline(real_metric, color='red', linewidth=2, linestyle='--',
                   label=f'Real: {real_metric:.2f}')
        ax.axvline(results['perm_mean'], color='yellow', linewidth=1,
                   linestyle=':', label=f'Mean: {results["perm_mean"]:.2f}')
        
        ax.set_xlabel(metric)
        ax.set_ylabel('Frequency')
        ax.set_title(
            f'MCPT: {strategy}\n'
            f'P-value: {p_value:.4f} | '
            f'Real: {real_metric:.2f} vs Perm Mean: {results["perm_mean"]:.2f}'
        )
        ax.legend()
        ax.grid(True, alpha=0.3)
        
        plot_file = MCPT_RESULTS / f'mcpt_{strategy}_{timestamp}.png'
        plt.tight_layout()
        plt.savefig(plot_file, dpi=150)
        plt.close()
        
        print(f"Plot saved to: {plot_file}")
        
    except ImportError:
        print("matplotlib not available, skipping plot")
    
    return results


def main():
    parser = argparse.ArgumentParser(
        description='Run MCPT for MeanReversionRSI or other strategies'
    )
    
    parser.add_argument('--strategy', '-s', default='MeanReversionRSI',
                        help='Strategy name (default: MeanReversionRSI)')
    parser.add_argument('--pairs', '-p', nargs='+', default=['BTC/USDT:USDT'],
                        help='Trading pairs')
    parser.add_argument('--timeframe', '-t', default='1h',
                        help='Timeframe (default: 1h)')
    parser.add_argument('--timerange', '-r', required=True,
                        help='Time range (YYYYMMDD-YYYYMMDD)')
    parser.add_argument('-n', '--n-permutations', type=int, default=50,
                        help='Number of permutations (default: 50)')
    parser.add_argument('--metric', '-m', default='profit_total_pct',
                        help='Metric to compare (default: profit_total_pct)')
    parser.add_argument('--config', '-c', default='user_data/config.json',
                        help='Config file path')
    
    args = parser.parse_args()
    
    run_strategy_mcpt(
        strategy=args.strategy,
        pairs=args.pairs,
        timeframe=args.timeframe,
        timerange=args.timerange,
        n_permutations=args.n_permutations,
        metric=args.metric,
        config_path=args.config
    )


if __name__ == '__main__':
    main()
