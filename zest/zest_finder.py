"""
Find and parse zests
"""

import os
import ast
import pkgutil
import sys
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


def _recurse_ast(body, parent_name, skips, path, lineno, bypass_skip, func_name):
    """
    Recursively traverse the Abstract Syntax Tree extracting zests

    Args:
        body: the AST func_body or module body
        parent_name: The parent function name or None if a module
        skips: a list of skip names for the current func_body or None if module

    Returns:
        list of children: [(full_name, skips, errors)]
    """
    n_test_funcs = 0
    found_zest_call = False
    found_zest_call_before_final_func_def = False
    child_list = []
    errors = []
    for i, part in enumerate(body):
        if isinstance(part, ast.With):
            _child_list, _errors = _recurse_ast(
                part.body,
                parent_name,
                skips,
                path,
                part.lineno,
                bypass_skip,
                func_name=None,
            )
            child_list += _child_list
            errors += _errors

        if isinstance(part, ast.FunctionDef):
            full_name = None
            if parent_name is None:
                if part.name.startswith("zest_"):
                    full_name = f"{part.name}"
            else:
                if not part.name.startswith("_"):
                    full_name = f"{parent_name}.{part.name}"

            _skips = []

            def add_to_skips(reason):
                nonlocal _skips
                if bypass_skip is None or bypass_skip == "" or reason in bypass_skip:
                    _skips += [reason]

            if part.decorator_list:
                for dec in part.decorator_list:
                    if isinstance(dec, ast.Call):
                        if isinstance(dec.func, ast.Attribute):
                            if dec.func.attr == "skip":
                                if len(dec.args) > 0 and isinstance(
                                    dec.args[0], ast.Str
                                ):
                                    add_to_skips(dec.args[0].s)
                                elif len(dec.keywords) > 0 and isinstance(
                                    dec.keywords[0].value, ast.Str
                                ):
                                    add_to_skips(dec.keywords[0].value.s)

            if full_name is not None:
                n_test_funcs += 1
                _child_list, _errors = _recurse_ast(
                    part.body,
                    full_name,
                    _skips,
                    path,
                    part.lineno,
                    bypass_skip,
                    func_name=full_name,
                )
                child_list += [(full_name, _skips)]
                child_list += _child_list
                errors += _errors

            if found_zest_call:
                found_zest_call_before_final_func_def = True

        if isinstance(part, ast.Expr):
            if isinstance(part.value, ast.Call):
                if isinstance(part.value.func, ast.Name):
                    if part.value.func.id == "zest":
                        found_zest_call = True

    # Show error message if this function did not end with a zest() call
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

    return child_list, errors


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
    match_string=None,
    exclude_string=None,
    bypass_skip=None,
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
        match_string:
            If not None then any zest full name that *contains* this string will be included.
            Note that match_string only narrows the scope from allow_to_run
        exclude_string:
            If not None then any zest full name that *contains* this string will be excluded.

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

    n_root_parts = len(root.split(os.sep))

    return_allow_to_run = set()  # Full names (dot delimited) of all tests to run
    root_zest_funcs = (
        {}
    )  # A dict of entrypoints (root zests) -> (module_name, package, path)
    errors_to_show = []

    for curr in _walk_include_dirs(root, include_dirs):
        for _, module_name, _ in pkgutil.iter_modules(path=[curr]):
            path = os.path.join(curr, module_name + ".py")
            log(f"search path {path}")
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
