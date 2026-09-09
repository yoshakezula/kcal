"""Thin wrapper around the National Weather Service (api.weather.gov, no API
key required): resolve the configured zip code to coordinates, then to an NWS
forecast grid, and fetch a 7-day forecast (daily summary + hourly breakdown),
normalized into the small shape the frontend needs.

NWS has no zip-code lookup of its own - everything hangs off a lat/lon - so
the zip is resolved separately (zippopotam.us) and the coordinates handed to
NWS. Both services are US-only, which suits a kiosk NWS couldn't forecast for
anyway.
"""

import datetime
import re
import threading
from zoneinfo import ZoneInfo

import requests

from config import get_zip_code

ZIP_URL = "https://api.zippopotam.us/us/{zip_code}"
POINTS_URL = "https://api.weather.gov/points/{latitude},{longitude}"

# api.weather.gov rejects requests that don't identify the client. It asks
# for contact details so it can get in touch about a misbehaving script;
# putting a real address here is optional but neighborly.
_HEADERS = {
    "User-Agent": "KcalKiosk/1.0 (personal calendar kiosk)",
    "Accept": "application/geo+json",
}

# Zip -> coordinates -> forecast grid barely ever changes, so this cache
# lives much longer than the forecast itself.
_LOCATION_TTL = datetime.timedelta(hours=24)
_location_cache = None
_location_cache_at = None
_location_cache_zip = None

# One pair of fetches populates both the daily summary and the hourly
# breakdown, so they share a single cache.
_FORECAST_TTL = datetime.timedelta(minutes=30)
_forecast_cache = None
_hourly_cache = None  # {date: [{time, temp, humidity, precipProbability, precipAmount, cloudCover}, ...]}
_forecast_cache_at = None
_forecast_cache_zip = None

# The server is threaded and startup primes the cache in the background, so
# a refill can have several callers at once. They queue here instead of each
# firing its own round of upstream requests.
_refill_lock = threading.Lock()

# The kiosk shows a week; NWS publishes 7 days as 14 day/night periods.
_FORECAST_DAYS = 7

# NWS condition tokens -> a representative emoji. The token is the path
# segment after day/night in a period's icon URL, e.g.
# ".../icons/land/day/rain_showers,20". A "wind_" prefix is stripped before
# lookup, so wind_bkn reads as bkn.
_WEATHER_ICONS = {
    "skc": "☀️",
    "few": "\U0001F324️",
    "sct": "⛅",
    "bkn": "\U0001F325️",
    "ovc": "☁️",
    "fog": "\U0001F32B️", "haze": "\U0001F32B️", "smoke": "\U0001F32B️", "dust": "\U0001F32B️",
    "rain": "\U0001F327️",
    "rain_showers": "\U0001F326️", "rain_showers_hi": "\U0001F326️",
    "tsra": "⛈️", "tsra_sct": "⛈️", "tsra_hi": "⛈️",
    "snow": "\U0001F328️", "blizzard": "\U0001F328️",
    "rain_snow": "\U0001F328️", "rain_sleet": "\U0001F328️", "snow_sleet": "\U0001F328️",
    "sleet": "\U0001F328️", "fzra": "\U0001F328️", "rain_fzra": "\U0001F328️",
    "snow_fzra": "\U0001F328️",
    "hurricane": "\U0001F300", "tropical_storm": "\U0001F300",
    "hot": "\U0001F321️",
    "cold": "\U0001F976",
}
_DEFAULT_ICON = "\U0001F321️"

# The subset of ISO-8601 durations NWS actually uses in grid data: whole
# days and hours (PT1H, PT6H, P5D, P1DT6H).
_DURATION_RE = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?)?$")


def _to_fahrenheit(celsius):
    return celsius * 9 / 5 + 32


def _to_inches(millimeters):
    return millimeters / 25.4


