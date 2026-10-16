import { useEffect, useRef, useState } from 'react'

/** Interpolates toward `target` over `ms` with ease-out; instant when `animate` is false. */
export function useAnimatedNumber(target: number, animate: boolean, ms = 300): number {
  const [value, setValue] = useState(target)
  const fromRef = useRef(target)
  const valueRef = useRef(target)

  useEffect(() => {
    if (!animate || typeof requestAnimationFrame === 'undefined') {
      valueRef.current = target
      return
    }
    fromRef.current = valueRef.current
    const start = performance.now()
    let raf = 0
    const step = (now: number) => {
      const t = Math.min(1, (now - start) / ms)
      const eased = 1 - (1 - t) ** 3
      const v = fromRef.current + (target - fromRef.current) * eased
      valueRef.current = v
      setValue(v)
      if (t < 1) raf = requestAnimationFrame(step)
    }
    raf = requestAnimationFrame(step)
    return () => cancelAnimationFrame(raf)
  }, [target, animate, ms])

  return animate ? value : target
}
