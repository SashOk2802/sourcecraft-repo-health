/*
 * Движение в интерфейсе: короткое, одинаковое по характеру и полностью
 * отключаемое. Если в системе включено «уменьшить движение», анимаций нет —
 * это не украшение, а требование доступности.
 */

/** Замедление к концу: быстро стартует, мягко останавливается. */
export function easeOutCubic(progress: number): number {
  const clamped = Math.min(1, Math.max(0, progress));
  return 1 - (1 - clamped) ** 3;
}

/** Значение счётчика на доле прохода анимации. */
export function countUpValue(from: number, to: number, progress: number): number {
  return from + (to - from) * easeOutCubic(progress);
}

export interface TiltAngles {
  /** Наклон вокруг горизонтальной оси, градусы. */
  rotateX: number;
  /** Наклон вокруг вертикальной оси, градусы. */
  rotateY: number;
}

export interface TiltBox {
  width: number;
  height: number;
}

/**
 * Наклон карточки под курсором: в центре нуль, к краям — не больше max.
 * Курсор сверху опускает верх на зрителя, слева — поворачивает влево.
 */
export function tiltAngles(box: TiltBox, x: number, y: number, max = 4): TiltAngles {
  if (box.width <= 0 || box.height <= 0) {
    return { rotateX: 0, rotateY: 0 };
  }
  const offsetX = Math.min(1, Math.max(-1, (x / box.width) * 2 - 1));
  const offsetY = Math.min(1, Math.max(-1, (y / box.height) * 2 - 1));
  return {
    rotateX: Number((-offsetY * max).toFixed(2)),
    rotateY: Number((offsetX * max).toFixed(2)),
  };
}

/** Пользователь попросил систему поменьше двигать интерфейс. */
export function prefersReducedMotion(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return false;
  }
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}
