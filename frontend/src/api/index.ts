import type { TempoApi } from "../types";
import { httpApi } from "./client";
import { mockApi } from "./mock";

// One switch: VITE_MOCK=1 -> built-in fixture; otherwise the real fetch layer.
// Views import `api` and are blind to which implementation backs it.
export const IS_MOCK: boolean = import.meta.env.VITE_MOCK === "1";
export const api: TempoApi = IS_MOCK ? mockApi : httpApi;
