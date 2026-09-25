import { useCallback, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent } from "react";

import { prefersReducedMotion, tiltAngles } from "../lib/motion";

/*
 * Лёгкий объём: карточка чуть поворачивается вслед за курсором.
 * Угол маленький — это подсказка, что элемент живой, а не аттракцион.
 * При «уменьшить движение» наклона нет.
 */
export function usePointerTilt(max = 4): {
  style: CSSProperties;
  onPointerMove: (event: ReactPointerEvent<HTMLElement>) => void;
  onPointerLeave: () => void;
} {
  const [angles, setAngles] = useState({ rotateX: 0, rotateY: 0 });
  const frame = useRef(0);

  const onPointerMove = useCallback(
    (event: ReactPointerEvent<HTMLElement>) => {
      if (prefersReducedMotion() || event.pointerType !== "mouse") {
        return;
      }
      const box = event.currentTarget.getBoundingClientRect();
      const x = event.clientX - box.left;
      const y = event.clientY - box.top;

      cancelAnimationFrame(frame.current);
      frame.current = requestAnimationFrame(() => {
        setAngles(tiltAngles({ width: box.width, height: box.height }, x, y, max));
      });
    },
    [max],
  );

  const onPointerLeave = useCallback(() => {
    cancelAnimationFrame(frame.current);
    setAngles({ rotateX: 0, rotateY: 0 });
  }, []);

  return {
    style: {
      "--rh-tilt-x": `${angles.rotateX}deg`,
      "--rh-tilt-y": `${angles.rotateY}deg`,
    } as CSSProperties,
    onPointerMove,
    onPointerLeave,
  };
}
