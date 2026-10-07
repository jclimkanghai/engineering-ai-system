"""V2 execution clients; controlled memory belongs to Engineering Registry."""

from .fem import (
    FEMRunError,
    run_linear_static_job,
    run_linear_static_job_isolated,
    validate_fem_job,
)
from .service import ExecutionService
from .solvers import SolverCatalog

__all__ = [
    "ExecutionService",
    "FEMRunError",
    "SolverCatalog",
    "run_linear_static_job",
    "run_linear_static_job_isolated",
    "validate_fem_job",
]
