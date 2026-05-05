import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

export type ThemeSetting = "dark" | "light" | "auto";
export type ResolvedTheme = "dark" | "light";

type ThemeContextValue = {
  themeSetting: ThemeSetting;
  currentTheme: ResolvedTheme;
  setThemeSetting: (setting: ThemeSetting) => void;
  setPreviewTheme: (setting: ThemeSetting | null) => void;
  savePreview: () => void;
  cancelPreview: () => void;
};

const THEME_KEY = "codeagents.theme";

const ThemeContext = createContext<ThemeContextValue | null>(null);

function systemTheme(): ResolvedTheme {
  if (
    typeof window !== "undefined" &&
    window.matchMedia?.("(prefers-color-scheme: light)").matches
  ) {
    return "light";
  }
  return "dark";
}

function readSavedTheme(): ThemeSetting {
  if (typeof window === "undefined") {
    return "dark";
  }
  const saved = window.localStorage.getItem(THEME_KEY);
  return saved === "light" || saved === "dark" || saved === "auto"
    ? saved
    : "dark";
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [themeSetting, setThemeSettingState] = useState<ThemeSetting>(readSavedTheme);
  const [previewTheme, setPreviewTheme] = useState<ThemeSetting | null>(null);
  const [system, setSystem] = useState<ResolvedTheme>(systemTheme);

  useEffect(() => {
    const media = window.matchMedia?.("(prefers-color-scheme: light)");
    if (!media) {
      return;
    }
    const onChange = () => setSystem(systemTheme());
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, []);

  const activeSetting = previewTheme ?? themeSetting;
  const currentTheme: ResolvedTheme =
    activeSetting === "auto" ? system : activeSetting;

  useEffect(() => {
    document.documentElement.dataset.theme = currentTheme;
  }, [currentTheme]);

  const value = useMemo<ThemeContextValue>(
    () => ({
      themeSetting,
      currentTheme,
      setThemeSetting: (setting) => {
        setThemeSettingState(setting);
        setPreviewTheme(null);
        window.localStorage.setItem(THEME_KEY, setting);
      },
      setPreviewTheme,
      savePreview: () => {
        if (previewTheme) {
          setThemeSettingState(previewTheme);
          window.localStorage.setItem(THEME_KEY, previewTheme);
          setPreviewTheme(null);
        }
      },
      cancelPreview: () => setPreviewTheme(null),
    }),
    [currentTheme, previewTheme, themeSetting],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme() {
  const ctx = useContext(ThemeContext);
  if (!ctx) {
    throw new Error("useTheme must be used within ThemeProvider");
  }
  return ctx;
}
