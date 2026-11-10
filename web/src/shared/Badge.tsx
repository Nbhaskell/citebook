import { Icon, type IconName } from "./Icon";
import type { Tone } from "./labels";

/** A status shown as word, icon and colour together; colour is never the only signal. */
export function Badge({ word, icon, tone = "neutral" }: { word: string; icon: IconName; tone?: Tone }) {
  return (
    <span className={`badge badge-${tone}`}>
      <Icon name={icon} /> {word}
    </span>
  );
}
