/*
 * Фон до загрузки приложения: без белой вспышки у тех, кто выбрал тёмную тему.
 * Отдельным файлом, а не встроенным скриптом: продакшн-nginx разрешает только
 * скрипты с того же адреса (CSP script-src 'self').
 */
try {
  var theme = localStorage.getItem("rh-theme");
  var dark = theme === "dark" || (theme !== "light" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.style.background = dark ? "#121214" : "#f2f3f5";
} catch (error) {}
