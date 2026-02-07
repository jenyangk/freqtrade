#!/usr/bin/env python3
"""
Advanced Monte Carlo Permutation Test Runner

This is an improved MCPT runner that:
1. Tests multiple metrics simultaneously (profit, Sharpe, Sortino, drawdown)
2. Provides comprehensive statistical output (effect sizes, confidence intervals)
3. Supports both standard and block permutation
4. Maintains timeframe consistency for multi-timeframe strategies
5. Generates detailed reports and visualizations

Usage:
    python run_advanced_mcpt.py --strategy MeanReversionRSI --pairs BTC/USDT:USDT \
        --timerange 20230101-20241201 -n 100 --permutation-type block
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

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent))

from bar_permute import get_permutation, get_block_permutation, get_permutation_with_resample
from mcpt_stats import (
    MCPTResult, ComprehensiveMCPTResults, 
    calculate_mcpt_result, run_comprehensive_mcpt, METRIC_CONFIG
)

# Paths
FREQTRADE_ROOT = Path(__file__).parent.parent.parent.parent
USER_DATA = FREQTRADE_ROOT / 'user_data'
DATA_DIR = USER_DATA / 'data'
BINANCE_DATA = DATA_DIR / 'binance' / 'futures'
MCPT_DATA = DATA_DIR / 'mcpt_permutations'
MCPT_RESULTS = USER_DATA / 'backtest_results' / 'mcpt'


def pair_to_filename(pair: str, timeframe: str) -> str:
    """Convert pair format to filename format."""
    clean = pair.replace('/', '_').replace(':', '_')
    return f"{clean}-{timeframe}-futures.feather"


def load_ohlcv(pair: str, timeframe: str) -> Optional[pd.DataFrame]:
    """Load OHLCV data for a pair/timeframe."""
    filename = pair_to_filename(pair, timeframe)
    filepath = BINANCE_DATA / filename
    
    if not filepath.exists():
        print(f"Warning: Data file not found: {filepath}")
        return None
    
    df = pd.read_feather(filepath)
    if 'date' in df.columns:
        df.set_index('date', inplace=True)
    
    return df


def generate_permutation_on_the_fly(
    pair: str,
    timeframes: List[str],
    permutation_type: str,
    block_size: int,
    seed: int,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
) -> Dict[str, pd.DataFrame]:
    """
    Generate permutation on-the-fly with proper timeframe relationships.
    
    Returns dict mapping timeframe to permuted DataFrame.
    """
    # Load base timeframe (smallest)
    base_tf = min(timeframes, key=lambda x: pd.Timedelta(x).total_seconds())
    df_base = load_ohlcv(pair, base_tf)
    
    if df_base is None:
        return {}
    
    # Filter date range
    if start_date:
        df_base = df_base[df_base.index >= start_date]
    if end_date:
        df_base = df_base[df_base.index <= end_date]
    
    result = {}
    
    if permutation_type == 'block':
        df_perm = get_block_permutation(df_base, block_size=block_size, seed=seed)
    else:
        df_perm = get_permutation(df_base, seed=seed)
    
    result[base_tf] = df_perm
    
    # Resample to higher timeframes
    for tf in timeframes:
        if tf == base_tf:
            continue
        
        df_higher = df_perm.resample(tf).agg({
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
            'volume': 'sum' if 'volume' in df_perm.columns else 'first'
        }).dropna()
        
        result[tf] = df_higher
    
    return result


def save_permutation_temp(
    perm_data: Dict[str, pd.DataFrame],
    pair: str,
    perm_id: int
) -> Path:
    """Save permutation to temporary directory for backtesting."""
    # Structure: temp_perm_{id}/binance/futures/PAIR.feather
    futures_dir = MCPT_DATA / f'temp_perm_{perm_id:04d}' / 'binance' / 'futures'
    futures_dir.mkdir(parents=True, exist_ok=True)
    
    for tf, df in perm_data.items():
        filename = pair_to_filename(pair, tf)
        df_save = df.reset_index()
        df_save.to_feather(futures_dir / filename)
    
    # Return path to 'binance' level (futures_dir.parent)
    return futures_dir.parent


def cleanup_temp_permutation(perm_id: int):
    """Remove temporary permutation directory."""
    import shutil
    temp_dir = MCPT_DATA / f'temp_perm_{perm_id:04d}'
    if temp_dir.exists():
        shutil.rmtree(temp_dir)


def run_backtest_and_extract_metrics(
    strategy: str,
    pairs: List[str],
    timeframe: str,
    timerange: str,
    data_dir: str,
    config_path: str = 'user_data/config.json'
) -> Optional[Dict]:
    """Run freqtrade backtest and extract metrics."""
    
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
        return parse_backtest_output(output, strategy)
        
    except subprocess.TimeoutExpired:
        return None
    except Exception as e:
        print(f"Error: {e}")
        return None


def parse_backtest_output(output: str, strategy: str) -> Dict:
    """Parse freqtrade backtest output to extract all metrics."""
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
        # Strategy summary line
        if '│' in line and strategy in line:
            parts = [p.strip() for p in line.split('│') if p.strip()]
            if len(parts) >= 5:
                try:
                    metrics['total_trades'] = int(parts[1])
                    metrics['avg_profit_pct'] = float(parts[2])
                    metrics['profit_total_pct'] = float(parts[4])
                except (ValueError, IndexError):
                    pass
        
        # TOTAL line
        if '│ TOTAL │' in line or '│         TOTAL │' in line:
            parts = [p.strip() for p in line.split('│') if p.strip()]
            if len(parts) >= 5:
                try:
                    if metrics['total_trades'] is None:
                        metrics['total_trades'] = int(parts[1])
                    if metrics['avg_profit_pct'] is None:
                        metrics['avg_profit_pct'] = float(parts[2])
                    if metrics['profit_total_pct'] is None:
                        metrics['profit_total_pct'] = float(parts[4])
                except (ValueError, IndexError):
                    pass
        
        # Individual metrics
        if '│ Profit factor' in line:
            match = re.search(r'│\s*(\d+\.?\d*)\s*│', line.split('Profit factor')[-1])
            if match:
                pf = float(match.group(1))
                if 0 <= pf < 1000:
                    metrics['profit_factor'] = pf
        
        if '│ Sharpe' in line:
            match = re.search(r'│\s*(-?\d+\.?\d*)\s*│', line.split('Sharpe')[-1])
            if match:
                metrics['sharpe_ratio'] = float(match.group(1))
        
        if '│ Sortino' in line:
            match = re.search(r'│\s*(-?\d+\.?\d*)\s*│', line.split('Sortino')[-1])
            if match:
                metrics['sortino_ratio'] = float(match.group(1))
        
        if '│ Calmar' in line:
            match = re.search(r'│\s*(-?\d+\.?\d*)\s*│', line.split('Calmar')[-1])
            if match:
                metrics['calmar_ratio'] = float(match.group(1))
        
        if '│ Absolute drawdown' in line or '│ Absolute Drawdown' in line:
            match = re.search(r'\((\d+\.?\d*)%\)', line)
            if match:
                metrics['max_drawdown_pct'] = float(match.group(1))
        
        # Win rate from pattern
        win_match = re.search(r'(\d+)\s+(\d+)\s+(\d+)\s+(\d+\.?\d*)\s*│', line)
        if win_match and metrics['win_rate'] is None:
            try:
                metrics['win_rate'] = float(win_match.group(4))
            except ValueError:
                pass
    
    return metrics


def run_advanced_mcpt(
    strategy: str,
    pairs: List[str],
    timeframe: str,
    timerange: str,
    n_permutations: int = 100,
    permutation_type: str = 'standard',
    block_size: int = 24,
    metrics_to_test: Optional[List[str]] = None,
    config_path: str = 'user_data/config.json',
    generate_on_fly: bool = True
) -> ComprehensiveMCPTResults:
    """
    Run comprehensive MCPT with multiple metrics.
    
    Parameters
    ----------
    strategy : str
        Strategy name
    pairs : List[str]
        Trading pairs
    timeframe : str
        Main timeframe
    timerange : str
        Time range (YYYYMMDD-YYYYMMDD)
    n_permutations : int
        Number of permutations
    permutation_type : str
        'standard' or 'block'
    block_size : int
        Block size for block permutation
    metrics_to_test : list
        Which metrics to test
    config_path : str
        Config file path
    generate_on_fly : bool
        If True, generate permutations on-the-fly (uses less disk)
    
    Returns
    -------
    ComprehensiveMCPTResults
        Complete results with all metrics
    """
    if metrics_to_test is None:
        metrics_to_test = ['profit_total_pct', 'sharpe_ratio', 'sortino_ratio', 'max_drawdown_pct', 'win_rate']
    
    # Parse timerange for date filtering
    start_date = None
    end_date = None
    if '-' in timerange:
        parts = timerange.split('-')
        if len(parts[0]) == 8:
            start_date = f"{parts[0][:4]}-{parts[0][4:6]}-{parts[0][6:]}"
        if len(parts[1]) == 8:
            end_date = f"{parts[1][:4]}-{parts[1][4:6]}-{parts[1][6:]}"
    
    print(f"""
{'='*70}
ADVANCED MONTE CARLO PERMUTATION TEST
{'='*70}
Strategy:         {strategy}
Pairs:            {', '.join(pairs)}
Timeframe:        {timeframe}
Timerange:        {timerange}
Permutations:     {n_permutations}
Permutation Type: {permutation_type} {'(block size: ' + str(block_size) + ')' if permutation_type == 'block' else ''}
Metrics:          {', '.join(metrics_to_test)}
{'='*70}
""")
    
    # Determine timeframes needed (for multi-timeframe strategies)
    # For MeanReversionRSI, we need both 1h and 4h
    timeframes_needed = [timeframe]
    if timeframe == '1h':
        timeframes_needed.append('4h')
    
    # Run on real data
    print("[1/2] Running backtest on REAL data...")
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
        raise RuntimeError("Failed to run backtest on real data")
    
    print("\nReal data results:")
    for k, v in real_results.items():
        if v is not None:
            print(f"  {k}: {v}")
    
    # Run on permuted data
    print(f"\n[2/2] Running backtests on {n_permutations} permuted datasets...")
    
    perm_results_list = []
    
    for perm_id in tqdm(range(n_permutations), desc=f"{permutation_type.capitalize()} permutation backtests"):
        try:
            if generate_on_fly:
                # Generate permutation on-the-fly for all pairs
                temp_dir = None
                all_pairs_generated = True
                
                for pair in pairs:
                    perm_data = generate_permutation_on_the_fly(
                        pair=pair,
                        timeframes=timeframes_needed,
                        permutation_type=permutation_type,
                        block_size=block_size,
                        seed=perm_id,
                        start_date=start_date,
                        end_date=end_date
                    )
                    
                    if not perm_data:
                        all_pairs_generated = False
                        break
                    
                    temp_dir = save_permutation_temp(perm_data, pair, perm_id)
                
                if not all_pairs_generated or temp_dir is None:
                    cleanup_temp_permutation(perm_id)
                    continue
                
                # Run backtest
                result = run_backtest_and_extract_metrics(
                    strategy=strategy,
                    pairs=pairs,
                    timeframe=timeframe,
                    timerange=timerange,
                    data_dir=str(temp_dir),
                    config_path=config_path
                )
                
                # Cleanup
                cleanup_temp_permutation(perm_id)
            else:
                # Use pre-generated permutations
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
                perm_results_list.append(result)
                
        except Exception as e:
            print(f"\nWarning: Permutation {perm_id} failed: {e}")
            cleanup_temp_permutation(perm_id)
            continue
    
    if len(perm_results_list) < 3:
        raise RuntimeError(f"Too few valid permutation results: {len(perm_results_list)}")
    
    print(f"\nCompleted {len(perm_results_list)} valid permutations")
    
    # Calculate comprehensive MCPT results
    mcpt_results = run_comprehensive_mcpt(
        real_metrics=real_results,
        perm_metrics_list=perm_results_list,
        metrics_to_test=metrics_to_test
    )
    
    # Create comprehensive results object
    comprehensive = ComprehensiveMCPTResults(
        strategy_name=strategy,
        pairs=pairs,
        timerange=timerange,
        n_permutations=len(perm_results_list)
    )
    
    for result in mcpt_results.values():
        comprehensive.add_result(result)
    
    # Print results
    print(comprehensive)
    
    return comprehensive


def save_advanced_results(results: ComprehensiveMCPTResults, permutation_type: str):
    """Save results to JSON and generate visualization."""
    MCPT_RESULTS.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    base_name = f"mcpt_advanced_{results.strategy_name}_{permutation_type}_{timestamp}"
    
    # Save JSON
    json_path = MCPT_RESULTS / f"{base_name}.json"
    with open(json_path, 'w') as f:
        json.dump(results.to_dict(), f, indent=2, default=str)
    
    print(f"\nResults saved to: {json_path}")
    
    # Generate comprehensive plot
    try:
        import matplotlib.pyplot as plt
        from matplotlib.gridspec import GridSpec
        
        n_metrics = len(results.results)
        if n_metrics == 0:
            return json_path
        
        plt.style.use('dark_background')
        
        # Create figure with subplots for each metric
        n_cols = min(3, n_metrics)
        n_rows = (n_metrics + n_cols - 1) // n_cols
        
        fig = plt.figure(figsize=(6 * n_cols, 5 * n_rows))
        fig.suptitle(
            f'MCPT Analysis: {results.strategy_name}\n'
            f'Assessment: {results.overall_assessment} | {results.significant_count}/{results.total_count} significant',
            fontsize=14
        )
        
        for idx, (metric_name, result) in enumerate(results.results.items()):
            ax = fig.add_subplot(n_rows, n_cols, idx + 1)
            
            # Histogram
            ax.hist(result.perm_values, bins=25, color='steelblue', alpha=0.7,
                    edgecolor='white', label='Permutations')
            
            # Real value line
            color = 'green' if result.is_significant() else 'red'
            ax.axvline(result.real_value, color=color, linewidth=2, linestyle='--',
                       label=f'Real: {result.real_value:.2f}')
            
            # Mean line
            ax.axvline(result.perm_mean, color='yellow', linewidth=1, linestyle=':',
                       label=f'Mean: {result.perm_mean:.2f}')
            
            # CI shading
            ax.axvspan(result.confidence_interval[0], result.confidence_interval[1],
                       alpha=0.2, color='gray', label='95% CI')
            
            ax.set_xlabel(metric_name)
            ax.set_ylabel('Frequency')
            ax.set_title(f'{metric_name}\np={result.p_value:.4f} | d={result.effect_size:.2f}')
            ax.legend(fontsize=8)
            ax.grid(True, alpha=0.3)
        
        plt.tight_layout()
        
        plot_path = MCPT_RESULTS / f"{base_name}.png"
        plt.savefig(plot_path, dpi=150, bbox_inches='tight')
        plt.close()
        
        print(f"Plot saved to: {plot_path}")
        
    except ImportError:
        print("matplotlib not available, skipping plot")
    
    return json_path


def main():
    parser = argparse.ArgumentParser(
        description='Run advanced MCPT with multiple metrics and permutation types',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Standard permutation test
  python run_advanced_mcpt.py -s MeanReversionRSI -p BTC/USDT:USDT -r 20230101-20241201 -n 50

  # Block permutation (preserves short-term patterns)
  python run_advanced_mcpt.py -s MeanReversionRSI -p BTC/USDT:USDT -r 20230101-20241201 \\
      -n 50 --permutation-type block --block-size 24

  # Test specific metrics
  python run_advanced_mcpt.py -s MyStrategy -p BTC/USDT:USDT -r 20230101-20241201 \\
      -n 100 --metrics profit_total_pct sharpe_ratio sortino_ratio
        """
    )
    
    parser.add_argument('--strategy', '-s', required=True, help='Strategy name')
    parser.add_argument('--pairs', '-p', nargs='+', required=True, help='Trading pairs')
    parser.add_argument('--timeframe', '-t', default='1h', help='Timeframe')
    parser.add_argument('--timerange', '-r', required=True, help='Time range (YYYYMMDD-YYYYMMDD)')
    parser.add_argument('-n', '--n-permutations', type=int, default=50, help='Number of permutations')
    parser.add_argument('--permutation-type', choices=['standard', 'block'], default='standard',
                        help='Type of permutation')
    parser.add_argument('--block-size', type=int, default=24,
                        help='Block size for block permutation (default: 24)')
    parser.add_argument('--metrics', nargs='+',
                        default=['profit_total_pct', 'sharpe_ratio', 'sortino_ratio', 'max_drawdown_pct'],
                        help='Metrics to test')
    parser.add_argument('--config', '-c', default='user_data/config.json', help='Config file')
    parser.add_argument('--use-pregenerated', action='store_true',
                        help='Use pre-generated permutations instead of on-the-fly')
    
    args = parser.parse_args()
    
    results = run_advanced_mcpt(
        strategy=args.strategy,
        pairs=args.pairs,
        timeframe=args.timeframe,
        timerange=args.timerange,
        n_permutations=args.n_permutations,
        permutation_type=args.permutation_type,
        block_size=args.block_size,
        metrics_to_test=args.metrics,
        config_path=args.config,
        generate_on_fly=not args.use_pregenerated
    )
    
    save_advanced_results(results, args.permutation_type)


if __name__ == '__main__':
    main()
