"""
Find and parse zests
"""

import os
import ast
import pkgutil
import sys
from typing import List
from dataclasses import dataclass
from importlib import util
from zest.zest import log, check_allow_to_run


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
    name: str
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
                        name="zest_root_1",
                        groups=[],
                        errors=[],
                        children=[
                            FoundZest("it_foos"),
                            FoundZest("it_bars", groups=["group1"]),
                        ],
                        skip=None,
                    ),
                    FoundZest(
                        name="zest_root_2",
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
    errors = []

    for i, part in enumerate(body):
        if isinstance(part, ast.With):
            _found_zests, _errors = _recurse_ast(path, part.lineno, part.body, func_name, parent_name)
            found_zests += _found_zests
            errors += _errors

        if isinstance(part, ast.FunctionDef):
            this_zest_groups = []
            this_zest_skip_reason = None

            if (is_module_level and part.name.startswith("zest_")) or (
                not is_module_level and not part.name.startswith("_")
            ):
                this_zest_name = part.name

                if part.decorator_list:
                    for dec in part.decorator_list:
                        if isinstance(dec, ast.Call):
                            if isinstance(dec.func, ast.Attribute):
                                if dec.func.attr == "group":
                                    group = dec.args[0].s
                                    this_zest_groups += [group]
                                elif dec.func.attr == "skip":
                                    if len(dec.args) == 1:
                                        reason = dec.args[0].s
                                    else:
                                        reason = dec.keywords[0].value.s
                                    this_zest_skip_reason = reason

                # RECURSE
                n_test_funcs += 1
                this_zest_children, this_zest_errors = _recurse_ast(
                    path, part.lineno, part.body, this_zest_name, parent_name
                )

                found_zests += [
                    FoundZest(
                        name=this_zest_name,
                        groups=this_zest_groups,
                        errors=this_zest_errors,
                        children=this_zest_children,
                        skip=this_zest_skip_reason,
                    )
                ]

                if found_zest_call:
                    # A call to zest() has already been seen previously in this context
                    # therefore it is an error to define another function after this point
                    # so we set the following flag
                    found_zest_call_before_final_func_def = True

        # Check for the call to "zest()"
        if (
            isinstance(part, ast.Expr)
            and isinstance(part.value, ast.Call)
            and isinstance(part.value.func, ast.Name)
            and part.value.func.id == "zest"
        ):
            found_zest_call = True

    if n_test_funcs > 0 and not is_module_level:
        if found_zest_call_before_final_func_def:
            error_message = "called zest() before all functions were defined."
            errors += [(func_name, path, lineno, error_message)]
        elif not found_zest_call:
            error_message = "did not terminate with a call to zest()"
            errors += [(func_name, path, lineno, error_message)]

    return found_zests, errors


def load_module(root_name, module_name, full_path):
    # TODO: Add cache here?
    spec = util.spec_from_file_location(module_name, full_path)
    mod = util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return getattr(mod, root_name)


def _flatten_found_zests(
    found_zests_tree: List[FoundZest], parent_name, parent_groups
) -> List[FoundZest]:
    """
    Convert a tree of found_zests_tree into a flat list converting
    the names to full names using a dot delimiter.
    """
    ret_list = []
    for found_zest in found_zests_tree or []:
        found_zest.name = (
            parent_name + "." if parent_name is not None else ""
        ) + found_zest.name
        _parent_groups = set(parent_groups) | set(found_zest.groups)
        found_zest.groups = list(_parent_groups)
        children = _flatten_found_zests(
            found_zest.children, found_zest.name, _parent_groups
        )
        found_zest.children = None
        ret_list += [found_zest]
        ret_list += children

    return ret_list


def find_zests(
    root,
    include_dirs,
    allow_to_run=None,
    allow_files=None,
    match_string=None,
    exclude_string=None,
    bypass_skip=None,
    groups=None,
    exclude_groups=None,
):
    """
    Traverses the tree looking for /found_zests/ folders and opens and parses any file found

    Arguments:
        root:
            Root path
        include_dirs: String
            Colon-delimited folders to search
        allow_to_run:
            If not None: a list of full test names (dot-delimited) that will be included.
            Plus two specials: "__all__" and "__failed__"
            If the name ends in "." then all children run too
        allow_files:
            If not None: a list of filenames (without directory) that will be included.
        match_string:
            If not None then any zest full name that *contains* this string will be included.
            Note that match_string only narrows the scope from allow_to_run
        exclude_string:
            If not None then any zest full name that *contains* this string will be excluded.
        only_groups:
            Run only this (colon delimited set of groups)
        exclude_groups:
            Do not run these (colon deliminted) set of groups

    Returns:
        dict of root found_zests by name -> (module_name, package, path)
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

    if root is None:
        return {}, {}, []

    n_root_parts = len(root.split(os.sep))

    if groups is not None:
        groups = set(groups)

    if exclude_groups is not None:
        exclude_groups = set(exclude_groups)

    return_allow_to_run = set()  # Full names (dot delimited) of all tests to run

    # root_zest_funcs is a dict of entrypoints (root found_zests) -> (module_name, package, path)
    root_zest_funcs = {}
    errors_to_show = []

    match_string_parts = match_string.split(".") if match_string is not None else []

    for curr in _walk_include_dirs(root, include_dirs):
        for _, module_name, _ in pkgutil.iter_modules(path=[curr]):
            if allow_files is not None:
                if module_name not in allow_files:
                    continue

            path = os.path.join(curr, module_name + ".py")

            # HACK!
            # global debug_hack
            # if path == "/erisyon/plaster/plaster/run/sigproc_v2/zests/zest_sigproc_v2_worker.py":
            #     import pudb; pudb.set_trace()
            #     debug_hack = True
            # else:
            #     debug_hack = False
            with open(path) as file:
                source = file.read()

            module_ast = ast.parse(source)

            found_zests, errors = _recurse_ast(path, 0, module_ast.body)
            assert len(errors) == 0
            found_zests = _flatten_found_zests(found_zests, None, set())

            for found_zest in found_zests:
                full_name = found_zest.name
                full_name_parts = full_name.split(".")
                package = ".".join(curr.split(os.sep)[n_root_parts:])

                allow = check_allow_to_run(allow_to_run, full_name_parts)
                if allow:
                    # If running all or the full_name matches or if the
                    # match_string contains an ancestor match
                    # Eg: match_string == "foo.bar" we have to match on
                    # foo and foo.bar

                    any_parent = all([
                        match_string_parts[i] == full_name_parts[i]
                        for i in range( min( len(match_string_parts), len(full_name_parts) ) )
                    ])

                    if match_string is None or match_string in full_name or any_parent:
                        # So that you can terminate a match_string like "it_foobars."
                        # we add an extra "." to the end pf full_name in this comparison
                        if exclude_string is not None and exclude_string in full_name + ".":
                            continue

                        # IGNORE skips
                        if found_zest.skip is not None:
                            # possible skip unless bypassed
                            if bypass_skip is None or bypass_skip != full_name:
                                continue

                        # IGNORE groups not in the groups list or in exclude_groups
                        if found_zest.groups is not None:
                            # If CLI groups is specified and there there is no
                            # group in common between the CLI groups and the
                            # groups of this test then skip it.
                            if groups is not None and not set.intersection(
                                set(found_zest.groups), groups
                            ):
                                continue

                            # If CLI exclude_groups is specified and there there *is*
                            # a group in common between then skip it.
                            if exclude_groups is not None and set.intersection(
                                set(found_zest.groups), exclude_groups
                            ):
                                continue

                        # FIND any errors from this zest:
                        for error in found_zest.errors:
                            errors_to_show += [error]

                        # Include this and all ancestors in the list
                        for i in range(len(full_name_parts)):
                            name = ".".join(full_name_parts[0 : i + 1])
                            return_allow_to_run.update({name})

                        root_zest_funcs[full_name_parts[0]] = (module_name, package, path)

    return root_zest_funcs, return_allow_to_run, errors_to_show


if __name__ == "__main__":
    zests = find_zests(
        ".", "./zests", allow_files="zest_basics.py", allow_to_run="__all__"
    )
    for z in zests:
        print(z)
