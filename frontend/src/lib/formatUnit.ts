// Phase 5.5: a number in its unit. An ISO currency (USD, EUR) is formatted as money; any other
// unit (°C, days, kg) follows the number. An unknown or malformed code never throws.
const ISO_CURRENCY = /^[A-Z]{3}$/;

export function isCurrency(unit?: string | null): boolean {
  return !!unit && ISO_CURRENCY.test(unit);
}

export function formatMoney(n: number, currency: string = "USD"): string {
  try {
    return n.toLocaleString("en-US", { style: "currency", currency, maximumFractionDigits: 2 });
  } catch {
    return `${currency} ${n.toLocaleString("en-US", { maximumFractionDigits: 2 })}`;
  }
}

export function formatWithUnit(n: number, unit?: string | null): string {
  if (isCurrency(unit)) return formatMoney(n, unit as string);
  const num = n.toLocaleString("en-US", { maximumFractionDigits: 2 });
  return unit ? `${num} ${unit}` : num;
}
