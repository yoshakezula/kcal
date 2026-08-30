"""Thin wrapper around Open-Meteo (no API key required): resolve the
configured zip code to coordinates and fetch a 7-day forecast (daily
summary + hourly breakdown), normalized into the small shape the frontend
needs.
"""

import datetime

import requests

from config import get_zip_code

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# Zip -> coordinates barely ever changes, so this cache lives much longer
# than the forecast itself.
_GEOCODE_TTL = datetime.timedelta(hours=24)
_geocode_cache = None
_geocode_cache_at = None
_geocode_cache_zip = None

# One fetch populates both the daily summary and the hourly breakdown, so
# they share a single cache.
_FORECAST_TTL = datetime.timedelta(minutes=30)
_forecast_cache = None
_hourly_cache = None  # {date: [{time, temp, humidity, precipProbability}, ...]}
_forecast_cache_at = None
_forecast_cache_zip = None

# WMO weather codes -> a representative emoji, per Open-Meteo's daily
# weather_code field.
_WEATHER_ICONS = {
    0: "☀️", 1: "\U0001F324️", 2: "⛅", 3: "☁️",
    45: "\U0001F32B️", 48: "\U0001F32B️",
    51: "\U0001F326️", 53: "\U0001F326️", 55: "\U0001F326️",
    56: "\U0001F327️", 57: "\U0001F327️",
    61: "\U0001F327️", 63: "\U0001F327️", 65: "\U0001F327️",
    66: "\U0001F327️", 67: "\U0001F327️",
    71: "\U0001F328️", 73: "\U0001F328️", 75: "\U0001F328️", 77: "\U0001F328️",
    80: "\U0001F326️", 81: "\U0001F326️", 82: "\U0001F327️",
    85: "\U0001F328️", 86: "\U0001F328️",
    95: "⛈️", 96: "⛈️", 99: "⛈️",
}
_DEFAULT_ICON = "\U0001F321️"


def _geocode_zip(zip_code):
    """Resolve a zip/postal code to {label, latitude, longitude}, or None
    if it doesn't match anywhere."""
    global _geocode_cache, _geocode_cache_at, _geocode_cache_zip

    now = datetime.datetime.now()
    is_stale = _geocode_cache_at is None or (now - _geocode_cache_at) > _GEOCODE_TTL
    if _geocode_cache is not None and not is_stale and _geocode_cache_zip == zip_code:
        return _geocode_cache

    response = requests.get(
        GEOCODE_URL,
        params={"name": zip_code, "count": 1, "language": "en", "format": "json"},
        timeout=10,
    )
    response.raise_for_status()
    results = response.json().get("results") or []
    if not results:
        return None

    r = results[0]
    label_parts = [r["name"]]
    if r.get("admin1"):
        label_parts.append(r["admin1"])
    if r.get("country"):
        label_parts.append(r["country"])

    resolved = {
        "label": ", ".join(label_parts),
        "latitude": r["latitude"],
        "longitude": r["longitude"],
    }
    _geocode_cache = resolved
    _geocode_cache_at = now
    _geocode_cache_zip = zip_code
    return resolved


def resolve_zip(zip_code):
    """Return the resolved place label for a zip code, or None if it
    doesn't match anywhere. Used to validate/display what a zip resolves to."""
    location = _geocode_zip(zip_code)
    return location["label"] if location else None


def _ensure_forecast_cache():
    """Fetch and cache both the daily summary and the hourly breakdown for
    the configured zip code, if the cache is missing, stale, or for a
    different zip."""
    global _forecast_cache, _hourly_cache, _forecast_cache_at, _forecast_cache_zip

    zip_code = get_zip_code()

    now = datetime.datetime.now()
    is_stale = _forecast_cache_at is None or (now - _forecast_cache_at) > _FORECAST_TTL
    if _forecast_cache is not None and not is_stale and _forecast_cache_zip == zip_code:
        return

    location = _geocode_zip(zip_code)
    if location is None:
        _forecast_cache = None
        _hourly_cache = None
        _forecast_cache_at = now
        _forecast_cache_zip = zip_code
        return

    response = requests.get(
        FORECAST_URL,
        params={
            "latitude": location["latitude"],
            "longitude": location["longitude"],
            "daily": "temperature_2m_max,temperature_2m_min,weather_code",
            "hourly": "temperature_2m,relative_humidity_2m,precipitation_probability",
            "temperature_unit": "fahrenheit",
            "timezone": "auto",
            "forecast_days": 7,
        },
        timeout=10,
    )
    response.raise_for_status()
    data = response.json()

    daily = data.get("daily", {})
    hourly = data.get("hourly", {})
    dates = daily.get("time", [])
    highs = daily.get("temperature_2m_max", [])
    lows = daily.get("temperature_2m_min", [])
    codes = daily.get("weather_code", [])

    hourly_times = hourly.get("time", [])
    hourly_temps = hourly.get("temperature_2m", [])
    hourly_humidity = hourly.get("relative_humidity_2m", [])
    hourly_precip = hourly.get("precipitation_probability", [])

    hourly_by_date = {}
    humidity_by_date = {}
    for i, timestamp in enumerate(hourly_times):
        date, time_part = timestamp.split("T")
        humidity = hourly_humidity[i] if i < len(hourly_humidity) else None
        hourly_by_date.setdefault(date, []).append(
            {
                "time": time_part,
                "temp": round(hourly_temps[i]) if i < len(hourly_temps) and hourly_temps[i] is not None else None,
                "humidity": humidity,
                "precipProbability": hourly_precip[i] if i < len(hourly_precip) else None,
            }
        )
        if humidity is not None:
            humidity_by_date.setdefault(date, []).append(humidity)

    forecast = []
    for i, date in enumerate(dates):
        day_humidity = humidity_by_date.get(date, [])
        forecast.append(
            {
                "date": date,
                "high": round(highs[i]),
                "low": round(lows[i]),
                "humidityHigh": max(day_humidity) if day_humidity else None,
                "humidityLow": min(day_humidity) if day_humidity else None,
                "icon": _WEATHER_ICONS.get(codes[i], _DEFAULT_ICON),
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
    window) as [{time, temp, humidity, precipProbability}, ...], or None if
    there's no data for that date."""
    _ensure_forecast_cache()
    if _hourly_cache is None:
        return None
    return _hourly_cache.get(date)
