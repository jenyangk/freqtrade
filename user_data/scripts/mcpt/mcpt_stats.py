"""
Advanced MCPT Statistics Module

Provides comprehensive statistical analysis for Monte Carlo Permutation Tests,
including effect sizes, confidence intervals, and multiple metric testing.
"""

import numpy as np
from dataclasses import dataclass, field
from typing import Optional, List, Dict
import json


@dataclass
class MCPTResult:
    """Comprehensive MCPT result for a single metric."""
    metric_name: str
    real_value: float
    perm_values: np.ndarray
    p_value: float
    z_score: float
    confidence_interval: tuple
    alternative: str = 'greater'  # 'greater', 'less', or 'two-sided'
    
    @property
    def perm_mean(self) -> float:
        return float(np.mean(self.perm_values))
    
    @property
    def perm_std(self) -> float:
        return float(np.std(self.perm_values))
    
    @property
    def perm_median(self) -> float:
        return float(np.median(self.perm_values))
    
    @property
    def effect_size(self) -> float:
        """Cohen's d - standardized effect size."""
        if self.perm_std == 0:
            return np.inf if self.real_value > self.perm_mean else 0
        return (self.real_value - self.perm_mean) / self.perm_std
    
    @property
    def percentile_rank(self) -> float:
        """What percentile is the real value in the permutation distribution?"""
        return float(np.mean(self.perm_values <= self.real_value) * 100)
    
    def is_significant(self, alpha: float = 0.05) -> bool:
        return self.p_value < alpha
    
    def interpretation(self) -> str:
        """Human-readable interpretation of the result."""
        effect = self.effect_size
        
        if effect > 3:
            effect_desc = "very large"
        elif effect > 2:
            effect_desc = "large"
        elif effect > 1:
            effect_desc = "medium"
        elif effect > 0.5:
            effect_desc = "small"
        else:
            effect_desc = "negligible"
        
        if self.p_value < 0.01:
            sig_desc = "highly significant"
        elif self.p_value < 0.05:
            sig_desc = "significant"
        elif self.p_value < 0.10:
            sig_desc = "marginally significant"
        else:
            sig_desc = "not significant"
        
        return f"{effect_desc} effect size ({effect:.2f}), {sig_desc} (p={self.p_value:.4f})"
    
    def to_dict(self) -> dict:
        """Convert to JSON-serializable dictionary."""
        return {
            'metric_name': self.metric_name,
            'real_value': self.real_value,
            'perm_mean': self.perm_mean,
            'perm_std': self.perm_std,
            'perm_median': self.perm_median,
            'p_value': self.p_value,
            'z_score': self.z_score,
            'effect_size': self.effect_size,
            'percentile_rank': self.percentile_rank,
            'confidence_interval': list(self.confidence_interval),
            'is_significant': self.is_significant(),
            'interpretation': self.interpretation(),
            'n_permutations': len(self.perm_values)
        }
    
    def __str__(self) -> str:
        sig_marker = "✅" if self.is_significant() else "❌"
        
        # Direction interpretation
        if self.alternative == 'greater':
            comparison = ">" if self.real_value > self.perm_mean else "<"
        elif self.alternative == 'less':
            comparison = "<" if self.real_value < self.perm_mean else ">"
        else:
            comparison = "≠" if abs(self.z_score) > 1.96 else "≈"
        
        return f"""
┌─ {self.metric_name} ─────────────────────────────────
│  Real value:      {self.real_value:>12.4f}
│  Perm mean:       {self.perm_mean:>12.4f} ± {self.perm_std:.4f}
│  Perm range:      [{min(self.perm_values):>8.4f}, {max(self.perm_values):>8.4f}]
│  ─────────────────────────────────────────
│  Z-score:         {self.z_score:>12.2f}
│  Effect size:     {self.effect_size:>12.2f} (Cohen's d)
│  Percentile:      {self.percentile_rank:>12.1f}%
│  P-value:         {self.p_value:>12.4f} {sig_marker}
│  95% CI:          [{self.confidence_interval[0]:.4f}, {self.confidence_interval[1]:.4f}]
│  ─────────────────────────────────────────
│  {self.interpretation()}
└───────────────────────────────────────────────────"""


@dataclass
class ComprehensiveMCPTResults:
    """Container for multiple MCPT results with overall summary."""
    strategy_name: str
    pairs: List[str]
    timerange: str
    n_permutations: int
    results: Dict[str, MCPTResult] = field(default_factory=dict)
    
    def add_result(self, result: MCPTResult):
        self.results[result.metric_name] = result
    
    @property
    def significant_count(self) -> int:
        return sum(1 for r in self.results.values() if r.is_significant())
    
    @property
    def total_count(self) -> int:
        return len(self.results)
    
    @property
    def overall_assessment(self) -> str:
        ratio = self.significant_count / self.total_count if self.total_count > 0 else 0
        
        if ratio >= 0.8:
            return "STRONG"
        elif ratio >= 0.5:
            return "MODERATE"
        elif ratio >= 0.3:
            return "WEAK"
        else:
            return "NONE"
    
    def to_dict(self) -> dict:
        return {
            'strategy_name': self.strategy_name,
            'pairs': self.pairs,
            'timerange': self.timerange,
            'n_permutations': self.n_permutations,
            'significant_count': self.significant_count,
            'total_count': self.total_count,
            'overall_assessment': self.overall_assessment,
            'results': {k: v.to_dict() for k, v in self.results.items()}
        }
    
    def __str__(self) -> str:
        header = f"""
{'='*60}
COMPREHENSIVE MCPT ANALYSIS
{'='*60}
Strategy:     {self.strategy_name}
Pairs:        {', '.join(self.pairs)}
Timerange:    {self.timerange}
Permutations: {self.n_permutations}
{'='*60}
"""
        
        metric_strs = [str(r) for r in self.results.values()]
        
        # Summary
        summary = f"""
{'='*60}
SUMMARY
{'='*60}
Significant metrics: {self.significant_count} / {self.total_count}
Overall evidence:    {self.overall_assessment}
"""
        
        if self.overall_assessment == "STRONG":
            summary += """
✅ STRONG EVIDENCE OF EDGE
   Most metrics show statistically significant outperformance.
   Strategy likely has genuine predictive power.
"""
        elif self.overall_assessment == "MODERATE":
            summary += """
⚠️  MODERATE EVIDENCE OF EDGE
   Some metrics significant, others not.
   Proceed with caution - consider more testing.
"""
        elif self.overall_assessment == "WEAK":
            summary += """
⚠️  WEAK EVIDENCE OF EDGE
   Few metrics show significance.
   High risk of overfitting - reconsider strategy.
"""
        else:
            summary += """
❌ NO EVIDENCE OF EDGE
   Strategy performance is consistent with random chance.
   Likely overfit to historical data.
"""
        
        summary += f"{'='*60}\n"
        
        return header + '\n'.join(metric_strs) + summary


