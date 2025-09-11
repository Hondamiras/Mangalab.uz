from django import template

register = template.Library()

_UNITS = [
    (1_000_000_000, "B"),
    (1_000_000, "M"),
    (1_000, "k"),
]

@register.filter
def short_number(value):
    """
    1234  -> '1.2k'
    12000 -> '12k'
    1250000 -> '1.3M'
    987 -> '987'
    """
    try:
        n = int(value)
    except (TypeError, ValueError):
        return value

    for threshold, suffix in _UNITS:
        if n >= threshold:
            v = n / threshold
            # katta sonlarda nuqtasiz
            if v >= 100:
                return f"{int(v)}{suffix}"
            if v >= 10:
                return f"{int(round(v))}{suffix}"
            # 1.2k kabi
            s = f"{v:.1f}"
            if s.endswith(".0"):
                s = s[:-2]
            return f"{s}{suffix}"
    return str(n)
