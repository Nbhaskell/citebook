// Small line icons drawn inline (no icon font, no emoji). Always shown beside a word.

export type IconName =
  | "clock"
  | "check"
  | "inbox"
  | "cross"
  | "alert"
  | "phone"
  | "move"
  | "undo"
  | "person"
  | "question"
  | "copy";

const PATHS: Record<IconName, string> = {
  clock: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18Zm0 4v5l3 2",
  check: "M4 12.5l5 5L20 6.5",
  inbox: "M3 13h5l1.5 3h5L16 13h5M5 5h14l2 8v6H3v-6l2-8Z",
  cross: "M6 6l12 12M18 6L6 18",
  alert: "M12 3l10 18H2L12 3Zm0 6v5m0 3v.5",
  phone:
    "M5 3h4l2 5-2.5 1.5a11 11 0 0 0 6 6L16 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 5a2 2 0 0 1 2-2Z",
  move: "M4 12h16M14 6l6 6-6 6M10 6 4 12l6 6",
  undo: "M9 7 4 12l5 5M4 12h11a5 5 0 0 1 0 10h-3",
  person: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8Zm-8 9a8 8 0 0 1 16 0",
  question: "M9 9a3 3 0 1 1 4 2.8c-.8.4-1 1-1 1.7V15m0 3v.5",
  copy: "M8 8h11v13H8zM5 16V3h11",
};

export function Icon({ name, label }: { name: IconName; label?: string }) {
  return (
    <svg
      className="icon"
      viewBox="0 0 24 24"
      width="1em"
      height="1em"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      focusable="false"
    >
      <path d={PATHS[name]} />
    </svg>
  );
}
