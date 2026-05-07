import { Button } from "../design-system/primitives";

export function SessionHeader({
  activeTitle,
  streaming,
  onStop,
}: {
  activeTitle: string;
  streaming: boolean;
  onStop: () => void;
  // Kept in the prop signature for backwards compatibility with App.tsx; the
  // ⌘K button itself was removed since the composer hint already advertises
  // the shortcut. Left here so we don't have to thread the change through.
  onOpenPalette?: () => void;
}) {
  return (
    // ``WebkitAppRegion: drag`` lets the user drag the window from this
    // header inside macOS WKWebView. Buttons re-enable normal clicks via
    // ``no-drag``. The left padding accounts for the traffic-light buttons
    // so they don't overlap the chat title.
    <header className="session-header" data-app-region="drag">
      <div className="session-title-block">
        <h1>{activeTitle}</h1>
      </div>
      {streaming ? (
        <div className="session-header-controls" data-app-region="no-drag">
          <Button onClick={onStop} type="button" variant="danger">
            Stop
          </Button>
        </div>
      ) : null}
    </header>
  );
}
