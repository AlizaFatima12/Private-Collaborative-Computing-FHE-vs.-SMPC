"""Automated Statistical Analysis, Crossover Threshold Detection, and Academic Graph Generation."""

from analysis.preprocessing import BenchmarkDataPreprocessor
from analysis.statistics import StatisticalComparator
from analysis.crossover_analysis import CrossoverAnalyzer
from analysis.graphs import AcademicGraphGenerator

__all__ = [
    "BenchmarkDataPreprocessor",
    "StatisticalComparator",
    "CrossoverAnalyzer",
    "AcademicGraphGenerator",
]
