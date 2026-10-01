export function timeAgo(iso: string): string {
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return "just now";
  const units: [number, string][] = [[60, "minute"], [3600, "hour"], [86400, "day"], [2592000, "month"], [31536000, "year"]];
  let i = 0;
  while (i < units.length - 1 && s >= units[i + 1][0]) i++;
  const n = Math.floor(s / units[i][0]);
  return `${n} ${units[i][1]}${n === 1 ? "" : "s"} ago`;
}
export const formatDate = (iso: string) => new Date(iso).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1048576) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1048576).toFixed(1)} MB`;
}
export const titleCase = (s: string) => s.toLowerCase().replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());

/** "Free", or "₹199 / month". Prices are in the smallest unit (paise) and come from the backend plan config; this only formats them. */
export function formatPrice(minor: number, currency: string, period: string): string {
  if (minor === 0) return "Free";
  const amount = new Intl.NumberFormat(currency === "INR" ? "en-IN" : undefined, { style: "currency", currency, maximumFractionDigits: minor % 100 ? 2 : 0 }).format(minor / 100);
  return `${amount} / ${period}`;
}
