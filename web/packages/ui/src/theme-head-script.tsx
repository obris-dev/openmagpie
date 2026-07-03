// Blocking inline script for the top of <body>: runs before content paints.
// Reads the cross-origin `openmagpie-theme` cookie (falling back to
// localStorage), applies the `dark` class on <html> immediately, and syncs the
// cookie value into localStorage so next-themes agrees. This is what makes a
// dark choice from another openmagpie origin show with no flash on first load.
const THEME_HEAD_SCRIPT = `
(function(){
  try {
    var m = document.cookie.match(/(?:^|;)\\s*openmagpie-theme=(light|dark)/);
    var theme = m ? m[1] : localStorage.getItem("theme");
    if (theme === "dark") { document.documentElement.classList.add("dark"); }
    else if (theme === "light") { document.documentElement.classList.remove("dark"); }
    if (m) localStorage.setItem("theme", m[1]);
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
