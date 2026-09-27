/** Форматирование чисел и дат для русского интерфейса. */

const DAY_MS = 24 * 60 * 60 * 1000;

/** Выбирает форму слова: plural(3, "день", "дня", "дней") → «дня». */
export function plural(count: number, one: string, few: string, many: string): string {
  const mod10 = Math.abs(count) % 10;
  const mod100 = Math.abs(count) % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
}

const integerFormat = new Intl.NumberFormat("ru-RU");
const decimalFormat = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 1 });

/** «1 284» с неразрывным пробелом между разрядами. */
export function formatInteger(value: number): string {
  return integerFormat.format(value);
}

/** «61,5», «4,1 тыс.», «1,2 млн» — как рейтинг лайков в каталоге SourceCraft. */
export function formatLikes(value: number): string {
  if (value >= 1_000_000) return `${decimalFormat.format(value / 1_000_000)} млн`;
  if (value >= 1_000) return `${decimalFormat.format(value / 1_000)} тыс.`;
  return decimalFormat.format(value);
}

/** Score и оценки категорий показываем целыми, округление — только для вывода. */
export function formatScore(value: number): string {
  return String(Math.round(value));
}

/** Доля 0..1 → «75%». */
export function formatShare(value: number): string {
  return `${Math.round(value * 100)}%`;
}

/** Баллы с одним знаком после запятой: «15,5». */
export function formatPoints(value: number): string {
  return decimalFormat.format(value);
}

function dateParts(iso: string, options: Intl.DateTimeFormatOptions): Record<string, string> {
  const parts = new Intl.DateTimeFormat("ru-RU", options).formatToParts(new Date(iso));
  return Object.fromEntries(parts.map((part) => [part.type, part.value]));
}

/** «16 сентября 2026». Собираем из частей, чтобы не зависеть от «г.» в разных движках. */
export function formatDate(iso: string, timeZone?: string): string {
  const p = dateParts(iso, { day: "numeric", month: "long", year: "numeric", timeZone });
  return `${p.day} ${p.month} ${p.year}`;
}

/** «16 сентября 2026, 14:05». */
export function formatDateTime(iso: string, timeZone?: string): string {
  const p = dateParts(iso, {
    day: "numeric",
    month: "long",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
    timeZone,
  });
  return `${p.day} ${p.month} ${p.year}, ${p.hour}:${p.minute}`;
}

/** «15 сентября, 06:04» для текущего года и с годом — для прошлых. */
export function formatDateTimeCompact(iso: string, now: Date = new Date(), timeZone?: string): string {
  const p = dateParts(iso, {
    day: "numeric",
    month: "long",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
    timeZone,
  });
  const sameYear = Number(p.year) === now.getFullYear();
  return sameYear
    ? `${p.day} ${p.month}, ${p.hour}:${p.minute}`
    : `${p.day} ${p.month} ${p.year}, ${p.hour}:${p.minute}`;
}

/** «14:05». */
export function formatTime(iso: string, timeZone?: string): string {
  const p = dateParts(iso, { hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZone });
  return `${p.hour}:${p.minute}`;
}

/** «сегодня», «вчера», «3 дня назад», «месяц назад». Считаем по календарным дням. */
export function formatRelativeDay(iso: string, now: Date = new Date()): string {
  const date = new Date(iso);
  const startOfDay = (value: Date): number =>
    new Date(value.getFullYear(), value.getMonth(), value.getDate()).getTime();
  const days = Math.round((startOfDay(now) - startOfDay(date)) / DAY_MS);

  if (days <= 0) return "сегодня";
  if (days === 1) return "вчера";
  if (days < 7) return `${days} ${plural(days, "день", "дня", "дней")} назад`;
  if (days < 30) {
    const weeks = Math.floor(days / 7);
    return weeks === 1 ? "неделю назад" : `${weeks} ${plural(weeks, "неделю", "недели", "недель")} назад`;
  }
  if (days < 365) {
    const months = Math.floor(days / 30);
    return months === 1 ? "месяц назад" : `${months} ${plural(months, "месяц", "месяца", "месяцев")} назад`;
  }
  const years = Math.floor(days / 365);
  return years === 1 ? "год назад" : `${years} ${plural(years, "год", "года", "лет")} назад`;
}

/** Длительность в секундах → «2 мин 14 с». */
export function formatDuration(totalSeconds: number): string {
  const seconds = Math.max(0, Math.round(totalSeconds));
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  if (minutes === 0) return `${rest} с`;
  return rest === 0 ? `${minutes} мин` : `${minutes} мин ${rest} с`;
}
