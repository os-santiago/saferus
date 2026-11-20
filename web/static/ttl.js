export function formatTTL(seconds) {
  if (!Number.isSafeInteger(seconds) || seconds < 0) return 'Unavailable';
  return `${seconds} seconds`;
}
