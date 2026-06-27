// Relative time formatting for the sidebar's conversation rows.
// No deps — Intl is in the runtime. No auto-refresh; the sidebar isn't a live feed.

function _sameCalendarDay(a: Date, b: Date): boolean {
  return (
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  );
}

function _calendarDaysAgo(target: Date, now: Date): number {
  // Diff in whole days between calendar dates (ignoring time-of-day) so
  // 23:00 → 01:00 next day counts as 1 day, not 0.
  const startOf = (d: Date) =>
    Date.UTC(d.getFullYear(), d.getMonth(), d.getDate());
  return Math.round((startOf(now) - startOf(target)) / 86_400_000);
}

const _timeOfDay = new Intl.DateTimeFormat(undefined, {
  hour: "numeric",
  minute: "2-digit",
});
const _monthDay = new Intl.DateTimeFormat(undefined, {
  month: "short",
  day: "numeric",
});
const _monthDayYear = new Intl.DateTimeFormat(undefined, {
  month: "short",
  day: "numeric",
  year: "numeric",
});
const _rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });

export function formatRelative(iso: string, now: Date = new Date()): string {
  const target = new Date(iso);
  if (Number.isNaN(target.getTime())) return "";
  if (_sameCalendarDay(target, now)) {
    return _timeOfDay.format(target);
  }
  const daysAgo = _calendarDaysAgo(target, now);
  if (daysAgo === 1) {
    // Intl emits "yesterday" with `numeric: "auto"` — capitalised by the
    // formatter in most locales, but force-capitalise the first letter so the
    // sidebar row reads cleanly.
    const s = _rtf.format(-1, "day");
    return s.charAt(0).toUpperCase() + s.slice(1);
  }
  if (daysAgo >= 2 && daysAgo <= 6) {
    return _rtf.format(-daysAgo, "day");
  }
  if (target.getFullYear() === now.getFullYear()) {
    return _monthDay.format(target);
  }
  return _monthDayYear.format(target);
}
