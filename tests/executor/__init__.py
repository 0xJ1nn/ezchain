"""Executor tests.

This is a package so pytest imports these modules by their dotted path. Without it,
`tests/executor/test_reconciliation.py` and `tests/api/test_reconciliation.py` both land in
`sys.path` as the top-level module `test_reconciliation`, and collection fails with an
import file mismatch on any run that reaches both.
"""
