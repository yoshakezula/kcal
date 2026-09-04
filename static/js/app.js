(function () {
  "use strict";

  const WEEKDAY_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  const MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
  ];
  const REFRESH_INTERVAL_MS = 5 * 60 * 1000;
  const WEATHER_REFRESH_INTERVAL_MS = 30 * 60 * 1000;
  const SWIPE_THRESHOLD_PX = 60;
  const GRID_WEEKS = 4; // the "month" grid is a rolling 4-week window, not a calendar month
  const TALL_FORECAST_VIEWS = new Set(["week", "week2", "list"]);
  // week3 has an extra calendar row to fit versus week2, so it gets a
  // mid-sized forecast strip instead of full-tall — more room for the grid.
  const MED_FORECAST_VIEWS = new Set(["week3"]);

  // Grid-style views (month + the multi-week views) all share the same
  // week-row rendering, just with a different row count.
  const VIEW_GRID_WEEKS = { month: GRID_WEEKS, week2: 2, week3: 3 };

  // How many event chips fit in a day cell is measured from the actual
  // rendered DOM (see computeMonthChipCapacity) rather than guessed, so it
  // adapts to any screen size/resolution and to entering/exiting fullscreen.
  // This is just the seed used before anything has been measured yet.
  let monthChipCapacity = 3;

  const state = {
    view: "week2", // "month" | "week" | "week2" | "week3" | "list"
    anchor: startOfDay(new Date()),
    events: [],
  };

  const el = {
    content: document.getElementById("content"),
    contentInner: document.getElementById("contentInner"),
    loadingOverlay: document.getElementById("loadingOverlay"),
    rangeLabel: document.getElementById("rangeLabel"),
    monthBtn: document.getElementById("monthBtn"),
    weekBtn: document.getElementById("weekBtn"),
    week2Btn: document.getElementById("week2Btn"),
    week3Btn: document.getElementById("week3Btn"),
    listBtn: document.getElementById("listBtn"),
    forecastStrip: document.getElementById("forecastStrip"),
    todayBtn: document.getElementById("todayBtn"),
    prevBtn: document.getElementById("prevBtn"),
    nextBtn: document.getElementById("nextBtn"),
    refreshBtn: document.getElementById("refreshBtn"),
    fullscreenBtn: document.getElementById("fullscreenBtn"),
    tasksBtn: document.getElementById("tasksBtn"),
    overlay: document.getElementById("overlay"),
    overlayPanel: document.querySelector(".overlay-panel"),
    overlayBackdrop: document.getElementById("overlayBackdrop"),
    overlayClose: document.getElementById("overlayClose"),
    overlayBody: document.getElementById("overlayBody"),
    disconnectedScreen: document.getElementById("disconnectedScreen"),
    retryBtn: document.getElementById("retryBtn"),
    confettiCanvas: document.getElementById("confettiCanvas"),
  };

  // ---------- Date helpers ----------

  function startOfDay(date) {
    const d = new Date(date);
    d.setHours(0, 0, 0, 0);
    return d;
  }

  function addDays(date, n) {
    const d = new Date(date);
    d.setDate(d.getDate() + n);
    return d;
  }

  function dateKey(date) {
    const y = date.getFullYear();
    const m = String(date.getMonth() + 1).padStart(2, "0");
    const d = String(date.getDate()).padStart(2, "0");
    return `${y}-${m}-${d}`;
  }

  function isSameDay(a, b) {
    return dateKey(a) === dateKey(b);
  }

  function startOfWeek(date) {
    const d = startOfDay(date);
    return addDays(d, -d.getDay());
  }

  // Parses a Google Calendar event's start/end value. All-day values are
  // plain "YYYY-MM-DD" and must be read as local-midnight, not UTC, or a
  // negative-UTC-offset timezone would show them a day early.
  function parseEventBoundary(value, allDay) {
    if (allDay) {
      const [y, m, d] = value.split("-").map(Number);
      return new Date(y, m - 1, d);
    }
    return new Date(value);
  }

  // Returns the array of date-keys (within [rangeStart, rangeEnd] inclusive)
  // that this event touches. Google's all-day "end" date is exclusive.
  function eventDayKeys(evt, rangeStart, rangeEnd) {
    const start = parseEventBoundary(evt.start, evt.allDay);
    let end = parseEventBoundary(evt.end, evt.allDay);
    if (evt.allDay) {
      end = addDays(end, -1); // exclusive -> inclusive last day
    } else {
      end = start; // timed events are placed on their start day only
    }

    const keys = [];
    let cursor = startOfDay(start) < rangeStart ? rangeStart : startOfDay(start);
    const last = startOfDay(end) > rangeEnd ? rangeEnd : startOfDay(end);
    while (cursor <= last) {
      keys.push(dateKey(cursor));
      cursor = addDays(cursor, 1);
    }
    return keys;
  }

  function formatTime(date) {
    return date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  }

  // Compact form for tight spaces, e.g. month-view chips: "9pm", "9:30pm".
  function formatShortTime(date) {
    const hours24 = date.getHours();
    const minutes = date.getMinutes();
    const ampm = hours24 >= 12 ? "pm" : "am";
    const hours12 = hours24 % 12 === 0 ? 12 : hours24 % 12;
    return minutes === 0 ? `${hours12}${ampm}` : `${hours12}:${String(minutes).padStart(2, "0")}${ampm}`;
  }

  function formatDuration(startDate, endDate) {
    const minutes = Math.round((endDate - startDate) / 60000);
    if (minutes <= 0) return "";
    const h = Math.floor(minutes / 60);
    const m = minutes % 60;
    if (h === 0) return `${m}m`;
    if (m === 0) return `${h}h`;
    return `${h}h ${m}m`;
  }

  function eventTimeLabel(evt) {
    if (evt.allDay) return "All day";
    const start = parseEventBoundary(evt.start, false);
    const end = parseEventBoundary(evt.end, false);
    return `${formatTime(start)} – ${formatTime(end)} · ${formatDuration(start, end)}`;
  }

  // ---------- Data loading ----------

  let currentRange = null; // {start: Date, end: Date} last fetched
  let refreshTimer = null;
  let listNextDay = null; // next day to append when the list view is scrolled to the bottom
  let listLoadingMore = false;

  function visibleRange() {
    const gridWeeks = VIEW_GRID_WEEKS[state.view];
    if (gridWeeks) {
      const gridStart = startOfWeek(state.anchor);
      return { start: gridStart, end: addDays(gridStart, gridWeeks * 7 - 1) };
    }
    const weekStart = startOfWeek(state.anchor);
    return { start: weekStart, end: addDays(weekStart, 6) };
  }

  // Always fetched relative to the anchor's week, regardless of which view
  // is active, so switching views never needs a new fetch - every view's
  // visibleRange() (at most GRID_WEEKS wide) is a subset of this window.
  function fetchRange() {
    const gridStart = startOfWeek(state.anchor);
    return { start: gridStart, end: addDays(gridStart, GRID_WEEKS * 7 - 1) };
  }

  async function loadEvents(opts) {
    const silent = !!(opts && opts.silent);
    const range = fetchRange();
    currentRange = range;
    const url = `/api/events?start=${dateKey(range.start)}&end=${dateKey(range.end)}`;

    loadTasksData(); // kick off in parallel; the Tasks overlay shouldn't wait on this, nor should events

    el.refreshBtn.classList.add("spinning");
    if (!silent) showLoading(true);
    try {
      let response;
      try {
        response = await fetch(url);
      } catch (err) {
        return; // transient network error: keep showing last-known events
      }

      if (response.status === 401) {
        showDisconnected(true);
        return;
      }

      if (!response.ok) {
        return;
      }

      showDisconnected(false);
      const data = await response.json();
      state.events = data.events || [];
      render();
    } finally {
      el.refreshBtn.classList.remove("spinning");
      if (!silent) showLoading(false);
    }
  }

  function showLoading(show) {
    el.loadingOverlay.classList.toggle("hidden", !show);
  }

  function showDisconnected(show) {
    el.disconnectedScreen.classList.toggle("hidden", !show);
  }

  // ---------- Weather forecast strip ----------

  function renderForecast(forecast) {
    const highs = forecast.map((d) => d.high);
    const lows = forecast.map((d) => d.low);
    const tempMax = Math.max(...highs);
    const tempMin = Math.min(...lows);
    const tempSpan = Math.max(tempMax - tempMin, 1);
    const today = new Date();

    // Bar position/height are percentages of the track's own box, so the
    // bars fill whatever height the flex layout gives the track (a short
    // track in normal views, a tall one in the "tall" views) instead of a
    // fixed pixel size that would leave dead space in a taller strip.
    const daysHtml = forecast
      .map((day) => {
        const [y, m, d] = day.date.split("-").map(Number);
        const dayDate = new Date(y, m - 1, d);
        const dayLabel = isSameDay(dayDate, today) ? "Today" : WEEKDAY_NAMES[dayDate.getDay()];

        const tempTop = ((tempMax - day.high) / tempSpan) * 100;
        const tempHeight = Math.max(((day.high - day.low) / tempSpan) * 100, 8);

        const humHigh = day.humidityHigh;
        const humLow = day.humidityLow;
        const hasHumidity = humHigh != null && humLow != null;
        const humTop = hasHumidity ? ((100 - humHigh) / 100) * 100 : 0;
        const humHeight = hasHumidity ? Math.max(((humHigh - humLow) / 100) * 100, 8) : 0;

        return `
          <div class="forecast-day ${isSameDay(dayDate, today) ? "today" : ""}" data-date="${day.date}">
            <div class="forecast-day-label">${dayLabel}</div>
            <div class="forecast-icon">${day.icon || ""}</div>
            <div class="forecast-metrics">
              <div class="forecast-metric">
                <span class="forecast-value">${day.high}&deg;</span>
                <div class="forecast-track">
                  <div class="forecast-bar forecast-bar-temp" style="top:${tempTop}%;height:${tempHeight}%"></div>
                </div>
                <span class="forecast-value forecast-value-low">${day.low}&deg;</span>
              </div>
              <div class="forecast-metric">
                <span class="forecast-value">${hasHumidity ? humHigh + "%" : "–"}</span>
                <div class="forecast-track">
                  ${
                    hasHumidity
                      ? `<div class="forecast-bar forecast-bar-humidity" style="top:${humTop}%;height:${humHeight}%"></div>`
                      : ""
                  }
                </div>
                <span class="forecast-value forecast-value-low">${hasHumidity ? humLow + "%" : "–"}</span>
              </div>
            </div>
          </div>`;
      })
      .join("");

    el.forecastStrip.innerHTML = `
      <div class="forecast-legend">
        <div class="forecast-legend-item"><span class="forecast-swatch forecast-swatch-temp"></span>Temp &deg;F</div>
        <div class="forecast-legend-item"><span class="forecast-swatch forecast-swatch-humidity"></span>Humidity %</div>
      </div>
      <div class="forecast-days">${daysHtml}</div>`;

    el.forecastStrip.querySelectorAll(".forecast-day").forEach((dayEl) => {
      dayEl.addEventListener("click", () => {
        const date = dayEl.getAttribute("data-date");
        if (date) openHourlyForecast(date);
      });
    });
  }

  // Hour labels are compact, matching formatShortTime's style elsewhere: "12a", "9p".
  function formatHourLabel(timeStr) {
    const hour24 = parseInt(timeStr.slice(0, 2), 10);
    const ampm = hour24 >= 12 ? "p" : "a";
    const hour12 = hour24 % 12 === 0 ? 12 : hour24 % 12;
    return `${hour12}${ampm}`;
  }

  const HOURLY_CHART_W = 900;
  const HOURLY_CHART_H = 170;
  const HOURLY_PAD_LEFT = 34;
  const HOURLY_PAD_RIGHT = 36;
  const HOURLY_PAD_TOP = 16;
  const HOURLY_PAD_BOTTOM = 8;
  const HOURLY_PAD_LEFT_PCT = ((HOURLY_PAD_LEFT / HOURLY_CHART_W) * 100).toFixed(2);
  const HOURLY_PAD_RIGHT_PCT = ((HOURLY_PAD_RIGHT / HOURLY_CHART_W) * 100).toFixed(2);
  const HUMIDITY_SHADE_RGB = "42,120,214"; // matches --humidity-color
  const PRECIP_SHADE_RGB = "26,158,143"; // matches --precip-color

  // Two series, two scales: temperature reads off the left axis, humidity
  // (always 0-100%) off the right - each drawn in its own color so the
  // axis a value belongs to is never ambiguous.
  function buildHourlyChartSvg(hours) {
    const temps = hours.map((h) => h.temp).filter((t) => t != null);
    if (!temps.length) {
      return `<p class="hourly-empty">No hourly temperature data.</p>`;
    }

    const plotW = HOURLY_CHART_W - HOURLY_PAD_LEFT - HOURLY_PAD_RIGHT;
    const plotH = HOURLY_CHART_H - HOURLY_PAD_TOP - HOURLY_PAD_BOTTOM;
    const tempMax = Math.max(...temps);
    const tempMin = Math.min(...temps);
    const tempSpan = Math.max(tempMax - tempMin, 1);

    const n = hours.length;
    const xAt = (i) => (n <= 1 ? HOURLY_PAD_LEFT + plotW / 2 : HOURLY_PAD_LEFT + (i / (n - 1)) * plotW);
    const yTempAt = (t) => HOURLY_PAD_TOP + ((tempMax - t) / tempSpan) * plotH;
    const yHumAt = (h) => HOURLY_PAD_TOP + ((100 - h) / 100) * plotH;

    let maxIdx = -1;
    let minIdx = -1;
    hours.forEach((h, i) => {
      if (h.temp == null) return;
      if (maxIdx === -1 || h.temp > hours[maxIdx].temp) maxIdx = i;
      if (minIdx === -1 || h.temp < hours[minIdx].temp) minIdx = i;
    });

    const tempPoints = hours
      .map((h, i) => (h.temp != null ? `${xAt(i).toFixed(1)},${yTempAt(h.temp).toFixed(1)}` : null))
      .filter(Boolean)
      .join(" ");
    const humPoints = hours
      .map((h, i) => (h.humidity != null ? `${xAt(i).toFixed(1)},${yHumAt(h.humidity).toFixed(1)}` : null))
      .filter(Boolean)
      .join(" ");

    const tempGridHtml = [tempMax, (tempMax + tempMin) / 2, tempMin]
      .map((v) => {
        const y = yTempAt(v).toFixed(1);
        return `
          <line x1="${HOURLY_PAD_LEFT}" y1="${y}" x2="${HOURLY_CHART_W - HOURLY_PAD_RIGHT}" y2="${y}" class="hourly-gridline" />
          <text x="${HOURLY_PAD_LEFT - 6}" y="${y}" class="hourly-gridlabel hourly-gridlabel-temp" text-anchor="end" dominant-baseline="middle">${Math.round(v)}&deg;</text>`;
      })
      .join("");

    const humGridHtml = [100, 50, 0]
      .map((v) => {
        const y = yHumAt(v).toFixed(1);
        return `<text x="${HOURLY_CHART_W - HOURLY_PAD_RIGHT + 6}" y="${y}" class="hourly-gridlabel hourly-gridlabel-humidity" text-anchor="start" dominant-baseline="middle">${v}%</text>`;
      })
      .join("");

    let markersHtml = "";
    if (maxIdx !== -1) {
      const x = xAt(maxIdx);
      const y = yTempAt(hours[maxIdx].temp);
      const labelY = y < HOURLY_PAD_TOP + 16 ? y + 16 : y - 8;
      markersHtml += `
        <circle cx="${x}" cy="${y}" r="4" class="hourly-marker hourly-marker-temp" />
        <text x="${x}" y="${labelY}" class="hourly-point-label" text-anchor="middle">${Math.round(hours[maxIdx].temp)}&deg;</text>`;
    }
    if (minIdx !== -1 && minIdx !== maxIdx) {
      const x = xAt(minIdx);
      const y = yTempAt(hours[minIdx].temp);
      const labelY = y > HOURLY_CHART_H - HOURLY_PAD_BOTTOM - 10 ? y - 8 : y + 16;
      markersHtml += `
        <circle cx="${x}" cy="${y}" r="4" class="hourly-marker hourly-marker-temp" />
        <text x="${x}" y="${labelY}" class="hourly-point-label" text-anchor="middle">${Math.round(hours[minIdx].temp)}&deg;</text>`;
    }

    return `
      <svg class="hourly-svg" viewBox="0 0 ${HOURLY_CHART_W} ${HOURLY_CHART_H}" preserveAspectRatio="none">
        ${tempGridHtml}
        ${humGridHtml}
        <polyline points="${humPoints}" class="hourly-line hourly-line-humidity" fill="none" />
        <polyline points="${tempPoints}" class="hourly-line hourly-line-temp" fill="none" />
        ${markersHtml}
      </svg>`;
  }

  function buildHourlyAxisRow(hours, formatCell, shadeCell) {
    return `
      <div class="hourly-axis-row" style="padding-left:${HOURLY_PAD_LEFT_PCT}%;padding-right:${HOURLY_PAD_RIGHT_PCT}%">
        ${hours
          .map((h) => {
            const bg = shadeCell ? shadeCell(h) : null;
            const style = bg ? ` style="background:${bg}"` : "";
            return `<div${style}>${formatCell(h)}</div>`;
          })
          .join("")}
      </div>`;
  }

  // Shades a cell by how far its value sits on a fixed 0-100 scale, not by
  // where it falls within this day's own min/max - so a 40% reading looks
  // the same shade on a calm day as it does on a volatile one.
  function absoluteScaleShade(value, rgb) {
    if (value == null) return null;
    const t = Math.max(0, Math.min(100, value)) / 100;
    const alpha = 0.08 + t * 0.5;
    return `rgba(${rgb},${alpha.toFixed(2)})`;
  }

  function buildHourlyForecastHtml(date, hours) {
    const [y, m, d] = date.split("-").map(Number);
    const dayDate = new Date(y, m - 1, d);
    const heading = `${WEEKDAY_NAMES[dayDate.getDay()]}, ${MONTH_NAMES[dayDate.getMonth()]} ${dayDate.getDate()}`;

    if (!hours.length) {
      return `<h2>${heading}</h2><p>No hourly forecast available.</p>`;
    }

    return `
      <h2>${heading}</h2>
      <div class="hourly-legend">
        <span class="hourly-legend-item"><span class="forecast-swatch forecast-swatch-temp"></span>Temp &deg;F</span>
        <span class="hourly-legend-item"><span class="forecast-swatch forecast-swatch-humidity"></span>Humidity %</span>
      </div>
      <div class="hourly-grid">
        <div class="hourly-row-label"></div>
        <div class="hourly-chart-cell">${buildHourlyChartSvg(hours)}</div>

        <div class="hourly-row-label">Hour</div>
        ${buildHourlyAxisRow(hours, (h) => formatHourLabel(h.time))}

        <div class="hourly-row-label">Humidity</div>
        ${buildHourlyAxisRow(
          hours,
          (h) => (h.humidity != null ? h.humidity + "%" : "–"),
          (h) => absoluteScaleShade(h.humidity, HUMIDITY_SHADE_RGB)
        )}

        <div class="hourly-row-label">Rain</div>
        ${buildHourlyAxisRow(
          hours,
          (h) => (h.precipProbability != null ? h.precipProbability + "%" : "–"),
          (h) => absoluteScaleShade(h.precipProbability, PRECIP_SHADE_RGB)
        )}
      </div>`;
  }

  async function openHourlyForecast(date) {
    openOverlay(`<p>Loading&hellip;</p>`, { wide: true });
    try {
      const response = await fetch(`/api/weather/hourly?date=${encodeURIComponent(date)}`);
      if (!response.ok) {
        openOverlay(`<p>Couldn't load the hourly forecast for that day.</p>`, { wide: true });
        return;
      }
      const data = await response.json();
      openOverlay(buildHourlyForecastHtml(date, data.hours || []), { wide: true });
    } catch (err) {
      openOverlay(`<p>Couldn't load the hourly forecast for that day.</p>`, { wide: true });
    }
  }

  let lastForecast = null; // most recently fetched forecast, independent of which view is showing

  // The forecast strip is a top-level row, not part of any one view's
  // content, so its visibility depends on both whether we have data and
  // whether the current view wants it shown (hidden in month view - it's
  // dense enough there already).
  function updateForecastVisibility() {
    const shouldShow = state.view !== "month" && lastForecast && lastForecast.length > 0;
    el.forecastStrip.classList.toggle("hidden", !shouldShow);
    el.forecastStrip.classList.toggle("forecast-strip-tall", TALL_FORECAST_VIEWS.has(state.view));
    el.forecastStrip.classList.toggle("forecast-strip-med", MED_FORECAST_VIEWS.has(state.view));
    // Re-render so the bar-chart scale (short vs. tall) matches the new view.
    if (shouldShow) renderForecast(lastForecast);
  }

  async function loadWeather() {
    try {
      const response = await fetch("/api/weather");
      if (!response.ok) {
        lastForecast = null; // no location configured yet, or a transient error
        updateForecastVisibility();
        return;
      }
      const data = await response.json();
      lastForecast = data.forecast || null;
      if (lastForecast && lastForecast.length) {
        renderForecast(lastForecast);
      }
      updateForecastVisibility();
    } catch (err) {
      // transient network error: keep showing the last-known forecast
    }
  }

  // ---------- Rendering ----------

  function render() {
    renderRangeLabel();
    const gridWeeks = VIEW_GRID_WEEKS[state.view];
    if (gridWeeks) {
      renderGrid(gridWeeks);
    } else if (state.view === "week") {
      renderWeekColumns();
    } else {
      renderList();
    }
  }

  function formatDateRangeLabel(start, end) {
    const sameMonth = start.getMonth() === end.getMonth();
    const sameYear = start.getFullYear() === end.getFullYear();
    const startLabel = sameYear
      ? `${MONTH_NAMES[start.getMonth()].slice(0, 3)} ${start.getDate()}`
      : `${MONTH_NAMES[start.getMonth()].slice(0, 3)} ${start.getDate()}, ${start.getFullYear()}`;
    const endLabel = sameMonth
      ? `${end.getDate()}`
      : `${MONTH_NAMES[end.getMonth()].slice(0, 3)} ${end.getDate()}`;
    return `${startLabel} – ${endLabel}, ${end.getFullYear()}`;
  }

  function renderRangeLabel() {
    const range = visibleRange();
    el.rangeLabel.textContent = formatDateRangeLabel(range.start, range.end);
  }

  function eventsByDay(rangeStart, rangeEnd) {
    const map = new Map();
    for (const evt of state.events) {
      for (const key of eventDayKeys(evt, rangeStart, rangeEnd)) {
        if (!map.has(key)) map.set(key, []);
        map.get(key).push(evt);
      }
    }
    return map;
  }

  function buildMonthGridHtml(gridStart, byDay, today, chipCap, numWeeks) {
    const weekdaysHtml = WEEKDAY_NAMES.map((n) => `<div>${n}</div>`).join("");
    const todayKey = dateKey(today);

    let rowsHtml = "";
    for (let row = 0; row < numWeeks; row++) {
      let rowHtml = "";
      for (let col = 0; col < 7; col++) {
        const day = addDays(gridStart, row * 7 + col);
        const key = dateKey(day);
        const dayEvents = byDay.get(key) || [];
        const isToday = isSameDay(day, today);
        const isPast = key < todayKey;
        const shown = dayEvents.slice(0, chipCap);
        const extra = dayEvents.length - shown.length;

        // Since the grid no longer aligns to calendar month boundaries, label
        // the 1st of each month so a range spanning two months stays clear.
        const dayLabel =
          day.getDate() === 1
            ? `${MONTH_NAMES[day.getMonth()].slice(0, 3)} 1`
            : String(day.getDate());

        const chipsHtml = shown
          .map((evt) => {
            const timeHtml = evt.allDay
              ? ""
              : `<span class="event-chip-time">${formatShortTime(parseEventBoundary(evt.start, false))}</span>`;
            return `<div class="event-chip" style="background-color:${evt.color}"><span class="event-chip-title">${escapeHtml(evt.title)}</span>${timeHtml}</div>`;
          })
          .join("");
        const moreHtml = extra > 0 ? `<div class="day-more">+${extra} more</div>` : "";

        rowHtml += `
          <div class="day-cell ${isToday ? "today" : ""} ${isPast ? "past-day" : ""}" data-date-key="${key}">
            <div class="day-number">${dayLabel}</div>
            <div class="day-events">${chipsHtml}${moreHtml}</div>
          </div>`;
      }
      rowsHtml += `<div class="month-week-row">${rowHtml}</div>`;
    }

    return `
      <div class="month-grid" style="grid-template-rows:auto repeat(${numWeeks},1fr)">
        <div class="month-weekdays">${weekdaysHtml}</div>
        <div class="month-rows" style="grid-template-rows:repeat(${numWeeks},1fr);grid-row:2 / span ${numWeeks}">${rowsHtml}</div>
      </div>`;
  }

  // Measures how many event-chip lines actually fit inside a rendered day
  // cell, using the real cell height, day-number height, and chip height
  // from the DOM. Returns null if there's nothing on screen to measure yet
  // (e.g. the visible range has zero events, so there's no chip to sample).
  function computeMonthChipCapacity() {
    const cellEl = el.contentInner.querySelector(".day-cell");
    const numberEl = el.contentInner.querySelector(".day-number");
    const eventsEl = el.contentInner.querySelector(".day-events");
    const chipEl = el.contentInner.querySelector(".event-chip");
    if (!cellEl || !numberEl || !eventsEl || !chipEl) return null;

    const cellStyle = getComputedStyle(cellEl);
    const paddingTop = parseFloat(cellStyle.paddingTop) || 0;
    const paddingBottom = parseFloat(cellStyle.paddingBottom) || 0;
    const numberMarginBottom = parseFloat(getComputedStyle(numberEl).marginBottom) || 0;

    const availableHeight =
      cellEl.clientHeight - paddingTop - paddingBottom - numberEl.offsetHeight - numberMarginBottom;

    const gap = parseFloat(getComputedStyle(eventsEl).rowGap) || 0;
    const chipHeight = chipEl.offsetHeight;
    if (chipHeight <= 0) return null;

    const slots = Math.floor((availableHeight + gap) / (chipHeight + gap));
    return Math.max(1, slots);
  }

  function attachDayCellHandlers(byDay) {
    el.contentInner.querySelectorAll(".day-cell").forEach((cellEl) => {
      cellEl.addEventListener("click", () => {
        const key = cellEl.getAttribute("data-date-key");
        openDayOverlay(key, byDay.get(key) || []);
      });
    });
  }

  function renderGrid(numWeeks) {
    const gridStart = startOfWeek(state.anchor);
    const gridEnd = addDays(gridStart, numWeeks * 7 - 1);
    const byDay = eventsByDay(gridStart, gridEnd);
    const today = new Date();

    // First pass: render with the last-known-good chip capacity so there's
    // something on screen to measure against real, laid-out dimensions.
    el.contentInner.innerHTML = buildMonthGridHtml(gridStart, byDay, today, monthChipCapacity, numWeeks);

    const measured = computeMonthChipCapacity();
    if (measured !== null && measured !== monthChipCapacity) {
      monthChipCapacity = measured;
      el.contentInner.innerHTML = buildMonthGridHtml(gridStart, byDay, today, monthChipCapacity, numWeeks);
    }

    attachDayCellHandlers(byDay);
  }

  function sortDayEvents(dayEvents) {
    return dayEvents.slice().sort((a, b) => {
      if (a.allDay !== b.allDay) return a.allDay ? -1 : 1;
      return (a.start || "").localeCompare(b.start || "");
    });
  }

  function renderWeekColumns() {
    const weekStart = startOfWeek(state.anchor);
    const weekEnd = addDays(weekStart, 6);
    const byDay = eventsByDay(weekStart, weekEnd);
    const today = new Date();

    let colsHtml = "";
    for (let i = 0; i < 7; i++) {
      const day = addDays(weekStart, i);
      const key = dateKey(day);
      const dayEvents = sortDayEvents(byDay.get(key) || []);
      const isToday = isSameDay(day, today);

      const eventsHtml = dayEvents.length
        ? dayEvents
            .map(
              (evt) => `
          <div class="week-col-event" data-event-id="${evt.id}" data-date-key="${key}">
            <div class="week-col-event-title">
              <span class="week-col-event-dot" style="background-color:${evt.color}"></span>
              ${escapeHtml(evt.title)}
            </div>
            <div class="week-col-event-time">${eventTimeLabel(evt)}</div>
          </div>`
            )
            .join("")
        : `<div class="week-col-empty">No events</div>`;

      colsHtml += `
        <div class="week-col">
          <div class="week-col-header ${isToday ? "today" : ""}">
            <span class="day-name">${WEEKDAY_NAMES[day.getDay()]}</span>
            <span class="day-date">${MONTH_NAMES[day.getMonth()].slice(0, 3)} ${day.getDate()}</span>
          </div>
          <div class="week-col-events">${eventsHtml}</div>
        </div>`;
    }

    el.contentInner.innerHTML = `<div class="week-columns">${colsHtml}</div>`;

    el.contentInner.querySelectorAll(".week-col-event").forEach((rowEl) => {
      rowEl.addEventListener("click", () => {
        const key = rowEl.getAttribute("data-date-key");
        const id = rowEl.getAttribute("data-event-id");
        const evt = (byDay.get(key) || []).find((e) => e.id === id);
        if (evt) openEventOverlay(evt);
      });
    });
  }

  function buildListDaySectionHtml(day, byDay, today) {
    const key = dateKey(day);
    const dayEvents = sortDayEvents(byDay.get(key) || []);
    const isToday = isSameDay(day, today);

    const rowsHtml = dayEvents.length
      ? dayEvents
          .map(
            (evt) => `
        <div class="week-event-row" data-event-id="${evt.id}">
          <div class="week-event-dot" style="background-color:${evt.color}"></div>
          <div class="week-event-title">${escapeHtml(evt.title)}</div>
          <div class="week-event-time">${eventTimeLabel(evt)}</div>
        </div>`
          )
          .join("")
      : `<div class="week-day-empty">No events</div>`;

    return `
      <section class="week-day-section">
        <div class="week-day-header ${isToday ? "today" : ""}">
          <span class="day-name">${WEEKDAY_NAMES[day.getDay()]}</span>
          <span class="day-date">${MONTH_NAMES[day.getMonth()].slice(0, 3)} ${day.getDate()}</span>
        </div>
        ${rowsHtml}
      </section>`;
  }

  // Appends `count` more days of sections to the list view without touching
  // what's already rendered, so scroll position is preserved while loading more.
  function appendListDays(startDay, count) {
    const endDay = addDays(startDay, count - 1);
    const byDay = eventsByDay(startDay, endDay);
    const today = new Date();

    let sectionsHtml = "";
    for (let i = 0; i < count; i++) {
      sectionsHtml += buildListDaySectionHtml(addDays(startDay, i), byDay, today);
    }
    el.contentInner.querySelector(".week-list").insertAdjacentHTML("beforeend", sectionsHtml);
    listNextDay = addDays(endDay, 1);
  }

  // Fetches another week of events once the loaded range runs out, then
  // appends the next 7 days to the list.
  async function loadMoreListDays() {
    if (listLoadingMore || !listNextDay) return;
    const chunkStart = listNextDay;
    const chunkEnd = addDays(chunkStart, 6);

    if (chunkEnd > currentRange.end) {
      listLoadingMore = true;
      try {
        const fetchStart = addDays(currentRange.end, 1);
        const response = await fetch(`/api/events?start=${dateKey(fetchStart)}&end=${dateKey(chunkEnd)}`);
        if (!response.ok) return; // retry on next scroll
        const data = await response.json();
        const existingIds = new Set(state.events.map((e) => e.id));
        for (const evt of data.events || []) {
          if (!existingIds.has(evt.id)) state.events.push(evt);
        }
        currentRange.end = chunkEnd;
      } catch (err) {
        return; // transient network error: retry on next scroll
      } finally {
        listLoadingMore = false;
      }
    }

    appendListDays(chunkStart, 7);
  }

  function renderList() {
    const weekStart = startOfWeek(state.anchor);
    el.contentInner.innerHTML = `<div class="week-list"></div>`;
    const listEl = el.contentInner.querySelector(".week-list");

    listEl.addEventListener("click", (e) => {
      const rowEl = e.target.closest(".week-event-row");
      if (!rowEl) return;
      const id = rowEl.getAttribute("data-event-id");
      const evt = state.events.find((ev) => ev.id === id);
      if (evt) openEventOverlay(evt);
    });

    listEl.addEventListener("scroll", () => {
      if (listEl.scrollTop + listEl.clientHeight >= listEl.scrollHeight - 300) {
        loadMoreListDays();
      }
    });

    appendListDays(weekStart, 7);
  }

  function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
  }

  // ---------- Overlay ----------

  function openOverlay(bodyHtml, opts) {
    el.overlayBody.innerHTML = bodyHtml;
    el.overlayPanel.classList.toggle("overlay-panel-wide", !!(opts && opts.wide));
    el.overlay.classList.remove("hidden");
  }

  function closeOverlay() {
    el.overlay.classList.add("hidden");
  }

  function detailRowHtml(evt) {
    return `
      <div class="detail-row">
        <div class="detail-title">
          <span class="week-event-dot" style="background-color:${evt.color}"></span>
          ${escapeHtml(evt.title)}
        </div>
        <div class="detail-meta">${eventTimeLabel(evt)}</div>
        ${evt.location ? `<div class="detail-meta">${escapeHtml(evt.location)}</div>` : ""}
        <div class="detail-meta">${escapeHtml(evt.calendarName || "")}</div>
      </div>`;
  }

  function openDayOverlay(key, dayEvents) {
    const [y, m, d] = key.split("-").map(Number);
    const day = new Date(y, m - 1, d);
    const heading = `${WEEKDAY_NAMES[day.getDay()]}, ${MONTH_NAMES[day.getMonth()]} ${day.getDate()}`;
    const sorted = sortDayEvents(dayEvents);
    const rows = sorted.length
      ? sorted.map(detailRowHtml).join("")
      : `<p>No events.</p>`;
    openOverlay(`<h2>${heading}</h2>${rows}`);
  }

  function openEventOverlay(evt) {
    openOverlay(detailRowHtml(evt));
  }

  el.overlayBackdrop.addEventListener("click", closeOverlay);
  el.overlayClose.addEventListener("click", closeOverlay);

  // ---------- Confetti ----------

  const CONFETTI_COLORS = ["#f94144", "#f3722c", "#f8961e", "#f9c74f", "#90be6d", "#43aa8b", "#577590", "#c8b6ff"];
  const CONFETTI_DURATION_MS = 4000;
  let confettiRafId = null;

  function fireConfetti() {
    const canvas = el.confettiCanvas;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    canvas.width = window.innerWidth;
    canvas.height = window.innerHeight;
    canvas.classList.remove("hidden");

    const particleCount = 160;
    const particles = [];
    for (let i = 0; i < particleCount; i++) {
      particles.push({
        x: Math.random() * canvas.width,
        y: -20 - Math.random() * canvas.height * 0.5,
        size: 6 + Math.random() * 6,
        color: CONFETTI_COLORS[Math.floor(Math.random() * CONFETTI_COLORS.length)],
        vx: -2 + Math.random() * 4,
        vy: 2 + Math.random() * 3,
        rotation: Math.random() * 360,
        rotationSpeed: -8 + Math.random() * 16,
      });
    }

    const startTime = performance.now();
    if (confettiRafId !== null) cancelAnimationFrame(confettiRafId);

    function step(now) {
      const elapsed = now - startTime;
      ctx.clearRect(0, 0, canvas.width, canvas.height);

      particles.forEach((p) => {
        p.x += p.vx;
        p.y += p.vy;
        p.rotation += p.rotationSpeed;

        ctx.save();
        ctx.translate(p.x, p.y);
        ctx.rotate((p.rotation * Math.PI) / 180);
        ctx.fillStyle = p.color;
        ctx.fillRect(-p.size / 2, -p.size / 4, p.size, p.size / 2);
        ctx.restore();
      });

      if (elapsed < CONFETTI_DURATION_MS) {
        confettiRafId = requestAnimationFrame(step);
      } else {
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        canvas.classList.add("hidden");
        confettiRafId = null;
      }
    }

    confettiRafId = requestAnimationFrame(step);
  }

  // ---------- Tasks ----------

  // Task lists are fetched once and cached; the selected list and its tasks
  // are refetched whenever the dropdown changes or the popup is reopened.
  const taskState = {
    taskLists: [],
    selectedListId: null,
    tasks: [],
    pointsEnabled: false,
    totalPoints: 0,
    loaded: false, // true once a load attempt (success or failure) has completed
    loadOk: false,
  };
  let tasksPromise = null; // in-flight load, so callers can await instead of duplicating the fetch

  function escapeAttr(str) {
    return String(str).replace(/"/g, "&quot;");
  }

  function buildTasksOverlayHtml() {
    const optionsHtml = taskState.taskLists
      .map(
        (tl) =>
          `<option value="${escapeAttr(tl.id)}" ${tl.id === taskState.selectedListId ? "selected" : ""}>${escapeHtml(tl.title)}</option>`
      )
      .join("");

    const rowsHtml = taskState.tasks.length
      ? taskState.tasks
          .map(
            (t) => `
        <div class="task-row ${t.completed ? "task-completed" : ""}" data-task-id="${escapeAttr(t.id)}">
          <button class="task-check ${t.completed ? "checked" : ""}" aria-label="${t.completed ? "Mark incomplete" : "Mark complete"}"></button>
          <div class="task-title">${escapeHtml(t.title)}</div>
          ${taskState.pointsEnabled && t.points ? `<div class="task-points">+${t.points}</div>` : ""}
        </div>`
          )
          .join("")
      : `<p class="task-empty">No tasks in this list.</p>`;

    const totalPointsHtml = taskState.pointsEnabled
      ? `<div class="task-points-total">Total Points: ${taskState.totalPoints || 0}</div>`
      : "";

    return `
      <h2>Tasks</h2>
      ${totalPointsHtml}
      <select class="task-list-select" id="taskListSelect">${optionsHtml}</select>
      <div class="task-list" id="taskListBody">${rowsHtml}</div>`;
  }

  function renderTasksOverlay() {
    openOverlay(buildTasksOverlayHtml());

    const selectEl = document.getElementById("taskListSelect");
    if (selectEl) {
      selectEl.addEventListener("change", () => {
        taskState.selectedListId = selectEl.value;
        loadTasksForSelectedList();
      });
    }

    el.overlayBody.querySelectorAll(".task-check").forEach((btn) => {
      btn.addEventListener("click", () => {
        const taskId = btn.closest(".task-row").getAttribute("data-task-id");
        toggleTask(taskId);
      });
    });
  }

  async function loadTaskLists() {
    try {
      const response = await fetch("/api/tasklists");
      if (!response.ok) return false;
      const data = await response.json();
      taskState.taskLists = data.taskLists || [];
      if (!taskState.taskLists.some((tl) => tl.id === taskState.selectedListId)) {
        const fallback = taskState.taskLists.find((tl) => tl.id === data.defaultId) || taskState.taskLists[0];
        taskState.selectedListId = fallback ? fallback.id : null;
      }
      return true;
    } catch (err) {
      return false;
    }
  }

  async function loadTasksForSelectedList(opts) {
    const silent = !!(opts && opts.silent);
    if (!taskState.selectedListId) {
      taskState.tasks = [];
      taskState.pointsEnabled = false;
      taskState.totalPoints = 0;
      if (!silent) renderTasksOverlay();
      return;
    }
    try {
      const response = await fetch(`/api/tasks?tasklist=${encodeURIComponent(taskState.selectedListId)}`);
      const data = response.ok ? await response.json() : {};
      taskState.tasks = data.tasks || [];
      taskState.pointsEnabled = !!data.pointsEnabled;
      taskState.totalPoints = data.totalPoints || 0;
    } catch (err) {
      taskState.tasks = [];
      taskState.pointsEnabled = false;
      taskState.totalPoints = 0;
    }
    if (!silent) renderTasksOverlay();
  }

  // Loads task lists + the selected list's tasks, sharing one in-flight
  // promise so a background prefetch (triggered on every calendar reload)
  // and an overlay open racing it don't fire duplicate requests. Resolves
  // `tasksPromise` back to null when done so the next calendar reload
  // refetches fresh data instead of relying on a stale cache forever.
  function loadTasksData() {
    if (tasksPromise) return tasksPromise;
    tasksPromise = (async () => {
      try {
        const ok = await loadTaskLists();
        taskState.loadOk = ok;
        if (ok && taskState.taskLists.length) {
          await loadTasksForSelectedList({ silent: true });
        }
      } finally {
        taskState.loaded = true;
        tasksPromise = null;
      }
    })();
    return tasksPromise;
  }

  async function openTasksOverlay() {
    if (!taskState.loaded) {
      openOverlay(`<h2>Tasks</h2><p>Loading&hellip;</p>`);
      await loadTasksData();
    }
    if (!taskState.loadOk) {
      openOverlay(`<h2>Tasks</h2><p>Couldn't load task lists. Check Settings.</p>`);
      return;
    }
    if (!taskState.taskLists.length) {
      openOverlay(`<h2>Tasks</h2><p>No task lists found.</p>`);
      return;
    }
    renderTasksOverlay();
  }

  async function toggleTask(taskId) {
    const task = taskState.tasks.find((t) => t.id === taskId);
    if (!task) return;
    const newCompleted = !task.completed;
    const pointsDelta = taskState.pointsEnabled && task.points ? (newCompleted ? task.points : -task.points) : 0;

    task.completed = newCompleted; // optimistic
    if (pointsDelta) taskState.totalPoints = (taskState.totalPoints || 0) + pointsDelta;
    renderTasksOverlay();
    if (newCompleted) fireConfetti();

    try {
      const response = await fetch("/api/tasks/toggle", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tasklist: taskState.selectedListId, task: taskId, completed: newCompleted }),
      });
      if (!response.ok) throw new Error("toggle failed");
      const data = await response.json();
      if (typeof data.totalPoints === "number") {
        taskState.totalPoints = data.totalPoints;
        renderTasksOverlay();
      }
    } catch (err) {
      task.completed = !newCompleted; // revert on failure
      if (pointsDelta) taskState.totalPoints = (taskState.totalPoints || 0) - pointsDelta;
      renderTasksOverlay();
    }
  }

  el.tasksBtn.addEventListener("click", openTasksOverlay);

  // ---------- Navigation ----------

  function goToday() {
    state.anchor = startOfDay(new Date());
    loadEvents();
  }

  function goPrev() {
    const step = (VIEW_GRID_WEEKS[state.view] || 1) * 7;
    state.anchor = addDays(state.anchor, -step);
    loadEvents();
  }

  function goNext() {
    const step = (VIEW_GRID_WEEKS[state.view] || 1) * 7;
    state.anchor = addDays(state.anchor, step);
    loadEvents();
  }

  function setView(view) {
    if (view === state.view) return;
    state.view = view;
    el.monthBtn.classList.toggle("active", view === "month");
    el.weekBtn.classList.toggle("active", view === "week");
    el.week2Btn.classList.toggle("active", view === "week2");
    el.week3Btn.classList.toggle("active", view === "week3");
    el.listBtn.classList.toggle("active", view === "list");
    updateForecastVisibility();
    render();
  }

  el.todayBtn.addEventListener("click", goToday);
  el.prevBtn.addEventListener("click", goPrev);
  el.nextBtn.addEventListener("click", goNext);
  el.monthBtn.addEventListener("click", () => setView("month"));
  el.weekBtn.addEventListener("click", () => setView("week"));
  el.week2Btn.addEventListener("click", () => setView("week2"));
  el.week3Btn.addEventListener("click", () => setView("week3"));
  el.listBtn.addEventListener("click", () => setView("list"));
  el.refreshBtn.addEventListener("click", loadEvents);
  el.retryBtn.addEventListener("click", loadEvents);

  // ---------- Swipe navigation ----------

  (function setupSwipe() {
    let startX = 0;
    let startY = 0;
    let tracking = false;

    el.content.addEventListener("pointerdown", (e) => {
      startX = e.clientX;
      startY = e.clientY;
      tracking = true;
    });

    el.content.addEventListener("pointerup", (e) => {
      if (!tracking) return;
      tracking = false;
      const dx = e.clientX - startX;
      const dy = e.clientY - startY;
      if (Math.abs(dx) < SWIPE_THRESHOLD_PX) return;
      if (Math.abs(dx) < Math.abs(dy) * 1.5) return;
      if (dx < 0) goNext();
      else goPrev();
    });

    el.content.addEventListener("pointercancel", () => {
      tracking = false;
    });
  })();

  // ---------- Fullscreen ----------

  el.fullscreenBtn.addEventListener("click", () => {
    if (document.fullscreenElement) {
      document.exitFullscreen();
    } else {
      document.documentElement.requestFullscreen().catch(() => {});
    }
  });

  document.addEventListener("fullscreenchange", () => {
    if (VIEW_GRID_WEEKS[state.view]) render();
  });

  // Re-measure chip capacity if the window/viewport size actually changes
  // (e.g. resizing the browser, or a display resolution change), not just
  // on fullscreen toggles.
  let resizeTimer = null;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      if (VIEW_GRID_WEEKS[state.view]) render();
    }, 200);
  });

  // ---------- Touch ripple feedback ----------

  document.addEventListener("pointerdown", (e) => {
    if (e.pointerType !== "touch") return;
    const ripple = document.createElement("div");
    ripple.className = "touch-ripple";
    ripple.style.left = `${e.clientX}px`;
    ripple.style.top = `${e.clientY}px`;
    ripple.addEventListener("animationend", () => ripple.remove());
    document.body.appendChild(ripple);
  });

  // ---------- Auto-refresh ----------

  refreshTimer = setInterval(() => {
    if (document.visibilityState === "visible") loadEvents({ silent: state.events.length > 0 });
  }, REFRESH_INTERVAL_MS);
  setInterval(() => {
    if (document.visibilityState === "visible") loadWeather();
  }, WEATHER_REFRESH_INTERVAL_MS);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
      loadEvents({ silent: state.events.length > 0 });
      loadWeather();
    }
  });

  // ---------- Init ----------

  render();
  loadEvents();
  loadWeather();
})();
