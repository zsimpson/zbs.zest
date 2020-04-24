# TODO

# zest

A function-oriented testing framework for Python 3.


# deploy
```bash
$ pipenv shell
$ python setup.py sdist bdist_wheel
$ twine check dist/*
$ twine upload --repository-url https://test.pypi.org/legacy/ dist/*
#   Remove "--repository-url https://test.pypi.org/legacy/" for production
```