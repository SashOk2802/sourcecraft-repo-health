import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

// Onest близок к фирменному шрифту Яндекса и лежит в пакете: стенд не зависит от внешних CDN.
import "@fontsource/onest/400.css";
import "@fontsource/onest/500.css";
import "@fontsource/onest/600.css";
import "@fontsource/onest/700.css";

import "@gravity-ui/uikit/styles/styles.css";
import "./styles/tokens.css";
import "./styles/base.css";

import { App } from "./App";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
