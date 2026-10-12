"""Currency precision and rounding rules used by the verifier.

All financial arithmetic uses :class:`decimal.Decimal`; floats are never used.
"""

from decimal import ROUND_HALF_UP, Decimal

DEFAULT_MINOR_UNITS = 2

# ISO 4217 currencies whose minor unit is not 2 decimal places (common subset).
_NON_DEFAULT_MINOR_UNITS: dict[str, int] = {
    "JPY": 0,
    "KRW": 0,
    "VND": 0,
    "CLP": 0,
    "ISK": 0,
    "UGX": 0,
    "XAF": 0,
    "XOF": 0,
    "BHD": 3,
    "IQD": 3,
    "JOD": 3,
    "KWD": 3,
    "LYD": 3,
    "OMR": 3,
    "TND": 3,
}

# Common ISO 4217 currencies confirmed to use 2 decimal places.
_KNOWN_TWO_PLACE = frozenset(
    {"AUD", "CAD", "CHF", "CNY", "DKK", "EUR", "GBP", "HKD", "INR", "MXN", "NOK",
     "NZD", "PLN", "SEK", "SGD", "USD", "ZAR", "AED", "SAR", "BRL"}
)


ROUNDING_MODE = ROUND_HALF_UP


def minor_units(currency: str | None) -> tuple[int, bool]:
    """Return ``(decimal_places, assumed)`` for a currency.

    ``assumed`` is True when the currency is unknown or missing and the
    default of 2 decimal places was used.
    """
    if currency is None:
        return DEFAULT_MINOR_UNITS, True
    if currency in _NON_DEFAULT_MINOR_UNITS:
        return _NON_DEFAULT_MINOR_UNITS[currency], False
    # Unlisted codes default to 2 places, reported as an assumption.
    return DEFAULT_MINOR_UNITS, currency not in _KNOWN_TWO_PLACE


def round_money(value: Decimal, places: int) -> Decimal:
    """Round half-up to ``places`` decimal places (e.g. 2 -> 0.01)."""
    return value.quantize(Decimal(1).scaleb(-places), rounding=ROUNDING_MODE)


def fmt_amount(value: Decimal, places: int | None = None) -> str:
    """Render an amount as a string; when ``places`` is given, pad to that scale."""
    if places is not None and -value.as_tuple().exponent < places:  # type: ignore[operator]
        value = value.quantize(Decimal(1).scaleb(-places))
    return format(value, "f")


def fmt_quantity(value: Decimal) -> str:
    """Render a quantity without float artefacts (``10.0`` -> ``"10"``)."""
    return format(value.normalize(), "f")
