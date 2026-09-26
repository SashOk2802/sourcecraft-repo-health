/** Даты mock-данных считаются от текущего момента, чтобы демо не «старело». */

export function minutesAgo(minutes: number): string {
  return new Date(Date.now() - minutes * 60_000).toISOString();
}

export function daysAgo(days: number, hour = 13, minute = 20): string {
  const date = new Date();
  date.setDate(date.getDate() - days);
  date.setHours(hour, minute, 0, 0);
  // Сегодняшняя дата с часом из будущего выглядела бы странно.
  return date.getTime() > Date.now() ? minutesAgo(40) : date.toISOString();
}

export function todayAt(hour: number): string {
  const date = new Date();
  date.setHours(hour, 0, 0, 0);
  return date.toISOString();
}
