from django import template

register = template.Library()

@register.simple_tag(takes_context=True)
def url_replace(context, field, value):
    """
    Copy current GET parameters, replace `field` with `value`, return encoded string.
    """
    query = context['request'].GET.copy()
    query[field] = value
    # remove empty keys if you want
    if not value:
        query.pop(field, None)
    return query.urlencode()