def _locate_zip(zip_code):
    """Resolve a zip code to {label, timezone, forecast_url, grid_url}, or
    None if it isn't a US zip or NWS doesn't cover it."""
    global _location_cache, _location_cache_at, _location_cache_zip

    now = datetime.datetime.now()
    is_stale = _location_cache_at is None or (now - _location_cache_at) > _LOCATION_TTL
    if _location_cache is not None and not is_stale and _location_cache_zip == zip_code:
        return _location_cache

    zip_response = requests.get(ZIP_URL.format(zip_code=zip_code), timeout=10)
    if zip_response.status_code == 404:
        return None
    zip_response.raise_for_status()
    places = zip_response.json().get("places") or []
    if not places:
        return None

    place = places[0]
    label = ", ".join(p for p in (place.get("place name"), place.get("state")) if p)

    # NWS keys everything off a forecast office + grid square; /points is the
    # only way to find which one a coordinate falls in.
    points_response = requests.get(
        POINTS_URL.format(latitude=place["latitude"], longitude=place["longitude"]),
        headers=_HEADERS,
        timeout=10,
    )
    if points_response.status_code == 404:
        return None  # outside NWS coverage
    points_response.raise_for_status()
    properties = points_response.json()["properties"]

    resolved = {
        "label": label,
        # Grid data is timestamped in UTC, but the kiosk groups it by local
        # day, so we need the location's own zone rather than the Pi's.
        "timezone": ZoneInfo(properties["timeZone"]),
        "forecast_url": properties["forecast"],
        "grid_url": properties["forecastGridData"],
    }
    _location_cache = resolved
    _location_cache_at = now
    _location_cache_zip = zip_code
    return resolved


def resolve_zip(zip_code):
    """Return the resolved place label for a zip code, or None if it doesn't
    match anywhere NWS forecasts. Used to validate/display what a zip
    resolves to."""
    location = _locate_zip(zip_code)
    return location["label"] if location else None


def _duration_hours(text):
    """Whole hours covered by an ISO-8601 duration, rounded up and never
    below one, so every interval lands on at least one hour slot."""
    match = _DURATION_RE.match(text)
    if not match:
        return 1
    days, hours, minutes = (int(group) if group else 0 for group in match.groups())
    return max(days * 24 + hours + (1 if minutes else 0), 1)


def _expand_series(series, timezone, convert=None, spread=False):
    """Flatten one NWS grid series into {local datetime: value for that hour}.

    Grid values cover an interval rather than an instant - often PT6H, and
    for precipitation probability sometimes several days - so a value is
    repeated across every hour it covers. `spread` divides it instead, for
    accumulations like precipitation totals, where repeating would multiply
    the amount by the length of the interval.
    """
    expanded = {}
    for entry in (series or {}).get("values", []):
        valid_time = entry.get("validTime") or ""
        if "/" not in valid_time:
            continue
        start_text, duration_text = valid_time.split("/", 1)
        hours = _duration_hours(duration_text)
        start = datetime.datetime.fromisoformat(start_text).astimezone(timezone)

        value = entry.get("value")
        if value is not None:
            if spread:
                value = value / hours
            if convert is not None:
                value = convert(value)

        for offset in range(hours):
            expanded[start + datetime.timedelta(hours=offset)] = value
    return expanded


def _icon_for(icon_url):
    """Map a period's icon URL to an emoji. Dual-condition icons name two
    conditions (".../day/tsra,40/rain,20"); the first is the headline one."""
    if not icon_url:
        return _DEFAULT_ICON
    segments = [s for s in icon_url.split("?", 1)[0].split("/") if s]
    for i, segment in enumerate(segments):
        if segment in ("day", "night") and i + 1 < len(segments):
            token = segments[i + 1].split(",", 1)[0]
            return _WEATHER_ICONS.get(token.replace("wind_", "", 1), _DEFAULT_ICON)
    return _DEFAULT_ICON


def _build_daily(periods):
    """Fold NWS's alternating day/night periods into per-date highs, lows and
    icons.

    The daytime period carries the high and the icon, the night one the low.
    The first period is whatever is current - sometimes a night period, when
    the kiosk refreshes before dawn - so periods are bucketed by their own
    date rather than paired off by position.
    """
    highs = {}
    lows = {}
    icons = {}
    for period in periods:
        temperature = period.get("temperature")
        if temperature is None:
            continue
        date = period["startTime"][:10]  # startTime is local, so this is the local date
        if period.get("isDaytime"):
            highs[date] = max(highs.get(date, temperature), temperature)
            icons.setdefault(date, _icon_for(period.get("icon")))
        else:
            lows[date] = min(lows.get(date, temperature), temperature)
    return highs, lows, icons


