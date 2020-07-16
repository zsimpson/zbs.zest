#!/usr/bin/env bash

# install pipenv
pip install pipenv
# install all dependencies
pipenv sync --dev
# enter a shell with the correct python environment
exec pipenv shell

