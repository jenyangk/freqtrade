"""
Monte Carlo Permutation Test (MCPT) Module for Freqtrade

Detect overfitting in trading strategies using bar permutation tests.

Modules:
- bar_permute: Standard and block permutation algorithms
- mcpt_stats: Statistical analysis and result classes
- run_advanced_mcpt: Comprehensive MCPT runner with multiple metrics
"""

from .bar_permute import (
    get_permutation, 
    get_block_permutation,
    get_permutation_with_resample,
    validate_permutation,
    validate_block_permutation
)
from .mcpt_stats import (
    MCPTResult,
    ComprehensiveMCPTResults,
    calculate_mcpt_result,
    run_comprehensive_mcpt,
    METRIC_CONFIG
)

__all__ = [
    'get_permutation',
    'get_block_permutation', 
    'get_permutation_with_resample',
    'validate_permutation',
    'validate_block_permutation',
    'MCPTResult',
    'ComprehensiveMCPTResults',
    'calculate_mcpt_result',
    'run_comprehensive_mcpt',
    'METRIC_CONFIG'
]
