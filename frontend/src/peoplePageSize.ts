export function peoplePageSize() {
  if (window.innerHeight < 600) return 1;
  if (window.innerWidth <= 760) return window.innerHeight < 740 ? 1 : 2;
  return window.innerHeight < 800 ? 2 : 4;
}
