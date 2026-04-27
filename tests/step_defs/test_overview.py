"""Bind the overview feature scenarios. All steps used here live in
``conftest.py`` (shared across features)."""

from pytest_bdd import scenarios

scenarios("../features/overview.feature")
