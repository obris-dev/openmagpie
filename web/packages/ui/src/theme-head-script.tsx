import {
  THEME_COOKIE_NAME,
  THEME_STORAGE_KEY,
  THEME,
  EXPLICIT_THEMES,
} from "./theme-constants";

// Blocking inline script rendered first in <body> (NOT in <head>, despite the
// "Head" in the name: App Router has no clean per-app <head> injection point);
// it still runs before content paints, which is what matters.
// Reads the cross-origin theme cookie (falling back to localStorage), applies
// the `dark` class on <html> immediately, and syncs the cookie value into
// localStorage so next-themes agrees. This is what makes a dark choice from
// another openmagpie origin show with no flash on first load. The interpolated
// values are trusted module constants, so the inline script stays injection-safe.
// No CSP nonce is set: fine today (no app uses a strict script-src); if one
// adopts strict CSP, thread a nonce through to this <script>.
const THEME_HEAD_SCRIPT = `
(function(){
  try {
    var m = document.cookie.match(/(?:^|;)\\s*${THEME_COOKIE_NAME}=(${EXPLICIT_THEMES.join("|")})(?:;|$)/);
    var theme = m ? m[1] : localStorage.getItem("${THEME_STORAGE_KEY}");
    if (theme === "${THEME.DARK}") { document.documentElement.classList.add("${THEME.DARK}"); }
    else if (theme === "${THEME.LIGHT}") { document.documentElement.classList.remove("${THEME.DARK}"); }
    if (m) localStorage.setItem("${THEME_STORAGE_KEY}", m[1]);
  } catch (e) {}
})();
`;

export function ThemeHeadScript() {
  return (
    <script
      dangerouslySetInnerHTML={{ __html: THEME_HEAD_SCRIPT }}
      suppressHydrationWarning
    />
  );
}
