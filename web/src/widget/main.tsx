import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { WidgetApp } from "./WidgetApp";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <WidgetApp />
  </StrictMode>,
);
