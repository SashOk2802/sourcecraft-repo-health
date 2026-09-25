import { useEffect, useState } from "react";

import { countUpValue, prefersReducedMotion } from "../lib/motion";

/*
 * Score набегает от нуля до присланного backend значения.
 * Если пользователь попросил систему меньше двигать интерфейс,
 * число появляется сразу.
 */
export function useCountUp(target: number | null, durationMs = 900): number | null {
  const [value, setValue] = useState<number | null>(target);

  useEffect(() => {
    if (target === null) {
      setValue(null);
      return;
    }
    if (prefersReducedMotion()) {
      setValue(target);
      return;
    }

    let frame = 0;
    const startedAt = performance.now();

    const step = (now: number): void => {
      const progress = Math.min(1, (now - startedAt) / durationMs);
      setValue(countUpValue(0, target, progress));
      if (progress < 1) {
        frame = requestAnimationFrame(step);
      }
    };

    frame = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frame);
  }, [target, durationMs]);

  return value;
}
