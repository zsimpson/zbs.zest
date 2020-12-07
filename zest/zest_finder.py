"""
Find and parse zests
"""

import os
import ast
import pkgutil
import sys
import typing
from dataclasses import dataclass
from importlib import import_module, util
from zest.zest import log


def _walk_include_dirs(root, include_dirs):
    """
    Generator to walk from root though all included_dirs
    finding any folder that is called "/zests/"

    Arguments:
        root: String
            Root folder
        include_dirs: String
            Colon-delimited list of paths to search relative to root
    """
    for folder in (include_dirs or "").split(":"):
        for curr, dirs, _ in os.walk(os.path.abspath(os.path.join(root, folder))):
            # os.walk allows modifying the dirs. In this case, skip hidden
            dirs[:] = [d for d in dirs if d[0] != "."]
            if curr.endswith("/zests"):
                yield curr


@dataclass
class FoundZest:
    full_name: str
    groups: []
    errors: []
    children: []
    skip: str = None


def _recurse_ast(path, lineno, body, func_name=None, parent_name=None):
    """
    TODO

    body:
        This body of the module or function that is being analyzed
    parent_name:
        If not a module, this will contain the name of the parent
        For example:
        some_module:
            def zest_root_1():
                def it_foos():
                    pass

                @zest.group("group1")
                def it_bars():
                    pass

                zest()

            def zest_root_2():
                def it_goos():
                    pass

                @zest.skip("Ignore me")
                def it_zoos():
                    pass

                zest()

                def it_bad_zest_declared_after_the_call_to_zest():
                    pass


            _recurse_ast("./path/some_module.py", 0, body_of_some_module, None, None)
                Which will return:
                [
                    FoundZest(
                        full_name="zest_root_1",
                        groups=[],
                        errors=[],
                        children=[
                            FoundZest("it_foos"),
                            FoundZest("it_bars", groups=["group1"]),
                        ],
                        skip=None,
                    ),
                    FoundZest(
                        full_name="zest_root_2",
                        groups=[],
                        errors=["it_bad_zest_declared_after_the_call_to_zest was declared...."],
                        children=[
                            FoundZest("it_goos"),
                            FoundZest("it_zoos", skip="Ignore me"),
                        ],
                        skip=None,
                    ),
                ]


    The tricky thing here is that _recurse_ast is called in two contexts:
        1. At a module level where parent_name and func_name are None
        2. On a function where func_name is not None and parent_name
            might be None if this is a root-level test.

        In other words:
            If func_name is None then "body" is a MODULE.
            If func_name is not None and parent_name is None then "body" is a root-level FUNCTION
            If func_name is not None and parent_name is not None then "body" is a child FUNCTION

        Errors apply only to FUNCTIONS not to modules.
    """

    is_module_level = func_name is None
    is_root_zest = func_name is not None and parent_name is None

    # Will be incremented for each def that is found in this context
    # that does not start with an underscore.
    n_test_funcs = 0

    # Flag that will be set if any zest() call is found (there should be only one at the end!)
    found_zest_call = False

    # Flag that will be set if any test function is declared AFTER the call to zest()
    found_zest_call_before_final_func_def = False

    # The zests found in this context
    found_zests = []

    for i, part in enumerate(body):
        if isinstance(part, ast.With):
            _children_list_of_zests = _recurse_ast(
                part.body, parent_name, path, part.lineno,
            )
            found_zests += _children_list_of_zests

        if isinstance(part, ast.FunctionDef):
            this_zest_name = None
            this_zest_groups = []
            this_zest_errors = []
            this_zest_skip_reason = None
            this_zest_children = None

            if (is_module_level and part.name.startswith("zest_")) or (
                not is_module_level and not part.name.startswith("_")
            ):
                this_zest_name = part.name

                if part.decorator_list:
                    for dec in part.decorator_list:
                        if isinstance(dec, ast.Call):
                            if isinstance(dec.func, ast.Attribute):
                                if dec.func.attr == "group":
                                    this_zest_groups += ["?"]
                                elif dec.func.attr == "skip":
                                    this_zest_skip_reason = "?"

                # RECURSE un-skipped functions
                if this_zest_skip_reason is None:
                    n_test_funcs += 1
                    this_zest_children = _recurse_ast(
                        path, part.lineno, part.body, this_zest_name, parent_name
                    )
