/** Склеивает BEM-классы, пропуская пустые значения: cn("bar", active && "bar_active"). */
export function cn(...values: Array<string | false | null | undefined>): string {
  return values.filter(Boolean).join(" ");
}
