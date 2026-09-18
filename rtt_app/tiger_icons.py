"""Small, fixed SVG coach symbols; independent of service data or credentials."""


def _svg(body, label):
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="48" height="24" '
        'viewBox="0 0 64 32" role="img" aria-label="' + label + '">'
        '<title>' + label + '</title>'
        '<g stroke="#182e42" stroke-width="1.5" stroke-linejoin="round">'
        + body + '</g></svg>'
    )


_WHEELS = '<circle cx="17" cy="26" r="3" fill="#182e42"/><circle cx="47" cy="26" r="3" fill="#182e42"/>'
_FRONT = (
    '<path d="M5 24V20L15 7H58V24Z" fill="#d9edf7"/>'
    '<path d="M10 17L16 10H21V17Z" fill="#182e42"/>'
    '<path d="M27 11H35V17H27ZM40 11H48V17H40Z" fill="#287a9c"/>'
    '<path d="M6 21H58" stroke="#287a9c"/>' + _WHEELS
)
_MIDDLE = (
    '<rect x="5" y="7" width="54" height="17" rx="2" fill="#d9edf7"/>'
    '<path d="M12 11H20V17H12ZM28 11H36V17H28ZM44 11H52V17H44Z" fill="#287a9c"/>'
    '<path d="M5 21H59" stroke="#287a9c"/>' + _WHEELS
)
_SINGLE = (
    '<path d="M5 24V20L15 7H49L59 20V24Z" fill="#d9edf7"/>'
    '<path d="M10 17L16 10H21V17ZM43 10H48L54 17H43Z" fill="#182e42"/>'
    '<path d="M28 11H36V17H28Z" fill="#287a9c"/>' + _WHEELS
)
ICONS = {
    'front': _svg(_FRONT, 'Front carriage, nose facing left'),
    'intermediate': _svg(_MIDDLE, 'Intermediate carriage'),
    'rear': _svg('<g transform="translate(64 0) scale(-1 1)">' + _FRONT + '</g>', 'Rear carriage, nose facing right'),
    'unknown': _svg(_MIDDLE, 'Carriage, position unknown'),
    'frontAndRear': _svg(_SINGLE, 'Single carriage, both ends'),
}


def add_coach_icons(evidence, base_url):
    """Attach public image URLs to normalized evidence, keeping raw data intact."""
    return {**evidence, 'coaches': [
        {**coach, 'iconUrl': f"{base_url.rstrip('/')}/icons/coach-{coach.get('position', 'unknown')}.svg"}
        for coach in evidence.get('coaches', [])
    ]}
