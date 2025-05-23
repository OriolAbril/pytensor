import logging
from collections.abc import ValuesView
from io import StringIO

import numpy as np

import pytensor
from pytensor.compile.mode import Mode
from pytensor.configdefaults import config
from pytensor.tensor.type import discrete_dtypes


logger = logging.getLogger("pytensor.compile.nanguardmode")


def _is_numeric_value(arr, var):
    """
    Checks a variable against non-numeric types such as types, slices,
    empty arrays, and None, that need not be checked for NaN and Inf values.

    Parameters
    ----------
    arr : the data of that correspond to any PyTensor Variable
    var : The corresponding PyTensor variable

    Returns
    -------
    is_non_numeric : bool
        `True` the value is non-numeric.

    """
    from pytensor.link.c.type import _cdata_type
    from pytensor.tensor.random.type import RandomType

    if isinstance(arr, _cdata_type):
        return False
    elif isinstance(arr, np.random.Generator):
        return False
    elif var is not None and isinstance(var.type, RandomType):
        return False
    elif isinstance(arr, slice):
        return False
    elif arr is None:
        return False
    elif arr.size == 0:
        return False
    return True


def flatten(l):
    """
    Turns a nested graph of lists/tuples/other objects into a list of objects.

    Parameters
    ----------
    l : list/tuple/other objects
        Might be nested.

    Returns
    -------
    object
        A flattened list of objects.

    """
    if isinstance(l, list | tuple | ValuesView):
        rval = []
        for elem in l:
            if isinstance(elem, list | tuple):
                rval.extend(flatten(elem))
            else:
                rval.append(elem)
    else:
        return [l]
    return rval


def contains_nan(arr, node=None, var=None):
    """
    Test whether a numpy.ndarray contains any `np.nan` values.

    Parameters
    ----------
    arr : np.ndarray or output of any PyTensor op
    node : None or an Apply instance.
        If arr is the output of an PyTensor op, the node associated to it.
    var : The PyTensor symbolic variable.

    Returns
    -------
    contains_nan : bool
        `True` if the array contains any `np.nan` values, `False` otherwise.

    Notes
    -----
    Tests for the presence of `np.nan`'s using `np.isnan(np.min(ndarray))`.
    This approach is faster and more memory efficient than the obvious
    alternative, calling `np.any(np.isnan(ndarray))`, which requires the
    construction of a boolean array with the same shape as the input array.

    """
    if not _is_numeric_value(arr, var):
        return False
    elif getattr(arr, "dtype", "") in discrete_dtypes:
        return False
    return np.isnan(np.min(arr))


def contains_inf(arr, node=None, var=None):
    """
    Test whether a numpy.ndarray contains any `np.inf` values.

    Parameters
    ----------
    arr : np.ndarray or output of any PyTensor op
    node : None or an Apply instance.
        If the output of an PyTensor op, the node associated to it.
    var : The PyTensor symbolic variable.

    Returns
    -------
    contains_inf : bool
        `True` if the array contains any `np.inf` values, `False` otherwise.

    Notes
    -----
    Tests for the presence of `np.inf`'s by determining whether the
    values returned by `np.nanmin(arr)` and `np.nanmax(arr)` are finite.
    This approach is more memory efficient than the obvious alternative,
    calling `np.any(np.isinf(ndarray))`, which requires the construction of a
    boolean array with the same shape as the input array.

    """
    if not _is_numeric_value(arr, var):
        return False
    elif getattr(arr, "dtype", "") in discrete_dtypes:
        return False
    return np.isinf(np.nanmax(arr)) or np.isinf(np.nanmin(arr))


class NanGuardMode(Mode):
    """
    A PyTensor compilation Mode that makes the compiled function automatically
    detect NaNs and Infs and detect an error if they occur.

    Parameters
    ----------
    nan_is_error : bool
        If True, raise an error anytime a NaN is encountered.
    inf_is_error : bool
        If True, raise an error anytime an Inf is encountered.  Note that some
        pylearn2 modules currently use np.inf as a default value (e.g.
        mlp.max_pool) and these will cause an error if inf_is_error is True.
    big_is_error : bool
        If True, raise an error when a value greater than 1e10 is encountered.

    Notes
    -----
        We ignore the linker parameter
    """

    # We currently loose the 3 first params frequently, when calling
    # mode.including() and variant.
    def __init__(
        self,
        nan_is_error=None,
        inf_is_error=None,
        big_is_error=None,
        optimizer="default",
        linker=None,
        db=None,
    ):
        self.provided_optimizer = optimizer
        if nan_is_error is None:
            nan_is_error = config.NanGuardMode__nan_is_error
        if inf_is_error is None:
            inf_is_error = config.NanGuardMode__inf_is_error
        if big_is_error is None:
            big_is_error = config.NanGuardMode__big_is_error

        assert nan_is_error or inf_is_error or big_is_error

        def do_check_on(value, nd, var=None):
            """
            Checks `value` for NaNs / Infs. If detected, raises an exception
            and / or prints information about `nd`, `f`, and `is_input` to
            help the user determine the cause of the invalid values.

            Parameters
            ----------
            value : numpy.ndarray
                The value to be checked.
            nd : pytensor.graph.basic.Apply
                The Apply node being executed.
            var : pytensor.graph.basic.Variable
                Not used if nd is there. Otherwise, used to print the stack
                trace for inputs of the graph.

            """
            error = False
            sio = StringIO()
            if nan_is_error:
                if contains_nan(value, nd, var):
                    print("NaN detected", file=sio)
                    error = True
            if inf_is_error:
                if contains_inf(value, nd, var):
                    print("Inf detected", file=sio)
                    error = True
            if big_is_error:
                err = False
                if not _is_numeric_value(value, var):
                    err = False
                else:
                    err = np.abs(value).max() > 1e10
                if err:
                    print("Big value detected", file=sio)
                    error = True
            if error:
                if nd:
                    print(
                        "NanGuardMode found an error in the "
                        "output of a node in this variable:",
                        file=sio,
                    )
                    print(pytensor.printing.debugprint(nd, file="str"), file=sio)
                else:
                    print(
                        "NanGuardMode found an error in an input of the graph.",
                        file=sio,
                    )
                # Add the stack trace
                if nd:
                    var = nd.outputs[0]
                print(pytensor.graph.utils.get_variable_trace_string(var), file=sio)
                msg = sio.getvalue()
                if config.NanGuardMode__action == "raise":
                    raise AssertionError(msg)
                elif config.NanGuardMode__action == "pdb":
                    print(msg)  # noqa: T201
                    import pdb

                    pdb.set_trace()
                elif config.NanGuardMode__action == "warn":
                    logger.error(msg)

        def nan_check(node, thunk, storage_map, compute_map):
            for var in node.outputs:
                if compute_map[var][0] and getattr(
                    var.tag, "nan_guard_mode_check", True
                ):
                    do_check_on(storage_map[var][0], node)

        def nan_check_input(var, value):
            if getattr(var.tag, "nan_guard_mode_check", True):
                do_check_on(value, None, var=var)

        wrap_linker = pytensor.link.vm.VMLinker(
            callback=nan_check, callback_input=nan_check_input
        )
        super().__init__(linker=wrap_linker, optimizer=self.provided_optimizer, db=db)
