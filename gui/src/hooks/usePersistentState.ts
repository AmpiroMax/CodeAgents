import { useEffect, useState } from "react";

export function usePersistentState<T>(
  key: string,
  initial: T,
  parse: (raw: string) => T = JSON.parse,
): [T, (next: T | ((prev: T) => T)) => void] {
  const [value, setValue] = useState<T>(() => {
    if (typeof window === "undefined") {
      return initial;
    }
    const raw = window.localStorage.getItem(key);
    if (!raw) {
      return initial;
    }
    try {
      return parse(raw);
    } catch {
      return initial;
    }
  });

  useEffect(() => {
    window.localStorage.setItem(key, JSON.stringify(value));
  }, [key, value]);

  return [value, setValue];
}
