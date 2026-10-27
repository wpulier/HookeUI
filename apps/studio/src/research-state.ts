// One answer to "is this researcher working?" for the pill, the rail badge, the board and the researcher's page
// (2026-10-23 audit: shelf.ts and research.ts each had their own, and the badge could count one the pill called
// stopped). Working means the server lists it as queued, starting, running or revising, or its mission is still on.
import type { ResearchSession } from './protocol';

/** Working: queued, starting, running or revising (the Researchers tab's count and the shelf alike). */
export const ACTIVE = new Set(['queued', 'starting', 'running', 'revising']);
export const MISSION_ACTIVE = new Set(['watching', 'checkpoint', 'finishing']);
export const isWorking = (s: ResearchSession): boolean => ACTIVE.has(s.status) || !!(s.mission && MISSION_ACTIVE.has(s.mission.status));
