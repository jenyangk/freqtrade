#!/usr/bin/env python3
"""
Generate Permutated Data for Monte Carlo Permutation Tests

This script reads the original Binance data and creates multiple permutated versions
for use in MCPT analysis. The permutated data is stored separately to avoid
contaminating the original downloaded data.

Usage:
    python generate_permutations.py --pairs BTC/USDT:USDT ETH/USDT:USDT --timeframes 1h 4h --n-permutations 100

The output is stored in user_data/data/mcpt_permutations/
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional
import pandas as pd
import numpy as np
from tqdm import tqdm

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent))
from bar_permute import get_permutation, validate_permutation


# Paths
USER_DATA = Path(__file__).parent.parent.parent
DATA_DIR = USER_DATA / 'data'
BINANCE_DATA = DATA_DIR / 'binance' / 'futures'
MCPT_DATA = DATA_DIR / 'mcpt_permutations'


def get_available_pairs(timeframe: str = '1h') -> List[str]:
    """Get list of available trading pairs for a given timeframe."""
    pairs = []
    for f in BINANCE_DATA.glob(f'*-{timeframe}-futures.feather'):
        # Convert filename like "BTC_USDT_USDT-1h-futures.feather" to "BTC/USDT:USDT"
        name = f.stem.replace(f'-{timeframe}-futures', '')
        parts = name.split('_')
        if len(parts) >= 2:
            pair = f"{parts[0]}/{parts[1]}:{parts[2] if len(parts) > 2 else parts[1]}"
            pairs.append(pair)
    return sorted(pairs)


def pair_to_filename(pair: str, timeframe: str) -> str:
    """Convert pair format to filename format."""
    # "BTC/USDT:USDT" -> "BTC_USDT_USDT-1h-futures.feather"
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


def save_permutation(df: pd.DataFrame, pair: str, timeframe: str, perm_id: int):
    """Save a permutated dataset."""
    # Create directory structure
    perm_dir = MCPT_DATA / f'perm_{perm_id:04d}' / 'binance' / 'futures'
    perm_dir.mkdir(parents=True, exist_ok=True)
    
    filename = pair_to_filename(pair, timeframe)
    filepath = perm_dir / filename
    
    # Reset index for feather format
    df_save = df.reset_index()
    df_save.to_feather(filepath)


def generate_permutations(
    pairs: List[str],
    timeframes: List[str],
    n_permutations: int = 100,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    seed_base: int = 42
):
    """
    Generate n permutations of the specified pairs and timeframes.
    
    IMPORTANT: For multi-timeframe consistency, we permute the smallest timeframe
    and then resample to larger timeframes. This ensures that e.g., 4H candles
    are consistent with the underlying 1H candles.
    
    Parameters
    ----------
    pairs : List[str]
        List of trading pairs (e.g., ['BTC/USDT:USDT', 'ETH/USDT:USDT'])
    timeframes : List[str]
        List of timeframes (e.g., ['1h', '4h'])
    n_permutations : int
        Number of permutations to generate
    start_date : str, optional
        Start date for data (YYYY-MM-DD)
    end_date : str, optional
        End date for data (YYYY-MM-DD)
    seed_base : int
        Base random seed for reproducibility
    """
    
    print(f"Generating {n_permutations} permutations for {len(pairs)} pairs and {len(timeframes)} timeframes")
    print(f"Output directory: {MCPT_DATA}")
    
    # Sort timeframes by duration (smallest first)
    sorted_timeframes = sorted(timeframes, key=lambda x: pd.Timedelta(x).total_seconds())
    base_tf = sorted_timeframes[0]
    
    print(f"\nMulti-timeframe mode: Permuting {base_tf}, then resampling to {sorted_timeframes[1:]}")
    
    # Create metadata
    metadata = {
        'generated_at': datetime.now().isoformat(),
        'n_permutations': n_permutations,
        'pairs': pairs,
        'timeframes': timeframes,
        'base_timeframe': base_tf,
        'start_date': start_date,
        'end_date': end_date,
        'seed_base': seed_base,
        'multi_timeframe_consistent': True
    }
    
    MCPT_DATA.mkdir(parents=True, exist_ok=True)
    with open(MCPT_DATA / 'metadata.json', 'w') as f:
        json.dump(metadata, f, indent=2)
    
    # Load base timeframe data only (we'll resample for higher timeframes)
    print("\nLoading base timeframe data...")
    original_data = {}
    for pair in pairs:
        df = load_ohlcv(pair, base_tf)
        if df is not None:
            # Filter date range if specified
            if start_date:
                df = df[df.index >= start_date]
            if end_date:
                df = df[df.index <= end_date]
            
            original_data[pair] = df
            print(f"  Loaded {pair} {base_tf}: {len(df)} bars")
    
    # Generate permutations
    print(f"\nGenerating permutations...")
    
    for perm_id in tqdm(range(n_permutations), desc="Permutations"):
        seed = seed_base + perm_id
        
        for pair in pairs:
            if pair not in original_data:
                continue
                
            df_base = original_data[pair]
            
            # Generate permutation of base timeframe
            perm_df = get_permutation(df_base.copy(), start_index=0, seed=seed)
            
            # Save base timeframe
            save_permutation(perm_df, pair, base_tf, perm_id)
            
            # Resample and save higher timeframes
            for tf in sorted_timeframes[1:]:
                df_resampled = perm_df.resample(tf).agg({
                    'open': 'first',
                    'high': 'max',
                    'low': 'min',
                    'close': 'last',
                    'volume': 'sum' if 'volume' in perm_df.columns else 'first'
                }).dropna()
                
                save_permutation(df_resampled, pair, tf, perm_id)
    
    # Validate first permutation
    print("\nValidating first permutation...")
    for pair in pairs:
        if pair not in original_data:
            continue
            
        real_df = original_data[pair]
        perm_df = get_permutation(real_df.copy(), start_index=0, seed=seed_base)
        
        stats = validate_permutation(real_df, perm_df)
        print(f"\n{pair} {base_tf} (base timeframe):")
        print(f"  Mean  - Real: {stats['real_mean']:.6f}, Perm: {stats['perm_mean']:.6f}")
        print(f"  Std   - Real: {stats['real_std']:.6f}, Perm: {stats['perm_std']:.6f}")
        print(f"  Skew  - Real: {stats['real_skew']:.6f}, Perm: {stats['perm_skew']:.6f}")
        print(f"  Kurt  - Real: {stats['real_kurt']:.6f}, Perm: {stats['perm_kurt']:.6f}")
        
        # Validate higher timeframe consistency
        for tf in sorted_timeframes[1:]:
            perm_resampled = perm_df.resample(tf).agg({
                'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last',
                'volume': 'sum' if 'volume' in perm_df.columns else 'first'
            }).dropna()
            print(f"  {tf} resampled: {len(perm_resampled)} bars (derived from {base_tf})")
    
    print(f"\nGeneration complete! {n_permutations} permutations saved to {MCPT_DATA}")
    print(f"Note: Higher timeframes ({sorted_timeframes[1:]}) are resampled from {base_tf} for consistency.")


def main():
    parser = argparse.ArgumentParser(
        description='Generate permutated price data for Monte Carlo Permutation Tests',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Generate 100 permutations for BTC and ETH on 1h and 4h
  python generate_permutations.py --pairs BTC/USDT:USDT ETH/USDT:USDT --timeframes 1h 4h -n 100

  # List available pairs
  python generate_permutations.py --list-pairs

  # Generate for all available pairs
  python generate_permutations.py --all-pairs --timeframes 1h 4h -n 50
        """
    )
    
    parser.add_argument('--pairs', '-p', nargs='+', 
                        help='Trading pairs to permute (e.g., BTC/USDT:USDT)')
    parser.add_argument('--timeframes', '-t', nargs='+', default=['1h', '4h'],
                        help='Timeframes to process (default: 1h 4h)')
    parser.add_argument('-n', '--n-permutations', type=int, default=100,
                        help='Number of permutations to generate (default: 100)')
    parser.add_argument('--start-date', help='Start date (YYYY-MM-DD)')
    parser.add_argument('--end-date', help='End date (YYYY-MM-DD)')
    parser.add_argument('--seed', type=int, default=42,
                        help='Base random seed (default: 42)')
    parser.add_argument('--list-pairs', action='store_true',
                        help='List available pairs and exit')
    parser.add_argument('--all-pairs', action='store_true',
                        help='Use all available pairs')
    
    args = parser.parse_args()
    
    if args.list_pairs:
        print("Available pairs:")
        for pair in get_available_pairs('1h'):
            print(f"  {pair}")
        return
    
    if args.all_pairs:
        pairs = get_available_pairs(args.timeframes[0])
    elif args.pairs:
        pairs = args.pairs
    else:
        # Default pairs if none specified
        pairs = ['BTC/USDT:USDT', 'ETH/USDT:USDT']
    
    generate_permutations(
        pairs=pairs,
        timeframes=args.timeframes,
        n_permutations=args.n_permutations,
        start_date=args.start_date,
        end_date=args.end_date,
        seed_base=args.seed
    )


if __name__ == '__main__':
    main()