HERE
            if found_zest_call:
                # A call to zest() has already been seen previously in this context
                # therefore it is an error to define another function after this point
                # so we set the following flag
                found_zest_call_before_final_func_def = True

            found_zests += [
                FoundZest(
                    this_zest_full_name,
                    this_zest_groups,
                    this_zest_errors,
                    this_zest_skip_reason,
                    this_zest_children,
                )
            ]

        # Check for the call to "zest()"
        if (
            isinstance(part, ast.Expr)
            and isinstance(part.value, ast.Call)
            and isinstance(part.value.func, ast.Name)
            and part.value.func.id == "zest"
        ):
            found_zest_call = True

    if func_name is not None:
        this_func_skipped = False
        if skips and len(skips) > 0:
            this_func_skipped = True

            # There is a skip request on this func, but is there a bypass for it?
            if bypass_skip and bypass_skip in skips:
                this_func_skipped = False

        if n_test_funcs > 0 and parent_name is not None and not this_func_skipped:
            if found_zest_call_before_final_func_def:
                error_message = "called zest() before all functions were defined."
                errors += [(parent_name, path, lineno, error_message)]
            elif not found_zest_call:
                error_message = "did not terminate with a call to zest()"
                errors += [(parent_name, path, lineno, error_message)]

    return found_zests


def _recurse_ast_old(body, parent_name, skips, path, lineno, bypass_skip, func_name):
    """
    Recursively traverse the Abstract Syntax Tree extracting zests.

    Args:
        body: the AST func_body or module body
        parent_name: The parent function name or None if a module
        skips: a list of skip names for the current func_body or None if module
            This is used so that we do not accumulate errors on skipped zests

    Returns:
        flat list of all zests (and sub zests): [(full_name, errors, props)]
    """
    n_test_funcs = 0
    found_zest_call = False
    found_zest_call_before_final_func_def = False

    # list_of_zests is a FLAT list of zests, the recursive children are appended to this
    list_of_zests = []

    for i, part in enumerate(body):
        if isinstance(part, ast.With):
            _children_list_of_zests = _recurse_ast(
                part.body,
                parent_name,
                skips,
                path,
                part.lineno,
                bypass_skip,
                func_name=None,
            )
            list_of_zests += _children_list_of_zests

        if isinstance(part, ast.FunctionDef):
            props = {}
            full_name = None
            if parent_name is None:
                if part.name.startswith("zest_"):
                    full_name = f"{part.name}"
            else:
                if not part.name.startswith("_"):
                    full_name = f"{parent_name}.{part.name}"

            # Accumulate _skips so that we can ignore errors in skipped tests
            _skips = []

            def add_to_skips(full_name):
                nonlocal _skips
                if bypass_skip is None or bypass_skip == "" or full_name in bypass_skip:
                    _skips += [full_name]

            if part.decorator_list:
                for dec in part.decorator_list:
                    if isinstance(dec, ast.Call):
                        if isinstance(dec.func, ast.Attribute):
                            if dec.func.attr == "group":
                                props["group"] = "?"
                            elif dec.func.attr == "skip":
                                add_to_skips(full_name)
                                # if len(dec.args) > 0 and isinstance(
                                #     dec.args[0], ast.Str
                                # ):
                                # elif len(dec.keywords) > 0 and isinstance(
                                #     dec.keywords[0].value, ast.Str
                                # ):
                                #     add_to_skips(full_name, dec.keywords[0].value.s)

            if full_name is not None:
                n_test_funcs += 1
                _children_list_of_zests = _recurse_ast(
                    part.body,
                    full_name,
                    _skips,
                    path,
                    part.lineno,
                    bypass_skip,
                    func_name=full_name,
                )
                list_of_zests += [(full_name, [], _props)]
                list_of_zests += _children_list_of_zests
                errors += _errors

            if found_zest_call:
                found_zest_call_before_final_func_def = True

        if isinstance(part, ast.Expr):
            if isinstance(part.value, ast.Call):
                if isinstance(part.value.func, ast.Name):
                    if part.value.func.id == "zest":
                        found_zest_call = True

    # Accumulate error message if this function did not end with a zest() call
    # unless this function is skipped
    if func_name is not None:
        this_func_skipped = False
        if skips and len(skips) > 0:
            this_func_skipped = True

            # There is a skip request on this func, but is there a bypass for it?
            if bypass_skip and bypass_skip in skips:
                this_func_skipped = False

        if n_test_funcs > 0 and parent_name is not None and not this_func_skipped:
            if found_zest_call_before_final_func_def:
                error_message = "called zest() before all functions were defined."
                errors += [(parent_name, path, lineno, error_message)]
            elif not found_zest_call:
                error_message = "did not terminate with a call to zest()"
                errors += [(parent_name, path, lineno, error_message)]

    return list_of_zests, errors


