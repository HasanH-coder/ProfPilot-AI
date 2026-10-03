const relativeTime = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
const dateFormat = new Intl.DateTimeFormat("en", { dateStyle: "medium" });

/**
 * When something happened, relative to now: "just now", "5 minutes ago",
 * "yesterday", "3 days ago", or the date once it's a week old.
 */
export function timeAgo(isoDate: string, now = Date.now()) {
  const date = new Date(isoDate);
  const seconds = Math.round((date.getTime() - now) / 1000);
  if (seconds > -60) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes > -60) return relativeTime.format(minutes, "minute");
  const hours = Math.round(minutes / 60);
  if (hours > -24) return relativeTime.format(hours, "hour");
  const days = Math.round(hours / 24);
  if (days > -7) return relativeTime.format(days, "day");
  return dateFormat.format(date);
}
