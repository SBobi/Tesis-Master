export function shortId(value: string, size = 8): string {
  if (!value) return "-";
  return value.slice(0, size);
}

export function formatDate(input: string | null | undefined): string {
  if (!input) return "-";
  const date = new Date(input);
  if (Number.isNaN(date.getTime())) return input;
  return new Intl.DateTimeFormat("es-ES", {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

export function formatSeconds(value: number | null | undefined): string {
  if (value === null || value === undefined) return "-";
  return `${value.toFixed(1)} s`;
}

export function metric(value: number | null | undefined): string {
  if (value === null || value === undefined) return "N/A";
  return value.toFixed(3);
}