def calculate_mcpt_result(
    metric_name: str,
    real_value: float, 
    perm_values: np.ndarray,
    alternative: str = 'greater'
) -> MCPTResult:
    """
    Calculate comprehensive MCPT statistics for a single metric.
    
    Parameters
    ----------
    metric_name : str
        Name of the metric being tested
    real_value : float
        Value of the metric on real data
    perm_values : np.ndarray
        Array of metric values from permutations
    alternative : str
        Alternative hypothesis: 'greater', 'less', or 'two-sided'
    
    Returns
    -------
    MCPTResult
        Complete statistical result
    """
    perm_values = np.array(perm_values)
    perm_mean = np.mean(perm_values)
    perm_std = np.std(perm_values)
    
    # Z-score
    if perm_std > 0:
        z_score = (real_value - perm_mean) / perm_std
    else:
        z_score = np.inf if real_value > perm_mean else (-np.inf if real_value < perm_mean else 0)
    
    # P-value based on alternative hypothesis
    n_perms = len(perm_values)
    if alternative == 'greater':
        # How often does permutation beat real? (+ 1 for conservative estimate)
        p_value = (np.sum(perm_values >= real_value) + 1) / (n_perms + 1)
    elif alternative == 'less':
        p_value = (np.sum(perm_values <= real_value) + 1) / (n_perms + 1)
    else:  # two-sided
        p_greater = np.sum(perm_values >= real_value)
        p_less = np.sum(perm_values <= real_value)
        p_value = (2 * min(p_greater, p_less) + 1) / (n_perms + 1)
    
    # Confidence interval for permutation distribution
    ci_low = np.percentile(perm_values, 2.5)
    ci_high = np.percentile(perm_values, 97.5)
    
    return MCPTResult(
        metric_name=metric_name,
        real_value=real_value,
        perm_values=perm_values,
        p_value=float(p_value),
        z_score=float(z_score),
        confidence_interval=(float(ci_low), float(ci_high)),
        alternative=alternative
    )


# Metric configuration: which direction is "better"
METRIC_CONFIG = {
    'profit_total_pct': {'alternative': 'greater', 'description': 'Total profit percentage'},
    'profit_factor': {'alternative': 'greater', 'description': 'Ratio of gross profit to gross loss'},
    'sharpe_ratio': {'alternative': 'greater', 'description': 'Risk-adjusted return'},
    'sortino_ratio': {'alternative': 'greater', 'description': 'Downside risk-adjusted return'},
    'calmar_ratio': {'alternative': 'greater', 'description': 'Return relative to max drawdown'},
    'win_rate': {'alternative': 'greater', 'description': 'Percentage of winning trades'},
    'avg_profit_pct': {'alternative': 'greater', 'description': 'Average profit per trade'},
    'max_drawdown_pct': {'alternative': 'less', 'description': 'Maximum peak-to-trough decline'},
    'total_trades': {'alternative': 'two-sided', 'description': 'Number of trades executed'},
}


def run_comprehensive_mcpt(
    real_metrics: dict,
    perm_metrics_list: List[dict],
    metrics_to_test: Optional[List[str]] = None
) -> Dict[str, MCPTResult]:
    """
    Run MCPT on multiple metrics simultaneously.
    
    Parameters
    ----------
    real_metrics : dict
        Dictionary of metric values from real data backtest
    perm_metrics_list : list of dict
        List of metric dictionaries from each permutation
    metrics_to_test : list of str, optional
        Which metrics to test. Default: profit, sharpe, sortino, max_drawdown
    
    Returns
    -------
    dict
        Dictionary mapping metric names to MCPTResult objects
    """
    if metrics_to_test is None:
        metrics_to_test = ['profit_total_pct', 'sharpe_ratio', 'sortino_ratio', 'max_drawdown_pct']
    
    results = {}
    
    for metric in metrics_to_test:
        real_value = real_metrics.get(metric)
        if real_value is None:
            continue
        
        # Collect permutation values, filtering out None
        perm_values = []
        for pm in perm_metrics_list:
            val = pm.get(metric)
            if val is not None:
                perm_values.append(val)
        
        if len(perm_values) < 5:
            print(f"Warning: Too few valid permutation values for {metric} ({len(perm_values)})")
            continue
        
        perm_values = np.array(perm_values)
        
        # Get alternative hypothesis for this metric
        config = METRIC_CONFIG.get(metric, {'alternative': 'greater'})
        alternative = config['alternative']
        
        results[metric] = calculate_mcpt_result(
            metric_name=metric,
            real_value=real_value,
            perm_values=perm_values,
            alternative=alternative
        )
    
    return results
