import json
import pandas as pd
import matplotlib.pyplot as plt
import sys
import os
import argparse
import zipfile
from datetime import datetime
import matplotlib.dates as mdates

def load_backtest_data(filepath):
    if filepath.endswith('.zip'):
        try:
            with zipfile.ZipFile(filepath, 'r') as z:
                # Find the backtest result json file
                json_files = [f for f in z.namelist() if f.endswith('.json') and 'backtest-result' in f]
                if not json_files:
                    raise ValueError("No backtest result JSON found in zip file")
                
                # Prefer the one that is not .meta.json if possible
                target_file = next((f for f in json_files if not f.endswith('.meta.json')), json_files[0])
                print(f"Extracting {target_file} from zip...")
                with z.open(target_file) as f:
                    return json.load(f)
        except zipfile.BadZipFile:
            print(f"Error: {filepath} is not a valid zip file.")
            sys.exit(1)
    else:
        with open(filepath, 'r') as f:
            return json.load(f)

def get_strategy_data(data):
    # Support both structure with 'strategy' key and direct metrics
    if 'strategy' in data:
        # Get the first strategy
        strategy_name = list(data['strategy'].keys())[0]
        return data['strategy'][strategy_name], strategy_name
    return data, "Unknown Strategy"

def generate_charts(strategy_data, output_dir):
    charts = {}
    
    # Prepare DataFrames
    trades = pd.DataFrame(strategy_data['trades'])
    if not trades.empty:
        trades['open_date'] = pd.to_datetime(trades['open_date'])
        trades['close_date'] = pd.to_datetime(trades['close_date'])
        trades['profit_abs'] = pd.to_numeric(trades['profit_abs'])
        trades['profit_ratio'] = pd.to_numeric(trades['profit_ratio'])
    
    daily_profit = pd.DataFrame(strategy_data['daily_profit'], columns=['date', 'profit_abs'])
    if not daily_profit.empty:
        daily_profit['date'] = pd.to_datetime(daily_profit['date'])
        daily_profit = daily_profit.set_index('date')
        daily_profit['cumulative_profit'] = daily_profit['profit_abs'].cumsum()

    # 1. Cumulative Profit Chart
    if not daily_profit.empty:
        plt.figure(figsize=(12, 6))
        plt.plot(daily_profit.index, daily_profit['cumulative_profit'], label='Cumulative Profit', linewidth=2)
        plt.title('Cumulative Profit Over Time', fontsize=14)
        plt.xlabel('Date', fontsize=12)
        plt.ylabel('Profit', fontsize=12)
        plt.grid(True, linestyle='--', alpha=0.7)
        plt.legend()
        plt.tight_layout()
        chart_path = os.path.join(output_dir, 'cumulative_profit.png')
        plt.savefig(chart_path)
        plt.close()
        charts['cumulative_profit'] = 'cumulative_profit.png'

    # 2. Profit Distribution Chart
    if not trades.empty:
        plt.figure(figsize=(12, 6))
        plt.hist(trades['profit_ratio'] * 100, bins=50, alpha=0.7, color='#2ca02c', edgecolor='black')
        plt.title('Profit Distribution', fontsize=14)
        plt.xlabel('Profit Ratio (%)', fontsize=12)
        plt.ylabel('Frequency', fontsize=12)
        plt.grid(True, linestyle='--', alpha=0.7)
        plt.tight_layout()
        chart_path = os.path.join(output_dir, 'profit_distribution.png')
        plt.savefig(chart_path)
        plt.close()
        charts['profit_distribution'] = 'profit_distribution.png'

    # 3. Trades Log Chart (Scatter)
    if not trades.empty:
        plt.figure(figsize=(12, 6))
        colors = trades['profit_ratio'].apply(lambda x: '#2ca02c' if x > 0 else '#d62728')
        plt.scatter(trades['close_date'], trades['profit_ratio'] * 100, c=colors, alpha=0.6, s=50)
        plt.title('Trades Log', fontsize=14)
        plt.xlabel('Close Date', fontsize=12)
        plt.ylabel('Profit Ratio (%)', fontsize=12)
        plt.grid(True, linestyle='--', alpha=0.7)
        plt.gcf().autofmt_xdate()
        plt.tight_layout()
        chart_path = os.path.join(output_dir, 'trades_log.png')
        plt.savefig(chart_path)
        plt.close()
        charts['trades_log'] = 'trades_log.png'

    return charts

