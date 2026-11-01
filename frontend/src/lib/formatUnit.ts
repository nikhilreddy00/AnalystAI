// Phase 5.5: a number in its unit, written exactly as the backend writes it
// (agents/visualization_agent.py _format_value), so a tile and its filtered recompute agree.
// Money: the same currency list as the backend, Intl en-US decimals (JPY none). Any other unit
// (°C, days, RPM) follows the number after a no-break space. Never throws.
const ISO_CURRENCIES = new Set("USD EUR GBP JPY INR CNY CAD AUD CHF BRL MXN SEK NOK DKK PLN CZK HUF ZAR NZD SGD HKD KRW TRY ILS AED SAR THB MYR IDR PHP VND RUB EGP NGN KES COP CLP PEN ARS TWD".split(" "));
const NBSP = "\u00a0";

export function isCurrency(unit?: string | null): boolean {
  return !!unit && ISO_CURRENCIES.has(unit);
}

export function formatMoney(n: number, currency?: string | null): string {
  if (!Number.isFinite(n)) return "—";
  const code = currency && isCurrency(currency) ? currency : "USD";
  return n.toLocaleString("en-US", { style: "currency", currency: code });
}

export function formatWithUnit(n: number, unit?: string | null): string {
  if (!Number.isFinite(n)) return "—";
  if (isCurrency(unit)) return formatMoney(n, unit);
  const num = n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return unit ? `${num}${NBSP}${unit}` : num;
}
