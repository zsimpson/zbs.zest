# Zest

A function-oriented testing framework for Python 3.

# Motivation

Python's default unittest module is a class-oriented approach that
does not lend itself will to recursive setup and teardown.

Zest uses a recursive function-based approach best demonstrated
with examples.

```python
# some_module.py

def _say_hello():
    print("Hello")

def unit_under_test(a):
    if a <= 0:
        raise ValueError("a should be positive")
    
    _say_hello()

    return a + 1

##########################################

# zest_some_module.py

from zest import zest
from . import some_module

def zest_unit_under_test():

    def it_raises_on_non_positive():
        def it_raises_on_negative():
            with zest.raises(ValueError):
                some_module.unit_under_test(-1)
    
        def it_raises_on_zero():
            with zest.raises(ValueError):
                some_module.unit_under_test(0)

        zest()  # Note this is a recursive entrypoint, also required to run the sub-tests

    def it_calls_say_hello_once():
        with zest.mock(some_module._say_hello) as m_say_hello:
            some_module.unit_under_test(0)
            assert m_say_hello.called_once()

    zest()  # Note, this is special entrypoint is required
```

The zest() function uses stack reflection to call each function that
it finds in the caller's stack-frame.  However, it only calls functions
that do not start with an underscore.

Two special functions are reserved: _before() and _after()
which are called before/after _each_ test function in the scope.

For example, often you may want to set up some complex state.

```python
def zest_my_test():
    state = None

    def _before():
        nonlocal state
        state = State(1, 2, 3)

    def it_raises_if_bad():
        with zest.raises(Exception):
            unit_under_test(state)

    def it_modifies_state_on_1():
        unit_under_test(state, 1)
        assert state.foo == 1

    def it_modifies_state_on_2():
        unit_under_test(state, 2)
        assert state.foo == 2
```

# Usage

Search recursively all directories for def zest_*() functions and execute them.
```bash
$ zest
```

```bash
$ zest --verbose=0  # Show no progress
$ zest --verbose=1  # Show "dot" progress
$ zest --verbose=2  # Show hierarchical full progress
```

Search only inside the specific dirs
```bash
$ zest --include_dirs=
```


Notes:
    Unless zest._disable_shuffle is True it will randomized run order
    to prevent accidental order dependencies.



# Gotchas

Don't do mock outside of test functions:
```python
def zest_something():
    with zest.mock(...):
        def it_does_something():
            assert something

        def it_does_something_else():
            assert something

    # The zest() will execute outside of the above "with" statement so
    # the two tests will not inherit the mock as expected.
    zest()
```

Rather, put the zest() inside the "with mock":
```python
def zest_something():
    with zest.mock(...):
        def it_does_something():
            assert something

        def it_does_something_else():
            assert something

        # This is fine because zest() was called INSIDE the with
        zest()
```

Don't have more than one zest() call in the same scope.
```python
def zest_something():
    with zest.mock(...):
        def it_does_something():
            assert something

        def it_does_something_else():
            assert something

        # Like above example; so far, so good, but watch out...
        zest()

    with zest.mock(...):
        def it_does_yet_another_thing():
            assert something

        # ... This is BAD! In this case this second call to zest()
        # will RE-EXECUTE the above two tests (it_does_something and it_does_something_else)
        # because this call to zest() doesn't know that it is inside of a with statement.
        # It looks visually different but really the following zest() and the one above
        # are actually in the same scope. 
        zest()
```


# Development

## Setup

When installed as a package, "zest" is created as an entrypoint
in setup.py.  But in development mode, an alias is created
in `.pipenvshrc`. Add this following to your ~/.bashrc (yes, even in OSX)
so that `pipenv shell` will be able to pick it up.

```bash
if [[ -f .pipenvshrc ]]; then
  . .pipenvshrc
fi
```

## Test

To run all the example tests (which actually test the tester itself).
```bash
$ zest
```  

## Deploy
```bash
$ ./deploy.sh
```
You will need the user and password and credentials for Pypi.org


# TODO
 * Add --version option