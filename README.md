# Zest


A function-oriented testing framework for Python 3.

Written by Zack Booth Simpson, 2020

Available as a pip package: `pip install zbs.zest`

# Motivation

Python's default unittest module is a class-oriented approach that
does not lend itself well to recursive setup and teardown.

Zest uses a recursive function-based approach best demonstrated
with examples.

```python
##########################################
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
import some_module

def zest_unit_under_test():
    # This is a root-level zest because it starts with "zest_"

    def it_raises_on_non_positive():
        def it_raises_on_negative():
            with zest.raises(ValueError):
                some_module.unit_under_test(-1)
    
        def it_raises_on_zero():
            with zest.raises(ValueError):
                some_module.unit_under_test(0)

        zest()  # Note this call which tells zest to run the above two tests

    def it_calls_say_hello_once():
        with zest.mock(some_module._say_hello) as m_say_hello:
            some_module.unit_under_test(0)
            assert m_say_hello.called_once()

    zest()  # Same here, this will cause it_raises_on_non_positive and it_calls_say_hello_once to run
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

# Examples

See `./zests/zest_examples.py` for more examples.  

# Usage

Search recursively all directories for def zest_*() functions and execute them.
```bash
$ zest
```

Show progress
```bash
$ zest --verbose=0  # Show no progress
$ zest --verbose=1  # Show "dot" progress (default)
$ zest --verbose=2  # Show hierarchical full progress
```

Search only inside the specific dirs
```bash
$ zest --include_dirs=./abc:./def
```

Run only tests that are in the "integration" or "slow" groups
```bash
$ zest --groups=integration:slow
```

Run only tests that contain the string "foobar". This will also
run any parent test needed to execute the match.
```bash
$ zest foobar
```

Disable test order shuffling which is on by default to increase the
liklihood that accidental order-dependencies are manifest.
```bash
$ zest --disable_shuffle
```

# Helpers

## Expected exceptions

```python
def zest_foobar_should_raise_on_no_arguments():
    with zest.raises(ValueError):
        foobar()
```

Sometimes you may wish to check a property of the trapped exception
```python
def zest_foobar_should_raise_on_no_arguments():
    with zest.raises(ValueError) as e:
        foobar()
    assert e.exception.args == ("bad juju",)
```

Often you may wish to check only for a string of a property of the trapped exception
in which case you can use the in_* argument to the raises.
```python
def zest_foobar_should_raise_on_no_arguments():
    with zest.raises(ValueError, in_args="bad juju") as e:
        foobar()
```

## Mocks

```python
import unit_under_test

def zest_foobar():
    with zest.mock(unit_under_test.bar) as m_bar:
        # Suppose unit_under_test.foobar() calls bar()
        m_bar.returns(0)
        unit_under_test.foobar()
    assert m_bar.called_once_with(0)
```

See `zest.MockFunction` for a complete MockFunction API.


# Gotchas

Don't forget to put the zest() call at each level of the test.
If you forget, the zest runner will throw an error along the lines of:
"function did not terminate with a call to zest()..."

```python
def zest_something():
    def it_foos():
        foo()

    def it_bars():
        bar()

    # WRONG! zest() wasn't called here. Error will be thrown when the test is run.
```


Do not mock outside of test functions:
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

        # WRONG! A second call to zest() will RE-EXECUTE the above two tests
        # (it_does_something and it_does_something_else) because this
        # second call to zest() doesn't know that it is inside of a with statement.
        # The "with" scope makes it look different but really the following
        # call to zest() and the call to zest above are actually in the same scope. 
        zest()
```


When asserting on properties of an expected exception,
be sure to do assert outside the scope of the "with" as demonstrated:

Wrong:
```python
with zest.raises(SomeException) as e:
    something_that_raises()
    assert e.exception.property == "something"
    # The above "assert" will NOT be run because the exception thrown by 
    # something_that_raises() will be caught and never get to execute the assert!
```

Right:
```python
with zest.raises(SomeException) as e:
    something_that_raises()
assert e.exception.property == "something"
    # (Note the reference to "e.exception." as opposed to "e."
```

Remember that the exception returned from a zest.raises() is
*not* of the type you are expecting but rather of a wrapper
class called `TrappedException`. To get to the properties
of interest you need to use `e.exception.*`.

Wrong:
```python
with zest.raises(SomeException) as e:
    something_that_raises()

assert e.property == "something"
# Wrong! e is of type TrappedException therefore the above will not work as expected.
```

Right:
```python
with zest.raises(SomeException) as e:
    something_that_raises()

assert e.exception.property == "something"
# Correct, .exception reference to get original exception from the `e` TrappedException wrapper.
```

# Development

## Run in development mode

```bash
pipenv shell
pipenv sync

# Run all the example tests (which actually test the tester itself).
$ ./zest.sh
```

## Deploy
```bash
$ ./deploy.sh
```
You will need the user and password and credentials for Pypi.org


# TODO
* When debug mode is on in ui, and a test runs, you don't see the success increment
* --ui fails is broken
* When match string matches nothing it is confusing. Need "nothing was run"
* Add a "slowest last" to UI
* Add "raises" to mock and stack mock. And error if BOTh returns and raises are set
* Add --rng_seed option
* Make searches more clear -- currently hard-coded to only search "zests" directories
* Harden failed imports on zest runner AST import
* Mirror debugger-like stack introspection into a set of check-like helpers for type, arrays, etc.
* Add a zest internal test that _after is called even if the subsequent test exceptions (that is, _after is in a finally block)
* Add coverage
