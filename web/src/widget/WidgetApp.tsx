import { StatusPanel } from "../shared/StatusPanel";

// Phase 0 placeholder: the chat widget is built in Phase 5 (docs/UI_UX_DESIGN_BRIEF.md section 3).
export function WidgetApp() {
  return (
    <main>
      <h1>Citebook widget</h1>
      <StatusPanel />
      <footer>
        <p>Demo: all details are invented. Don't enter real information.</p>
      </footer>
    </main>
  );
}
