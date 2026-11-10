import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { WidgetApp } from "../widget/WidgetApp";
import "./demo.css";

// The demo plumber website (UI brief section 2) is static HTML; React adds only the widget.
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <WidgetApp mode="launcher" />
  </StrictMode>,
);