def _forecast_is_fresh(zip_code):
    """Whether the cache holds a current answer for this zip. A zip that
    didn't resolve counts as an answer, so an unusable one isn't re-looked-up
    on every request - changing it in Settings changes the zip, which
    invalidates this on its own."""
    if _forecast_cache_at is None or _forecast_cache_zip != zip_code:
        return False
    return (datetime.datetime.now() - _forecast_cache_at) <= _FORECAST_TTL


def _ensure_forecast_cache():
    """Fetch and cache both the daily summary and the hourly breakdown for
    the configured zip code, if the cache is missing, stale, or for a
    different zip."""
    zip_code = get_zip_code()
    if _forecast_is_fresh(zip_code):
        return

    with _refill_lock:
        # Another caller may have refilled it while we waited for the lock.
        if _forecast_is_fresh(zip_code):
            return
        _refill_forecast_cache(zip_code)


def _refill_forecast_cache(zip_code):
    """Refill both caches from upstream. Called only under _refill_lock."""
    global _forecast_cache, _hourly_cache, _forecast_cache_at, _forecast_cache_zip

    now = datetime.datetime.now()
    location = _locate_zip(zip_code)
    if location is None:
        _forecast_cache = None
        _hourly_cache = None
        _forecast_cache_at = now
        _forecast_cache_zip = zip_code
        return

    timezone = location["timezone"]

    daily_response = requests.get(location["forecast_url"], headers=_HEADERS, timeout=10)
    daily_response.raise_for_status()
    highs, lows, icons = _build_daily(daily_response.json()["properties"]["periods"])

    # The hourly breakdown comes from the raw grid data rather than the
    # /forecast/hourly endpoint, which omits cloud cover and precipitation
    # amount entirely.
    grid_response = requests.get(location["grid_url"], headers=_HEADERS, timeout=10)
    grid_response.raise_for_status()
    grid = grid_response.json()["properties"]

    temps = _expand_series(grid.get("temperature"), timezone, convert=_to_fahrenheit)
    humidities = _expand_series(grid.get("relativeHumidity"), timezone)
    precip_probabilities = _expand_series(grid.get("probabilityOfPrecipitation"), timezone)
    precip_amounts = _expand_series(
        grid.get("quantitativePrecipitation"), timezone, convert=_to_inches, spread=True
    )
    cloud_covers = _expand_series(grid.get("skyCover"), timezone)

    hourly_by_date = {}
    humidity_by_date = {}
    for when in sorted(temps):
        date = when.date().isoformat()
        temperature = temps[when]
        humidity = humidities.get(when)
        hourly_by_date.setdefault(date, []).append(
            {
                "time": when.strftime("%H:%M"),
                "temp": round(temperature) if temperature is not None else None,
                "humidity": humidity,
                "precipProbability": precip_probabilities.get(when),
                "precipAmount": precip_amounts.get(when),
                "cloudCover": cloud_covers.get(when),
            }
        )
        if humidity is not None:
            humidity_by_date.setdefault(date, []).append(humidity)

    forecast = []
    for date in sorted(highs)[:_FORECAST_DAYS]:
        day_humidity = humidity_by_date.get(date, [])
        forecast.append(
            {
                "date": date,
                "high": round(highs[date]),
                "low": round(lows[date]) if date in lows else None,
                "humidityHigh": max(day_humidity) if day_humidity else None,
                "humidityLow": min(day_humidity) if day_humidity else None,
                "icon": icons.get(date, _DEFAULT_ICON),
            }
        )

    _forecast_cache = forecast
    _hourly_cache = hourly_by_date
    _forecast_cache_at = now
    _forecast_cache_zip = zip_code


def get_forecast():
    """Return the cached (or freshly fetched) 7-day forecast for the
    configured zip code as [{date, high, low, humidityHigh, humidityLow,
    icon}], or None if the zip code doesn't resolve to a location."""
    _ensure_forecast_cache()
    return _forecast_cache


def get_hourly(date):
    """Return the hourly breakdown for one date (within the cached 7-day
    window) as [{time, temp, humidity, precipProbability, precipAmount,
    cloudCover}, ...], or None if there's no data for that date."""
    _ensure_forecast_cache()
    if _hourly_cache is None:
        return None
    return _hourly_cache.get(date)
