import { useLayoutEffect, useRef } from 'react';

// Stable event props let memoized panels ignore unrelated sensor updates.
// Refresh after commit so handlers always use the current form/configuration.
export function useLiveActions(handlers) {
  const current = useRef(handlers);
  useLayoutEffect(() => { current.current = handlers; });
  const stable = useRef(null);
  if (!stable.current) stable.current = Object.fromEntries(
    Object.keys(handlers).map(key => [key, (...args) => current.current[key](...args)]));
  return stable.current;
}
