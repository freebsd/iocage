#!/bin/sh
# Lint the current source and propagate failures to CI.
exec flake8 --max-line-length=100 --ignore=E127,E203,W503,F811,W504 \
    iocage_cli iocage_lib
