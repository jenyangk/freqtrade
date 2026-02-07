#!/usr/bin/env python3
"""
Monte Carlo Permutation Test (MCPT) Runner for Freqtrade Strategies

This script runs backtests on the original data and all permutated datasets,
then calculates p-values to assess whether the strategy's performance could
be achieved by chance (indicating overfitting).

Key Concept:
- Run strategy on real data -> get real performance metric (e.g., profit factor, sharpe)
- Run strategy on N permuted datasets -> get distribution of performance metrics
- P-value = (count of permuted metrics >= real metric) / N
- Low p-value (e.g., < 0.05) suggests the strategy has genuine edge
- High p-value suggests the strategy is likely overfit to specific price patterns

Usage:
    python run_mcpt.py --strategy MeanReversionRSI --pairs BTC/USDT:USDT --timerange 20230101-20241201

Requirements:
    - Permutated data must be generated first using generate_permutations.py
    - Strategy must be in user_data/strategies/
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import numpy as np
import pandas as pd
from tqdm import tqdm

# Paths
FREQTRADE_ROOT = Path(__file__).parent.parent.parent.parent
USER_DATA = FREQTRADE_ROOT / 'user_data'
DATA_DIR = USER_DATA / 'data'
MCPT_DATA = DATA_DIR / 'mcpt_permutations'
MCPT_RESULTS = USER_DATA / 'backtest_results' / 'mcpt'


def run_backtest(
    strategy: str,
    pairs: List[str],
    timeframe: str,
    timerange: str,
    data_dir: Path,
    config: str = 'user_data/config.json',
    extra_args: Optional[List[str]] = None
) -> Optional[Dict]:
    """
    Run a freqtrade backtest and return the results.
    
    Returns
    -------
    dict or None
        Backtest results dictionary, or None if failed
    """
    cmd = [
        sys.executable, '-m', 'freqtrade', 'backtesting',
        '--strategy', strategy,
        '-c', config,
        '--datadir', str(data_dir),
        '--timeframe', timeframe,
        '--timerange', timerange,
        '--pairs', *pairs,
        '--export', 'none',  # Don't export trades to avoid clutter
        '--cache', 'none',   # Don't use cache
    ]
    
    if extra_args:
        cmd.extend(extra_args)
    
    try:
        result = subprocess.run(
            cmd,
            cwd=FREQTRADE_ROOT,
            capture_output=True,
            text=True,
            timeout=600  # 10 minute timeout per backtest
        )
        
        if result.returncode != 0:
            # Try to extract error
            if 'Error' in result.stderr:
                print(f"Backtest error: {result.stderr[-500:]}")
            return None
        
        # Parse results from stdout
        return parse_backtest_output(result.stdout)
        
    except subprocess.TimeoutExpired:
        print("Backtest timed out")
        return None
    except Exception as e:
        print(f"Backtest failed: {e}")
        return None


def parse_backtest_output(output: str) -> Dict:
    """
    Parse freqtrade backtest output to extract key metrics.
    
    This is a simple parser - for more robust parsing, you could
    use the JSON export option.
    """
    metrics = {
        'profit_total': None,
        'profit_total_pct': None,
        'profit_factor': None,
        'sharpe_ratio': None,
        'sortino_ratio': None,
        'calmar_ratio': None,
        'max_drawdown': None,
        'win_rate': None,
        'total_trades': None,
    }
    
    lines = output.split('\n')
    
    for line in lines:
        line_lower = line.lower()
        
        if 'total profit' in line_lower and '%' in line:
            try:
                # Extract percentage
                parts = line.split()
                for i, p in enumerate(parts):
                    if '%' in p:
                        pct = p.replace('%', '').replace(',', '')
                        metrics['profit_total_pct'] = float(pct)
                        break
            except:
                pass
        
        if 'profit factor' in line_lower:
            try:
                parts = line.split()
                for i, p in enumerate(parts):
                    if p.replace('.', '').replace('-', '').isdigit():
                        metrics['profit_factor'] = float(p)
                        break
            except:
                pass
        
        if 'sharpe' in line_lower:
            try:
                parts = line.split()
                for i, p in enumerate(parts):
                    try:
                        val = float(p)
                        metrics['sharpe_ratio'] = val
                        break
                    except:
                        continue
            except:
                pass
        
        if 'sortino' in line_lower:
            try:
                parts = line.split()
                for i, p in enumerate(parts):
                    try:
                        val = float(p)
                        metrics['sortino_ratio'] = val
                        break
                    except:
                        continue
            except:
                pass
        
        if 'max drawdown' in line_lower:
            try:
                parts = line.split()
                for i, p in enumerate(parts):
                    if '%' in p:
                        pct = p.replace('%', '').replace(',', '')
                        metrics['max_drawdown'] = float(pct)
                        break
            except:
                pass
        
        if 'win rate' in line_lower or 'wins/draws/losses' in line_lower:
            try:
                # Look for pattern like "150 / 0 / 50"
                if '/' in line:
                    parts = line.split('/')
                    if len(parts) >= 3:
                        wins = int(parts[0].split()[-1])
                        losses = int(parts[2].split()[0])
                        if wins + losses > 0:
                            metrics['win_rate'] = wins / (wins + losses) * 100
                            metrics['total_trades'] = wins + losses
            except:
                pass
        
        if 'total trades' in line_lower:
            try:
                parts = line.split()
                for i, p in enumerate(parts):
                    if p.isdigit():
                        metrics['total_trades'] = int(p)
                        break
            except:
                pass
    
    return metrics


def run_mcpt(
    strategy: str,
    pairs: List[str],
    timeframe: str,
    timerange: str,
    config: str = 'user_data/config.json',
    n_permutations: Optional[int] = None,
    metric: str = 'profit_total_pct',
    extra_args: Optional[List[str]] = None
) -> Dict:
    """
    Run Monte Carlo Permutation Test for a strategy.
    
    Parameters
    ----------
    strategy : str
        Name of the strategy to test
    pairs : List[str]
        Trading pairs to use
    timeframe : str
        Timeframe to use
    timerange : str
        Time range for backtest (format: YYYYMMDD-YYYYMMDD)
    config : str
        Path to config file
    n_permutations : int, optional
        Number of permutations to use (default: use all available)
    metric : str
        Metric to use for comparison (default: profit_total_pct)
    extra_args : List[str], optional
        Extra arguments to pass to freqtrade backtest
        
    Returns
    -------
    dict
        MCPT results including p-value and distributions
    """
    
    # Check if permutations exist
    if not MCPT_DATA.exists():
        raise FileNotFoundError(
            f"Permutation data not found at {MCPT_DATA}. "
            "Please run generate_permutations.py first."
        )
    
    # Load metadata
    metadata_file = MCPT_DATA / 'metadata.json'
    if metadata_file.exists():
        with open(metadata_file) as f:
            perm_metadata = json.load(f)
        available_perms = perm_metadata.get('n_permutations', 0)
    else:
        # Count directories
        available_perms = len(list(MCPT_DATA.glob('perm_*')))
    
    if n_permutations is None:
        n_permutations = available_perms
    else:
        n_permutations = min(n_permutations, available_perms)
    
    print(f"\n{'='*60}")
    print(f"Monte Carlo Permutation Test")
    print(f"{'='*60}")
    print(f"Strategy:      {strategy}")
    print(f"Pairs:         {', '.join(pairs)}")
    print(f"Timeframe:     {timeframe}")
    print(f"Timerange:     {timerange}")
    print(f"Permutations:  {n_permutations}")
    print(f"Metric:        {metric}")
    print(f"{'='*60}\n")
    
    # Run backtest on real data
    print("Running backtest on REAL data...")
    real_data_dir = DATA_DIR / 'binance'
    real_results = run_backtest(
        strategy=strategy,
        pairs=pairs,
        timeframe=timeframe,
        timerange=timerange,
        data_dir=real_data_dir,
        config=config,
        extra_args=extra_args
    )
    
    if real_results is None:
        raise RuntimeError("Failed to run backtest on real data")
    
    real_metric = real_results.get(metric)
    if real_metric is None:
        raise ValueError(f"Could not extract metric '{metric}' from backtest results")
    
    print(f"\nReal data {metric}: {real_metric:.4f}")
    print(f"Real data results: {real_results}")
    
    # Run backtests on permuted data
    print(f"\nRunning backtests on {n_permutations} permuted datasets...")
    
    perm_results = []
    perm_metrics = []
    perm_better_count = 1  # Start at 1 for conservative p-value
    
    for perm_id in tqdm(range(n_permutations), desc="Permutation backtests"):
        perm_data_dir = MCPT_DATA / f'perm_{perm_id:04d}' / 'binance'
        
        if not perm_data_dir.exists():
            print(f"\nWarning: Permutation {perm_id} not found, skipping")
            continue
        
        result = run_backtest(
            strategy=strategy,
            pairs=pairs,
            timeframe=timeframe,
            timerange=timerange,
            data_dir=perm_data_dir,
            config=config,
            extra_args=extra_args
        )
        
        if result is not None:
            perm_results.append(result)
            perm_metric = result.get(metric)
            
            if perm_metric is not None:
                perm_metrics.append(perm_metric)
                
                # Check if permutation beats real
                if perm_metric >= real_metric:
                    perm_better_count += 1
    
    # Calculate p-value
    if len(perm_metrics) == 0:
        raise RuntimeError("No valid permutation results obtained")
    
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
        'perm_mean': np.mean(perm_metrics),
        'perm_std': np.std(perm_metrics),
        'perm_median': np.median(perm_metrics),
        'perm_min': np.min(perm_metrics),
        'perm_max': np.max(perm_metrics),
        'timestamp': datetime.now().isoformat()
    }
    
    # Print summary
    print(f"\n{'='*60}")
    print(f"MCPT Results")
    print(f"{'='*60}")
    print(f"Real {metric}:      {real_metric:.4f}")
    print(f"Permuted mean:      {results['perm_mean']:.4f}")
    print(f"Permuted std:       {results['perm_std']:.4f}")
    print(f"Permuted range:     [{results['perm_min']:.4f}, {results['perm_max']:.4f}]")
    print(f"Times perm >= real: {perm_better_count}")
    print(f"\n*** P-VALUE: {p_value:.4f} ***")
    print(f"{'='*60}")
    
    if p_value < 0.05:
        print("\n✅ P-value < 0.05: Strategy shows statistically significant edge")
        print("   The performance is unlikely to be due to random chance.")
    elif p_value < 0.10:
        print("\n⚠️  P-value between 0.05 and 0.10: Marginal significance")
        print("   The strategy may have some edge, but be cautious.")
    else:
        print("\n❌ P-value >= 0.10: Strategy performance is NOT statistically significant")
        print("   The performance could likely be achieved by chance on random data.")
        print("   This suggests potential OVERFITTING to the specific price history.")
    
    return results


def save_results(results: Dict, output_dir: Optional[Path] = None):
    """Save MCPT results to JSON and generate visualization."""
    if output_dir is None:
        output_dir = MCPT_RESULTS
    
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Save JSON results
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"mcpt_{results['strategy']}_{timestamp}.json"
    filepath = output_dir / filename
    
    with open(filepath, 'w') as f:
        json.dump(results, f, indent=2, default=str)
    
    print(f"\nResults saved to: {filepath}")
    
    # Generate plot
    try:
        import matplotlib.pyplot as plt
        
        plt.style.use('dark_background')
        fig, ax = plt.subplots(figsize=(12, 6))
        
        # Histogram of permutation results
        ax.hist(results['perm_metrics'], bins=30, color='steelblue', 
                alpha=0.7, label='Permutation Distribution', edgecolor='white')
        
        # Real result line
        ax.axvline(results['real_metric_value'], color='red', linewidth=2, 
                   linestyle='--', label=f'Real: {results["real_metric_value"]:.2f}')
        
        # Mean line
        ax.axvline(results['perm_mean'], color='yellow', linewidth=1,
                   linestyle=':', label=f'Mean: {results["perm_mean"]:.2f}')
        
        ax.set_xlabel(results['metric'])
        ax.set_ylabel('Frequency')
        ax.set_title(
            f"MCPT: {results['strategy']} | P-value: {results['p_value']:.4f}\n"
            f"Pairs: {', '.join(results['pairs'])} | Timerange: {results['timerange']}"
        )
        ax.legend()
        ax.grid(True, alpha=0.3)
        
        plot_path = output_dir / f"mcpt_{results['strategy']}_{timestamp}.png"
        plt.tight_layout()
        plt.savefig(plot_path, dpi=150)
        plt.close()
        
        print(f"Plot saved to: {plot_path}")
        
    except ImportError:
        print("matplotlib not available, skipping plot generation")
    
    return filepath


def main():
    parser = argparse.ArgumentParser(
        description='Run Monte Carlo Permutation Test for a Freqtrade strategy',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run MCPT with 100 permutations
  python run_mcpt.py --strategy MeanReversionRSI --pairs BTC/USDT:USDT --timerange 20230101-20241201

  # Run with specific metric
  python run_mcpt.py --strategy MyStrategy --pairs BTC/USDT:USDT ETH/USDT:USDT -n 50 --metric sharpe_ratio

  # Run with extra freqtrade arguments
  python run_mcpt.py --strategy MyStrategy --pairs BTC/USDT:USDT -- --stake-amount unlimited
        """
    )
    
    parser.add_argument('--strategy', '-s', required=True,
                        help='Strategy name to test')
    parser.add_argument('--pairs', '-p', nargs='+', required=True,
                        help='Trading pairs to use')
    parser.add_argument('--timeframe', '-t', default='1h',
                        help='Timeframe (default: 1h)')
    parser.add_argument('--timerange', '-r', required=True,
                        help='Time range (format: YYYYMMDD-YYYYMMDD)')
    parser.add_argument('--config', '-c', default='user_data/config.json',
                        help='Config file path')
    parser.add_argument('-n', '--n-permutations', type=int,
                        help='Number of permutations to use (default: all available)')
    parser.add_argument('--metric', '-m', default='profit_total_pct',
                        choices=['profit_total_pct', 'profit_factor', 'sharpe_ratio', 
                                'sortino_ratio', 'win_rate', 'max_drawdown'],
                        help='Metric to compare (default: profit_total_pct)')
    parser.add_argument('--no-save', action='store_true',
                        help='Do not save results to file')
    
    args, extra = parser.parse_known_args()
    
    # Remove leading '--' if present in extra args
    if extra and extra[0] == '--':
        extra = extra[1:]
    
    results = run_mcpt(
        strategy=args.strategy,
        pairs=args.pairs,
        timeframe=args.timeframe,
        timerange=args.timerange,
        config=args.config,
        n_permutations=args.n_permutations,
        metric=args.metric,
        extra_args=extra if extra else None
    )
    
    if not args.no_save:
        save_results(results)


if __name__ == '__main__':
    main()
