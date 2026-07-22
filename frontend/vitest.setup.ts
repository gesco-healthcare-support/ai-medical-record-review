// Adds jest-dom matchers (toBeInTheDocument, etc.) to Vitest's expect and augments its types, then
// unmounts React trees after each test so component tests stay isolated.
import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(cleanup);
