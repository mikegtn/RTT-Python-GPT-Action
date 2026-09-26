"""Deterministic passenger-stop schematics; no geographical position is inferred."""
from io import BytesIO
from pathlib import Path
import re
import secrets
import threading
import time
from zoneinfo import ZoneInfo

from .mcp_tools import timestamp
from .service_progress import PASSENGER_TYPES, passenger_call

TTL = 7 * 24 * 3600
MAX_IMAGES = 512
WIDTH = 1120
TEAL = '#087f82'
RED = '#c3343b'
INK = '#19333e'
MUTED = '#536875'


def observed(call, event, cutoff):
    temporal = call.get('temporalData') or {}
    timing = temporal.get(event) or {}
    actual = timing.get('realtimeActual')
    return timing if (actual and temporal.get('isInterpolated') is not True
                      and timing.get('isCancelled') is not True and timestamp(actual) <= cutoff) else None


def timing_color(timing):
    if not timing or not timing.get('scheduleAdvertised'):
        return MUTED
    delay = timestamp(timing['realtimeActual']) - timestamp(timing['scheduleAdvertised'])
    return RED if delay.total_seconds() > 60 else TEAL


def render(service, progress):
    from PIL import Image, ImageDraw, ImageFont, ImageFilter

    calls = [(i, c) for i, c in enumerate(service.get('calls') or [])
             if ((c.get('temporalData') or {}).get('scheduledCallType') in PASSENGER_TYPES
                 or (c.get('temporalData') or {}).get('realtimeCallType') in PASSENGER_TYPES)]
    if not calls or len(calls) > 120:
        raise ValueError('Schematic requires between 1 and 120 passenger calls')
    cutoff = timestamp(progress['evaluatedAt'], require_offset=True)

    def font(size, bold=False):
        names = (['/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 'C:/Windows/Fonts/segoeuib.ttf']
                 if bold else ['/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 'C:/Windows/Fonts/segoeui.ttf'])
        for name in names:
            try:
                return ImageFont.truetype(name, size)
            except OSError:
                pass
        return ImageFont.load_default(size=size)

    image = Image.new('RGB', (WIDTH, 366 + len(calls) * 48 + 206), '#f5f8fa')
    draw = ImageDraw.Draw(image)

    def text(x, y, value, size=22, color=INK, bold=False, width=None):
        value = ' '.join(str(value).split())
        f = font(size, bold)
        if width:
            while size > 13 and draw.textlength(value, font=f) > width:
                size -= 1
                f = font(size, bold)
            while draw.textlength(value, font=f) > width and len(value) > 1:
                value = value[:-2].rstrip() + '…'
        draw.text((x, y), value, fill=color, font=f)

    def clock(value):
        return timestamp(value).astimezone(ZoneInfo('Europe/London')).strftime('%H:%M:%S')

    metadata = service.get('scheduleMetadata') or {}
    operator = (metadata.get('operator') or {}).get('name') or 'Rail service'
    headcode = metadata.get('trainReportingIdentity') or ''
    first, last = [calls[i][1]['location']['description'] for i in (0, -1)]
    text(42, 27, f'{operator}  {headcode}', 19, MUTED, width=1030)
    text(42, 61, f'{first} → {last}', 34, bold=True, width=1030)
    local = cutoff.astimezone(ZoneInfo('Europe/London'))
    text(42, 110, 'Evaluation: ' + local.strftime('%d %B %Y · %H:%M:%S %Z'), 20, MUTED)
    state = progress['state']
    if state == 'between_calls':
        headline = f"Between {progress['from']['name']} and {progress['to']['name']}"
    elif state == 'at_station':
        headline = f"Reported at {progress['at']['name']}"
    elif state == 'completed':
        headline = f"Completed at {progress['at']['name']}"
    else:
        headline = 'No actual movement reported by this time'
    draw.rounded_rectangle((30, 153, 1090, 291), radius=16, fill='#e2f2f1')
    text(51, 171, headline, 29, TEAL, True, 1010)
    report = progress.get('lastReport')
    current_color = TEAL
    if report:
        reported_call = service['calls'][report['callIndex']]
        current_color = timing_color(observed(reported_call, report['event'], cutoff))
    if report:
        detail = f"Last report: {report['location']['description']} · {report['event']} {clock(report['reportedAt'])}"
        text(51, 217, detail, 21, color=current_color, width=1000)
        late = progress.get('latenessMinutes')
        lateness = ('Lateness unavailable' if late is None else
                    'On time' if late == 0 else f'{abs(late):g} min ' + ('late' if late > 0 else 'early'))
        text(51, 253, lateness + ' · actual movement report', 18, MUTED)
    else:
        text(51, 219, 'Not started in the available reports. Missing reports do not prove no movement.', 20, width=1000)
    text(133, 315, 'PASSENGER STOPS · SERVICE ORDER', 16, MUTED, True)
    text(700, 315, 'BOOKED', 16, MUTED, True)
    text(870, 315, 'ACTUAL', 16, MUTED, True)
    ys = {i: 368 + row * 48 for row, (i, _) in enumerate(calls)}
    x = 84
    draw.line((x, min(ys.values()), x, max(ys.values())), fill='#b6c9cf', width=6)
    # A section is completed only when an actual report exists at its far end.
    # Prefer arrival timing; departure is a fallback when the arrival is absent.
    for (start, _), (end, call) in zip(calls, calls[1:]):
        timing = observed(call, 'arrival', cutoff) or observed(call, 'departure', cutoff)
        if timing:
            draw.line((x, ys[start], x, ys[end]), fill=timing_color(timing), width=8)
    selected = []
    marker_y = None
    if state == 'between_calls':
        selected = [progress['from']['callIndex'], progress['to']['callIndex']]
        if not all(i in ys for i in selected):
            raise ValueError('Progress endpoints are absent from the schematic')
        marker_y = (ys[selected[0]] + ys[selected[1]]) // 2
    elif state in {'at_station', 'completed'}:
        selected = [progress['at']['callIndex']]
        if selected[0] not in ys:
            raise ValueError('Reported station is absent from the schematic')
        marker_y = ys[selected[0]]
    else:
        # Hollow endpoint marker denotes no actual report, never a located train.
        draw.ellipse((x-17, min(ys.values())-17, x+17, min(ys.values())+17), outline=TEAL, width=3)

    if state in {'between_calls', 'at_station'}:
        glow = Image.new('RGBA', image.size, (0, 0, 0, 0))
        glow_draw = ImageDraw.Draw(glow)
        rgb = tuple(int(current_color[i:i+2], 16) for i in (1, 3, 5))
        if state == 'between_calls':
            glow_draw.line((x, ys[selected[0]], x, ys[selected[1]]), fill=(*rgb, 125), width=28)
        else:
            glow_draw.ellipse((x-20, marker_y-20, x+20, marker_y+20), fill=(*rgb, 125))
        image = Image.alpha_composite(image.convert('RGBA'), glow.filter(ImageFilter.GaussianBlur(8))).convert('RGB')
        draw = ImageDraw.Draw(image)
        if state == 'between_calls':
            draw.line((x, ys[selected[0]], x, ys[selected[1]]), fill=current_color, width=12)

    for index, call in calls:
        y = ys[index]
        active = index in selected
        temporal = call.get('temporalData') or {}
        cancelled = not passenger_call(call)
        if active:
            draw.rounded_rectangle((117, y-20, 1076, y+23), radius=8, fill='#e2f2f1')
        draw.ellipse((x-8, y-8, x+8, y+8), fill=current_color if active else '#ffffff', outline=current_color if active else '#829ba6', width=3)
        name = (call.get('location') or {}).get('description') or 'Unnamed stop'
        if cancelled:
            name += ' · cancelled / not calling'
        text(133, y-14, name, 22, MUTED if cancelled else INK, active, 543)
        # Never reveal future actuals or infer observations from forecasts.
        observations = []
        if temporal.get('isInterpolated') is not True:
            for kind in ('arrival', 'departure'):
                data = temporal.get(kind) or {}
                actual = data.get('realtimeActual')
                if actual and data.get('isCancelled') is not True and timestamp(actual) <= cutoff:
                    observations.append((timestamp(actual), kind, actual))
        latest = max(observations, default=None)
        event = latest[1] if latest else ('arrival' if index == (progress.get('to') or {}).get('callIndex')
                                          or not temporal.get('departure') else 'departure')
        scheduled = (temporal.get(event) or {}).get('scheduleAdvertised')
        booked = ('Arr ' if event == 'arrival' else 'Dep ') + clock(scheduled)[:5] if scheduled else '—'
        text(700, y-12, booked, 18, MUTED, width=150)
        label = ('Arr ' if latest[1] == 'arrival' else 'Dep ') + clock(latest[2]) if latest else '—'
        actual_color = timing_color(observed(call, latest[1], cutoff)) if latest else MUTED
        text(870, y-12, label, 18, actual_color, width=195)

    if marker_y is not None:
        # A small train glyph outside the line, linked to the highlighted segment.
        # Its position within that segment is purely symbolic.
        draw.line((42, marker_y, 72, marker_y), fill=current_color, width=3)
        draw.rounded_rectangle((20, marker_y-17, 48, marker_y+17), radius=6, fill=current_color)
        draw.rectangle((25, marker_y-10, 43, marker_y-1), fill='white')
        for cx in (27, 41):
            draw.ellipse((cx-2, marker_y+8, cx+2, marker_y+12), fill='white')
    footer = max(ys.values()) + 42
    draw.line((42, footer, 1076, footer), fill='#d4dfe3', width=1)
    text(42, footer+17, 'RTT actual movement reports; not GPS', 20, INK, True)
    text(42, footer+49, 'Schematic, not to scale. Train symbol identifies a station or segment, not distance travelled.', 18, MUTED, width=1032)
    text(42, footer+77, 'Missing reports do not prove current position. Historical replay uses the record retrieved today.', 18, MUTED, width=1032)
    text(42, footer+109, 'Red: over 1 min late · Teal: within 1 min / early · Glow: current report · Grey: unknown / ahead', 17, MUTED, width=1032)
    text(42, footer+139, progress['uniqueIdentity'], 16, MUTED, width=1032)
    output = BytesIO()
    image.save(output, format='PNG')
    return output.getvalue(), image.size, headline


class ProgressImages:
    def __init__(self, directory, base_url='https://rail.mikegtn.net'):
        self.directory = Path(directory)
        self.base_url = base_url.rstrip('/')
        self.lock = threading.Lock()

    def read(self, image_id):
        if not re.fullmatch(r'[0-9a-f]{32}', image_id):
            return None
        target = self.directory / (image_id + '.png')
        try:
            if time.time() - target.stat().st_mtime >= TTL:
                return None
            return target.read_bytes()
        except FileNotFoundError:
            return None

    def create(self, service, progress):
        with self.lock:
            data, size, alt = render(service, progress)
            self.directory.mkdir(parents=True, exist_ok=True)
            existing = sorted(self.directory.glob('*.png'), key=lambda p: p.stat().st_mtime)
            now = time.time()
            for index, path in enumerate(existing):
                if now - path.stat().st_mtime >= TTL or index <= len(existing) - MAX_IMAGES:
                    path.unlink(missing_ok=True)
            image_id = secrets.token_hex(16)
            target = self.directory / (image_id + '.png')
            temporary = target.with_suffix('.tmp')
            try:
                temporary.write_bytes(data)
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
        url = self.base_url + '/mcp/progress/' + image_id + '.png'
        return {'schematicId': image_id, 'imageUrl': url, 'imageMimeType': 'image/png',
                'imageWidth': size[0], 'imageHeight': size[1],
                'imageAlt': alt + '. RTT actual movement reports; not GPS. Schematic, not to scale.',
                'imageMarkdown': f'![Service progress schematic]({url})',
                'imageLinkMarkdown': f'[Open schematic]({url})',
                'imageRetention': 'Public link; retained for up to seven days, subject to a 512-image storage limit.'}
