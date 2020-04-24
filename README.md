# Still a Work In Progress

# zest

A function-oriented testing framework for Python 3.

# Development

When installed as a package, "zest" is created as an entrypoint
in setup.py.  But in development mode, an alias is created
in `.pipenvshrc`. Add this following to your ~/.bashrc (yes, even in OSX)
so that `pipenv shell` will be able to pick it up.

```bash
if [[ -f .pipenvshrc ]]; then
  . .pipenvshrc
fi
```

# deploy
```bash
$ ./deploy.sh
```