def format_value(data, key, fmt=None):
    val = data.get(key)
    if val is None:
        return "N/A"
    if callable(fmt):
        return fmt(data)
    if fmt == "pct":
        return f"{val * 100:.2f}%"
    if fmt == "float":
        return f"{val:.4f}"
    if fmt == "int":
        return str(val)
    return str(val)

def generate_markdown_report(data, strategy_name, charts, output_dir):
    report_path = os.path.join(output_dir, 'report.md')
    
    metrics_groups = {
        "Overview": [
            ("Time Range", lambda d: f"{d.get('backtest_start')} - {d.get('backtest_end')} ({d.get('backtest_days')} days)"),
            ("Total Trades", lambda d: str(d.get('total_trades'))),
            ("Total Volume", lambda d: f"{d.get('total_volume'):.4f}"),
            ("Avg Stake Amount", lambda d: f"{d.get('avg_stake_amount'):.4f}"),
            ("Starting Balance", lambda d: f"{d.get('starting_balance'):.4f}"),
            ("Final Balance", lambda d: f"{d.get('final_balance'):.4f}"),
        ],
        "Profitability": [
            ("Total Profit (Abs)", lambda d: f"{d.get('profit_total_abs'):.8f}"),
            ("Total Profit (%)", lambda d: f"{d.get('profit_total_pct', 0):.2f}%"),
            ("Profit Factor", lambda d: f"{d.get('profit_factor', 0):.2f}"),
            ("CAGR", lambda d: f"{d.get('cagr', 0):.2f}%"),
            ("Market Change", lambda d: f"{d.get('market_change', 0):.2f}%"),
            ("Win Rate", lambda d: f"{d.get('wins', 0) / d.get('total_trades', 1) * 100:.2f}%" if d.get('total_trades') else "0%"),
        ],
        "Drawdown": [
            ("Max Drawdown", lambda d: f"{d.get('max_drawdown', 0) * 100:.2f}%"),
            ("Max Drawdown (Account)", lambda d: f"{d.get('max_drawdown_account', 0) * 100:.2f}%"),
            ("Max Drawdown (Abs)", lambda d: f"{d.get('max_drawdown_abs'):.8f}"),
            ("Drawdown Start", lambda d: str(d.get('drawdown_start'))),
            ("Drawdown End", lambda d: str(d.get('drawdown_end'))),
        ],
        "Trade Statistics": [
            ("Wins", lambda d: str(d.get('wins'))),
            ("Losses", lambda d: str(d.get('losses'))),
            ("Draws", lambda d: str(d.get('draws'))),
            ("Holding Avg", lambda d: str(d.get('holding_avg'))),
            ("Winner Holding Avg", lambda d: str(d.get('winner_holding_avg'))),
            ("Loser Holding Avg", lambda d: str(d.get('loser_holding_avg'))),
            ("Trades per Day", lambda d: str(d.get('trades_per_day'))),
        ],
        "Daily Stats": [
            ("Best Day", lambda d: f"{d.get('backtest_best_day', 0):.2%}"),
            ("Worst Day", lambda d: f"{d.get('backtest_worst_day', 0):.2%}"),
            ("Winning Days", lambda d: str(d.get('winning_days'))),
            ("Losing Days", lambda d: str(d.get('losing_days'))),
            ("Draw Days", lambda d: str(d.get('draw_days'))),
        ]
    }

    with open(report_path, 'w') as f:
        f.write(f"# Backtest Report: {strategy_name}\n\n")
        
        # Executive Summary
        f.write("## Executive Summary\n\n")
        f.write(f"**Total Profit:** {data.get('profit_total_pct', 0):.2f}% | ")
        f.write(f"**Win Rate:** {data.get('wins', 0) / data.get('total_trades', 1) * 100:.2f}% | ")
        f.write(f"**Profit Factor:** {data.get('profit_factor', 0):.2f} | ")
        f.write(f"**Max Drawdown:** {data.get('max_drawdown', 0) * 100:.2f}%\n\n")
        f.write("---\n\n")

        # Metrics Tables
        f.write("## Detailed Metrics\n\n")
        
        # Create a 2-column layout for metrics groups if possible (Markdown doesn't support columns natively, so we use sequential tables)
        for group_name, metrics in metrics_groups.items():
            f.write(f"### {group_name}\n\n")
            f.write("| Metric | Value |\n")
            f.write("|---|---|\n")
            for label, accessor in metrics:
                try:
                    value = accessor(data)
                except Exception:
                    value = "N/A"
                f.write(f"| {label} | {value} |\n")
            f.write("\n")

        # Best/Worst Pair
        f.write("## Pair Performance Highlights\n\n")
        if 'best_pair' in data:
            bp = data['best_pair']
            f.write(f"**Best Pair:** `{bp.get('key')}`\n")
            profit_pct = bp.get('profit_sum_pct')
            profit_str = f"{profit_pct:.2f}%" if profit_pct is not None else "N/A"
            f.write(f"- Profit: {profit_str}\n")
            f.write(f"- Trades: {bp.get('trades')}\n\n")
        
        if 'worst_pair' in data:
            wp = data['worst_pair']
            f.write(f"**Worst Pair:** `{wp.get('key')}`\n")
            profit_pct = wp.get('profit_sum_pct')
            profit_str = f"{profit_pct:.2f}%" if profit_pct is not None else "N/A"
            f.write(f"- Profit: {profit_str}\n")
            f.write(f"- Trades: {wp.get('trades')}\n\n")

        # Charts
        f.write("## Charts\n\n")
        if 'cumulative_profit' in charts:
            f.write(f"![Cumulative Profit]({charts['cumulative_profit']})\n\n")
        if 'profit_distribution' in charts:
            f.write(f"![Profit Distribution]({charts['profit_distribution']})\n\n")
        if 'trades_log' in charts:
            f.write(f"![Trades Log]({charts['trades_log']})\n\n")

        # Monthly Breakdown
        f.write("## Monthly Breakdown\n\n")
        if 'daily_profit' in data and data['daily_profit']:
            daily_profit = pd.DataFrame(data['daily_profit'], columns=['date', 'profit_abs'])
            daily_profit['date'] = pd.to_datetime(daily_profit['date'])
            daily_profit['month'] = daily_profit['date'].dt.to_period('M')
            monthly_profit = daily_profit.groupby('month')['profit_abs'].sum().reset_index()
            
            f.write("| Month | Profit (Abs) |\n")
            f.write("|---|---|\n")
            for _, row in monthly_profit.iterrows():
                f.write(f"| {row['month']} | {row['profit_abs']:.8f} |\n")
            f.write("\n")

        # Results per pair
        f.write("## Results per Pair\n\n")
        if 'results_per_pair' in data:
            f.write("| Pair | Trades | Win | Loss | Draw | Profit Mean | Profit Sum | Profit Total % |\n")
            f.write("|---|---|---|---|---|---|---|---|\n")
            for pair_data in data['results_per_pair']:
                if pair_data['key'] == 'TOTAL':
                    continue
                
                profit_mean = pair_data.get('profit_mean', 0)
                profit_sum = pair_data.get('profit_sum', 0)
                profit_total_pct = pair_data.get('profit_total_pct', 0)
                
                f.write(f"| {pair_data['key']} | {pair_data.get('trades', 0)} | {pair_data.get('wins', 0)} | {pair_data.get('losses', 0)} | {pair_data.get('draws', 0)} | {profit_mean:.8f} | {profit_sum:.8f} | {profit_total_pct:.2f}% |\n")
            f.write("\n")

    return report_path

def main():
    parser = argparse.ArgumentParser(description='Generate Markdown report from Freqtrade backtest result.')
    parser.add_argument('file', help='Path to backtest-result.json or .zip file')
    parser.add_argument('--output', help='Output directory', default='user_data/reports')
    
    args = parser.parse_args()
    
    if not os.path.exists(args.output):
        os.makedirs(args.output)
        
    print(f"Loading data from {args.file}...")
    data = load_backtest_data(args.file)
    
    strategy_data, strategy_name = get_strategy_data(data)
    
    print(f"Generating charts for {strategy_name}...")
    charts = generate_charts(strategy_data, args.output)
    
    print("Generating markdown report...")
    report_path = generate_markdown_report(strategy_data, strategy_name, charts, args.output)
    
    print(f"Report generated at: {report_path}")

if __name__ == "__main__":
    main()
