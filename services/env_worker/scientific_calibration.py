"""Exact coordinate identity, allowing only canonical unit-name aliases.

The accepted calibration is never rewritten. Convertible quantities, a different
affine or coordinate-space identity require explicit conversion. Identical opaque
unit names retain the existing exact-match behavior.
"""
from functools import lru_cache
import re

COMPONENT = 'pint'
_NAME = r'[^\W\d]\w*'
_FACTOR = _NAME + r'(?:\s*\*\*\s*[+-]?[0-8])?'
_EXPRESSION = re.compile(r'\s*' + _FACTOR + r'(?:\s*[*/]\s*' + _FACTOR + r'){0,7}\s*')


@lru_cache(maxsize=256)
def _unit_identity(value):
    # This is a deliberately bounded alias grammar, not a quantity parser. General
    # arithmetic, nested powers, fractional exponents and numeric factors never
    # reach Pint. Exact identical opaque strings remain handled by the caller.
    if (_EXPRESSION.fullmatch(value) is None
            or any(not all(c.isalpha() or c == '_' for c in name)
                   for name in re.findall(_NAME, value))):
        return None
    from pint import UnitRegistry
    from pint.errors import PintError

    # Each miss owns its default registry. No package's mutable registry or growing
    # parser cache becomes shared application state; only bounded tuples survive.
    try:
        registry = UnitRegistry()
        unit = registry.parse_units(value, as_delta=False, case_sensitive=True)
        # Public serialization preserves exact canonical names/exponents. A display
        # formatter may round exponents and must never determine scientific identity.
        return tuple(sorted(registry.Quantity(1, unit).to_tuple()[1]))
    except (PintError, ValueError, TypeError, SyntaxError, ArithmeticError):
        return None


def same_calibration(left, right):
    """Compare admitted calibration records without converting or mutating them."""
    if type(left) is not dict or type(right) is not dict:
        return False
    if {k: v for k, v in left.items() if k != 'units'} != {
            k: v for k, v in right.items() if k != 'units'}:
        return False
    a, b = left.get('units'), right.get('units')
    if (type(a) is not list or type(b) is not list or len(a) != len(b)
            or not 1 <= len(a) <= 8
            or any(type(unit) is not str or not 0 < len(unit) <= 128 for unit in (*a, *b))):
        return False
    for original, current in zip(a, b):
        if original == current:
            continue
        identity = _unit_identity(original)
        if identity is None or identity != _unit_identity(current):
            return False
    return True
