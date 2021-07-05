#!/usr/bin/env bash

VERSION=$(cat ./zest/version.py | sed 's/__version__[ ]*=[ ]*\"//')
VERSION=$(echo $VERSION | sed 's/\"//g')
NEW_VERSION=$(echo $VERSION | awk -F. '{$NF = $NF + 1;} 1' | sed 's/ /./g')

echo "Bumping from version $VERSION to $NEW_VERSION.  ENTER to continue or ^c."
read -p "$*"

echo "__version__ = \"${NEW_VERSION}\"" > ./zest/version.py

rm -rf dist/

pipenv run python setup.py sdist \
	&& rm -rf zbs.zest.egg-info \
	&& pipenv run twine check dist/* \
	&& pipenv run twine upload dist/*

echo "go check https://pypi.org/project/zbs.zest for shas"

