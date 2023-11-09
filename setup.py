#!/usr/bin/env python3

from setuptools import setup  # type: ignore
import ma_plan_validator


long_description = """
 ============================================================
    MA-PLAN-VALIDATOR
 ============================================================

    ma_plan_validator is a validator for multi-agent problems.
"""

setup(
    name="ma_plan_validator",
    version=ma_plan_validator.__version__,
    description="ma_plan_validator",
    author="Alessandro Trapasso",
    author_email="ale.trapasso8@gmail.com",
    url="",
    packages=["ma_plan_validator"],
    install_requires=[""],
    python_requires="",
    license="APACHE",
)