def load_module(root_name, module_name, full_path):
    # TODO: Add cache here?
    spec = util.spec_from_file_location(module_name, full_path)
    mod = util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return getattr(mod, root_name)


def find_zests(
    root,
    include_dirs,
    allow_to_run=None,
    allow_files=None,
    match_string=None,
    exclude_string=None,
    bypass_skip=None,
    only_groups=None,
    exclude_groups=None,
):
    """
    Traverses the tree looking for /zests/ folders and opens and parses any file found

    Arguments:
        root:
            Root path
        include_dirs: String
            Colon-delimited folders to search
        allow_to_run:
            If not None: a list of full test names (dot-delimited) that will be included.
            Plus two specials: "__all__" and "__failed__"
        allow_files:
            If not None: a list of filenames (without directory) that will be included.
        match_string:
            If not None then any zest full name that *contains* this string will be included.
            Note that match_string only narrows the scope from allow_to_run
        exclude_string:
            If not None then any zest full name that *contains* this string will be excluded.
        bypass_skip:
            Used for debugging/testing
        only_groups:
            Run only this (colon delimited set of groups)
        exclude_groups:
            Do not run these (colon deliminted) set of groups

    Returns:
        dict of root zests by name -> (module_name, package, path)
        set of full names allowed to run (not all test under a root have to be allowed)
        list of all errors

    Note, when a zest is identified all of its ancestor are also be added to the the list.
    Example:
        full_name = "zest_test1.it_does_y.it_does_y1"
        match_string = "it_does_y1"
        Then return_allow_to_run == set(
            "zest_test1",
            "zest_test1.it_does_y",
            "zest_test1.it_does_y.it_does_y1",
        )
    """

    if allow_to_run is None:
        allow_to_run = []

    if root is None:
        log(f"root none {include_dirs} {allow_to_run} {match_string} {allow_files}")
        return {}, {}, []

    n_root_parts = len(root.split(os.sep))

    return_allow_to_run = set()  # Full names (dot delimited) of all tests to run

    # root_zest_funcs is a dict of entrypoints (root zests) -> (module_name, package, path)
    root_zest_funcs = {}
    errors_to_show = []

    for curr in _walk_include_dirs(root, include_dirs):
        for _, module_name, _ in pkgutil.iter_modules(path=[curr]):
            if allow_files is not None:
                if module_name not in allow_files:
                    continue

            path = os.path.join(curr, module_name + ".py")
            with open(path) as file:
                source = file.read()

            module_ast = ast.parse(source)
            zests, errors = _recurse_ast(
                module_ast.body, None, None, path, 0, bypass_skip, func_name=None
            )

            for full_name, skips in zests:
                parts = full_name.split(".")
                package = ".".join(curr.split(os.sep)[n_root_parts:])

                if "__all__" in allow_to_run or full_name in allow_to_run:
                    if match_string is None or match_string in full_name:
                        if exclude_string is not None and exclude_string in full_name:
                            continue

                        # FIND any errors from this zest:
                        for error in errors:
                            if error[0] == full_name:
                                errors_to_show += [error]

                        # Include this and all ancestors in the list
                        for i in range(len(parts)):
                            name = ".".join(parts[0 : i + 1])
                            return_allow_to_run.update({name})

                        root_zest_funcs[parts[0]] = (module_name, package, path)

    return root_zest_funcs, return_allow_to_run, errors_to_show
