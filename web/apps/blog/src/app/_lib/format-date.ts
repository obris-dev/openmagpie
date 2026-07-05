// Render a YYYY-MM-DD post date as "Month D, YYYY". Parsed at LOCAL midnight (no
// Z) so the displayed day matches the authored date regardless of the reader's
// timezone. (Machine-readable dates in the sitemap/feed use UTC instead.)
export function formatDate(dateString: string): string {
  return new Date(dateString + "T00:00:00").toLocaleDateString("en-US", {
    year: "numeric",
    month: "long",
    day: "numeric",
  });
}

// Parse a YYYY-MM-DD post date at UTC midnight (stable across server timezones),
// for machine-readable output (sitemap lastmod, RSS pubDate) and date validation.
export function toUtcDate(dateString: string): Date {
  return new Date(`${dateString}T00:00:00Z`);
}
